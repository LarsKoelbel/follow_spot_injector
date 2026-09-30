"""
Art-Net follow-spot injector.

Sits between a lighting console and a moving-head fixture. It receives a
unicast Art-Net ArtDmx stream, lets a game controller nudge the pan/tilt
channels away from whatever the console sent, and forwards the modified
packet on to the fixture. As soon as the console sends a different pan/tilt
value (a programmed move, or the operator touching the fader), the
accumulated nudge is dropped and the console's raw value is forwarded as-is.

All settings live in profile.json next to this script (created with
defaults on first run).
"""

import json
import os
import socket
import struct
import sys
import threading
import time
from collections import deque

import pygame

from ui.server import run_ui_server

PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.json")

DEFAULT_PROFILE = {
    "listen": {"ip": "0.0.0.0", "port": 6454},
    "receive_universe": 0,
    "target": {"ip": "192.168.1.50", "port": 6454},
    "send_universe": 0,
    "channels": {
        "pan": {"address": 1, "bits": 16},
        "tilt": {"address": 3, "bits": 16}
    },
    "controller": {
        "joystick_index": 0,
        "pan_axis": 0,
        "tilt_axis": 1,
        "deadzone": 0.1,
        "pan_speed": 3000,
        "tilt_speed": 3000,
        "invert_pan": False,
        "invert_tilt": False,
        "return_speed": 1500
    },
    "update_rate_hz": 40,
    "ui": {"port": 3011, "edit_code": "1234"}
}

ARTNET_HEADER = b"Art-Net\x00"
OP_DMX = 0x5000

STALE_AFTER_SECONDS = 2.0

LOG_BUFFER = deque(maxlen=200)


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    LOG_BUFFER.append(line)


def merge_defaults(loaded, defaults):
    """Fill in any keys missing from an older profile.json. Returns True if it changed anything."""
    changed = False
    for key, value in defaults.items():
        if key not in loaded:
            loaded[key] = value
            changed = True
        elif isinstance(value, dict) and isinstance(loaded.get(key), dict):
            if merge_defaults(loaded[key], value):
                changed = True
    return changed


def load_profile():
    if not os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, "w") as f:
            json.dump(DEFAULT_PROFILE, f, indent=4)
        print("Created default profile at %s - edit it and re-run." % PROFILE_PATH)
        sys.exit(0)
    with open(PROFILE_PATH, "r") as f:
        profile = json.load(f)
    if merge_defaults(profile, DEFAULT_PROFILE):
        with open(PROFILE_PATH, "w") as f:
            json.dump(profile, f, indent=4)
        log("profile.json was missing some fields (e.g. web UI settings) - filled in defaults.")
    return profile


def parse_artdmx(packet):
    """Return (universe, data) for an ArtDmx packet, or None otherwise."""
    if len(packet) < 18 or packet[:8] != ARTNET_HEADER:
        return None
    opcode = packet[8] | (packet[9] << 8)
    if opcode != OP_DMX:
        return None
    sub_uni = packet[14]
    net = packet[15]
    universe = (net << 8) | sub_uni
    length = (packet[16] << 8) | packet[17]
    data = bytearray(packet[18:18 + length])
    if len(data) < length:
        return None
    return universe, data


def build_artdmx(universe, data, sequence):
    sub_uni = universe & 0xFF
    net = (universe >> 8) & 0x7F
    packet = bytearray()
    packet += ARTNET_HEADER
    packet += struct.pack("<H", OP_DMX)
    packet += struct.pack(">H", 14)  # protocol version
    packet.append(sequence & 0xFF)
    packet.append(0)  # physical port, unused
    packet.append(sub_uni)
    packet.append(net)
    packet += struct.pack(">H", len(data))
    packet += data
    return bytes(packet)


def read_value(data, address, bits):
    """Read a big-endian (coarse-first) value spanning bits//8 channels, 1-based address."""
    n_bytes = bits // 8
    value = 0
    for i in range(n_bytes):
        idx = address - 1 + i
        byte = data[idx] if idx < len(data) else 0
        value = (value << 8) | byte
    return value


def write_value(data, address, bits, value):
    n_bytes = bits // 8
    max_val = (1 << bits) - 1
    value = max(0, min(max_val, int(round(value))))
    for i in range(n_bytes):
        idx = address - 1 + i
        if idx >= len(data):
            data.extend(bytes(idx - len(data) + 1))
        shift = (n_bytes - 1 - i) * 8
        data[idx] = (value >> shift) & 0xFF


def _ease_toward_zero(offset, max_speed, dt):
    """Move offset toward 0 by at most max_speed * dt (max_speed <= 0 snaps instantly)."""
    if max_speed <= 0:
        return 0.0
    step = max_speed * dt
    if offset > 0:
        return max(0.0, offset - step)
    return min(0.0, offset + step)


