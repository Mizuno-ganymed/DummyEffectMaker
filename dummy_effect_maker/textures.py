# -*- coding: utf-8 -*-
"""天候の絵 (雨の筋・雪の粒・煙・霧の模様) を作る。Maya にも Blender にも Qt にも依存しない。

どれも白 (色はマテリアルで付ける) + 透明度の PNG。同じ種なら毎回同じ絵になる。
"""
from __future__ import absolute_import

import math
import random
import struct
import zlib

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


class Canvas(object):
    """RGBA (0〜1 の float) の絵。"""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.rgb = [[(1.0, 1.0, 1.0)] * w for _ in range(h)]
        self.a = [[0.0] * w for _ in range(h)]

    def blend(self, x, y, coverage, alpha=1.0, color=(1.0, 1.0, 1.0), wrap=False):
        """(x, y) に色を重ねる (coverage = 画素が覆われる割合)。"""
        if wrap:
            x, y = x % self.w, y % self.h
        elif not (0 <= x < self.w and 0 <= y < self.h):
            return
        a = coverage * alpha
        if a <= 0:
            return
        old_a = self.a[y][x]
        new_a = a + old_a * (1 - a)
        if new_a > 0:
            oc = self.rgb[y][x]
            self.rgb[y][x] = tuple((c * a + o * old_a * (1 - a)) / new_a for c, o in zip(color, oc))
        self.a[y][x] = new_a

    def circle(self, cx, cy, r, alpha=1.0, color=(1.0, 1.0, 1.0), wrap=False):
        """ふちを 1 画素ぼかした丸。"""
        for y in range(int(math.floor(cy - r - 1)), int(math.ceil(cy + r + 1)) + 1):
            for x in range(int(math.floor(cx - r - 1)), int(math.ceil(cx + r + 1)) + 1):
                d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                cov = min(1.0, max(0.0, r - d + 0.5))
                if cov > 0:
                    self.blend(x, y, cov, alpha, color, wrap)

    def streak(self, x, y, width, length, alpha=1.0, wrap=False):
        """縦の線 (両端は丸く細くなる)。"""
        for yy in range(int(math.floor(y)), int(math.ceil(y + length))):
            t = (yy + 0.5 - y) / max(length, 1e-6)
            taper = min(1.0, t * 6, (1 - t) * 6)
            for xx in range(int(math.floor(x - width)), int(math.ceil(x + width)) + 1):
                cov = min(1.0, max(0.0, width / 2.0 - abs(xx + 0.5 - x) + 0.5))
                self.blend(xx, yy, cov * max(0.0, taper), alpha, wrap=wrap)

    def rgba_bytes(self, flip=True):
        """PNG 用の行 (上から)。flip=True なら下の行から (この Canvas は y が下向きでそのまま)。"""
        rows = []
        for y in range(self.h):
            row = bytearray()
            for x in range(self.w):
                r, g, b = self.rgb[y][x]
                row += bytes((int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5),
                              int(min(1.0, self.a[y][x]) * 255 + 0.5)))
            rows.append(bytes(row))
        return rows


