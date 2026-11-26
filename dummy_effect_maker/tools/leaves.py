# -*- coding: utf-8 -*-
"""天候タブ (落ち葉): 舞い散る落ち葉と、地面のカルマン渦に巻かれる落ち葉。

    落ち葉 (舞い散る)  範囲の箱の上から、左右に振れながら (振れの端で傾く) 回って落ち、
                       地面に着いたらしばらく寝て消える。風 (dfx_windN) に流される
    落ち葉 (地面の渦)  出る場所のロケータ (_emitter) から下流 (ロケータの X) へ、渦が左右交互に
                       出て流れる (カルマン渦列)。葉は渦に巻き上げられて回り、渦が弱まると地面に落ちる

葉は 3 色 (色・色 2・色 3)。色ごとに別のメッシュとマテリアル (葉 j の色 = j % 3)。
    dfx_leavesN            ルート (パラメータ。風とつながる)
      dfx_leavesN_area     範囲の箱
      dfx_leavesN_leaves1  葉 (色ごとのグループ。_leaves2 / _leaves3)
    dfx_leafVortexN        ルート
      dfx_leafVortexN_emitter  渦の出る所 (X が下流。動かす・回す・拡大縮小してよい)
計算は motion.py の leaf_* と同じ式 (Blender 版も同じ関数を使う)。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param
from . import _weather as wx
from .. import motion as mo

TAB = wx.TAB
CATEGORY = u"落ち葉"
H = wx.mel_hash
REBUILD = ("count",)
CREATE_ONLY = ("area_width", "area_height", "follow_camera")
COLOR_PARAMS = ("color", "color2", "color3")


def leaf_points():
    """葉の形 (ローカルの XZ 面。先が +X、長さ 1) と短い柄。(点, 面)。"""
    outline = []
    for k in range(12):
        t = k / 12.0 * 2 * math.pi
        x = 0.5 * math.cos(t)
        w = 0.32 * math.sin(t) * (1 - 0.35 * math.cos(t))   # 根元が少し太い
        tip = max(0.0, math.cos(t)) ** 6 * 0.1               # 先をとがらせる
        outline.append((x + tip, 0.0, w))
    stem = [(-0.5, 0.0, -0.02), (-0.66, 0.0, -0.015), (-0.66, 0.0, 0.015), (-0.5, 0.0, 0.02)]
    pts = outline + stem
    return pts, [list(range(12)), [12, 13, 14, 15]]


def _color_params():
    return [Param("color", u"色", (0.86, 0.43, 0.1), kind="color"),
            Param("color2", u"色 2", (0.68, 0.2, 0.08), kind="color"),
            Param("color3", u"色 3", (0.9, 0.72, 0.18), kind="color"),
            Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1)]


def _leaf_params(size, tilt):
    return [Param("size", u"葉の大きさ", size, minimum=0.0, step=0.05, decimals=3),
            Param("size_random", u"大きさのばらつき", 0.4, minimum=0.0, maximum=0.95, step=0.1),
            Param("tilt", u"傾き (度)", tilt, minimum=0.0, maximum=90.0, step=5.0, decimals=1,
                  tooltip=u"葉がひらひら傾く大きさ")]


class LeafFallTool(BaseTool):
    label = u"落ち葉 (舞い散る)"
    tab = TAB
    category = CATEGORY
    order = 875.5
    tooltip = (u"範囲の箱の上から落ち葉が、左右に振れながら回って舞い落ち、地面でしばらく寝てから消えます。\n"
               u"風 (dfx_windN) があれば流されます。葉は 3 色 (色・色 2・色 3) を混ぜます。")
    params = ([Param("count", u"葉の数", 150, minimum=1, maximum=3000)]
              + [Param("area_width", u"範囲の幅", 20.0, minimum=0.1, step=5.0,
                       tooltip=u"作成時の範囲の箱 (_area) の幅と奥行き。作成後は箱を拡大縮小する"),
                 Param("area_height", u"範囲の高さ", 10.0, minimum=0.1, step=1.0,
                       tooltip=u"作成時の範囲の箱の高さ (箱の底が地面 = 高さ 0)"),
                 Param("follow_camera", u"カメラに付いて動く", False,
                       tooltip=u"ON: 範囲の箱をカメラの真下に置き、カメラの横移動に付いて動かす。作成時のみ")]
              + [Param("amount", u"量", 1.0, minimum=0.0, maximum=1.0, step=0.1,
                       tooltip=u"舞っている葉の割合。キーを打てば舞い始め・止みを作れる"),
                 Param("speed", u"落ちる速さ", 0.05, minimum=0.001, step=0.01, decimals=3,
                       tooltip=u"1 フレームに落ちる距離"),
                 Param("speed_random", u"速さのばらつき", 0.3, minimum=0.0, maximum=0.9, step=0.1),
                 Param("sway", u"振れ幅", 0.6, minimum=0.0, step=0.1, tooltip=u"左右に振れる幅"),
                 Param("sway_period", u"振れの周期 (フレーム)", 40.0, minimum=1.0, step=5.0, decimals=1),
                 Param("spin", u"回る速さ (度/フレーム)", 3.0, minimum=0.0, step=1.0, decimals=1,
                       tooltip=u"落ちながら向きが回る速さ (葉ごとに左右・速さが違う)"),
                 Param("ground_frames", u"地面に残るフレーム数", 60.0, minimum=0.0, step=10.0, decimals=1,
                       tooltip=u"地面に着いてから寝ているフレーム数 (最後に縮んで消える)。0 で着いたらすぐ消える")]
              + _leaf_params(0.25, 50.0) + _color_params()
              + [Param("wind_influence", u"風の影響", 1.0, minimum=0.0, maximum=5.0, step=0.1,
                       tooltip=u"風 (dfx_windN) に流される割合。0 で風を受けない"),
                 Param("seed", u"シード", 1, minimum=0, maximum=99999)])

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, FALL)


class LeafVortexTool(BaseTool):
    label = u"落ち葉 (地面の渦)"
    tab = TAB
    category = CATEGORY
    order = 875.6
    tooltip = (u"地面で落ち葉が渦を巻いて回ります (カルマン渦列)。渦は出る場所のロケータ (_emitter) から\n"
               u"ロケータの X の向き (下流) へ、左右交互に出て流れ、回る向きも交互です。\n"
               u"葉は渦に巻き上げられて回り、渦が弱まると地面に落ちます。オブジェクトを選んでいればそれに付いて動きます。")
    params = ([Param("count", u"葉の数", 120, minimum=1, maximum=3000),
               Param("start_frame", u"開始フレーム", 1.0, decimals=2, tooltip=u"最初の渦が出るフレーム"),
               Param("shed_interval", u"渦の出る間隔 (フレーム)", 16.0, minimum=0.5, step=2.0, decimals=1,
                     tooltip=u"次の渦が出るまでのフレーム数 (左右交互に出る)"),
               Param("speed", u"渦の流れる速さ", 0.12, minimum=0.0, step=0.02, decimals=3,
                     tooltip=u"渦が下流 (ロケータの X) へ 1 フレームに進む距離"),
               Param("row_width", u"渦の列の幅", 1.2, minimum=0.0, step=0.2,
                     tooltip=u"左右の渦の列の間隔 (ロケータの Z の向き)"),
               Param("wander", u"渦の蛇行", 0.2, minimum=0.0, step=0.1, tooltip=u"渦の進み方の揺らぎ"),
               Param("radius", u"渦の大きさ", 0.9, minimum=0.0, step=0.1, tooltip=u"葉が回る半径 (育ち切った時)"),
               Param("spin", u"渦の回る速さ (度/フレーム)", 14.0, minimum=0.0, step=2.0, decimals=1,
                     tooltip=u"出た時の回る速さ (中心ほど速い)。渦が弱まるとゆっくりになる"),
               Param("lift", u"巻き上げる高さ", 0.8, minimum=0.0, step=0.1),
               Param("life", u"渦が消えるまでのフレーム数", 100.0, minimum=1.0, step=10.0, decimals=1),
               Param("ground_frames", u"地面に残るフレーム数", 30.0, minimum=0.0, step=10.0, decimals=1,
                     tooltip=u"渦が消えてから葉が地面で寝ているフレーム数 (最後に縮んで消え、次の渦に乗る)")]
              + _leaf_params(0.22, 60.0) + _color_params()
              + [Param("seed", u"シード", 1, minimum=0, maximum=99999)])

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, VORTEX)


# ---------------------------------------------------------------- 構築 (Maya)
def color_counts(count):
    """色ごとの葉の数 (葉 j の色 = j % LEAF_COLORS)。"""
    n = mo.LEAF_COLORS
    return [(int(count) - g + n - 1) // n for g in range(n)]


def _create_area(cmds, mu, root, v, camera=None, target=None):
    wx.create_area(cmds, mu, root, (v["area_width"], v["area_height"], v["area_width"]),
                   v["follow_camera"], camera)


def _create_emitter(cmds, mu, root, v, camera=None, target=None):
    wx.create_emitter(cmds, mu, root, target)


def _axes_code():
    """$yaw $rock $roll → $X $Y $Z (motion.leaf_axes と同じ。$X $Y $Z は宣言済み)。"""
    return u"""vector $x0 = <<cos($yaw), 0, -sin($yaw)>>;
