# -*- coding: utf-8 -*-
"""核爆発: 弾が無くても、置いた所で起きる大きな爆発 (キノコ雲)。

使い方:
    「核爆発」を押すと、爆心のロケータ (_center) とルートができる。ロケータを動かして場所を、
    ロケータの上 (Y) で雲が上がる向きを決める。オブジェクトを選んでいれば、その位置に置く。
    起きるフレームはパラメータの「開始フレーム」。

部品 (時間の順):
    _flash     閃光 (カメラを向く丸。すぐ消える)
    _shock     地面を走る衝撃波の輪 (爆心の X-Z 面に広がって薄れる)
    _dome      衝撃波のドーム (半球。速く広がって薄れる)
    _fireball  火の玉 (かさと一緒に上がり、小さくなって消える)
    _stem      キノコ雲の柱 (根元ほど太い)
    _cap       キノコ雲のかさ (外側から下へ巻き込みながら広がる)
    _base      根元に広がる土煙
雲 (柱・かさ・土煙) は火の色から煙の色へ冷え (火から煙になるフレーム数)、最後の 1/4 で薄れて消える。

計算は motion.py の nuke_* と同じ式 (Blender 版も同じ関数を使う)。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param, ToolError
from . import _weather as wx

EFFECT_TYPE = "nuke"
TAB = u"爆発"
CATEGORY = u"核爆発"
H = wx.mel_hash
COUNT_PARAMS = ("stem_count", "cap_count", "base_count")


# ---------------------------------------------------------------- 形 (Blender 版も使う)
def hemisphere_points(segments=24, rings=8):
    """上 (+Y) 半分の球 (半径 1)。(頂点, 面)。"""
    pts = [(0.0, 1.0, 0.0)]
    for r in range(1, rings + 1):
        lat = math.pi / 2 * r / rings
        y, rad = math.cos(lat), math.sin(lat)
        for s in range(segments):
            a = 2 * math.pi * s / segments
            pts.append((rad * math.cos(a), y, rad * math.sin(a)))
    faces = [[0, 1 + (s + 1) % segments, 1 + s] for s in range(segments)]
    for r in range(1, rings):
        o0, o1 = 1 + (r - 1) * segments, 1 + r * segments
        for s in range(segments):
            s1 = (s + 1) % segments
            faces.append([o0 + s, o0 + s1, o1 + s1, o1 + s])
    return pts, faces


# ---------------------------------------------------------------- ツール
def _params():
    return [
        Param("start_frame", u"開始フレーム", 1.0, decimals=2, tooltip=u"爆発が起きるフレーム"),
        Param("size", u"大きさ", 10.0, minimum=0.01, step=1.0,
              tooltip=u"全体の大きさ (火の玉の半径くらい)。ほかの大きさはこの倍率"),
        Param("speed", u"速さ (時間の倍率)", 1.0, minimum=0.01, step=0.1,
              tooltip=u"大きいほど速く進む。0.5 で半分の速さ (スローモーション)"),
        Param("cloud_height", u"キノコ雲の高さ", 3.0, minimum=0.0, step=0.5,
              tooltip=u"かさが最後に届く高さ (大きさの倍率)"),
        Param("rise_frames", u"上がるフレーム数", 90.0, minimum=1.0, step=10.0, decimals=1,
              tooltip=u"雲が上がり切るまでのおおよそのフレーム数"),
        Param("life_frames", u"消えるまでのフレーム数", 300.0, minimum=1.0, step=10.0, decimals=1,
              tooltip=u"雲が消えるまで。最後の 1/4 で薄れる"),
        Param("cool_frames", u"火から煙になるフレーム数", 60.0, minimum=1.0, step=5.0, decimals=1,
              tooltip=u"雲の色が火の色から煙の色に変わるまで"),
        Param("flash_size", u"閃光の大きさ", 4.0, minimum=0.0, step=0.5,
              tooltip=u"最初に光る丸 (大きさの倍率。0 で出さない)"),
        Param("flash_frames", u"閃光のフレーム数", 8.0, minimum=0.5, step=1.0, decimals=1),
        Param("flash_color", u"閃光の色", (1.0, 1.0, 0.92), kind="color"),
        Param("shock_size", u"衝撃波の大きさ", 8.0, minimum=0.0, step=0.5,
              tooltip=u"地面を走る輪の最後の半径 (大きさの倍率。0 で出さない)"),
        Param("shock_frames", u"衝撃波のフレーム数", 40.0, minimum=1.0, step=5.0, decimals=1),
        Param("shock_color", u"衝撃波の色", (1.0, 1.0, 1.0), kind="color"),
        Param("dome_opacity", u"衝撃波のドームの濃さ", 0.5, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"爆心を包んで広がる半球 (0 で出さない)"),
        Param("fireball_size", u"火の玉の大きさ", 1.2, minimum=0.0, step=0.1),
        Param("fireball_frames", u"火の玉のフレーム数", 50.0, minimum=1.0, step=5.0, decimals=1),
        Param("stem_count", u"柱の煙の数", 16, minimum=0, maximum=80),
        Param("stem_width", u"柱の太さ", 1.0, minimum=0.0, step=0.1),
        Param("cap_count", u"かさの煙の数", 28, minimum=0, maximum=120),
        Param("cap_width", u"かさの広がり", 1.4, minimum=0.0, step=0.1),
        Param("roll", u"かさの巻き込み (度 / フレーム)", 1.5, step=0.5, decimals=2,
              tooltip=u"かさの煙が外側から下へ回り込む速さ"),
        Param("base_count", u"根元の煙の数", 30, minimum=0, maximum=120),
        Param("base_spread", u"根元の広がり", 2.5, minimum=0.0, step=0.5),
        Param("base_size", u"根元の煙の大きさ", 0.9, minimum=0.0, step=0.1),
        Param("spin", u"煙の回転 (度 / フレーム)", 1.0, step=0.5, decimals=2),
        Param("fire_color", u"火の色", (1.0, 0.8, 0.35), kind="color"),
        Param("fire_shade", u"火の影の色", (0.95, 0.35, 0.08), kind="color"),
        Param("cloud_color", u"煙の色", (0.78, 0.72, 0.66), kind="color"),
        Param("cloud_shade", u"煙の影の色", (0.48, 0.42, 0.38), kind="color"),
        Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1),
        Param("seed", u"シード", 1, minimum=0, maximum=99999),
    ]


class NukeTool(BaseTool):
    label = u"核爆発"
    tab = TAB
    category = CATEGORY
    order = 920
    tooltip = (u"弾が無くても、爆心のロケータの所で起きる大きな爆発 (キノコ雲) を作ります。\n"
               u"閃光・衝撃波の輪とドーム・火の玉・キノコ雲 (柱とかさ)・根元の土煙が順に出ます。\n"
               u"オブジェクトを選んでいればその位置に置きます。雲はロケータの上 (Y) へ上がります。")
    params = _params()

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, KIND)


class NukeUpdateTool(BaseTool):
    label = u"核爆発を更新"
    tab = TAB
    category = CATEGORY
    order = 922
    tooltip = (u"選択した核爆発を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (煙の数) を変えた時に使います。爆心のロケータはそのまま。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.rebuild_effects(cmds, mu, ctx, _selected(cmds, mu, u"更新する"))


def _selected(cmds, mu, purpose):
    roots = [r for r in mu.selected_effect_roots()
             if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) == EFFECT_TYPE]
    if not roots:
        raise ToolError(u"%s核爆発 (dfx_nukeN、またはその中の物) を選択してください" % purpose)
    return roots


# ---------------------------------------------------------------- 構築 (Maya)
def _create_center(cmds, mu, root, v, camera=None, target=None):
    """爆心のロケータ。オブジェクトを選んでいれば、その位置に置く (付いては動かない)。"""
    prefix = wx.short(root)
    loc = cmds.spaceLocator(name=prefix + "_center")[0]
    loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
    shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 13)  # 赤
    for a in ("localScaleX", "localScaleY", "localScaleZ"):
        cmds.setAttr(shape + "." + a, max(v["size"] * 0.3, 0.1))
    if target:
        pos = cmds.xform(target, query=True, worldSpace=True, translation=True)
        cmds.xform(loc, worldSpace=True, translation=pos)
    mu.tag(loc)
    wx.link(cmds, root, wx.EMITTER_ATTR, loc)
    return loc


def _head(root):
    """爆心 ($g0 と軸 $gR0 / $gR1 (上) / $gR2) と、全体の時刻の値 (nuke_state と同じ)。"""
    r = wx.short(root)
    dm = wx.track_name(root, wx.EMITTER_ATTR)
    return wx.mel_head(root, need_camera=True, need_wind=False) + wx.mel_rows(dm, "g") + u"""
