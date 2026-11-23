# -*- coding: utf-8 -*-
"""天候タブ: 粒で作るもの (雨・雪・土煙・火花) と、天候共通の更新・プリセット。

    雨 (線と跳ね)      落ちる向きに伸びた線が降り、地面 (範囲の箱の底) で輪が跳ねる
    雪 (粒)            カメラを向く白い丸が、ゆらゆら揺れながら降る
    土煙 (スプライト)  煙の絵を貼った板が、広がり・回りながら大きくなって消える
    土煙 (もこもこ)    丸を集めた立体の煙 (アニメ調の 2 色) が広がって消える
    火花 (線)          進む向きに伸びた線が重力で落ち、地面で跳ねる
    火花 (尾を引く)    火花が通った跡を細い帯で引く

雨・雪は範囲の箱 (_area) の中に降り続け、横は箱の中で回り込む (はみ出した分は反対側から出る)。
土煙・火花は出る場所 (_emitter) から出る。作成時にオブジェクトを選んでいれば、それに付いて動く。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param, ToolError
from . import _weather as wx

H = wx.mel_hash


def _p(name):
    return "{r}.dfx_" + name


# ---------------------------------------------------------------- 純粋な計算 (検証用)
def cone_direction(up, h_angle, h_turn, spread):
    """up を中心に spread 度までの円錐の中の向き (MEL と同じ式)。"""
    ux, uy, uz = up
    # up に直交する A (cand = X 軸)、B = up × A
    c = (1.0, 0.0, 0.0)
    d = sum(a * b for a, b in zip(c, up))
    a = tuple(ci - d * ui for ci, ui in zip(c, up))
    if math.sqrt(sum(x * x for x in a)) < 1e-6:
        a = (uy * 1 - uz * 0, uz * 0 - ux * 1, ux * 0 - uy * 0)  # up × (0, 0, 1)
    n = math.sqrt(sum(x * x for x in a))
    a = tuple(x / n for x in a)
    b = (uy * a[2] - uz * a[1], uz * a[0] - ux * a[2], ux * a[1] - uy * a[0])
    th = math.radians(spread * h_angle)
    ph = 2 * math.pi * h_turn
    return tuple(u * math.cos(th) + (ai * math.cos(ph) + bi * math.sin(ph)) * math.sin(th)
                 for u, ai, bi in zip(up, a, b))


def spark_motion(p0, v0, gravity, ground, bounce, age, max_bounces=3, friction=0.8):
    """火花の (位置, 速さ)。地面 (高さ ground) で bounce の割合で跳ね返る (MEL と同じ式)。"""
    p, v, a = list(p0), list(v0), age
    for _ in range(max_bounces):
        disc = v[1] * v[1] + 2 * gravity * (p[1] - ground)
        if gravity <= 1e-6 or p[1] < ground - 1e-6 or disc < 0:
            break
        th = (v[1] + math.sqrt(disc)) / gravity
        if th >= a or th <= 1e-4:
            break
        p = [p[0] + v[0] * th, ground, p[2] + v[2] * th]
        vy = v[1] - gravity * th
        v = [v[0] * friction, -vy * bounce, v[2] * friction]
        a -= th
    pos = [p[0] + v[0] * a, p[1] + v[1] * a - 0.5 * gravity * a * a, p[2] + v[2] * a]
    if pos[1] < ground and p[1] >= ground - 1e-6:
        pos[1] = ground
    return tuple(pos), (v[0], v[1] - gravity * a, v[2])


def cycle(frame, life, phase):
    """降り続ける粒の (何回目か, 出てからのフレーム)。"""
    u = frame / life + phase
    c = math.floor(u)
    return int(c), (u - c) * life


# ---------------------------------------------------------------- 共通のパラメータ
def _common_tail(wind=1.0):
    return [Param("wind_influence", u"風の影響", wind, minimum=0.0, maximum=5.0, step=0.1,
                  tooltip=u"風 (dfx_windN) に流される割合。0 で風を受けない"),
            Param("seed", u"シード", 1, minimum=0, maximum=99999)]


def _area_params(width, height, follow):
    return [Param("area_width", u"範囲の幅", width, minimum=0.1, step=5.0,
                  tooltip=u"作成時の範囲の箱 (_area) の幅と奥行き。作成後は箱を拡大縮小する"),
            Param("area_height", u"範囲の高さ", height, minimum=0.1, step=1.0,
                  tooltip=u"作成時の範囲の箱の高さ (箱の底が地面 = 高さ 0)"),
            Param("follow_camera", u"カメラに付いて動く", follow,
                  tooltip=u"OFF: 範囲の箱は原点に置く。ON: 箱をカメラの真下に置き、カメラの横移動に付いて動かす\n"
                          u"(ビューのカメラ persp だと、ビューを回すたびに箱も動くので注意)。作成時のみ")]


def _emitter_params(count, start=1.0):
    return [Param("count", u"数", count, minimum=1, maximum=500),
            Param("start_frame", u"開始フレーム", start, decimals=2),
            Param("duration", u"出し続けるフレーム数", 0.0, minimum=0.0, step=5.0, decimals=2,
                  tooltip=u"0 なら開始フレームに一度に出す。それ以上なら、この間に均等に出す"),
            Param("life", u"消えるまでのフレーム数", 30.0, minimum=1.0, step=5.0, decimals=1),
            Param("life_random", u"消えるまでのばらつき", 0.3, minimum=0.0, maximum=0.95, step=0.1)]


# ---------------------------------------------------------------- 雨
class RainTool(BaseTool):
    label = u"雨 (線と跳ね)"
    tab = wx.TAB
    category = u"雨"
    order = 872
    tooltip = (u"落ちる向きに伸びた線が範囲の箱の中に降り続け、地面 (箱の底) で輪が跳ねます。\n"
               u"線はカメラに向きます (カメラを選んでおくとそのカメラ、無ければ今のビュー)。")
    params = ([Param("count", u"雨粒の数", 400, minimum=1, maximum=3000)]
              + _area_params(30.0, 20.0, False)
              + [Param("amount", u"量", 1.0, minimum=0.0, maximum=1.0, step=0.1,
                       tooltip=u"降っている粒の割合。キーを打てば降り始め・止みを作れる"),
                 Param("speed", u"落ちる速さ", 1.5, minimum=0.01, step=0.25,
                       tooltip=u"1 フレームに落ちる距離"),
                 Param("speed_random", u"速さのばらつき", 0.2, minimum=0.0, maximum=0.9, step=0.1),
                 Param("length", u"線の長さ", 1.2, minimum=0.0, step=0.2),
                 Param("width", u"線の太さ", 0.025, minimum=0.0, step=0.01, decimals=3),
                 Param("splash_size", u"はねの大きさ", 0.35, minimum=0.0, step=0.05,
                       tooltip=u"地面に落ちた時の輪の大きさ (0 で出さない)"),
                 Param("splash_frames", u"はねのフレーム数", 4.0, minimum=0.5, step=1.0, decimals=1),
                 Param("color", u"色", (0.78, 0.86, 1.0), kind="color"),
                 Param("opacity", u"不透明度", 0.7, minimum=0.0, maximum=1.0, step=0.1)]
              + _common_tail())

    def run(self, ctx, **v):
        return _create(ctx, v, RAIN)


def _create_area(cmds, mu, root, v, camera=None, target=None):
    wx.create_area(cmds, mu, root, (v["area_width"], v["area_height"], v["area_width"]),
                   v["follow_camera"], camera)


def _create_emitter(cmds, mu, root, v, camera=None, target=None):
    wx.create_emitter(cmds, mu, root, target)


def _area_head(root):
    """範囲の箱のワールド行列 $am と逆行列 $ai、高さ $aH (箱の移動・回転・大きさから作る)。"""
    dm = wx.track_name(root, wx.AREA_ATTR)
    return wx.mel_rows(dm, "a") + u"""float $as0 = {dm}.outputScaleX; if (abs($as0) < 1e-9) $as0 = 1e-9;
