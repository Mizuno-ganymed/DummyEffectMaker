# -*- coding: utf-8 -*-
"""天候タブ: 大きな板 (テクスチャ) で作るもの。遠景や、広い範囲をまとめて覆う時に。

    雨 (遠景の筒)      カメラを囲む筒 (何重か) に雨の筋のテクスチャを流す
    雪 (遠景の筒)      同じ作りで、雪の粒のテクスチャをゆっくり流す
    霧 (霧の板)        カメラを向く大きな霧の板を範囲の箱の中に置き、風で流す
    霧 (遠景の重ね板)  カメラから離れるほど何枚も重なる薄い板で、遠くほど霧の色にする
    地霧 (重ね板)      地面と平行な板を高さ違いで重ね、霧の模様を流す (上ほど薄い)
    地霧 (這う霧)      平たい霧の板が地面すれすれを漂う

テクスチャはプロジェクトの sourceimages/dfx_weather/ に作る (アニメ調の段のある模様)。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param
from . import _weather as wx
from .weather import _area_params, _common_tail, _create, _create_area, _live, _area_head

H = wx.mel_hash


# ---------------------------------------------------------------- 遠景の筒 (雨・雪)
def _far_params(snow):
    if snow:
        speed, tile, near, far, color = 0.05, 6.0, 0.9, 0.5, (1.0, 1.0, 1.0)
    else:
        speed, tile, near, far, color = 1.5, 3.0, 0.5, 0.2, (0.78, 0.86, 1.0)
    params = [Param("layers", u"筒の数", 3, minimum=1, maximum=8,
                    tooltip=u"何重の筒にするか (遠いほど薄く、ゆっくり動いて見える)"),
              Param("radius", u"内側の筒の半径", 12.0, minimum=0.1, step=2.0),
              Param("radius_step", u"筒の間隔", 10.0, minimum=0.0, step=2.0),
              Param("height", u"筒の高さ", 40.0, minimum=0.1, step=5.0),
              Param("speed", u"落ちる速さ", speed, minimum=0.0, step=0.05, decimals=3,
                    tooltip=u"1 フレームに落ちる距離"),
              Param("tile", u"模様の大きさ", tile, minimum=0.01, step=0.5,
                    tooltip=u"テクスチャ 1 枚分の幅 (ワールドの単位)")]
    if snow:
        params.append(Param("sway", u"揺れ幅", 0.3, minimum=0.0, step=0.1))
    return params + [
        Param("opacity_near", u"内側の不透明度", near, minimum=0.0, maximum=1.0, step=0.1),
        Param("opacity_far", u"外側の不透明度", far, minimum=0.0, maximum=1.0, step=0.1),
        Param("color", u"色", color, kind="color"),
        wx.texture_param("snow" if snow else "rain", tiling=True)] + _common_tail()


class RainFarTool(BaseTool):
    label = u"雨 (遠景の筒)"
    tab = wx.TAB
    category = u"雨"
    order = 873
    tooltip = (u"カメラを囲む筒 (何重か) に雨の筋のテクスチャを流します。とても軽いので遠くの雨に。\n"
               u"筒はカメラに付いて動きます。近くは「雨 (線と跳ね)」と組み合わせます。")
    params = _far_params(False)

    def run(self, ctx, **v):
        return _create(ctx, v, RAIN_FAR)


class SnowFarTool(BaseTool):
    label = u"雪 (遠景の筒)"
    tab = wx.TAB
    category = u"雪"
    order = 875
    tooltip = (u"カメラを囲む筒 (何重か) に雪の粒のテクスチャをゆっくり流します。遠くの雪に。\n"
               u"近くは「雪 (粒)」と組み合わせます。")
    params = _far_params(True)

    def run(self, ctx, **v):
        return _create(ctx, v, SNOW_FAR)


def cylinder_layout(sides=48):
    """半径 1・高さ 1 (y = -0.5〜0.5) の筒 (ふたなし)。UV は U が周り、V が高さ。"""
    pts, uvs, faces, uv_ids = [], [], [], []
    for y in (-0.5, 0.5):
        for k in range(sides):
            a = 2 * math.pi * k / sides
            pts.append((math.cos(a), y, -math.sin(a)))
        for k in range(sides + 1):
            uvs.append((k / float(sides), y + 0.5))
    for k in range(sides):
        k1 = (k + 1) % sides
        faces.append([k, sides + k, sides + k1, k1])
        uv_ids += [k, (sides + 1) + k, (sides + 1) + k + 1, k + 1]
    return pts, faces, uvs, uv_ids


def texture_aspect(path):
    """テクスチャの 縦 / 横 (模様の高さ = 模様の大きさ × これ)。読めなければ 1。"""
    try:
        from ..ui.qt import QtGui
        size = QtGui.QImageReader(path).size()
        if size.width() > 0 and size.height() > 0:
            return size.height() / float(size.width())
    except Exception:
        pass
    return 1.0


def far_repeat(radius, height, tile, aspect):
    """筒の UV の繰り返し (周りは整数にして継ぎ目を出さない)。aspect = テクスチャの縦 / 横。"""
    around = max(1, int(round(2 * math.pi * radius / max(tile, 1e-3))))
    return around, height / (max(tile, 1e-3) * aspect)


def build_far(cmds, mu, root, v, snow=False):
    prefix = wx.short(root)
    owned = []
    tracks = wx.track_links(cmds, mu, root, owned)
    grp = cmds.group(empty=True, name=prefix + "_layers", parent=root)
    grp = cmds.ls(grp, long=True)[0]
    owned.append(grp)
    # 筒はカメラの位置に付いて動く (回転はしない)
    cmds.connectAttr(tracks[wx.CAMERA_ATTR] + ".outputTranslate", grp + ".translate")
    texture = wx.resolve_texture(cmds, v, "snow" if snow else "rain")
    aspect = texture_aspect(texture)
    pts, faces, uvs, uv_ids = cylinder_layout()
    layers = int(v["layers"])
    items = []
    for k in range(layers):
        cyl = wx.make_mesh(cmds, "%s_layer_%02d" % (prefix, k + 1), grp, pts, faces, uvs, uv_ids)
        shader, sg, tex, place = wx.texture_shader(cmds, mu, root, "%s_layer_%02d_mat" % (prefix, k + 1),
                                                   texture, owned, color_attr=None)
        cmds.sets(cyl, edit=True, forceElement=sg)
        cmds.setAttr(cmds.listRelatives(cyl, shapes=True, fullPath=True)[0] + ".doubleSided", 1)
        items.append((cyl, tex, place))
    r = prefix
    code = [wx.mel_head(root, need_camera=True), u"""float $o[];
