# -*- coding: utf-8 -*-
"""電撃 (Blender 版): 中心の Empty (_center) のまわりに、稲妻がまとわりつく / 外へ放電する。

Maya 版 (tools/electric.py) と同じパラメータ・同じ計算 (motion.py の electric_*)。
    dfx_electricN          ルート (パラメータ。dfx_camera = 帯が向くカメラ)
      dfx_electricN_center 中心 (Empty の球。大きさで、まとわりつく形 (楕円体) が変わる。
                           オブジェクトを選んで作ると、チャイルドオブでそれに付いて動き、大きさを合わせる)
      dfx_electricN_bolt   帯 (線ごとの点に 2 頂点。毎フレーム置き直す)。アニメは円柱
      dfx_electricN_glow   2 本目の帯 (実写: 外側の光 / アニメ: 円柱の表面のすぐ前の芯)
"""
from __future__ import absolute_import

import bpy
from mathutils import Matrix, Vector

from ... import motion as mo
from ...core import BaseTool
from ...tools import electric as me
from .. import runtime as rt
from . import _build as bd, _wx as wx
from .lightning import strand_objects

BOX_TYPES = ("MESH", "CURVE", "SURFACE", "FONT", "META")


def _tool(maya_tool):
    tip = maya_tool.tooltip.replace(u"ロケータ", u"Empty")
    return dict(label=maya_tool.label, tab=wx.TAB, category=me.CATEGORY, order=maya_tool.order,
                hosts=("blender",), params=maya_tool.params,
                tooltip=tip + u"\n本数・点の数 (実写は光の広がり、アニメは円柱の分割数) を変えたら「エフェクトを更新」を押します。")


def target_box(obj):
    """オブジェクトのローカルの空間のバウンディングボックス (bmin, bmax)。無ければ None。"""
    if obj.type in BOX_TYPES:
        pts = [Vector(c) for c in obj.bound_box]
    else:   # Empty など: 子孫の形の箱を、このオブジェクトのローカルに戻す
        inv = obj.matrix_world.inverted_safe()
        pts = [inv @ (o.matrix_world @ Vector(c)) for o in obj.children_recursive if o.type in BOX_TYPES
               for c in o.bound_box]
    if not pts:
        return None
    return (tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3)))


def create(ctx, kind, v):
    root, cam, target = wx.create_root(bpy.context, ctx, kind.effect_type, kind.root_name.replace("#", ""),
                                       kind.stored_params(), kind.live, v, need_camera=True, need_wind=False)
    center = rt.add_control(root, "center", (0.0, 0.0, 1.0), display="SPHERE", size=1.0)
    if target is not None:
        box = target_box(target)
        c, half = me.fit_box(*box) if box else ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        con = center.constraints.new("CHILD_OF")   # オブジェクトに付いて動く (位置・大きさはオブジェクトの空間)
        con.target = target
        con.inverse_matrix = Matrix.Identity(4)
        con.set_inverse_pending = False
        center.location = c
        center.scale = half
        ctx.log(u"  %s にまとわりつきます" % target.name)
    build(root, v)
    rt.update_root(root)
    wx.select_only(root)
    return root.name


class ElectricRealTool(BaseTool):
    locals().update(_tool(me.ElectricRealTool))

    def run(self, ctx, **v):
        return create(ctx, me.REAL, v)


class ElectricAnimeTool(BaseTool):
    locals().update(_tool(me.ElectricAnimeTool))

    def run(self, ctx, **v):
        return create(ctx, me.ANIME, v)


KINDS = dict((k.effect_type, k) for k in (me.REAL, me.ANIME))


def build(root, v):
    style = me.STYLES[root.get(rt.TYPE_KEY)]
    n, count = int(v["segments"]), int(v["count"])
    for obj in strand_objects(root, v, style, [mo.strand_rows(style, n)] * count):
        obj.data["dfx_strands"] = [n, count]


def center_yup(center):
    """中心の Empty の (位置, 軸 a0 a1 a2) (Y が上。軸は大きさ込み。Maya のロケータの X / Y / Z にあたる)。"""
    m = center.matrix_world
    return (mo.yup_vector(tuple(m.translation)),
            (mo.yup_vector(tuple(m.col[0].xyz)), mo.yup_vector(tuple(m.col[2].xyz)),
             mo.yup_vector(tuple(-m.col[1].xyz))))


def update(root, frame):
    effect_type = root.get(rt.TYPE_KEY)
    v = rt.read_params(root, KINDS[effect_type].stored_params())
    center = rt.control(root, "center")
    bolt, glow = wx.part(root, "_bolt"), wx.part(root, "_glow")
    if center is None or bolt is None:
        return
    n, count = list(bolt.data["dfx_strands"])
    cp, cdir = wx.camera_yup(root)
    pos, axes = center_yup(center)
    rows = mo.electric_rows(me.STYLES[effect_type], frame, v, pos, axes, cp, cdir, count, n)
    placements = [(p, ax, (w,) * 3) if w > 1e-9 else wx.hidden(p) for p, ax, w in rows]
    rt.set_material(wx.material_of(bolt), v["color"], v["opacity"])
    wx.write_particles(bolt, placements)
    if glow is not None:
        rt.set_material(wx.material_of(glow), v["glow_color"], v["opacity"] * v["glow_opacity"])
        wx.write_particles(glow, placements)


for _kind in (me.REAL, me.ANIME):
    bd.register_builder(_kind.effect_type, _kind.stored_params, _kind.live, build, update)
