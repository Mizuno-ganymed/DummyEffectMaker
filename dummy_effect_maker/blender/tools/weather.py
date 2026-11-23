# -*- coding: utf-8 -*-
"""天候の粒 (Blender 版): 雨・雪・土煙・火花。

Maya 版 (tools/weather.py) と同じパラメータ・同じ計算 (motion.py)。
    dfx_rainN              ルート (パラメータ。カメラ・風を dfx_camera / dfx_wind で指す)
      dfx_rainN_area       範囲の箱 (動かす・回す・拡大縮小してよい)
      dfx_rainN_drops      雨の線 (全部で 1 つのメッシュ。毎フレーム頂点を置き直す)
      dfx_rainN_splashes   はね
土煙・火花は範囲の箱の代わりに出る場所 (_emitter。矢印の向きが上) から出る。
"""
from __future__ import absolute_import

from ... import motion as mo
from ...core import BaseTool
from ...tools import weather as mw
from .. import runtime as rt
from . import _build as bd
from . import _wx as wx

WEATHER_TYPES = set()   # 風を受ける種類 (風を作った時に、風のない物をつなぐ)


def _register(kind, build, update):
    WEATHER_TYPES.add(kind.effect_type)
    bd.register_builder(kind.effect_type, kind.stored_params, kind.live, build, update)


def create(ctx, kind, v, base, area=None, emitter=False):
    """天候を作る。area = (幅, 高さ) なら範囲の箱、emitter なら出る場所を作る。"""
    import bpy
    root, cam, target = wx.create_root(bpy.context, ctx, kind.effect_type, base,
                                       kind.stored_params(), kind.live, v,
                                       need_camera=kind.need_camera, need_wind=kind.need_wind)
    if area is not None:
        wx.add_area(root, area[0], area[1], v.get("follow_camera"), cam)
    if emitter:
        wx.add_emitter(root, target)
        if target is not None:
            ctx.log(u"  出る場所は %s に付いて動きます" % target.name)
    BUILDERS_BY_TYPE[kind.effect_type](root, v)
    rt.update_root(root)
    wx.select_only(root)
    return root.name


BUILDERS_BY_TYPE = {}


def _tool(maya_tool, order):
    """Maya 版のツールと同じ見た目・パラメータの Blender 版のツールの土台。"""
    return dict(label=maya_tool.label, tab=wx.TAB, category=maya_tool.category, order=order,
                hosts=("blender",), tooltip=maya_tool.tooltip, params=maya_tool.params)


# ---------------------------------------------------------------- 雨
def build_rain(root, v):
    count = int(v["count"])
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    wx.particle_object(root, root.name + "_drops", wx.card_template(-1.0, 0.0), count, mat)
    wx.particle_object(root, root.name + "_splashes", wx.disc_template(16, 0.5, 0.38, flat_y=True),
                       count, mat)


def update_rain(root, frame):
    v = rt.read_params(root, mw.RAIN.stored_params())
    am, ai = wx.area_yup(root)
    drops, splashes = wx.part(root, "_drops"), wx.part(root, "_splashes")
    if am is None or drops is None or splashes is None:
        return
    rt.set_material(wx.material_of(drops), v["color"], v["opacity"])
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    out_d, out_s = [], []
    for i in range(drops.data["dfx_count"]):
        st = mo.falling(i, frame, v, am, ai, wind)
        if st["vis"]:
            out_d.append((st["p"], mo.streak(st["p"], st["vel"], cp, cdir), (v["width"], v["length"], 1.0)))
        else:
            out_d.append(wx.hidden(st["p"]))
        p, size, vis = mo.splash(i, frame, v, am, ai, wind)
        out_s.append((p, wx.IDENTITY_AXES, (size,) * 3) if vis else wx.hidden(p))
    wx.write_particles(drops, out_d)
    wx.write_particles(splashes, out_s)


class RainTool(BaseTool):
    locals().update(_tool(mw.RainTool, 872))

    def run(self, ctx, **v):
        return create(ctx, mw.RAIN, v, "dfx_rain", area=(v["area_width"], v["area_height"]))


# ---------------------------------------------------------------- 雪
def build_snow(root, v):
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    wx.particle_object(root, root.name + "_flakes", wx.disc_template(10, 0.5), int(v["count"]), mat)


def update_snow(root, frame):
    v = rt.read_params(root, mw.SNOW.stored_params())
    am, ai = wx.area_yup(root)
    flakes = wx.part(root, "_flakes")
    if am is None or flakes is None:
        return
    rt.set_material(wx.material_of(flakes), v["color"], v["opacity"])
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    out = []
    for i in range(flakes.data["dfx_count"]):
        st = mo.falling(i, frame, v, am, ai, wind, sway=True)
        sz = mo.snow_size(i, v)
        out.append((st["p"], mo.billboard(st["p"], cp, cdir), (sz,) * 3) if st["vis"] else wx.hidden(st["p"]))
    wx.write_particles(flakes, out)


class SnowTool(BaseTool):
    locals().update(_tool(mw.SnowTool, 874))

    def run(self, ctx, **v):
        return create(ctx, mw.SNOW, v, "dfx_snow", area=(v["area_width"], v["area_height"]))


