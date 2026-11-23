# -*- coding: utf-8 -*-
"""ショットの受け渡し形式 (Maya / Blender → Unreal Engine)。

Maya にも Blender にも Unreal にも依存しない (ただの Python と JSON)。
形式の詳細は docs/exchange_format.md。

共通の空間 (この形式の中の座標):
    右手系・Z が上・単位はメートル (Blender と同じ)
    Maya (Y が上・センチ) からは書き出し時に、Unreal (左手系・Z が上・センチ) へは読み込み時に変換する。

行列は Maya と同じ「行ベクトル」の並び (16 個。3 行目まで各行が軸、4 行目が位置)。
"""
from __future__ import absolute_import

import io
import json
import math

FORMAT_NAME = "DummyEffectMakerShot"
FORMAT_VERSION = 1
SPACE = {"up": "Z", "handedness": "right", "unit": "meter"}

# 長さの単位 → メートル
UNIT_TO_METER = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
                 "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344}

# 軸の付け替え (行ベクトル p_共通 = p_元 · B の B の行)
_BASIS = {
    "y": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),   # Y が上 (Maya の既定)
    "z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),    # Z が上 (Blender・Maya の Z アップ)
}


class ExchangeError(Exception):
    """読み込めないファイル (ユーザー向けの説明付き)。"""


# ---------------------------------------------------------------- 座標の変換
def _mul3(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _transpose3(a):
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


def convert_point(p, up="y", scale=1.0):
    """元の空間の点 → 共通の空間 (Z が上・メートル)。scale は元の単位 → メートル。"""
    b = _BASIS[up]
    return tuple(sum(p[k] * b[k][j] for k in range(3)) * scale for j in range(3))


def convert_vector(v, up="y"):
    """向き (法線など。長さは変えない)。"""
    return convert_point(v, up, 1.0)


def convert_matrix(m, up="y", scale=1.0):
    """ワールド行列 (16 個、行ベクトル) → 共通の空間の行列 (16 個)。

    ワールドの軸だけを付け替える (各行 = オブジェクトのローカル軸を、共通の空間の向きで表す)。
    オブジェクトのローカル軸の意味は元のまま (カメラは -Z が前・+Y が上。Maya も Blender も同じ)。
    位置は p · B × scale、軸の長さ (拡大縮小) は変えない。
    """
    b = _BASIS[up]
    rot = ((m[0], m[1], m[2]), (m[4], m[5], m[6]), (m[8], m[9], m[10]))
    r = _mul3(rot, b)
    t = convert_point((m[12], m[13], m[14]), up, scale)
    return [r[0][0], r[0][1], r[0][2], 0.0, r[1][0], r[1][1], r[1][2], 0.0,
            r[2][0], r[2][1], r[2][2], 0.0, t[0], t[1], t[2], 1.0]


def decompose(m):
    """行列 (16 個、行ベクトル) → (位置, 回転の四元数 (x, y, z, w), 拡大縮小)。せん断は無視。"""
    rows = [list(m[0:3]), list(m[4:7]), list(m[8:11])]
    scale = [math.sqrt(sum(x * x for x in r)) for r in rows]
    for i in range(3):
        if scale[i] > 1e-12:
            rows[i] = [x / scale[i] for x in rows[i]]
    det = (rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
           - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
           + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0]))
    if det < 0:  # 裏返し (負の拡大縮小) は Z の拡大縮小を負にする
        scale[2] = -scale[2]
        rows[2] = [-x for x in rows[2]]
    return (tuple(m[12:15]), quaternion_from_rows(rows), tuple(scale))


