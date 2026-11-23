# -*- coding: utf-8 -*-
"""粒の動きの計算 (Maya 版のエクスプレッション (MEL) と同じ式の Python 版)。

Maya にも Blender にも依存しない。Blender 版はこれを使い、Maya 版と同じ動きにする
(Maya 版との一致は mayapy の検証で確かめる)。

空間は Maya と同じ「Y が上」。行列は Maya と同じ行ベクトルの 16 個 (各行がローカル軸、4 行目が位置)。
Z が上のソフト (Blender) は yup_matrix / yup_vector で直してから使い、結果を zup_* で戻す。
"""
from __future__ import absolute_import

import math

from .tools._weather import hash01, table_value
from .tools.weather import spark_motion

TAU = 6.2831853

# ---------------------------------------------------------------- ベクトル
def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def mag(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = mag(a)
    return mul(a, 1.0 / n) if n > 0 else (0.0, 0.0, 0.0)


def xf(m, p):
    """点 × 行列 (MEL の mel_xf)。"""
    return (p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12],
            p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13],
            p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14])


def xv(m, v):
    """向き × 行列 (移動なし。MEL の mel_xv)。"""
    return (v[0] * m[0] + v[1] * m[4] + v[2] * m[8],
            v[0] * m[1] + v[1] * m[5] + v[2] * m[9],
            v[0] * m[2] + v[1] * m[6] + v[2] * m[10])


def ortho(primary, cand):
    """cand から primary の成分を除いて正規化 (MEL の mel_ortho と同じ。平行なら決まった向き)。"""
    d = sub(cand, mul(primary, dot(cand, primary)))
    if mag(d) < 1e-6:
        d = cross(primary, (0.0, 0.0, 1.0))
    if mag(d) < 1e-6:
        d = cross(primary, (1.0, 0.0, 0.0))
    return unit(d)


def clamp(lo, hi, x):
    return min(hi, max(lo, x))


def h(i, c, k, seed):
    return hash01(i, c, k, seed)


# ---------------------------------------------------------------- Z が上 ↔ Y が上
# Y が上の空間の点 = (x, z, -y)  (Z が上の (x, y, z) から)
_B = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))


def yup_vector(v):
    return (v[0], v[2], -v[1])


def zup_vector(v):
    return (v[0], -v[2], v[1])


def yup_matrix(m):
    """Z が上の行列 → Y が上の行列 (ローカル軸の意味も付け替える: ローカル Z (上) → ローカル Y)。"""
    rows = [yup_vector((m[4 * r], m[4 * r + 1], m[4 * r + 2])) for r in range(3)]
    # ローカル軸の付け替え: 新しいローカル X, Y, Z = 元のローカル X, Z, -Y
    rx, ry, rz = rows[0], rows[2], mul(rows[1], -1.0)
    t = yup_vector((m[12], m[13], m[14]))
    return [rx[0], rx[1], rx[2], 0.0, ry[0], ry[1], ry[2], 0.0,
            rz[0], rz[1], rz[2], 0.0, t[0], t[1], t[2], 1.0]


def inverse(m):
    """回転・拡大縮小・移動の行列 (せん断なし) の逆行列。"""
    rows = [(m[0], m[1], m[2]), (m[4], m[5], m[6]), (m[8], m[9], m[10])]
    sq = [dot(r, r) or 1e-18 for r in rows]
    # 逆の 3x3: 列 i = 行 i / |行 i|^2
    inv3 = [[rows[c][r] / sq[c] for c in range(3)] for r in range(3)]
    t = (m[12], m[13], m[14])
    it = tuple(-sum(t[k] * inv3[k][j] for k in range(3)) for j in range(3))
    return [inv3[0][0], inv3[0][1], inv3[0][2], 0.0, inv3[1][0], inv3[1][1], inv3[1][2], 0.0,
            inv3[2][0], inv3[2][1], inv3[2][2], 0.0, it[0], it[1], it[2], 1.0]


