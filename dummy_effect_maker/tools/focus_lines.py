# -*- coding: utf-8 -*-
"""集中線: カメラに張り付き、画面の端から「中心の円」へ向かう線を並べる。

構成:
    dfx_focusLinesN                  ルート (移動ロック。パラメータをアトリビュートで持つ)
      dfx_focusLinesN_follow         カメラにペアレントコンストレイントで張り付く
        dfx_focusLinesN_frame        カメラ前方「カメラからの距離」の位置。画面の大きさに合わせて拡大
          dfx_focusLinesN_focus      中心の円 (動かす・拡大縮小する。キーも打てる)
          dfx_focusLinesN_lineSrc    線の元メッシュ (画面の端 → 先端。非表示。インスタンス元)
          dfx_focusLinesN_tailSrc    画面の外への延長の元メッシュ (長方形)
          dfx_focusLinesN_tipSrc     丸い先端の元メッシュ (円)
          dfx_focusLinesN_lines
            dfx_focusLinesN_line_001 線の本体 (画面の端から先端まで。先端は円の中心向き)
            ...
          dfx_focusLinesN_tails
            dfx_focusLinesN_tail_001 本体の根元から画面の外へ一定の長さだけ延ばす部分
          dfx_focusLinesN_tips
            dfx_focusLinesN_tip_001  丸い先端 (先端の形が「丸い」の時だけ表示)

延長を本体と分けている理由: 本体は長さに合わせて X 方向に拡大縮小するので、
根元を本体に含めると、短い線 (伸び始めなど) の根元が画面の端のすぐ外までしか届かず、
斜めの線では平らな根元が画面の中に見えてしまう。
丸い先端を分けている理由も同じで、本体は縦横で倍率が違うので、本体に丸みを付けると潰れる。

カメラの子にしない理由: Maya Software ではレンダーカメラの子のジオメトリが
レンダリングされない (Maya 2027 で確認)。コンストレイントなら描画される。

frame の中は「画面の横幅の半分 = 1」の座標。frame の大きさはカメラの
焦点距離とフィルムアパーチャにつないであるので、ズームしても画面を覆い続ける
(フィルムゲートの合わせ方は「水平」前提)。

線は円の中心から見た角度 (線の dfx_angle) の方向に並び、画面の端から円の中心へ向かう。
長さは「画面の端〜円の縁」の距離 × 線の長さ × 線ごとの倍率 (dfx_len) なので、
線の長さ 1 以下なら円の中には入らない。位置・向き・長さは線ごとのエクスプレッションが
円の位置と大きさ (scaleX / scaleY。楕円にもできる) から毎フレーム計算するので、
円を動かす・キーを打つと線の向かう先がそのまま変わる。

作成後の調整はチャンネルボックスで行う (追尾弾と同じ仕組み)。
LIVE_PARAMS (色・不透明度・長さ・太さ・距離) はすぐ反映、
REBUILD_PARAMS (本数・ばらつき・シード・ちらつき) は「集中線を更新」で反映。
円は更新しても (位置・大きさ・キーごと) そのまま残る。
"""
from __future__ import absolute_import

import bisect
import math
import random

from ..core import BaseTool, Param, ToolError

EFFECT_TYPE = "focus_lines"
CAMERA_ATTR = "dfx_camera"
FOCUS_ATTR = "dfx_focus"
TAB = u"カメラ"
CATEGORY = u"集中線"

# 線ごとのアトリビュート (エクスプレッションが読む。伸びて移動・ちらつきのキーはここに打つ)
LINE_ANGLE_ATTR = "dfx_angle"       # 円の中心から見た向き (度)
LINE_LENGTH_ATTR = "dfx_len"        # 長さの倍率 (0〜1)
LINE_THICKNESS_ATTR = "dfx_thick"   # 太さの倍率

# 作成時の円の大きさにだけ使う (作成後は円を直接拡大縮小する)
CREATE_PARAMS = ("focus_radius",)
FOCUS_DEFAULT_RADIUS = 0.3
LIVE_PARAMS = ("length", "thickness", "tip_shape", "tip_width", "overscan",
               "color", "opacity", "distance")
REBUILD_PARAMS = ("count", "length_random", "thickness_random", "seed",
                  "motion", "grow_frames", "flicker_interval")

TIP_SHARP = u"とがる"
TIP_ROUND = u"丸い"

