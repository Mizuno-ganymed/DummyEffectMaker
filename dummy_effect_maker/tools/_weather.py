# -*- coding: utf-8 -*-
"""天候タブ (風・雨・雪・土煙・火花・霧・地霧) の共通処理。

ファイル名が "_" で始まるのでツールとしては登録されない。

作り方の方針 (他のエフェクトと同じ):
    nParticle (シミュレーション) は使わず、粒 1 つ 1 つの位置を「発生フレーム・初速・重力・風」
    から計算式で決める。どのフレームに飛んでも同じ結果になり、ベイクすればキーだけで動く。

    - 粒は元のメッシュのインスタンス (transform) で、エクスプレッションが毎フレーム置く
    - エクスプレッションは CHUNK 個ずつに分ける (1 つに全部書くと作成が粒の数の 2 乗で遅くなる)
    - 乱数は hash01 (sin を使ったハッシュ)。MEL と Python で同じ値になる
    - 風はルートのメッセージ (dfx_wind) につないだ風 (dfx_windN) の「流された距離」の表を読む
      (風を更新した時に、毎フレームの値を風のアトリビュート (数の配列) に書く)
"""
from __future__ import absolute_import

import math
import os
import random

from ..core import ToolError

TAB = u"天候"
WIND = "wind"
WIND_ATTR = "dfx_wind"            # 天候のルート → 風 (メッセージ)
CAMERA_ATTR = "dfx_camera"        # 天候のルート → カメラ
AREA_ATTR = "dfx_area"            # 天候のルート → 範囲の箱
EMITTER_ATTR = "dfx_emitter"      # 天候のルート → 出る場所のロケータ
STRIKE_ATTR = "dfx_strike"        # 天候のルート → 落ちる所のロケータ (稲妻)
LINK_ATTRS = (WIND_ATTR, CAMERA_ATTR, AREA_ATTR, EMITTER_ATTR, STRIKE_ATTR)

# 風のルートに持たせる毎フレームの表 (doubleArray。パラメータではないので dfx_ で始めない)
DRIFT_ATTRS = ("dfxDriftX", "dfxDriftY", "dfxDriftZ")   # 風に流された距離 (積分)
WIND_VEC_ATTRS = ("dfxWindX", "dfxWindY", "dfxWindZ")   # その時の風 (1 フレームに流れる距離)
DRIFT_START_ATTR = "dfxDriftStart"                       # 表の最初のフレーム
WIND_PREROLL = 400       # タイムスライダーより前も表に入れるフレーム数 (前から降っている雪など)
WIND_POSTROLL = 60

CHUNK = 10               # エクスプレッション 1 つが受け持つ粒の数 (多いと作成が遅く、少ないと再生が遅い)
TEXTURE_DIR = "dfx_weather"

KINDS = {}               # effect_type → Kind (各モジュールが登録する)


# ---------------------------------------------------------------- 純粋な計算
def hash01(i, c, k, seed):
    """0〜1 の乱数 (MEL の mel_hash と同じ式)。"""
    x = math.sin(i * 12.9898 + c * 78.233 + k * 37.719 + seed * 4.1414) * 43758.5453
    return math.fmod(abs(x), 1.0)


def mel_hash(i, c, k):
    """hash01 と同じ式の MEL (乱数の種は変数 $sd)。"""
    return ("fmod(abs(sin(({i}) * 12.9898 + ({c}) * 78.233 + {k} * 37.719 + $sd * 4.1414)"
            " * 43758.5453), 1.0)").format(i=i, c=c, k="%.1f" % k)


def smooth_noise(t, seed, period, salt=0):
    """なめらかな乱数 (-1〜1)。period フレームごとの乱数を smoothstep でつなぐ。"""
    period = max(period, 1e-3)
    u = t / period
    k = math.floor(u)
    f = u - k
    f = f * f * (3 - 2 * f)
    a = hash01(k, salt, 3, seed) * 2 - 1
    b = hash01(k + 1, salt, 3, seed) * 2 - 1
    return a + (b - a) * f


def gust_factor(t, seed, interval, length, randomness):
    """突風 (0〜): interval ごと (ばらつきあり) に、長さ length の山 (sin^2) が来る。"""
    interval = max(interval, 1.0)
    length = max(length, 1.0)
    k0 = int(math.floor(t / interval)) - int(math.ceil(length / interval)) - 1
    total = 0.0
    for k in range(k0, k0 + int(math.ceil(length / interval)) + 3):
        start = k * interval + (hash01(k, 7, 1, seed) - 0.5) * randomness * interval
        amp = 1.0 + (hash01(k, 7, 2, seed) - 0.5) * randomness
        if start <= t <= start + length:
            total += amp * math.sin(math.pi * (t - start) / length) ** 2
    return total


