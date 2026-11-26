# -*- coding: utf-8 -*-
"""追尾弾 (Blender 版): A → B (経由) → C へ、弾を一定間隔で撃ち続ける。

Maya 版 (tools/homing.py) と同じパラメータ・同じ計算。
    dfx_homingN             ルート (パラメータ)
      dfx_homingN_locA      発射位置 (B: 経由点 / C: 着弾点。動かしてよい。キーを打ってよい)
      dfx_homingN_path      軌道 (表示だけ。ロケータに付いて形が変わる)
      dfx_homingN_bullet_001 ...  弾 (同じメッシュを共有。毎フレーム置き直す)

軌道は Maya 版と同じ: なめらか = B を通る 2 次ベジェ / 折れ線 = A-B-C。
弾は軌道に沿って弧長で等速 (または減速) に進み、進む向きにローカル X を向ける。
"""
from __future__ import absolute_import

import bpy
from mathutils import Vector

from ...core import BaseTool
from ...tools import homing as mh
from .. import runtime as rt
from . import _build as bd, _bullets as _bl

PATH_SAMPLES = 48
ROLES = ("locA", "locB", "locC")


class HomingTool(BaseTool):
    label = mh.HomingTool.label
    tab = mh.HomingTool.tab
    category = mh.HomingTool.category
    order = 10
    hosts = ("blender",)
    tooltip = (u"発射位置 A / 経由点 B / 着弾点 C の Empty と、A から B を経由して C まで飛ぶ弾を作ります。\n"
               u"Empty を動かすと弾道が付いてきます。作成後はサイドバーでパラメータを調整できます。")
    params = mh.HomingTool.params

    def run(self, ctx, **v):
        frames = mh.check_fire_frames(v)
        root = rt.new_root(mh.EFFECT_TYPE, "dfx_homing")
        rt.add_params(root, mh.stored_params(), v, mh.LIVE_PARAMS)
        half = v["length"] * 0.5
        for role, pos in zip(ROLES, ((0.0, 0.0, 0.0), (half, 0.0, v["height"]), (v["length"], 0.0, 0.0))):
            rt.add_control(root, role, pos)
        build(root, v)
        rt.update_root(root)
        from . import _wx
        _wx.select_only(root)
        ctx.log(u"  %s: %d 発 (フレーム %s 〜 %s)" % (root.name, len(frames), frames[0], frames[-1]))
        return root.name


