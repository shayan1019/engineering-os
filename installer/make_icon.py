#!/usr/bin/env python3
"""Generate the installer icon (32x32, 32-bit ICO) without any image library.

The icon is drawn from primitives so the build stays dependency-free: a dark
rounded tile with a light "E" and a small accent bar. Running this twice
produces byte-identical output, so the icon can be committed and verified.
"""
import struct
import sys

SIZE = 32
BG = (16, 22, 34, 255)        # deep slate
FG = (233, 238, 247, 255)     # near-white
ACCENT = (56, 189, 248, 255)  # cyan accent


def blank():
    return [[(0, 0, 0, 0) for _ in range(SIZE)] for _ in range(SIZE)]


def rect(px, x0, y0, x1, y1, color):
    for y in range(max(0, y0), min(SIZE, y1)):
        for x in range(max(0, x0), min(SIZE, x1)):
            px[y][x] = color


def rounded(px, x0, y0, x1, y1, r, color):
    for y in range(y0, y1):
        for x in range(x0, x1):
            dx = min(x - x0, x1 - 1 - x)
            dy = min(y - y0, y1 - 1 - y)
            if dx < r and dy < r:
                if (r - dx) ** 2 + (r - dy) ** 2 > r * r:
                    continue
            px[y][x] = color


def glyph(px):
    # "E"
    rect(px, 8, 8, 12, 24, FG)
    rect(px, 12, 8, 21, 11, FG)
    rect(px, 12, 14, 18, 17, FG)
    rect(px, 12, 21, 21, 24, FG)
    # accent dot
    rect(px, 23, 19, 26, 22, ACCENT)


def to_bmp(px):
    # BITMAPINFOHEADER + XOR (BGRA, bottom-up) + AND mask
    header = struct.pack(
        "<IiiHHIIiiII",
        40, SIZE, SIZE * 2, 1, 32, 0, SIZE * SIZE * 4, 0, 0, 0, 0,
    )
    xor = bytearray()
    for y in range(SIZE - 1, -1, -1):
        for x in range(SIZE):
            r, g, b, a = px[y][x]
            xor += bytes((b, g, r, a))
    and_mask = bytearray()
    stride = ((SIZE + 31) // 32) * 4
    for y in range(SIZE - 1, -1, -1):
        row = bytearray(stride)
        for x in range(SIZE):
            if px[y][x][3] == 0:
                row[x // 8] |= 0x80 >> (x % 8)
        and_mask += row
    return header + bytes(xor) + bytes(and_mask)


def main(out_path):
    px = blank()
    rounded(px, 1, 1, 31, 31, 6, BG)
    glyph(px)
    data = to_bmp(px)
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, len(data), 6 + 16)
    with open(out_path, "wb") as fh:
        fh.write(header + entry + data)
    print(f"wrote {out_path} ({len(data) + 22} bytes)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eos.ico")