# ---------------------------------------------------------------- 板の向き
def billboard(p, cp, cdir, roll=None):
    """カメラを向く板の軸 (X, Y, Z)。mel_billboard と同じ。"""
    z = sub(cp, p)
    if mag(z) < 1e-6:
        z = mul(cdir, -1.0)
    z = unit(z)
    y = ortho(z, (0.0, 1.0, 0.0))
    x = cross(y, z)
    if roll is not None:
        ra = math.radians(roll)
        x2 = add(mul(x, math.cos(ra)), mul(y, math.sin(ra)))
        y = sub(mul(y, math.cos(ra)), mul(x, math.sin(ra)))
        x = x2
    return x, y, z


def streak(p, vel, cp, cdir):
    """進む向き (vel) に伸びてカメラを向く板の軸。mel_streak と同じ。"""
    y = vel if mag(vel) >= 1e-9 else (0.0, -1.0, 0.0)
    y = unit(y)
    tc = sub(cp, p)
    if mag(tc) < 1e-6:
        tc = mul(cdir, -1.0)
    z = ortho(y, tc)
    return cross(y, z), y, z


class Wind(object):
    """風の表 (流された距離と、その時の風)。無ければ全部 0。"""

    def __init__(self, drift=None, velocity=None, start=0.0, influence=1.0):
        self.drift = drift or []
        self.velocity = velocity or []
        self.start = start
        self.influence = influence

    def drift_at(self, t):
        n = min(len(self.drift), len(self.velocity))
        return table_value(self.drift[:n], self.start, t) if n else (0.0, 0.0, 0.0)

    def velocity_at(self, t):
        n = min(len(self.drift), len(self.velocity))
        return table_value(self.velocity[:n], self.start, t) if n else (0.0, 0.0, 0.0)


# ---------------------------------------------------------------- 雨・雪
def falling(i, frame, v, am, ai, wind, sway=False, seed=None):
    """降り続ける粒 (雨・雪)。_falling_body と同じ式。辞書を返す。"""
    sd = v["seed"] if seed is None else seed
    a_h = mag((am[4], am[5], am[6]))
    sp = v["speed"] * (1 + (h(i, 0, 1, sd) - 0.5) * 2 * v["speed_random"])
    sp = max(sp, 0.0001)
    life = a_h / sp
    u = frame / life + h(i, 0, 2, sd)
    c = math.floor(u)
    age = (u - c) * life
    ts = frame - age
    q = (h(i, c, 3, sd) - 0.5, 0.5, h(i, c, 4, sd) - 0.5)
    ds = wind.drift_at(ts)
    dnow = wind.drift_at(frame)
    dw = add(mul(sub(dnow, ds), wind.influence), (0.0, -sp * age, 0.0))
    if sway:
        sw = v["sway"]
        per = max(v["sway_period"], 1.0)
        dw = add(dw, (sw * math.sin(TAU * age / per + TAU * h(i, 0, 5, sd)), 0.0,
                      sw * math.cos(TAU * age / (per * 1.3) + TAU * h(i, 0, 6, sd))))
    q = add(q, xv(ai, dw))
    q = (math.fmod(q[0] + 1000.5, 1.0) - 0.5, q[1], math.fmod(q[2] + 1000.5, 1.0) - 0.5)
    p = xf(am, q)
    vel = add((0.0, -sp, 0.0), mul(wind.velocity_at(frame), wind.influence))
    vis = h(i, c, 7, sd) < v["amount"]
    return {"p": p, "vel": vel, "vis": vis, "age": age, "life": life, "c": c, "ts": ts,
            "ds": ds, "sp": sp}