float $as1 = {dm}.outputScaleY; if (abs($as1) < 1e-9) $as1 = 1e-9;
float $as2 = {dm}.outputScaleZ; if (abs($as2) < 1e-9) $as2 = 1e-9;
vector $aT = <<{dm}.outputTranslateX, {dm}.outputTranslateY, {dm}.outputTranslateZ>>;
float $am[] = {{$aR0.x * $as0, $aR0.y * $as0, $aR0.z * $as0, 0.0,
               $aR1.x * $as1, $aR1.y * $as1, $aR1.z * $as1, 0.0,
               $aR2.x * $as2, $aR2.y * $as2, $aR2.z * $as2, 0.0,
               $aT.x, $aT.y, $aT.z, 1.0}};
float $ai[] = {{$aR0.x / $as0, $aR1.x / $as1, $aR2.x / $as2, 0.0,
               $aR0.y / $as0, $aR1.y / $as1, $aR2.y / $as2, 0.0,
               $aR0.z / $as0, $aR1.z / $as1, $aR2.z / $as2, 0.0,
               -dot($aT, $aR0) / $as0, -dot($aT, $aR1) / $as1, -dot($aT, $aR2) / $as2, 1.0}};
float $aH = abs($as1);
""".format(dm=dm)


def _falling_body(r, sway=False):
    """降り続ける粒 (雨・雪) の $p / $vel / $vis / $age / $L / $c。範囲の箱の中で横に回り込む。"""
    code = u"""float $sp = {r}.dfx_speed * (1 + ({hs} - 0.5) * 2 * {r}.dfx_speed_random);
if ($sp < 0.0001) $sp = 0.0001;
float $L = $aH / $sp;
float $u = frame / $L + {hp};
float $c = floor($u);
float $age = ($u - $c) * $L;
float $ts = frame - $age;
vector $q = <<{hx} - 0.5, 0.5, {hz} - 0.5>>;
""".format(r=r, hs=H("$i", 0, 1), hp=H("$i", 0, 2), hx=H("$i", "$c", 3), hz=H("$i", "$c", 4))
    code += wx.mel_table("$Ds", "$wd", "$ts")
    code += u"vector $dw = ($Dnow - $Ds) * $wf + <<0, -$sp * $age, 0>>;\n"
    if sway:
        code += u"""float $sw = {r}.dfx_sway;
float $per = max({r}.dfx_sway_period, 1.0);
$dw = $dw + <<$sw * sin(6.2831853 * $age / $per + 6.2831853 * {h1}),
               0, $sw * cos(6.2831853 * $age / ($per * 1.3) + 6.2831853 * {h2})>>;
""".format(r=r, h1=H("$i", 0, 5), h2=H("$i", 0, 6))
    code += u"""vector $dl = {xv};