def write_png(path, canvas):
    rows = canvas.rgba_bytes()
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = (PNG_SIGNATURE
           + chunk(b"IHDR", struct.pack(">IIBBBBB", canvas.w, canvas.h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------- 絵
def rain(path):
    """縦に繰り返せる雨の筋 (256 x 512)。"""
    c = Canvas(256, 512)
    rng = random.Random("dfx-rain")
    for _ in range(70):
        c.streak(rng.uniform(0, 256), rng.uniform(0, 512), rng.uniform(1.0, 2.2),
                 rng.uniform(40, 140), rng.uniform(0.47, 1.0), wrap=True)
    write_png(path, c)


def snow(path):
    """上下左右に繰り返せる雪の粒 (512 x 512)。"""
    c = Canvas(512, 512)
    rng = random.Random("dfx-snow")
    for _ in range(160):
        c.circle(rng.uniform(0, 512), rng.uniform(0, 512), rng.uniform(1.5, 5.0),
                 rng.uniform(0.67, 1.0), wrap=True)
    write_png(path, c)


def puff(path):
    """アニメ調の煙の塊 (丸の集まり。下が影色、上がハイライトの 2 色)。"""
    c = Canvas(256, 256)
    rng = random.Random("dfx-puff")
    circles = [(128.0, 140.0, 70.0)]
    for _ in range(7):
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(35, 60)
        circles.append((128 + d * math.cos(a), 140 + d * math.sin(a) * 0.7, rng.uniform(32, 48)))
    shade, light = (0.667, 0.667, 0.667), (1.0, 1.0, 1.0)
    for y in range(c.h):
        for x in range(c.w):
            px, py = x + 0.5, y + 0.5
            cov = max(min(1.0, max(0.0, r - math.hypot(px - cx, py - cy) + 0.5)) for cx, cy, r in circles)
            if cov <= 0:
                continue
            hi = max(min(1.0, max(0.0, r * 0.82 - math.hypot(px - (cx - r * 0.12), py - (cy - r * 0.18)) + 0.5))
                     for cx, cy, r in circles)
            color = tuple(s + (l - s) * hi for s, l in zip(shade, light))
            c.blend(x, y, cov, 1.0, color)
    write_png(path, c)


def value_noise(w, h, cells, seed, octaves=4):
    """上下左右に繰り返せる値ノイズ (0〜1) の 2 次元リスト。"""
    rng = random.Random(seed)
    layers = []
    for o in range(octaves):
        n = cells * (2 ** o)
        layers.append((n, [[rng.random() for _ in range(n)] for _ in range(n)], 0.5 ** o))
    total = sum(a for _, _, a in layers)
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            v = 0.0
            for n, grid, amp in layers:
                fx, fy = x * n / float(w), y * n / float(h)
                ix, iy = int(fx), int(fy)
                tx, ty = fx - ix, fy - iy
                tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
                a = grid[iy % n][ix % n]
                b = grid[iy % n][(ix + 1) % n]
                cc = grid[(iy + 1) % n][ix % n]
                d = grid[(iy + 1) % n][(ix + 1) % n]
                v += amp * ((a + (b - a) * tx) * (1 - ty) + (cc + (d - cc) * tx) * ty)
            row.append(v / total)
        out.append(row)
    return out


def posterize(v, levels=(0.42, 0.55, 0.68), alphas=(0.0, 0.35, 0.7, 1.0), soft=0.02):
    """アニメ調の段 (くっきりした濃さの段に分ける。境目だけ少しぼかす)。"""
    a = alphas[0]
    for edge, value in zip(levels, alphas[1:]):
        t = min(1.0, max(0.0, (v - edge + soft) / (2 * soft)))
        a = a + (value - a) * t if t > 0 else a
    return a


def _alpha_canvas(rows):
    h, w = len(rows), len(rows[0])
    c = Canvas(w, h)
    for y in range(h):
        for x in range(w):
            c.a[y][x] = min(1.0, max(0.0, rows[y][x]))
    return c


def fog_tile(path):
    """繰り返せる霧の模様 (段になった濃さ)。地霧の重ね板用。"""
    noise = value_noise(256, 256, 3, "dfx-fog-tile")
    write_png(path, _alpha_canvas([[posterize(v) for v in row] for row in noise]))


def fog_card(path):
    """霧の板 1 枚分 (ふちに向かって消える、段になった雲の形)。"""
    w = h = 256
    noise = value_noise(w, h, 2, "dfx-fog-card", octaves=3)
    rows = []
    for y, row in enumerate(noise):
        out = []
        for x, v in enumerate(row):
            d = math.hypot((x - w / 2.0) / (w / 2.0), (y - h / 2.0) / (h / 2.0))
            fall = max(0.0, 1.0 - d) ** 0.8
            out.append(posterize(v * 0.55 + fall * 0.6, levels=(0.5, 0.65, 0.8),
                                 alphas=(0.0, 0.3, 0.6, 0.9)))
        rows.append(out)
    write_png(path, _alpha_canvas(rows))


GENERATORS = {"rain": rain, "snow": snow, "puff": puff, "fog_tile": fog_tile, "fog_card": fog_card}
VERSION = 2   # 絵を変えたら上げる (古い絵のファイルを使い続けないように)


def file_name(name):
    return "dfx_%s_v%d.png" % (name, VERSION)


def make(name, path):
    GENERATORS[name](path)
    return path


def image_size(path):
    """画像の (幅, 高さ)。PNG はファイルの頭から読む。ほかは Qt があれば Qt で。分からなければ None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == PNG_SIGNATURE:
            return struct.unpack(">II", head[16:24])
    except (IOError, OSError):
        return None
    try:
        from .ui.qt import QtGui
        size = QtGui.QImageReader(path).size()
        if size.width() > 0 and size.height() > 0:
            return size.width(), size.height()
    except Exception:
        pass
    return None
