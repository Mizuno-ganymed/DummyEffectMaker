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


# ---------------------------------------------------------------- 核爆発 (単体の大きな爆発)
# 中心 (爆心) の位置 p0 と、中心の向き axes = (X, 上, Z) (単位ベクトル) から決める。
# 部品の位置は「中心の空間」(x: X 軸、y: 上、z: Z 軸) で計算してから p0 + 軸 で置く。
def nuke_state(frame, v):
    """全体の時刻の値 (経過 a・大きさ S・上がり具合 rise・かさの高さ H・薄れ fade・冷え cool)。"""
    a = (frame - v["start_frame"]) * max(v["speed"], 0.001)
    s0 = v["size"]
    tau = max(v["rise_frames"], 1.0) / 3.0
    rise = 1 - math.exp(-max(a, 0.0) / tau)
    life = max(v["life_frames"], 1.0)
    return {"a": a, "S": s0, "tau": tau, "rise": rise, "H": s0 * v["cloud_height"] * rise,
            "life": life, "vis": 0 <= a <= life,
            "fade": clamp(0, 1, (life - a) / (0.25 * life)),
            "cool": clamp(0, 1, a / max(v["cool_frames"], 1.0))}


def nuke_place(p0, axes, local):
    x, u, z = axes
    return add(p0, add(mul(x, local[0]), add(mul(u, local[1]), mul(z, local[2]))))


def _nuke_rot(j, k, a, spin, sd):
    return (h(j, 0, k, sd) * 360 + spin * a, h(j, 0, k + 1, sd) * 360, h(j, 0, k + 2, sd) * 360 + spin * 0.5 * a)


def nuke_flash(frame, v, p0, axes):
    """閃光 (位置, 大きさ, 表示)。向きはカメラを向く板 (billboard)。"""
    st = nuke_state(frame, v)
    a, s0 = st["a"], st["S"]
    life = max(v["flash_frames"], 0.01)
    sz = s0 * v["flash_size"] * math.sin(3.1415927 * clamp(0, 1, a / life))
    return nuke_place(p0, axes, (0.0, s0 * 0.5, 0.0)), sz, 0 <= a <= life and sz > 0


def nuke_shock(frame, v, p0, axes):
    """地面を走る衝撃波の輪 (位置, 軸, 大きさ, 表示, 濃さ)。輪は中心の X-Z 面。"""
    st = nuke_state(frame, v)
    a, s0 = st["a"], st["S"]
    k = clamp(0, 1, a / max(v["shock_frames"], 0.01))
    sz = s0 * v["shock_size"] * (1 - (1 - k) ** 3)
    return (nuke_place(p0, axes, (0.0, s0 * 0.02, 0.0)), axes, sz, 0 <= a < v["shock_frames"] and sz > 0,
            v["opacity"] * (1 - k))


def nuke_dome(frame, v, p0, axes):
    """衝撃波のドーム (半球。位置, 軸, 大きさ, 表示, 濃さ)。"""
    st = nuke_state(frame, v)
    a, s0 = st["a"], st["S"]
    k = clamp(0, 1, a / max(v["shock_frames"] * 0.5, 0.01))
    sz = s0 * v["shock_size"] * 0.6 * (1 - (1 - k) ** 2)
    vis = 0 <= a < v["shock_frames"] * 0.5 and sz > 0 and v["dome_opacity"] > 0
    return p0, axes, sz, vis, v["dome_opacity"] * (1 - k) ** 2


def nuke_fireball(frame, v, p0, axes):
    """火の玉 (位置, 大きさ, 表示, 回転)。かさと一緒に上がり、だんだん小さくなって消える。"""
    st = nuke_state(frame, v)
    a, s0 = st["a"], st["S"]
    fb = max(v["fireball_frames"], 1.0)
    ag = max(a, 0.0)
    sz = s0 * v["fireball_size"] * (1 - math.exp(-ag / 3.0))
    if a > 0.6 * fb:
        sz = sz * max(0.0, (fb - a) / (0.4 * fb))
    p = nuke_place(p0, axes, (0.0, st["H"] + sz * 0.5, 0.0))
    return p, sz, 0 <= a <= fb and sz > 0, _nuke_rot(0, 1, ag, v["spin"], v["seed"])