def splash(i, frame, v, am, ai, wind):
    """雨のはね (1 つ前の回に地面に落ちた所)。splash_body と同じ式。(位置, 大きさ, 表示)。"""
    sd = v["seed"]
    f = falling(i, frame, v, am, ai, wind)
    c, life, ts = f["c"], f["life"], f["ts"]
    tp = ts - life
    q0 = (h(i, c - 1, 3, sd) - 0.5, 0.5, h(i, c - 1, 4, sd) - 0.5)
    dp = wind.drift_at(tp)
    dh = add(mul(sub(f["ds"], dp), wind.influence), (0.0, -f["sp"] * life, 0.0))
    qh = add(q0, xv(ai, dh))
    qh = (math.fmod(qh[0] + 1000.5, 1.0) - 0.5, -0.5, math.fmod(qh[2] + 1000.5, 1.0) - 0.5)
    k = f["age"] / max(v["splash_frames"], 0.001)
    size = v["splash_size"] * (0.35 + 0.65 * k)
    vis = h(i, c - 1, 7, sd) < v["amount"] and k < 1 and v["splash_size"] > 0
    return xf(am, qh), size, vis


def snow_size(i, v):
    return v["size"] * (1 + (h(i, 0, 8, v["seed"]) - 0.5) * 2 * v["size_random"])


# ---------------------------------------------------------------- 出る場所から出るもの
def spawn(i, frame, v, count, emitter_at, wind):
    """出る場所から出る粒 (土煙・火花) の出た時・場所・向き。_spawn_body と同じ式。"""
    sd = v["seed"]
    life = v["life"] * (1 + (h(i, 0, 1, sd) - 0.5) * 2 * v["life_random"])
    life = max(life, 1.0)
    den = float(max(count - 1, 1))
    ts = v["start_frame"] + v["duration"] * i / den
    if v["duration"] <= 0:
        ts = v["start_frame"] + h(i, 0, 2, sd) * 2.0
    age = frame - ts
    vis = 0 <= age <= life
    a = max(age, 0.0)
    em = emitter_at(ts)
    e = (em[12], em[13], em[14])
    up = unit((em[4], em[5], em[6]))
    aa = ortho(up, (1.0, 0.0, 0.0))
    bb = cross(up, aa)
    th = math.radians(v["spread"] * h(i, 0, 3, sd))
    ph = TAU * h(i, 0, 4, sd)
    direction = add(mul(up, math.cos(th)), mul(add(mul(aa, math.cos(ph)), mul(bb, math.sin(ph))), math.sin(th)))
    spd = v["speed"] * (1 + (h(i, 0, 5, sd) - 0.5) * 2 * v["speed_random"])
    wd = mul(sub(wind.drift_at(frame), wind.drift_at(ts)), wind.influence)
    return {"life": life, "ts": ts, "age": age, "vis": vis, "a": a, "e": e, "up": up,
            "dir": direction, "spd": spd, "wd": wd}


def dust(i, frame, v, count, emitter_at, wind):
    """土煙の塊 (位置, 大きさ, 表示, 回転の元の乱数)。_dust_motion と同じ式。"""
    s = spawn(i, frame, v, count, emitter_at, wind)
    a, life = s["a"], s["life"]
    kd = max(v["drag"], 0.0001)
    grow = 1 - math.exp(-3.0 * a / life)
    sz = (v["size_start"] + (v["size_end"] - v["size_start"]) * grow) \
        * (1 + (h(i, 0, 6, v["seed"]) - 0.5) * 2 * v["size_random"])
    if a > 0.7 * life:
        sz = sz * max(0.0, (life - a) / (0.3 * life))
    p = add(add(add(s["e"], mul(s["dir"], s["spd"] * (1 - math.exp(-kd * a)) / kd)),
                mul(s["up"], v["rise"] * a + sz * 0.35)), s["wd"])
    return p, sz, s["vis"], a


def dust_roll(i, v, a):
    return h(i, 0, 7, v["seed"]) * 360 + v["spin"] * a


def dust_euler(i, v, a):
    sd = v["seed"]
    return (h(i, 0, 7, sd) * 360 + v["spin"] * a, h(i, 0, 8, sd) * 360,
            h(i, 0, 9, sd) * 360 + v["spin"] * 0.5 * a)


