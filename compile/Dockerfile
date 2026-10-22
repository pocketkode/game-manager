# © 2026 PocketKode · SPDX-License-Identifier: GPL-3.0-only
# Compiles the app's Python modules into native libraries for muOS handhelds (aarch64,
# Python 3.11, glibc 2.37). Debian bookworm has glibc 2.36, so the results run on muOS.
FROM --platform=linux/arm64 python:3.11.8-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev binutils \
    libsdl2-2.0-0 libsdl2-ttf-2.0-0 libsdl2-image-2.0-0 && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "cython==3.0.11" "setuptools<75"