def nuke_stem(j, count, frame, v, p0, axes):
    """キノコ雲の柱の煙 j (位置, 大きさ, 表示, 回転)。根元ほど太い。"""
    st = nuke_state(frame, v)
    a, s0, sd = st["a"], st["S"], v["seed"]
    ag = max(a, 0.0)
    s = (j + 0.5) / float(max(count, 1))
    th = TAU * h(j, 0, 11, sd) + 0.03 * ag
    rr = s0 * v["stem_width"] * 0.35 * h(j, 0, 12, sd)
    grow = 1 - math.exp(-ag / (st["tau"] * 0.5))
    sz = s0 * v["stem_width"] * (0.55 + 0.45 * h(j, 0, 13, sd)) * (0.5 + 0.5 * (1 - s)) * grow
    p = nuke_place(p0, axes, (math.cos(th) * rr, st["H"] * s * 0.95, math.sin(th) * rr))
    return p, sz, st["vis"] and sz > 0, _nuke_rot(j, 14, ag, v["spin"], sd)


def nuke_cap(j, count, frame, v, p0, axes):
    """キノコ雲のかさの煙 j (位置, 大きさ, 表示, 回転)。外側から下へ巻き込みながら広がる。"""
    st = nuke_state(frame, v)
    a, s0, sd = st["a"], st["S"], v["seed"]
    ag = max(a, 0.0)
    n = float(max(count, 1))
    th = TAU * (j + (h(j, 0, 21, sd) - 0.5)) / n
    ph = TAU * h(j, 0, 22, sd) - math.radians(v["roll"]) * ag
    big = s0 * v["cap_width"] * (0.3 + 0.7 * st["rise"])
    tube = big * 0.5
    out = big + tube * math.cos(ph)
    p = nuke_place(p0, axes, (math.cos(th) * out, st["H"] + tube * 0.7 * math.sin(ph), math.sin(th) * out))
    sz = s0 * v["cap_width"] * 0.45 * (0.7 + 0.5 * h(j, 0, 23, sd)) * (0.35 + 0.65 * st["rise"])
    return p, sz, st["vis"] and sz > 0, _nuke_rot(j, 24, ag, v["spin"], sd)


def nuke_base(j, count, frame, v, p0, axes):
    """根元に広がる土煙 j (位置, 大きさ, 表示, 回転)。"""
    st = nuke_state(frame, v)
    a, s0, sd = st["a"], st["S"], v["seed"]
    ag = max(a, 0.0)
    n = float(max(count, 1))
    th = TAU * (j + (h(j, 0, 31, sd) - 0.5)) / n
    dist = s0 * v["base_spread"] * (1 - math.exp(-ag / (st["tau"] * 0.7))) * (0.75 + 0.5 * h(j, 0, 32, sd))
    sz = s0 * v["base_size"] * (0.5 + 0.5 * h(j, 0, 33, sd)) * (0.3 + 0.7 * (1 - math.exp(-ag / (st["tau"] * 0.5))))
    p = nuke_place(p0, axes, (math.cos(th) * dist, sz * 0.35, math.sin(th) * dist))
    return p, sz, st["vis"] and sz > 0, _nuke_rot(j, 34, ag, v["spin"], sd)


def nuke_colors(frame, v):
    """雲の (色, 影の色, 濃さ)。火の色から煙の色へ冷え、最後に薄れる。"""
    st = nuke_state(frame, v)
    k = st["cool"]
    color = tuple(f + (c - f) * k for f, c in zip(v["fire_color"], v["cloud_color"]))
    shade = tuple(f + (c - f) * k for f, c in zip(v["fire_shade"], v["cloud_shade"]))
    return color, shade, v["opacity"] * st["fade"]