vector $g0 = <<{dm}.outputTranslateX, {dm}.outputTranslateY, {dm}.outputTranslateZ>>;
float $a = (frame - {r}.dfx_start_frame) * max({r}.dfx_speed, 0.001);
float $ag = max($a, 0);
float $S0 = {r}.dfx_size;
float $tau = max({r}.dfx_rise_frames, 1.0) / 3.0;
float $rise = 1 - exp(-$ag / $tau);
float $life = max({r}.dfx_life_frames, 1.0);
float $H = $S0 * {r}.dfx_cloud_height * $rise;
int $alive = ($a >= 0) && ($a <= $life);
""".format(r=r, dm=dm)


def _place(x, y, z):
    return u"vector $p = $g0 + $gR0 * ({x}) + $gR1 * ({y}) + $gR2 * ({z});\n".format(x=x, y=y, z=z)


def _rot(r, k):
    return u"""float $rx = {h1} * 360 + {r}.dfx_spin * $ag;
float $ry = {h2} * 360;
float $rz = {h3} * 360 + {r}.dfx_spin * 0.5 * $ag;
""".format(r=r, h1=H("$i", 0, k), h2=H("$i", 0, k + 1), h3=H("$i", 0, k + 2))


def _flash_body(r):
    return (u"""float $fl = max({r}.dfx_flash_frames, 0.01);