def wind_vector(direction, strength, gust, gust_amount, turbulence, t, seed):
    """その時の風 (1 フレームに流れる距離)。direction は水平の単位ベクトル。"""
    s = strength * (1.0 + gust_amount * gust)
    ang = math.radians(45.0 * turbulence * smooth_noise(t, seed, 30.0, salt=11))
    c, sn = math.cos(ang), math.sin(ang)
    dx = direction[0] * c - direction[2] * sn
    dz = direction[0] * sn + direction[2] * c
    up = turbulence * 0.3 * smooth_noise(t, seed, 20.0, salt=13)
    return (dx * s, direction[1] * s + up * strength, dz * s)


def integrate(values):
    """毎フレームの風 → 流された距離 (台形公式。最初のフレームを 0)。"""
    out = [(0.0, 0.0, 0.0)]
    for a, b in zip(values[:-1], values[1:]):
        p = out[-1]
        out.append(tuple(p[i] + (a[i] + b[i]) / 2.0 for i in range(3)))
    return out


def table_value(table, start, t):
    """毎フレームの表 (start から) の t の値 (直線補間、範囲外は端の値)。mel_table と同じ。"""
    n = len(table)
    if n == 0:
        return (0.0, 0.0, 0.0)
    if n == 1:
        return table[0]
    u = min(max(t - start, 0.0), n - 1.0)
    k = min(int(math.floor(u)), n - 2)
    f = u - k
    return tuple(a + (b - a) * f for a, b in zip(table[k], table[k + 1]))


# ---------------------------------------------------------------- MEL の断片
# カメラ・範囲の箱・出る場所の位置は、getAttr の文字列ではなく decomposeMatrix の出力を
# 直接読む (エクスプレッションの入力として接続される)。そうしないと Maya はつながりを知らず、
# フレームを変えずに箱やカメラを動かした時に、雨などが付いてこない。
TRACK_SUFFIX = {CAMERA_ATTR: "_cameraXform", AREA_ATTR: "_areaXform", EMITTER_ATTR: "_emitterXform",
                STRIKE_ATTR: "_strikeXform"}


def track_name(root, attr):
    return short(root) + TRACK_SUFFIX[attr]


def track_links(cmds, mu, root, owned):
    """root につながったカメラ・範囲の箱・出る場所のワールド行列を分解するノードを作る。"""
    try:
        cmds.loadPlugin("matrixNodes", quiet=True)
    except RuntimeError:
        pass
    made = {}
    for attr in (CAMERA_ATTR, AREA_ATTR, EMITTER_ATTR, STRIKE_ATTR):
        node = find_link(cmds, root, attr)
        if not node:
            continue
        dm = cmds.createNode("decomposeMatrix", name=track_name(root, attr))
        cmds.connectAttr(node + ".worldMatrix[0]", dm + ".inputMatrix")
        owned.append(dm)
        made[attr] = dm
    return made


def mel_rows(dm, p):
    """decomposeMatrix の回転 (xyz) → 軸 ${p}R0 / ${p}R1 / ${p}R2 (行ベクトル。rotation_rows と同じ)。"""
    return u"""float ${p}a1 = deg_to_rad({dm}.outputRotateX);
float ${p}a2 = deg_to_rad({dm}.outputRotateY);
float ${p}a3 = deg_to_rad({dm}.outputRotateZ);
float ${p}c1 = cos(${p}a1); float ${p}s1 = sin(${p}a1);
float ${p}c2 = cos(${p}a2); float ${p}s2 = sin(${p}a2);
float ${p}c3 = cos(${p}a3); float ${p}s3 = sin(${p}a3);
vector ${p}R0 = <<${p}c2 * ${p}c3, ${p}c2 * ${p}s3, -${p}s2>>;
vector ${p}R1 = <<${p}s1 * ${p}s2 * ${p}c3 - ${p}c1 * ${p}s3, ${p}s1 * ${p}s2 * ${p}s3 + ${p}c1 * ${p}c3, ${p}s1 * ${p}c2>>;
vector ${p}R2 = <<${p}c1 * ${p}s2 * ${p}c3 + ${p}s1 * ${p}s3, ${p}c1 * ${p}s2 * ${p}s3 - ${p}s1 * ${p}c3, ${p}c1 * ${p}c2>>;
""".format(dm=dm, p=p)