def receiver_loop(sock, state, lock, receive_universe, pan_cfg, tilt_cfg):
    log("Receiver thread started, waiting for Art-Net packets...")
    seen_other_universes = set()
    packet_count = 0

    while True:
        try:
            packet, addr = sock.recvfrom(600)
        except OSError:
            return
        parsed = parse_artdmx(packet)
        if parsed is None:
            continue
        universe, data = parsed
        if universe != receive_universe:
            if universe not in seen_other_universes:
                seen_other_universes.add(universe)
                log("Ignoring Art-Net for universe %d from %s (listening on universe %d)" %
                    (universe, addr[0], receive_universe))
            continue

        pan_raw = read_value(data, pan_cfg["address"], pan_cfg["bits"])
        tilt_raw = read_value(data, tilt_cfg["address"], tilt_cfg["bits"])

        packet_count += 1
        if packet_count == 1:
            log("First Art-Net packet received from %s (universe %d): pan=%d tilt=%d" %
                (addr[0], universe, pan_raw, tilt_raw))

        with lock:
            pan_changed = pan_raw != state["last_pan_raw"]
            tilt_changed = tilt_raw != state["last_tilt_raw"]
            any_changed = pan_changed or tilt_changed
            first_packet = state["last_pan_raw"] is None

            if first_packet:
                state["offset_pan"] = 0.0
                state["offset_tilt"] = 0.0
                state["pan_returning"] = False
                state["tilt_returning"] = False
            elif any_changed:
                # Console sent a new position (programmed move or a fader touch) - it
                # doesn't matter whether that changed pan, tilt, or both: forget every
                # override. An axis with no active override just follows the console
                # directly (no lag); an axis that was actively overridden keeps the
                # output where it currently is and eases back to zero at up to
                # controller.return_speed, instead of snapping there instantly.
                if pan_changed:
                    if state["offset_pan"] != 0.0:
                        output_before = state["last_pan_raw"] + state["offset_pan"]
                        state["offset_pan"] = output_before - pan_raw
                        state["pan_returning"] = state["offset_pan"] != 0.0
                        log("Console changed PAN: %d -> %d - easing override back to it" %
                            (state["last_pan_raw"], pan_raw))
                    else:
                        state["pan_returning"] = False
                        log("Console changed PAN: %d -> %d" % (state["last_pan_raw"], pan_raw))
                elif state["offset_pan"] != 0.0:
                    # Pan itself didn't change, but the console still sent a new
                    # position - drop pan's leftover override too.
                    state["pan_returning"] = True
                    log("Also easing PAN override back (console sent a new position)")

                if tilt_changed:
                    if state["offset_tilt"] != 0.0:
                        output_before = state["last_tilt_raw"] + state["offset_tilt"]
                        state["offset_tilt"] = output_before - tilt_raw
                        state["tilt_returning"] = state["offset_tilt"] != 0.0
                        log("Console changed TILT: %d -> %d - easing override back to it" %
                            (state["last_tilt_raw"], tilt_raw))
                    else:
                        state["tilt_returning"] = False
                        log("Console changed TILT: %d -> %d" % (state["last_tilt_raw"], tilt_raw))
                elif state["offset_tilt"] != 0.0:
                    state["tilt_returning"] = True
                    log("Also easing TILT override back (console sent a new position)")

            state["last_pan_raw"] = pan_raw
            state["last_tilt_raw"] = tilt_raw
            state["last_full_data"] = data
            state["last_packet_time"] = time.time()


def open_joystick(ctrl_cfg):
    pygame.init()
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    if count == 0:
        log("No game controller found.")
        sys.exit(1)
    log("Found %d controller(s)." % count)
    joystick = pygame.joystick.Joystick(ctrl_cfg["joystick_index"])
    joystick.init()
    num_axes = joystick.get_numaxes()
    log("Using controller: %s (%d axes)" % (joystick.get_name(), num_axes))
    if ctrl_cfg["pan_axis"] >= num_axes or ctrl_cfg["tilt_axis"] >= num_axes:
        log("WARNING: pan_axis/tilt_axis in profile.json is out of range for this controller.")
    return joystick