def spark(i, frame, v, count, emitter_at, wind, age=None):
    """火花の (位置, 速さ, 表示, 消え方の割合, 寿命)。spawn + spark_motion。"""
    s = spawn(i, frame, v, count, emitter_at, wind)
    a = s["a"] if age is None else age
    p, vel = spark_motion(s["e"], mul(s["dir"], s["spd"]), v["gravity"], v["ground"],
                          v["bounce"], a)
    fade = 1.0
    if s["a"] > 0.7 * s["life"]:
        fade = max(0.0, (s["life"] - s["a"]) / (0.3 * s["life"]))
    return add(p, s["wd"]), vel, s["vis"], fade, s


def spark_trail_rows(i, frame, v, count, emitter_at, wind, cp, cdir):
    """火花の尾の行 [(位置, (X, Y, Z), 太さ)]。build_spark_trail と同じ式。"""
    p0, _, vis, fade, s = spark(i, frame, v, count, emitter_at, wind)
    if not vis:
        fade = 0.0
    rows = int(v["segments"]) + 1
    pts = []
    for j in range(rows):
        aj = max(0.0, s["a"] - v["tail_frames"] * j / float(rows - 1))
        p, _vel = spark_motion(s["e"], mul(s["dir"], s["spd"]), v["gravity"], v["ground"],
                               v["bounce"], aj)
        pts.append(add(p, s["wd"]))
    out = []
    for j in range(rows):
        j0, j1 = max(0, j - 1), min(rows - 1, j + 1)
        p = pts[j]
        t = sub(pts[j0], pts[j1])
        x = unit(t) if mag(t) > 1e-9 else (0.0, -1.0, 0.0)
        tc = sub(cp, p)
        if mag(tc) < 1e-6:
            tc = mul(cdir, -1.0)
        z = ortho(x, tc)
        y = cross(z, x)
        w = v["width"] * fade * (1 - j / float(rows))
        out.append((p, (x, y, z), w))
    return out


# ---------------------------------------------------------------- 霧の板 (霧・這う霧)
def fog_card(i, frame, v, am, ai, wind, cp):
    """霧の板 (位置, 大きさ (x, y), 濃さ, 回転)。build_cards と同じ式。"""
    sd = v["seed"]
    q = (h(i, 0, 1, sd) - 0.5, h(i, 0, 2, sd) - 0.5, h(i, 0, 3, sd) - 0.5)
    q = add(q, xv(ai, mul(wind.drift_at(frame), wind.influence)))
    q = (math.fmod(q[0] + 1000.5, 1.0) - 0.5, clamp(-0.5, 0.5, q[1]), math.fmod(q[2] + 1000.5, 1.0) - 0.5)
    p = xf(am, q)
    sz = v["size"] * (1 + (h(i, 0, 4, sd) - 0.5) * 2 * v["size_random"])
    ed = max(v["edge_fade"], 0.0001)
    fe = clamp(0, 1, (0.5 - abs(q[0])) / ed) * clamp(0, 1, (0.5 - abs(q[2])) / ed)
    nf = max(v["near_fade"], 0.0001)
    fn = clamp(0, 1, (mag(sub(p, cp)) - nf * 0.5) / (nf * 0.5))
    alpha = v["opacity"] * fe * fn
    roll = (h(i, 0, 5, sd) - 0.5) * 360 * min(1.0, v["squash"]) ** 4 + v["spin"] * frame
    return p, (sz, sz * v["squash"]), alpha, roll


# ---------------------------------------------------------------- 爆発
def _cone(n, spread, h1, h2):
    aa = ortho(n, (0.0, 1.0, 0.0))
    bb = cross(n, aa)
    th = math.radians(spread * h1)
    ph = TAU * h2
    return add(mul(n, math.cos(th)), mul(add(mul(aa, math.cos(ph)), mul(bb, math.sin(ph))), math.sin(th)))


def explosion_flash(e, frame, hit, v):
    life = max(v["flash_frames"], 0.01)
    age = frame - hit["frame"]
    s0 = v["size"]
    vis = 0 <= age <= life and v["flash_size"] > 0
    sz = s0 * v["flash_size"] * math.sin(3.1415927 * clamp(0, 1, age / life))
    return add(hit["pos"], mul(hit["normal"], s0 * 0.3)), sz, vis, h(e, 0, 1, v["seed"]) * 360