vector $z0 = <<sin($yaw), 0, cos($yaw)>>;
vector $y0 = <<0, 1, 0>>;
vector $y1 = $y0 * cos($rock) + $z0 * sin($rock);
$Z = $z0 * cos($rock) - $y0 * sin($rock);
$X = $x0 * cos($roll) + $y1 * sin($roll);
$Y = $y1 * cos($roll) - $x0 * sin($roll);
"""


def _size_code(r):
    return u"float $sz = {r}.dfx_size * (1 + ({h} - 0.5) * 2 * {r}.dfx_size_random);\n".format(
        r=r, h=H("$j", 0, 8))


def _fall_body(r, g):
    """舞い散る葉 $j (= $i * 色の数 + g) の $p / $X $Y $Z / $S / $vis (motion.leaf_fall と同じ)。"""
    tau = repr(mo.TAU)
    code = u"""int $j = $i * {n} + {g};
float $sp = {r}.dfx_speed * (1 + ({h1} - 0.5) * 2 * {r}.dfx_speed_random);
if ($sp < 0.0001) $sp = 0.0001;
float $fall = $aH / $sp;
float $rest = max({r}.dfx_ground_frames, 0.0);
float $life = $fall + $rest;
float $u = frame / $life + {h2};
float $c = floor($u);
float $age = ($u - $c) * $life;
float $ts = frame - $age;
float $ta = min($age, $fall);
int $landed = $age > $fall;
float $te = $ts + $ta;
vector $q = <<{h3} - 0.5, 0.5, {h4} - 0.5>>;
""".format(n=mo.LEAF_COLORS, g=g, r=r, h1=H("$j", 0, 1), h2=H("$j", 0, 2), h3=H("$j", "$c", 3),
           h4=H("$j", "$c", 4))
    code += wx.mel_table("$De", "$wd", "$te") + wx.mel_table("$Ds", "$wd", "$ts")
    code += u"""vector $dw = ($De - $Ds) * $wf + <<0, -$sp * $ta, 0>>;
