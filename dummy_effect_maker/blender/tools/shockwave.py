# -*- coding: utf-8 -*-
"""衝撃波 (Blender 版): 中心の Empty から、端の Empty の距離まで、ドーナツが広がる。

Maya 版 (tools/shockwave.py) と同じパラメータ・同じ計算 (ring_state / ring_start)。
    dfx_shockwaveN            ルート (パラメータ)
      dfx_shockwaveN_center   中心 (動かす・回転させると輪の向きが変わる。輪は中心のローカル XY 面)
        dfx_shockwaveN_ring_01 ...   輪 (トーラス。太さはシェイプキー「thick」)
      dfx_shockwaveN_edge     端 (中心からの距離まで広がる)
"""
from __future__ import absolute_import

from ...core import BaseTool
from ...tools import shockwave as sw
from .. import runtime as rt
from . import _build as bd


class ShockwaveTool(BaseTool):
    label = sw.ShockwaveTool.label
    tab = sw.TAB
    category = sw.CATEGORY
    order = 800
    hosts = ("blender",)
    tooltip = (u"中心の Empty から端の Empty の距離まで、ドーナツがブワッと広がる衝撃波を作ります。\n"
               u"中心の Empty を回転させると輪の向きが変わります (輪は中心のローカル XY 面)。")
    params = sw.ShockwaveTool.params

    def run(self, ctx, **v):
        import bpy
        root = rt.new_root(sw.EFFECT_TYPE, "dfx_shockwave")
        rt.add_params(root, sw.stored_params(), v, sw.LIVE_PARAMS)
        rt.add_control(root, "center", (0.0, 0.0, 0.0), display="PLAIN_AXES", size=0.5)
        rt.add_control(root, "edge", (v["distance"], 0.0, 0.0))
        build(root, v)
        rt.update_root(root)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        root.select_set(True)
        bpy.context.view_layer.objects.active = root
        target, frames, ok = sw.reach(v["distance"], v["speed"], v["decay"])
        ctx.log(u"  %s: 輪 %d 個 / 端まで %.1f フレームで広がる%s"
                % (root.name, v["count"], frames, u"" if ok else u" (端まで届きません)"))
        return root.name


def build(root, v):
    import bpy
    center = rt.control(root, "center")
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    root["dfx_material"] = mat
    sub_x = int(v["subdivisions"])
    sub_y = max(4, sub_x // 4)
    thin, faces = bd.torus_points(sub_x, sub_y, sw.BASE_TUBE)
    thick, _ = bd.torus_points(sub_x, sub_y, 1.0)
    for i in range(int(v["count"])):
        name = "%s_ring_%02d" % (root.name, i + 1)
        mesh = bpy.data.meshes.new(name + "Mesh")
        mesh.from_pydata(thin, [], faces)
        for poly in mesh.polygons:
            poly.use_smooth = True
        mesh.materials.append(mat)
        rt.own(root, mesh)
        ring = bd.new_object(root, name, mesh, parent=center)
        ring["dfx_ring"] = i
        ring.shape_key_add(name="Basis", from_mix=False)
        key = ring.shape_key_add(name="thick", from_mix=False)
        for k, co in enumerate(thick):
            key.data[k].co = co


def rings(root):
    import bpy
    return sorted((o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and "dfx_ring" in o),
                  key=lambda o: o["dfx_ring"])


def update(root, frame):
    v = rt.read_params(root, sw.stored_params())
    center, edge = rt.control(root, "center"), rt.control(root, "edge")
    if center is None or edge is None:
        return
    mat = root.get("dfx_material")
    if mat is not None:
        rt.set_material(mat, v["color"], v["opacity"])
    distance = (center.matrix_world.inverted() @ edge.matrix_world.translation).length
    for ring in rings(root):
        r, w, vis = sw.ring_state(frame - sw.ring_start(v, ring["dfx_ring"]), v, distance)
        if not vis or r < 1e-6:
            ring.scale = (0.0, 0.0, 0.0)
            continue
        s = max(r, 1e-4)
        ring.scale = (s, s, s * v["flatten"])
        keys = ring.data.shape_keys
        if keys is not None and "thick" in keys.key_blocks:
            keys.key_blocks["thick"].value = min(1.0, max(0.0, (w / s - sw.BASE_TUBE) / (1.0 - sw.BASE_TUBE)))


bd.register_builder(sw.EFFECT_TYPE, sw.stored_params, sw.LIVE_PARAMS, build, update)
