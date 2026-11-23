# -*- coding: utf-8 -*-
"""天候タブ: 稲妻。雲のロケータ (_top) から地面のロケータ (_ground) へ、何回か落ちる。

    稲妻 (実写)    細い線。細かいギザギザと枝がたくさん。まわりに青白い光の帯
    稲妻 (アニメ)  太い帯。大きなジグザグと少しの枝。真ん中に白い芯

回ごとに形と落ちる所 (落ちる所のばらつき) が変わる。1 回の稲妻は、雲から地面へ
「伸びるフレーム数」で伸びてから、ちらつきながら「光るフレーム数」だけ光って消える。

構成 (火花 (尾を引く) と同じく、ジョイントにスキンした帯。ベイクすれば FBX でも使える):
    dfx_lightningN            ルート (パラメータ。カメラとつながる)
      dfx_lightningN_top      雲の点 (動かしてよい)
      dfx_lightningN_ground   落ちる所 (動かしてよい。作成時にオブジェクトを選んでいればその位置)
      dfx_lightningN_bolt     帯 (本筋 → 枝の順の点ごとに 2 頂点)
      dfx_lightningN_glow     2 本目の帯 (実写: 外側の光 / アニメ: 白い芯)
      dfx_lightningN_joints   帯の点のジョイント
計算は motion.py の lightning_* と同じ式 (Blender 版も同じ関数を使う)。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param
from . import _weather as wx
from .. import motion as mo

TAB = wx.TAB
CATEGORY = u"稲妻"
REBUILD = ("segments", "branches", "branch_segments", "glow_width")
CREATE_ONLY = ("height",)


def _hash(i, c, k):
    return (u"fmod(abs(sin(({i}) * 12.9898 + ({c}) * 78.233 + ({k}) * 37.719 + $sd * 4.1414)"
            u" * 43758.5453), 1.0)").format(i=i, c=c, k=k)


def _params(real):
    def d(a, b):
        return a if real else b
    glow = ([Param("glow_color", u"光の色", (0.55, 0.65, 1.0), kind="color",
                   tooltip=u"まわりのぼんやりした光の帯の色"),
             Param("glow_width", u"光の広がり", 14.0, minimum=0.0, step=1.0,
                   tooltip=u"光の帯の太さ (線の太さの倍率)"),
             Param("glow_opacity", u"光の濃さ", 0.35, minimum=0.0, maximum=1.0, step=0.1)]
            if real else
            [Param("glow_color", u"芯の色", (1.0, 1.0, 1.0), kind="color", tooltip=u"帯の真ん中の芯の色"),
             Param("glow_width", u"芯の太さ", 0.4, minimum=0.0, maximum=1.0, step=0.1,
                   tooltip=u"芯の太さ (帯の太さの倍率。0 で芯なし)"),
             Param("glow_opacity", u"芯の濃さ", 1.0, minimum=0.0, maximum=1.0, step=0.1)])
    return ([Param("count", u"落ちる回数", 5, minimum=1, maximum=200),
             Param("start_frame", u"開始フレーム", 1.0, decimals=2, tooltip=u"1 回目が落ちるフレーム"),
             Param("interval", u"間隔 (フレーム)", 30.0, minimum=1.0, step=5.0, decimals=1,
                   tooltip=u"次が落ちるまでのフレーム数"),
             Param("interval_random", u"間隔のばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1),
             Param("duration", u"光るフレーム数", d(6.0, 8.0), minimum=0.5, step=1.0, decimals=1),
             Param("grow_frames", u"伸びるフレーム数", d(2.0, 3.0), minimum=0.0, step=1.0, decimals=1,
                   tooltip=u"雲から地面まで伸びるのにかかるフレーム数 (0 で一瞬)"),
             Param("flicker", u"ちらつき", d(0.4, 0.25), minimum=0.0, maximum=1.0, step=0.1,
                   tooltip=u"光っている間に消えるフレームの割合 (0 でずっと光る)"),
             Param("height", u"雲の高さ", 40.0, minimum=0.1, step=5.0,
                   tooltip=u"作成時の雲の点の高さ (作成後は _top を動かす)"),
             Param("spread", u"落ちる所のばらつき", 6.0, minimum=0.0, step=1.0,
                   tooltip=u"回ごとに落ちる所 (と雲の点) が横にずれる幅"),
             Param("segments", u"本筋の点の数", d(48, 12), minimum=3, maximum=200,
                   tooltip=u"多いほど細かく折れ曲がる"),
             Param("branches", u"枝の数", d(6, 2), minimum=0, maximum=30),
             Param("branch_segments", u"枝の点の数", d(14, 5), minimum=2, maximum=60),
             Param("jag", u"ギザギザの大きさ", d(0.05, 0.15), minimum=0.0, step=0.01, decimals=3,
                   tooltip=u"横へのずれ (長さの割合)"),
             Param("branch_length", u"枝の長さ", d(0.35, 0.3), minimum=0.0, step=0.05,
                   tooltip=u"本筋の長さの割合"),
             Param("width", u"太さ", d(0.05, 2.5), minimum=0.0, step=d(0.01, 0.1), decimals=3),
             Param("taper", u"先の細さ", d(0.5, 0.4), minimum=0.0, maximum=1.0, step=0.1,
                   tooltip=u"地面での太さ (雲での太さの割合)"),
             Param("color", u"色", d((0.92, 0.95, 1.0), (1.0, 0.9, 0.25)), kind="color")]
            + glow
            + [Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1),
               Param("seed", u"シード", 1, minimum=0, maximum=99999)])


class LightningRealTool(BaseTool):
    label = u"稲妻 (実写)"
    tab = TAB
    category = CATEGORY
    order = 884
    tooltip = (u"雲の点から地面へ、細かく枝分かれした細い稲妻が何回か落ちます (まわりに青白い光)。\n"
               u"オブジェクトを選んでいれば、そこへ落ちます。カメラを選んでいれば、帯はそのカメラを向きます。")
    params = _params(True)

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, REAL)


class LightningAnimeTool(BaseTool):
    label = u"稲妻 (アニメ)"
    tab = TAB
    category = CATEGORY
    order = 885
    tooltip = (u"雲の点から地面へ、大きくジグザグに折れた太い稲妻が何回か落ちます (真ん中に白い芯)。\n"
               u"オブジェクトを選んでいれば、そこへ落ちます。カメラを選んでいれば、帯はそのカメラを向きます。")
    params = _params(False)

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, ANIME)


# ---------------------------------------------------------------- 構築 (Maya)
def _create_points(cmds, mu, root, v, camera=None, target=None):
    prefix = wx.short(root)
    made = []
    for name, attr, pos, color in (("top", wx.EMITTER_ATTR, (0.0, v["height"], 0.0), 17),
                                   ("ground", wx.STRIKE_ATTR, (0.0, 0.0, 0.0), 13)):
        loc = cmds.spaceLocator(name="%s_%s" % (prefix, name))[0]
        loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
        shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
        cmds.setAttr(shape + ".overrideEnabled", 1)
        cmds.setAttr(shape + ".overrideColor", color)
        cmds.setAttr(loc + ".translate", *pos, type="double3")
        mu.tag(loc)
        wx.link(cmds, root, attr, loc)
        made.append(loc)
    if target:
        p = cmds.xform(target, query=True, worldSpace=True, translation=True)
        cmds.xform(made[1], worldSpace=True, translation=p)
        cmds.xform(made[0], worldSpace=True, translation=(p[0], p[1] + v["height"], p[2]))
    return made


def _head(root):
    """カメラ・雲の点 $top・落ちる所 $gnd と、今光っている回 $c / 始まり $ts、本筋の向き (lightning_frame)。"""
    r = wx.short(root)
    td, gd = wx.track_name(root, wx.EMITTER_ATTR), wx.track_name(root, wx.STRIKE_ATTR)
    return wx.mel_head(root, need_camera=True, need_wind=False) + u"""