float $sz = $S0 * {r}.dfx_flash_size * sin(3.1415927 * clamp(0, 1, $a / $fl));
int $vis = ($a >= 0) && ($a <= $fl) && ($sz > 0);
""".format(r=r) + _place("0", "$S0 * 0.5", "0") + u"vector $S = <<$sz, $sz, $sz>>;\n"
            + wx.mel_billboard("$p"))


def _flat_axes():
    return u"vector $X = $gR0; vector $Y = $gR1; vector $Z = $gR2;\n"


def _shock_body(r):
    return (u"""float $k = clamp(0, 1, $a / max({r}.dfx_shock_frames, 0.01));
float $sz = $S0 * {r}.dfx_shock_size * (1 - pow(1 - $k, 3));
int $vis = ($a >= 0) && ($a < {r}.dfx_shock_frames) && ($sz > 0);
""".format(r=r) + _place("0", "$S0 * 0.02", "0") + u"vector $S = <<$sz, $sz, $sz>>;\n" + _flat_axes())


def _dome_body(r):
    return (u"""float $k = clamp(0, 1, $a / max({r}.dfx_shock_frames * 0.5, 0.01));
float $sz = $S0 * {r}.dfx_shock_size * 0.6 * (1 - pow(1 - $k, 2));
int $vis = ($a >= 0) && ($a < {r}.dfx_shock_frames * 0.5) && ($sz > 0) && ({r}.dfx_dome_opacity > 0);
vector $p = $g0;
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r) + _flat_axes())


def _fireball_body(r):
    # nuke_fireball は回転の乱数を粒 0 の 1〜3 番で作る
    return (u"""float $fb = max({r}.dfx_fireball_frames, 1.0);
float $sz = $S0 * {r}.dfx_fireball_size * (1 - exp(-$ag / 3.0));
if ($a > 0.6 * $fb) $sz = $sz * max(0, ($fb - $a) / (0.4 * $fb));
int $vis = ($a >= 0) && ($a <= $fb) && ($sz > 0);
""".format(r=r) + _place("0", "$H + $sz * 0.5", "0") + u"vector $S = <<$sz, $sz, $sz>>;\n" + _rot(r, 1))


def _stem_body(r, count):
    return (u"""float $s = ($i + 0.5) / {n};
float $th = 6.2831853 * {h1} + 0.03 * $ag;
float $rr = $S0 * {r}.dfx_stem_width * 0.35 * {h2};
float $grow = 1 - exp(-$ag / ($tau * 0.5));
float $sz = $S0 * {r}.dfx_stem_width * (0.55 + 0.45 * {h3}) * (0.5 + 0.5 * (1 - $s)) * $grow;
int $vis = $alive && ($sz > 0);
""".format(r=r, n=repr(float(max(count, 1))), h1=H("$i", 0, 11), h2=H("$i", 0, 12), h3=H("$i", 0, 13))
            + _place("cos($th) * $rr", "$H * $s * 0.95", "sin($th) * $rr")
            + u"vector $S = <<$sz, $sz, $sz>>;\n" + _rot(r, 14))