# ---------------------------------------------------------------- 稲妻
# 雲 (top) から地面 (ground) へ落ちる帯。回 c ごとに形・落ちる所が変わる。
# 形は「本筋 (点 N 個)」と「枝 B 本 (各 M 個)」の点の列。点ごとに (位置, 軸, 太さ) の行を返す
# (火花の尾と同じ: 軸 X が進む向き、Z がカメラの方。帯は Y の向きに太さだけ広がる)。
STYLE_REAL = "real"     # 細い実写的: 細かいギザギザ (値ノイズを重ねる)
STYLE_ANIME = "anime"   # 太いアニメ的: 大きなジグザグ (点ごとに左右へ交互)
REAL_OCTAVES = 4


def lightning_strike(frame, v):
    """今光っている回 (c, 始まり) 。光っていなければ None。"""
    sd = v["seed"]
    t = v["start_frame"]
    for c in range(int(v["count"])):
        if c > 0:
            t += max(1.0, v["interval"] * (1 + (h(c, 0, 51, sd) - 0.5) * 2 * v["interval_random"]))
        if t > frame:
            return None
        if frame < t + v["duration"]:
            return c, t
    return None


def lightning_ends(c, v, top, ground):
    """回 c の (雲の点, 地面の点)。水平 (X / Z) にばらつく。"""
    sd, sp = v["seed"], v["spread"]
    t = add(top, ((h(c, 0, 52, sd) - 0.5) * sp, 0.0, (h(c, 0, 53, sd) - 0.5) * sp))
    g = add(ground, ((h(c, 0, 54, sd) - 0.5) * 2 * sp, 0.0, (h(c, 0, 55, sd) - 0.5) * 2 * sp))
    return t, g


def _vnoise(x, c, k, sd):
    """-1〜1 の値ノイズ (整数の所の乱数を直線でつなぐ)。"""
    i = math.floor(x)
    a = h(i, c, k, sd)
    b = h(i + 1, c, k, sd)
    return (a + (b - a) * (x - i)) * 2 - 1


def lightning_offset(style, k, n, u, c, sd):
    """点 k (全部で n 個、割合 u) の横のずれ (A, B の向き。長さ 1 あたり)。"""
    if style == STYLE_ANIME:
        if k == 0 or k == n - 1:
            return 0.0, 0.0
        s = 1.0 if k % 2 else -1.0
        return s * (0.4 + 0.6 * h(k, c, 70, sd)), (h(k, c, 71, sd) - 0.5) * 0.3
    env = min(1.0, 6 * u, 6 * (1 - u)) if k < n - 1 else 0.0
    oa = sum((0.5 ** o) * _vnoise(u * 4 * 2 ** o, c, 60 + o * 2, sd) for o in range(REAL_OCTAVES))
    ob = sum((0.5 ** o) * _vnoise(u * 4 * 2 ** o, c, 61 + o * 2, sd) for o in range(REAL_OCTAVES))
    return oa * env, ob * env


def lightning_branch_offset(style, m, n, u, c, sd):
    """枝の点のずれ (根元は 0、先は動いてよい)。"""
    if style == STYLE_ANIME:
        if m == 0:
            return 0.0, 0.0
        s = 1.0 if m % 2 else -1.0
        return s * (0.4 + 0.6 * h(m, c, 70, sd)), (h(m, c, 71, sd) - 0.5) * 0.3
    env = min(1.0, 6 * u)
    oa = sum((0.5 ** o) * _vnoise(u * 4 * 2 ** o, c, 60 + o * 2, sd) for o in range(REAL_OCTAVES))
    ob = sum((0.5 ** o) * _vnoise(u * 4 * 2 ** o, c, 61 + o * 2, sd) for o in range(REAL_OCTAVES))
    return oa * env, ob * env


def lightning_frame(top, ground):
    """本筋の (長さ, 向き, 横 A, 横 B)。"""
    d = sub(ground, top)
    length = mag(d)
    d = unit(d) if length > 1e-9 else (0.0, -1.0, 0.0)
    a = ortho(d, (1.0, 0.0, 0.0))
    return length, d, a, cross(d, a)