def main():
    profile = load_profile()
    channels = profile["channels"]
    pan_cfg = channels["pan"]
    tilt_cfg = channels["tilt"]
    ctrl = profile["controller"]

    pan_max = (1 << pan_cfg["bits"]) - 1
    tilt_max = (1 << tilt_cfg["bits"]) - 1

    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind((profile["listen"]["ip"], profile["listen"]["port"]))
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    joystick = open_joystick(ctrl)

    lock = threading.Lock()
    state = {
        "last_pan_raw": None,
        "last_tilt_raw": None,
        "last_full_data": None,
        "offset_pan": 0.0,
        "offset_tilt": 0.0,
        "pan_returning": False,
        "tilt_returning": False,
        "last_packet_time": None,
    }
    restart_event = threading.Event()

    receiver = threading.Thread(
        target=receiver_loop,
        args=(recv_sock, state, lock, profile["receive_universe"], pan_cfg, tilt_cfg),
        daemon=True,
    )
    receiver.start()

    ui_context = {
        "state": state,
        "lock": lock,
        "pan_max": pan_max,
        "tilt_max": tilt_max,
        "controller_name": joystick.get_name(),
        "log_buffer": LOG_BUFFER,
        "profile_path": PROFILE_PATH,
        "port": profile["ui"]["port"],
        "log": log,
        "restart_event": restart_event,
    }
    ui_thread = threading.Thread(target=run_ui_server, args=(ui_context,), daemon=True)
    ui_thread.start()
    log("Web UI available at http://localhost:%d" % profile["ui"]["port"])

    log("Listening for Art-Net on %s:%d (universe %d)" %
        (profile["listen"]["ip"], profile["listen"]["port"], profile["receive_universe"]))
    log("Forwarding to %s:%d (universe %d)" %
        (profile["target"]["ip"], profile["target"]["port"], profile["send_universe"]))
    log("Press Ctrl+C to stop.")

    period = 1.0 / profile["update_rate_hz"]
    sequence = 1
    last_time = time.time()
    have_logged_first_send = False
    is_stale = False

    try:
        while True:
            if restart_event.is_set():
                log("Restart requested via web UI - restarting now.")
                break

            loop_start = time.time()
            dt = loop_start - last_time
            last_time = loop_start

            pygame.event.pump()
            pan_axis = joystick.get_axis(ctrl["pan_axis"])
            tilt_axis = joystick.get_axis(ctrl["tilt_axis"])
            if abs(pan_axis) < ctrl["deadzone"]:
                pan_axis = 0.0
            if abs(tilt_axis) < ctrl["deadzone"]:
                tilt_axis = 0.0
            if ctrl["invert_pan"]:
                pan_axis = -pan_axis
            if ctrl["invert_tilt"]:
                tilt_axis = -tilt_axis

            #if pan_axis != 0.0 or tilt_axis != 0.0:
            #    print("Pan axis after deadzone and inversion:", pan_axis)
            #    print("Tilt axis after deadzone and inversion:", tilt_axis)

            with lock:
                data = state["last_full_data"]
                last_packet_time = state["last_packet_time"]
                if data is not None:
                    if not have_logged_first_send:
                        have_logged_first_send = True
                        log("First Art-Net packet processed - starting to forward to fixture.")

                    pan_raw = state["last_pan_raw"]
                    tilt_raw = state["last_tilt_raw"]
                    return_speed = ctrl["return_speed"]

                    if pan_axis != 0.0:
                        # Operator input always wins - cancel any auto-return in progress.
                        state["pan_returning"] = False
                        state["offset_pan"] += pan_axis * ctrl["pan_speed"] * dt
                    elif state["pan_returning"]:
                        state["offset_pan"] = _ease_toward_zero(state["offset_pan"], return_speed, dt)
                        if state["offset_pan"] == 0.0:
                            state["pan_returning"] = False

                    if tilt_axis != 0.0:
                        state["tilt_returning"] = False
                        state["offset_tilt"] += tilt_axis * ctrl["tilt_speed"] * dt
                    elif state["tilt_returning"]:
                        state["offset_tilt"] = _ease_toward_zero(state["offset_tilt"], return_speed, dt)
                        if state["offset_tilt"] == 0.0:
                            state["tilt_returning"] = False

                    pan_out = max(0, min(pan_max, pan_raw + state["offset_pan"]))
                    state["offset_pan"] = pan_out - pan_raw

                    tilt_out = max(0, min(tilt_max, tilt_raw + state["offset_tilt"]))
                    state["offset_tilt"] = tilt_out - tilt_raw

                    out_data = bytearray(data)
                    write_value(out_data, pan_cfg["address"], pan_cfg["bits"], pan_out)
                    write_value(out_data, tilt_cfg["address"], tilt_cfg["bits"], tilt_out)

                    packet = build_artdmx(profile["send_universe"], out_data, sequence)
                    sequence = 1 if sequence >= 255 else sequence + 1
                    send_sock.sendto(packet, (profile["target"]["ip"], profile["target"]["port"]))

            # Warn if the console's Art-Net stream drops out, and note when it comes back.
            if last_packet_time is not None:
                stale_now = (time.time() - last_packet_time) > STALE_AFTER_SECONDS
                if stale_now and not is_stale:
                    log("WARNING: no Art-Net received for over %.0fs - is the console still sending?" %
                        STALE_AFTER_SECONDS)
                elif not stale_now and is_stale:
                    log("Art-Net stream resumed.")
                is_stale = stale_now

            elapsed = time.time() - loop_start
            time.sleep(max(0.0, period - elapsed))
    except KeyboardInterrupt:
        log("Stopping.")
    else:
        # Only reached via the restart_event break above (not on Ctrl+C) - tear down
        # our resources and re-exec the same command so profile.json is reloaded fresh.
        for sock in (recv_sock, send_sock):
            try:
                sock.close()
            except OSError:
                pass
        try:
            pygame.quit()
        except Exception:
            pass
        time.sleep(0.3)
        os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    main()