def _cap_body(r, count):
    return (u"""float $th = 6.2831853 * ($i + ({h1} - 0.5)) / {n};
float $ph = 6.2831853 * {h2} - deg_to_rad({r}.dfx_roll) * $ag;
float $big = $S0 * {r}.dfx_cap_width * (0.3 + 0.7 * $rise);
float $tube = $big * 0.5;
float $out = $big + $tube * cos($ph);
float $sz = $S0 * {r}.dfx_cap_width * 0.45 * (0.7 + 0.5 * {h3}) * (0.35 + 0.65 * $rise);
int $vis = $alive && ($sz > 0);
""".format(r=r, n=repr(float(max(count, 1))), h1=H("$i", 0, 21), h2=H("$i", 0, 22), h3=H("$i", 0, 23))
            + _place("cos($th) * $out", "$H + $tube * 0.7 * sin($ph)", "sin($th) * $out")
            + u"vector $S = <<$sz, $sz, $sz>>;\n" + _rot(r, 24))


def _base_body(r, count):
    return (u"""float $th = 6.2831853 * ($i + ({h1} - 0.5)) / {n};
float $dist = $S0 * {r}.dfx_base_spread * (1 - exp(-$ag / ($tau * 0.7))) * (0.75 + 0.5 * {h2});
float $sz = $S0 * {r}.dfx_base_size * (0.5 + 0.5 * {h3}) * (0.3 + 0.7 * (1 - exp(-$ag / ($tau * 0.5))));
int $vis = $alive && ($sz > 0);
""".format(r=r, n=repr(float(max(count, 1))), h1=H("$i", 0, 31), h2=H("$i", 0, 32), h3=H("$i", 0, 33))
            + _place("cos($th) * $dist", "$sz * 0.35", "sin($th) * $dist")
            + u"vector $S = <<$sz, $sz, $sz>>;\n" + _rot(r, 34))


def _driven_toon(cmds, mu, name, owned):
    """アニメ調の 2 色のマテリアル (色・影の色・透明度はエクスプレッションで決める)。(shader, sg, ramp)"""
    shader, sg = mu.create_surface_shader(name, (1, 1, 1))
    info = cmds.shadingNode("samplerInfo", asUtility=True, name=name + "_facing")
    ramp = cmds.shadingNode("ramp", asTexture=True, name=name + "_toon")
    cmds.setAttr(ramp + ".type", 0)
    cmds.setAttr(ramp + ".interpolation", 0)
    cmds.connectAttr(info + ".facingRatio", ramp + ".vCoord")
    for idx, pos in ((0, 0.0), (1, 0.45)):
        cmds.setAttr("%s.colorEntryList[%d].position" % (ramp, idx), pos)
    cmds.connectAttr(ramp + ".outColor", shader + ".outColor", force=True)
    owned.extend([shader, sg, info, ramp])
    return shader, sg, ramp


def _shading_expression(root, cloud_shader, cloud_ramp, shock_shader, dome_shader):
    """雲の色 (火 → 煙)・薄れ、衝撃波・ドームの濃さ (nuke_colors / nuke_shock / nuke_dome と同じ)。"""
    r = wx.short(root)
    code = [u"""float $a = (frame - {r}.dfx_start_frame) * max({r}.dfx_speed, 0.001);
float $life = max({r}.dfx_life_frames, 1.0);
float $k = clamp(0, 1, $a / max({r}.dfx_cool_frames, 1.0));
float $fade = clamp(0, 1, ($life - $a) / (0.25 * $life));
float $t = 1 - {r}.dfx_opacity * $fade;
""".format(r=r)]
    if cloud_shader:
        for idx, (fire, cloud) in ((1, ("fire_color", "cloud_color")), (0, ("fire_shade", "cloud_shade"))):
            for c in "RGB":
                code.append(u"{n}.colorEntryList[{i}].color{c} = {r}.dfx_{f}{c} + ({r}.dfx_{k}{c} - {r}.dfx_{f}{c}) * $k;\n"
                            .format(n=wx.short(cloud_ramp), i=idx, c=c, r=r, f=fire, k=cloud))
        code.append(u"{s}.outTransparencyR = $t; {s}.outTransparencyG = $t; {s}.outTransparencyB = $t;\n"
                    .format(s=wx.short(cloud_shader)))
    code.append(u"""float $ks = clamp(0, 1, $a / max({r}.dfx_shock_frames, 0.01));
float $ts = 1 - {r}.dfx_opacity * (1 - $ks);
{s}.outTransparencyR = $ts; {s}.outTransparencyG = $ts; {s}.outTransparencyB = $ts;
float $kd = clamp(0, 1, $a / max({r}.dfx_shock_frames * 0.5, 0.01));
float $td = 1 - {r}.dfx_dome_opacity * pow(1 - $kd, 2);
{d}.outTransparencyR = $td; {d}.outTransparencyG = $td; {d}.outTransparencyB = $td;
""".format(r=r, s=wx.short(shock_shader), d=wx.short(dome_shader)))
    return u"".join(code)


