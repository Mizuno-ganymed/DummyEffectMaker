# -*- coding: utf-8 -*-
"""落ち葉 (Blender 版): 舞い散る落ち葉と、地面のカルマン渦に巻かれる落ち葉。

Maya 版 (tools/leaves.py) と同じパラメータ・同じ計算 (motion.py の leaf_*)。
    dfx_leavesN              ルート (パラメータ。風を dfx_wind で指す)
      dfx_leavesN_area       範囲の箱
      dfx_leavesN_leaves1    葉 (色ごとに 1 つのメッシュ。_leaves2 / _leaves3。毎フレーム頂点を置き直す)
    dfx_leafVortexN          ルート
      dfx_leafVortexN_emitter  渦の出る所 (矢印の Empty。ローカル X が下流、Z が上)
"""
from __future__ import absolute_import

from ... import motion as mo
from ...core import BaseTool
from ...tools import leaves as ml
from .. import runtime as rt
from . import _build as bd, _wx as wx
from . import weather as bw


def _tool(maya_tool):
    tip = maya_tool.tooltip.replace(u"ロケータ", u"Empty").replace(u"ロケータの X", u"Empty の X")
    return dict(label=maya_tool.label, tab=wx.TAB, category=ml.CATEGORY, order=maya_tool.order,
                hosts=("blender",), params=maya_tool.params,
                tooltip=tip + u"\n葉の数を変えたら「エフェクトを更新」を押します。")


def _template():
    pts, faces = ml.leaf_points()
    return pts, [tuple(f) for f in faces], None


def build(root, v):
    for g, n in enumerate(ml.color_counts(v["count"])):
        mat = rt.flat_material("%s_mat%d" % (root.name, g + 1), v[ml.COLOR_PARAMS[g]], v["opacity"], owner=root)
        obj = wx.particle_object(root, "%s_leaves%d" % (root.name, g + 1), _template(), n, mat)
        obj.data["dfx_color_index"] = g


def _groups(root):
    out = []
    for g in range(mo.LEAF_COLORS):
        obj = wx.part(root, "_leaves%d" % (g + 1))
        if obj is not None:
            out.append((g, obj))
    return out


def _write(root, v, place):
    """色ごとの葉を置く。place(j) → (位置, 軸, 大きさ, 表示) (Y が上のワールド)。"""
    for g, obj in _groups(root):
        rt.set_material(wx.material_of(obj), v[ml.COLOR_PARAMS[g]], v["opacity"])
        out = []
        for idx in range(obj.data["dfx_count"]):
            p, axes, sz, vis = place(idx * mo.LEAF_COLORS + g)
            out.append((p, axes, (sz,) * 3) if vis else wx.hidden(p))
        wx.write_particles(obj, out)


def update_fall(root, frame):
    v = rt.read_params(root, ml.FALL.stored_params())
    am, ai = wx.area_yup(root)
    if am is None:
        return
    wind = wx.wind_of(root)
    _write(root, v, lambda j: mo.leaf_fall(j, frame, v, am, ai, wind))


def update_vortex(root, frame):
    v = rt.read_params(root, ml.VORTEX.stored_params())
    m = wx.emitter_at(root)(frame)

    def place(j):
        p, axes, sz, vis = mo.leaf_vortex(j, frame, v)
        p, axes = mo.leaf_place(m, p, axes)
        return p, axes, sz, vis
    _write(root, v, place)


class LeafFallTool(BaseTool):
    locals().update(_tool(ml.LeafFallTool))

    def run(self, ctx, **v):
        return bw.create(ctx, ml.FALL, v, "dfx_leaves", area=(v["area_width"], v["area_height"]))


class LeafVortexTool(BaseTool):
    locals().update(_tool(ml.LeafVortexTool))

    def run(self, ctx, **v):
        import bpy
        name = bw.create(ctx, ml.VORTEX, v, "dfx_leafVortex", emitter=True)
        em = rt.control(bpy.data.objects[name], "emitter")
        if em is not None:   # 下流 (ローカル X) が分かるように、3 軸の矢印にする
            em.empty_display_type = "ARROWS"
        return name


bw.BUILDERS_BY_TYPE[ml.FALL.effect_type] = build
bw.BUILDERS_BY_TYPE[ml.VORTEX.effect_type] = build
bw._register(ml.FALL, build, update_fall)                                         # 風を受ける
bd.register_builder(ml.VORTEX.effect_type, ml.VORTEX.stored_params, ml.VORTEX.live, build, update_vortex)
