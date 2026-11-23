# -*- coding: utf-8 -*-
"""天候タブ: 細い煙 (たばこ・線香の、たゆたう煙の筋)。

出る場所 (_emitter) から、煙の筋が何本か帯になって上がる。根元は「まっすぐ上がるフレーム数」の間まっすぐで、
その後は上ほど揺れが大きくなり、巻いたり折り返したりして広がり、薄くなって消える。風にも流される。

作り (重いシミュレーションは使わない):
    筋ごとに点 (ジョイント) を並べ、点 j を「出てから a フレームたった煙」として、その位置を出た時刻から
    直接計算する (motion.wisp_point)。帯は点のジョイントにスキンしたカメラを向く帯 (火花の尾と同じ)。
    帯には煙の筋の絵 (幅の真ん中が濃く、先に行くほど薄い) を、根元 → 先に貼る。

    dfx_wispN            ルート (パラメータ。カメラ・風とつながる)
      dfx_wispN_emitter  出る場所 (動かしてよい。オブジェクトを選んで作ると、それに付いて動く)
      dfx_wispN_strand_01 ...  筋の帯 / dfx_wispN_joints  帯の点のジョイント
"""
from __future__ import absolute_import

from ..core import BaseTool, Param
from . import _weather as wx

EFFECT_TYPE = "wisp"
STYLE_REAL = u"実写"
STYLE_ANIME = u"アニメ"
REBUILD = ("count", "points", "style", "texture")
H = wx.mel_hash


def texture_name(style):
    return "wisp_anime" if style == STYLE_ANIME else "wisp"


class WispTool(BaseTool):
    label = u"細い煙 (たばこ・線香)"
    tab = wx.TAB
    category = u"煙"
    order = 877.5
    tooltip = (u"出る場所から、細い煙の筋がゆらゆら上がります (たばこ・線香・ろうそくの煙など)。\n"
               u"根元はまっすぐ、上ほど大きく揺れて巻き、広がって消えます。風にも流されます。\n"
               u"オブジェクトを選んで作ると、出る場所がそれに付いて動きます。")
    params = [
        Param("style", u"見た目", STYLE_REAL, kind="choice", choices=[STYLE_REAL, STYLE_ANIME],
              tooltip=u"実写: やわらかく消える筋 / アニメ: くっきりした濃さの段の筋"),
        Param("count", u"筋の数", 5, minimum=1, maximum=12, tooltip=u"少しずつずれて絡み合う筋の数"),
        Param("points", u"筋の点の数", 80, minimum=4, maximum=200, tooltip=u"多いほどなめらかに曲がる"),
        Param("start_frame", u"開始フレーム", 1.0, decimals=2, tooltip=u"煙が出始めるフレーム"),
        Param("life", u"消えるまでのフレーム数", 110.0, minimum=1.0, step=10.0, decimals=1,
              tooltip=u"出てから消えるまで (筋の長さ)"),
        Param("speed", u"上がる速さ", 0.12, minimum=0.0, step=0.02, decimals=3, tooltip=u"1 フレームに上がる距離"),
        Param("laminar", u"まっすぐ上がるフレーム数", 20.0, minimum=0.0, step=5.0, decimals=1,
              tooltip=u"出てから揺れ始めるまで (根元のまっすぐな所の長さ)"),
        Param("turbulence", u"揺れの大きさ", 2.5, minimum=0.0, step=0.1,
              tooltip=u"消える頃の横の揺れの大きさ (上ほど大きくなる)"),
        Param("wavelength", u"揺れの長さ", 1.6, minimum=0.01, step=0.5,
              tooltip=u"揺れの 1 回分のおおよその高さ (小さいほど細かく曲がる)"),
        Param("swirl", u"揺れの速さ", 0.03, step=0.01, decimals=3, tooltip=u"揺れの形が変わっていく速さ (0 で形はそのまま流れる)"),
        Param("root_spread", u"根元の広がり", 0.05, minimum=0.0, step=0.05, decimals=3,
              tooltip=u"筋どうしの根元のずれ"),
        Param("width_start", u"根元の太さ", 0.03, minimum=0.0, step=0.01, decimals=3),
        Param("width_end", u"先の太さ", 0.9, minimum=0.0, step=0.1),
        Param("color", u"色", (0.85, 0.85, 0.88), kind="color"),
        Param("opacity", u"不透明度", 0.6, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"キーを打てば、煙を出し始め・止めにできる"),
        wx.texture_param("wisp", tiling=False),
        Param("wind_influence", u"風の影響", 1.0, minimum=0.0, maximum=5.0, step=0.1),
        Param("seed", u"シード", 1, minimum=0, maximum=99999),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, KIND)


# ---------------------------------------------------------------- 構築 (Maya)
def strip_points(count, points):
    """帯の頂点・面・UV (筋 s の点 j のジョイントは x = s * points + j。UV は u = 根元 0 → 先 1、v = 幅)。"""
    pts, faces, uvs = [], [], []
    for s in range(count):
        for j in range(points):
            k = s * points + j
            pts += [(float(k), 0.5, 0.0), (float(k), -0.5, 0.0)]
            u = j / float(max(points - 1, 1))
            uvs += [(u, 1.0), (u, 0.0)]
            if j < points - 1:
                faces.append([2 * k, 2 * k + 1, 2 * k + 3, 2 * k + 2])
    return pts, faces, uvs