# ---------------------------------------------------------------- 土煙
def build_dust(root, v):
    mat = wx.texture_material(root.name + "_mat", wx.resolve_texture(v, "puff"), v["color"],
                              v["opacity"], root)
    wx.particle_object(root, root.name + "_puffs", wx.card_template(), int(v["count"]), mat)


def build_dust_mesh(root, v):
    mat = wx.toon_material(root.name + "_mat", v["color"], v["shade_color"], v["opacity"], root)
    wx.particle_object(root, root.name + "_puffs", wx.puff_template(), int(v["count"]), mat, smooth=True)


def _update_dust(root, frame, kind, mesh):
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
    count = puffs.data["dfx_count"]
    out = []
    for i in range(count):
        p, sz, vis, a = mo.dust(i, frame, v, count, at, wind)
        if not vis or sz <= 1e-9:
            out.append(wx.hidden(p))
        elif mesh:
            out.append((p, wx.euler_axes(*mo.dust_euler(i, v, a)), (sz,) * 3))
        else:
            out.append((p, mo.billboard(p, cp, cdir, mo.dust_roll(i, v, a)), (sz,) * 3))
    wx.write_particles(puffs, out)


def update_dust(root, frame):
    _update_dust(root, frame, mw.DUST, False)


def update_dust_mesh(root, frame):
    _update_dust(root, frame, mw.DUST_MESH, True)


class DustTool(BaseTool):
    locals().update(_tool(mw.DustTool, 876))

    def run(self, ctx, **v):
        return create(ctx, mw.DUST, v, "dfx_dust", emitter=True)


class DustMeshTool(BaseTool):
    locals().update(_tool(mw.DustMeshTool, 877))

    def run(self, ctx, **v):
        return create(ctx, mw.DUST_MESH, v, "dfx_dustMesh", emitter=True)


# ---------------------------------------------------------------- 火花
def build_sparks(root, v):
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    wx.particle_object(root, root.name + "_sparks", wx.card_template(-1.0, 0.0), int(v["count"]), mat)


def update_sparks(root, frame):
    v = rt.read_params(root, mw.SPARKS.stored_params())
    sparks = wx.part(root, "_sparks")
    if sparks is None:
        return
    rt.set_material(wx.material_of(sparks), v["color"], v["opacity"])
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    at = wx.emitter_at(root)
    count = sparks.data["dfx_count"]
    out = []
    for i in range(count):
        p, vel, vis, fade, _ = mo.spark(i, frame, v, count, at, wind)
        w = v["width"] * fade
        if not vis or w <= 1e-9:
            out.append(wx.hidden(p))
        else:
            out.append((p, mo.streak(p, vel, cp, cdir), (w, max(mo.mag(vel) * v["length"], w), 1.0)))
    wx.write_particles(sparks, out)


def build_spark_trail(root, v):
    """火花ごとに (分割数 + 1) 行の帯。行ごとに 2 頂点 (ローカル Y の ±0.5)。"""
    count, rows = int(v["count"]), int(v["segments"]) + 1
    faces = []
    for s in range(count):
        for j in range(rows - 1):
            k = s * rows + j
            faces.append((2 * k, 2 * k + 1, 2 * k + 3, 2 * k + 2))
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    obj = wx.particle_object(root, root.name + "_trails", ([(0.0, 0.5, 0.0), (0.0, -0.5, 0.0)], [], None),
                             count * rows, mat, faces=faces)
    obj.data["dfx_rows"] = rows


def update_spark_trail(root, frame):
    v = rt.read_params(root, mw.SPARK_TRAIL.stored_params())
    trails = wx.part(root, "_trails")
    if trails is None:
        return
    rt.set_material(wx.material_of(trails), v["color"], v["opacity"])
    rows = trails.data["dfx_rows"]
    count = trails.data["dfx_count"] // rows
    v = dict(v, segments=rows - 1)
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    at = wx.emitter_at(root)
    out = []
    for i in range(count):
        for p, axes, w in mo.spark_trail_rows(i, frame, v, count, at, wind, cp, cdir):
            out.append((p, axes, (w,) * 3) if w > 1e-9 else wx.hidden(p))
    wx.write_particles(trails, out)


class SparksTool(BaseTool):
    locals().update(_tool(mw.SparksTool, 878))

    def run(self, ctx, **v):
        return create(ctx, mw.SPARKS, v, "dfx_sparks", emitter=True)


class SparkTrailTool(BaseTool):
    locals().update(_tool(mw.SparkTrailTool, 879))

    def run(self, ctx, **v):
        return create(ctx, mw.SPARK_TRAIL, v, "dfx_sparkTrail", emitter=True)


for _kind, _build_fn, _update_fn in ((mw.RAIN, build_rain, update_rain),
                                     (mw.SNOW, build_snow, update_snow),
                                     (mw.DUST, build_dust, update_dust),
                                     (mw.DUST_MESH, build_dust_mesh, update_dust_mesh),
                                     (mw.SPARKS, build_sparks, update_sparks),
                                     (mw.SPARK_TRAIL, build_spark_trail, update_spark_trail)):
    BUILDERS_BY_TYPE[_kind.effect_type] = _build_fn
    _register(_kind, _build_fn, _update_fn)
