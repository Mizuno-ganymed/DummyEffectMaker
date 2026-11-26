# -*- coding: utf-8 -*-
"""バルカン砲 (Blender 版): 発射した時点の目標へ、まっすぐ弾が飛ぶ。

Maya 版 (tools/vulcan.py) と同じパラメータ・同じ計算 (bullet_position / bullet_visible)。
    dfx_vulcanN             ルート (パラメータ)
      dfx_vulcanN_muzzle    発射位置 (動かしてよい。キーを打ってよい)
      dfx_vulcanN_target    目標 (同上)
      dfx_vulcanN_bullet_001 ...  弾 (同じメッシュを共有。毎フレーム置き直す)
"""
from __future__ import absolute_import

from mathutils import Vector

from ...core import BaseTool
from ...tools import homing, vulcan as mv
from .. import runtime as rt
from . import _build as bd, _bullets as _bl


class VulcanTool(BaseTool):
    label = mv.VulcanTool.label
    tab = mv.TAB
    category = mv.CATEGORY
    order = 20
    hosts = ("blender",)
    tooltip = (u"発射位置と目標の Empty と、まっすぐ飛ぶ弾を作ります。\n"
               u"弾は発射した時点の目標へ飛ぶので、後から目標を動かしても飛んでいる弾は変わりません。")
    params = mv.VulcanTool.params

    def run(self, ctx, **v):
        import bpy
        frames = homing.check_fire_frames(v)
        root = rt.new_root(mv.EFFECT_TYPE, "dfx_vulcan")
        rt.add_params(root, mv.stored_params(), v, mv.LIVE_PARAMS)
        rt.add_control(root, "muzzle", (0.0, 0.0, 0.0))
        rt.add_control(root, "target", (v["length"], 0.0, 0.0))
        build(root, v)
        rt.update_root(root)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        root.select_set(True)
        bpy.context.view_layer.objects.active = root
        ctx.log(u"  %s: %d 発 (フレーム %s 〜 %s)" % (root.name, len(frames), frames[0], frames[-1]))
        return root.name


def build(root, v):
    frames = homing.check_fire_frames(v)
    mesh, _ = _bl.sphere_material(root, v, "_bulletMesh", v["color"], v["subdivisions"])
    for i, fire in enumerate(frames):
        obj = bd.new_object(root, "%s_bullet_%03d" % (root.name, i + 1), mesh)
        obj["dfx_fire"] = float(fire)
        obj.rotation_mode = "QUATERNION"


def bullets(root):
    return _bl.bullet_objects(root)


def _state(v, muzzle, target, fire, frame, cache):
    """弾 1 発の (位置, 進む向き, 表示)。発射した時点の発射位置・目標で決まる。"""
    travel = max(v["travel_frames"], 0.001)
    if fire not in cache:
        cache[fire] = (rt.matrix_world_at(muzzle, fire).translation.copy(),
                       rt.matrix_world_at(target, fire).translation.copy())
    a, c = cache[fire]
    min_ratio = min(max(v["min_speed"] / 100.0, 0.0), 1.0)
    p = mv.bullet_position(tuple(a), tuple(c), frame, fire, travel, v["speed_mode"], min_ratio)
    return Vector(p), Vector(c) - Vector(a), mv.bullet_visible(frame, fire, travel)


def sampler(root):
    v = rt.read_params(root, mv.stored_params())
    muzzle, target = rt.control(root, "muzzle"), rt.control(root, "target")
    fires = [b["dfx_fire"] for b in bullets(root)]
    cache = {}

    def at(t):
        out = []
        for fire in fires:
            p, _, vis = _state(v, muzzle, target, fire, t, cache)
            out.append((p, vis))
        return out
    return at


def update(root, frame):
    v = rt.read_params(root, mv.stored_params())
    muzzle, target = rt.control(root, "muzzle"), rt.control(root, "target")
    if muzzle is None or target is None:
        return
    _bl.refresh_sphere(root, v["subdivisions"], v["color"])
    squashed = v["shape"] == homing.SHAPE_SQUASHED
    radius = v["radius"]
    size = (radius * (v["stretch"] if squashed else 1.0), radius, radius)
    hidden = _bl.hide_frames(root)
    cache = {}
    for i, b in enumerate(bullets(root)):
        p, d, vis = _state(v, muzzle, target, b["dfx_fire"], frame, cache)
        if not vis or _bl.is_hit(hidden, i, frame):
            b.scale = (0.0, 0.0, 0.0)
            continue
        b.location = p
        if d.length > 1e-9:
            b.rotation_quaternion = d.to_track_quat("X", "Z")
        b.scale = size


bd.register_builder(mv.EFFECT_TYPE, mv.stored_params, mv.LIVE_PARAMS, build, update)
_bl.register_sampler(mv.EFFECT_TYPE, sampler)