string $gp = "{g}";
float $rad0 = {r}.dfx_radius;
float $step = {r}.dfx_radius_step;
float $hh = {r}.dfx_height;
float $tile = max({r}.dfx_tile, 0.001);
vector $right = unit(cross($cdir, <<0, 1, 0>>));
if (mag(cross($cdir, <<0, 1, 0>>)) < 1e-6) $right = <<1, 0, 0>>;
float $side = dot($Dnow * $wf, $right);
""".format(g=wx.short(grp), r=r)]
    for k, (cyl, tex, place) in enumerate(items):
        ratio = k / float(max(layers - 1, 1))
        code.append(u"""{{
    float $R = $rad0 + $step * {k};
    int $ru = max(1, floor(6.2831853 * $R / $tile + 0.5));
    float $rv = $hh / ($tile * {aspect});
    float $sway = 0;
    {sway}
    {c}.scaleX = $R; {c}.scaleY = $hh; {c}.scaleZ = $R;
    {pl}.repeatU = $ru; {pl}.repeatV = $rv;
    {pl}.offsetU = -$side / $tile + $sway + {ho};
    {pl}.offsetV = {r}.dfx_speed * frame / ($tile * {aspect}) + {hv};
    float $op = {r}.dfx_opacity_near + ({r}.dfx_opacity_far - {r}.dfx_opacity_near) * {ratio};
    {tx}.alphaGain = $op;
    {tx}.colorGainR = {r}.dfx_colorR * $op; {tx}.colorGainG = {r}.dfx_colorG * $op; {tx}.colorGainB = {r}.dfx_colorB * $op;
}}
""".format(k=k, aspect=repr(aspect), c=wx.short(cyl), pl=wx.short(place), tx=wx.short(tex),
           r=r, ratio=repr(ratio), ho=H(k, 0, 1), hv=H(k, 0, 2),
           sway=(u"$sway = {r}.dfx_sway * sin(6.2831853 * frame / 90.0 + {k}) / $tile;".format(
               r=r, k=k) if snow else u"")))
    expr = cmds.expression(name=prefix + "_layers", alwaysEvaluate=True, unitConversion="all",
                           string=u"".join(code))
    owned.append(expr)
    wx.finish(cmds, mu, root, owned)


# ---------------------------------------------------------------- 霧の板 (霧・這う霧)
def _card_params(count, width, height, size, squash, opacity, near, spin):
    return ([Param("count", u"板の数", count, minimum=1, maximum=300)]
            + _area_params(width, height, False)
            + [Param("size", u"板の大きさ", size, minimum=0.01, step=1.0),
               Param("size_random", u"大きさのばらつき", 0.4, minimum=0.0, maximum=0.95, step=0.1),
               Param("squash", u"平たさ", squash, minimum=0.01, maximum=2.0, step=0.05,
                     tooltip=u"板の縦の長さの倍率 (小さいほど横長で平たい)"),
               Param("opacity", u"不透明度", opacity, minimum=0.0, maximum=1.0, step=0.05),
               Param("spin", u"回転 (度 / フレーム)", spin, step=0.05, decimals=3),
               Param("near_fade", u"カメラの近くで消える距離", near, minimum=0.0, step=1.0,
                     tooltip=u"カメラがこれより近づくと薄くなって消える (板の切れ目を見せない)"),
               Param("edge_fade", u"範囲の端で消える幅", 0.15, minimum=0.0, maximum=0.5, step=0.05,
                     tooltip=u"範囲の箱の端からこの割合の所で薄くなる (風で回り込む時に、ぱっと出ない)"),
               Param("color", u"色", (0.88, 0.92, 1.0), kind="color"),
               wx.texture_param("fog_card", tiling=False)]
            + _common_tail())


class FogTool(BaseTool):
    label = u"霧 (霧の板)"
    tab = wx.TAB
    category = u"霧"
    order = 880
    tooltip = (u"カメラを向く大きな霧の板 (アニメ調の段のある雲の形) を範囲の箱の中に置き、風で流します。\n"
               u"カメラが近づくと薄くなって消えます。")
    params = _card_params(16, 50.0, 8.0, 14.0, 0.6, 0.4, 8.0, 0.05)

    def run(self, ctx, **v):
        return _create(ctx, v, FOG)


class GroundCrawlTool(BaseTool):
    label = u"地霧 (這う霧)"
    tab = wx.TAB
    category = u"地霧"
    order = 883
    tooltip = (u"平たい霧の板が、地面すれすれ (低い範囲の箱の中) を漂います。\n"
               u"カメラが低くても板の重なりが見えにくいので、「地霧 (重ね板)」の手前に。")
    params = _card_params(24, 40.0, 1.5, 8.0, 0.3, 0.5, 4.0, 0.02)

    def run(self, ctx, **v):
        return _create(ctx, v, GROUND_CRAWL)


CARD_TEX_OUTPUTS = ("alphaGain", "colorGainR", "colorGainG", "colorGainB")
CARD_STRIDE = len(wx.TRANSFORM_OUTPUTS) + len(CARD_TEX_OUTPUTS)


def build_cards(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    count = int(v["count"])
    texture = wx.resolve_texture(cmds, v, "fog_card")
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    pts, faces, uvs = wx.card_points()
    src = wx.make_mesh(cmds, prefix + "_cardSrc", src_grp, pts, faces, uvs)
    cmds.setAttr(src_grp + ".visibility", 0)
    cards = wx.make_instances(cmds, root, src, prefix + "_cards", count, owned)
    texes = []
    for i, card in enumerate(cards):
        # 板ごとに濃さが違うので、マテリアルも板ごと (インスタンスは面ではなく transform に割り当てる)
        shader, sg, tex, _ = wx.texture_shader(cmds, mu, root, "%s_card_%03d_mat" % (prefix, i + 1),
                                               texture, owned, color_attr=None)
        cmds.sets(card, edit=True, forceElement=sg)
        texes.append(tex)
    r = prefix
    head = wx.mel_head(root, need_camera=True) + _area_head(root)

    def body(a, b):
        code = u"""vector $q = <<{h1} - 0.5, {h2} - 0.5, {h3} - 0.5>>;