def mel_head(root, need_camera=False, need_wind=True):
    """エクスプレッションの先頭: 乱数の種 $sd、カメラの位置 $cp と向き $cdir、風の表。"""
    r = root.split("|")[-1]
    code = [u"float $sd = {r}.dfx_seed;\n".format(r=r)]
    if need_camera:
        dm = track_name(root, CAMERA_ATTR)
        code.append(mel_rows(dm, "c") + u"""vector $cp = <<{dm}.outputTranslateX, {dm}.outputTranslateY, {dm}.outputTranslateZ>>;
vector $cdir = $cR2 * -1.0;
""".format(dm=dm))
    if need_wind:
        code.append(u"""float $wf = {r}.dfx_wind_influence;
float $wdx[]; float $wdy[]; float $wdz[];
float $wvx[]; float $wvy[]; float $wvz[];
float $w0 = 0;
int $wn = 0;
string $wl[];
if (`objExists "{root}.{attr}"`) $wl = `listConnections -source 1 -destination 0 "{root}.{attr}"`;
if (size($wl) > 0) {{
    string $w = $wl[0];
    if (`attributeQuery -node $w -exists "{dx}"`) {{
        $wdx = `getAttr ($w + ".{dx}")`;
        $wdy = `getAttr ($w + ".{dy}")`;
        $wdz = `getAttr ($w + ".{dz}")`;
        $wvx = `getAttr ($w + ".{vx}")`;
        $wvy = `getAttr ($w + ".{vy}")`;
        $wvz = `getAttr ($w + ".{vz}")`;
        $w0 = `getAttr ($w + ".{st}")`;
        $wn = min(size($wdx), size($wvx));
    }}
}}
""".format(r=r, root=root, attr=WIND_ATTR, dx=DRIFT_ATTRS[0], dy=DRIFT_ATTRS[1],
           dz=DRIFT_ATTRS[2], vx=WIND_VEC_ATTRS[0], vy=WIND_VEC_ATTRS[1],
           vz=WIND_VEC_ATTRS[2], st=DRIFT_START_ATTR))
        code.append(mel_table("$Dnow", "$wd", "frame"))
        code.append(mel_table("$Wnow", "$wv", "frame"))
    return u"".join(code)


def mel_table(dst, prefix, t):
    """風の表 (prefix + x/y/z) の時刻 t の値を vector dst に入れる (table_value と同じ)。"""
    return u"""vector {d} = <<0, 0, 0>>;
if ($wn == 1) {d} = <<{p}x[0], {p}y[0], {p}z[0]>>;
if ($wn > 1) {{
    float $tu = clamp(0, $wn - 1.0, ({t}) - $w0);
    int $tk = floor($tu);
    if ($tk > $wn - 2) $tk = $wn - 2;
    float $tf = $tu - $tk;
    {d} = <<{p}x[$tk] + ({p}x[$tk + 1] - {p}x[$tk]) * $tf,
           {p}y[$tk] + ({p}y[$tk + 1] - {p}y[$tk]) * $tf,
           {p}z[$tk] + ({p}z[$tk + 1] - {p}z[$tk]) * $tf>>;
}}
""".format(d=dst, p=prefix, t=t)


def mel_matrix(var, node_expr, time_expr=None):
    """ノードのワールド行列を float 配列 var に。"""
    if time_expr is None:
        return u'float {v}[] = `getAttr ({n} + ".worldMatrix[0]")`;\n'.format(v=var, n=node_expr)
    return u'float {v}[] = `getAttr -time ({t}) ({n} + ".worldMatrix[0]")`;\n'.format(
        v=var, n=node_expr, t=time_expr)


def mel_xf(m, p):
    """点 p (vector の式) × 行列 m (float[16]) の vector の式。"""
    return (u"<<{p}.x * {m}[0] + {p}.y * {m}[4] + {p}.z * {m}[8] + {m}[12], "
            u"{p}.x * {m}[1] + {p}.y * {m}[5] + {p}.z * {m}[9] + {m}[13], "
            u"{p}.x * {m}[2] + {p}.y * {m}[6] + {p}.z * {m}[10] + {m}[14]>>").format(m=m, p=p)


def mel_xv(m, p):
    """向き p × 行列 m (移動なし)。"""
    return (u"<<{p}.x * {m}[0] + {p}.y * {m}[4] + {p}.z * {m}[8], "
            u"{p}.x * {m}[1] + {p}.y * {m}[5] + {p}.z * {m}[9], "
            u"{p}.x * {m}[2] + {p}.y * {m}[6] + {p}.z * {m}[10]>>").format(m=m, p=p)


def mel_ortho(dst, primary, cand):
    """dst = cand から primary の成分を除いて正規化 (平行なら適当な直交する向き)。"""
    return u"""vector {d} = ({c}) - dot(({c}), {p}) * {p};
if (mag({d}) < 1e-6) {d} = cross({p}, <<0, 0, 1>>);
if (mag({d}) < 1e-6) {d} = cross({p}, <<1, 0, 0>>);
{d} = unit({d});
""".format(d=dst, p=primary, c=cand)