def build(root, v):
    frames = mh.check_fire_frames(v)
    mesh, _ = _bl.sphere_material(root, v, "_bulletMesh", v["color"], v["subdivisions"])
    for i, fire in enumerate(frames):
        obj = bd.new_object(root, "%s_bullet_%03d" % (root.name, i + 1), mesh)
        obj["dfx_fire"] = float(fire)
        obj.rotation_mode = "QUATERNION"
    # 軌道の表示 (ロケータに付いて形が変わる)
    curve = bpy.data.curves.new(root.name + "_pathShape", "CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(PATH_SAMPLES)
    rt.own(root, curve)
    path = bpy.data.objects.new(root.name + "_path", curve)
    rt.collection().objects.link(path)
    path.parent = root
    path.hide_select = True
    path.hide_render = True
    rt.own(root, path)
    root["dfx_path"] = path


def bullets(root):
    return _bl.bullet_objects(root)


# ---------------------------------------------------------------- 軌道
class Path(object):
    """A-B-C の軌道 (弧長で進み具合を決める)。"""

    def __init__(self, a, b, c, smooth):
        if smooth:
            mid = Vector(mh.bezier_control_through(tuple(a), tuple(b), tuple(c)))
            self.points = [a * ((1 - s) ** 2) + mid * (2 * (1 - s) * s) + c * (s * s)
                           for s in (k / float(PATH_SAMPLES) for k in range(PATH_SAMPLES + 1))]
        else:
            self.points = [a, b, c]
        self.lengths = [0.0]
        for p, q in zip(self.points[:-1], self.points[1:]):
            self.lengths.append(self.lengths[-1] + (q - p).length)

    def at(self, u):
        """弧長の割合 u (0〜1) の (位置, 進む向き)。"""
        total = self.lengths[-1]
        if total < 1e-12:
            return self.points[0].copy(), Vector((1.0, 0.0, 0.0))
        d = min(max(u, 0.0), 1.0) * total
        k = 0
        while k < len(self.lengths) - 2 and self.lengths[k + 1] < d:
            k += 1
        seg = self.lengths[k + 1] - self.lengths[k]
        f = (d - self.lengths[k]) / seg if seg > 1e-12 else 0.0
        p, q = self.points[k], self.points[k + 1]
        return p.lerp(q, f), (q - p)

    def tangent(self, u):
        """u の所の進む向き (前後の少し離れた点から。折れ目の手前・先ではその線の向き)。"""
        p, d = self.at(u)
        if len(self.points) <= 3:
            return d
        e = 0.5 / PATH_SAMPLES
        a, _ = self.at(max(0.0, u - e))
        b, _ = self.at(min(1.0, u + e))
        return b - a if (b - a).length > 1e-12 else d


def travel_u(v, fire, frame):
    """発射からの進み具合 (Maya 版の uValue のキーと同じ。キーの間は直線)。"""
    travel = v["travel_frames"]
    keys = mh.travel_keys(v["speed_mode"], v["min_speed"] / 100.0, fire, travel)
    if frame <= keys[0][0]:
        return keys[0][1]
    for (t0, u0), (t1, u1) in zip(keys[:-1], keys[1:]):
        if frame <= t1:
            return u0 + (u1 - u0) * (frame - t0) / (t1 - t0) if t1 > t0 else u1
    return keys[-1][1]


def _path_at(root, v, t, cache):
    if t not in cache:
        locs = [rt.control(root, r) for r in ROLES]
        if any(l is None for l in locs):
            return None
        pts = [rt.matrix_world_at(l, t).translation.copy() for l in locs]
        cache[t] = Path(pts[0], pts[1], pts[2], v["path_type"] == mh.PATH_SMOOTH)
    return cache[t]


def _state(root, v, fire, t, cache):
    path = _path_at(root, v, t, cache)
    if path is None:
        return None, None, False
    u = travel_u(v, fire, t)
    p, _ = path.at(u)
    from ...tools import vulcan as mv
    return p, path.tangent(u), mv.bullet_visible(t, fire, v["travel_frames"])


def sampler(root):
    v = rt.read_params(root, mh.stored_params())
    fires = [b["dfx_fire"] for b in bullets(root)]
    cache = {}

    def at(t):
        out = []
        for fire in fires:
            p, _, vis = _state(root, v, fire, t, cache)
            out.append((p if p is not None else Vector(), vis))
        return out
    return at


def update(root, frame):
    v = rt.read_params(root, mh.stored_params())
    cache = {}
    path = _path_at(root, v, frame, cache)
    if path is None:
        return
    _bl.refresh_sphere(root, v["subdivisions"], v["color"])
    shape = root.get("dfx_path")
    if shape is not None:
        pts = [path.at(k / float(PATH_SAMPLES))[0] for k in range(PATH_SAMPLES + 1)]
        for sp, p in zip(shape.data.splines[0].points, pts):
            sp.co = (p.x, p.y, p.z, 1.0)
    squashed = v["shape"] == mh.SHAPE_SQUASHED
    radius = v["radius"]
    size = (radius * (v["stretch"] if squashed else 1.0), radius, radius)
    hidden = _bl.hide_frames(root)
    for i, b in enumerate(bullets(root)):
        p, d, vis = _state(root, v, b["dfx_fire"], frame, cache)
        if not vis or _bl.is_hit(hidden, i, frame):
            b.scale = (0.0, 0.0, 0.0)
            continue
        b.location = p
        if d.length > 1e-9:
            b.rotation_quaternion = d.to_track_quat("X", "Z")
        b.scale = size


bd.register_builder(mh.EFFECT_TYPE, mh.stored_params, mh.LIVE_PARAMS, build, update)
_bl.register_sampler(mh.EFFECT_TYPE, sampler)
