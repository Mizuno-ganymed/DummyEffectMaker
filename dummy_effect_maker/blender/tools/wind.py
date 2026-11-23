# -*- coding: utf-8 -*-
"""風 (Blender 版): 天候をまとめて流す、風向き・強さ・突風のコントローラ。

Maya 版 (tools/wind.py) と同じパラメータ・同じ計算 (wind_vector / gust_factor / integrate)。
    dfx_windN         ルート (動かす・回してよい。ローカル +X が風向き。パラメータを持つ)
      dfx_windN_arrow 矢印の形

風に流された距離の表 (dfxDrift* / dfxWind*) はルートのカスタムプロパティ (Blender のワールド = Z が上)。
Maya 版は「風を更新」で表を作り直すが、Blender 版は矢印・強さ・キーを変えると自動で作り直す。
"""
from __future__ import absolute_import

import bpy

from ... import motion as mo
from ...core import BaseTool, ToolError
from ...tools import _weather as mx, wind as mw
from .. import runtime as rt
from . import _build as bd
from . import _wx as wx

ARROW = [(0, 0, 0), (2.4, 0, 0), (2.4, 0.35, 0), (3.2, 0, 0), (2.4, -0.35, 0), (2.4, 0, 0)]
STORED = mw.KIND.stored_params
LIVE = mw.LIVE_PARAMS + mw.REBUILD_PARAMS   # Blender 版は全部すぐ反映


class WindTool(BaseTool):
    label = mw.WindTool.label
    tab = wx.TAB
    category = mw.CATEGORY
    order = 870
    hosts = ("blender",)
    tooltip = (u"風のコントローラ (矢印) を作ります。矢印の向き (+X) が風向きで、回転させて変えます。\n"
               u"シーンの天候 (雨・雪・土煙・火花・霧・地霧) がこの風で流れます。\n"
               u"強さ・突風にはキーを打てます (変えるとすぐ反映されます)。")
    params = mw.WindTool.params

    def run(self, ctx, **v):
        root = rt.new_root(mx.WIND, "dfx_wind")
        root.lock_location = (False, False, False)
        root.lock_rotation = (False, False, False)
        root.empty_display_type = "PLAIN_AXES"
        root.empty_display_size = 0.3
        root.location = (0.0, 0.0, 3.0)
        rt.add_params(root, STORED(), v, LIVE)
        build(root, v)
        rt.update_root(root)
        linked = link_unlinked(root)
        ctx.log(u"  %s: 風" % root.name)
        if linked:
            ctx.log(u"  風のない天候 %d 個をこの風につなぎました" % linked)
        wx.select_only(root)
        return root.name


class WindUpdateTool(BaseTool):
    label = mw.WindUpdateTool.label
    tab = wx.TAB
    category = mw.CATEGORY
    order = 871
    hosts = ("blender",)
    tooltip = (u"選択した風 (何も選ばなければシーンの全部の風) を、今のパラメータと\n"
               u"シーンのフレーム範囲で計算し直します (ふつうは自動で計算し直されます)。")

    def run(self, ctx, **v):
        roots = rt.selected_roots(bpy.context, (mx.WIND,)) or wx.wind_roots()
        if not roots:
            raise ToolError(u"シーンに風がありません (「風」で作ります)")
        for root in roots:
            root["dfx_wind_signature"] = ""
            rt.update_root(root)
            ctx.log(u"  %s を計算し直しました" % root.name)
        return [r.name for r in roots]


def build(root, v):
    curve = bpy.data.curves.new(root.name + "_arrowShape", "CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(len(ARROW) - 1)
    for p, co in zip(spline.points, ARROW):
        p.co = (co[0], co[1], co[2], 1.0)
    curve.bevel_depth = 0.03
    rt.own(root, curve)
    obj = bpy.data.objects.new(root.name + "_arrow", curve)
    rt.collection().objects.link(obj)
    obj.parent = root
    obj.hide_select = True
    obj.hide_render = True
    rt.own(root, obj)


def _signature(root, start, end):
    """表を作り直すかどうかの目印 (パラメータ・置き方・キー・範囲)。"""
    parts = [start, end, tuple(round(x, 6) for x in wx.rows(root.matrix_basis))]
    parts.append(tuple((p.name, root.get(rt.PARAM_PREFIX + p.name)) for p in STORED()))
    obj = root
    while obj is not None:
        for fc in rt._fcurves(obj):
            parts.append((obj.name, fc.data_path, fc.array_index,
                          tuple(tuple(round(c, 5) for c in k.co) + tuple(k.handle_left) + tuple(k.handle_right)
                                for k in fc.keyframe_points)))
        if obj is not root:
            parts.append(tuple(round(x, 6) for x in wx.rows(obj.matrix_world)))
        obj = obj.parent
    return repr(parts)


def wind_series(root, frames):
    """毎フレームの風 (Y が上)。強さ・突風・向きのキーも読む。"""
    v = rt.read_params(root, STORED())
    out = []
    for f in frames:
        m = rt.matrix_world_at(root, f)
        d = mo.unit(mo.yup_vector(tuple(m.col[0].xyz)))
        strength = rt.prop_at(root, "dfx_strength", f)
        gust = rt.prop_at(root, "dfx_gust", f)
        out.append(mx.wind_vector(d, strength if strength is not None else v["strength"],
                                  mx.gust_factor(f, v["seed"], v["gust_interval"], v["gust_length"],
                                                 v["gust_random"]),
                                  gust if gust is not None else v["gust"], v["turbulence"], f, v["seed"]))
    return out


def update(root, frame):
    scene = bpy.context.scene
    start = int(scene.frame_start) - mx.WIND_PREROLL
    end = int(scene.frame_end) + mx.WIND_POSTROLL
    sig = _signature(root, start, end)
    if root.get("dfx_wind_signature") == sig:
        return
    frames = list(range(start, end + 1))
    values = wind_series(root, frames)
    drift = mx.integrate(values)
    for keys, series in ((wx.TABLE_KEYS[3:], values), (wx.TABLE_KEYS[:3], drift)):
        world = [mo.zup_vector(s) for s in series]
        for axis, key in enumerate(keys):
            root[key] = [s[axis] for s in world]
    root[wx.DRIFT_START_KEY] = float(start)
    root["dfx_wind_signature"] = sig
    for r in rt.effect_roots():
        if r is not root and r.get(wx.WIND_KEY) == root:
            rt.update_root(r, frame)


def link_unlinked(wind):
    from . import weather
    count = 0
    for r in rt.effect_roots():
        if r.get(rt.TYPE_KEY) in weather.WEATHER_TYPES and r.get(wx.WIND_KEY) is None:
            r[wx.WIND_KEY] = wind
            rt.update_root(r)
            count += 1
    return count


bd.register_builder(mx.WIND, STORED, LIVE, build, update)