def mel_billboard(pos, roll=None):
    """カメラを向く板の軸 $X $Y $Z (Z がカメラの方、Y がおおむね上)。roll は回転 (度) の式。"""
    code = [u"vector $Z = $cp - {p};\n".format(p=pos),
            u"if (mag($Z) < 1e-6) $Z = $cdir * -1.0;\n$Z = unit($Z);\n",
            mel_ortho("$Y", "$Z", "<<0, 1, 0>>"),
            u"vector $X = cross($Y, $Z);\n"]
    if roll:
        code.append(u"""float $ra = deg_to_rad({r});
vector $X2 = $X * cos($ra) + $Y * sin($ra);
$Y = $Y * cos($ra) - $X * sin($ra);
$X = $X2;
""".format(r=roll))
    return u"".join(code)


def mel_streak(pos, vel):
    """進む向き (vel) に伸び、カメラを向く板の軸 ($Y が進む向き)。"""
    return (u"vector $Y = {v};\nif (mag($Y) < 1e-9) $Y = <<0, -1, 0>>;\n$Y = unit($Y);\n"
            u"vector $tc = $cp - {p};\nif (mag($tc) < 1e-6) $tc = $cdir * -1.0;\n").format(v=vel, p=pos) + \
        mel_ortho("$Z", "$Y", "$tc") + u"vector $X = cross($Y, $Z);\n"


def mel_euler():
    """$X $Y $Z (正規直交) → $rx $ry $rz (回転順 xyz)。"""
    return u"""float $rx; float $ry; float $rz;
float $eh = sqrt($X.x * $X.x + $X.y * $X.y);
$ry = atan2d(-$X.z, $eh);
if ($eh > 1e-6) { $rx = atan2d($Y.z, $Z.z); $rz = atan2d($X.y, $X.x); }
else { $rx = 0.0; $rz = atan2d(-$Y.x, $Y.y); }
"""


TRANSFORM_OUTPUTS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ",
                     "scaleX", "scaleY", "scaleZ", "visibility")


def mel_store_transform(index_offset):
    """$p / ($X $Y $Z) / $S / $vis を出力の配列 $o に入れる (TRANSFORM_OUTPUTS の順)。"""
    return mel_euler() + u"""int $ob = ($i - {a}) * 10;
$o[$ob] = $p.x; $o[$ob + 1] = $p.y; $o[$ob + 2] = $p.z;
$o[$ob + 3] = $rx; $o[$ob + 4] = $ry; $o[$ob + 5] = $rz;
$o[$ob + 6] = $S.x; $o[$ob + 7] = $S.y; $o[$ob + 8] = $S.z;
$o[$ob + 9] = $vis;
""".format(a=index_offset)


def mel_writes(nodes, outputs, start_index=0):
    """$o の値を nodes の outputs に書く (エクスプレッションの出力として接続される)。"""
    lines = []
    n_out = len(outputs)
    for i, node in enumerate(nodes):
        short = node.split("|")[-1]
        lines.append(u" ".join(u"%s.%s = $o[%d];" % (short, a, (start_index + i) * n_out + k)
                               for k, a in enumerate(outputs)) + u"\n")
    return u"".join(lines)


# ---------------------------------------------------------------- Maya の構築
def short(node):
    return node.split("|")[-1]


def find_link(cmds, root, attr):
    if not cmds.attributeQuery(attr, node=root, exists=True):
        return None
    conns = cmds.listConnections(root + "." + attr, source=True, destination=False) or []
    return cmds.ls(conns[0], long=True)[0] if conns else None


def link(cmds, root, attr, node):
    if not cmds.attributeQuery(attr, node=root, exists=True):
        cmds.addAttr(root, longName=attr, attributeType="message")
    for src in cmds.listConnections(root + "." + attr, source=True, destination=False,
                                    plugs=True) or []:
        cmds.disconnectAttr(src, root + "." + attr)
    if node:
        cmds.connectAttr(node + ".message", root + "." + attr)


def wind_roots(cmds, mu):
    return [r for r in mu.all_effect_roots()
            if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) == WIND and not mu.is_baked(r)]


def choose_wind(cmds, mu):
    """選択中の風 → シーンの最初の風 → None。"""
    selected = mu.selected_effect_roots(WIND)
    if selected:
        return selected[0]
    winds = wind_roots(cmds, mu)
    return winds[0] if winds else None


def choose_camera(cmds, ctx=None):
    """選択中のカメラ → アクティブなビューのカメラ → persp。"""
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if cmds.objectType(node, isAType="camera"):
            return cmds.listRelatives(node, parent=True, fullPath=True)[0]
        if cmds.listRelatives(node, shapes=True, type="camera"):
            return node
    if not cmds.about(batch=True):
        panel = cmds.getPanel(withFocus=True)
        if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
            cam = cmds.modelPanel(panel, query=True, camera=True)
            if cam:
                if cmds.objectType(cam, isAType="camera"):
                    cam = cmds.listRelatives(cam, parent=True, fullPath=True)[0]
                return cmds.ls(cam, long=True)[0]
    found = cmds.ls("persp", long=True, type="transform")
    if found:
        return found[0]
    raise ToolError(u"カメラを選択してください (板をカメラに向けるのに使います)")


