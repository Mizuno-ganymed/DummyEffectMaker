# -*- coding: utf-8 -*-
"""細い煙 (Blender 版): 出る場所から、たゆたう煙の筋が上がる (たばこ・線香の煙)。

Maya 版 (tools/wisp.py) と同じパラメータ・同じ計算 (motion.wisp_rows)。
    dfx_wispN            ルート (パラメータ。カメラ・風とつながる)
      dfx_wispN_emitter  出る場所 (矢印の Empty。オブジェクトを選んで作ると、その子になって付いて動く)
      dfx_wispN_strands  筋の帯 (全部で 1 つのメッシュ。点ごとに 2 頂点を毎フレーム置き直す。UV は根元 → 先)
"""
from __future__ import absolute_import

from ... import motion as mo
from ...core import BaseTool
from ...tools import wisp as mw
from .. import runtime as rt
from . import _wx as wx
from . import weather as bw


class WispTool(BaseTool):
    locals().update(bw._tool(mw.WispTool, mw.WispTool.order))
    tooltip = (u"出る場所から、細い煙の筋がゆらゆら上がります (たばこ・線香・ろうそくの煙など)。\n"
               u"根元はまっすぐ、上ほど大きく揺れて巻き、広がって消えます。風にも流されます。\n"
               u"オブジェクトを選んで作ると、出る場所がそれに付いて動きます。")

    def run(self, ctx, **v):
        return bw.create(ctx, mw.KIND, v, "dfx_wisp", emitter=True)


def build(root, v):
    count, points = int(v["count"]), int(v["points"])
    faces = []
    for s in range(count):
        for j in range(points - 1):
            k = s * points + j
            faces.append((2 * k, 2 * k + 1, 2 * k + 3, 2 * k + 2))
    mat = wx.texture_material(root.name + "_mat", wx.resolve_texture(v, mw.texture_name(v["style"])),
                              v["color"], v["opacity"], root)
    obj = wx.particle_object(root, root.name + "_strands", ([(0.0, 0.5, 0.0), (0.0, -0.5, 0.0)], [], None),
                             count * points, mat, faces=faces)
    mesh = obj.data
    layer = mesh.uv_layers.new()
    for loop in mesh.loops:   # u = 根元 0 → 先 1、v = 帯の幅 (Maya 版の strip_points と同じ)
        vi = loop.vertex_index
        j = (vi // 2) % points
        layer.data[loop.index].uv = (j / float(max(points - 1, 1)), 1.0 if vi % 2 == 0 else 0.0)
    mesh["dfx_strands"] = [count, points]


def update(root, frame):
    v = rt.read_params(root, mw.KIND.stored_params())
    obj = wx.part(root, "_strands")
    if obj is None:
        return
    count, points = list(obj.data["dfx_strands"])
    wx.set_texture_material(wx.material_of(obj), v["color"], v["opacity"])
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    at = wx.emitter_at(root)
    out = []
    for s in range(count):
        for p, axes, w in mo.wisp_rows(s, frame, v, points, at, wind, cp, cdir):
            out.append((p, axes, (w,) * 3) if w > 1e-9 else wx.hidden(p))
    wx.write_particles(obj, out)


bw.BUILDERS_BY_TYPE[mw.EFFECT_TYPE] = build
bw._register(mw.KIND, build, update)
