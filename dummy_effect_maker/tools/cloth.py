# -*- coding: utf-8 -*-
"""布タブ: 波打つ布 (人形劇で海などを表す、透ける布 (オーガンジー・シフォン) を何枚も重ねて波打たせるもの)。

    dfx_clothN                 ルート (パラメータ)
      dfx_clothN_stage         舞台 (ロケータ。動かす・回す・拡大縮小すると、布の束がまとめて動く。キーも打てる)
        dfx_clothN_layer_01    布 1 枚分 (手前から奥へ。置き場所はエクスプレッションが決める)
          dfx_clothN_cloth_01  布の板 (細かく分けたポリゴン平面)
          dfx_clothN_lattice_01 / _latticeBase_01  布を波打たせる格子 (ラティス)

布は格子の点 (長さの向きに「波の細かさ」個 × 横に 4 個) の高さだけを、布 1 枚につき 1 つのエクスプレッションで
決めるので軽い。波は長さの向きに流れ、横にも少しねじれる。布ごとに「布ごとのずれ」だけ波が遅れる
(順にあおったように見える)。色は手前 → 奥で変わり、透けて重なる。計算は motion.cloth_wave と同じ式。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param, ToolError
from . import _weather as wx
from .. import motion as mo

EFFECT_TYPE = "cloth_waves"
TAB = u"布"
CATEGORY = u"波打つ布"
STAGE_ATTR = "dfx_stage"
ACROSS = 4                      # 格子の横の点の数
MESH_DIVISIONS = (60, 8)        # 布の板の分け方 (長さ, 横)
REBUILD = ("layers", "length", "width", "divisions")
H = wx.mel_hash


class ClothTool(BaseTool):
    label = u"波打つ布"
    tab = TAB
    category = CATEGORY
    order = 950
    tooltip = (u"人形劇の海のように、透ける布を何枚も重ねて波打たせます (オーガンジー・シフォンの布)。\n"
               u"舞台のロケータ (_stage) を動かす・回す・拡大縮小すると、布の束がまとめて動きます。\n"
               u"オブジェクトを選んでいれば、その位置に置きます。")
    params = [
        Param("layers", u"布の枚数", 4, minimum=1, maximum=20),
        Param("length", u"布の長さ", 20.0, minimum=0.1, step=1.0, tooltip=u"波が流れる向きの長さ"),
        Param("width", u"布の幅", 3.0, minimum=0.01, step=0.5),
        Param("divisions", u"波の細かさ (格子の分割)", 24, minimum=4, maximum=64,
              tooltip=u"布を曲げる格子の、長さの向きの点の数 (多いほど細かい波まで曲がる)"),
        Param("spacing", u"重なりの間隔", 0.8, minimum=0.0, step=0.1, tooltip=u"布と布の奥行きの間隔"),
        Param("height_step", u"高さの差", 0.15, step=0.05, tooltip=u"奥の布ほど、この分だけ高くする"),
        Param("amplitude", u"波の高さ", 0.6, minimum=0.0, step=0.1),
        Param("wavelength", u"波の長さ", 8.0, minimum=0.01, step=1.0, tooltip=u"波の山から山までの長さ"),
        Param("period", u"波の周期 (フレーム)", 60.0, minimum=1.0, step=5.0, decimals=1,
              tooltip=u"波が 1 つ分流れるフレーム数 (小さいほど速い)"),
        Param("cross", u"横のねじれ", 0.3, minimum=0.0, maximum=2.0, step=0.1,
              tooltip=u"布の横の向きの波の割合 (布がねじれるように見える)"),
        Param("phase_step", u"布ごとのずれ", 0.12, step=0.02, decimals=3,
              tooltip=u"奥の布ほど、波がこの分 (1 で 1 周期) 遅れる"),
        Param("pin_ends", u"両端を押さえる", False, tooltip=u"ON: 布の両端は波打たない (人が端を持っているように)"),
        Param("color_front", u"手前の布の色", (0.55, 0.8, 1.0), kind="color"),
        Param("color_back", u"奥の布の色", (0.15, 0.35, 0.8), kind="color"),
        Param("opacity", u"不透明度", 0.45, minimum=0.0, maximum=1.0, step=0.05, tooltip=u"布の透け具合"),
        Param("seed", u"シード", 1, minimum=0, maximum=99999),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, KIND)


class ClothUpdateTool(BaseTool):
    label = u"波打つ布を更新"
    tab = TAB
    category = CATEGORY
    order = 952
    tooltip = (u"選択した波打つ布を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (枚数・長さ・幅・細かさ) を変えた時に使います。舞台のロケータはそのまま。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        roots = [r for r in mu.selected_effect_roots()
                 if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) == EFFECT_TYPE]
        if not roots:
            raise ToolError(u"更新する波打つ布 (dfx_clothN、またはその中の物) を選択してください")
        return wx.rebuild_effects(cmds, mu, ctx, roots)


# ---------------------------------------------------------------- 構築 (Maya)
def _create_stage(cmds, mu, root, v, camera=None, target=None):
    prefix = wx.short(root)
    loc = cmds.spaceLocator(name=prefix + "_stage")[0]
    loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
    shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 18)
    for a in ("localScaleX", "localScaleY", "localScaleZ"):
        cmds.setAttr(shape + "." + a, max(v["width"], 1.0))
    if target:
        cmds.xform(loc, worldSpace=True, translation=cmds.xform(target, query=True, worldSpace=True, translation=True))
    mu.tag(loc)
    wx.link(cmds, root, STAGE_ATTR, loc)
    return loc


def _layer_expression(r, layer, layers, group, shader, lattice_shape, s_div):
    """布 l の置き場所・色・透明度と、格子の点の高さ (motion.cloth_layer_offset / cloth_color / cloth_wave と同じ)。"""
    tcol = repr(layer / float(max(layers - 1, 1)))
    code = [u"""float $t = frame;
