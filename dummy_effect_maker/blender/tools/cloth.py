# -*- coding: utf-8 -*-
"""波打つ布 (Blender 版): 透ける布を何枚も重ねて波打たせる (人形劇の海)。

Maya 版 (tools/cloth.py) と同じパラメータ・同じ計算 (motion.cloth_wave)。
    dfx_clothN               ルート (パラメータ)
      dfx_clothN_stage       舞台 (Empty。動かす・回す・拡大縮小すると、布の束がまとめて動く)
        dfx_clothN_layer_01  布 1 枚分 (手前 → 奥。置き場所は毎フレーム決める)
          dfx_clothN_cloth_01    布の板 (Lattice モディファイアーで曲げる)
          dfx_clothN_lattice_01  布を波打たせる格子 (点の高さを毎フレーム置き直す)
Blender は Z が上: 布の長さは X、横は Y、奥 (Maya の -Z) は +Y。
"""
from __future__ import absolute_import

import bmesh
import bpy

from ... import motion as mo
from ...core import BaseTool, ToolError
from ...tools import cloth as mc
from .. import runtime as rt
from . import _build as bd, _wx as wx


class ClothTool(BaseTool):
    label = mc.ClothTool.label
    tab = mc.TAB
    category = mc.CATEGORY
    order = 950
    hosts = ("blender",)
    tooltip = (u"人形劇の海のように、透ける布を何枚も重ねて波打たせます (オーガンジー・シフォンの布)。\n"
               u"舞台の Empty (_stage) を動かす・回す・拡大縮小すると、布の束がまとめて動きます。\n"
               u"オブジェクトを選んでいれば、その位置に置きます。枚数・長さ・幅・細かさを変えたら「エフェクトを更新」。")
    params = mc.ClothTool.params

    def run(self, ctx, **v):
        kind = mc.KIND
        root, _, target = wx.create_root(bpy.context, ctx, kind.effect_type, "dfx_cloth", kind.stored_params(),
                                         kind.live, v, need_camera=False, need_wind=False)
        stage = rt.add_control(root, "stage", (0.0, 0.0, 0.0), display="ARROWS", size=max(v["width"], 1.0))
        if target is not None:
            stage.location = target.matrix_world.translation
        build(root, v)
        rt.update_root(root)
        wx.select_only(root)
        return root.name


def _plane(name, length, width, owner):
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=mc.MESH_DIVISIONS[0], y_segments=mc.MESH_DIVISIONS[1], size=0.5)
    for vert in bm.verts:
        vert.co.x *= length
        vert.co.y *= width
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    rt.own(owner, mesh)
    return mesh


def build(root, v):
    stage = rt.control(root, "stage")
    if stage is None:
        raise ToolError(u"%s の舞台の Empty が見つかりません (削除された可能性があります)" % root.name)
    layers, s_div = int(v["layers"]), int(v["divisions"])
    length, width = v["length"], v["width"]
    for l in range(layers):
        tag = "%02d" % (l + 1)
        layer = rt.new_empty("%s_layer_%s" % (root.name, tag), display="PLAIN_AXES", size=0.2, parent=stage)
        rt.own(root, layer)
        layer["dfx_layer"] = l
        lat = bpy.data.lattices.new("%s_latticeShape_%s" % (root.name, tag))
        lat.points_u, lat.points_v, lat.points_w = s_div, mc.ACROSS, 2
        lat.interpolation_type_u = "KEY_CARDINAL"   # 点を通るなめらかな曲がり方
        lat.interpolation_type_v = lat.interpolation_type_w = "KEY_LINEAR"
        rt.own(root, lat)
        lat_obj = bpy.data.objects.new("%s_lattice_%s" % (root.name, tag), lat)
        rt.collection().objects.link(lat_obj)
        lat_obj.parent = layer
        # Blender の格子の点は 1 ずつ離れて並ぶ (16 点なら -7.5〜7.5)。布の大きさになるように拡大する
        lat_obj.scale = (length / float(max(s_div - 1, 1)), width / float(mc.ACROSS - 1), 1.0)
        lat_obj.hide_render = True
        lat_obj.hide_viewport = True
        rt.own(root, lat_obj)
        mat = rt.flat_material("%s_mat_%s" % (root.name, tag), mo.cloth_color(l, layers, v), v["opacity"], owner=root)
        mesh = _plane("%s_clothMesh_%s" % (root.name, tag), length, width, root)
        mesh.materials.append(mat)
        obj = bd.new_object(root, "%s_cloth_%s" % (root.name, tag), mesh, parent=layer)
        mod = obj.modifiers.new("dfx_wave", "LATTICE")
        mod.object = lat_obj
        obj["dfx_layer"] = l
        obj["dfx_lattice"] = lat_obj


def layer_objects(root):
    out = [o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and "dfx_lattice" in o]
    return sorted(out, key=lambda o: o["dfx_layer"])


def lattice_points(lat, frame, v, layer):
    """格子の点 (Blender の並び: u (長さ) が一番速く、次に v (横)、w (上下))。Maya 版の点の高さと同じ。"""
    s_div, across = lat.points_u, lat.points_v
    disp = mo.cloth_lattice(layer, frame, v, s_div, across)
    rest = [0.0] * (len(lat.points) * 3)
    lat.points.foreach_get("co", rest)   # 元の位置 (点の間隔 1。上下は -0.5 / 0.5)
    out = list(rest)
    n = 0
    for w in range(2):
        for k in range(across):
            for i in range(s_div):
                # Blender の横 (+Y) は Maya の -Z: Maya の横の番号は反対から
                out[3 * n + 2] = rest[3 * n + 2] + disp[i][across - 1 - k]
                n += 1
    return out


def update(root, frame):
    v = rt.read_params(root, mc.KIND.stored_params())
    objs = layer_objects(root)
    layers = len(objs)
    for obj in objs:
        l = obj["dfx_layer"]
        obj.parent.location = mo.zup_vector(mo.cloth_layer_offset(l, v))
        rt.set_material(wx.material_of(obj), mo.cloth_color(l, layers, v), v["opacity"])
        lat = obj["dfx_lattice"].data
        lat.points.foreach_set("co_deform", lattice_points(lat, frame, v, l))
        lat.update_tag()
        obj["dfx_lattice"].update_tag()


bd.register_builder(mc.EFFECT_TYPE, mc.KIND.stored_params, mc.KIND.live, build, update)