vector $dw = $Dnow * $wf;
$q = $q + {xv};
$q = <<fmod($q.x + 1000.5, 1.0) - 0.5, clamp(-0.5, 0.5, $q.y), fmod($q.z + 1000.5, 1.0) - 0.5>>;
vector $p = {xf};
float $sz = {r}.dfx_size * (1 + ({h4} - 0.5) * 2 * {r}.dfx_size_random);
float $ed = max({r}.dfx_edge_fade, 0.0001);
float $fe = clamp(0, 1, (0.5 - abs($q.x)) / $ed) * clamp(0, 1, (0.5 - abs($q.z)) / $ed);
float $nf = max({r}.dfx_near_fade, 0.0001);
float $fn = clamp(0, 1, (mag($p - $cp) - $nf * 0.5) / ($nf * 0.5));
float $alpha = {r}.dfx_opacity * $fe * $fn;
int $vis = $alpha > 0.001;
vector $S = <<$sz, $sz * {r}.dfx_squash, $sz>>;
""".format(h1=H("$i", 0, 1), h2=H("$i", 0, 2), h3=H("$i", 0, 3), h4=H("$i", 0, 4), r=r,
           xv=wx.mel_xv("$ai", "$dw"), xf=wx.mel_xf("$am", "$q"))
        # 平たい板を大きく傾けると斜めの破片に見えるので、平たいほど傾きのばらつきを小さく
        code += wx.mel_billboard("$p", u"({h} - 0.5) * 360 * pow(min(1, {r}.dfx_squash), 4) + {r}.dfx_spin * frame"
                                 .format(h=H("$i", 0, 5), r=r))
        code += wx.mel_store_transform(a).replace("* 10;", "* %d;" % CARD_STRIDE) + (
            u"$o[$ob + 10] = $alpha; $o[$ob + 11] = {r}.dfx_colorR * $alpha;\n"
            u"$o[$ob + 12] = {r}.dfx_colorG * $alpha; $o[$ob + 13] = {r}.dfx_colorB * $alpha;\n"
            .format(r=r))
        return code

    for a in range(0, count, wx.CHUNK):
        b = min(count, a + wx.CHUNK)
        lines = []
        for i in range(a, b):
            base = (i - a) * CARD_STRIDE
            lines.append(u" ".join(u"%s.%s = $o[%d];" % (wx.short(cards[i]), attr, base + k)
                                   for k, attr in enumerate(wx.TRANSFORM_OUTPUTS)))
            lines.append(u" " + u" ".join(u"%s.%s = $o[%d];" % (wx.short(texes[i]), attr, base + 10 + k)
                                          for k, attr in enumerate(CARD_TEX_OUTPUTS)) + u"\n")
        code = (head + u"float $o[];\nint $i;\nfor ($i = %d; $i < %d; $i++) {\n" % (a, b)
                + body(a, b) + u"}\n" + u"".join(lines))
        owned.append(cmds.expression(name="%s_card_%02d" % (prefix, a // wx.CHUNK + 1),
                                     alwaysEvaluate=True, unitConversion="all", string=code))
    wx.finish(cmds, mu, root, owned)


# ---------------------------------------------------------------- 霧 (遠景の重ね板)
class FogDepthTool(BaseTool):
    label = u"霧 (遠景の重ね板)"
    tab = wx.TAB
    category = u"霧"
    order = 881
    tooltip = (u"カメラの前に、離れるほど何枚も重なる薄い単色の板を置きます (カメラに付いて動く)。\n"
               u"遠くの物ほど多くの板の向こうになるので、霧の色に近づきます (アニメの空気遠近)。")
    params = [Param("layers", u"板の数", 16, minimum=1, maximum=60,
                    tooltip=u"多いほど、地面などに板の境目 (段) が見えにくい"),
              Param("start", u"霧が始まる距離", 20.0, minimum=0.01, step=5.0),
              Param("end", u"一番濃くなる距離", 150.0, minimum=0.01, step=10.0),
              Param("density", u"濃さ", 0.85, minimum=0.0, maximum=0.999, step=0.05,
                    tooltip=u"一番濃くなる距離より遠い物の、霧の不透明度 (板を全部重ねた濃さ)"),
              Param("color", u"色", (0.8, 0.86, 0.95), kind="color"),
              Param("seed", u"シード", 1, minimum=0, maximum=99999)]

    def run(self, ctx, **v):
        return _create(ctx, v, FOG_DEPTH)


def layer_opacity(density, layers):
    """板 1 枚の不透明度 (全部重ねると density)。"""
    return 1.0 - (1.0 - min(density, 0.999)) ** (1.0 / max(layers, 1))


def build_fog_depth(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    tracks = wx.track_links(cmds, mu, root, owned)
    frame = cmds.group(empty=True, name=prefix + "_frame", parent=root)
    frame = cmds.ls(frame, long=True)[0]
    owned.append(frame)
    # 板の入れ物はカメラと同じ位置・向き
    dm = tracks[wx.CAMERA_ATTR]
    cmds.connectAttr(dm + ".outputTranslate", frame + ".translate")
    cmds.connectAttr(dm + ".outputRotate", frame + ".rotate")
    cam_shape = cmds.listRelatives(wx.find_link(cmds, root, wx.CAMERA_ATTR), shapes=True,
                                   type="camera", fullPath=True)[0]
    shader, sg = mu.create_surface_shader(prefix + "_mat", (1, 1, 1))
    owned.extend([shader, sg])
    layers = int(v["layers"])
    pts, faces, uvs = wx.card_points()
    planes = []
    for k in range(layers):
        plane = wx.make_mesh(cmds, "%s_layer_%02d" % (prefix, k + 1), frame, pts, faces, uvs)
        planes.append(plane)
    cmds.sets(planes, edit=True, forceElement=sg)
    r = prefix
    code = [wx.mel_head(root, need_wind=False), u"""float $tanh = {cs}.horizontalFilmAperture * 12.7 / max({cs}.focalLength, 0.001);
