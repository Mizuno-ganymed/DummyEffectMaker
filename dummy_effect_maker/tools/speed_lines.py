# -*- coding: utf-8 -*-
"""スピードライン: カメラの前を、線が速いスピードで横切っていく。

構成:
    dfx_speedLinesN                  ルート (移動ロック。パラメータをアトリビュートで持つ)
      dfx_speedLinesN_follow         カメラにペアレントコンストレイントで張り付く (集中線と同じ)
        dfx_speedLinesN_frame        カメラ前方。中は「画面の横幅の半分 = 1」の座標
          dfx_speedLinesN_band       中央の四角 (動かす・回転・拡大縮小する。キーも打てる)
          dfx_speedLinesN_lineSrc    線の元メッシュ (非表示。インスタンス元)
          dfx_speedLinesN_flow       四角の位置と回転に追従する (線が乗る面)
            dfx_speedLinesN_line_001 線
            ...

線は四角のローカル X 方向 (回転 0 なら左 → 右) に流れ、四角の上辺より上と
下辺より下だけに出る (上辺・下辺を延ばした帯の中には入らない)。
四角を Z 回転させると流れる向きが変わる (90° で下 → 上、45° で斜め)。
四角を X / Y 回転させると、線が乗る面が奥行き方向に傾き、カメラのパースが付く
(Y 回転なら流れる先がすぼまる、X 回転なら上下の片側が奥へ遠ざかる)。

線が画面全体に出るように、画面の 4 隅を通るカメラからの視線と、線が乗る面との交点を
毎フレーム求め (_flow の dfx_umin など)、その範囲を線が流れる・散らばるようにする。

線の動きはキーではなく、線ごとのエクスプレッションで毎フレーム計算する:
    進み具合 = 速さ × 線ごとの速さの倍率 × フレーム / 基準の 1 周 + 線ごとのずれ
    位置 = 範囲の左端 - 線の長さ + (進み具合の小数部分) × (範囲の幅 + 線の長さ)
    画面を通り過ぎるたびに (進み具合の整数部分が変わるたびに)、高さ・上下・長さを乱数で変える
四角を動かしても進み具合は変わらないので、線は飛ばない。

線の太さは「入り (後ろの端)・中・終わり (先頭の端)」の 3 つを通る 2 次曲線で、
元メッシュの頂点をエクスプレッションで動かす (全部の線に効く)。
"""
from __future__ import absolute_import

import math
import random

from ..core import BaseTool, Param, ToolError
from . import focus_lines as fl

EFFECT_TYPE = "speed_lines"
BAND_ATTR = "dfx_band"
TAB = fl.TAB
CATEGORY = u"スピードライン"

# 線ごとのアトリビュート (作成・更新時に決める乱数)
LINE_INDEX_ATTR = "dfx_index"
LINE_PHASE_ATTR = "dfx_phase"       # 流れのずれ (0〜1 周)
LINE_SPEED_ATTR = "dfx_speedf"      # 速さの倍率
LINE_THICK_ATTR = "dfx_thick"       # 太さの倍率
# _flow に付ける、線が流れる範囲 (線が乗る面の上での、画面の範囲)
REGION_ATTRS = ("dfx_umin", "dfx_umax", "dfx_vmin", "dfx_vmax")

# 元メッシュの、長さ方向の頂点数 (多いほど太さの変化がなめらか)
WIDTH_SAMPLES = 11
MAX_LINES = 1000
# 視線が面に当たらない (地平線が見える) 時などに使う、カメラからの最大距離 (カメラ〜画面の何倍か)
MAX_RAY = 30.0

CREATE_PARAMS = ("band_width", "band_height")
LIVE_PARAMS = ("speed", "length", "length_random", "width_start", "width_mid", "width_end", "reach",
               "color", "opacity", "distance")
REBUILD_PARAMS = ("count", "speed_random", "thickness_random", "seed")


# ---------------------------------------------------------------- 純粋な計算
def width_at(x, start, mid, end):
    """長さ方向の位置 x (0 = 入り、1 = 終わり) での太さ (半分)。3 点を通る 2 次曲線、0 未満は 0。"""
    l0 = 2.0 * (x - 0.5) * (x - 1.0)
    l1 = -4.0 * x * (x - 1.0)
    l2 = 2.0 * x * (x - 0.5)
    return max(0.0, start * l0 + mid * l1 + end * l2)