def explosion_ring(e, frame, hit, v):
    life = max(v["ring_frames"], 0.01)
    age = frame - hit["frame"]
    k = clamp(0, 1, age / life)
    vis = 0 <= age <= life and v["ring_size"] > 0
    sz = 2 * v["size"] * v["ring_size"] * (1 - (1 - k) ** 3)
    z = hit["normal"]
    x = ortho(z, (0.0, 1.0, 0.0))
    return add(hit["pos"], mul(z, v["size"] * 0.03)), (x, cross(z, x), z), sz, vis


def explosion_fire(e, j, frame, hit, v):
    sd, s0, age = v["seed"], v["size"], frame - hit["frame"]
    life = v["fire_frames"] * (0.7 + 0.6 * h(e, j, 1, sd))
    d = _cone(hit["normal"], 75, h(e, j, 2, sd), h(e, j, 3, sd))
    a = max(age, 0.0)
    dist = s0 * v["fire_spread"] * (1 - math.exp(-5 * a / life))
    p = add(add(hit["pos"], mul(hit["normal"], s0 * 0.25)), mul(d, dist))
    sz = s0 * v["fire_size"] * (0.5 + 0.5 * h(e, j, 4, sd)) * (1 - math.exp(-8 * a / life))
    if age > 0.6 * life:
        sz = sz * max(0.0, (life - age) / (0.4 * life))
    rot = (h(e, j, 5, sd) * 360 + 4 * age, h(e, j, 6, sd) * 360, h(e, j, 7, sd) * 360 + 2 * age)
    return p, sz, 0 <= age <= life, rot


def explosion_smoke(e, j, frame, hit, v):
    sd, s0, age = v["seed"], v["size"], frame - hit["frame"]
    life = v["smoke_frames"] * (0.7 + 0.6 * h(e, j, 11, sd))
    a_s = age - (1.5 + 2.0 * h(e, j, 12, sd))
    d = _cone(hit["normal"], 85, h(e, j, 13, sd), h(e, j, 14, sd))
    a = max(a_s, 0.0)
    dist = s0 * v["smoke_spread"] * (1 - math.exp(-3 * a / life))
    p = add(add(add(hit["pos"], mul(hit["normal"], s0 * 0.4)), mul(d, dist)),
            (0.0, v["smoke_rise"] * s0 * a, 0.0))
    sz = s0 * v["smoke_size"] * (0.4 + 0.6 * h(e, j, 15, sd)) * (0.3 + 0.7 * (1 - math.exp(-4 * a / life)))
    if a_s > 0.7 * life:
        sz = sz * max(0.0, (life - a_s) / (0.3 * life))
    rot = (h(e, j, 16, sd) * 360 + 4 * a_s, h(e, j, 17, sd) * 360, h(e, j, 18, sd) * 360 + 2 * a_s)
    return p, sz, 0 <= a_s <= life, rot


def explosion_spark(e, j, frame, hit, v):
    sd, s0, age = v["seed"], v["size"], frame - hit["frame"]
    life = v["spark_frames"] * (0.6 + 0.8 * h(e, j, 21, sd))
    d = _cone(hit["normal"], 80, h(e, j, 22, sd), h(e, j, 23, sd))
    ag = max(age, 0.0)
    g = v["gravity"] * s0
    v0 = mul(d, v["spark_speed"] * s0 * (0.5 + h(e, j, 24, sd)))
    p = add(add(add(hit["pos"], mul(hit["normal"], s0 * 0.05)), mul(v0, ag)), (0.0, -0.5 * g * ag * ag, 0.0))
    vel = add(v0, (0.0, -g * ag, 0.0))
    fade = 1.0
    if ag > 0.7 * life:
        fade = max(0.0, (life - ag) / (0.3 * life))
    w = 0.04 * s0 * fade
    return p, vel, (w, max(mag(vel) * 1.5, w), 1.0), 0 <= age <= life