int $n = {n};
float $d0 = {r}.dfx_start;
float $d1 = max({r}.dfx_end, $d0 + 0.001);
float $alpha = 1 - pow(1 - min({r}.dfx_density, 0.999), 1.0 / $n);
{sh}.outTransparencyR = 1 - $alpha; {sh}.outTransparencyG = 1 - $alpha; {sh}.outTransparencyB = 1 - $alpha;
{sh}.outColorR = {r}.dfx_colorR * $alpha; {sh}.outColorG = {r}.dfx_colorG * $alpha; {sh}.outColorB = {r}.dfx_colorB * $alpha;
""".format(cs=wx.short(cam_shape), n=layers, r=r, sh=wx.short(shader))]
    for k, plane in enumerate(planes):
        t = k / float(max(layers - 1, 1))
        code.append(u"""{{
    float $d = $d0 + ($d1 - $d0) * {t};
    float $w = 2 * $d * $tanh * 1.4;
    {p}.translateZ = -$d; {p}.scaleX = $w; {p}.scaleY = $w; {p}.scaleZ = 1;
}}
""".format(t=repr(t), p=wx.short(plane)))
    owned.append(cmds.expression(name=prefix + "_layers", alwaysEvaluate=True,
                                 unitConversion="all", string=u"".join(code)))
    wx.finish(cmds, mu, root, owned)


# ---------------------------------------------------------------- 地霧 (重ね板)
class GroundFogTool(BaseTool):
    label = u"地霧 (重ね板)"
    tab = wx.TAB
    category = u"地霧"
    order = 882
    tooltip = (u"地面と平行な板を、範囲の箱の中に高さ違いで重ね、霧の模様を風で流します (上ほど薄い)。\n"
               u"箱の端はぼかして消えます。カメラが低いと板の重なりが見えるので、その時は「這う霧」と組み合わせます。")
    params = ([Param("layers", u"板の数", 8, minimum=1, maximum=40)]
              + _area_params(40.0, 1.5, False)
              + [Param("opacity", u"下の不透明度", 0.6, minimum=0.0, maximum=1.0, step=0.05),
                 Param("falloff", u"上ほど薄く", 1.5, minimum=0.0, maximum=5.0, step=0.25,
                       tooltip=u"高さによる薄まり方 (0 で全部同じ濃さ、大きいほど下に溜まる)"),
                 Param("tile", u"模様の大きさ", 10.0, minimum=0.01, step=1.0,
                       tooltip=u"霧の模様 1 枚分の大きさ (ワールドの単位)"),
                 Param("flow", u"自然な流れ", 0.01, step=0.005, decimals=4,
                       tooltip=u"風が無くても模様が少しずつ動く速さ (板ごとに少し違う)"),
                 Param("edge", u"端のぼかし", 0.3, minimum=0.0, maximum=0.99, step=0.05,
                       tooltip=u"箱の端からこの割合の所から薄くなる"),
                 Param("color", u"色", (0.9, 0.93, 1.0), kind="color"),
                 wx.texture_param("fog_tile", tiling=True)]
              + _common_tail(0.6))

    def run(self, ctx, **v):
        return _create(ctx, v, GROUND_FOG)


def ground_layer_opacity(opacity, falloff, k, layers):
    """下から k 枚目 (0 始まり) の板の濃さ。"""
    return opacity * (1.0 - k / float(layers)) ** falloff


def build_ground_fog(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    area = wx.find_link(cmds, root, wx.AREA_ATTR)
    layers = int(v["layers"])
    texture = wx.resolve_texture(cmds, v, "fog_tile")
    pts = [(-0.5, 0.0, 0.5), (0.5, 0.0, 0.5), (0.5, 0.0, -0.5), (-0.5, 0.0, -0.5)]
    uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    items = []
    for k in range(layers):
        name = "%s_layer_%02d" % (prefix, k + 1)
        plane = wx.make_mesh(cmds, name, area, pts, [[0, 1, 2, 3]], uvs)
        cmds.setAttr(plane + ".translateY", -0.5 + (k + 0.5) / layers)
        shader, sg = mu.create_surface_shader(name + "_mat", (1, 1, 1))
        # 霧の模様 (流れる) × 端が消えるボックスランプ (流れない) = 濃さ
        # 色も同じ形のランプ (中は 色 × 濃さ) で作る (乗算済みの色)
        place = cmds.shadingNode("place2dTexture", asUtility=True, name=name + "_place")
        tex = cmds.shadingNode("file", asTexture=True, name=name + "_file")
        cmds.connectAttr(place + ".outUV", tex + ".uvCoord")
        cmds.connectAttr(place + ".outUvFilterSize", tex + ".uvFilterSize")
        for a in ("repeatUV", "offset", "wrapU", "wrapV"):
            cmds.connectAttr(place + "." + a, tex + "." + a)
        cmds.setAttr(tex + ".fileTextureName", texture, type="string")
        mask_place = cmds.shadingNode("place2dTexture", asUtility=True, name=name + "_maskPlace")
        mask = cmds.shadingNode("ramp", asTexture=True, name=name + "_mask")
        cmds.connectAttr(mask_place + ".outUV", mask + ".uvCoord")
        cmds.connectAttr(mask_place + ".outUvFilterSize", mask + ".uvFilterSize")
        tint = cmds.shadingNode("ramp", asTexture=True, name=name + "_tint")
        cmds.connectAttr(mask_place + ".outUV", tint + ".uvCoord")
        cmds.connectAttr(mask_place + ".outUvFilterSize", tint + ".uvFilterSize")
        for ramp in (mask, tint):
            cmds.setAttr(ramp + ".type", 5)            # ボックスランプ (中心 0 → 端 1)
            cmds.setAttr(ramp + ".interpolation", 1)   # 直線
            for idx, pos in ((0, 0.0), (1, 0.7), (2, 1.0)):
                cmds.setAttr("%s.colorEntryList[%d].position" % (ramp, idx), pos)
            cmds.setAttr("%s.colorEntryList[2].color" % ramp, 0, 0, 0, type="double3")
            cmds.connectAttr(tex + ".outColor", ramp + ".colorGain")
        inv = cmds.createNode("reverse", name=name + "_transparency")
        cmds.connectAttr(mask + ".outColor", inv + ".input")
        cmds.connectAttr(inv + ".output", shader + ".outTransparency", force=True)
        cmds.connectAttr(tint + ".outColor", shader + ".outColor", force=True)
        cmds.sets(plane, edit=True, forceElement=sg)
        owned.extend([plane, shader, sg, place, tex, mask_place, mask, tint, inv])
        items.append((plane, place, mask, tint))
    r = prefix
    code = [wx.mel_head(root), _area_head(root), u"""float $sw = mag(<<$am[0], $am[1], $am[2]>>);