vector $top = <<{td}.outputTranslateX, {td}.outputTranslateY, {td}.outputTranslateZ>>;
vector $gnd = <<{gd}.outputTranslateX, {gd}.outputTranslateY, {gd}.outputTranslateZ>>;
float $f = frame;
int $c = -1;
float $ts = 0;
float $tt = {r}.dfx_start_frame;
int $ci;
for ($ci = 0; $ci < {r}.dfx_count; $ci++) {{
    if ($ci > 0) $tt += max(1.0, {r}.dfx_interval * (1 + ({h51} - 0.5) * 2 * {r}.dfx_interval_random));
    if ($tt > $f) break;
    if ($f < $tt + {r}.dfx_duration) {{ $c = $ci; $ts = $tt; break; }}
}}
float $sp = {r}.dfx_spread;
vector $t0 = $top + <<({h52} - 0.5) * $sp, 0, ({h53} - 0.5) * $sp>>;
vector $g0 = $gnd + <<({h54} - 0.5) * 2 * $sp, 0, ({h55} - 0.5) * 2 * $sp>>;
vector $dd = $g0 - $t0;
float $len = mag($dd);
vector $d = <<0, -1, 0>>;
if ($len > 1e-9) $d = unit($dd);
""".format(r=r, td=td, gd=gd, h51=_hash("$ci", 0, 51), h52=_hash("$c", 0, 52), h53=_hash("$c", 0, 53),
           h54=_hash("$c", 0, 54), h55=_hash("$c", 0, 55)) + wx.mel_ortho("$A", "$d", "<<1, 0, 0>>") + u"""vector $B = cross($d, $A);