$q = $q + $dl;
$q = <<fmod($q.x + 1000.5, 1.0) - 0.5, $q.y, fmod($q.z + 1000.5, 1.0) - 0.5>>;
vector $p = {xf};
vector $vel = <<0, -$sp, 0>> + $Wnow * $wf;
int $vis = {hv} < {r}.dfx_amount;
""".format(xv=wx.mel_xv("$ai", "$dw"), xf=wx.mel_xf("$am", "$q"), r=r, hv=H("$i", "$c", 7))
    return code


def build_rain(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    pts, faces, uvs = wx.card_points(-1.0, 0.0)
    drop = wx.make_mesh(cmds, prefix + "_dropSrc", src_grp, pts, faces, uvs)
    ring, ring_faces = wx.disc_points(16, 0.5, 0.38)
    ring = [(x, 0.0, y) for x, y, _ in ring]
    splash = wx.make_mesh(cmds, prefix + "_splashSrc", src_grp, ring, ring_faces)
    cmds.setAttr(src_grp + ".visibility", 0)
    shader, sg = wx.flat_shader(cmds, mu, root, prefix + "_mat", owned)
    cmds.sets([drop, splash], edit=True, forceElement=sg)
    count = int(v["count"])
    drops = wx.make_instances(cmds, root, drop, prefix + "_drops", count, owned)
    splashes = wx.make_instances(cmds, root, splash, prefix + "_splashes", count, owned)
    head = wx.mel_head(root, need_camera=True) + _area_head(root)
    r = prefix

    def drop_body(a, b):
        return (_falling_body(r) + wx.mel_streak("$p", "$vel")
                + u"vector $S = <<{r}.dfx_width, {r}.dfx_length, 1>>;\n".format(r=r)
                + wx.mel_store_transform(a))

    def splash_body(a, b):
        # 1 つ前の回 (c - 1) に地面に落ちた所で、今の回の出始めにはねる
        code = _falling_body(r) + u"""float $tp = $ts - $L;
vector $q0 = <<{hx} - 0.5, 0.5, {hz} - 0.5>>;
""".format(hx=H("$i", "$c - 1", 3), hz=H("$i", "$c - 1", 4))
        code += wx.mel_table("$Dp", "$wd", "$tp")
        code += u"""vector $dh = ($Ds - $Dp) * $wf + <<0, -$sp * $L, 0>>;
vector $qh = $q0 + {xv};
$qh = <<fmod($qh.x + 1000.5, 1.0) - 0.5, -0.5, fmod($qh.z + 1000.5, 1.0) - 0.5>>;
$p = {xf};
float $k = $age / max({r}.dfx_splash_frames, 0.001);
float $ss = {r}.dfx_splash_size * (0.35 + 0.65 * $k);
$vis = ({hv} < {r}.dfx_amount) && ($k < 1) && ({r}.dfx_splash_size > 0);
vector $X = <<1, 0, 0>>; vector $Y = <<0, 1, 0>>; vector $Z = <<0, 0, 1>>;
vector $S = <<$ss, $ss, $ss>>;
""".format(xv=wx.mel_xv("$ai", "$dh"), xf=wx.mel_xf("$am", "$qh"), r=r, hv=H("$i", "$c - 1", 7))
        return code + wx.mel_store_transform(a)

    wx.chunked_expressions(cmds, root, prefix + "_drop", drops, wx.TRANSFORM_OUTPUTS, head,
                           drop_body, owned)
    wx.chunked_expressions(cmds, root, prefix + "_splash", splashes, wx.TRANSFORM_OUTPUTS, head,
                           splash_body, owned)
    wx.finish(cmds, mu, root, owned)


# ---------------------------------------------------------------- 雪
class SnowTool(BaseTool):
    label = u"雪 (粒)"
    tab = wx.TAB
    category = u"雪"
    order = 874
    tooltip = (u"カメラを向く白い丸が、ゆらゆら揺れながら範囲の箱の中に降り続けます。\n"
               u"風 (dfx_windN) があれば流されます。")
    params = ([Param("count", u"粒の数", 500, minimum=1, maximum=3000)]
              + _area_params(30.0, 15.0, False)
              + [Param("amount", u"量", 1.0, minimum=0.0, maximum=1.0, step=0.1,
                       tooltip=u"降っている粒の割合。キーを打てば降り始め・止みを作れる"),
                 Param("speed", u"落ちる速さ", 0.06, minimum=0.001, step=0.01, decimals=3),
                 Param("speed_random", u"速さのばらつき", 0.3, minimum=0.0, maximum=0.9, step=0.1),
                 Param("size", u"粒の大きさ", 0.08, minimum=0.0, step=0.02, decimals=3),
                 Param("size_random", u"大きさのばらつき", 0.5, minimum=0.0, maximum=0.95, step=0.1),
                 Param("sway", u"揺れ幅", 0.4, minimum=0.0, step=0.1),
                 Param("sway_period", u"揺れの周期 (フレーム)", 50.0, minimum=1.0, step=5.0, decimals=1),
                 Param("color", u"色", (1.0, 1.0, 1.0), kind="color"),
                 Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1)]
              + _common_tail())

    def run(self, ctx, **v):
        return _create(ctx, v, SNOW)


def build_snow(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    pts, faces = wx.disc_points(10, 0.5)
    flake = wx.make_mesh(cmds, prefix + "_flakeSrc", src_grp, pts, faces)
    cmds.setAttr(src_grp + ".visibility", 0)
    shader, sg = wx.flat_shader(cmds, mu, root, prefix + "_mat", owned)
    cmds.sets(flake, edit=True, forceElement=sg)
    flakes = wx.make_instances(cmds, root, flake, prefix + "_flakes", int(v["count"]), owned)
    head = wx.mel_head(root, need_camera=True) + _area_head(root)
    r = prefix

    def body(a, b):
        return (_falling_body(r, sway=True) + wx.mel_billboard("$p")
                + u"float $sz = {r}.dfx_size * (1 + ({h} - 0.5) * 2 * {r}.dfx_size_random);\n"
                  u"vector $S = <<$sz, $sz, $sz>>;\n".format(r=r, h=H("$i", 0, 8))
                + wx.mel_store_transform(a))

    wx.chunked_expressions(cmds, root, prefix + "_flake", flakes, wx.TRANSFORM_OUTPUTS, head,
                           body, owned)
    wx.finish(cmds, mu, root, owned)


# ---------------------------------------------------------------- 出る場所から出るもの (土煙・火花)
def _emitter_head(root):
    """出る場所の名前 $em (過去のフレームの位置は getAttr -time で読む)。

    今の位置は使わないが、decomposeMatrix の出力を読んでおくことで、出る場所を動かした時にも
    計算し直されるようにする。
    """
    dm = wx.track_name(root, wx.EMITTER_ATTR)
    return u"""string $em = "";