def quaternion_from_rows(rows):
    """回転行列 (行ベクトル: 各行が軸) → 四元数 (x, y, z, w)。"""
    # 列ベクトルの行列 R = rows の転置
    r = [[rows[j][i] for j in range(3)] for i in range(3)]
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s)
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = (0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s)
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = ((r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s)
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = ((r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s)
    n = math.sqrt(sum(x * x for x in q))
    q = tuple(x / n for x in q)
    return q if q[3] >= 0 else tuple(-x for x in q)


def rows_from_quaternion(q):
    """四元数 (x, y, z, w) → 回転行列の行 (quaternion_from_rows の逆。検証・読み込み用)。"""
    x, y, z, w = q
    col = ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
           (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
           (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))
    return _transpose3(col)


def to_unreal(position, quaternion=None):
    """共通の空間 → Unreal (左手系・Z が上・センチ)。Y を反転して 100 倍。(位置, 四元数)。

    四元数は Y の反転に合わせて (-x, y, -z, w) になる (Unreal の FQuat は (X, Y, Z, W))。
    """
    p = (position[0] * 100.0, -position[1] * 100.0, position[2] * 100.0)
    if quaternion is None:
        return p, None
    x, y, z, w = quaternion
    return p, (-x, y, -z, w)


# ---------------------------------------------------------------- トラック (毎フレームの値)
def make_track(frames, samples, eps=1e-6):
    """毎フレームの値 → トラック。全部同じなら {"static": True, 値} にまとめる。

    samples: [{"t": (x, y, z), "q": (x, y, z, w), "s": (x, y, z), ...}] (frames と同じ数)
    """
    if not samples:
        return {"static": True}
    keys = sorted(samples[0])
    first = samples[0]
    if all(_same(s[k], first[k], eps) for s in samples for k in keys):
        out = {"static": True}
        out.update((k, _round(first[k])) for k in keys)
        return out
    out = {"static": False, "frames": [_round(f) for f in frames]}
    for k in keys:
        out[k] = [_round(s[k]) for s in samples]
    return out


def track_value(track, key, frame):
    """トラックの frame の値 (static ならその値。間は直線補間。四元数は補間しない)。"""
    if track.get("static"):
        return track[key]
    frames = track["frames"]
    values = track[key]
    if frame <= frames[0]:
        return values[0]
    if frame >= frames[-1]:
        return values[-1]
    for i in range(len(frames) - 1):
        if frames[i] <= frame <= frames[i + 1]:
            a, b = values[i], values[i + 1]
            if key == "q" or not isinstance(a, (list, tuple)):
                return a if frame - frames[i] < frames[i + 1] - frame else b
            f = (frame - frames[i]) / float(frames[i + 1] - frames[i])
            return [x + (y - x) * f for x, y in zip(a, b)]
    return values[-1]


def _same(a, b, eps):
    if isinstance(a, (list, tuple)):
        return all(abs(x - y) <= eps for x, y in zip(a, b))
    if isinstance(a, float):
        return abs(a - b) <= eps
    return a == b


def _round(v, digits=6):
    if isinstance(v, (list, tuple)):
        return [_round(x, digits) for x in v]
    if isinstance(v, float):
        return round(v, digits)
    return v


def param_values(params, values):
    """パラメータの値を JSON にできる形に (色はリスト)。"""
    return dict((p.name, list(values[p.name]) if isinstance(values[p.name], tuple)
                 else values[p.name]) for p in params)


# ---------------------------------------------------------------- ファイル
def new_shot(source, fps, start, end):
    return {"format": FORMAT_NAME, "version": FORMAT_VERSION, "source": source,
            "space": dict(SPACE), "time": {"fps": fps, "start": start, "end": end},
            "cameras": [], "effects": []}


def write_shot(path, shot):
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(shot, ensure_ascii=False, indent=1, sort_keys=True))
    except (IOError, OSError) as e:
        raise ExchangeError(u"書き出せません: %s" % e)


def read_shot(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError) as e:
        raise ExchangeError(u"読み込めません: %s" % e)
    if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
        raise ExchangeError(u"Dummy Effect Maker のショットのファイルではありません")
    if data.get("version", 0) > FORMAT_VERSION:
        raise ExchangeError(u"新しい形式のファイルです (形式 %s。このツールは %d まで)"
                            % (data.get("version"), FORMAT_VERSION))
    return data