float $prog = clamp(0, 1, ($f - $ts) / max({r}.dfx_grow_frames, 0.01));
float $jagM = $len * {r}.dfx_jag;
""".format(r=r)


def _offset(style, kv, n, uv, cv, branch):
    """lightning_offset / lightning_branch_offset と同じ: $oa $ob に横のずれ。"""
    if style == mo.STYLE_ANIME:
        cond = (u"{k} != 0".format(k=kv) if branch else u"{k} != 0 && {k} != {last}".format(k=kv, last=n - 1))
        return u"""float $oa = 0; float $ob = 0;
if ({cond}) {{
    float $sg = -1; if ({k} % 2) $sg = 1;
    $oa = $sg * (0.4 + 0.6 * {h70});
    $ob = ({h71} - 0.5) * 0.3;
}}
""".format(cond=cond, k=kv, h70=_hash(kv, cv, 70), h71=_hash(kv, cv, 71))
    if branch:
        code = [u"float $env = min(1.0, 6 * {u});\n".format(u=uv)]
    else:
        code = [u"float $env = min(1.0, min(6 * {u}, 6 * (1 - {u})));\nif ({k} >= {last}) $env = 0;\n"
                .format(u=uv, k=kv, last=n - 1)]
    code.append(u"float $oa = 0; float $ob = 0;\n")
    for o in range(mo.REAL_OCTAVES):
        for ax, var in ((0, "$oa"), (1, "$ob")):
            k = 60 + o * 2 + ax
            code.append(u"""{{ float $x = {u} * {s}; float $xi = floor($x); float $va = {ha}; float $vb = {hb};
  {var} += {w} * (($va + ($vb - $va) * ($x - $xi)) * 2 - 1); }}