def selected_target(cmds, mu):
    """選択中の (カメラ・天候以外の) transform。出る場所を付けて動かす相手。"""
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if not cmds.objectType(node, isAType="transform"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            node = parents[0] if parents else None
        if not node or cmds.listRelatives(node, shapes=True, type="camera"):
            continue
        parts = node.split("|")
        inside = any(cmds.attributeQuery(mu.EFFECT_TYPE_ATTR, node="|".join(parts[:i]), exists=True)
                     for i in range(2, len(parts) + 1))
        if not inside:
            return node
    return None


def box_curve(cmds, name, parent):
    """-0.5〜0.5 の箱のワイヤー (範囲の表示)。"""
    pts = [(-.5, -.5, -.5), (.5, -.5, -.5), (.5, -.5, .5), (-.5, -.5, .5), (-.5, -.5, -.5),
           (-.5, .5, -.5), (.5, .5, -.5), (.5, -.5, -.5), (.5, .5, -.5), (.5, .5, .5),
           (.5, -.5, .5), (.5, .5, .5), (-.5, .5, .5), (-.5, -.5, .5), (-.5, .5, .5),
           (-.5, .5, -.5)]
    node = cmds.curve(degree=1, point=pts, name=name)
    node = cmds.ls(cmds.parent(node, parent, relative=True)[0], long=True)[0]
    shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 18)  # 水色
    return node


def create_area(cmds, mu, root, size, follow_camera, camera, ground=0.0):
    """範囲の箱 (_area) を作る。follow_camera ならカメラの X / Z に付いて動く。"""
    prefix = short(root)
    area = box_curve(cmds, prefix + "_area", root)
    width, height, depth = size
    cmds.setAttr(area + ".scale", width, height, depth, type="double3")
    if follow_camera and camera:
        try:
            cmds.loadPlugin("matrixNodes", quiet=True)
        except RuntimeError:
            pass
        dm = cmds.createNode("decomposeMatrix", name=prefix + "_cameraPos")
        cm = cmds.createNode("composeMatrix", name=prefix + "_cameraXZ")
        cmds.connectAttr(camera + ".worldMatrix[0]", dm + ".inputMatrix")
        cmds.connectAttr(dm + ".outputTranslateX", cm + ".inputTranslateX")
        cmds.connectAttr(dm + ".outputTranslateZ", cm + ".inputTranslateZ")
        cmds.connectAttr(cm + ".outputMatrix", area + ".offsetParentMatrix")
        mu.tag(dm, cm)
        cmds.setAttr(area + ".translate", 0, ground + height / 2.0, 0, type="double3")
    else:
        cmds.setAttr(area + ".translate", 0, ground + height / 2.0, 0, type="double3")
    mu.tag(area)
    link(cmds, root, AREA_ATTR, area)
    return area


def create_emitter(cmds, mu, root, target):
    """出る場所のロケータ (_emitter)。target があれば、それに付いて動く (位置は target の原点)。"""
    prefix = short(root)
    loc = cmds.spaceLocator(name=prefix + "_emitter")[0]
    loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
    shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)  # 黄
    if target:
        cmds.connectAttr(target + ".worldMatrix[0]", loc + ".offsetParentMatrix")
    mu.tag(loc)
    link(cmds, root, EMITTER_ATTR, loc)
    return loc


def make_instances(cmds, root, src, name, count, owned, copies=False):
    """src のインスタンスを count 個、ルートの子のグループに作る。

    copies=True ならインスタンスではなく別々のメッシュ (板ごとに違うマテリアルを付ける時。
    インスタンスごとにマテリアルを変えると Arnold では表示されない)。
    """
    grp = cmds.group(empty=True, name=name, parent=root)
    grp = cmds.ls(grp, long=True)[0]
    owned.append(grp)
    if copies:
        nodes = [cmds.duplicate(src, name="%s_%03d" % (name, i + 1))[0] for i in range(count)]
    else:
        nodes = [cmds.instance(src, name="%s_%03d" % (name, i + 1))[0] for i in range(count)]
    if nodes:
        cmds.parent(nodes, grp)  # 1 つずつ親を変えると遅いので、まとめて
    return cmds.listRelatives(grp, children=True, fullPath=True) or []


def make_mesh(cmds, name, parent, points, faces, uvs=None, uv_ids=None):
    """頂点 (x, y, z) と面 (頂点番号のリスト) からメッシュを作る。"""
    from maya.api import OpenMaya as om
    xform = cmds.createNode("transform", name=name, parent=parent)
    xform = cmds.ls(xform, long=True)[0]
    sel = om.MSelectionList()
    sel.add(xform)
    counts = [len(f) for f in faces]
    connects = [i for f in faces for i in f]
    fn = om.MFnMesh()
    if uvs:
        fn.create([om.MPoint(*p) for p in points], counts, connects,
                  [u for u, _ in uvs], [v for _, v in uvs], sel.getDependNode(0))
        fn.assignUVs(counts, uv_ids or connects)
    else:
        fn.create([om.MPoint(*p) for p in points], counts, connects, parent=sel.getDependNode(0))
    shape = cmds.listRelatives(xform, shapes=True, fullPath=True)[0]
    cmds.rename(shape, short(xform) + "Shape")
    shape = cmds.listRelatives(xform, shapes=True, fullPath=True)[0]
    for a in ("castsShadows", "receiveShadows"):
        cmds.setAttr(shape + "." + a, 0)
    return xform