string $el[];
if (`objExists "{root}.{attr}"`) $el = `listConnections -source 1 -destination 0 "{root}.{attr}"`;
if (size($el) > 0) $em = $el[0];
float $edep = {dm}.outputTranslateX + {dm}.outputTranslateY + {dm}.outputTranslateZ
            + {dm}.outputRotateX + {dm}.outputRotateY + {dm}.outputRotateZ;
""".format(root=root, attr=wx.EMITTER_ATTR, dm=dm)


def _spawn_body(r, count):
    """粒 $i が出たフレーム $ts、出てからの $age、寿命 $life、出た場所 $E と向き $dir / 上 $U。"""
    den = repr(float(max(count - 1, 1)))
    code = u"""float $life = {r}.dfx_life * (1 + ({hl} - 0.5) * 2 * {r}.dfx_life_random);
if ($life < 1) $life = 1;
float $ts = {r}.dfx_start_frame + {r}.dfx_duration * $i / {den};
if ({r}.dfx_duration <= 0) $ts = {r}.dfx_start_frame + {hj} * 2.0;
float $age = frame - $ts;
int $vis = ($age >= 0) && ($age <= $life);
float $a = max($age, 0);
float $emx[] = {{1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0}};
if ($em != "") $emx = `getAttr -time $ts ($em + ".worldMatrix[0]")`;
vector $E = <<$emx[12], $emx[13], $emx[14]>>;
vector $U = unit(<<$emx[4], $emx[5], $emx[6]>>);
""".format(r=r, hl=H("$i", 0, 1), hj=H("$i", 0, 2), den=den)
    code += wx.mel_ortho("$A", "$U", "<<1, 0, 0>>")
    code += u"""vector $B = cross($U, $A);
float $th = deg_to_rad({r}.dfx_spread * {h1});
float $ph = 6.2831853 * {h2};
vector $dir = $U * cos($th) + ($A * cos($ph) + $B * sin($ph)) * sin($th);
float $spd = {r}.dfx_speed * (1 + ({h3} - 0.5) * 2 * {r}.dfx_speed_random);
""".format(r=r, h1=H("$i", 0, 3), h2=H("$i", 0, 4), h3=H("$i", 0, 5))
    code += wx.mel_table("$Ds", "$wd", "$ts")
    code += u"vector $wd = ($Dnow - $Ds) * $wf;\n"
    return code


def _dust_params(mesh):
    base = (_emitter_params(24)
            + [Param("speed", u"広がる速さ", 0.35, minimum=0.0, step=0.05),
               Param("speed_random", u"速さのばらつき", 0.4, minimum=0.0, maximum=0.95, step=0.1),
               Param("spread", u"広がる角度", 80.0, minimum=0.0, maximum=180.0, step=10.0, decimals=1,
                     tooltip=u"出る場所の上 (Y) からの角度。90 で横 (地面に沿って) 広がる"),
               Param("rise", u"上がる速さ", 0.03, step=0.01, decimals=3),
               Param("drag", u"減速", 0.12, minimum=0.0, maximum=1.0, step=0.02, decimals=3,
                     tooltip=u"広がる速さが落ちる速さ (大きいほどすぐ止まる)"),
               Param("size_start", u"出始めの大きさ", 0.4, minimum=0.0, step=0.1),
               Param("size_end", u"最後の大きさ", 1.6, minimum=0.0, step=0.1),
               Param("size_random", u"大きさのばらつき", 0.3, minimum=0.0, maximum=0.95, step=0.1),
               Param("spin", u"回転 (度 / フレーム)", 3.0, step=1.0, decimals=1),
               Param("color", u"色", (0.86, 0.76, 0.6), kind="color")])
    if mesh:
        base.append(Param("shade_color", u"影の色", (0.62, 0.52, 0.42), kind="color"))
    base.append(Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1))
    if not mesh:
        base.append(wx.texture_param("puff", tiling=False))
    return base + _common_tail(0.5)


class DustTool(BaseTool):
    label = u"土煙 (スプライト)"
    tab = wx.TAB
    category = u"土煙"
    order = 876
    tooltip = (u"アニメ調の煙の絵を貼った板が、出る場所 (_emitter) から広がり、回りながら大きくなって消えます。\n"
               u"オブジェクト (車輪・足など) を選んで作ると、出る場所がそれに付いて動きます。")
    params = _dust_params(False)

    def run(self, ctx, **v):
        return _create(ctx, v, DUST)


class DustMeshTool(BaseTool):
    label = u"土煙 (もこもこ)"
    tab = wx.TAB
    category = u"土煙"
    order = 877
    tooltip = (u"丸を集めた立体の煙 (中が明るく、ふちが影の色の 2 色) が、広がりながら大きくなって消えます。\n"
               u"どの向きから見ても形がある (板ではない) ので、カメラが回り込む時や遠景に。")
    params = _dust_params(True)

    def run(self, ctx, **v):
        return _create(ctx, v, DUST_MESH)


def _dust_motion(r):
    return u"""float $kd = max({r}.dfx_drag, 0.0001);
