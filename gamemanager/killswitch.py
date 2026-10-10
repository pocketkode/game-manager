# © 2026 PocketKode. All rights reserved.
"""A panic exit while a game is running.

Once a game starts, the emulator owns the screen and the gamepad, so the app can't see button presses
through SDL any more. Some homebrew games and PortMaster ports have no way out at all. This watches the
gamepad straight from the kernel (/dev/input/event*) for a held combo (SELECT+START by default) and, when
it's held long enough, kills the game so the app comes back.

The combo-tracking (Combo) is plain logic with no device access, so it can be tested on a PC.
"""
import os
import select
import signal
import struct
import threading
import time

# Linux input_event on 64-bit: struct timeval (two longs) + type (u16) + code (u16) + value (s32).
_FORMAT = "llHHi"
_SIZE = struct.calcsize(_FORMAT)
EV_KEY = 0x01

# evdev key codes for the gamepad's Select and Start (BTN_SELECT, BTN_START). These are the usual codes on
# muOS handhelds; if a device reports different ones, set exit_combo in config.ini (see config.ini.example).
BTN_SELECT = 0x13A
BTN_START = 0x13B
DEFAULT_COMBO = (BTN_SELECT, BTN_START)
HOLD_SECS = 2.0


class Combo:
    """Tracks whether every button of a combo is held, and for how long."""

    def __init__(self, codes, hold_secs=HOLD_SECS):
        self.codes = set(codes)
        self.hold_secs = hold_secs
        self.held = set()
        self.since = None  # when the whole combo became held (monotonic), or None

    def update(self, code, value, now):
        """Feed one EV_KEY event. value: 1 down, 0 up, 2 auto-repeat."""
        if code not in self.codes:
            return
        if value == 0:
            self.held.discard(code)
            self.since = None
        else:
            self.held.add(code)
            if self.held == self.codes and self.since is None:
                self.since = now

    def fired(self, now):
        return self.since is not None and now - self.since >= self.hold_secs


class Watchdog(threading.Thread):
    """Reads every /dev/input/event* for the combo and calls on_trigger once when it's held long enough."""

    def __init__(self, on_trigger, codes=DEFAULT_COMBO, hold_secs=HOLD_SECS, input_dir="/dev/input"):
        super().__init__(daemon=True)
        self.on_trigger = on_trigger
        self.combo = Combo(codes, hold_secs)
        self.input_dir = input_dir
        self._stopped = threading.Event()

    def _open_pads(self):
        fds = {}
        try:
            names = sorted(os.listdir(self.input_dir))
        except OSError:
            return fds
        for n in names:
            if not n.startswith("event"):
                continue
            try:
                fds[os.open(os.path.join(self.input_dir, n), os.O_RDONLY | os.O_NONBLOCK)] = n
            except OSError:
                pass  # a device we can't open (busy or no permission); the others still work
        return fds

    def run(self):
        fds = self._open_pads()
        if not fds:
            return
        try:
            while not self._stopped.is_set():
                ready, _, _ = select.select(list(fds), [], [], 0.1)
                now = time.monotonic()
                for fd in ready:
                    try:
                        data = os.read(fd, _SIZE * 64)
                    except OSError:
                        continue
                    # evdev only ever returns whole events
                    for off in range(0, len(data) - _SIZE + 1, _SIZE):
                        _s, _us, typ, code, val = struct.unpack(_FORMAT, data[off:off + _SIZE])
                        if typ == EV_KEY:
                            self.combo.update(code, val, now)
                if self.combo.fired(now):
                    try:
                        self.on_trigger()
                    except Exception:  # noqa: BLE001
                        pass
                    return
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def stop(self):
        self._stopped.set()


def kill_process_group(proc, grace=2.0):
    """End a game started with start_new_session=True: SIGTERM its whole group, then SIGKILL if it hangs on."""
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)