float $sdp = mag(<<$am[8], $am[9], $am[10]>>);
vector $ax = unit(<<$am[0], $am[1], $am[2]>>);
vector $az = unit(<<$am[8], $am[9], $am[10]>>);
float $tile = max({r}.dfx_tile, 0.001);
vector $dw = $Dnow * $wf;
float $du = dot($dw, $ax);
float $dv = -dot($dw, $az);
float $edge = clamp(0.0, 0.99, 1 - {r}.dfx_edge);
""".format(r=r)]
    for k, (plane, place, mask, tint) in enumerate(items):
        code.append(u"""{{
    float $a = {r}.dfx_opacity * pow(1 - {k} / {n}, {r}.dfx_falloff);
    {pl}.repeatU = $sw / $tile; {pl}.repeatV = $sdp / $tile;
    {pl}.offsetU = -$du / $tile + {r}.dfx_flow * frame * (1 + {k} * 0.37) + {h1};
    {pl}.offsetV = -$dv / $tile + {r}.dfx_flow * frame * (0.5 - {k} * 0.21) + {h2};
    {m}.colorEntryList[0].colorR = $a; {m}.colorEntryList[0].colorG = $a; {m}.colorEntryList[0].colorB = $a;
    {m}.colorEntryList[1].colorR = $a; {m}.colorEntryList[1].colorG = $a; {m}.colorEntryList[1].colorB = $a;
    {m}.colorEntryList[1].position = $edge;
    {t}.colorEntryList[0].colorR = {r}.dfx_colorR * $a; {t}.colorEntryList[0].colorG = {r}.dfx_colorG * $a; {t}.colorEntryList[0].colorB = {r}.dfx_colorB * $a;
    {t}.colorEntryList[1].colorR = {r}.dfx_colorR * $a; {t}.colorEntryList[1].colorG = {r}.dfx_colorG * $a; {t}.colorEntryList[1].colorB = {r}.dfx_colorB * $a;
    {t}.colorEntryList[1].position = $edge;
}}
""".format(r=r, k=k, n=repr(float(layers)), pl=wx.short(place), m=wx.short(mask),
           t=wx.short(tint), h1=H(k, 0, 1), h2=H(k, 0, 2)))
    owned.append(cmds.expression(name=prefix + "_layers", alwaysEvaluate=True,
                                 unitConversion="all", string=u"".join(code)))
    wx.finish(cmds, mu, root, owned)


# ---------------------------------------------------------------- 種類の登録
RAIN_FAR = wx.register(wx.Kind("rain_far", RainFarTool, "dfx_rainFar#", u"雨 (遠景の筒)",
                               _live(RainFarTool, ("layers", "texture")), ("layers", "texture"),
                               build=lambda c, m, r, v: build_far(c, m, r, v, snow=False),
                               need_camera=True))
SNOW_FAR = wx.register(wx.Kind("snow_far", SnowFarTool, "dfx_snowFar#", u"雪 (遠景の筒)",
                               _live(SnowFarTool, ("layers", "texture")), ("layers", "texture"),
                               build=lambda c, m, r, v: build_far(c, m, r, v, snow=True),
                               need_camera=True))
FOG = wx.register(wx.Kind("fog", FogTool, "dfx_fog#", u"霧 (霧の板)",
                          _live(FogTool, ("count", "texture")), ("count", "texture"),
                          build=build_cards,
                          create=_create_area, need_camera=True))
FOG_DEPTH = wx.register(wx.Kind("fog_depth", FogDepthTool, "dfx_fogDepth#", u"霧 (遠景の重ね板)",
                                _live(FogDepthTool, ("layers",)), ("layers",), build=build_fog_depth,
                                need_camera=True, need_wind=False))
GROUND_FOG = wx.register(wx.Kind("ground_fog", GroundFogTool, "dfx_groundFog#", u"地霧 (重ね板)",
                                 _live(GroundFogTool, ("layers", "texture")), ("layers", "texture"),
                                 build=build_ground_fog, create=_create_area))
GROUND_CRAWL = wx.register(wx.Kind("ground_crawl", GroundCrawlTool, "dfx_groundCrawl#",
                                   u"地霧 (這う霧)", _live(GroundCrawlTool, ("count", "texture")),
                                   ("count", "texture"),
                                   build=build_cards, create=_create_area, need_camera=True))
