# -*- coding: utf-8 -*-
"""天候タブ (稲妻): 電撃。中心のロケータ (_center) のまわりに、稲妻が流れ続ける。

    まとわりつく  中心のまわりの球 (ロケータの大きさで楕円体になる) の上を、稲妻が這う
    外へ放電      中心から外へ向かって、稲妻が飛び出す

作成時にオブジェクトを選んでいれば、_center はそのオブジェクトに付いて動き、大きさはオブジェクトの
バウンディングボックスに合わせる (オブジェクトの形に沿ってまとわりつく)。
線ごとに「形が変わる間隔」で向き・形が変わる (変わる時は線ごとにずれる)。

構成 (稲妻と同じく、ジョイントにスキンした帯。ベイクすれば FBX でも使える):
    dfx_electricN            ルート (パラメータ。カメラとつながる)
      dfx_electricN_center   中心 (動かしてよい。大きさで形が変わる)
      dfx_electricN_bolt     帯 (線ごとの点に 2 頂点)。アニメは円柱 (点ごとに輪)
      dfx_electricN_glow     2 本目の帯 (実写: 外側の光 / アニメ: 円柱の前の芯)
      dfx_electricN_joints   帯の点のジョイント
計算は motion.py の electric_* と同じ式 (Blender 版も同じ関数を使う)。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param
from . import _weather as wx
from . import lightning as lt
from .. import motion as mo

TAB = wx.TAB
CATEGORY = lt.CATEGORY
REBUILD = ("count", "segments", "glow_width")
ANIME_REBUILD = REBUILD + ("sides",)
MIN_SHAPE = 0.25   # オブジェクトに合わせた大きさの、一番小さい軸 (一番大きい軸に対する割合。板でもつぶれない)


def _params(real):
    def d(a, b):
        return a if real else b
    glow = dict((p.name, p) for p in lt._params(real))
    extra = ["glow_color", "glow_width", "glow_opacity"] + ([] if real else ["sides"])
    if real:   # 稲妻より近くで見るので、光の帯は細めに
        glow["glow_width"].default = 6.0
    return ([Param("mode", u"形", mo.ELECTRIC_CLING, kind="choice", choices=list(mo.ELECTRIC_MODES),
                   tooltip=u"まとわりつく: 中心のまわりを稲妻が這う\n外へ放電: 中心から外へ稲妻が飛び出す"),
             Param("count", u"本数", d(8, 6), minimum=1, maximum=60, tooltip=u"同時に出ている稲妻の数"),
             Param("start_frame", u"開始フレーム", 1.0, decimals=2),
             Param("end_frame", u"終わりのフレーム", 60.0, decimals=2),
             Param("change_frames", u"形が変わる間隔 (フレーム)", d(2.0, 3.0), minimum=0.5, step=1.0, decimals=1,
                   tooltip=u"何フレームごとに稲妻の向き・形が変わるか (小さいほどバチバチする)。\n"
                           u"変わる時は線ごとにずれる"),
             Param("flicker", u"ちらつき", d(0.3, 0.2), minimum=0.0, maximum=1.0, step=0.1,
                   tooltip=u"光っている間に消えるフレームの割合 (0 でずっと光る)"),
             Param("radius", u"まとわる半径", 1.2, minimum=0.0, step=0.1,
                   tooltip=u"まとわりつく: 稲妻が這う球の半径 (中心のロケータの大きさが 1)。\n"
                           u"オブジェクトに合わせた時は、1 でバウンディングボックスに内接する大きさ"),
             Param("arc", u"弧の角度", d(120.0, 100.0), minimum=0.0, maximum=360.0, step=10.0, decimals=1,
                   tooltip=u"まとわりつく: 1 本の稲妻が球のまわりを這う角度"),
             Param("inner", u"出る所の半径", 0.0, minimum=0.0, step=0.1,
                   tooltip=u"外へ放電: 稲妻が出る所 (0 で中心から。1 でロケータの大きさの球の表面から)"),
             Param("length", u"放電の長さ", 2.5, minimum=0.0, step=0.1,
                   tooltip=u"外へ放電: 稲妻の長さ (中心のロケータの大きさが 1)"),
             Param("length_random", u"長さのばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1,
                   tooltip=u"稲妻ごとに、弧の角度・放電の長さが短くなる割合"),
             Param("segments", u"点の数", d(24, 8), minimum=3, maximum=200,
                   tooltip=u"1 本の稲妻の点の数 (多いほど細かく折れ曲がる)"),
             Param("jag", u"ギザギザの大きさ", d(0.08, 0.12), minimum=0.0, step=0.01, decimals=3,
                   tooltip=u"横へのずれ (稲妻の長さの割合)"),
             Param("width", u"太さ", d(0.02, 0.12), minimum=0.0, step=d(0.01, 0.02), decimals=3),
             Param("taper", u"先の細さ", d(0.3, 0.4), minimum=0.0, maximum=1.0, step=0.1,
                   tooltip=u"先での太さ (太い所に対する割合)。まとわりつく時は両端が細くなる"),
             Param("color", u"色", d((0.8, 0.9, 1.0), (0.45, 0.8, 1.0)), kind="color")]
            + [glow[name] for name in extra]
            + [Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1),
               Param("seed", u"シード", 1, minimum=0, maximum=99999)])


class ElectricRealTool(BaseTool):
    label = u"電撃 (実写)"
    tab = TAB
    category = CATEGORY
    order = 888
    tooltip = (u"中心のロケータのまわりに、細い稲妻がまとわりつきます (「形」で、中心から外への放電にもできます)。\n"
               u"オブジェクトを選んで押すと、そのオブジェクトに付いて動き、大きさに合わせてまとわりつきます。\n"
               u"中心のロケータ (_center) の大きさで、まとわりつく形 (楕円体) が変わります。")
    params = _params(True)

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, REAL)


class ElectricAnimeTool(BaseTool):
    label = u"電撃 (アニメ)"
    tab = TAB
    category = CATEGORY
    order = 889
    tooltip = (u"中心のロケータのまわりに、太い円柱の稲妻がまとわりつきます (「形」で、中心から外への放電にもできます)。\n"
               u"オブジェクトを選んで押すと、そのオブジェクトに付いて動き、大きさに合わせてまとわりつきます。\n"
               u"中心のロケータ (_center) の大きさで、まとわりつく形 (楕円体) が変わります。")
    params = _params(False)

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, ANIME)


# ---------------------------------------------------------------- 構築 (Maya)
def fit_box(bmin, bmax):
    """バウンディングボックス → (中心, 半分の大きさ)。薄い軸は一番大きい軸の MIN_SHAPE まで広げる。"""
    center = tuple((a + b) * 0.5 for a, b in zip(bmin, bmax))
    half = [max((b - a) * 0.5, 0.0) for a, b in zip(bmin, bmax)]
    big = max(half) if max(half) > 1e-6 else 1.0
    return center, tuple(max(x, big * MIN_SHAPE) for x in half)


def _create_center(cmds, mu, root, v, camera=None, target=None):
    """中心のロケータ (_center)。オブジェクトを選んでいれば、それに付いて動き、大きさをそれに合わせる。"""
    prefix = wx.short(root)
    loc = cmds.spaceLocator(name=prefix + "_center")[0]
    loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
    shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)  # 黄
    if target:
        # オブジェクトのローカルの空間のバウンディングボックス (オブジェクトの回転・大きさにも付いていく)
        center, half = fit_box(*_target_box(cmds, target))
        cmds.connectAttr(target + ".worldMatrix[0]", loc + ".offsetParentMatrix")
        cmds.setAttr(loc + ".translate", *center, type="double3")
        cmds.setAttr(loc + ".scale", *half, type="double3")
    else:
        cmds.setAttr(loc + ".translate", 0.0, 1.0, 0.0, type="double3")
    mu.tag(loc)
    wx.link(cmds, root, wx.EMITTER_ATTR, loc)
    return [loc]


def _target_box(cmds, target):
    """オブジェクトのローカルの空間のバウンディングボックス (bmin, bmax)。

    シェイプがあればその箱 (ローカルの空間)。グループなどは transform の箱 (親の空間) をローカルに戻す。
    """
    shapes = cmds.listRelatives(target, shapes=True, noIntermediate=True, fullPath=True) or []
    if shapes:
        mins = [cmds.getAttr(s + ".boundingBoxMin")[0] for s in shapes]
        maxs = [cmds.getAttr(s + ".boundingBoxMax")[0] for s in shapes]
        return (tuple(min(p[k] for p in mins) for k in range(3)), tuple(max(p[k] for p in maxs) for k in range(3)))
    return _local_box(cmds.getAttr(target + ".boundingBoxMin")[0], cmds.getAttr(target + ".boundingBoxMax")[0],
                      cmds.getAttr(target + ".matrix"))


def _local_box(bmin, bmax, m):
    """親の空間の箱 (bmin, bmax) の 8 つの角を、行列 m (ローカル → 親) の逆でローカルに戻した箱。"""
    inv = mo.inverse(m)
    pts = [mo.xf(inv, (x, y, z)) for x in (bmin[0], bmax[0]) for y in (bmin[1], bmax[1])
           for z in (bmin[2], bmax[2])]
    return tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3))


def _head(root, b):
    """カメラ・中心 ($C と大きさ込みの軸 $a0 $a1 $a2)・線 b の今の回 $c / 光っているか $on。"""
    r = wx.short(root)
    dm = wx.track_name(root, wx.EMITTER_ATTR)
    return wx.mel_head(root, need_camera=True, need_wind=False) + wx.mel_rows(dm, "e") + u"""
