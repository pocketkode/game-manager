# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Gamepad + keyboard input for muOS (SDL2 GameController API)."""
import ctypes
import time

import sdl2

BUTTONS = {
    sdl2.SDL_CONTROLLER_BUTTON_A: "A",
    sdl2.SDL_CONTROLLER_BUTTON_B: "B",
    sdl2.SDL_CONTROLLER_BUTTON_X: "X",
    sdl2.SDL_CONTROLLER_BUTTON_Y: "Y",
    sdl2.SDL_CONTROLLER_BUTTON_LEFTSHOULDER: "L1",
    sdl2.SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: "R1",
    sdl2.SDL_CONTROLLER_BUTTON_BACK: "SELECT",
    sdl2.SDL_CONTROLLER_BUTTON_START: "START",
    sdl2.SDL_CONTROLLER_BUTTON_GUIDE: "MENU",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_UP: "UP",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_DOWN: "DOWN",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_LEFT: "LEFT",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_RIGHT: "RIGHT",
}

# Keyboard fallback (handy when testing on a PC, or with a USB keyboard)
KEYS = {
    sdl2.SDLK_UP: "UP",
    sdl2.SDLK_DOWN: "DOWN",
    sdl2.SDLK_LEFT: "LEFT",
    sdl2.SDLK_RIGHT: "RIGHT",
    sdl2.SDLK_RETURN: "A",
    sdl2.SDLK_ESCAPE: "B",
    sdl2.SDLK_BACKSPACE: "B",
    sdl2.SDLK_x: "X",
    sdl2.SDLK_y: "Y",
    sdl2.SDLK_q: "L1",
    sdl2.SDLK_e: "R1",
    sdl2.SDLK_TAB: "SELECT",
    sdl2.SDLK_s: "START",
}

REPEATABLE = {"UP", "DOWN", "LEFT", "RIGHT", "L1", "R1", "B"}
REPEAT_DELAY = 0.35
REPEAT_RATE = 0.08
STICK_THRESHOLD = 16000
TRIGGER_THRESHOLD = 16000


class Input:
    def __init__(self, swap_ab=False):
        self.swap_ab = swap_ab
        self.controllers = {}
        self.held = {}
        self.quit = False
        self._axis_dirs = {}
        sdl2.SDL_GameControllerEventState(sdl2.SDL_ENABLE)
        for i in range(sdl2.SDL_NumJoysticks()):
            self._open(i)

    def _open(self, index):
        if not sdl2.SDL_IsGameController(index):
            print(f"[input] joystick {index} has no controller mapping")
            return
        pad = sdl2.SDL_GameControllerOpen(index)
        if pad:
            joy = sdl2.SDL_GameControllerGetJoystick(pad)
            self.controllers[sdl2.SDL_JoystickInstanceID(joy)] = pad
            name = sdl2.SDL_GameControllerName(pad) or b"?"
            print(f"[input] opened controller: {name.decode(errors='replace')}")

    def _map(self, name):
        if self.swap_ab and name in ("A", "B"):
            return "B" if name == "A" else "A"
        return name

    def _down(self, name, out):
        if not name:
            return
        name = self._map(name)
        if name in self.held:
            return
        now = time.monotonic()
        out.append(name)
        self.held[name] = now + REPEAT_DELAY

    def _up(self, name):
        if name:
            self.held.pop(self._map(name), None)

    def _axis(self, key, value, neg, pos, threshold, out):
        new = neg if value < -threshold else pos if value > threshold else None
        old = self._axis_dirs.get(key)
        if new == old:
            return
        if old:
            self._up(old)
        if new:
            self._down(new, out)
        self._axis_dirs[key] = new

    def clear(self):
        self.held.clear()
        self._axis_dirs.clear()

    def poll(self):
        """Return the list of buttons pressed (or auto-repeated) since last call."""
        out = []
        ev = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(ev)):
            t = ev.type
            if t == sdl2.SDL_QUIT:
                self.quit = True
            elif t == sdl2.SDL_CONTROLLERDEVICEADDED:
                self._open(ev.cdevice.which)
            elif t == sdl2.SDL_CONTROLLERDEVICEREMOVED:
                pad = self.controllers.pop(ev.cdevice.which, None)
                if pad:
                    sdl2.SDL_GameControllerClose(pad)
            elif t == sdl2.SDL_CONTROLLERBUTTONDOWN:
                self._down(BUTTONS.get(ev.cbutton.button), out)
            elif t == sdl2.SDL_CONTROLLERBUTTONUP:
                self._up(BUTTONS.get(ev.cbutton.button))
            elif t == sdl2.SDL_CONTROLLERAXISMOTION:
                a, v = ev.caxis.axis, ev.caxis.value
                if a == sdl2.SDL_CONTROLLER_AXIS_LEFTX:
                    self._axis("lx", v, "LEFT", "RIGHT", STICK_THRESHOLD, out)
                elif a == sdl2.SDL_CONTROLLER_AXIS_LEFTY:
                    self._axis("ly", v, "UP", "DOWN", STICK_THRESHOLD, out)
                elif a == sdl2.SDL_CONTROLLER_AXIS_TRIGGERLEFT:
                    self._axis("l2", v, None, "L2", TRIGGER_THRESHOLD, out)
                elif a == sdl2.SDL_CONTROLLER_AXIS_TRIGGERRIGHT:
                    self._axis("r2", v, None, "R2", TRIGGER_THRESHOLD, out)
            elif t == sdl2.SDL_KEYDOWN and not ev.key.repeat:
                self._down(KEYS.get(ev.key.keysym.sym), out)
            elif t == sdl2.SDL_KEYUP:
                self._up(KEYS.get(ev.key.keysym.sym))

        now = time.monotonic()
        for name, next_t in list(self.held.items()):
            if name in REPEATABLE and now >= next_t:
                out.append(name)
                self.held[name] = now + REPEAT_RATE
        return out