float $per = max({r}.dfx_sway_period, 1.0);
float $ph = {tau} * $ta / $per + {tau} * {h5};
float $sdir = {tau} * {h6};
float $sw = {r}.dfx_sway * sin($ph);
$dw = $dw + <<$sw * cos($sdir), 0, $sw * sin($sdir)>>;
vector $dl = {xv};
$q = $q + $dl;
$q = <<fmod($q.x + 1000.5, 1.0) - 0.5, max($q.y, -0.5), fmod($q.z + 1000.5, 1.0) - 0.5>>;
vector $p = {xf};
float $flat = 1;
if ($landed) $flat = 0;
float $yaw = -$sdir + deg_to_rad({r}.dfx_spin) * $ta * (2 * {h9} - 1);
float $rock = deg_to_rad({r}.dfx_tilt) * cos($ph) * $flat;
float $roll = deg_to_rad({r}.dfx_tilt) * 0.4 * sin(2 * $ph + {tau} * {h10}) * $flat;
vector $X; vector $Y; vector $Z;
""".format(r=r, tau=tau, h5=H("$j", 0, 5), h6=H("$j", "$c", 6), h9=H("$j", 0, 9), h10=H("$j", 0, 10),
           xv=wx.mel_xv("$ai", "$dw"), xf=wx.mel_xf("$am", "$q"))
    code += _axes_code() + _size_code(r) + u"""if ($landed && $rest > 0) $sz = $sz * clamp(0, 1, (1 - ($age - $fall) / $rest) / 0.2);
