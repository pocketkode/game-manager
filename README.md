# Game Manager

**Find, install and manage free homebrew games on muOS handhelds**, from Homebrew Hub, itch.io and PortMaster, right on the
game console. Checks each game can run on your device before installing it. Free. No PocketKode account, no ads, no
tracking (itch.io downloads need your own free itch.io API key).

| | |
|---|---|
| ![Home screen](screenshots/home.png) | ![A game's page](screenshots/game-page.png) |
| ![PortMaster ports](screenshots/portmaster.png) | ![Sort and filter](screenshots/sort-filter.png) |
| ![Search your SD card and all the sources](screenshots/search-sd.png) | ![My games](screenshots/my-games.png) |
| ![Recently played, with favourites](screenshots/recently-played.png) | ![PortMaster ports on the SD card](screenshots/ports-shelf.png) |

- **Three sources**: Homebrew Hub (Game Boy, GBC, GBA and NES homebrew), itch.io (homebrew for NES, SNES, Game Boy, GBA, Mega Drive and more, plus your own library) and PortMaster ports.
- **Checks every game** before and after downloading, and installs into the ROM folders muOS already uses, with box art and a description.
- **Search** your SD card and all the sources at once, **My games** (play time, favourites, box art, a PortMaster ports shelf), **Try it**, and a **panic exit** (hold SELECT + START to quit a stuck game).

## Guide

How to install and use Game Manager, with screenshots — finding games, installing, Try it, My games, box art and
more — is on **[pocketkode.com/apps/game-manager](https://pocketkode.com/apps/game-manager/)**. Downloads come only from there.

## Privacy

Game Manager never reads or sends your saves, screenshots or files, and doesn't track what you play. For updates it
reads the latest release on GitHub (switchable in **START → Settings**), and an update you choose to install is
downloaded from pocketkode.com; it sends nothing about you or the handheld.
To list and download games it connects to Homebrew Hub, itch.io, PortMaster (GitHub) and the libretro box-art library;
your searches go to those sites, and your itch.io API key only to itch.io. No ads, no analytics, no tracking.

## Build

`bash build.sh` (needs bash, curl, unzip, zip and python3; no Docker) produces `dist/GameManager.muxapp` — the app's Python
source as it is, plus PySDL2 and the fonts. The settings in `config.ini` are documented in `config.ini.example`.

## Third-party

- **PySDL2** ([py-sdl2](https://github.com/py-sdl/py-sdl2)), © Marcus von Appen, CC0 or zlib: bundled unmodified in
  `deps/sdl2/` (licence in `licenses/`).
- **DejaVu Sans** ([dejavu-fonts](https://dejavu-fonts.github.io/)), Bitstream Vera Fonts license, and **Noto Sans JP,
  SC and KR** ([noto-cjk](https://github.com/notofonts/noto-cjk)), SIL Open Font License 1.1: `fonts/` (licences in
  `licenses/`).
- Game listings and files come from [Homebrew Hub](https://hh3.gbdev.io), [itch.io](https://itch.io) and
  [PortMaster](https://portmaster.games), and box art from the [libretro thumbnail library](https://thumbnails.libretro.com),
  fetched on your handheld when you ask. Games belong to their creators and come with their own licenses. Game Manager
  is an independent project, not affiliated with or endorsed by any of them, muOS or Anbernic.

## License

© 2026 PocketKode. All rights reserved. Bundled third-party parts keep their own licenses (see the `licenses/` folder).
Bug reports and ideas: **feedback@pocketkode.com**.