def jag_scale(v, u):
    """ギザギザの大きさの倍率 (雲・発生の側 u = 0 で 1 → 終端 u = 1 で「終端のギザギザ」)。"""
    return 1 - (1 - v.get("jag_taper", 1.0)) * u


def lightning_main_point(style, k, n, c, v, top, ground):
    length, d, a, b = lightning_frame(top, ground)
    u = k / float(max(n - 1, 1))
    oa, ob = lightning_offset(style, k, n, u, c, v["seed"])
    jag = length * v["jag"] * jag_scale(v, u)
    return add(add(top, mul(sub(ground, top), u)), add(mul(a, oa * jag), mul(b, ob * jag)))


ANIME_SUB = 4   # アニメ: ジグザグの辺を細かく分ける数 (折れ目だけで向きが変わり、円柱がつぶれない)


def strand_rows(style, count):
    """点 count 個の列の、帯の行の数 (アニメは辺を ANIME_SUB に分ける)。"""
    count = int(count)
    return (count - 1) * ANIME_SUB + 1 if style == STYLE_ANIME else count


def _subdivide(pts, widths, parts):
    """点の列を、辺ごとに parts に分けた列にする (位置・太さは直線で補間)。"""
    out_p, out_w = [], []
    for k in range(len(pts) - 1):
        for s in range(parts):
            t = s / float(parts)
            out_p.append(add(pts[k], mul(sub(pts[k + 1], pts[k]), t)))
            out_w.append(widths[k] + (widths[k + 1] - widths[k]) * t)
    out_p.append(pts[-1])
    out_w.append(widths[-1])
    return out_p, out_w


def _strand_rows(pts, widths, cp, cdir, style=None):
    if style == STYLE_ANIME:
        pts, widths = _subdivide(pts, widths, ANIME_SUB)
    out = []
    n = len(pts)
    for j in range(n):
        j0, j1 = max(0, j - 1), min(n - 1, j + 1)
        p = pts[j]
        t = sub(pts[j0], pts[j1])
        x = unit(t) if mag(t) > 1e-9 else (0.0, -1.0, 0.0)
        tc = sub(cp, p)
        if mag(tc) < 1e-6:
            tc = mul(cdir, -1.0)
        z = ortho(x, tc)
        out.append((p, (x, cross(z, x), z), widths[j]))
    return out


def lightning_rows(style, frame, v, top, ground, cp, cdir, segments, branches, branch_segments, strike=False):
    """全部の点の行 [(位置, (X, Y, Z), 太さ)] (本筋 → 枝の順) と濃さ。光っていなければ太さ 0。"""
    sd = v["seed"]
    n, m = int(segments), int(branch_segments)
    if strike is False:
        strike = lightning_strike(frame, v)
    count = strand_rows(style, n) + int(branches) * strand_rows(style, m)
    if strike is None:
        return [(top, IDENTITY, 0.0)] * count, 0.0
    c, ts = strike
    t0, g0 = lightning_ends(c, v, top, ground)
    length, d, a, b = lightning_frame(t0, g0)
    prog = 1.0 if v["grow_frames"] <= 0 else clamp(0, 1, (frame - ts) / v["grow_frames"])   # 0 なら一瞬で伸び切る
    pts, widths = [], []
    last = min(n - 1, int(math.floor((prog + 1e-9) * max(n - 1, 1))))   # 伸びた所までの点
    for k in range(n):
        u = k / float(max(n - 1, 1))
        pts.append(lightning_main_point(style, k, n, c, v, t0, g0))
        widths.append(v["width"] * (1 - (1 - v["taper"]) * u) if k <= last else 0.0)
    for k in range(last + 1, n):
        pts[k] = pts[last]
    rows = _strand_rows(pts, widths, cp, cdir, style)
    for br in range(int(branches)):
        kb = int(math.floor((n - 1) * (0.15 + 0.6 * h(br, c, 80, sd))))
        tb = kb / float(max(n - 1, 1))
        start = pts[min(kb, last)]
        db = unit(add(mul(d, 0.8), mul(add(mul(a, (h(br, c, 81, sd) - 0.5) * 2), mul(b, (h(br, c, 82, sd) - 0.5) * 2)), 0.9)))
        lb = length * v["branch_length"] * (0.4 + 0.6 * h(br, c, 83, sd))
        ab = ortho(db, (1.0, 0.0, 0.0))
        bb = cross(db, ab)
        pb = clamp(0, 1, (prog - tb) / 0.3)
        cb = c * 13 + br + 1
        bpts, bw = [], []
        blast = min(m - 1, int(math.floor((pb + 1e-9) * max(m - 1, 1)))) if prog > tb else 0
        for j in range(m):
            u = j / float(max(m - 1, 1))
            oa, ob = lightning_branch_offset(style, j, m, u, cb, sd)
            jag = lb * v["jag"] * jag_scale(v, tb)   # 枝は出た所のギザギザの大きさ
            bpts.append(add(add(start, mul(db, lb * u)), add(mul(ab, oa * jag), mul(bb, ob * jag))))
            bw.append(v["width"] * 0.5 * (1 - (1 - v["taper"]) * tb) * (1 - u) if prog > tb and j <= blast else 0.0)
        for j in range(blast + 1, m):
            bpts[j] = bpts[blast]
        rows += _strand_rows(bpts, bw, cp, cdir, style)
    return rows, lightning_alpha(frame, v, c, ts)