""".format(u=uv, s=repr(4.0 * 2 ** o), ha=_hash("$xi", cv, k), hb=_hash("$xi + 1", cv, k), var=var,
           w=repr(0.5 ** o)))
    code.append(u"$oa = $oa * $env; $ob = $ob * $env;\n")
    return u"".join(code)


def _main_point(style, kv, n, dst):
    """本筋の点 kv (lightning_main_point と同じ) を vector dst に。"""
    return u"{{\nfloat $u = {k} / {n1};\n".format(k=kv, n1=repr(float(max(n - 1, 1)))) + \
        _offset(style, kv, n, "$u", "$c", False) + \
        u"{d} = $t0 + ($g0 - $t0) * $u + $A * ($oa * $jagM) + $B * ($ob * $jagM);\n}}\n".format(d=dst)


def _rows_code(count, r, a):
    """$P[] / $W[] (count 個) → 行 (位置・軸・太さ) を $o に (_strand_rows と同じ)。"""
    return u"""for ($j = 0; $j < {n}; $j++) {{
    int $j0 = max(0, $j - 1);
    int $j1 = min({last}, $j + 1);
    vector $p = $P[$j];
    vector $T = $P[$j0] - $P[$j1];
    vector $X = <<0, -1, 0>>;
    if (mag($T) > 1e-9) $X = unit($T);
    vector $tc = $cp - $p;
    if (mag($tc) < 1e-6) $tc = $cdir * -1.0;
""".format(n=count, last=count - 1) + wx.mel_ortho("$Z", "$X", "$tc") + u"""    vector $Y = cross($Z, $X);
    float $w = $W[$j];
""" + wx.mel_euler() + u"""    int $ob = $j * 9;
    $o[$ob] = $p.x; $o[$ob + 1] = $p.y; $o[$ob + 2] = $p.z;
    $o[$ob + 3] = $rx; $o[$ob + 4] = $ry; $o[$ob + 5] = $rz;
    $o[$ob + 6] = $w; $o[$ob + 7] = $w; $o[$ob + 8] = $w;
}
"""


def _main_expression(style, root, n, bolt_shader, glow_shader):
    r = wx.short(root)
    code = _head(root) + u"""float $o[];
vector $P[];
float $W[];
int $k; int $j;
int $last = 0;
if ($c >= 0) $last = min({last}, floor(($prog + 1e-9) * {n1}));
for ($k = 0; $k < {n}; $k++) {{
    vector $pk = $top;
    if ($c >= 0) {{
""".format(n=n, last=n - 1, n1=repr(float(max(n - 1, 1)))) + _main_point(style, "$k", n, "$pk") + u"""    }}
    $P[$k] = $pk;
    $W[$k] = 0;
    if ($c >= 0 && $k <= $last) $W[$k] = {r}.dfx_width * (1 - (1 - {r}.dfx_taper) * ($k / {n1}));
}}
for ($k = $last + 1; $k < {n}; $k++) $P[$k] = $P[$last];
""".format(r=r, n=n, n1=repr(float(max(n - 1, 1)))) + _rows_code(n, r, 0)
    # 濃さ (lightning_alpha と同じ)
    code += u"""float $al = 0;
if ($c >= 0) {{
    float $ph = floor($f - $ts);
    float $on = 0;
    if ($ph <= 0 || {h90} >= {r}.dfx_flicker) $on = 1;
    $al = {r}.dfx_opacity * $on * (1 - 0.5 * clamp(0, 1, ($f - $ts) / max({r}.dfx_duration, 0.01)));
}}
float $tb = 1 - $al;
float $tg = 1 - $al * {r}.dfx_glow_opacity;
{b}.outTransparencyR = $tb; {b}.outTransparencyG = $tb; {b}.outTransparencyB = $tb;
{g}.outTransparencyR = $tg; {g}.outTransparencyG = $tg; {g}.outTransparencyB = $tg;
""".format(r=r, h90=_hash("$c", "$ph", 90), b=wx.short(bolt_shader), g=wx.short(glow_shader))
    return code


def _branch_expression(style, root, n, m, br):
    r = wx.short(root)
    m1 = repr(float(max(m - 1, 1)))
    code = _head(root) + u"""float $o[];
vector $P[];
float $W[];
int $j; int $k;
for ($j = 0; $j < {m}; $j++) {{ $P[$j] = $top; $W[$j] = 0; }}
if ($c >= 0) {{
    int $last = min({last}, floor(($prog + 1e-9) * {n1}));
    int $kb = floor({n1} * (0.15 + 0.6 * {h80}));
    float $tbr = $kb / {n1};
    int $kk = min($kb, $last);
    vector $start = $top;
""".format(m=m, last=n - 1, n1=repr(float(max(n - 1, 1))), h80=_hash(br, "$c", 80)) + \
        _main_point(style, "$kk", n, "$start") + u"""    vector $db = unit($d * 0.8 + ($A * (({h81}) - 0.5) * 2 + $B * (({h82}) - 0.5) * 2) * 0.9);
    float $lb = $len * {r}.dfx_branch_length * (0.4 + 0.6 * {h83});
""".format(r=r, h81=_hash(br, "$c", 81), h82=_hash(br, "$c", 82), h83=_hash(br, "$c", 83)) + \
        wx.mel_ortho("$Ab", "$db", "<<1, 0, 0>>") + u"""    vector $Bb = cross($db, $Ab);
    float $pb = clamp(0, 1, ($prog - $tbr) / 0.3);
    int $blast = 0;
    if ($prog > $tbr) $blast = min({mlast}, floor(($pb + 1e-9) * {m1}));
    float $jagB = $lb * {r}.dfx_jag;
    int $cb = $c * 13 + {br} + 1;
    for ($j = 0; $j < {m}; $j++) {{
        float $ub = $j / {m1};
""".format(r=r, m=m, m1=m1, mlast=m - 1, br=br) + _offset(style, "$j", m, "$ub", "$cb", True) + u"""        $P[$j] = $start + $db * ($lb * $ub) + $Ab * ($oa * $jagB) + $Bb * ($ob * $jagB);
        if ($prog > $tbr && $j <= $blast) $W[$j] = {r}.dfx_width * 0.5 * (1 - (1 - {r}.dfx_taper) * $tbr) * (1 - $ub);
    }}
    for ($j = $blast + 1; $j < {m}; $j++) $P[$j] = $P[$blast];
}}
""".format(r=r, m=m) + _rows_code(m, r, 0)
    return code


def _ribbon(cmds, mu, root, name, joints, strands, half, z, owned):
    """ジョイントにスキンした帯 (点ごとに 2 頂点: ローカル Y の ±half、Z の z)。"""
    points, faces = [], []
    k = 0
    for count in strands:
        for j in range(count):
            points += [(float(k), half, z), (float(k), -half, z)]
            if j < count - 1:
                faces.append([2 * k, 2 * k + 1, 2 * k + 3, 2 * k + 2])
            k += 1
    mesh = wx.make_mesh(cmds, name, root, points, faces)
    owned.append(mesh)
    skin = cmds.skinCluster(joints, mesh, toSelectedBones=True, bindMethod=0, maximumInfluences=1,
                            normalizeWeights=1, name=name + "_skin")[0]
    for i, jnt in enumerate(joints):
        cmds.skinPercent(skin, "%s.vtx[%d:%d]" % (mesh, 2 * i, 2 * i + 1), transformValue=[(jnt, 1.0)])
    owned.append(skin)
    return mesh


def build(cmds, mu, root, v, style):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    n, m, nb = int(v["segments"]), int(v["branch_segments"]), int(v["branches"])
    strands = [n] + [m] * nb
    jgrp = cmds.ls(cmds.group(empty=True, name=prefix + "_joints", parent=root), long=True)[0]
    owned.append(jgrp)
    joints = []
    for k in range(sum(strands)):
        jnt = cmds.createNode("joint", name="%s_jnt_%04d" % (prefix, k), parent=jgrp)
        cmds.setAttr(jnt + ".translate", float(k), 0, 0, type="double3")
        cmds.setAttr(jnt + ".drawStyle", 2)
        joints.append(cmds.ls(jnt, long=True)[0])
    glow = v["glow_width"]
    bolt = _ribbon(cmds, mu, root, prefix + "_bolt", joints, strands, 0.5, 0.0, owned)
    second = _ribbon(cmds, mu, root, prefix + "_glow", joints, strands, 0.5 * glow,
                     -0.05 if glow >= 1 else 0.05, owned)
    plug = "%s.%s" % (root, mu.PARAM_PREFIX)
    shaders = []
    for mesh, color in ((bolt, "color"), (second, "glow_color")):
        shader, sg = mu.create_surface_shader(wx.short(mesh) + "_mat", (1, 1, 1), 1.0)
        cmds.connectAttr(plug + color, shader + ".outColor", force=True)
        cmds.sets(mesh, edit=True, forceElement=sg)
        owned.extend([shader, sg])
        shaders.append(shader)
    outputs = wx.TRANSFORM_OUTPUTS[:9]
    owned.append(cmds.expression(name=prefix + "_main", alwaysEvaluate=True, unitConversion="all",
                                 string=_main_expression(style, root, n, shaders[0], shaders[1])
                                 + wx.mel_writes(joints[:n], outputs)))
    for br in range(nb):
        js = joints[n + br * m:n + (br + 1) * m]
        owned.append(cmds.expression(name="%s_branch_%02d" % (prefix, br + 1), alwaysEvaluate=True,
                                     unitConversion="all",
                                     string=_branch_expression(style, root, n, m, br) + wx.mel_writes(js, outputs)))
    wx.finish(cmds, mu, root, owned)


def _live(tool):
    return tuple(p.name for p in tool.params if p.name not in REBUILD and p.name not in CREATE_ONLY)


REAL = wx.register(wx.Kind("lightning_real", LightningRealTool, "dfx_lightning#", u"稲妻 (実写)",
                           _live(LightningRealTool), REBUILD,
                           build=lambda cmds, mu, root, v: build(cmds, mu, root, v, mo.STYLE_REAL),
                           create=_create_points, need_camera=True, need_wind=False))
ANIME = wx.register(wx.Kind("lightning_anime", LightningAnimeTool, "dfx_lightningAnime#", u"稲妻 (アニメ)",
                            _live(LightningAnimeTool), REBUILD,
                            build=lambda cmds, mu, root, v: build(cmds, mu, root, v, mo.STYLE_ANIME),
                            create=_create_points, need_camera=True, need_wind=False))
STYLES = {"lightning_real": mo.STYLE_REAL, "lightning_anime": mo.STYLE_ANIME}