vector $C = <<{dm}.outputTranslateX, {dm}.outputTranslateY, {dm}.outputTranslateZ>>;
vector $a0 = $eR0 * {dm}.outputScaleX;
vector $a1 = $eR1 * {dm}.outputScaleY;
vector $a2 = $eR2 * {dm}.outputScaleZ;
float $f = frame;
int $b = {b};
float $chg = max({r}.dfx_change_frames, 0.5);
float $off = {h40} * $chg;
int $c = -1;
float $ts = 0;
if ($f >= {r}.dfx_start_frame && $f <= {r}.dfx_end_frame) {{
    $c = floor(($f - {r}.dfx_start_frame + $off) / $chg);
    $ts = {r}.dfx_start_frame - $off + $c * $chg;
}}
int $cid = $c * {cid} + $b + 1;
int $on = 0;
if ($c >= 0) {{
    float $ph = floor($f - $ts);
    if ($ph <= 0 || {h90} >= {r}.dfx_flicker) $on = 1;
}}
""".format(dm=dm, r=r, b=b, cid=mo.ELECTRIC_ID, h40=lt._hash("$b", 0, 40), h90=lt._hash("$cid", "$ph", 90))


def _expression(style, root, n, b, shaders):
    """線 b の行を $o に (motion.electric_rows の 1 本分と同じ)。"""
    r = wx.short(root)
    n1 = repr(float(max(n - 1, 1)))
    code = _head(root, b) + u"""float $o[];