float $grow = 1 - exp(-3.0 * $a / $life);
float $sz = ({r}.dfx_size_start + ({r}.dfx_size_end - {r}.dfx_size_start) * $grow)
            * (1 + ({h} - 0.5) * 2 * {r}.dfx_size_random);
if ($a > 0.7 * $life) $sz = $sz * max(0, ($life - $a) / (0.3 * $life));
vector $p = $E + $dir * ($spd * (1 - exp(-$kd * $a)) / $kd) + $U * ({r}.dfx_rise * $a + $sz * 0.35) + $wd;
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r, h=H("$i", 0, 6))


def build_dust(cmds, mu, root, v, mesh=False):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    if mesh:
        src = _puff_mesh(cmds, prefix + "_puffSrc", src_grp)
        sg = _toon_shader(cmds, mu, root, prefix + "_mat", owned)
    else:
        pts, faces, uvs = wx.card_points()
        src = wx.make_mesh(cmds, prefix + "_puffSrc", src_grp, pts, faces, uvs)
        shader, sg, tex, _ = wx.texture_shader(cmds, mu, root, prefix + "_mat",
                                               wx.resolve_texture(cmds, v, "puff"), owned,
                                               opacity_attr="opacity")
    cmds.setAttr(src_grp + ".visibility", 0)
    cmds.sets(src, edit=True, forceElement=sg)
    count = int(v["count"])
    puffs = wx.make_instances(cmds, root, src, prefix + "_puffs", count, owned)
    head = wx.mel_head(root, need_camera=not mesh) + _emitter_head(root)
    r = prefix

    def body(a, b):
        code = _spawn_body(r, count) + _dust_motion(r)
        roll = u"{h} * 360 + {r}.dfx_spin * $a".format(h=H("$i", 0, 7), r=r)
        if mesh:
            # 立体なので、ばらばらの向きでゆっくり転がす
            code += u"""float $rx = {h1} * 360 + {r}.dfx_spin * $a;
float $ry = {h2} * 360;
float $rz = {h3} * 360 + {r}.dfx_spin * 0.5 * $a;
""".format(h1=H("$i", 0, 7), h2=H("$i", 0, 8), h3=H("$i", 0, 9), r=r)
            return code + _store_euler(a)
        return code + wx.mel_billboard("$p", roll) + wx.mel_store_transform(a)

    wx.chunked_expressions(cmds, root, prefix + "_puff", puffs, wx.TRANSFORM_OUTPUTS, head,
                           body, owned)
    wx.finish(cmds, mu, root, owned)


def _store_euler(a):
    """$rx $ry $rz を直接使う版の mel_store_transform。"""
    return u"""int $ob = ($i - {a}) * 10;
$o[$ob] = $p.x; $o[$ob + 1] = $p.y; $o[$ob + 2] = $p.z;
$o[$ob + 3] = $rx; $o[$ob + 4] = $ry; $o[$ob + 5] = $rz;
$o[$ob + 6] = $S.x; $o[$ob + 7] = $S.y; $o[$ob + 8] = $S.z;
$o[$ob + 9] = $vis;
""".format(a=a)


def _puff_mesh(cmds, name, parent):
    """丸を 5 つ集めた、もこもこの塊 (大きさ約 1)。"""
    groups_before = set(cmds.ls(type="groupId"))
    parts = []
    for x, y, z, r in ((0, 0, 0, 0.42), (0.28, -0.05, 0.1, 0.3), (-0.27, -0.04, -0.08, 0.3),
                       (0.05, 0.22, -0.05, 0.3), (-0.05, -0.02, 0.3, 0.26)):
        s = cmds.polySphere(radius=r, subdivisionsAxis=12, subdivisionsHeight=8,
                            constructionHistory=False)[0]
        cmds.setAttr(s + ".translate", x, y, z, type="double3")
        parts.append(s)
    merged = cmds.polyUnite(parts, constructionHistory=False, name=name)[0]
    merged = cmds.ls(cmds.parent(merged, parent)[0], long=True)[0]
    cmds.makeIdentity(merged, apply=True, translate=True)
    for s in parts:
        if cmds.objExists(s):
            cmds.delete(s)
    shape = cmds.listRelatives(merged, shapes=True, fullPath=True)[0]
    for a in ("castsShadows", "receiveShadows"):
        cmds.setAttr(shape + "." + a, 0)
    # 結合で面ごとの割り当て用の groupId ができ、作り直すたびに残ってしまうので、
    # 物体ごとの割り当てにしてから消す (マテリアルは後で割り当てる)
    cmds.sets(merged, edit=True, forceElement="initialShadingGroup")
    leftover = [g for g in set(cmds.ls(type="groupId")) - groups_before if cmds.objExists(g)]
    if leftover:
        cmds.delete(leftover)
    return merged


