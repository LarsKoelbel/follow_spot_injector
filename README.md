# Follow Spot Injector

Sits between a lighting console and a moving-head fixture on the network.
It receives the console's unicast Art-Net stream, lets a game controller
nudge the pan/tilt channels away from whatever value the console sent, and
forwards the result to the fixture. Because it only speaks plain Art-Net
(ArtDmx), it works with any lighting software that can unicast Art-Net to
a fixed IP.

Behaviour:
- Holding a stick moves the spot by `speed * axis_value` per second, added
  on top of the console's value.
- The moment the console sends a *different* pan/tilt value (a programmed
  cue move, or the operator touching the fader), the override starts
  easing back to zero at up to `return_speed` DMX units/second, so the
  spot glides back onto the console's value instead of snapping there
  instantly. Touching the stick again at any point cancels the ease and
  hands control straight back to the operator. This is how the tool avoids
  interfering with playback or manual desk control - it only overrides a
  value that the console is currently holding static.

## Setup

```
pip install -r requirements.txt
```

Point your console at this machine's IP, port 6454, unicast, on the
universe you want to intercept. Set `target.ip` in `profile.json` to the
moving head's IP.

Run it:

```
python follow_spot.py
```

On first run it creates `profile.json` with defaults next to the script,
then exits so you can edit it. A web UI (see below) also starts
automatically alongside the injector.

## Web UI

Open `http://localhost:3011` (or whatever `ui.port` is set to) while
`follow_spot.py` is running. It shows:
- A live pan/tilt pad: a hollow circle for the console's raw value, a
  filled dot for the actual output value, plus a numeric readout of the
  current offset.
- A connection indicator and the detected controller name.
- A scrolling debug log (the same messages printed to the console).
- An Art-Net/controller settings form, locked behind the 4-digit
  `ui.edit_code` from `profile.json`. Saving writes straight to
  `profile.json`.
- A "Restart injector" button (next to the settings form, same code
  required) that re-executes `follow_spot.py` in place so it picks up
  whatever you just saved - no need to go back to the terminal. It briefly
  drops Art-Net output while the process restarts (well under a second on
  a local machine); the page keeps polling and reconnects automatically.

The UI runs in a background thread of the same process, so it needs no
separate server or network hop - just reads the injector's in-memory state.

## profile.json

```jsonc
{
  "listen": { "ip": "0.0.0.0", "port": 6454 },   // where this machine listens for the console
  "receive_universe": 0,                          // Art-Net universe (Net<<8 | Sub-Net/Universe) to accept from the console
  "target": { "ip": "192.168.1.50", "port": 6454 },// the moving head's IP
  "send_universe": 0,                             // universe to forward to the moving head
  "channels": {
    "pan":  { "address": 1, "bits": 16 },          // 1-based DMX channel + bit depth
    "tilt": { "address": 3, "bits": 16 }
  },
  "controller": {
    "joystick_index": 0,                           // which controller, if more than one is plugged in
    "pan_axis": 0,                                 // joystick axis index that drives pan
    "tilt_axis": 1,                                // joystick axis index that drives tilt
    "deadzone": 0.1,                                // axis magnitude below this is treated as 0
    "pan_speed": 3000,                              // DMX units/second added at full stick deflection
    "tilt_speed": 3000,
    "invert_pan": false,
    "invert_tilt": false,
    "return_speed": 1500                            // max DMX units/second the override eases back at after a console change; 0 = snap instantly
  },
  "update_rate_hz": 40,                             // how often the controller is polled and a packet sent
  "ui": { "port": 3011, "edit_code": "1234" }        // web UI port and the 4-digit code required to save settings
}
```

If you already have a `profile.json` from an older version, any fields it's
missing (the `ui` section, `return_speed`, etc.) are added with their
defaults in place the next time you run the script - change them
afterwards if you like.

`bits` supports 8 (single channel), 16 (coarse+fine, the usual case for
fine pan/tilt), or any other multiple of 8 the fixture uses - the channel's
value spans that many consecutive DMX channels, most-significant byte
first, starting at `address`.

Every other channel in the universe is passed through to the fixture
unmodified.