def card_points(y0=-0.5, y1=0.5):
    """XY 面の板 (Z を向く)。幅 1、y0〜y1。UV 付き。"""
    pts = [(-0.5, y0, 0.0), (0.5, y0, 0.0), (0.5, y1, 0.0), (-0.5, y1, 0.0)]
    return pts, [[0, 1, 2, 3]], [(0, 0), (1, 0), (1, 1), (0, 1)]


def disc_points(sides=12, radius=0.5, inner=0.0):
    """XY 面の円 (inner > 0 なら輪)。"""
    if inner <= 0:
        pts = [(radius * math.cos(2 * math.pi * k / sides),
                radius * math.sin(2 * math.pi * k / sides), 0.0) for k in range(sides)]
        return pts, [list(range(sides))]
    pts = []
    for k in range(sides):
        a = 2 * math.pi * k / sides
        pts.append((radius * math.cos(a), radius * math.sin(a), 0.0))
        pts.append((inner * math.cos(a), inner * math.sin(a), 0.0))
    faces = [[2 * k, 2 * ((k + 1) % sides), 2 * ((k + 1) % sides) + 1, 2 * k + 1]
             for k in range(sides)]
    return pts, faces


def chunked_expressions(cmds, root, name, nodes, outputs, head, body, owned):
    """nodes を CHUNK 個ずつに分けて、エクスプレッションを作る。

    head: 共通の先頭の MEL。body(a, b): 粒 $i (a <= $i < b) の値を $o に入れる MEL。
    """
    exprs = []
    for a in range(0, len(nodes), CHUNK):
        b = min(len(nodes), a + CHUNK)
        code = head + u"float $o[];\nint $i;\nfor ($i = %d; $i < %d; $i++) {\n" % (a, b) \
            + body(a, b) + u"}\n" + mel_writes(nodes[a:b], outputs)
        expr = cmds.expression(name="%s_%02d" % (name, a // CHUNK + 1), alwaysEvaluate=True,
                               unitConversion="all", string=code)
        exprs.append(expr)
    owned.extend(exprs)
    return exprs


def flat_shader(cmds, mu, root, name, owned, color_attr="color", opacity_attr="opacity"):
    """ライティングの影響を受けない単色のマテリアル (色・不透明度はルートとつなぐ)。

    半透明でも色は「色そのまま」(不透明度を掛けない)。ビューポート (Viewport 2.0)・
    Hardware 2.0・Arnold は、色 × 不透明度 + 後ろ × (1 - 不透明度) で重ねるため。
    (Maya Software だけは色に不透明度を掛けておく決まりなので、半透明の所が明るく出る)
    """
    plug = "%s.%s" % (root, mu.PARAM_PREFIX)
    shader, sg = mu.create_surface_shader(name, (1, 1, 1))
    cmds.connectAttr(plug + color_attr, shader + ".outColor", force=True)
    if opacity_attr:
        inv = cmds.createNode("reverse", name=name + "_transparency")
        for axis in "XYZ":
            cmds.connectAttr(plug + opacity_attr, inv + ".input" + axis)
        cmds.connectAttr(inv + ".output", shader + ".outTransparency", force=True)
        owned.append(inv)
    owned.extend([shader, sg])
    return shader, sg


def texture_shader(cmds, mu, root, name, texture, owned, color_attr="color", opacity_attr=None):
    """テクスチャ (透明度付き PNG) を貼るマテリアル。色はテクスチャ × ルートの色。

    戻り値 (shader, sg, file, place2d)。不透明度は file.alphaGain で決める。
    opacity_attr を渡すと、ルートの不透明度を alphaGain につなぐ。
    color_attr が None なら色・濃さはつながない (エクスプレッションが colorGain / alphaGain を決める)。
    """
    shader, sg = mu.create_surface_shader(name, (1, 1, 1))
    place = cmds.shadingNode("place2dTexture", asUtility=True, name=name + "_place")
    tex = cmds.shadingNode("file", asTexture=True, name=name + "_file")
    for a in ("coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV", "stagger",
              "wrapU", "wrapV", "repeatUV", "offset", "rotateUV", "noiseUV",
              "vertexUvOne", "vertexUvTwo", "vertexUvThree", "vertexCameraOne"):
        cmds.connectAttr(place + "." + a, tex + "." + a)
    cmds.connectAttr(place + ".outUV", tex + ".uvCoord")
    cmds.connectAttr(place + ".outUvFilterSize", tex + ".uvFilterSize")
    cmds.setAttr(tex + ".fileTextureName", texture, type="string")
    plug = "%s.%s" % (root, mu.PARAM_PREFIX)
    if color_attr:
        cmds.connectAttr(plug + color_attr, tex + ".colorGain")
    if color_attr and opacity_attr:
        cmds.connectAttr(plug + opacity_attr, tex + ".alphaGain")
    cmds.connectAttr(tex + ".outColor", shader + ".outColor", force=True)
    cmds.connectAttr(tex + ".outTransparency", shader + ".outTransparency", force=True)
    owned.extend([shader, sg, place, tex])
    return shader, sg, tex, place


# ---------------------------------------------------------------- テクスチャ (PNG を作る)
def texture_path(cmds, name):
    """作るテクスチャのパス (プロジェクトの sourceimages/dfx_weather/)。無ければ作る。"""
    root = cmds.workspace(query=True, rootDirectory=True) or os.path.expanduser("~")
    rule = cmds.workspace(fileRuleEntry="sourceImages") or "sourceimages"
    folder = os.path.join(root, rule, TEXTURE_DIR)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    from .. import textures
    path = os.path.join(folder, textures.file_name(name)).replace("\\", "/")
    if not os.path.isfile(path):
        textures.make(name, path)
    return path


TEXTURE_PARAM = "texture"


def _texture_version():
    from .. import textures
    return textures.VERSION


def texture_param(default_name, tiling):
    """「テクスチャのファイル」のパラメータ (空欄ならツールが作る既定の絵)。"""
    from ..core import Param
    tip = (u"貼る絵のファイル (透明度付きの PNG など)。空欄ならツールが作る既定の絵 "
           u"(sourceimages/dfx_weather/dfx_%s_v%d.png)。\n"
           u"白っぽく描くと「色」で色を付けられる。" % (default_name, _texture_version()))
    if tiling:
        tip += u"\nこの絵は繰り返して貼るので、上下 (と左右) の端がつながる絵にする"
    return Param(TEXTURE_PARAM, u"テクスチャのファイル", u"", kind="file", tooltip=tip)


def resolve_texture(cmds, v, default_name):
    """パラメータで指定したテクスチャ (無ければ既定の絵を作って) のパス。"""
    path = (v.get(TEXTURE_PARAM) or u"").strip()
    if not path:
        return texture_path(cmds, default_name)
    path = path.replace("\\", "/")
    if not os.path.isfile(path):
        raise ToolError(u"テクスチャのファイルが見つかりません: %s" % path)
    return path




# ---------------------------------------------------------------- 種類と共通のツール処理
class Kind(object):
    """天候 1 種類分。

    create(cmds, mu, root, v, ctx): 範囲・出る場所などの作り直さないもの (作成時だけ)
    build(cmds, mu, root, v): 粒・マテリアル・エクスプレッション (作成時と更新時)
    """

    def __init__(self, effect_type, tool, root_name, noun, live, rebuild, build,
                 create=None, need_camera=False, need_wind=True, keep=()):
        self.effect_type = effect_type
        self.tool = tool
        self.root_name = root_name
        self.noun = noun
        self.live = tuple(live)
        self.rebuild = tuple(rebuild)
        self.build = build
        self.create = create
        self.need_camera = need_camera
        self.need_wind = need_wind
        self.keep = tuple(keep)

    def stored_params(self):
        by_name = dict((p.name, p) for p in self.tool.params)
        return [by_name[n] for n in self.live + self.rebuild]


def register(kind):
    KINDS[kind.effect_type] = kind
    return kind


def weather_roots(cmds, mu, include_wind=True):
    return [r for r in mu.all_effect_roots()
            if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in KINDS
            and (include_wind or cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) != WIND)]