MOTION_STATIC = u"静止"
MOTION_FLICKER = u"ちらつき"
MOTION_GROW = u"伸びて移動"
# 伸びて移動: 次に出る場所は、直前の場所からこの角度以上離す
MIN_MOVE_ANGLE = math.radians(30.0)
# 伸びて移動: 移動先の候補数 (多いほど均等、少ないほどランダム)
GROW_CANDIDATES = 16

# フィルムアパーチャ (inch) の半分 → mm
HALF_INCH_MM = 12.7
MAX_LINES = 1000


# ---------------------------------------------------------------- 純粋な計算
def edge_distance(angle, aspect, center=(0.0, 0.0)):
    """center から angle 方向に進んで画面の端に当たるまでの距離 (横幅の半分 = 1)。

    center が画面の外のときは、その方向の「奥側の端」までの距離 (画面を横切る
    方向の線は画面の外の円へ向かう。画面に入らない方向は 0 か画面外の位置になる)。
    """
    half_w, half_h = 1.0, 1.0 / aspect
    cx, cy = center
    c, s = math.cos(angle), math.sin(angle)
    hits = []
    if c > 1e-9:
        hits.append((half_w - cx) / c)
    elif c < -1e-9:
        hits.append((-half_w - cx) / c)
    if s > 1e-9:
        hits.append((half_h - cy) / s)
    elif s < -1e-9:
        hits.append((-half_h - cy) / s)
    return max(0.0, min(hits))


def ellipse_radius(angle, rx, ry):
    """半径 (rx, ry) の楕円の、中心から angle 方向の縁までの距離。"""
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-9 or ry < 1e-9:
        return 0.0
    c, s = math.cos(angle), math.sin(angle)
    return rx * ry / math.sqrt((ry * c) ** 2 + (rx * s) ** 2)


def line_geometry(angle, aspect, center, radius, length):
    """線の (画面の端の位置, 先端の位置)。length = 線の長さ × 線ごとの倍率。

    先端は「画面の端〜円の縁」を length の割合だけ進んだ所 (エクスプレッションと同じ式。検証用)。
    """
    edge = edge_distance(angle, aspect, center)
    room = max(0.0, edge - ellipse_radius(angle, radius[0], radius[1]))
    c, s = math.cos(angle), math.sin(angle)
    tip = edge - length * room
    return ((center[0] + c * edge, center[1] + s * edge),
            (center[0] + c * tip, center[1] + s * tip))


def line_layout(count, aspect, seed, length_random, thickness_random):
    """各線の (角度, 画面の端までの距離, 長さの倍率, 太さの倍率)。

    角度は全周を count 等分した区間の中でランダム (偏りなく、でも規則的すぎない)。
    線の先端は 端までの距離 × (1 - 線の長さ × 長さの倍率) の位置に来る。
    """
    rng = random.Random(seed)
    lines = []
    for i in range(count):
        angle = 2.0 * math.pi * (i + rng.random()) / count
        length = 1.0 - length_random * rng.random()
        thickness = 1.0 - thickness_random * rng.random()
        lines.append((angle, edge_distance(angle, aspect), length, thickness))
    return lines


def angle_gap(a, b):
    """2 つの角度の差 (0〜π)。"""
    d = abs(a - b) % (2.0 * math.pi)
    return min(d, 2.0 * math.pi - d)


def _nearest_gap(sorted_angles, angle):
    """sorted_angles (0〜2π の昇順) の中で angle に一番近いものとの角度差。"""
    if not sorted_angles:
        return math.pi
    i = bisect.bisect_left(sorted_angles, angle)
    return min(angle_gap(angle, sorted_angles[i % len(sorted_angles)]),
               angle_gap(angle, sorted_angles[i - 1]))