def lightning_alpha(frame, v, c, ts):
    """光っている回の濃さ (ちらつき: 2 フレーム目からは flicker の割合で消える。だんだん弱まる)。"""
    phase = math.floor(frame - ts)
    on = 1.0 if phase <= 0 or h(c, phase, 90, v["seed"]) >= v["flicker"] else 0.0
    return v["opacity"] * on * (1 - 0.5 * clamp(0, 1, (frame - ts) / max(v["duration"], 0.01)))


IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))



# ---------------------------------------------------------------- 放電 (2 つのロケータの間に流れ続ける稲妻)
def discharge_strike(frame, v):
    """放電の今の回 (c, 始まり)。開始〜終わりのフレームの間、「形が変わる間隔」ごとに形が変わる。"""
    if frame < v["start_frame"] or frame > v["end_frame"]:
        return None
    chg = max(v["change_frames"], 0.5)
    c = int(math.floor((frame - v["start_frame"]) / chg))
    return c, v["start_frame"] + c * chg


def discharge_values(v):
    """放電を稲妻の計算に渡す値 (伸びは一瞬・1 回の長さ = 形が変わる間隔)。"""
    return dict(v, grow_frames=0.0, duration=max(v["change_frames"], 0.5))


def discharge_rows(style, frame, v, start, end, cp, cdir, segments, branches, branch_segments):
    """放電の帯の行と濃さ (lightning_rows と同じ形)。start = 発生、end = 終端 (Y が上)。"""
    return lightning_rows(style, frame, discharge_values(v), start, end, cp, cdir, segments, branches,
                          branch_segments, strike=discharge_strike(frame, v))


def discharge_samples(v):
    """当たりを調べるフレーム (開始〜終わりを「爆発の間隔」ごと)。"""
    step = max(v["burst_interval"], 0.5)
    out = []
    t = v["start_frame"]
    while t <= v["end_frame"] + 1e-6:
        out.append(t)
        t += step
    return out


def discharge_probe(start, end, reach):
    """当たりを調べる線分 (発生と終端の真ん中から、終端の少し先 (reach) まで)。"""
    d = sub(end, start)
    n = mag(d)
    u = unit(d) if n > 1e-9 else (0.0, -1.0, 0.0)
    return add(start, mul(d, 0.5)), add(end, mul(u, reach))



