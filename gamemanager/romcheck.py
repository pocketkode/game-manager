# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Reads ROM headers to check a download before it is installed.

check() returns a Verdict:
  level  "ok" (should run), "warn" (probably runs, see notes) or "bad" (don't install)
  system the system the file really is (e.g. a "Game Boy" game that is Color-only -> "gbc")
  core   a core-file prefix to prefer, e.g. "mgba_" (None = muOS default)
  notes  short sentences for the user
"""
import hashlib
import os

from .systems import BY_ID, system_for_file

# SHA-256 of the 48-byte logo every licensed Game Boy cartridge header carries at 0x104.
# Comparing a checksum keeps Nintendo's logo data out of this code.
GB_LOGO_SHA256 = "daf4cabdc852baa0291849203f0b41fd0b4ecd58e0d7aff4a509f5de4d7f9a2e"

GB_CART = {
    0x00: "ROM only", 0x01: "MBC1", 0x02: "MBC1", 0x03: "MBC1", 0x05: "MBC2", 0x06: "MBC2",
    0x08: "ROM+RAM", 0x09: "ROM+RAM", 0x0B: "MMM01", 0x0C: "MMM01", 0x0D: "MMM01",
    0x0F: "MBC3", 0x10: "MBC3", 0x11: "MBC3", 0x12: "MBC3", 0x13: "MBC3",
    0x19: "MBC5", 0x1A: "MBC5", 0x1B: "MBC5", 0x1C: "MBC5", 0x1D: "MBC5", 0x1E: "MBC5",
    0x20: "MBC6", 0x22: "MBC7", 0xFC: "Pocket Camera", 0xFD: "TAMA5", 0xFE: "HuC3", 0xFF: "HuC1",
}
# Cartridge chips the default core (Gambatte) doesn't handle; mGBA does.
GB_NEEDS_MGBA = {0x20, 0x22, 0xFC, 0xFD}

# Simple, very common NES mappers every core handles (QuickNES only does these).
NES_BASIC_MAPPERS = {0, 1, 2, 3, 4, 7}

MAX_SIZE = {"gb": 8, "gbc": 8, "gba": 32, "nes": 8, "snes": 12, "md": 10, "sms": 4, "gg": 4}  # MB


class Verdict:
    def __init__(self, system, level="ok", notes=None, core=None):
        self.system = system
        self.level = level
        self.notes = list(notes or [])
        self.core = core

    def warn(self, note):
        if self.level == "ok":
            self.level = "warn"
        self.notes.append(note)

    def fail(self, note):
        self.level = "bad"
        self.notes.append(note)

    def __repr__(self):
        return f"Verdict({self.system}, {self.level}, core={self.core}, {self.notes})"


def check(path, hint=None):
    sid = system_for_file(path, hint) or hint
    if sid not in BY_ID:
        return Verdict(sid, "bad", ["This isn't a ROM for a console this app supports."])
    size = os.path.getsize(path)
    v = Verdict(sid)
    if size == 0:
        v.fail("The file is empty.")
        return v
    if size > MAX_SIZE[sid] * 1024 * 1024:
        v.fail(f"The file is too big to be a {BY_ID[sid].name} ROM ({size // 1024} KB).")
        return v
    with open(path, "rb") as f:
        head = f.read(0x10000)
    sample = head[:1024]
    if sample and sum(c in (9, 10, 13) or 32 <= c < 127 for c in sample) > 0.95 * len(sample):
        v.fail("This is a text file, not a ROM.")
        return v
    checker = {"gb": _gb, "gbc": _gb, "gba": _gba, "nes": _nes, "snes": _snes, "md": _md,
               "sms": _sega8, "gg": _sega8}.get(sid)
    if checker:
        checker(head, size, v)
    return v


def _gb(b, size, v):
    if len(b) < 0x150:
        v.fail("The file is too small to be a Game Boy ROM.")
        return
    if hashlib.sha256(b[0x104:0x134]).hexdigest() != GB_LOGO_SHA256:
        v.warn("The Game Boy header logo is missing. Emulators usually still run it.")
    cgb = b[0x143]
    if cgb == 0xC0:
        if v.system == "gb":
            v.notes.append("Game Boy Color only, so it goes in the GBC folder.")
        v.system = "gbc"
    elif cgb == 0x80:
        v.notes.append("Works on Game Boy and Game Boy Color.")
    cart = b[0x147]
    kind = GB_CART.get(cart)
    if kind is None:
        v.warn(f"Unknown cartridge type (0x{cart:02X}). It may not run.")
    elif cart in GB_NEEDS_MGBA:
        v.core = "mgba_"
        v.warn(f"Uses the {kind} chip: choose the mGBA core if it doesn't start.")
    x = 0
    for i in range(0x134, 0x14D):
        x = (x - b[i] - 1) & 0xFF
    if x != b[0x14D]:
        v.warn("Header checksum is wrong. Emulators ignore this, so it will most likely run.")


def _gba(b, size, v):
    if len(b) < 0xC0:
        v.fail("The file is too small to be a GBA ROM.")
        return
    if b[0xB2] != 0x96:
        v.warn("The GBA header looks unusual. mGBA usually still runs it.")
    chk = (-(sum(b[0xA0:0xBD]) + 0x19)) & 0xFF
    if chk != b[0xBD]:
        v.notes.append("Header checksum is wrong (fine in emulators, not on real hardware).")


def _nes(b, size, v):
    if b[:4] != b"NES\x1a":
        v.fail("This isn't a valid NES ROM (no iNES header).")
        return
    mapper = (b[6] >> 4) | (b[7] & 0xF0)
    if (b[7] & 0x0C) == 0x08:  # NES 2.0
        mapper |= (b[8] & 0x0F) << 8
    if mapper not in NES_BASIC_MAPPERS:
        v.core = "fceumm_"
        v.notes.append(f"Uses mapper {mapper}: FCEUmm (muOS's default) handles it; QuickNES may not.")
    if mapper > 255:
        v.warn(f"Mapper {mapper} is rare. It may not run in every core.")
    prg = b[4] * 16384
    if prg and size < 16 + prg:
        v.warn("The ROM seems to be cut short.")


def _snes(b, size, v):
    off = 512 if size % 1024 == 512 else 0
    if off:
        v.notes.append("Has a 512-byte copier header (fine in emulators).")
    best = None
    for base in (0x7FC0, 0xFFC0):
        h = b[off + base: off + base + 0x40]
        if len(h) < 0x40:
            continue
        title_ok = all(32 <= c < 127 for c in h[:21])
        comp_ok = ((h[0x1C] | h[0x1D] << 8) ^ (h[0x1E] | h[0x1F] << 8)) == 0xFFFF
        score = title_ok + 2 * comp_ok + ((h[0x15] & 0xE0) == 0x20)
        if best is None or score > best[0]:
            best = (score, h)
    if not best or best[0] == 0:
        v.warn("No valid SNES header found. It may still run in Snes9x.")
        return
    chip = best[1][0x16]
    if chip in (0x13, 0x14, 0x15, 0x1A):
        v.core = "snes9x_"
        v.warn("Uses the Super FX chip: use the Snes9x core (it can be slow).")
    elif chip in (0x34, 0x35):
        v.core = "snes9x_"
        v.warn("Uses the SA-1 chip: use the Snes9x core.")


def _md(b, size, v):
    if _looks_smd(b):
        v.warn("Looks like the old interleaved .smd format. Genesis Plus GX can usually run it.")
        return
    if b[0x100:0x104] == b"SEGA" or b[0x101:0x105] == b"SEGA":
        if b[0x100:0x108] == b"SEGA 32X":
            v.fail("This is a 32X game, not a Mega Drive game.")
        return
    v.warn("No SEGA header. Most emulators still run it.")


def _looks_smd(b):
    # .smd dumps start with a 512-byte header whose bytes 8 and 9 are 0xAA 0xBB
    return len(b) > 10 and b[8] == 0xAA and b[9] == 0xBB


def _sega8(b, size, v):
    for base in (0x7FF0, 0x3FF0, 0x1FF0):
        if b[base:base + 8] == b"TMR SEGA":
            region = b[base + 0xF] >> 4
            if region in (5, 6, 7) and v.system == "sms":
                v.system = "gg"
                v.notes.append("This is a Game Gear game, so it goes in the GG folder.")
            elif region in (3, 4) and v.system == "gg":
                v.system = "sms"
                v.notes.append("This is a Master System game, so it goes in the SMS folder.")
            return
    v.notes.append("No SEGA header (common for homebrew; emulators run it).")
