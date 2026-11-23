# -*- coding: utf-8 -*-
"""風: 天候 (雨・雪・土煙・火花・霧・地霧) をまとめて流す、風向き・強さ・突風のコントローラ。

構成:
    dfx_windN     矢印 (移動・回転できる)。矢印の向き (+X) が風向き。パラメータを持つ

天候の粒は「風に流された距離」(風の速さを時間で積分したもの) だけずれる。この距離は
「風を作成」「風を更新」の時に、タイムスライダーの範囲 (と、その前後) の毎フレームの値を計算し、
風のアトリビュート (数の配列) に書いておく。
強さ・突風の強さ・矢印の回転にキーを打ったら「風を更新」で反映する。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param, ToolError
from . import _weather as wx

CATEGORY = u"風"
LIVE_PARAMS = ("strength", "gust")          # キーを打てる (反映は「風を更新」)
REBUILD_PARAMS = ("gust_interval", "gust_length", "gust_random", "turbulence", "seed")


class WindTool(BaseTool):
    label = u"風"
    tab = wx.TAB
    category = CATEGORY
    order = 870
    tooltip = (u"風のコントローラ (矢印) を作ります。矢印の向きが風向きで、回転させて変えます。\n"
               u"シーンの天候 (雨・雪・土煙・火花・霧・地霧) がこの風で流れます。\n"
               u"強さ・突風にキーを打ったり、矢印を回したら「風を更新」を押します。")
    params = [
        Param("strength", u"強さ", 0.15, minimum=0.0, step=0.05,
              tooltip=u"1 フレームに流れる距離。キーを打てる (反映は「風を更新」)"),
        Param("gust", u"突風の強さ", 1.5, minimum=0.0, step=0.5,
              tooltip=u"突風の時に、強さがこの倍率だけ増える (0 で突風なし)。キーを打てる"),
        Param("gust_interval", u"突風の間隔 (フレーム)", 48.0, minimum=1.0, step=4.0, decimals=1),
        Param("gust_length", u"突風の長さ (フレーム)", 18.0, minimum=1.0, step=2.0, decimals=1),
        Param("gust_random", u"突風のばらつき", 0.6, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"突風の来るタイミングと強さのばらつき"),
        Param("turbulence", u"乱れ", 0.3, minimum=0.0, maximum=2.0, step=0.1,
              tooltip=u"風向きのゆらぎ (1 で ±45°) と、上下のゆらぎ"),
        Param("seed", u"シード", 1, minimum=0, maximum=99999),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        root = wx.create_effect(cmds, mu, ctx, v, KIND)
        linked = link_unlinked(cmds, mu, root)
        if linked:
            ctx.log(u"  風のない天候 %d 個をこの風につなぎました" % linked)
        return root


class WindUpdateTool(BaseTool):
    label = u"風を更新"
    tab = wx.TAB
    category = CATEGORY
    order = 871
    tooltip = (u"選択した風 (何も選ばなければシーンの全部の風) を、今のパラメータと\n"
               u"タイムスライダーの範囲で計算し直します。強さ・突風・矢印の向き、範囲を変えた時に使います。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = mu.selected_effect_roots(wx.WIND) or wx.wind_roots(cmds, mu)
        if not roots:
            raise ToolError(u"シーンに風がありません (「風」で作ります)")
        return wx.rebuild_effects(cmds, mu, ctx, roots)


# ---------------------------------------------------------------- 構築
ARROW = [(0, 0, 0), (2.4, 0, 0), (2.4, 0.35, 0), (3.2, 0, 0), (2.4, -0.35, 0), (2.4, 0, 0)]


def create(cmds, mu, root, v, camera=None, target=None):
    """矢印の形と、キーを打つアトリビュートを作る (作成時だけ)。"""
    curve = cmds.curve(degree=1, point=ARROW)
    shape = cmds.listRelatives(curve, shapes=True, fullPath=True)[0]
    cmds.parent(shape, root, shape=True, relative=True)
    cmds.delete(curve)
    shape = cmds.listRelatives(root, shapes=True, fullPath=True)[0]
    cmds.rename(shape, wx.short(root) + "Shape")
    shape = cmds.listRelatives(root, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 18)
    cmds.setAttr(shape + ".lineWidth", 2)
    cmds.setAttr(root + ".translate", 0, 3, 0, type="double3")
    for axis in "xyz":
        cmds.setAttr("%s.s%s" % (root, axis), lock=True, keyable=False, channelBox=False)
    for attr in wx.DRIFT_ATTRS + wx.WIND_VEC_ATTRS:
        cmds.addAttr(root, longName=attr, dataType="doubleArray")
    cmds.addAttr(root, longName=wx.DRIFT_START_ATTR, attributeType="double")
    # キーは打てるが、反映は「風を更新」なので表示名に「(更新)」を付ける
    for p in KIND.stored_params():
        if p.name in LIVE_PARAMS:
            cmds.addAttr("%s.%s%s" % (root, mu.PARAM_PREFIX, p.name), edit=True,
                         niceName=p.label + mu.REBUILD_SUFFIX)


def build(cmds, mu, root, v):
    """タイムスライダーの範囲 (と前後) の毎フレームの風と流された距離を、表に書く。"""
    start = int(cmds.playbackOptions(query=True, minTime=True)) - wx.WIND_PREROLL
    end = int(cmds.playbackOptions(query=True, maxTime=True)) + wx.WIND_POSTROLL
    frames = list(range(start, end + 1))
    values = wind_series(cmds, mu, root, frames)
    drift = wx.integrate(values)
    for attrs, series in ((wx.WIND_VEC_ATTRS, values), (wx.DRIFT_ATTRS, drift)):
        for axis, attr in enumerate(attrs):
            cmds.setAttr(root + "." + attr, [s[axis] for s in series], type="doubleArray")
    cmds.setAttr(root + "." + wx.DRIFT_START_ATTR, start)
    return len(frames)


def wind_series(cmds, mu, root, frames):
    """毎フレームの風 (矢印の向き × 強さ × 突風 + 乱れ)。強さ・突風・向きのキーも読む。"""
    def attr(name):
        return "%s.%s%s" % (root, mu.PARAM_PREFIX, name)

    seed = cmds.getAttr(attr("seed"))
    interval = cmds.getAttr(attr("gust_interval"))
    length = cmds.getAttr(attr("gust_length"))
    randomness = cmds.getAttr(attr("gust_random"))
    turbulence = cmds.getAttr(attr("turbulence"))
    out = []
    for f in frames:
        m = cmds.getAttr(root + ".worldMatrix[0]", time=f)
        d = (m[0], m[1], m[2])
        n = max(1e-9, sum(x * x for x in d) ** 0.5)
        d = tuple(x / n for x in d)
        out.append(wx.wind_vector(d, cmds.getAttr(attr("strength"), time=f),
                                  wx.gust_factor(f, seed, interval, length, randomness),
                                  cmds.getAttr(attr("gust"), time=f), turbulence, f, seed))
    return out


def link_unlinked(cmds, mu, wind):
    """風につながっていない天候を wind につなぐ。つないだ数を返す。"""
    count = 0
    for root in wx.weather_roots(cmds, mu, include_wind=False):
        kind = wx.kind_of(cmds, mu, root)
        if kind.need_wind and not mu.is_baked(root) and not wx.find_link(cmds, root, wx.WIND_ATTR):
            wx.link(cmds, root, wx.WIND_ATTR, wind)
            count += 1
    return count


KIND = wx.register(wx.Kind(wx.WIND, WindTool, "dfx_wind#", u"風", LIVE_PARAMS, REBUILD_PARAMS,
                           build=build, create=create, need_wind=False))