# ---------------------------------------------------------------- 細い煙 (たばこ・線香の、たゆたう煙の筋)
# 煙の筋は「同じ所から出た煙が、それぞれ流されて並んだ跡」。筋 s の点 j は、出てから a フレーム
# (a = 消えるまで × j / (点の数 - 1)) たった煙で、その位置を出た時刻 te = frame - a から直接計算する
# (前のフレームから積み上げないので、どのフレームにも飛べる)。
#   上がる: 速さ × a。風: 出てから今までに流された距離
#   揺れ:  まっすぐ上がる間は 0、その後 a とともに大きくなる。なめらかな乱数で、入力は「高さ / 揺れの長さ」と
#          「出た時刻 × 揺れの速さ」なので、出た煙ごとに揺れの形を持ったまま上がり、ゆっくり形を変えながら
#          上ほど大きく曲がる (筋どうしは乱数が違うので、絡み合って巻いたように見える)
def _snoise(x, c, k, sd):
    """なめらかな乱数 (-1〜1)。整数の所の乱数を smoothstep でつなぐ。"""
    i = math.floor(x)
    f = x - i
    f = f * f * (3 - 2 * f)
    a = h(i, c, k, sd)
    return (a + (h(i + 1, c, k, sd) - a) * f) * 2 - 1


def wisp_point(s, j, frame, v, points, emitter_at, wind):
    """筋 s の点 j の (位置, 太さ, 生きているか)。emitter_at(t) は出る場所の行列 (Y が上)。"""
    sd = v["seed"]
    life = max(v["life"], 1.0)
    a = life * j / float(max(points - 1, 1))
    te = frame - a
    alive = te >= v["start_frame"]
    em = emitter_at(te)
    e = (em[12], em[13], em[14])
    spd = v["speed"] * (1 + (h(s, 0, 41, sd) - 0.5) * 0.4)
    y = spd * a
    lam = v["laminar"]
    r = clamp(0, 1, (a - lam) / max(life - lam, 1e-3))
    amp = v["turbulence"] * r ** 1.5
    q = y / max(v["wavelength"], 0.01) + v["swirl"] * te
    ox = amp * (_snoise(q, s, 46, sd) + 0.5 * _snoise(2.1 * q + 7.3, s, 47, sd))         + v["root_spread"] * (h(s, 0, 44, sd) - 0.5)
    oz = amp * (_snoise(q + 13.7, s, 48, sd) + 0.5 * _snoise(2.1 * q + 3.1, s, 49, sd))         + v["root_spread"] * (h(s, 0, 45, sd) - 0.5)
    wd = mul(sub(wind.drift_at(frame), wind.drift_at(te)), wind.influence)
    p = add(add(e, (ox, y, oz)), wd)
    width = v["width_start"] + (v["width_end"] - v["width_start"]) * (a / life) ** 0.7
    return p, width, alive


def wisp_rows(s, frame, v, points, emitter_at, wind, cp, cdir):
    """筋 s の帯の行 [(位置, (X, Y, Z), 太さ)] (根元 → 先)。まだ出ていない所は太さ 0 で、一番古い点に重ねる。"""
    pts, widths = [], []
    last = -1
    for j in range(int(points)):
        p, w, alive = wisp_point(s, j, frame, v, int(points), emitter_at, wind)
        pts.append(p)
        widths.append(w if alive else 0.0)
        if alive:
            last = j
    if last < 0:
        return [(pts[0], IDENTITY, 0.0)] * len(pts)
    for j in range(last + 1, len(pts)):
        pts[j] = pts[last]
    return _strand_rows(pts, widths, cp, cdir)



# ---------------------------------------------------------------- 波打つ布 (人形劇の、透ける布を重ねた海)
# 布 l の格子の点 (長さの向き i / 横の向き k) の高さのずれ。布の空間 (Y が上、x が長さ、z が横。
# 布の真ん中が原点) で、x と z は -0.5〜0.5 の割合。波は長さの向きに流れ、横にも少しねじれる。
# 布ごとに「布ごとのずれ」だけ波のタイミングが遅れる (順にあおったように見える)。
def cloth_layer_offset(layer, v):
    """布 l の置き場所 (舞台の空間: 奥 (-Z) へ重なり、少しずつ高く)。"""
    return (0.0, layer * v["height_step"], -layer * v["spacing"])