def _toon_shader(cmds, mu, root, name, owned, color="color", shade="shade_color",
                 opacity="opacity"):
    """アニメ調の 2 色 (正面は色、ふちは影の色) のマテリアル。ライトが無くても形が分かる。

    color / shade / opacity はルートのパラメータ名 (dfx_ を除く)。
    """
    plug = "%s.%s" % (root, mu.PARAM_PREFIX)
    shader, sg = mu.create_surface_shader(name, (1, 1, 1))
    info = cmds.shadingNode("samplerInfo", asUtility=True, name=name + "_facing")
    ramp = cmds.shadingNode("ramp", asTexture=True, name=name + "_toon")
    cmds.setAttr(ramp + ".type", 0)            # V ランプ
    cmds.setAttr(ramp + ".interpolation", 0)   # なし (くっきり 2 色)
    cmds.connectAttr(info + ".facingRatio", ramp + ".vCoord")
    for idx, pos, name_ in ((0, 0.0, shade), (1, 0.45, color)):
        cmds.setAttr("%s.colorEntryList[%d].position" % (ramp, idx), pos)
        cmds.connectAttr(plug + name_, "%s.colorEntryList[%d].color" % (ramp, idx))
    cmds.connectAttr(ramp + ".outColor", shader + ".outColor", force=True)
    inv = cmds.createNode("reverse", name=name + "_transparency")
    for axis in "XYZ":
        cmds.connectAttr(plug + opacity, inv + ".input" + axis)
    cmds.connectAttr(inv + ".output", shader + ".outTransparency", force=True)
    owned.extend([shader, sg, info, ramp, inv])
    return sg


# ---------------------------------------------------------------- 火花
def _spark_params(trail):
    base = (_emitter_params(40)
            + [Param("speed", u"飛ぶ速さ", 0.9, minimum=0.0, step=0.1),
               Param("speed_random", u"速さのばらつき", 0.5, minimum=0.0, maximum=0.95, step=0.1),
               Param("spread", u"広がる角度", 50.0, minimum=0.0, maximum=180.0, step=5.0, decimals=1,
                     tooltip=u"出る場所の上 (Y) からの角度"),
               Param("gravity", u"重力", 0.04, minimum=0.0, step=0.01, decimals=3),
               Param("ground", u"地面の高さ", 0.0, step=0.5,
                     tooltip=u"この高さで跳ね返る (ワールドの Y)"),
               Param("bounce", u"はね返り", 0.4, minimum=0.0, maximum=1.0, step=0.1)])
    for p in base:
        if p.name == "life":
            p.default = 18.0
        if p.name == "life_random":
            p.default = 0.5
    if trail:
        base += [Param("segments", u"尾の分割数", 6, minimum=1, maximum=30),
                 Param("tail_frames", u"尾の長さ (フレーム)", 4.0, minimum=0.0, step=1.0, decimals=1,
                       tooltip=u"何フレーム前の位置までを尾にするか")]
    else:
        base.append(Param("length", u"線の長さ (速さの倍率)", 1.5, minimum=0.0, step=0.5,
                          tooltip=u"線の長さ = 速さ × この値"))
    return base + [Param("width", u"太さ", 0.03, minimum=0.0, step=0.01, decimals=3),
                   Param("color", u"色", (1.0, 0.85, 0.35), kind="color"),
                   Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1)] \
        + _common_tail(0.2)


class SparksTool(BaseTool):
    label = u"火花 (線)"
    tab = wx.TAB
    category = u"火花"
    order = 878
    tooltip = (u"進む向きに伸びた線が出る場所 (_emitter) から飛び、重力で落ちて地面で跳ねます。\n"
               u"オブジェクトを選んで作ると、出る場所がそれに付いて動きます。")
    params = _spark_params(False)

    def run(self, ctx, **v):
        return _create(ctx, v, SPARKS)


class SparkTrailTool(BaseTool):
    label = u"火花 (尾を引く)"
    tab = wx.TAB
    category = u"火花"
    order = 879
    tooltip = (u"火花が通った跡を、先が細くなる帯で引きます (帯はカメラを向きます)。\n"
               u"帯は火花ごとのジョイントにスキンしてあり、ベイクすれば FBX でも使えます。")
    params = _spark_params(True)

    def run(self, ctx, **v):
        return _create(ctx, v, SPARK_TRAIL)


def _spark_motion(r, age_var, pos_var, vel_var):
    """出てから age_var の火花の位置・速さ (spark_motion と同じ式)。"""
    return u"""vector {pv}; vector {vv};
{{
    vector $P = $E; vector $V = $dir * $spd; float $ra = {age};
    float $g = {r}.dfx_gravity; float $G = {r}.dfx_ground; float $bn = {r}.dfx_bounce;
    int $nb;
    for ($nb = 0; $nb < 3; $nb++) {{
        float $disc = $V.y * $V.y + 2 * $g * ($P.y - $G);
        if ($g <= 1e-6 || $P.y < $G - 1e-6 || $disc < 0) break;
        float $th = ($V.y + sqrt($disc)) / $g;
        if ($th >= $ra || $th <= 1e-4) break;
        $P = <<$P.x + $V.x * $th, $G, $P.z + $V.z * $th>>;
        float $vy = $V.y - $g * $th;
        $V = <<$V.x * 0.8, -$vy * $bn, $V.z * 0.8>>;
        $ra = $ra - $th;
    }}
    vector $pp = <<$P.x + $V.x * $ra, $P.y + $V.y * $ra - 0.5 * $g * $ra * $ra, $P.z + $V.z * $ra>>;
    if ($pp.y < $G && $P.y >= $G - 1e-6) $pp = <<$pp.x, $G, $pp.z>>;
    {pv} = $pp + $wd;
    {vv} = <<$V.x, $V.y - $g * $ra, $V.z>>;
}}
""".format(pv=pos_var, vv=vel_var, age=age_var, r=r)