int $vis = ({h7} < {r}.dfx_amount) && ($sz > 1e-9);
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r, h7=H("$j", "$c", 7))
    return code


def _emitter_matrix_head(root):
    """出る場所のワールド行列 $om (ロケータの移動・回転・大きさから作る。_area_head と同じ作り)。"""
    dm = wx.track_name(root, wx.EMITTER_ATTR)
    return wx.mel_rows(dm, "o") + u"""float $os0 = {dm}.outputScaleX;
float $os1 = {dm}.outputScaleY;
float $os2 = {dm}.outputScaleZ;
float $om[] = {{$oR0.x * $os0, $oR0.y * $os0, $oR0.z * $os0, 0.0,
               $oR1.x * $os1, $oR1.y * $os1, $oR1.z * $os1, 0.0,
               $oR2.x * $os2, $oR2.y * $os2, $oR2.z * $os2, 0.0,
               {dm}.outputTranslateX, {dm}.outputTranslateY, {dm}.outputTranslateZ, 1.0}};
""".format(dm=dm)


def _vortex_body(r, g):
    """渦に巻かれる葉 $j の $p / $X $Y $Z / $S / $vis (motion.leaf_vortex + leaf_place と同じ)。"""
    tau = repr(mo.TAU)
    code = u"""int $j = $i * {n} + {g};
float $shed = max({r}.dfx_shed_interval, 0.5);
float $life = max({r}.dfx_life, 1.0);
int $lanes = max(1, ceil(($life + max({r}.dfx_ground_frames, 0.0)) / $shed));
int $m = $j % $lanes;
float $t = frame - {r}.dfx_start_frame - $m * $shed;
vector $p = <<0, 0, 0>>;
vector $X = <<1, 0, 0>>; vector $Y = <<0, 1, 0>>; vector $Z = <<0, 0, 1>>;
float $sz = 0;
int $vis = 0;
if ($t >= 0) {{
    float $period = $lanes * $shed;
    float $n = floor($t / $period);
    int $k = $m + $lanes * $n;
    float $a = $t - $n * $period;
    float $ta = min($a, $life);
    float $side = 1;
    if ($k % 2) $side = -1;
    float $turn = -$side;
    float $env = (1 - $ta / $life) * (1 - exp(-$ta / 6.0));
    float $grow = 1 - exp(-$ta / max(0.3 * $life, 1.0));
    float $x = sqrt({h11});
    float $rho = {r}.dfx_radius * (0.2 + 0.9 * $x) * (0.55 + 0.45 * $grow);
    float $w = 1.4 / (0.4 + $x);
    float $th = {tau} * {h12} + $turn * deg_to_rad({r}.dfx_spin) * $w * ($ta - $ta * $ta / (2 * $life));
    float $hy = {r}.dfx_lift * $env * (0.3 + 0.7 * {h13}) * (0.75 + 0.25 * sin(2 * $th + {tau} * {h14}));
    float $cx = {r}.dfx_speed * $ta + {r}.dfx_wander * sin({tau} * $ta / 90.0 + {tau} * {h15});
    float $cz = $side * {r}.dfx_row_width * 0.5;
    vector $lp = <<$cx + $rho * cos($th), $hy, $cz + $rho * sin($th)>>;
    float $yaw = -($th + $turn * {half_pi});
    float $rock = deg_to_rad({r}.dfx_tilt) * $env * (0.6 + 0.4 * sin(3 * $th + {tau} * {h16}));
    float $roll = deg_to_rad({r}.dfx_tilt) * 0.5 * $env * sin(1.7 * $th + {tau} * {h17});
""".format(n=mo.LEAF_COLORS, g=g, r=r, tau=tau, half_pi=repr(math.pi / 2), h11=H("$j", "$k", 11),
           h12=H("$j", "$k", 12), h13=H("$j", "$k", 13), h14=H("$j", "$k", 14), h15=H("$k", 0, 15),
           h16=H("$j", "$k", 16), h17=H("$j", "$k", 17))
    code += _axes_code() + u"""    $sz = {r}.dfx_size * (1 + ({h8} - 0.5) * 2 * {r}.dfx_size_random) * clamp(0, 1, $a / 4.0);
    float $tail = min(8.0, $period - $life);
    if ($tail > 0 && $a > $period - $tail) $sz = $sz * clamp(0, 1, ($period - $a) / $tail);
    $vis = $sz > 1e-9;
    $p = {xf};
    vector $wx = {xx};
    $X = unit($wx);
    vector $wy = {xy};
""".format(r=r, h8=H("$j", 0, 8), xf=wx.mel_xf("$om", "$lp"), xx=wx.mel_xv("$om", "$X"),
           xy=wx.mel_xv("$om", "$Y")) + u"""    $Y = $wy - dot($wy, $X) * $X;
    if (mag($Y) < 1e-6) $Y = cross($X, <<0, 0, 1>>);
    if (mag($Y) < 1e-6) $Y = cross($X, <<1, 0, 0>>);
    $Y = unit($Y);
    $Z = cross($X, $Y);
}
vector $S = <<$sz, $sz, $sz>>;
"""
    return code