def cloth_wave(layer, x, z, frame, v):
    """布 l の、割合 (x, z) の所の高さのずれ。"""
    sd = v["seed"]
    length, width = v["length"], max(v["width"], 0.01)
    kx = TAU / max(v["wavelength"], 0.01)
    w = TAU / max(v["period"], 1.0)
    t = frame
    ph = TAU * (layer * v["phase_step"] + 0.15 * h(layer, 0, 61, sd))
    ph2 = TAU * h(layer, 0, 62, sd)
    X, Z = x * length, z * width
    y = math.sin(kx * X - w * t - ph) + v["cross"] * math.sin(0.6 * kx * X + TAU * 0.5 * Z / width + 1.4 * w * t + ph2)
    env = math.sin(math.pi * (x + 0.5)) if v["pin_ends"] else 1.0
    return v["amplitude"] * y * env


def cloth_lattice(layer, frame, v, s_div, u_div):
    """布 l の格子の点の高さのずれ [[k ごと] i ごと] (長さの向きに s_div 個、横に u_div 個)。"""
    out = []
    for i in range(int(s_div)):
        x = i / float(max(s_div - 1, 1)) - 0.5
        row = []
        for k in range(int(u_div)):
            z = k / float(max(u_div - 1, 1)) - 0.5
            row.append(cloth_wave(layer, x, z, frame, v))
        out.append(row)
    return out


def cloth_color(layer, layers, v):
    """手前 (0) → 奥の布の色。"""
    t = layer / float(max(layers - 1, 1))
    return tuple(a + (b - a) * t for a, b in zip(v["color_front"], v["color_back"]))



# ---------------------------------------------------------------- スピードライン (トンネル)
# カメラの空間 (X 右・Y 上・-Z 前。Maya も Blender も同じ) で考える。カメラはトンネル (円柱) の軸の上にいて、
# トンネルの軸は消失点 (カメラの空間の点) の方へ伸びる。トンネルの空間は、ローカル -Z が消失点の向き、
# Y がなるべく上 (aimConstraint (aim -Z, up Y) と同じ)。線・形はトンネルの内側の壁に並んで、軸に沿って流れる。
TUNNEL_AWAY = u"奥へ"
TUNNEL_TOWARD = u"手前へ"


def tunnel_basis(vanish):
    """トンネルの空間の軸 (X, Y, Z) (カメラの空間で)。"""
    n = mag(vanish)
    z = mul(vanish, -1.0 / n) if n > 1e-9 else (0.0, 0.0, 1.0)
    x = cross((0.0, 1.0, 0.0), z)
    x = unit(x) if mag(x) > 1e-6 else (1.0, 0.0, 0.0)
    return x, cross(z, x), z


def tunnel_width_at(t, v):
    """線の長さ方向の位置 t (0 = 入り (後ろの端)、1 = 終わり (先頭)) での太さ (半分)。

    スピードラインと同じ、入り・中・終わりの 3 つを通る 2 次曲線 (0 未満は 0)。
    """
    return max(0.0, v["width_start"] * 2.0 * (t - 0.5) * (t - 1.0) - v["width_mid"] * 4.0 * t * (t - 1.0)
               + v["width_end"] * 2.0 * t * (t - 0.5))


def tunnel_roll(frame, v):
    """トンネルの回転 (ラジアン): 回転 (度) + 回転の速さ (度 / フレーム) × フレーム。"""
    return math.radians(v["roll"] + v["roll_speed"] * frame)