def plane_axes(rx, ry, rz):
    """回転 (度、回転順 xyz) した面の X / Y / Z 軸 (_frame の座標)。"""
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    ex = (cy * cz, cy * sz, -sy)
    ey = (sx * sy * cz - cx * sz, sx * sy * sz + cx * cz, sx * cy)
    ez = (cx * sy * cz + sx * sz, cx * sy * sz - sx * cz, cx * cy)
    return ex, ey, ez


def camera_depth(focal_length, horizontal_aperture):
    """_frame の座標でのカメラの位置 (Z)。画面 (Z=0) の横幅の半分が 1 になる距離。"""
    return focal_length / max(horizontal_aperture * 12.7, 1e-6)


def plane_region(aspect, zc, band, reach=None):
    """線が乗る面の上での、画面の範囲 (umin, umax, vmin, vmax)。エクスプレッションと同じ式。

    band = (移動 X, 移動 Y, 回転 X, 回転 Y, 回転 Z)。カメラ (0, 0, zc) から画面の 4 隅
    (±1, ±1/aspect, 0) への視線と面の交点を、面の座標 (u = 流れる向き, v = 上下) で表した
    範囲。視線が面に当たらない・遠すぎる時は MAX_RAY 倍の所を面に投影する。
    reach (奥の届く距離) を渡すと、範囲を四角から ±reach までに打ち切る (面を大きく傾けた時に、
    線が遠くまで散らばらないように)。
    """
    tx, ty = band[0], band[1]
    ex, ey, ez = plane_axes(band[2], band[3], band[4])
    hh = 1.0 / aspect
    smax = MAX_RAY
    us, vs = [], []
    for qx, qy in ((1, hh), (-1, hh), (-1, -hh), (1, -hh)):
        d = (qx, qy, -zc)
        den = d[0] * ez[0] + d[1] * ez[1] + d[2] * ez[2]
        num = tx * ez[0] + ty * ez[1] - zc * ez[2]
        s = smax
        if abs(den) > 1e-9:
            s = num / den
            if s <= 0 or s > smax:
                s = smax
        h = (d[0] * s - tx, d[1] * s - ty, zc + d[2] * s)
        us.append(h[0] * ex[0] + h[1] * ex[1] + h[2] * ex[2])
        vs.append(h[0] * ey[0] + h[1] * ey[1] + h[2] * ey[2])
    umin, umax, vmin, vmax = min(us), max(us), min(vs), max(vs)
    if reach is not None:
        r = max(reach, 0.0)
        umin, umax = max(umin, -r), min(umax, r)
        vmin, vmax = max(vmin, -r), min(vmax, r)
        umax, vmax = max(umax, umin), max(vmax, vmin)
    return umin, umax, vmin, vmax


def _hash(k, i, seed, a, b, c, d):
    v = math.sin(k * a + i * b + seed * c) * d
    return v - math.floor(v)


def line_state(frame, index, phase, speed_factor, thick, v, region, band_half_height):
    """線 1 本の、面の上での (左端の u, 中心の v, 長さ)。エクスプレッションと同じ式 (検証用)。

    線は範囲の外 (左) から入って範囲の外 (右) へ出る。1 周にかかるフレーム数は範囲の
    大きさによらず「(2 + 線の長さ) / 速さ」(四角が回転 0 で傾いていない時は 速さ = 1 フレームの距離)。
    上下の位置は「四角の上辺〜範囲の上端」(または下側) の間で、1 周ごとに乱数で変わる。
    """
    umin, umax, vmin, vmax = region
    max_len = max(v["length"], 0.001)
    n = v["speed"] * speed_factor * frame / (2.0 + max_len) + phase
    k = math.floor(n)
    q = n - k
    h1 = _hash(k, index, v["seed"], 12.9898, 78.233, 37.719, 43758.5453)
    h2 = _hash(k, index, v["seed"], 39.3468, 11.135, 73.157, 24634.6345)
    h3 = _hash(k, index, v["seed"], 73.156, 52.235, 9.151, 14375.5964)
    hb = abs(band_half_height)
    wmax = max(v["width_start"], v["width_mid"], v["width_end"]) * thick
    if h2 < 0.5:
        y = -(hb + wmax + h1 * max(-vmin - hb - wmax, 0.0))
    else:
        y = hb + wmax + h1 * max(vmax - hb - wmax, 0.0)
    length = max_len * (1.0 - v["length_random"] * h3)
    width = max(umax - umin, 0.0)
    return umin - max_len + q * (width + max_len), y, length