def SN(x, c, k):
    """motion._snoise と同じなめらかな乱数の MEL の式 (x は式)。"""
    xx = u"(%s)" % x
    fl = u"floor(%s)" % xx
    f = u"(%s - %s)" % (xx, fl)
    a = u"fmod(abs(sin((%s) * 12.9898 + (%s) * 78.233 + %s * 37.719 + $sd * 4.1414) * 43758.5453), 1.0)" % (fl, c, k)
    b = u"fmod(abs(sin((%s + 1) * 12.9898 + (%s) * 78.233 + %s * 37.719 + $sd * 4.1414) * 43758.5453), 1.0)" % (fl, c, k)
    return u"((%s + (%s - %s) * %s * %s * (3 - 2 * %s)) * 2 - 1)" % (a, b, a, f, f, f)


def _strand_code(r, s, points):
    """筋 s の点の位置 $P[] と太さ $W[] (motion.wisp_point / wisp_rows と同じ)。"""
    m1 = repr(float(max(points - 1, 1)))
    return u"""vector $P[];
float $W[];
int $j;
int $last = -1;
float $L = max({r}.dfx_life, 1.0);
float $lam = {r}.dfx_laminar;
float $spd = {r}.dfx_speed * (1 + ({h41} - 0.5) * 0.4);
for ($j = 0; $j < {n}; $j++) {{
    float $a = $L * $j / {m1};
    float $te = frame - $a;
    vector $E = <<0, 0, 0>>;
    if ($em != "") {{
        float $m[] = `getAttr -time $te ($em + ".worldMatrix[0]")`;
        $E = <<$m[12], $m[13], $m[14]>>;
    }}
    float $y = $spd * $a;
    float $rr = clamp(0, 1, ($a - $lam) / max($L - $lam, 1e-3));
    float $amp = {r}.dfx_turbulence * pow($rr, 1.5);
    float $q = $y / max({r}.dfx_wavelength, 0.01) + {r}.dfx_swirl * $te;
    float $ox = $amp * ({n46} + 0.5 * {n47}) + {r}.dfx_root_spread * ({h44} - 0.5);
    float $oz = $amp * ({n48} + 0.5 * {n49}) + {r}.dfx_root_spread * ({h45} - 0.5);
""".format(r=r, n=points, m1=m1, h41=H(s, 0, 41), h44=H(s, 0, 44), h45=H(s, 0, 45),
           n46=SN("$q", s, "46.0"), n47=SN("2.1 * $q + 7.3", s, "47.0"), n48=SN("$q + 13.7", s, "48.0"),
           n49=SN("2.1 * $q + 3.1", s, "49.0")) + wx.mel_table("$Dte", "$wd", "$te") + u"""    $P[$j] = $E + <<$ox, $y, $oz>> + ($Dnow - $Dte) * $wf;
    $W[$j] = 0;
    if ($te >= {r}.dfx_start_frame) {{
        $W[$j] = {r}.dfx_width_start + ({r}.dfx_width_end - {r}.dfx_width_start) * pow($a / $L, 0.7);
        $last = $j;
    }}
}}
if ($last < 0) {{ for ($j = 1; $j < {n}; $j++) $P[$j] = $P[0]; }}
else {{ for ($j = $last + 1; $j < {n}; $j++) $P[$j] = $P[$last]; }}
""".format(r=r, n=points)


def build(cmds, mu, root, v):
    from . import lightning, weather
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    count, points = int(v["count"]), int(v["points"])
    jgrp = cmds.ls(cmds.group(empty=True, name=prefix + "_joints", parent=root), long=True)[0]
    owned.append(jgrp)
    joints = []
    for k in range(count * points):
        jnt = cmds.createNode("joint", name="%s_jnt_%04d" % (prefix, k), parent=jgrp)
        cmds.setAttr(jnt + ".translate", float(k), 0, 0, type="double3")
        cmds.setAttr(jnt + ".drawStyle", 2)
        joints.append(cmds.ls(jnt, long=True)[0])
    pts, faces, uvs = strip_points(count, points)
    mesh = wx.make_mesh(cmds, prefix + "_strands", root, pts, faces, uvs)
    owned.append(mesh)
    skin = cmds.skinCluster(joints, mesh, toSelectedBones=True, bindMethod=0, maximumInfluences=1,
                            normalizeWeights=1, name=prefix + "_skin")[0]
    for i, jnt in enumerate(joints):
        cmds.skinPercent(skin, "%s.vtx[%d:%d]" % (mesh, 2 * i, 2 * i + 1), transformValue=[(jnt, 1.0)])
    owned.append(skin)
    texture = wx.resolve_texture(cmds, v, texture_name(v["style"]))
    _, sg, _, _ = wx.texture_shader(cmds, mu, root, prefix + "_mat", texture, owned, color_attr="color",
                                    opacity_attr="opacity")
    cmds.sets(mesh, edit=True, forceElement=sg)
    head = wx.mel_head(root, need_camera=True) + weather._emitter_head(root)
    r = prefix
    for s in range(count):
        code = (head + u"float $o[];\n" + _strand_code(r, s, points) + lightning._rows_code(points, r, 0)
                + wx.mel_writes(joints[s * points:(s + 1) * points], wx.TRANSFORM_OUTPUTS[:9]))
        owned.append(cmds.expression(name="%s_strand_%02d" % (prefix, s + 1), alwaysEvaluate=True,
                                     unitConversion="all", string=code))
    wx.finish(cmds, mu, root, owned)


def _live():
    return tuple(p.name for p in WispTool.params if p.name not in REBUILD)


def _create(cmds, mu, root, v, camera=None, target=None):
    from .weather import _create_emitter
    return _create_emitter(cmds, mu, root, v, camera, target)


KIND = wx.register(wx.Kind(EFFECT_TYPE, WispTool, "dfx_wisp#", u"細い煙", _live(), REBUILD, build=build,
                           create=_create, need_camera=True))
