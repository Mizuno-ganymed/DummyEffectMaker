# -*- coding: utf-8 -*-
"""天候タブ (砂煙): 足元を漂う砂煙。

    足元の砂煙 (スプライト)  煙の絵の板 (カメラを向く) が、出る場所の真下 (地面) の円の中から湧き続け、
                             地面すれすれを回りながら広がり、ふわりと浮いて大きくなって消える
    足元の砂煙 (もこもこ)    同じ動きを、丸を集めた立体の煙 (中は明るく、ふちは影の色) で

    dfx_footDustN            ルート (パラメータ。カメラ・風とつながる)
      dfx_footDustN_emitter  出る場所 (選んだオブジェクトに付いて動く。高さは「地面の高さ」で決める)
      dfx_footDustN_puffs    砂煙の塊
計算は motion.py の foot_dust と同じ式 (Blender 版も同じ関数を使う)。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param
from . import _weather as wx
from . import weather as mw
from .. import motion as mo

TAB = wx.TAB
CATEGORY = u"砂煙"
H = wx.mel_hash


def _params(mesh):
    out = [Param("count", u"塊の数", 40, minimum=1, maximum=500,
                 tooltip=u"同時に漂う砂煙の塊の数"),
           Param("start_frame", u"開始フレーム", 1.0, decimals=2,
                 tooltip=u"このフレームから少しずつ湧き始める"),
           Param("amount", u"量", 1.0, minimum=0.0, maximum=1.0, step=0.1,
                 tooltip=u"湧いている塊の割合。キーを打てば立ち始め・収まりを作れる"),
           Param("life", u"消えるまでのフレーム数", 60.0, minimum=1.0, step=5.0, decimals=1),
           Param("life_random", u"消えるまでのばらつき", 0.3, minimum=0.0, maximum=0.95, step=0.1),
           Param("radius", u"足元の広さ", 0.5, minimum=0.0, step=0.1,
                 tooltip=u"出る場所の真下の、塊が湧く円の半径"),
           Param("ground", u"地面の高さ", 0.0, step=0.5,
                 tooltip=u"塊が湧く高さ (ワールドの Y)。出る場所が跳んでも砂煙は地面に残る"),
           Param("follow", u"付いて動く割合", 0.3, minimum=0.0, maximum=1.0, step=0.1,
                 tooltip=u"1: 出る場所に付いて動く。0: 湧いた所に残る (歩くと後ろに尾を引く)"),
           Param("spread", u"広がる速さ", 0.008, minimum=0.0, step=0.002, decimals=4,
                 tooltip=u"外へ広がる速さ (1 フレームに進む距離)"),
           Param("swirl", u"回る速さ (度 / フレーム)", 1.5, step=0.5, decimals=1,
                 tooltip=u"出る場所のまわりを回る速さ (塊ごとに右回り・左回り)"),
           Param("rise", u"浮き上がる高さ", 0.25, minimum=0.0, step=0.05),
           Param("wobble", u"上下の揺れ", 0.06, minimum=0.0, step=0.02, decimals=3,
                 tooltip=u"ふわふわ上下する幅"),
           Param("size_start", u"出始めの大きさ", 0.35, minimum=0.0, step=0.05),
           Param("size_end", u"最後の大きさ", 1.0, minimum=0.0, step=0.1),
           Param("size_random", u"大きさのばらつき", 0.3, minimum=0.0, maximum=0.95, step=0.1),
           Param("spin", u"回転 (度 / フレーム)", 1.0, step=0.5, decimals=1),
           Param("color", u"色", (0.84, 0.74, 0.58), kind="color")]
    if mesh:
        out.append(Param("shade_color", u"影の色", (0.6, 0.5, 0.4), kind="color"))
    out.append(Param("opacity", u"不透明度", 0.8 if mesh else 0.55, minimum=0.0, maximum=1.0, step=0.1))
    if not mesh:
        out.append(wx.texture_param("puff", tiling=False))
    return out + mw._common_tail(0.6)


class FootDustTool(BaseTool):
    label = u"足元の砂煙 (スプライト)"
    tab = TAB
    category = CATEGORY
    order = 877.2
    tooltip = (u"足元に砂煙が湧き続け、地面すれすれを回りながら広がって漂い、ふわりと浮いて消えます。\n"
               u"キャラクター (足・腰など) を選んで作ると、出る場所 (_emitter) がそれに付いて動きます。\n"
               u"煙の絵を貼った板がカメラを向きます。風 (dfx_windN) があれば流されます。")
    params = _params(False)

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, SPRITE)


class FootDustMeshTool(BaseTool):
    label = u"足元の砂煙 (もこもこ)"
    tab = TAB
    category = CATEGORY
    order = 877.3
    tooltip = (u"「足元の砂煙 (スプライト)」と同じ動きを、丸を集めた立体の煙 (中は明るく、ふちは影の色) で作ります。\n"
               u"どの向きから見ても形があるので、カメラが回り込む時に。")
    params = _params(True)

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, MESH)


# ---------------------------------------------------------------- 構築 (Maya)
def _create_emitter(cmds, mu, root, v, camera=None, target=None):
    wx.create_emitter(cmds, mu, root, target)


def _head(root, mesh):
    dm = wx.track_name(root, wx.EMITTER_ATTR)
    return (wx.mel_head(root, need_camera=not mesh) + mw._emitter_head(root)
            + u"float $enx = {dm}.outputTranslateX;\nfloat $enz = {dm}.outputTranslateZ;\n".format(dm=dm))


def _foot_body(r):
    """塊 $i の $p / $S / $vis / $a / $c / $turn (motion.foot_dust と同じ)。"""
    tau = repr(mo.TAU)
    code = u"""float $life = max({r}.dfx_life * (1 + ({h1} - 0.5) * 2 * {r}.dfx_life_random), 1.0);