vector $P[];
float $W[];
int $k; int $j;
for ($k = 0; $k < {n}; $k++) {{ $P[$k] = $C; $W[$k] = 0; }}
if ($on) {{
    float $z = 2 * {h41} - 1;
    float $phi = {tau} * {h42};
    float $rr = sqrt(max(0, 1 - $z * $z));
    vector $d0 = <<$rr * cos($phi), $z, $rr * sin($phi)>>;
    float $kl = 1 - {r}.dfx_length_random * {h44};
    if ({r}.dfx_mode == 1) {{
        vector $top = $d0 * {r}.dfx_inner;
        vector $gnd = $d0 * ({r}.dfx_inner + {r}.dfx_length * $kl);
        vector $dd = $gnd - $top;
        float $len = mag($dd);
        vector $d = <<0, -1, 0>>;
        if ($len > 1e-9) $d = unit($dd);
""".format(n=n, r=r, tau=repr(mo.TAU), h41=lt._hash("$b", "$c", 41), h42=lt._hash("$b", "$c", 42),
           h44=lt._hash("$b", "$c", 44)) + wx.mel_ortho("$A", "$d", "<<1, 0, 0>>") + u"""        vector $B = cross($d, $A);
        float $jg = $len * {r}.dfx_jag;
        for ($k = 0; $k < {n}; $k++) {{
            float $u = $k / {n1};
""".format(r=r, n=n, n1=n1) + lt._offset(style, "$k", n, "$u", "$cid", False) + u"""            vector $q = $top + ($gnd - $top) * $u + $A * ($oa * $jg) + $B * ($ob * $jg);
            $P[$k] = $C + $a0 * $q.x + $a1 * $q.y + $a2 * $q.z;
            $W[$k] = {r}.dfx_width * (1 - (1 - {r}.dfx_taper) * $u);
        }}
    }} else {{
""".format(r=r) + wx.mel_ortho("$e1", "$d0", "<<1, 0, 0>>") + u"""        vector $e2 = cross($d0, $e1);
        float $ps = {tau} * {h43};
        vector $tg = $e1 * cos($ps) + $e2 * sin($ps);
        vector $nr = cross($d0, $tg);
        float $ang = deg_to_rad({r}.dfx_arc) * $kl;
        float $R = {r}.dfx_radius;
        float $jg = $R * $ang * {r}.dfx_jag;
        for ($k = 0; $k < {n}; $k++) {{
            float $u = $k / {n1};
""".format(r=r, n=n, n1=n1, tau=repr(mo.TAU), h43=lt._hash("$b", "$c", 43)) + \
        lt._offset(style, "$k", n, "$u", "$cid", False) + u"""            vector $dr = $d0 * cos($ang * $u) + $tg * sin($ang * $u);
            vector $q = $dr * ($R + abs($ob) * $jg) + $nr * ($oa * $jg);
            $P[$k] = $C + $a0 * $q.x + $a1 * $q.y + $a2 * $q.z;
            $W[$k] = {r}.dfx_width * (1 - (1 - {r}.dfx_taper) * abs(2 * $u - 1));
        }}
    }}
}}
""".format(r=r) + lt._rows_code(n, r, 0, style)
    if b == 0:   # 不透明度 (全部の線で 1 つのシェーダー)
        code += u"""float $tb = 1 - {r}.dfx_opacity;
{s}.outTransparencyR = $tb; {s}.outTransparencyG = $tb; {s}.outTransparencyB = $tb;
float $tg2 = 1 - {r}.dfx_opacity * {r}.dfx_glow_opacity;
{g}.outTransparencyR = $tg2; {g}.outTransparencyG = $tg2; {g}.outTransparencyB = $tg2;
""".format(r=r, s=wx.short(shaders[0]), g=wx.short(shaders[1]))
    return code


def build(cmds, mu, root, v, style):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    n, count = int(v["segments"]), int(v["count"])
    rn = mo.strand_rows(style, n)
    joints, shaders = lt.strand_meshes(cmds, mu, root, v, style, [rn] * count, owned)
    outputs = wx.TRANSFORM_OUTPUTS[:9]
    for b in range(count):
        owned.append(cmds.expression(name="%s_bolt_%02d" % (prefix, b + 1), alwaysEvaluate=True,
                                     unitConversion="all",
                                     string=_expression(style, root, n, b, shaders)
                                     + wx.mel_writes(joints[b * rn:(b + 1) * rn], outputs)))
    wx.finish(cmds, mu, root, owned)


def _live(tool, rebuild):
    return tuple(p.name for p in tool.params if p.name not in rebuild)


REAL = wx.register(wx.Kind("electric_real", ElectricRealTool, "dfx_electric#", u"電撃 (実写)",
                           _live(ElectricRealTool, REBUILD), REBUILD,
                           build=lambda cmds, mu, root, v: build(cmds, mu, root, v, mo.STYLE_REAL),
                           create=_create_center, need_camera=True, need_wind=False))
ANIME = wx.register(wx.Kind("electric_anime", ElectricAnimeTool, "dfx_electricAnime#", u"電撃 (アニメ)",
                            _live(ElectricAnimeTool, ANIME_REBUILD), ANIME_REBUILD,
                            build=lambda cmds, mu, root, v: build(cmds, mu, root, v, mo.STYLE_ANIME),
                            create=_create_center, need_camera=True, need_wind=False))
STYLES = {"electric_real": mo.STYLE_REAL, "electric_anime": mo.STYLE_ANIME}