def _build_leaves(cmds, mu, root, v, head, body):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    cmds.setAttr(src_grp + ".visibility", 0)
    pts, faces = leaf_points()
    for g, n in enumerate(color_counts(v["count"])):
        src = wx.make_mesh(cmds, "%s_leafSrc%d" % (prefix, g + 1), src_grp, pts, faces)
        shader, sg = wx.flat_shader(cmds, mu, root, "%s_mat%d" % (prefix, g + 1), owned,
                                    color_attr=COLOR_PARAMS[g])
        cmds.sets(src, edit=True, forceElement=sg)
        if not n:
            continue
        nodes = wx.make_instances(cmds, root, src, "%s_leaves%d" % (prefix, g + 1), n, owned)
        wx.chunked_expressions(cmds, root, "%s_leaf%d" % (prefix, g + 1), nodes, wx.TRANSFORM_OUTPUTS, head,
                               lambda a, b, g=g: body(prefix, g) + wx.mel_store_transform(a), owned)
    wx.finish(cmds, mu, root, owned)


def build_fall(cmds, mu, root, v):
    from .weather import _area_head
    head = wx.mel_head(root, need_camera=False, need_wind=True) + _area_head(root)
    _build_leaves(cmds, mu, root, v, head, _fall_body)


def build_vortex(cmds, mu, root, v):
    head = wx.mel_head(root, need_camera=False, need_wind=False) + _emitter_matrix_head(root)
    _build_leaves(cmds, mu, root, v, head, _vortex_body)


def _live(tool):
    return tuple(p.name for p in tool.params if p.name not in REBUILD and p.name not in CREATE_ONLY)


FALL = wx.register(wx.Kind("leaves_fall", LeafFallTool, "dfx_leaves#", u"落ち葉 (舞い散る)",
                           _live(LeafFallTool), REBUILD, build=build_fall, create=_create_area,
                           need_camera=False, need_wind=True))
VORTEX = wx.register(wx.Kind("leaves_vortex", LeafVortexTool, "dfx_leafVortex#", u"落ち葉 (地面の渦)",
                             _live(LeafVortexTool), REBUILD, build=build_vortex, create=_create_emitter,
                             need_camera=False, need_wind=False))