def grow_schedule(seed, count, start, end, grow_frames, aspect, length_random,
                  min_move=MIN_MOVE_ANGLE, candidates=GROW_CANDIDATES):
    """伸びて移動: 線ごとの [(開始フレーム, 角度, 端までの距離, 長さの倍率), ...] のリスト。

    均等に散らすため、全部の線をまとめて決める:
      - 最初は全周を count 等分した区間に 1 本ずつ
      - 伸び始めのずれ (位相) は 0〜grow_frames-1 を均等に割り振る
        (毎フレーム同じくらいの本数が移動し、一斉には移動しない)
      - 移動するときは、ランダムな候補を candidates 個作り、直前の場所から
        min_move 未満のものを除いて、今出ているほかの線から一番離れた候補を選ぶ
        (空いている所から優先的に出る。ランダムさは残る)
    最初の 1 回は start 以前に始まることがある (start の時点で伸びている途中から)。
    """
    rng = random.Random("%s-grow" % seed)
    s0 = int(math.floor(start))
    phases = [i % grow_frames for i in range(count)]
    rng.shuffle(phases)

    def length():
        return 1.0 - length_random * rng.random()

    current = [2.0 * math.pi * (i + rng.random()) / count for i in range(count)]
    schedules = [[(s0 - phases[i], a, edge_distance(a, aspect), length())]
                 for i, a in enumerate(current)]
    next_start = [s0 - phases[i] + grow_frames for i in range(count)]
    occupied = sorted(current)

    t = min(next_start) if count else end + 1
    while t <= end:
        movers = [i for i in range(count) if next_start[i] == t]
        rng.shuffle(movers)
        # 消えていく線 (今回移動するもの) は場所の取り合いから外す
        for i in movers:
            occupied.remove(current[i])
        for i in movers:
            prev = current[i]
            best, best_gap = None, -1.0
            for _ in range(candidates):
                a = 2.0 * math.pi * rng.random()
                if angle_gap(a, prev) < min_move:
                    continue
                gap = _nearest_gap(occupied, a)
                if gap > best_gap:
                    best, best_gap = a, gap
            if best is None:  # 候補が全部近すぎた (ほぼ起きない): 反対側へ
                best = (prev + math.pi) % (2.0 * math.pi)
            current[i] = best
            bisect.insort(occupied, best)
            schedules[i].append((t, best, edge_distance(best, aspect), length()))
            next_start[i] = t + grow_frames
        t += 1
    return schedules


def flicker_lengths(seed, index, key_count, length_random):
    """ちらつき用に、線 index の長さの倍率を key_count 個返す (シードで再現可能)。"""
    rng = random.Random("%s-%d" % (seed, index))
    return [1.0 - length_random * rng.random() for _ in range(key_count)]