def tunnel_item(i, frame, v, line):
    """線・形 i の (トンネルの空間の位置, 軸 (X, Y, Z), 大きさ (x, y, z))。消えている時は大きさ 0。

    奥へ: カメラの近く (手前の端) から出て、完全に消える距離まで遠ざかる。手前へ: その逆。
    消え始める距離 → 完全に消える距離 で小さくなり、カメラのすぐ前 (手前の端 + 手前で消える距離) でも小さくなる。
    トンネルが回る (回転の速さ) と、線・形は軸の周りを回りながら流れる (らせん = 弧を描いて飛んでくる)。
    線は、その通り道の少し前 (尻尾) から今の位置 (先頭) までを結ぶ向きに伸びる。
    """
    sd = v["seed"]
    near, far = v["near"], max(v["fade_end"], v["near"] + 0.01)
    span = far - near
    speed = v["speed"] * (1 - v["speed_random"] * h(i, 0, 78, sd))   # 線・形ごとの速さ (ずっと同じ)
    n = speed * frame / span + h(i, 0, 71, sd)
    c = math.floor(n)
    q = n - c
    away = v["direction"] != TUNNEL_TOWARD
    depth = near + q * span if away else far - q * span
    th = TAU * h(i, c, 72, sd) + tunnel_roll(frame, v)
    r = v["radius"] * (1 + (h(i, c, 73, sd) - 0.5) * 2 * v["radius_random"])
    fade = (clamp(0, 1, (far - depth) / max(far - v["fade_start"], 1e-3))
            * clamp(0, 1, (depth - near) / max(v["near_fade"], 1e-3)))
    ct, st = math.cos(th), math.sin(th)
    p = (r * ct, r * st, -depth)
    inward = (-ct, -st, 0.0)
    if line:
        # 尻尾: 通り道 (らせん) を長さ L だけ戻った所。a = 流れる向きと軸の間の角
        length = v["length"] * (1 - v["length_random"] * h(i, c, 77, sd))
        a = math.atan2(r * math.radians(v["roll_speed"]), speed)
        dth = length * math.sin(a) / max(r, 1e-6)
        dz = length * math.cos(a)
        tail_depth = depth - dz if away else depth + dz
        d = sub(p, (r * math.cos(th - dth), r * math.sin(th - dth), -tail_depth))
        chord = mag(d)
        y = mul(d, 1.0 / chord) if chord > 1e-9 else ((0.0, 0.0, -1.0) if away else (0.0, 0.0, 1.0))
        x = cross(y, inward)
        if mag(x) < 1e-6:
            x = cross(y, (0.0, 0.0, 1.0))
        x = unit(x)
        thick = 1 - v["thickness_random"] * h(i, c, 76, sd)
        return p, (x, y, cross(x, y)), (thick * fade, chord * fade, 1.0)
    # 形: 壁に貼り付き (上 = 奥の向き)、回る
    y0 = (0.0, 0.0, -1.0)
    x0 = cross(y0, inward)
    a = math.radians(v["spin"] * frame + 360.0 * h(i, c, 75, sd) * v["spin_random"])
    x = add(mul(x0, math.cos(a)), mul(y0, math.sin(a)))
    y = sub(mul(y0, math.cos(a)), mul(x0, math.sin(a)))
    sz = v["size"] * (1 + (h(i, c, 74, sd) - 0.5) * 2 * v["size_random"]) * fade
    return p, (x, y, inward), (sz, sz, sz)


def tunnel_ghost_scale(k, v):
    """k 番目の残像 (1 = すぐ後ろ) の大きさの倍率。"""
    return 1.0 - v["ghost_shrink"] * k / (int(v["ghost_count"]) + 1.0)


def tunnel_ghost_opacity(k, v):
    """k 番目の残像の不透明度 (後ろほど薄い)。"""
    return v["opacity"] * (1.0 - v["ghost_fade"] * k / (int(v["ghost_count"]) + 1.0))


def tunnel_ghost(i, frame, k, v, line):
    """線・形 i の k 番目の残像: 残像の間隔 × k フレーム前の線・形を、小さくしたもの。"""
    p, axes, s = tunnel_item(i, frame - k * v["ghost_step"], v, line)
    m = tunnel_ghost_scale(k, v)
    return p, axes, (s[0] * m, s[1] * m, s[2] * m)
