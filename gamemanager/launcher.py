# © 2026 PocketKode · SPDX-License-Identifier: GPL-3.0-only
"""Start a game with the same launch script muOS uses."""
import os
import subprocess
import time

from . import killswitch

FUNC_SH = "/opt/muos/script/var/func.sh"


def pick_core(device, muos_name, preferred_so=None):
    cores = device.cores_for(muos_name)
    if preferred_so:
        for c in cores:
            if c.so == preferred_so:
                return c
    return cores[0] if cores else None


def run(device, muos_name, path, log_path, preferred_so=None, exit_combo=killswitch.DEFAULT_COMBO,
        exit_hold=killswitch.HOLD_SECS):
    """Blocks until the emulator exits. Returns (seconds, exit_code, emulator_name).

    While the game runs, a watchdog (killswitch) watches the gamepad for the held exit_combo and kills the
    game if it fires, so a game with no exit of its own can still be left. exit_combo=None turns it off.
    """
    core = pick_core(device, muos_name, preferred_so)
    if not core:
        raise RuntimeError(f"No emulator is set up for {muos_name} on this device.")
    name = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    start = time.monotonic()
    with open(log_path, "w") as log:
        log.write(f"core={core.so} launcher={core.launcher} file={path}\n")
        log.flush()
        # start_new_session puts the emulator (and anything it starts) in its own process group, so the
        # watchdog can take the whole thing down at once.
        proc = subprocess.Popen(["sh", core.launcher, name, core.so, path],
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        watchdog = None
        if exit_combo:
            watchdog = killswitch.Watchdog(lambda: killswitch.kill_process_group(proc), exit_combo, exit_hold)
            watchdog.start()
        try:
            rc = proc.wait()
        finally:
            if watchdog:
                watchdog.stop()
    secs = time.monotonic() - start
    # The launch script marks the emulator as the foreground app; hand it back to us so
    # muOS's hotkeys and sleep handling treat this app as running again.
    if os.path.exists(FUNC_SH):
        subprocess.call(["sh", "-c", f'. {FUNC_SH}; SET_VAR "system" "foreground_process" "python3"'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return secs, rc, core.name
