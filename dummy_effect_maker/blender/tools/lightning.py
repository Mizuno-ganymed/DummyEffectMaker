# -*- coding: utf-8 -*-
"""稲妻 (Blender 版): 雲の点 (_top) から落ちる所 (_ground) へ、何回か落ちる。

Maya 版 (tools/lightning.py) と同じパラメータ・同じ計算 (motion.py の lightning_*)。
    dfx_lightningN          ルート (パラメータ。dfx_camera = 帯が向くカメラ)
      dfx_lightningN_top    雲の点 (動かしてよい)
      dfx_lightningN_ground 落ちる所 (動かしてよい)
      dfx_lightningN_bolt   帯 (本筋 → 枝の順の点ごとに 2 頂点。毎フレーム置き直す)
      dfx_lightningN_glow   2 本目の帯 (実写: 外側の光 / アニメ: 白い芯)
"""
from __future__ import absolute_import

import bpy

from ... import motion as mo
from ...core import BaseTool
from ...tools import lightning as ml
from .. import runtime as rt
from . import _build as bd, _wx as wx


def _tool(maya_tool):
    return dict(label=maya_tool.label, tab=wx.TAB, category=ml.CATEGORY, order=maya_tool.order,
                hosts=("blender",), params=maya_tool.params,
                tooltip=maya_tool.tooltip + u"\n点の数・枝の数・2 本目の帯の太さを変えたら「エフェクトを更新」を押します。")


def create(ctx, kind, v):
    root, cam, target = wx.create_root(bpy.context, ctx, kind.effect_type,
                                       kind.root_name.replace("#", ""), kind.stored_params(), kind.live, v,
                                       need_camera=True, need_wind=False)
    base = target.matrix_world.translation.copy() if target is not None else None
    for role, pos, display in (("top", (0.0, 0.0, v["height"]), "CONE"), ("ground", (0.0, 0.0, 0.0), "SPHERE")):
        c = rt.add_control(root, role, pos, display=display, size=max(v["height"] * 0.03, 0.2))
        if base is not None:
            c.location = (base.x + pos[0], base.y + pos[1], base.z + pos[2])
    if target is not None:
        ctx.log(u"  %s に落ちます" % target.name)
    build(root, v)
    rt.update_root(root)
    wx.select_only(root)
    return root.name


class LightningRealTool(BaseTool):
    locals().update(_tool(ml.LightningRealTool))

    def run(self, ctx, **v):
        return create(ctx, ml.REAL, v)


class LightningAnimeTool(BaseTool):
    locals().update(_tool(ml.LightningAnimeTool))

    def run(self, ctx, **v):
        return create(ctx, ml.ANIME, v)


def build(root, v):
    n, m, nb = int(v["segments"]), int(v["branch_segments"]), int(v["branches"])
    strands = [n] + [m] * nb
    faces = []
    k = 0
    for count in strands:
        for j in range(count - 1):
            faces.append((2 * (k + j), 2 * (k + j) + 1, 2 * (k + j) + 3, 2 * (k + j) + 2))
        k += count
    total = sum(strands)
    glow = v["glow_width"]
    for name, color, half, z in (("_bolt", v["color"], 0.5, 0.0),
                                 ("_glow", v["glow_color"], 0.5 * glow, -0.05 if glow >= 1 else 0.05)):
        mat = rt.flat_material(root.name + name + "_mat", color, v["opacity"], owner=root)
        obj = wx.particle_object(root, root.name + name, ([(0.0, half, z), (0.0, -half, z)], [], None), total,
                                 mat, faces=faces)
        obj.data["dfx_strands"] = [n, nb, m]


def update(root, frame):
    kind = ml.REAL if root.get(rt.TYPE_KEY) == ml.REAL.effect_type else ml.ANIME
    v = rt.read_params(root, kind.stored_params())
    top, gnd = rt.control(root, "top"), rt.control(root, "ground")
    bolt, glow = wx.part(root, "_bolt"), wx.part(root, "_glow")
    if top is None or gnd is None or bolt is None:
        return
    n, nb, m = list(bolt.data["dfx_strands"])
    cp, cdir = wx.camera_yup(root)
    rows, alpha = mo.lightning_rows(ml.STYLES[kind.effect_type], frame, v,
                                    mo.yup_vector(tuple(top.matrix_world.translation)),
                                    mo.yup_vector(tuple(gnd.matrix_world.translation)), cp, cdir, n, nb, m)
    placements = [(p, axes, (w,) * 3) if w > 1e-9 else wx.hidden(p) for p, axes, w in rows]
    rt.set_material(wx.material_of(bolt), v["color"], alpha)
    wx.write_particles(bolt, placements)
    if glow is not None:
        rt.set_material(wx.material_of(glow), v["glow_color"], alpha * v["glow_opacity"])
        wx.write_particles(glow, placements)


for _kind in (ml.REAL, ml.ANIME):
    bd.register_builder(_kind.effect_type, _kind.stored_params, _kind.live, build, update)