float $sd = {r}.dfx_seed;
float $Lc = {r}.dfx_length;
float $Wc = max({r}.dfx_width, 0.01);
float $kx = 6.2831853 / max({r}.dfx_wavelength, 0.01);
float $w = 6.2831853 / max({r}.dfx_period, 1.0);
float $ph = 6.2831853 * ({l} * {r}.dfx_phase_step + 0.15 * {h61});
float $ph2 = 6.2831853 * {h62};
{g}.translateX = 0;
{g}.translateY = {l} * {r}.dfx_height_step;
{g}.translateZ = -{l} * {r}.dfx_spacing;
{sh}.outColorR = {r}.dfx_color_frontR + ({r}.dfx_color_backR - {r}.dfx_color_frontR) * {tc};
{sh}.outColorG = {r}.dfx_color_frontG + ({r}.dfx_color_backG - {r}.dfx_color_frontG) * {tc};
{sh}.outColorB = {r}.dfx_color_frontB + ({r}.dfx_color_backB - {r}.dfx_color_frontB) * {tc};
float $tr = 1 - {r}.dfx_opacity;
{sh}.outTransparencyR = $tr; {sh}.outTransparencyG = $tr; {sh}.outTransparencyB = $tr;
float $d[];
int $i; int $k;
for ($i = 0; $i < {s}; $i++) {{
    float $x = $i / {s1} - 0.5;
    for ($k = 0; $k < {u}; $k++) {{
        float $z = $k / {u1} - 0.5;
        float $X = $x * $Lc;
        float $Z = $z * $Wc;
        float $y = sin($kx * $X - $w * $t - $ph)
                 + {r}.dfx_cross * sin(0.6 * $kx * $X + 6.2831853 * 0.5 * $Z / $Wc + 1.4 * $w * $t + $ph2);
        float $env = 1;
        if ({r}.dfx_pin_ends) $env = sin(3.14159265358979 * ($x + 0.5));
        $d[$i * {u} + $k] = {r}.dfx_amplitude * $y * $env;
    }}
}}
""".format(r=r, l=layer, h61=H(layer, 0, 61), h62=H(layer, 0, 62), g=wx.short(group), sh=wx.short(shader),
           tc=tcol, s=s_div, s1=repr(float(max(s_div - 1, 1))), u=ACROSS, u1=repr(float(ACROSS - 1)))]
    for i in range(s_div):
        for k in range(ACROSS):
            n = i * ACROSS + k
            code.append(u"{p}.pt[{i}][0][{k}].yValue = -0.5 + $d[{n}]; {p}.pt[{i}][1][{k}].yValue = 0.5 + $d[{n}];\n"
                        .format(p=lattice_shape, i=i, k=k, n=n))
    return u"".join(code)


def build(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    stage = wx.find_link(cmds, root, STAGE_ATTR)
    if not stage:
        raise ToolError(u"%s の舞台のロケータが見つかりません (削除された可能性があります)" % prefix)
    layers, s_div = int(v["layers"]), int(v["divisions"])
    length, width = v["length"], v["width"]
    for l in range(layers):
        tag = "%02d" % (l + 1)
        grp = cmds.ls(cmds.group(empty=True, name="%s_layer_%s" % (prefix, tag), parent=stage), long=True)[0]
        owned.append(grp)
        plane = cmds.polyPlane(width=length, height=width, subdivisionsX=MESH_DIVISIONS[0],
                               subdivisionsY=MESH_DIVISIONS[1], constructionHistory=False,
                               name="%s_cloth_%s" % (prefix, tag))[0]
        plane = cmds.ls(cmds.parent(plane, grp, relative=True)[0], long=True)[0]
        shape = cmds.listRelatives(plane, shapes=True, fullPath=True)[0]
        for a in ("castsShadows", "receiveShadows"):
            cmds.setAttr(shape + "." + a, 0)
        ffd, lat, base = cmds.lattice(plane, divisions=(s_div, 2, ACROSS), objectCentered=False,
                                      outsideLattice=1, name="%s_wave_%s" % (prefix, tag))
        # 近くの格子の点だけが効くようにする (既定は全部の点でなめらかにするので、波が小さくなってしまう)
        cmds.setAttr(ffd + ".local", 1)
        for axis in "STU":
            cmds.setAttr("%s.localInfluence%s" % (ffd, axis), 2)
        lat = cmds.rename(lat, "%s_lattice_%s" % (prefix, tag))
        base = cmds.rename(base, "%s_latticeBase_%s" % (prefix, tag))
        lat, base = [cmds.ls(cmds.parent(n, grp, relative=True)[0], long=True)[0] for n in (lat, base)]
        for n in (lat, base):
            cmds.setAttr(n + ".translate", 0, 0, 0, type="double3")
            cmds.setAttr(n + ".scale", length, 1.0, width, type="double3")
            cmds.setAttr(n + ".visibility", 0)
        lat_shape = wx.short(cmds.listRelatives(lat, shapes=True, fullPath=True)[0])
        shader, sg = mu.create_surface_shader("%s_mat_%s" % (prefix, tag), mo.cloth_color(l, layers, v),
                                              1 - v["opacity"])
        cmds.sets(plane, edit=True, forceElement=sg)
        owned += [plane, ffd, lat, base, shader, sg]
        owned.append(cmds.expression(name="%s_wave_%s" % (prefix, tag), alwaysEvaluate=True,
                                     unitConversion="all",
                                     string=_layer_expression(prefix, l, layers, grp, shader, lat_shape, s_div)))
    wx.finish(cmds, mu, root, owned)


def _live():
    return tuple(p.name for p in ClothTool.params if p.name not in REBUILD)


KIND = wx.register(wx.Kind(EFFECT_TYPE, ClothTool, "dfx_cloth#", u"波打つ布", _live(), REBUILD, build=build,
                           create=_create_stage, need_camera=False, need_wind=False, keep=(STAGE_ATTR,)))