def selected_roots(cmds, mu, purpose, types=None):
    types = types or tuple(KINDS)
    roots = [r for r in mu.selected_effect_roots()
             if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in types]
    if not roots:
        raise ToolError(u"%s天候 (dfx_rainN など、またはその中の物) を選択してください" % purpose)
    return roots


def kind_of(cmds, mu, root):
    return KINDS[cmds.getAttr(root + "." + mu.EFFECT_TYPE_ATTR)]


# 色・透明度をテクスチャ (画像・ランプ) で作る種類。ビューポートのテクスチャ表示が OFF だと黒くなる
TEXTURED_TYPES = ("dust", "dust_mesh", "wisp", "rain_far", "snow_far", "fog", "ground_fog", "ground_crawl")
TEXTURE_HINT = (u"  ※ ビューポートのテクスチャ表示が OFF のため、黒く (不透明に) 表示されます。"
                u"ビューポートで 6 キー (シェーディング → ハードウェアテクスチャリング) を押すと正しく表示されます")


def panels_without_textures(cmds):
    """テクスチャ表示が OFF の、表示中のビューポート (シェーディング表示のもの)。バッチでは空。"""
    if cmds.about(batch=True):
        return []
    panels = []
    for panel in cmds.getPanel(visiblePanels=True) or []:
        if cmds.getPanel(typeOf=panel) != "modelPanel":
            continue
        if cmds.modelEditor(panel, query=True, displayAppearance=True) not in ("smoothShaded",
                                                                               "flatShaded"):
            continue
        if not cmds.modelEditor(panel, query=True, displayTextures=True):
            panels.append(panel)
    return panels


