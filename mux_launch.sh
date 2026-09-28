#!/bin/sh
# HELP: Find, install and manage free homebrew games from Homebrew Hub, itch.io and PortMaster
# ICON: gamemanager
# GRID: Game Manager
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode

. /opt/muos/script/var/func.sh

APP_BIN="python3"
SETUP_APP "$APP_BIN" ""

SETUP_STAGE_OVERLAY

# -----------------------------------------------------------------------------

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR" || exit 1

mkdir -p "$APP_DIR/logs" "$APP_DIR/data"

export PYSDL2_DLL_PATH="/usr/lib"
export SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1
export PYTHONDONTWRITEBYTECODE=1

# Exit code 42: the app asks to be started again (to finish an update)
while :; do
	python3 -u main.py >"$APP_DIR/logs/app.log" 2>&1
	[ $? -eq 42 ] || break
done

# New games were added to ROMS; flush them to the SD card before muOS rescans
sync
