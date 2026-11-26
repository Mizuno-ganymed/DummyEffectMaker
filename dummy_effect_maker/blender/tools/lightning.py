# -*- coding: utf-8 -*-
"""稲妻 (Blender 版): 雲の点 (_top) から落ちる所 (_ground) へ、何回か落ちる。

Maya 版 (tools/lightning.py) と同じパラメータ・同じ計算 (motion.py の lightning_*)。
    dfx_lightningN          ルート (パラメータ。dfx_camera = 帯が向くカメラ)
      dfx_lightningN_top    雲の点 (動かしてよい)
      dfx_lightningN_ground 落ちる所 (動かしてよい)
      dfx_lightningN_bolt   帯 (本筋 → 枝の順の点ごとに 2 頂点。毎フレーム置き直す)。
                            アニメは円柱 (点ごとに輪。どこから見ても太さがある)
      dfx_lightningN_glow   2 本目の帯 (実写: 外側の光 / アニメ: 円柱の表面のすぐ前の芯)
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
                tooltip=maya_tool.tooltip + u"\n点の数・枝の数 (実写は光の広がり、アニメは円柱の分割数) を変えたら"
                                            u"「エフェクトを更新」を押します。")


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
    style = ml.STYLES[root.get(rt.TYPE_KEY)]
    strands = [mo.strand_rows(style, n)] + [mo.strand_rows(style, m)] * nb   # 帯の行の数 (アニメは細かく分ける)
    for obj in strand_objects(root, v, style, strands):
        obj.data["dfx_strands"] = [n, nb, m]


def strand_objects(root, v, style, strands):
    """点の列 (strands = 各列の行の数) の帯 _bolt / _glow (電撃も使う)。"""
    faces = []
    k = 0
    for count in strands:
        for j in range(count - 1):
            faces.append((2 * (k + j), 2 * (k + j) + 1, 2 * (k + j) + 3, 2 * (k + j) + 2))
        k += count
    total = sum(strands)
    half, z = ml.second_ribbon(style, v["glow_width"])
    out = []
    if style == mo.STYLE_ANIME:
        # 円柱 (点ごとに輪。どこから見ても太さがある) + 円柱の表面のすぐ前に、カメラを向いた芯の帯
        sides = int(v["sides"])
        bolt = ((ml.tube_ring(sides), [], None), [tuple(f) for f in ml.tube_faces(strands, sides)], True)
    else:
        bolt = (([(0.0, 0.5, 0.0), (0.0, -0.5, 0.0)], [], None), faces, False)
    for name, color, (template, fcs, smooth) in (
            ("_bolt", v["color"], bolt),
            ("_glow", v["glow_color"], (([(0.0, half, z), (0.0, -half, z)], [], None), faces, False))):
        mat = rt.flat_material(root.name + name + "_mat", color, v["opacity"], owner=root)
        out.append(wx.particle_object(root, root.name + name, template, total, mat, faces=fcs, smooth=smooth))
    return out


KINDS = dict((k.effect_type, k) for k in (ml.REAL, ml.ANIME, ml.DISCHARGE_REAL, ml.DISCHARGE_ANIME))
ENDS = {False: ("top", "ground"), True: ("start", "end")}   # 稲妻 / 放電 のコントロールの名前


def update(root, frame):
    effect_type = root.get(rt.TYPE_KEY)
    kind = KINDS[effect_type]
    discharge = effect_type in ml.DISCHARGE_TYPES
    v = rt.read_params(root, kind.stored_params())
    top, gnd = [rt.control(root, r) for r in ENDS[discharge]]
    bolt, glow = wx.part(root, "_bolt"), wx.part(root, "_glow")
    if top is None or gnd is None or bolt is None:
        return
    n, nb, m = list(bolt.data["dfx_strands"])
    cp, cdir = wx.camera_yup(root)
    fn = mo.discharge_rows if discharge else mo.lightning_rows
    rows, alpha = fn(ml.STYLES[effect_type], frame, v, mo.yup_vector(tuple(top.matrix_world.translation)),
                     mo.yup_vector(tuple(gnd.matrix_world.translation)), cp, cdir, n, nb, m)
    if discharge:
        from . import explosion as bex
        bex.update_parts(root, v, bex.yup_hits(root), frame)
    placements = [(p, axes, (w,) * 3) if w > 1e-9 else wx.hidden(p) for p, axes, w in rows]
    rt.set_material(wx.material_of(bolt), v["color"], alpha)
    wx.write_particles(bolt, placements)
    if glow is not None:
        rt.set_material(wx.material_of(glow), v["glow_color"], alpha * v["glow_opacity"])
        wx.write_particles(glow, placements)


for _kind in (ml.REAL, ml.ANIME):
    bd.register_builder(_kind.effect_type, _kind.stored_params, _kind.live, build, update)


# ---------------------------------------------------------------- 放電
def _discharge_tool(maya_tool):
    d = _tool(maya_tool)
    d["tooltip"] = (maya_tool.tooltip.replace(u"ロケータ", u"Empty").replace(u"「天候を更新」", u"「エフェクトを更新」"))
    return d


class DischargeRealTool(BaseTool):
    locals().update(_discharge_tool(ml.DischargeRealTool))

    def run(self, ctx, **v):
        return create_discharge(ctx, ml.DISCHARGE_REAL, v)


class DischargeAnimeTool(BaseTool):
    locals().update(_discharge_tool(ml.DischargeAnimeTool))

    def run(self, ctx, **v):
        return create_discharge(ctx, ml.DISCHARGE_ANIME, v)


def create_discharge(ctx, kind, v):
    from . import explosion as bex
    root, cam, target = wx.create_root(bpy.context, ctx, kind.effect_type, kind.root_name.replace("#", ""),
                                       kind.stored_params(), kind.live, v, need_camera=True, need_wind=False)
    base = target.matrix_world.translation.copy() if target is not None else None
    for role, pos, display in (("start", (0.0, 0.0, 8.0), "CONE"), ("end", (6.0, 0.0, 0.0), "SPHERE")):
        c = rt.add_control(root, role, pos, display=display, size=0.4)
        if base is not None:
            off = (-6.0, 0.0, 8.0) if role == "start" else (0.0, 0.0, 0.0)
            c.location = (base.x + off[0], base.y + off[1], base.z + off[2])
    build_discharge(root, v)
    rt.update_root(root)
    wx.select_only(root)
    hits = list(root.get("dfxHits", []))
    if not bex.colliders():
        ctx.log(u"  コリジョンが無いので爆発はしません (ぶつける相手を「コリジョンに指定」して「エフェクトを更新」)")
    else:
        ctx.log(u"  終端の側がコリジョンに %d 回当たります" % (len(hits) // 8))
    return root.name


def discharge_probes(root, v):
    """当たりを調べる線分 [(フレーム, 始まり, 終わり)] (Blender のワールド。motion.discharge_probe と同じ)。"""
    start, end = rt.control(root, "start"), rt.control(root, "end")
    out = []
    for t in mo.discharge_samples(v):
        a = tuple(rt.matrix_world_at(start, t).translation)
        b = tuple(rt.matrix_world_at(end, t).translation)
        p0, p1 = mo.discharge_probe(a, b, v["reach"])
        out.append((t, p0, p1))
    return out


def build_discharge(root, v):
    from . import explosion as bex
    build(root, v)
    cols = bex.colliders()
    hits = bex.probe_hits(cols, discharge_probes(root, v)) if cols else []
    bex.store_hits(root, hits)
    bex.build_parts(root, v, len(hits))


for _kind in (ml.DISCHARGE_REAL, ml.DISCHARGE_ANIME):
    bd.register_builder(_kind.effect_type, _kind.stored_params, _kind.live, build_discharge, update)