def warn_if_textures_hidden(cmds, ctx):
    """テクスチャ表示が OFF のビューポートがあればログで知らせる。知らせたら True。"""
    if panels_without_textures(cmds):
        ctx.log(TEXTURE_HINT)
        return True
    return False


# 半透明が見どころの種類。ハードウエアレンダリングの透明度が「アルファ カット」だと消える
TRANSLUCENT_TYPES = ("rain", "rain_far", "snow_far", "fog", "fog_depth", "ground_fog",
                     "ground_crawl", "lightning_real", "lightning_anime", "wisp")
ALPHA_CUT = 5   # hardwareRenderingGlobals.transparencyAlgorithm の「アルファ カット」
ALPHA_CUT_HINT = (u"  ※ ハードウエアレンダリングの透明度のアルゴリズムが「アルファ カット」のため、"
                  u"半透明の部分がビューポートと Hardware 2.0 で消えます。"
                  u"Viewport 2.0 の設定 (またはレンダー設定) で「オブジェクトのソート」にしてください "
                  u"(Arnold には影響しません)")


def alpha_cut_enabled(cmds):
    try:
        return cmds.getAttr("hardwareRenderingGlobals.transparencyAlgorithm") == ALPHA_CUT
    except (RuntimeError, ValueError):
        return False


def warn_if_alpha_cut(cmds, ctx):
    """透明度のアルゴリズムがアルファ カットならログで知らせる。知らせたら True。"""
    if alpha_cut_enabled(cmds):
        ctx.log(ALPHA_CUT_HINT)
        return True
    return False


def create_effect(cmds, mu, ctx, v, kind):
    """天候を 1 つ作る (風・カメラ・選択したオブジェクトはこの時に決める)。"""
    camera = choose_camera(cmds, ctx) if kind.need_camera else None
    wind = choose_wind(cmds, mu) if kind.need_wind else None
    target = selected_target(cmds, mu)
    root = cmds.group(empty=True, name=kind.root_name, world=True)
    root = cmds.ls(root, long=True)[0]
    mu.tag(root)
    mu.mark_effect_root(root, kind.effect_type)
    mu.add_param_attrs(root, kind.stored_params(), v, kind.live)
    if kind.effect_type != WIND:
        mu.lock_transform(root)
    if kind.need_wind:
        link(cmds, root, WIND_ATTR, wind)
    if camera:
        link(cmds, root, CAMERA_ATTR, camera)
    if kind.create:
        kind.create(cmds, mu, root, v, camera=camera, target=target)
    kind.build(cmds, mu, root, v)
    log_effect(ctx, cmds, root, kind, camera, wind, target)
    if kind.effect_type in TEXTURED_TYPES:
        warn_if_textures_hidden(cmds, ctx)
    if kind.effect_type in TRANSLUCENT_TYPES:
        warn_if_alpha_cut(cmds, ctx)
    cmds.select(root)
    return root


def log_effect(ctx, cmds, root, kind, camera=None, wind=None, target=None):
    parts = [u"  %s: %s" % (short(root), kind.noun)]
    if camera:
        parts.append(u"カメラ %s" % short(camera))
    if kind.need_wind:
        parts.append(u"風 %s" % short(wind) if wind else u"風なし (「風」を作ると流れます)")
    if target:
        parts.append(u"%s に付いて動く" % short(target))
    ctx.log(u" / ".join(parts))


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % short(root))
        kind = kind_of(cmds, mu, root)
        if kind.need_camera and not find_link(cmds, root, CAMERA_ATTR):
            raise ToolError(u"%s のカメラが見つかりません (削除された可能性があります)" % short(root))
        jobs.append((root, kind))
    for root, kind in jobs:
        params = kind.stored_params()
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        mu.delete_owned(root)
        mu.sync_param_attrs(root, params, values, kind.live, keep=LINK_ATTRS + kind.keep)
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        kind.build(cmds, mu, root, values)
        ctx.log(u"  %s: %s を作り直しました" % (short(root), kind.noun))
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s" % u", ".join(ignored))
    result = [r for r, _ in jobs]
    cmds.select(result)
    return result


def finish(cmds, mu, root, owned):
    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)