float $u = (frame - {r}.dfx_start_frame) / $life + {h2};
float $c = floor($u);
float $a = ($u - $c) * $life;
float $ts = frame - $a;
float $esx = 0; float $esz = 0;
if ($em != "") {{
    float $emx[] = `getAttr -time $ts ($em + ".worldMatrix[0]")`;
    $esx = $emx[12]; $esz = $emx[14];
}}
float $ex = $esx + ($enx - $esx) * {r}.dfx_follow;
float $ez = $esz + ($enz - $esz) * {r}.dfx_follow;
float $turn = 1;
if ({h4} >= 0.5) $turn = -1;
float $rho = {r}.dfx_radius * sqrt({h3}) + {r}.dfx_spread * $a;
float $th = {tau} * {h5} + $turn * deg_to_rad({r}.dfx_swirl) * $a;
float $grow = 1 - exp(-3.0 * $a / $life);
float $sz = ({r}.dfx_size_start + ({r}.dfx_size_end - {r}.dfx_size_start) * $grow)
            * (1 + ({h6} - 0.5) * 2 * {r}.dfx_size_random);
$sz = $sz * clamp(0, 1, $a / (0.2 * $life));
if ($a > 0.6 * $life) $sz = $sz * max(0, ($life - $a) / (0.4 * $life));
float $hy = {r}.dfx_rise * {h8} * $grow + {r}.dfx_wobble * (0.5 + 0.5 * sin({tau} * $a / 40.0 + {tau} * {h9}));
""".format(r=r, tau=tau, h1=H("$i", 0, 1), h2=H("$i", 0, 2), h3=H("$i", "$c", 3), h4=H("$i", "$c", 4),
           h5=H("$i", "$c", 5), h6=H("$i", "$c", 6), h8=H("$i", "$c", 8), h9=H("$i", "$c", 9))
    code += wx.mel_table("$Ds", "$wd", "$ts")
    code += u"""vector $wd = ($Dnow - $Ds) * $wf;
vector $p = <<$ex + $rho * cos($th), {r}.dfx_ground + $sz * 0.3 + $hy, $ez + $rho * sin($th)>> + $wd;
int $vis = ($ts >= {r}.dfx_start_frame) && ({h7} < {r}.dfx_amount) && ($sz > 1e-9);
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r, h7=H("$i", "$c", 7))
    return code


def build(cmds, mu, root, v, mesh=False):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    if mesh:
        src = mw._puff_mesh(cmds, prefix + "_puffSrc", src_grp)
        sg = mw._toon_shader(cmds, mu, root, prefix + "_mat", owned)
    else:
        pts, faces, uvs = wx.card_points()
        src = wx.make_mesh(cmds, prefix + "_puffSrc", src_grp, pts, faces, uvs)
        shader, sg, tex, _ = wx.texture_shader(cmds, mu, root, prefix + "_mat",
                                               wx.resolve_texture(cmds, v, "puff"), owned,
                                               opacity_attr="opacity")
    cmds.setAttr(src_grp + ".visibility", 0)
    cmds.sets(src, edit=True, forceElement=sg)
    puffs = wx.make_instances(cmds, root, src, prefix + "_puffs", int(v["count"]), owned)
    r = prefix

    def body(a, b):
        code = _foot_body(r)
        if mesh:
            code += u"""float $rx = {h10} * 360 + $turn * {r}.dfx_spin * $a;
float $ry = {h11} * 360;
float $rz = {h12} * 360 + $turn * {r}.dfx_spin * 0.5 * $a;
""".format(r=r, h10=H("$i", "$c", 10), h11=H("$i", "$c", 11), h12=H("$i", "$c", 12))
            return code + mw._store_euler(a)
        roll = u"{h} * 360 + $turn * {r}.dfx_spin * $a".format(h=H("$i", "$c", 10), r=r)
        return code + wx.mel_billboard("$p", roll) + wx.mel_store_transform(a)

    wx.chunked_expressions(cmds, root, prefix + "_puff", puffs, wx.TRANSFORM_OUTPUTS, _head(root, mesh),
                           body, owned)
    wx.finish(cmds, mu, root, owned)


def _live(tool, rebuild):
    return tuple(p.name for p in tool.params if p.name not in rebuild)


SPRITE = wx.register(wx.Kind("foot_dust", FootDustTool, "dfx_footDust#", u"足元の砂煙 (スプライト)",
                             _live(FootDustTool, ("count", "texture")), ("count", "texture"),
                             build=build, create=_create_emitter, need_camera=True))
MESH = wx.register(wx.Kind("foot_dust_mesh", FootDustMeshTool, "dfx_footDustMesh#", u"足元の砂煙 (もこもこ)",
                           _live(FootDustMeshTool, ("count",)), ("count",),
                           build=lambda cmds, mu, root, v: build(cmds, mu, root, v, mesh=True),
                           create=_create_emitter))