def line_seeds(count, seed, speed_random, thickness_random):
    """線ごとの (ずれ, 速さの倍率, 太さの倍率)。"""
    rng = random.Random("%s-speed" % seed)
    return [(rng.random(), 1.0 - speed_random * rng.random(),
             1.0 - thickness_random * rng.random()) for _ in range(count)]


# ---------------------------------------------------------------- ツール
class SpeedLinesTool(BaseTool):
    label = u"スピードライン"
    tab = TAB
    category = CATEGORY
    order = 620
    tooltip = (u"カメラの前を線が速く横切るスピードラインを作成します。\n"
               u"カメラを選択して押してください (未選択ならアクティブなビューのカメラ)。\n"
               u"中央の四角 (dfx_speedLinesN_band) の上辺より上と下辺より下に線が流れます。\n"
               u"四角を Z 回転させると流れる向きが、X / Y 回転させると奥行きの傾き (パース) が変わります。")
    params = [
        Param("band_width", u"四角の幅", 1.0, minimum=0.0, maximum=4.0, step=0.1,
              tooltip=u"作成時の中央の四角の幅 (画面の横幅 = 2)。作成後は四角を直接拡大縮小する"),
        Param("band_height", u"四角の高さ", 0.4, minimum=0.0, maximum=4.0, step=0.05,
              tooltip=u"作成時の中央の四角の高さ (画面の横幅 = 2)。この高さの帯には線が入らない"),
        Param("speed", u"速さ", 0.3, minimum=0.0, maximum=10.0, step=0.05,
              tooltip=u"1 フレームに進む距離 (画面の横幅の半分 = 1)。0.3 なら約 7 フレームで画面を横切る\n"
                      u"(四角を回転・傾けた時は、画面を横切るのにかかるフレーム数が同じになるように調整される)"),
        Param("length", u"線の長さ", 0.8, minimum=0.01, maximum=10.0, step=0.1,
              tooltip=u"線の長さ (画面の横幅の半分 = 1)"),
        Param("length_random", u"長さのばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"0 で全部同じ長さ。1 で 0 〜「線の長さ」までばらつく (画面を通り過ぎるたびに変わる)"),
        Param("width_start", u"線の入りの太さ", 0.0, minimum=0.0, maximum=1.0, step=0.002,
              decimals=4, tooltip=u"線の後ろの端 (流れてくる側と反対) の太さ"),
        Param("width_mid", u"線の中の太さ", 0.012, minimum=0.0, maximum=1.0, step=0.002,
              decimals=4, tooltip=u"線の真ん中の太さ"),
        Param("width_end", u"線の終わりの太さ", 0.002, minimum=0.0, maximum=1.0, step=0.002,
              decimals=4, tooltip=u"線の先頭の端 (進んでいく側) の太さ"),
        Param("color", u"色", (1.0, 1.0, 1.0), kind="color"),
        Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1),
        Param("distance", u"カメラからの距離", 1.0, minimum=0.001, step=0.5,
              tooltip=u"線を置く位置。物体より手前になるよう小さめに\n"
                      u"(ただしカメラのニアクリップより大きく)"),
        Param("reach", u"奥の届く距離", 4.0, minimum=0.1, step=0.5,
              tooltip=u"線が流れる範囲を、四角からこの距離まで (画面の横幅の半分 = 1) に打ち切る。\n"
                      u"四角を X / Y に大きく傾けた時に、線が遠く (奥) まで散らばらないようにする"),
        Param("count", u"本数", 40, minimum=1, maximum=MAX_LINES),
        Param("speed_random", u"速さのばらつき", 0.3, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"0 で全部同じ速さ。1 で 0 〜「速さ」までばらつく"),
        Param("thickness_random", u"太さのばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1),
        Param("seed", u"乱数シード", 1, minimum=0, maximum=99999,
              tooltip=u"変えると線の並び・タイミングが変わる"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        camera = fl.resolve_camera(cmds, u"スピードライン")
        root = cmds.group(empty=True, name="dfx_speedLines#", world=True)
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, EFFECT_TYPE)
        mu.add_param_attrs(root, stored_params(), v, LIVE_PARAMS)
        fl.attach_camera(cmds, root, camera)
        mu.lock_transform(root)

        build_effect(cmds, mu, root, v, camera,
                     band_size=(v["band_width"] / 2.0, v["band_height"] / 2.0))
        cmds.select(root)
        _log(ctx, root, v, camera)
        return root


class SpeedLinesUpdateTool(BaseTool):
    label = u"スピードラインを更新"
    tab = TAB
    category = CATEGORY
    order = 630
    tooltip = (u"選択したスピードラインを、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (本数・ばらつき・シード) を変えたあとに使います。\n"
               u"中央の四角は位置・回転・大きさ・キーごと残ります。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return rebuild_effects(cmds, mu, ctx, _selected_roots(mu, u"更新する"))


# ---------------------------------------------------------------- 構築
def stored_params():
    by_name = dict((p.name, p) for p in SpeedLinesTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def _short(node):
    return node.split("|")[-1]


def _log(ctx, root, v, camera):
    ctx.log(u"  %s: %d 本 (カメラ: %s)" % (_short(root), v["count"], _short(camera)))


def _selected_roots(mu, purpose):
    roots = mu.selected_effect_roots(EFFECT_TYPE)
    if not roots:
        raise ToolError(u"%sスピードライン (dfx_speedLinesN か、その中の線) を選択してください"
                        % purpose)
    return roots


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    """roots をアトリビュートの値で作り直す。overrides があれば先にその値を設定する
    (キーや接続で決まっているアトリビュートには書き込まない)。四角は位置・回転・大きさ・キーごと残す。
    """
    params = stored_params()
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % _short(root))
        jobs.append((root, fl.find_camera(cmds, root)))
    for root, camera in jobs:
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        band = stored_band(cmds, root)
        if band:
            band = cmds.ls(cmds.parent(band, root, relative=True)[0], long=True)[0]
        mu.delete_owned(root)
        mu.sync_param_attrs(root, params, values, LIVE_PARAMS, keep=(fl.CAMERA_ATTR, BAND_ATTR))
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        build_effect(cmds, mu, root, values, camera, band=band)
        _log(ctx, root, values, camera)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s" % u", ".join(ignored))
    result = [job[0] for job in jobs]
    cmds.select(result)
    return result


def stored_band(cmds, root):
    if not cmds.attributeQuery(BAND_ATTR, node=root, exists=True):
        return None
    conns = cmds.listConnections(root + "." + BAND_ATTR, source=True, destination=False) or []
    return cmds.ls(conns[0], long=True)[0] if conns else None


def _setup_band_channels(cmds, band):
    """四角は Z 移動と Z 拡大縮小だけロック (回転は X / Y / Z とも使える)。"""
    for plug in ("tz", "sz"):
        cmds.setAttr(band + "." + plug, lock=True, keyable=False, channelBox=False)
    for plug in ("rx", "ry"):
        cmds.setAttr(band + "." + plug, lock=False)
        cmds.setAttr(band + "." + plug, keyable=True)
    cmds.setAttr(band + ".rotateOrder", 0)  # xyz (エクスプレッションの計算と同じ)


def _create_band(cmds, mu, root, prefix, size):
    """中央の四角 (X/Y の移動・X/Y/Z の回転・X/Y の拡大縮小ができる)。"""
    band = cmds.curve(degree=1, point=[(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0), (-1, -1, 0)],
                      name=prefix + "_band")
    band = cmds.ls(cmds.parent(band, root, relative=True)[0], long=True)[0]
    cmds.setAttr(band + ".scale", max(size[0], 1e-4), size[1], 1.0, type="double3")
    shape = cmds.listRelatives(band, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)  # 黄
    mu.tag(band)
    if not cmds.attributeQuery(BAND_ATTR, node=root, exists=True):
        cmds.addAttr(root, longName=BAND_ATTR, attributeType="message")
    cmds.connectAttr(band + ".message", root + "." + BAND_ATTR, force=True)
    return band


def source_points():
    """元メッシュの頂点: 上側を入り → 終わり、下側を終わり → 入り (太さはエクスプレッションで付ける)。"""
    xs = [j / float(WIDTH_SAMPLES - 1) for j in range(WIDTH_SAMPLES)]
    return [(x, 0.0, 0.0) for x in xs] + [(x, 0.0, 0.0) for x in reversed(xs)]


def build_effect(cmds, mu, root, v, camera, band=None, band_size=(0.5, 0.2)):
    """root の下にスピードラインを作る (作成時と更新時で共通)。"""
    def attr(name):
        return "%s.%s%s" % (root, mu.PARAM_PREFIX, name)

    prefix = _short(root)
    owned = []
    frame = fl.build_camera_frame(cmds, root, prefix, camera, attr("distance"), owned)
    cam_shape = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True)[0]

    # --- 中央の四角 (所有ノードにしない: 更新しても残す。全削除では消える)
    if band is None:
        band = _create_band(cmds, mu, root, prefix, band_size)
    band = cmds.ls(cmds.parent(band, frame, relative=True)[0], long=True)[0]
    _setup_band_channels(cmds, band)

    # --- 線が乗る面: 四角の位置と回転だけに追従 (大きさには追従しない)
    flow = cmds.group(empty=True, name=prefix + "_flow", parent=frame)
    flow = cmds.ls(flow, long=True)[0]
    cmds.setAttr(flow + ".rotateOrder", 0)
    for a in ("translateX", "translateY", "rotateX", "rotateY", "rotateZ"):
        cmds.connectAttr(band + "." + a, flow + "." + a)
    for name in REGION_ATTRS:
        cmds.addAttr(flow, longName=name, attributeType="double")
    owned.append(flow)
    # 面の上での画面の範囲 (毎フレーム)
    owned.append(cmds.expression(name=prefix + "_region", alwaysEvaluate=True, unitConversion="all",
                                 string=_region_expression(band, flow, cam_shape, prefix)))

    # --- 線の元メッシュ: x=0 (入り) → x=1 (終わり)。太さは頂点をエクスプレッションで動かす
    src = fl.make_source(cmds, frame, prefix + "_lineSrc", source_points(), owned)
    src_shape = _short(cmds.listRelatives(src, shapes=True, fullPath=True)[0])
    owned.append(cmds.expression(name=prefix + "_lineWidth", alwaysEvaluate=True,
                                 unitConversion="all", string=_width_expression(prefix, src_shape)))
    sg = fl.build_material(cmds, mu, prefix, attr, v, owned)
    cmds.sets(src, edit=True, forceElement=sg)

    # --- 線
    lines = []
    for i, (phase, speed_f, thick) in enumerate(
            line_seeds(v["count"], v["seed"], v["speed_random"], v["thickness_random"])):
        line = cmds.instance(src, name="%s_line_%03d" % (prefix, i + 1))[0]
        line = cmds.ls(cmds.parent(line, flow, relative=True)[0], long=True)[0]
        for name, value in ((LINE_INDEX_ATTR, float(i)), (LINE_PHASE_ATTR, phase),
                            (LINE_SPEED_ATTR, speed_f), (LINE_THICK_ATTR, thick)):
            cmds.addAttr(line, longName=name, attributeType="double", defaultValue=value)
        owned.append(cmds.expression(name="%s_lineMotion_%03d" % (prefix, i + 1),
                                     string=_line_expression(prefix, band, flow, line),
                                     alwaysEvaluate=True, unitConversion="all"))
        lines.append(line)
    cmds.sets(lines, edit=True, forceElement=sg)
    cmds.setAttr(src + ".visibility", 0)

    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)
    return lines


def _width_expression(r, shape):
    """元メッシュの頂点に太さ (入り・中・終わりを通る 2 次曲線) を付ける。"""
    n = WIDTH_SAMPLES
    out = [u"float $ws = {r}.dfx_width_start;".format(r=r),
           u"float $wm = {r}.dfx_width_mid;".format(r=r),
           u"float $we = {r}.dfx_width_end;".format(r=r),
           u"float $x; float $w;"]
    for j in range(n):
        x = j / float(n - 1)
        out.append(u"$x = %r;" % x)
        out.append(u"$w = max(0, $ws * 2 * ($x - 0.5) * ($x - 1) - $wm * 4 * $x * ($x - 1)"
                   u" + $we * 2 * $x * ($x - 0.5));")
        out.append(u"%s.pnts[%d].pnty = $w;" % (shape, j))
        out.append(u"%s.pnts[%d].pnty = -$w;" % (shape, 2 * n - 1 - j))
    return u"\n".join(out) + u"\n"


def _region_expression(band, flow, cam_shape, r):
    """面の上での画面の範囲を _flow に書く (plane_region と同じ式)。"""
    b, f, c = _short(band), _short(flow), _short(cam_shape)
    return u"""
float $asp = max(defaultResolution.deviceAspectRatio, 0.001);
float $hh = 1.0 / $asp;
float $zc = {c}.focalLength / max({c}.horizontalFilmAperture * 12.7, 0.000001);
float $tx = {b}.translateX;
float $ty = {b}.translateY;
float $cx = cosd({b}.rotateX);
float $sx = sind({b}.rotateX);
float $cy = cosd({b}.rotateY);
float $sy = sind({b}.rotateY);
float $cz = cosd({b}.rotateZ);
float $sz = sind({b}.rotateZ);
float $ex[] = {{$cy * $cz, $cy * $sz, -$sy}};
float $ey[] = {{$sx * $sy * $cz - $cx * $sz, $sx * $sy * $sz + $cx * $cz, $sx * $cy}};
float $ez[] = {{$cx * $sy * $cz + $sx * $sz, $cx * $sy * $sz - $sx * $cz, $cx * $cy}};
float $umin = 1e9;
float $umax = -1e9;
float $vmin = 1e9;
float $vmax = -1e9;
int $k;
for ($k = 0; $k < 4; $k++) {{
    float $qx = 1;
    if ($k == 1 || $k == 2) $qx = -1;
    float $qy = $hh;
    if ($k >= 2) $qy = -$hh;
    float $den = $qx * $ez[0] + $qy * $ez[1] - $zc * $ez[2];
    float $num = $tx * $ez[0] + $ty * $ez[1] - $zc * $ez[2];
    float $s = {smax};
    if (abs($den) > 1e-9) {{
        $s = $num / $den;
        if ($s <= 0 || $s > {smax}) $s = {smax};
    }}
    float $hx = $qx * $s - $tx;
    float $hy = $qy * $s - $ty;
    float $hz = $zc - $zc * $s;
    float $u = $hx * $ex[0] + $hy * $ex[1] + $hz * $ex[2];
    float $v = $hx * $ey[0] + $hy * $ey[1] + $hz * $ey[2];
    $umin = min($umin, $u);
    $umax = max($umax, $u);
    $vmin = min($vmin, $v);
    $vmax = max($vmax, $v);
}}
float $reach = max({r}.dfx_reach, 0);
$umin = max($umin, -$reach);
$umax = max(min($umax, $reach), $umin);
$vmin = max($vmin, -$reach);
$vmax = max(min($vmax, $reach), $vmin);
{f}.dfx_umin = $umin;
{f}.dfx_umax = $umax;
{f}.dfx_vmin = $vmin;
{f}.dfx_vmax = $vmax;
""".format(b=b, f=f, c=c, r=r, smax=repr(MAX_RAY))


def _line_expression(r, band, flow, line):
    """線 1 本のエクスプレッション (line_state と同じ式)。"""
    b, f, l = _short(band), _short(flow), _short(line)
    return u"""
float $Lmax = max({r}.dfx_length, 0.001);
float $umin = {f}.dfx_umin;
float $W = max({f}.dfx_umax - $umin, 0);
float $n = {r}.dfx_speed * {l}.{sp} * frame / (2 + $Lmax) + {l}.{ph};
float $k = floor($n);
float $q = $n - $k;
float $i = {l}.{ix};
float $sd = {r}.dfx_seed;
float $h1 = sin($k * 12.9898 + $i * 78.233 + $sd * 37.719) * 43758.5453;
$h1 = $h1 - floor($h1);
float $h2 = sin($k * 39.3468 + $i * 11.135 + $sd * 73.157) * 24634.6345;
$h2 = $h2 - floor($h2);
float $h3 = sin($k * 73.156 + $i * 52.235 + $sd * 9.151) * 14375.5964;
$h3 = $h3 - floor($h3);
float $hb = abs({b}.scaleY);
float $wmax = max(max({r}.dfx_width_start, {r}.dfx_width_mid), {r}.dfx_width_end) * {l}.{th};
float $y = $hb + $wmax + $h1 * max({f}.dfx_vmax - $hb - $wmax, 0);
if ($h2 < 0.5) $y = -($hb + $wmax + $h1 * max(-{f}.dfx_vmin - $hb - $wmax, 0));
{l}.translateX = $umin - $Lmax + $q * ($W + $Lmax);
{l}.translateY = $y;
{l}.scaleX = $Lmax * (1 - {r}.dfx_length_random * $h3);
{l}.scaleY = {l}.{th};
""".format(r=r, b=b, f=f, l=l, sp=LINE_SPEED_ATTR, ph=LINE_PHASE_ATTR, ix=LINE_INDEX_ATTR,
           th=LINE_THICK_ATTR)