# ---------------------------------------------------------------- ツール
class FocusLinesTool(BaseTool):
    label = u"集中線"
    tab = TAB
    category = CATEGORY
    order = 600
    tooltip = (u"カメラに張り付く集中線を作成します。\n"
               u"カメラを選択して押してください (未選択ならアクティブなビューのカメラ)。\n"
               u"線は中心の円 (dfx_focusLinesN_focus) に向かい、円の中には入りません。\n"
               u"円を動かす・拡大縮小すると、線の向かう先と空ける大きさが変わります (キーも打てます)。\n"
               u"作成後はルート (dfx_focusLinesN) のチャンネルボックスで調整できます。")
    params = [
        Param("focus_radius", u"中心の円の大きさ", FOCUS_DEFAULT_RADIUS, minimum=0.0,
              maximum=2.0, step=0.05,
              tooltip=u"作成時の円の半径 (画面の横幅の半分 = 1)。線はこの円の中には入らない。\n"
                      u"作成後は円 (dfx_focusLinesN_focus) を直接拡大縮小する"),
        Param("length", u"線の長さ", 1.0, minimum=0.0, maximum=1.0, step=0.05,
              tooltip=u"画面の端から円へ向かって伸びる長さ。1 で円の縁に届く\n"
                      u"(方向ごとの「画面の端〜円の縁」の距離に対する割合)"),
        Param("thickness", u"線の太さ", 0.03, minimum=0.0, maximum=1.0, step=0.005,
              tooltip=u"画面の端での線の太さ。画面の横幅の半分を 1 とする。先端に向かって細くなる"),
        Param("tip_shape", u"先端の形", TIP_SHARP, kind="choice",
              choices=[TIP_SHARP, TIP_ROUND],
              tooltip=u"とがる: 先端が点になる\n丸い: 先端を丸める (太さは「先端の太さ」)"),
        Param("tip_width", u"先端の太さ (丸い時)", 0.5, minimum=0.01, maximum=1.0, step=0.05,
              tooltip=u"丸い時の先端の太さ。画面の端での太さに対する割合 (1 で同じ太さのまま)"),
        Param("overscan", u"画面の外への延長", 0.5, minimum=0.0, maximum=5.0, step=0.1,
              tooltip=u"線の根元を画面の端から外へ延ばす長さ (画面の横幅の半分 = 1)。\n"
                      u"線の根元が画面の中に見える時 (ゲートの外も写る時など) は大きくする"),
        Param("color", u"色", (1.0, 1.0, 1.0), kind="color"),
        Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1),
        Param("distance", u"カメラからの距離", 1.0, minimum=0.001, step=0.5,
              tooltip=u"線を置く位置。物体より手前になるよう小さめに\n"
                      u"(ただしカメラのニアクリップより大きく)"),
        Param("count", u"本数", 80, minimum=3, maximum=MAX_LINES),
        Param("length_random", u"長さのばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"0 で全部同じ長さ。1 で 0 〜「線の長さ」までばらつく"),
        Param("thickness_random", u"太さのばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1),
        Param("seed", u"乱数シード", 1, minimum=0, maximum=99999,
              tooltip=u"変えると線の並びが変わる"),
        Param("motion", u"動き", MOTION_GROW, kind="choice",
              choices=[MOTION_STATIC, MOTION_FLICKER, MOTION_GROW],
              tooltip=u"静止: 動かない\n"
                      u"ちらつき: 「ちらつき間隔」ごとに各線の長さが変わる\n"
                      u"伸びて移動: 画面の端から伸び、伸びきったら別の場所 (直前から 30° 以上離れた所) に移ってまた伸びる\n"
                      u"(動きは作成・更新した時点のタイムスライダーの範囲にキーを打つ)"),
        Param("grow_frames", u"伸びるフレーム数", 6, minimum=2, maximum=200,
              tooltip=u"伸びて移動の時: 1 本が伸びきるまでのフレーム数"),
        Param("flicker_interval", u"ちらつき間隔 (フレーム)", 2, minimum=1, maximum=100,
              tooltip=u"ちらつきの時: 何フレームごとに長さが変わるか"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        camera = resolve_camera(cmds, u"集中線")
        root = cmds.group(empty=True, name="dfx_focusLines#", world=True)
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, EFFECT_TYPE)
        mu.add_param_attrs(root, stored_params(), v, LIVE_PARAMS)
        attach_camera(cmds, root, camera)
        mu.lock_transform(root)

        build_effect(cmds, mu, root, v, camera, focus_radius=v["focus_radius"])
        cmds.select(root)
        _log(ctx, root, v, camera)
        return root


class FocusLinesUpdateTool(BaseTool):
    label = u"集中線を更新"
    tab = TAB
    category = CATEGORY
    order = 610
    tooltip = (u"選択した集中線を、ルートのアトリビュートの値で作り直します。\n"
               u"チャンネルボックスで「(更新)」の付いた項目を変えたあとに使います。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return rebuild_effects(cmds, mu, ctx, _selected_roots(mu, u"更新する"))


class FocusLinesSavePresetTool(BaseTool):
    label = u"集中線のパラメータを保存"
    tab = TAB
    category = CATEGORY
    order = 612
    undoable = False  # シーンは変えない
    tooltip = (u"選択した集中線のパラメータ (ルートのアトリビュート) を\n"
               u"プリセットファイル (.json) に保存します。中心の円の位置・大きさは含みません。")

    def run(self, ctx, **v):
        from . import _maya_utils as mu

        roots = _selected_roots(mu, u"保存する")
        return mu.save_preset_dialog(ctx, roots, EFFECT_TYPE, stored_params(),
                                     self.label, u"集中線")


class FocusLinesLoadPresetTool(BaseTool):
    label = u"集中線のパラメータを読み込み"
    tab = TAB
    category = CATEGORY
    order = 614
    tooltip = (u"プリセットファイル (.json) のパラメータを選択した集中線に設定し、\n"
               u"作り直します (複数選択可。中心の円はそのまま)。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = _selected_roots(mu, u"読み込み先の")
        values = mu.load_preset_dialog(ctx, EFFECT_TYPE, stored_params(), self.label)
        if values is None:
            return None
        return rebuild_effects(cmds, mu, ctx, roots, overrides=values)


def _selected_roots(mu, purpose):
    roots = mu.selected_effect_roots(EFFECT_TYPE)
    if not roots:
        raise ToolError(u"%s集中線 (dfx_focusLinesN か、その中の線) を選択してください" % purpose)
    return roots


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    """roots をアトリビュートの値で作り直す。overrides があれば先にその値を設定する
    (キーや接続で決まっているアトリビュートには書き込まない)。円は位置・大きさ・キーごと残す。
    """
    params = stored_params()
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % _short(root))
        jobs.append((root, find_camera(cmds, root)))
    for root, camera in jobs:
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        # 円は一旦ルートの下に退避して、作り直しで戻す
        focus = stored_focus(cmds, root)
        if focus:
            focus = cmds.ls(cmds.parent(focus, root, relative=True)[0], long=True)[0]
        mu.delete_owned(root)
        mu.sync_param_attrs(root, params, values, LIVE_PARAMS, keep=(CAMERA_ATTR, FOCUS_ATTR))
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        build_effect(cmds, mu, root, values, camera, focus=focus)
        _log(ctx, root, values, camera)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s" % u", ".join(ignored))
    result = [job[0] for job in jobs]
    cmds.select(result)
    return result


# ---------------------------------------------------------------- 構築
def stored_params():
    by_name = dict((p.name, p) for p in FocusLinesTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def _short(node):
    return node.split("|")[-1]


def _log(ctx, root, v, camera):
    ctx.log(u"  %s: %d 本 (カメラ: %s)" % (_short(root), v["count"], _short(camera)))


def resolve_camera(cmds, what):
    """選択中のカメラ → アクティブなビューのカメラ の順に探す (transform を返す)。"""
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if cmds.objectType(node, isAType="camera"):
            return cmds.listRelatives(node, parent=True, fullPath=True)[0]
        if cmds.listRelatives(node, shapes=True, type="camera"):
            return node
    panel = cmds.getPanel(withFocus=True) if not cmds.about(batch=True) else None
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        cam = cmds.modelPanel(panel, query=True, camera=True)
        if cam:
            if cmds.objectType(cam, isAType="camera"):
                cam = cmds.listRelatives(cam, parent=True, fullPath=True)[0]
            return cmds.ls(cam, long=True)[0]
    raise ToolError(u"%sを付けるカメラを選択してください" % what)


def stored_focus(cmds, root):
    """root の中心の円 (無ければ None。この機能より前に作った集中線など)。"""
    if not cmds.attributeQuery(FOCUS_ATTR, node=root, exists=True):
        return None
    conns = cmds.listConnections(root + "." + FOCUS_ATTR, source=True,
                                 destination=False) or []
    return cmds.ls(conns[0], long=True)[0] if conns else None


def _create_focus(cmds, mu, root, prefix, radius):
    """中心の円を作る (画面に平行。X/Y の移動と拡大縮小だけできる)。"""
    focus = cmds.circle(normal=(0, 0, 1), radius=1.0, sections=32, degree=3,
                        constructionHistory=False, name=prefix + "_focus")[0]
    focus = cmds.ls(cmds.parent(focus, root, relative=True)[0], long=True)[0]
    cmds.setAttr(focus + ".scale", radius, radius, 1.0, type="double3")
    for plug in ("tz", "rx", "ry", "rz", "sz"):
        cmds.setAttr(focus + "." + plug, lock=True, keyable=False, channelBox=False)
    shape = cmds.listRelatives(focus, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)  # 黄
    mu.tag(focus)
    if not cmds.attributeQuery(FOCUS_ATTR, node=root, exists=True):
        cmds.addAttr(root, longName=FOCUS_ATTR, attributeType="message")
    cmds.connectAttr(focus + ".message", root + "." + FOCUS_ATTR, force=True)
    return focus


def build_camera_frame(cmds, root, prefix, camera, distance_plug, owned):
    """root の下に、カメラに張り付く _follow と、その中の画面座標の _frame を作る。

    _frame の中は「画面の横幅の半分 = 1」(スピードラインと共通)。
    """
    cam_shape = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True)[0]
    # --- カメラに張り付ける (ペアレントコンストレイント)
    follow = cmds.group(empty=True, name=prefix + "_follow", parent=root)
    follow = cmds.ls(follow, long=True)[0]
    constraint = cmds.parentConstraint(camera, follow, maintainOffset=False,
                                       name=prefix + "_followCamera")[0]
    owned.extend([follow, cmds.ls(constraint, long=True)[0]])

    # --- カメラ前方に置き、画面の大きさに合わせる
    #     z = -距離、拡大率 = 距離 × (水平アパーチャ[inch] × 12.7) / 焦点距離[mm]
    frame = cmds.group(empty=True, name=prefix + "_frame", parent=follow)
    frame = cmds.ls(frame, long=True)[0]
    owned.append(frame)
    neg = cmds.createNode("multiplyDivide", name=prefix + "_negDistance")
    cmds.setAttr(neg + ".input2X", -1.0)
    cmds.connectAttr(distance_plug, neg + ".input1X")
    cmds.connectAttr(neg + ".outputX", frame + ".translateZ")
    aperture = cmds.createNode("multiplyDivide", name=prefix + "_apertureDistance")
    cmds.connectAttr(cam_shape + ".horizontalFilmAperture", aperture + ".input1X")
    cmds.connectAttr(distance_plug, aperture + ".input2X")
    size = cmds.createNode("multiplyDivide", name=prefix + "_frameSize")
    cmds.setAttr(size + ".operation", 2)  # divide
    cmds.connectAttr(aperture + ".outputX", size + ".input1X")
    cmds.connectAttr(cam_shape + ".focalLength", size + ".input2X")
    half = cmds.createNode("multiplyDivide", name=prefix + "_halfWidth")
    cmds.connectAttr(size + ".outputX", half + ".input1X")
    cmds.setAttr(half + ".input2X", HALF_INCH_MM)
    for axis in "XYZ":
        cmds.connectAttr(half + ".outputX", frame + ".scale" + axis)
    owned.extend([neg, aperture, size, half])
    return frame


def build_material(cmds, mu, prefix, attr, v, owned):
    """色・不透明度がルートのアトリビュートにつながったマテリアル。シェーディンググループを返す。"""
    shader, sg = mu.create_surface_shader(prefix + "_mat", v["color"], 1.0 - v["opacity"])
    cmds.connectAttr(attr("color"), shader + ".outColor", force=True)
    inv = cmds.createNode("reverse", name=prefix + "_transparency")
    for axis in "XYZ":
        cmds.connectAttr(attr("opacity"), inv + ".input" + axis)
    cmds.connectAttr(inv + ".output", shader + ".outTransparency", force=True)
    owned.extend([shader, sg, inv])
    return sg


def make_source(cmds, parent, name, points, owned):
    """parent の下に、インスタンスの元になる 1 枚ポリゴンを作る (影なし)。"""
    node = cmds.polyCreateFacet(point=points, constructionHistory=False, name=name)[0]
    # relative: カメラの下でもワールド位置を保とうとして回転が入らないように
    node = cmds.ls(cmds.parent(node, parent, relative=True)[0], long=True)[0]
    shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
    for a in ("castsShadows", "receiveShadows"):
        cmds.setAttr(shape + "." + a, 0)
    owned.append(node)
    return node


def find_camera(cmds, root):
    conns = cmds.listConnections(root + "." + CAMERA_ATTR, source=True,
                                 destination=False) or []
    if not conns:
        raise ToolError(u"%s のカメラが見つかりません" % _short(root))
    return cmds.ls(conns[0], long=True)[0]


def attach_camera(cmds, root, camera):
    cmds.addAttr(root, longName=CAMERA_ATTR, attributeType="message")
    cmds.connectAttr(camera + ".message", root + "." + CAMERA_ATTR)


def build_effect(cmds, mu, root, v, camera, focus=None, focus_radius=FOCUS_DEFAULT_RADIUS):
    """root の下に集中線を作る (作成時と更新時で共通)。

    focus (既存の円) が無ければ半径 focus_radius の円を作る。
    """
    def attr(name):
        return "%s.%s%s" % (root, mu.PARAM_PREFIX, name)

    prefix = _short(root)
    owned = []
    frame = build_camera_frame(cmds, root, prefix, camera, attr("distance"), owned)

    # --- 中心の円 (所有ノードにしない: 更新しても残す。全削除では消える)
    if focus is None:
        focus = _create_focus(cmds, mu, root, prefix, focus_radius)
    focus = cmds.ls(cmds.parent(focus, frame, relative=True)[0], long=True)[0]

    # --- 元メッシュ (どれも x=0 が画面の端、太さ (Y) 1 が画面の端での太さ)
    #     本体: x=0 (太さ 1) → x=-1 (先端。太さは先端の太さ。とがる時は 0 = 三角形)
    #     延長: x=0 → x=1 の長方形 (画面の外側)
    #     丸い先端: 半径 1 の円
    circle_pts = [(math.cos(2 * math.pi * i / 32.0), math.sin(2 * math.pi * i / 32.0), 0.0)
                  for i in range(32)]
    src, tail_src, tip_src = [
        make_source(cmds, frame, prefix + name, pts, owned)
        for name, pts in (("_lineSrc", [(0, 1, 0), (-1, 0, 0), (-1, 0, 0), (0, -1, 0)]),
                          ("_tailSrc", [(0, 1, 0), (0, -1, 0), (1, -1, 0), (1, 1, 0)]),
                          ("_tipSrc", circle_pts))]
    # 本体の先端の太さ (丸い時だけ)。元メッシュの頂点を動かすので全部の線に効く
    src_shape = _short(cmds.listRelatives(src, shapes=True, fullPath=True)[0])
    owned.append(cmds.expression(
        name=prefix + "_tipWidth", alwaysEvaluate=True, unitConversion="all", string=u"""
float $k = 0;
if ({r}.dfx_tip_shape == 1) $k = clamp(0.01, 1, {r}.dfx_tip_width);
{s}.pnts[1].pnty = $k;
{s}.pnts[2].pnty = -$k;
""".format(r=prefix, s=src_shape)))

    # --- マテリアル (色・不透明度はライブ)
    sg = build_material(cmds, mu, prefix, attr, v, owned)
    cmds.sets([src, tail_src, tip_src], edit=True, forceElement=sg)

    # --- 線を並べる (配置の角度だけ使う。端までの距離はエクスプレッションが毎フレーム計算)
    aspect = cmds.getAttr("defaultResolution.deviceAspectRatio") or 1.0
    layout = line_layout(v["count"], aspect, v["seed"], v["length_random"],
                         v["thickness_random"])
    motion = v["motion"]
    start = cmds.playbackOptions(query=True, minTime=True)
    end = cmds.playbackOptions(query=True, maxTime=True)
    flicker_times = (_frames(start, end, v["flicker_interval"])
                     if motion == MOTION_FLICKER else [])
    schedules = (grow_schedule(v["seed"], v["count"], start, end, v["grow_frames"],
                               aspect, v["length_random"])
                 if motion == MOTION_GROW else None)
    groups = []
    for name in ("_lines", "_tails", "_tips"):
        grp = cmds.group(empty=True, name=prefix + name, parent=frame)
        groups.append(grp)
        owned.append(grp)
    lines, parts = [], []
    for i, (angle, _, length, thickness) in enumerate(layout):
        line, tail, tip = [
            cmds.ls(cmds.parent(cmds.instance(s, name="%s_%s_%03d" % (prefix, n, i + 1))[0],
                                grp, relative=True)[0], long=True)[0]
            for s, n, grp in zip((src, tail_src, tip_src), ("line", "tail", "tip"), groups)]
        parts.extend([line, tail, tip])
        for name, value in ((LINE_ANGLE_ATTR, math.degrees(angle)),
                            (LINE_LENGTH_ATTR, length), (LINE_THICKNESS_ATTR, thickness)):
            cmds.addAttr(line, longName=name, attributeType="double", defaultValue=value)
        if flicker_times:
            factors = flicker_lengths(v["seed"], i, len(flicker_times), v["length_random"])
            for t, f in zip(flicker_times, factors):
                cmds.setKeyframe(line, attribute=LINE_LENGTH_ATTR, time=t, value=f,
                                 inTangentType="linear", outTangentType="step")
        elif schedules:
            _key_grow(cmds, line, schedules[i], v["grow_frames"])
        expr = cmds.expression(name="%s_lineShape_%03d" % (prefix, i + 1),
                               string=_line_expression(root, focus, line, tail, tip),
                               alwaysEvaluate=True, unitConversion="all")
        owned.append(expr)
        lines.append(line)
    cmds.sets(parts, edit=True, forceElement=sg)
    for s in (src, tail_src, tip_src):
        cmds.setAttr(s + ".visibility", 0)

    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)
    return lines


def _frames(start, end, interval):
    times = []
    t = start
    while t <= end + 1e-6:
        times.append(t)
        t += interval
    return times


def tip_layout(length, thickness, tip_round, tip_width):
    """(本体の長さ, 丸い先端の半径)。length は画面の端から先端までの長さ。

    丸い時は先端の円が length の位置でちょうど終わるように本体を短くする
    (円の半径 = 先端の太さ。length が短ければ length まで)。
    """
    if not tip_round:
        return length, 0.0
    radius = min(max(0.01, min(1.0, tip_width)) * thickness, length)
    return length - radius, radius


def _line_expression(root, focus, line, tail, tip):
    """線 1 本のエクスプレッション。円の位置・大きさから位置・向き・長さを決める。

    元メッシュは x=0 が画面の端なので、本体・延長は translate = 画面の端、
    rotateZ = 円の中心から見た向き、scaleX = 長さ にする。丸い先端は本体の先に置く。
    """
    r, f, l = _short(root), _short(focus), _short(line)
    tl, tp = _short(tail), _short(tip)
    return u"""
float $asp = max(defaultResolution.deviceAspectRatio, 0.001);
float $hh = 1.0 / $asp;
float $px = {f}.translateX;
float $py = {f}.translateY;
float $ra = abs({f}.scaleX);
float $rb = abs({f}.scaleY);
float $ang = {l}.{a};
float $c = cosd($ang);
float $s = sind($ang);
float $d = 1.0e9;
if ($c > 1e-9) $d = min($d, (1.0 - $px) / $c);
else if ($c < -1e-9) $d = min($d, (-1.0 - $px) / $c);
if ($s > 1e-9) $d = min($d, ($hh - $py) / $s);
else if ($s < -1e-9) $d = min($d, (-$hh - $py) / $s);
$d = max($d, 0);
float $rad = 0;
if ($ra > 1e-9 && $rb > 1e-9) $rad = $ra * $rb / sqrt(pow($rb * $c, 2) + pow($ra * $s, 2));
float $room = max($d - $rad, 0);
float $len = {r}.dfx_length * {l}.{n} * $room;
float $th = {r}.dfx_thickness * {l}.{t};
float $cap = 0;
if ({r}.dfx_tip_shape == 1) $cap = min(clamp(0.01, 1, {r}.dfx_tip_width) * $th, $len);
float $body = $len - $cap;
float $ex = $px + $c * $d;
float $ey = $py + $s * $d;
{l}.translateX = $ex;
{l}.translateY = $ey;
{l}.rotateZ = $ang;
{l}.scaleX = $body;
{l}.scaleY = $th;
{tl}.translateX = $ex;
{tl}.translateY = $ey;
{tl}.rotateZ = $ang;
{tl}.scaleX = {r}.dfx_overscan;
{tl}.scaleY = $th;
{tl}.visibility = $len > 1e-6;
{tp}.translateX = $ex - $c * $body;
{tp}.translateY = $ey - $s * $body;
{tp}.scaleX = $cap;
{tp}.scaleY = $cap;
{tp}.visibility = $cap > 1e-6;
""".format(r=r, f=f, l=l, tl=tl, tp=tp, a=LINE_ANGLE_ATTR, n=LINE_LENGTH_ATTR,
           t=LINE_THICKNESS_ATTR)


def _key_grow(cmds, line, cycles, grow_frames):
    """伸びて移動のキーを線のアトリビュート (向き・長さの倍率) に打つ。

    1 回の伸び (開始フレーム t) の中の k フレーム目 (0〜grow_frames-1) で
    長さの倍率 = 倍率 × (k + 1) / grow_frames。最後のフレームで伸びきり、
    次のフレームで別の向きに移って短い状態から伸び直す (向きはステップキー)。
    """
    for t, angle, _, length in cycles:
        last = t + grow_frames - 1
        cmds.setKeyframe(line, attribute=LINE_ANGLE_ATTR, time=t, value=math.degrees(angle),
                         inTangentType="linear", outTangentType="step")
        cmds.setKeyframe(line, attribute=LINE_LENGTH_ATTR, time=t, value=length / grow_frames,
                         inTangentType="linear", outTangentType="linear")
        cmds.setKeyframe(line, attribute=LINE_LENGTH_ATTR, time=last, value=length,
                         inTangentType="linear", outTangentType="step")