def build(cmds, mu, root, v):
    from . import weather
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    plug = "%s.%s" % (root, mu.PARAM_PREFIX)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    pts, faces = wx.disc_points(24, 0.5)
    flash_src = wx.make_mesh(cmds, prefix + "_flashSrc", src_grp, pts, faces)
    ring, ring_faces = wx.disc_points(48, 0.5, 0.44)
    shock_src = wx.make_mesh(cmds, prefix + "_shockSrc", src_grp, [(x, 0.0, y) for x, y, _ in ring], ring_faces)
    hpts, hfaces = hemisphere_points()
    dome_src = wx.make_mesh(cmds, prefix + "_domeSrc", src_grp, hpts, hfaces)
    fire_src = weather._puff_mesh(cmds, prefix + "_fireSrc", src_grp)
    cloud_src = weather._puff_mesh(cmds, prefix + "_cloudSrc", src_grp)
    cmds.setAttr(src_grp + ".visibility", 0)

    _, sg = wx.flat_shader(cmds, mu, root, prefix + "_flash_mat", owned, color_attr="flash_color",
                           opacity_attr="opacity")
    cmds.sets(flash_src, edit=True, forceElement=sg)
    shock_shader, sg = mu.create_surface_shader(prefix + "_shock_mat", (1, 1, 1))
    cmds.connectAttr(plug + "shock_color", shock_shader + ".outColor", force=True)
    owned.extend([shock_shader, sg])
    cmds.sets(shock_src, edit=True, forceElement=sg)
    dome_shader, sg = mu.create_surface_shader(prefix + "_dome_mat", (1, 1, 1))
    cmds.connectAttr(plug + "shock_color", dome_shader + ".outColor", force=True)
    owned.extend([dome_shader, sg])
    cmds.sets(dome_src, edit=True, forceElement=sg)
    sg = weather._toon_shader(cmds, mu, root, prefix + "_fire_mat", owned, color="fire_color",
                              shade="fire_shade")
    cmds.sets(fire_src, edit=True, forceElement=sg)
    cloud_shader, sg, cloud_ramp = _driven_toon(cmds, mu, prefix + "_cloud_mat", owned)
    cmds.sets(cloud_src, edit=True, forceElement=sg)

    head = _head(root)
    r = prefix
    single = ((flash_src, "flash", _flash_body(r), True), (shock_src, "shock", _shock_body(r), True),
              (dome_src, "dome", _dome_body(r), True), (fire_src, "fireball", _fireball_body(r), False))
    for src, name, body, axes in single:
        nodes = wx.make_instances(cmds, root, src, "%s_%s" % (prefix, name), 1, owned)
        store = wx.mel_store_transform(0) if axes else weather._store_euler(0)
        wx.chunked_expressions(cmds, root, "%s_%s" % (prefix, name), nodes, wx.TRANSFORM_OUTPUTS, head,
                               (lambda code: lambda a, b: code)(body + store), owned)
    for name, count, body_fn in (("stem", int(v["stem_count"]), _stem_body),
                                 ("cap", int(v["cap_count"]), _cap_body),
                                 ("base", int(v["base_count"]), _base_body)):
        if count <= 0:
            continue
        nodes = wx.make_instances(cmds, root, cloud_src, "%s_%s" % (prefix, name), count, owned)
        body = body_fn(r, count)
        wx.chunked_expressions(cmds, root, "%s_%s" % (prefix, name), nodes, wx.TRANSFORM_OUTPUTS, head,
                               (lambda code: lambda a, b: code + weather._store_euler(a))(body), owned)
    owned.append(cmds.expression(name=prefix + "_shading", alwaysEvaluate=True, unitConversion="all",
                                 string=_shading_expression(root, cloud_shader, cloud_ramp, shock_shader,
                                                            dome_shader)))
    wx.finish(cmds, mu, root, owned)


def _live():
    return tuple(p.name for p in _params() if p.name not in COUNT_PARAMS)


KIND = wx.register(wx.Kind(EFFECT_TYPE, NukeTool, "dfx_nuke#", u"核爆発", _live(), COUNT_PARAMS,
                           build=build, create=_create_center, need_camera=True, need_wind=False))
