# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Supported systems, and what this muOS device can actually run."""
import configparser
import json
import os


class System:
    def __init__(self, sid, name, muos, folder, exts):
        self.id = sid
        self.name = name        # short name shown in the app
        self.muos = muos        # muOS system / catalogue name
        self.folder = folder    # folder to create under ROMS if none exists yet
        self.exts = exts        # ROM file extensions, preferred first


SYSTEMS = [
    System("gb", "Game Boy", "Nintendo Game Boy", "GB", (".gb",)),
    System("gbc", "Game Boy Color", "Nintendo Game Boy Color", "GBC", (".gbc", ".gb")),
    System("gba", "Game Boy Advance", "Nintendo Game Boy Advance", "GBA", (".gba",)),
    System("nes", "NES / Famicom", "Nintendo NES - Famicom", "NES", (".nes",)),
    System("snes", "SNES / Super Famicom", "Nintendo SNES - SFC", "SNES", (".sfc", ".smc")),
    System("md", "Mega Drive / Genesis", "Sega Mega Drive - Genesis", "MD", (".md", ".gen", ".bin", ".smd")),
    System("sms", "Master System", "Sega Master System", "SMS", (".sms",)),
    System("gg", "Game Gear", "Sega Game Gear", "GG", (".gg",)),
    System("ports", "PC ports (PortMaster)", "External - Ports", "Ports", (".sh",)),
]
BY_ID = {s.id: s for s in SYSTEMS}

# Extension -> system, for files whose system isn't known in advance.
# ".bin" is left out on purpose: it is used by too many systems.
EXT_SYSTEM = {".gb": "gb", ".gbc": "gbc", ".gba": "gba", ".nes": "nes", ".sfc": "snes",
              ".smc": "snes", ".md": "md", ".gen": "md", ".smd": "md", ".sms": "sms", ".gg": "gg"}
ROM_EXTS = set(EXT_SYSTEM) | {".bin"}


def system_for_file(filename, hint=None):
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".bin":
        return hint if hint == "md" else None
    sid = EXT_SYSTEM.get(ext)
    if sid == "gb" and hint == "gbc":
        return "gbc"
    return sid


class Core:
    def __init__(self, key, name, so, launcher):
        self.key = key            # muOS core id, e.g. "genesis plus gx"
        self.name = name          # e.g. "Genesis Plus GX"
        self.so = so              # e.g. "genesis_plus_gx_libretro.so"
        self.launcher = launcher  # e.g. /opt/muos/script/launch/lr-general.sh


class Device:
    """Reads muOS's own system/core tables so the app agrees with the frontend."""

    def __init__(self, share_dir="/opt/muos/share", roms_path="/mnt/mmc/ROMS",
                 catalogue_dir=None, bios_dir=None):
        self.share_dir = share_dir
        self.roms_path = roms_path
        self.catalogue_dir = catalogue_dir or next(
            (p for p in ("/run/muos/storage/info/catalogue", "/mnt/mmc/MUOS/info/catalogue")
             if os.path.isdir(p)), None)
        self.assign = {}
        try:
            with open(os.path.join(share_dir, "info", "assign", "assign.json")) as f:
                self.assign = {k.lower(): v for k, v in json.load(f).items() if isinstance(v, str)}
        except (OSError, ValueError):
            pass
        core_dir = os.path.join(share_dir, "core")
        self.core_files = set(os.listdir(core_dir)) if os.path.isdir(core_dir) else set()
        self._cores = {}
        # muOS's info folder (play time tracker, history…) sits next to the catalogue
        self.info_dir = os.path.dirname(self.catalogue_dir) if self.catalogue_dir else None
        self.bios_dir = bios_dir or next(
            (p for p in ("/run/muos/storage/bios", "/mnt/mmc/MUOS/bios") if os.path.isdir(p)), None)

    @property
    def is_muos(self):
        return bool(self.assign) and bool(self.core_files)

    def cores(self, sid):
        """Installed cores for one of this app's systems, muOS's default first."""
        return self.cores_for(BY_ID[sid].muos)

    def cores_for(self, muos_name):
        """Installed emulators for any muOS system (RetroArch cores and standalone ones), default first."""
        if muos_name in self._cores:
            return self._cores[muos_name]
        out, default = [], None
        adir = os.path.join(self.share_dir, "info", "assign", muos_name)
        if os.path.isdir(adir):
            for fn in sorted(os.listdir(adir)):
                if not fn.endswith(".ini"):
                    continue
                cp = configparser.ConfigParser(interpolation=None, allow_no_value=True, strict=False)
                try:
                    cp.read(os.path.join(adir, fn))
                except configparser.Error:
                    continue
                key = fn[:-4]
                if key == "global":
                    default = cp.get("global", "default", fallback=None)
                    continue
                so = cp.get(key, "core", fallback="")
                launcher = cp.get("launch", "exec", fallback="")
                # RetroArch cores that are really installed, or standalone emulators
                # (core=ext-…) whose launch script exists
                installed = so in self.core_files or (
                    (so.startswith("ext-") or so == "external") and os.path.exists(launcher))
                if so and launcher and installed:
                    out.append(Core(key, cp.get(key, "name", fallback=key), so, launcher))
        out.sort(key=lambda c: c.key != default)
        self._cores[muos_name] = out
        return out

    def core_by_so_prefix(self, sid, prefix):
        for c in self.cores(sid):
            if c.so.startswith(prefix):
                return c
        return None

    def rom_folder(self, sid):
        """Existing ROMS/<folder> that muOS maps to this system, else a new one to create."""
        want = BY_ID[sid].muos
        if os.path.isdir(self.roms_path):
            for d in sorted(os.listdir(self.roms_path)):
                if os.path.isdir(os.path.join(self.roms_path, d)) and self.assign.get(d.lower()) == want:
                    return os.path.join(self.roms_path, d)
        return os.path.join(self.roms_path, BY_ID[sid].folder)

    def art_paths(self, sid, rom_stem):
        """(box art png, description txt) paths for muOS's catalogue, or (None, None)."""
        if not self.catalogue_dir:
            return None, None
        base = os.path.join(self.catalogue_dir, BY_ID[sid].muos)
        return os.path.join(base, "box", rom_stem + ".png"), os.path.join(base, "text", rom_stem + ".txt")
