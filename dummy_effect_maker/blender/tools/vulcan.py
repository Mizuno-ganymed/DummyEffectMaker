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
from . import _build as bd


class VulcanTool(BaseTool):
    label = mv.VulcanTool.label
    tab = u"ツール"
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
    mesh = bd.sphere_mesh(root.name + "_bulletMesh", v["subdivisions"], root)
    mat = rt.flat_material(root.name + "_mat", v["color"], 1.0, owner=root)
    mesh.materials.append(mat)
    root["dfx_mesh"] = mesh
    root["dfx_material"] = mat
    root["dfx_mesh_sub"] = int(v["subdivisions"])
    for i, fire in enumerate(frames):
        obj = bd.new_object(root, "%s_bullet_%03d" % (root.name, i + 1), mesh)
        obj["dfx_fire"] = float(fire)
        obj.rotation_mode = "QUATERNION"


def bullets(root):
    import bpy
    return sorted((o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and "dfx_fire" in o),
                  key=lambda o: o.name)


def update(root, frame):
    v = rt.read_params(root, mv.stored_params())
    muzzle, target = rt.control(root, "muzzle"), rt.control(root, "target")
    if muzzle is None or target is None:
        return
    mesh, mat = root.get("dfx_mesh"), root.get("dfx_material")
    if mesh is not None and root.get("dfx_mesh_sub") != int(v["subdivisions"]):
        bd.fill_sphere(mesh, v["subdivisions"])
        root["dfx_mesh_sub"] = int(v["subdivisions"])
    if mat is not None:
        rt.set_material(mat, v["color"])
    travel = max(v["travel_frames"], 0.001)
    min_ratio = min(max(v["min_speed"] / 100.0, 0.0), 1.0)
    squashed = v["shape"] == homing.SHAPE_SQUASHED
    radius = v["radius"]
    size = (radius * (v["stretch"] if squashed else 1.0), radius, radius)
    for b in bullets(root):
        fire = b["dfx_fire"]
        if not mv.bullet_visible(frame, fire, travel):
            b.scale = (0.0, 0.0, 0.0)
            continue
        a = rt.matrix_world_at(muzzle, fire).translation
        c = rt.matrix_world_at(target, fire).translation
        p = mv.bullet_position(tuple(a), tuple(c), frame, fire, travel, v["speed_mode"], min_ratio)
        b.location = p
        d = Vector(c) - Vector(a)
        if d.length > 1e-9:
            b.rotation_quaternion = d.to_track_quat("X", "Z")
        b.scale = size


bd.register_builder(mv.EFFECT_TYPE, mv.stored_params, mv.LIVE_PARAMS, build, update)
