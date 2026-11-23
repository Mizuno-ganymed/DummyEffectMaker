# -*- coding: utf-8 -*-
"""核爆発 (Blender 版): 弾が無くても、置いた所で起きる大きな爆発 (キノコ雲)。

Maya 版 (tools/nuke.py) と同じパラメータ・同じ計算 (motion.py の nuke_*)。
    dfx_nukeN              ルート (パラメータ。dfx_camera = 閃光が向くカメラ)
      dfx_nukeN_center     爆心 (動かす・回してよい。ローカル Z (上) の向きに雲が上がる)
      dfx_nukeN_flash      閃光 / _shock 衝撃波の輪 / _dome ドーム / _fireball 火の玉
      dfx_nukeN_cloud      キノコ雲の柱・かさ・根元の土煙 (全部で 1 つのメッシュ)
"""
from __future__ import absolute_import

import bpy

from ... import motion as mo
from ...core import BaseTool
from ...tools import nuke as mn
from ...tools.ghost_blur import rotation_rows
from .. import runtime as rt
from . import _build as bd, _wx as wx

PARTS = ("stem", "cap", "base")


class NukeTool(BaseTool):
    label = mn.NukeTool.label
    tab = mn.TAB
    category = mn.CATEGORY
    order = 920
    hosts = ("blender",)
    tooltip = (u"弾が無くても、爆心の Empty の所で起きる大きな爆発 (キノコ雲) を作ります。\n"
               u"閃光・衝撃波の輪とドーム・火の玉・キノコ雲 (柱とかさ)・根元の土煙が順に出ます。\n"
               u"オブジェクトを選んでいればその位置に置きます。雲は Empty の上 (ローカル Z) へ上がります。\n"
               u"煙の数を変えたら「エフェクトを更新」を押します。")
    params = mn.NukeTool.params

    def run(self, ctx, **v):
        kind = mn.KIND
        root, cam, target = wx.create_root(bpy.context, ctx, kind.effect_type, "dfx_nuke",
                                           kind.stored_params(), kind.live, v, need_camera=True,
                                           need_wind=False)
        center = rt.add_control(root, "center", (0.0, 0.0, 0.0), display="ARROWS",
                                size=max(v["size"] * 0.3, 0.1))
        if target is not None:
            center.location = target.matrix_world.translation
            ctx.log(u"  爆心を %s の位置に置きました" % target.name)
        build(root, v)
        rt.update_root(root)
        wx.select_only(root)
        return root.name


def build(root, v):
    flash = rt.flat_material(root.name + "_flash_mat", v["flash_color"], v["opacity"], owner=root)
    shock = rt.flat_material(root.name + "_shock_mat", v["shock_color"], v["opacity"], owner=root)
    dome = rt.flat_material(root.name + "_dome_mat", v["shock_color"], v["dome_opacity"], owner=root)
    fire = wx.toon_material(root.name + "_fire_mat", v["fire_color"], v["fire_shade"], v["opacity"], root)
    cloud = wx.toon_material(root.name + "_cloud_mat", v["fire_color"], v["fire_shade"], v["opacity"], root)
    hpts, hfaces = mn.hemisphere_points()
    wx.particle_object(root, root.name + "_flash", wx.disc_template(24, 0.5), 1, flash)
    wx.particle_object(root, root.name + "_shock", wx.disc_template(48, 0.5, 0.44, flat_y=True), 1, shock)
    wx.particle_object(root, root.name + "_dome", (hpts, hfaces, None), 1, dome, smooth=True)
    wx.particle_object(root, root.name + "_fireball", wx.puff_template(), 1, fire, smooth=True)
    counts = [int(v[p + "_count"]) for p in PARTS]
    obj = wx.particle_object(root, root.name + "_cloud", wx.puff_template(), max(sum(counts), 1), cloud,
                             smooth=True)
    obj.data["dfx_counts"] = counts


def center_yup(root):
    """爆心の (位置, 軸 (X, 上, Z)) (Y が上)。"""
    center = rt.control(root, "center")
    m = wx.yup(center.matrix_world)
    axes = tuple(mo.unit((m[4 * r], m[4 * r + 1], m[4 * r + 2])) for r in range(3))
    return (m[12], m[13], m[14]), axes


def placements(root, v, frame):
    """部品ごとの置き方 {名前: [(位置, 軸, 大きさ)]} (Y が上)。"""
    p0, axes = center_yup(root)
    cp, cdir = wx.camera_yup(root)
    out = {}
    p, sz, vis = mo.nuke_flash(frame, v, p0, axes)
    out["flash"] = [(p, mo.billboard(p, cp, cdir), (sz,) * 3) if vis else wx.hidden(p)]
    p, rows, sz, vis, _ = mo.nuke_shock(frame, v, p0, axes)
    out["shock"] = [(p, rows, (sz,) * 3) if vis else wx.hidden(p)]
    p, rows, sz, vis, _ = mo.nuke_dome(frame, v, p0, axes)
    out["dome"] = [(p, rows, (sz,) * 3) if vis else wx.hidden(p)]
    p, sz, vis, rot = mo.nuke_fireball(frame, v, p0, axes)
    out["fireball"] = [(p, rotation_rows(*rot), (sz,) * 3) if vis else wx.hidden(p)]
    return out, p0, axes


def cloud_placements(counts, v, frame, p0, axes):
    out = []
    for part, fn, count in zip(PARTS, (mo.nuke_stem, mo.nuke_cap, mo.nuke_base), counts):
        for j in range(count):
            p, sz, vis, rot = fn(j, count, frame, v, p0, axes)
            out.append((p, rotation_rows(*rot), (sz,) * 3) if vis else wx.hidden(p))
    return out


def update(root, frame):
    v = rt.read_params(root, mn.KIND.stored_params())
    if rt.control(root, "center") is None:
        return
    singles, p0, axes = placements(root, v, frame)
    for name, pl in singles.items():
        obj = wx.part(root, "_" + name)
        if obj is not None:
            wx.write_particles(obj, pl)
    cloud = wx.part(root, "_cloud")
    if cloud is not None:
        counts = list(cloud.data["dfx_counts"])
        pl = cloud_placements(counts, v, frame, p0, axes)
        if not pl:
            pl = [wx.hidden(p0)]
        wx.write_particles(cloud, pl)
        color, shade, alpha = mo.nuke_colors(frame, v)
        wx.set_toon_material(wx.material_of(cloud), color, shade, alpha)
    _, _, _, _, shock_alpha = mo.nuke_shock(frame, v, p0, axes)
    _, _, _, _, dome_alpha = mo.nuke_dome(frame, v, p0, axes)
    for name, color, alpha in (("flash", v["flash_color"], v["opacity"]), ("shock", v["shock_color"], shock_alpha),
                               ("dome", v["shock_color"], dome_alpha)):
        obj = wx.part(root, "_" + name)
        if obj is not None:
            rt.set_material(wx.material_of(obj), color, alpha)
    fire = wx.part(root, "_fireball")
    if fire is not None:
        wx.set_toon_material(wx.material_of(fire), v["fire_color"], v["fire_shade"], v["opacity"])


bd.register_builder(mn.EFFECT_TYPE, mn.KIND.stored_params, mn.KIND.live, build, update)
