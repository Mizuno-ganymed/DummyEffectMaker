# -*- coding: utf-8 -*-
"""足元の砂煙 (Blender 版)。

Maya 版 (tools/foot_dust.py) と同じパラメータ・同じ計算 (motion.py の foot_dust)。
    dfx_footDustN            ルート (パラメータ。カメラ・風を dfx_camera / dfx_wind で指す)
      dfx_footDustN_emitter  出る場所 (選んだオブジェクトに付いて動く。高さは「地面の高さ」で決める)
      dfx_footDustN_puffs    砂煙の塊 (全部で 1 つのメッシュ。毎フレーム頂点を置き直す)
"""
from __future__ import absolute_import

from ... import motion as mo
from ...core import BaseTool
from ...tools import foot_dust as mf
from .. import runtime as rt
from . import _wx as wx
from . import weather as bw


def _tool(maya_tool):
    return dict(label=maya_tool.label, tab=wx.TAB, category=mf.CATEGORY, order=maya_tool.order,
                hosts=("blender",), params=maya_tool.params,
                tooltip=maya_tool.tooltip.replace(u"ロケータ", u"Empty")
                + u"\n塊の数を変えたら「エフェクトを更新」を押します。")


def build_sprite(root, v):
    mat = wx.texture_material(root.name + "_mat", wx.resolve_texture(v, "puff"), v["color"],
                              v["opacity"], root)
    wx.particle_object(root, root.name + "_puffs", wx.card_template(), int(v["count"]), mat)


def build_mesh(root, v):
    mat = wx.toon_material(root.name + "_mat", v["color"], v["shade_color"], v["opacity"], root)
    wx.particle_object(root, root.name + "_puffs", wx.puff_template(), int(v["count"]), mat, smooth=True)


def _update(root, frame, kind, mesh):
    v = rt.read_params(root, kind.stored_params())
    puffs = wx.part(root, "_puffs")
    if puffs is None:
        return
    mat = wx.material_of(puffs)
    if mesh:
        wx.set_toon_material(mat, v["color"], v["shade_color"], v["opacity"])
    else:
        wx.set_texture_material(mat, v["color"], v["opacity"])
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    at = wx.emitter_at(root)
    out = []
    for i in range(puffs.data["dfx_count"]):
        p, sz, vis, a, cyc = mo.foot_dust(i, frame, v, at, wind)
        if not vis:
            out.append(wx.hidden(p))
        elif mesh:
            out.append((p, wx.euler_axes(*mo.foot_dust_euler(i, v, a, cyc)), (sz,) * 3))
        else:
            out.append((p, mo.billboard(p, cp, cdir, mo.foot_dust_roll(i, v, a, cyc)), (sz,) * 3))
    wx.write_particles(puffs, out)


def update_sprite(root, frame):
    _update(root, frame, mf.SPRITE, False)


def update_mesh(root, frame):
    _update(root, frame, mf.MESH, True)


class FootDustTool(BaseTool):
    locals().update(_tool(mf.FootDustTool))

    def run(self, ctx, **v):
        return bw.create(ctx, mf.SPRITE, v, "dfx_footDust", emitter=True)


class FootDustMeshTool(BaseTool):
    locals().update(_tool(mf.FootDustMeshTool))

    def run(self, ctx, **v):
        return bw.create(ctx, mf.MESH, v, "dfx_footDustMesh", emitter=True)


for _kind, _build_fn, _update_fn in ((mf.SPRITE, build_sprite, update_sprite),
                                     (mf.MESH, build_mesh, update_mesh)):
    bw.BUILDERS_BY_TYPE[_kind.effect_type] = _build_fn
    bw._register(_kind, _build_fn, _update_fn)   # 風を受ける
