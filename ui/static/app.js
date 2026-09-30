const connDot = document.getElementById('connDot');
const connText = document.getElementById('connText');
const controllerName = document.getElementById('controllerName');
const consoleMarker = document.getElementById('consoleMarker');
const outputMarker = document.getElementById('outputMarker');
const panRaw = document.getElementById('panRaw');
const panOut = document.getElementById('panOut');
const panOffset = document.getElementById('panOffset');
const tiltRaw = document.getElementById('tiltRaw');
const tiltOut = document.getElementById('tiltOut');
const tiltOffset = document.getElementById('tiltOffset');
const logBox = document.getElementById('log');

function pct(value, max) {
    return Math.max(0, Math.min(100, (value / max) * 100));
}

async function pollStatus() {
    try {
        const res = await fetch('/api/status');
        const s = await res.json();

        connDot.classList.toggle('ok', s.connected);
        connText.textContent = s.connected ? 'Receiving Art-Net' : 'No Art-Net signal';
        controllerName.textContent = s.controller_name || '-';

        if (s.have_data) {
            const panPct = pct(s.pan_raw, s.pan_max);
            const tiltPct = pct(s.tilt_raw, s.tilt_max);
            const panOutPct = pct(s.pan_out, s.pan_max);
            const tiltOutPct = pct(s.tilt_out, s.tilt_max);

            consoleMarker.style.left = panPct + '%';
            consoleMarker.style.top = (100 - tiltPct) + '%';
            outputMarker.style.left = panOutPct + '%';
            outputMarker.style.top = (100 - tiltOutPct) + '%';

            panRaw.textContent = s.pan_raw;
            panOut.textContent = Math.round(s.pan_out);
            panOffset.textContent = Math.round(s.offset_pan);
            tiltRaw.textContent = s.tilt_raw;
            tiltOut.textContent = Math.round(s.tilt_out);
            tiltOffset.textContent = Math.round(s.offset_tilt);
        }
    } catch (e) {
        connDot.classList.remove('ok');
        connText.textContent = 'UI cannot reach the injector';
    }
}

async function pollLogs() {
    try {
        const res = await fetch('/api/logs');
        const lines = await res.json();
        logBox.textContent = lines.join('\n');
        logBox.scrollTop = logBox.scrollHeight;
    } catch (e) {
        // ignore - status poll already reports connectivity problems
    }
}

setInterval(pollStatus, 300);
setInterval(pollLogs, 1000);
pollStatus();
pollLogs();

// --- Settings ---

const lockOverlay = document.getElementById('lockOverlay');
const settingsForm = document.getElementById('settingsForm');
const codeInput = document.getElementById('codeInput');
const unlockBtn = document.getElementById('unlockBtn');
const saveMessage = document.getElementById('saveMessage');

let unlockCode = null;

function fillForm(profile) {
    document.getElementById('listenIp').value = profile.listen.ip;
    document.getElementById('listenPort').value = profile.listen.port;
    document.getElementById('receiveUniverse').value = profile.receive_universe;
    document.getElementById('targetIp').value = profile.target.ip;
    document.getElementById('targetPort').value = profile.target.port;
    document.getElementById('sendUniverse').value = profile.send_universe;
    document.getElementById('panAddress').value = profile.channels.pan.address;
    document.getElementById('panBits').value = profile.channels.pan.bits;
    document.getElementById('tiltAddress').value = profile.channels.tilt.address;
    document.getElementById('tiltBits').value = profile.channels.tilt.bits;
    document.getElementById('joystickIndex').value = profile.controller.joystick_index;
    document.getElementById('panAxis').value = profile.controller.pan_axis;
    document.getElementById('tiltAxis').value = profile.controller.tilt_axis;
    document.getElementById('deadzone').value = profile.controller.deadzone;
    document.getElementById('panSpeed').value = profile.controller.pan_speed;
    document.getElementById('tiltSpeed').value = profile.controller.tilt_speed;
    document.getElementById('invertPan').checked = !!profile.controller.invert_pan;
    document.getElementById('invertTilt').checked = !!profile.controller.invert_tilt;
    document.getElementById('returnSpeed').value = profile.controller.return_speed;
    document.getElementById('updateRate').value = profile.update_rate_hz;
}

unlockBtn.addEventListener('click', async () => {
    const code = codeInput.value.trim();
    if (code.length !== 4) {
        saveMessage.textContent = 'Enter the 4-digit code.';
        saveMessage.className = 'error';
        return;
    }
    unlockCode = code;
    const res = await fetch('/api/settings');
    const profile = await res.json();
    fillForm(profile);
    lockOverlay.hidden = true;
    settingsForm.hidden = false;
    restartRow.hidden = false;
    saveMessage.textContent = '';
    saveMessage.className = '';
});

settingsForm.addEventListener('submit', async (ev) => {
    ev.preventDefault();

    const data = {
        listen: {
            ip: document.getElementById('listenIp').value,
            port: parseInt(document.getElementById('listenPort').value, 10),
        },
        receive_universe: parseInt(document.getElementById('receiveUniverse').value, 10),
        target: {
            ip: document.getElementById('targetIp').value,
            port: parseInt(document.getElementById('targetPort').value, 10),
        },
        send_universe: parseInt(document.getElementById('sendUniverse').value, 10),
        channels: {
            pan: {
                address: parseInt(document.getElementById('panAddress').value, 10),
                bits: parseInt(document.getElementById('panBits').value, 10),
            },
            tilt: {
                address: parseInt(document.getElementById('tiltAddress').value, 10),
                bits: parseInt(document.getElementById('tiltBits').value, 10),
            },
        },
        controller: {
            joystick_index: parseInt(document.getElementById('joystickIndex').value, 10),
            pan_axis: parseInt(document.getElementById('panAxis').value, 10),
            tilt_axis: parseInt(document.getElementById('tiltAxis').value, 10),
            deadzone: parseFloat(document.getElementById('deadzone').value),
            pan_speed: parseFloat(document.getElementById('panSpeed').value),
            tilt_speed: parseFloat(document.getElementById('tiltSpeed').value),
            invert_pan: document.getElementById('invertPan').checked,
            invert_tilt: document.getElementById('invertTilt').checked,
            return_speed: parseFloat(document.getElementById('returnSpeed').value),
        },
        update_rate_hz: parseInt(document.getElementById('updateRate').value, 10),
    };

    const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: unlockCode, data }),
    });
    const body = await res.json();

    if (res.ok && body.ok) {
        saveMessage.textContent = body.note;
        saveMessage.className = 'success';
    } else {
        saveMessage.textContent = body.error || 'Save failed.';
        saveMessage.className = 'error';
    }
});

const restartRow = document.getElementById('restartRow');
const restartBtn = document.getElementById('restartBtn');
const restartMessage = document.getElementById('restartMessage');

restartBtn.addEventListener('click', async () => {
    if (!confirm('Restart the injector now? Art-Net output will pause briefly.')) {
        return;
    }
    try {
        const res = await fetch('/api/restart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: unlockCode }),
        });
        const body = await res.json();
        if (res.ok && body.ok) {
            restartMessage.textContent = 'Restarting - reconnecting automatically...';
            restartMessage.className = 'success';
        } else {
            restartMessage.textContent = body.error || 'Restart failed.';
            restartMessage.className = 'error';
        }
    } catch (e) {
        restartMessage.textContent = 'Restarting - reconnecting automatically...';
        restartMessage.className = 'success';
    }
});