def build_sparks(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    pts, faces, uvs = wx.card_points(-1.0, 0.0)
    src = wx.make_mesh(cmds, prefix + "_sparkSrc", src_grp, pts, faces, uvs)
    cmds.setAttr(src_grp + ".visibility", 0)
    shader, sg = wx.flat_shader(cmds, mu, root, prefix + "_mat", owned)
    cmds.sets(src, edit=True, forceElement=sg)
    count = int(v["count"])
    sparks = wx.make_instances(cmds, root, src, prefix + "_sparks", count, owned)
    head = wx.mel_head(root, need_camera=True) + _emitter_head(root)
    r = prefix

    def body(a, b):
        return (_spawn_body(r, count) + _spark_motion(r, "$a", "$p", "$vel")
                + wx.mel_streak("$p", "$vel")
                + u"""float $fade = 1;
if ($a > 0.7 * $life) $fade = max(0, ($life - $a) / (0.3 * $life));
float $w = {r}.dfx_width * $fade;
vector $S = <<$w, max(mag($vel) * {r}.dfx_length, $w), 1>>;
""".format(r=r) + wx.mel_store_transform(a))

    wx.chunked_expressions(cmds, root, prefix + "_spark", sparks, wx.TRANSFORM_OUTPUTS, head,
                           body, owned)
    wx.finish(cmds, mu, root, owned)


JOINT_OUTPUTS = wx.TRANSFORM_OUTPUTS[:9]
TRAIL_SPARKS_PER_EXPRESSION = 3


def build_spark_trail(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    count = int(v["count"])
    rows = int(v["segments"]) + 1
    # メッシュ: 火花ごとに rows 行の帯。行 k (全体の番号) の頂点は x = k (ジョイント k の位置)
    points, faces = [], []
    for s in range(count):
        for j in range(rows):
            k = s * rows + j
            points += [(float(k), 0.5, 0.0), (float(k), -0.5, 0.0)]
            if j < rows - 1:
                faces.append([2 * k, 2 * k + 1, 2 * k + 3, 2 * k + 2])
    mesh = wx.make_mesh(cmds, prefix + "_trails", root, points, faces)
    owned.append(mesh)
    jgrp = cmds.group(empty=True, name=prefix + "_joints", parent=root)
    jgrp = cmds.ls(jgrp, long=True)[0]
    owned.append(jgrp)
    joints = []
    for k in range(count * rows):
        jnt = cmds.createNode("joint", name="%s_jnt_%04d" % (prefix, k), parent=jgrp)
        cmds.setAttr(jnt + ".translate", float(k), 0, 0, type="double3")
        cmds.setAttr(jnt + ".drawStyle", 2)
        joints.append(cmds.ls(jnt, long=True)[0])
    skin = cmds.skinCluster(joints, mesh, toSelectedBones=True, bindMethod=0,
                            maximumInfluences=1, normalizeWeights=1, name=prefix + "_skin")[0]
    for k, jnt in enumerate(joints):
        cmds.skinPercent(skin, "%s.vtx[%d:%d]" % (mesh, 2 * k, 2 * k + 1),
                         transformValue=[(jnt, 1.0)])
    owned.append(skin)
    shader, sg = wx.flat_shader(cmds, mu, root, prefix + "_mat", owned)
    cmds.sets(mesh, edit=True, forceElement=sg)

    head = wx.mel_head(root, need_camera=True) + _emitter_head(root)
    r = prefix
    for a in range(0, count, TRAIL_SPARKS_PER_EXPRESSION):
        b = min(count, a + TRAIL_SPARKS_PER_EXPRESSION)
        body = _spawn_body(r, count) + u"""vector $rp[];
float $fade = 1;
if ($a > 0.7 * $life) $fade = max(0, ($life - $a) / (0.3 * $life));
if (!$vis) $fade = 0;
int $j;
for ($j = 0; $j < {rows}; $j++) {{
    float $aj = max(0, $a - {r}.dfx_tail_frames * $j / {seg});
""".format(rows=rows, r=r, seg=repr(float(rows - 1))) + _spark_motion(r, "$aj", "$pj", "$vj") + u"""    $rp[$j] = $pj;
}}
for ($j = 0; $j < {rows}; $j++) {{
    int $j0 = max(0, $j - 1);
    int $j1 = min({last}, $j + 1);
    vector $p = $rp[$j];
    vector $T = $rp[$j0] - $rp[$j1];
    vector $X = <<0, -1, 0>>;
    if (mag($T) > 1e-9) $X = unit($T);
    vector $tc = $cp - $p;
    if (mag($tc) < 1e-6) $tc = $cdir * -1.0;
""".format(rows=rows, last=rows - 1) + wx.mel_ortho("$Z", "$X", "$tc") + u"""    vector $Y = cross($Z, $X);
    float $w = {r}.dfx_width * $fade * (1 - $j / {seg1});
    vector $S = <<$w, $w, $w>>;
""".format(r=r, seg1=repr(float(rows))) + wx.mel_euler() + u"""    int $ob = (($i - {a}) * {rows} + $j) * 9;
    $o[$ob] = $p.x; $o[$ob + 1] = $p.y; $o[$ob + 2] = $p.z;
    $o[$ob + 3] = $rx; $o[$ob + 4] = $ry; $o[$ob + 5] = $rz;
    $o[$ob + 6] = $S.x; $o[$ob + 7] = $S.y; $o[$ob + 8] = $S.z;
}}
""".format(a=a, rows=rows)
        code = (head + u"float $o[];\nint $i;\nfor ($i = %d; $i < %d; $i++) {\n" % (a, b)
                + body + u"}\n" + wx.mel_writes(joints[a * rows:b * rows], JOINT_OUTPUTS))
        expr = cmds.expression(name="%s_trail_%02d" % (prefix, a // TRAIL_SPARKS_PER_EXPRESSION + 1),
                               alwaysEvaluate=True, unitConversion="all", string=code)
        owned.append(expr)
    wx.finish(cmds, mu, root, owned)


# ---------------------------------------------------------------- 天候共通のツール
def _create(ctx, v, kind):
    from maya import cmds
    from . import _maya_utils as mu
    return wx.create_effect(cmds, mu, ctx, v, kind)


class WeatherUpdateTool(BaseTool):
    label = u"天候を更新"
    tab = wx.TAB
    category = u"調整"
    order = 890
    tooltip = (u"選択した天候 (ルートか、その中の物) を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (数など) を変えた時に使います。範囲の箱・出る場所はそのまま。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return wx.rebuild_effects(cmds, mu, ctx, wx.selected_roots(cmds, mu, u"更新する"))


class WeatherSavePresetTool(BaseTool):
    label = u"天候のパラメータを保存"
    tab = wx.TAB
    category = u"調整"
    order = 892
    undoable = False
    tooltip = u"選択した天候 (風も可) 1 つのパラメータをプリセットファイル (.json) に保存します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = wx.selected_roots(cmds, mu, u"保存する")
        if len(roots) > 1:
            raise ToolError(u"保存する天候を 1 つだけ選択してください (現在 %d 個)" % len(roots))
        kind = wx.kind_of(cmds, mu, roots[0])
        return mu.save_preset_dialog(ctx, roots, kind.effect_type, kind.stored_params(),
                                     self.label, kind.noun)


class WeatherLoadPresetTool(BaseTool):
    label = u"天候のパラメータを読み込み"
    tab = wx.TAB
    category = u"調整"
    order = 894
    tooltip = (u"プリセットファイル (.json) のパラメータを選択した天候に設定し、作り直します。\n"
               u"同じ種類のプリセットだけ読み込めます。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = wx.selected_roots(cmds, mu, u"読み込み先の")
        kinds = set(wx.kind_of(cmds, mu, r).effect_type for r in roots)
        if len(kinds) > 1:
            raise ToolError(u"読み込み先は同じ種類の天候だけ選択してください")
        kind = wx.KINDS[kinds.pop()]
        values = mu.load_preset_dialog(ctx, kind.effect_type, kind.stored_params(), self.label)
        if values is None:
            return None
        return wx.rebuild_effects(cmds, mu, ctx, roots, overrides=values)


# ---------------------------------------------------------------- 種類の登録
_CREATE_ONLY = ("area_width", "area_height", "follow_camera")


def _live(tool, rebuild):
    return tuple(p.name for p in tool.params if p.name not in rebuild and p.name not in _CREATE_ONLY)


RAIN = wx.register(wx.Kind("rain", RainTool, "dfx_rain#", u"雨 (線と跳ね)",
                           _live(RainTool, ("count",)), ("count",), build=build_rain,
                           create=_create_area, need_camera=True))
SNOW = wx.register(wx.Kind("snow", SnowTool, "dfx_snow#", u"雪 (粒)",
                           _live(SnowTool, ("count",)), ("count",), build=build_snow,
                           create=_create_area, need_camera=True))
DUST = wx.register(wx.Kind("dust", DustTool, "dfx_dust#", u"土煙 (スプライト)",
                           _live(DustTool, ("count", "texture")), ("count", "texture"),
                           build=build_dust,
                           create=_create_emitter, need_camera=True))
DUST_MESH = wx.register(wx.Kind("dust_mesh", DustMeshTool, "dfx_dustMesh#", u"土煙 (もこもこ)",
                                _live(DustMeshTool, ("count",)), ("count",),
                                build=lambda cmds, mu, root, v: build_dust(cmds, mu, root, v, mesh=True),
                                create=_create_emitter))
SPARKS = wx.register(wx.Kind("sparks", SparksTool, "dfx_sparks#", u"火花 (線)",
                             _live(SparksTool, ("count",)), ("count",), build=build_sparks,
                             create=_create_emitter, need_camera=True))
SPARK_TRAIL = wx.register(wx.Kind("spark_trail", SparkTrailTool, "dfx_sparkTrail#", u"火花 (尾を引く)",
                                  _live(SparkTrailTool, ("count", "segments")), ("count", "segments"),
                                  build=build_spark_trail, create=_create_emitter, need_camera=True))
