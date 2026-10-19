#!/usr/bin/env python3
"""Turn one terminal-browser shared-memory frame into a base64 PNG on stdout.

Over SSH the terminal cannot read this machine's shared memory, so Claude Code
refuses { shm } image sources; a { png } source of at most 2 MiB still draws.

usage: png-relay.py <shm path> <rgb|rgba> <width> <height>
"""
import base64
import os
import re
import struct
import sys
import zlib

MAX_PNG = 2 * 1024 * 1024  # Claude Code caps a decoded byte source at 2 MiB


def encode(raw, width, height, channels, level):
    stride = width * channels
    rows = b"".join(b"\x00" + raw[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    color_type = 6 if channels == 4 else 2
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows, level)) + chunk(b"IEND", b""))


def halve(raw, width, height, channels):
    """Nearest-neighbour 2x downscale, done with slices so it stays fast."""
    stride = width * channels
    half_w, half_h = width // 2, height // 2
    out = bytearray(half_w * half_h * channels)
    out_stride = half_w * channels
    for y in range(half_h):
        row = raw[2 * y * stride:(2 * y + 1) * stride]
        dest = y * out_stride
        for c in range(channels):
            out[dest + c:dest + out_stride:channels] = row[c:half_w * 2 * channels:2 * channels]
    return bytes(out), half_w, half_h


def drop_stale(path):
    """Remove frames of the same stream older than this one; nothing else reads them."""
    folder, name = os.path.split(path)
    match = re.fullmatch(r"(.*-)(\d+)", name)
    if not match:
        return
    prefix, number = match.group(1), int(match.group(2))
    for other in os.listdir(folder):
        tail = other[len(prefix):]
        if other.startswith(prefix) and tail.isdigit() and int(tail) < number:
            try:
                os.unlink(os.path.join(folder, other))
            except OSError:
                pass


def main():
    path, fmt, width, height = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    channels = 4 if fmt == "rgba" else 3
    with open(path, "rb") as f:
        raw = f.read(width * height * channels)
    os.unlink(path)
    drop_stale(path)
    if len(raw) < width * height * channels:
        sys.exit("short frame")
    data = encode(raw, width, height, channels, 1)
    if len(data) > MAX_PNG:
        data = encode(raw, width, height, channels, 6)
    while len(data) > MAX_PNG and width > 64:
        raw, width, height = halve(raw, width, height, channels)
        data = encode(raw, width, height, channels, 6)
    if len(data) > MAX_PNG:
        sys.exit("frame too large")
    sys.stdout.write(base64.b64encode(data).decode())


if __name__ == "__main__":
    main()
