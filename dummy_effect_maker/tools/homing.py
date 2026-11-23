# -*- coding: utf-8 -*-
"""追尾弾: ロケータ A → B (経由) → C へ弾を一定間隔で撃ち続ける。

「追尾弾」を押すと、ルート・ロケータ A/B/C・弾がまとめて作られ、
パラメータもルートのアトリビュートに設定済みの状態になる。

構成:
    dfx_homingN                  ルート (移動ロック。パラメータをアトリビュートで持つ)
      dfx_homingN_locA           発射位置 (B: 経由点 / C: 着弾点)。動かすと弾道が追従
      dfx_homingN_locB
      dfx_homingN_locC
      dfx_homingN_path           軌道カーブ (A/B/C にライブ接続。テンプレート表示)
      dfx_homingN_bulletSrc      弾の元メッシュ (非表示。インスタンス元)
      dfx_homingN_bullets
        dfx_homingN_bullet_001   モーションパスで飛ぶ弾
        ...

軌道カーブはロケータに接続されているので、作成後にロケータを動かすと
弾道も追従する。弾は弧長基準で等速 (または減速設定に従って減速) しながら飛び、
発射フレームに出現し、
C に到達した次のフレームで消える。

作成後の調整:
    ルートのアトリビュートをチャンネルボックスで変更する。
    LIVE_PARAMS (色・大きさ・形) はノード接続で即反映される。
    REBUILD_PARAMS (タイミング・軌道など、弾の数や構成が変わるもの) は
    表示名に「(更新)」が付き、「追尾弾を更新」で作り直すと反映される。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param, ToolError

EFFECT_TYPE = "homing"

PATH_SMOOTH = u"なめらか"
PATH_LINEAR = u"折れ線"
SHAPE_SPHERE = u"球体"
SHAPE_SQUASHED = u"つぶれた球体"
# 弾はモーションパスの follow で X 軸が進行方向を向く。
# つぶれた球体は X (進行方向) だけを伸び率倍して、パスに沿って伸びた形にする

MAX_BULLETS = 2000

LOCATOR_ATTRS = ("dfx_locatorA", "dfx_locatorB", "dfx_locatorC")
# 作成時のロケータ配置にだけ使うもの (作成後はロケータを直接動かす)
CREATE_PARAMS = ("length", "height")
# チャンネルボックスで変えるとすぐ反映されるもの
LIVE_PARAMS = ("color", "radius", "shape", "stretch", "subdivisions")
# 「追尾弾を更新」で作り直すと反映されるもの
REBUILD_PARAMS = ("start_frame", "end_frame", "interval", "travel_frames",
                  "speed_mode", "min_speed", "path_type", "use_instance")

SPEED_CONSTANT = u"なし (等速)"
SPEED_LINEAR = u"リニア"
SPEED_QUADRATIC = u"2乗"
# 減速ありのとき、1 フレームあたりに打つ uValue のキー数 (キーの間は直線補間)
SPEED_KEYS_PER_FRAME = 4


# ---------------------------------------------------------------- 純粋な計算
def compute_fire_frames(start, end, interval):
    """start から end まで (end を含む) interval ごとの発射フレーム。"""
    if interval <= 0:
        raise ValueError("interval must be positive")
    frames = []
    i = 0
    while True:
        f = start + i * interval
        if f > end + 1e-6:
            break
        frames.append(round(f, 4))
        i += 1
    return frames


def bezier_control_through(a, b, c):
    """2 次ベジェが t=0.5 で b を通るための中間制御点。

    P(0.5) = 0.25A + 0.5P1 + 0.25C = B  →  P1 = 2B - 0.5(A + C)
    """
    return tuple(2.0 * b[i] - 0.5 * (a[i] + c[i]) for i in range(3))


def speed_at(mode, min_ratio, t):
    """発射からの経過 t (0〜1) での速度。発射時を 1、着弾時が min_ratio。

    リニア: 一定の割合で減速          v = 1 - (1 - m) t
    2乗   : 最初に大きく減速し緩やかに v = m + (1 - m) (1 - t)^2
    """
    m = min_ratio
    if mode == SPEED_LINEAR:
        return 1.0 - (1.0 - m) * t
    if mode == SPEED_QUADRATIC:
        return m + (1.0 - m) * (1.0 - t) ** 2
    return 1.0


def travel_fraction(mode, min_ratio, t):
    """経過 t (0〜1) で進んだ距離の割合 (0〜1)。speed_at の積分を正規化したもの。"""
    t = min(1.0, max(0.0, t))
    m = min_ratio
    if mode == SPEED_LINEAR:
        dist, total = t - (1.0 - m) * t * t / 2.0, (1.0 + m) / 2.0
    elif mode == SPEED_QUADRATIC:
        dist = m * t + (1.0 - m) * (1.0 - (1.0 - t) ** 3) / 3.0
        total = m + (1.0 - m) / 3.0
    else:
        return t
    return dist / total


def travel_keys(mode, min_ratio, start, travel):
    """motionPath.uValue に打つ [(フレーム, 値)]。等速なら始点と終点だけ。"""
    if mode == SPEED_CONSTANT or min_ratio >= 1.0:
        return [(start, 0.0), (start + travel, 1.0)]
    count = max(8, int(math.ceil(travel * SPEED_KEYS_PER_FRAME)))
    return [(start + travel * i / float(count),
             travel_fraction(mode, min_ratio, i / float(count)))
            for i in range(count + 1)]


def check_fire_frames(v):
    """パラメータから発射フレームを求める。不正ならユーザー向けエラー。"""
    if v["end_frame"] < v["start_frame"]:
        raise ToolError(u"発射終了フレームが開始フレームより前になっています")
    frames = compute_fire_frames(v["start_frame"], v["end_frame"], v["interval"])
    if len(frames) > MAX_BULLETS:
        raise ToolError(u"弾数が多すぎます (%d 発 / 上限 %d)。発射間隔を確認してください"
                        % (len(frames), MAX_BULLETS))
    return frames


# ---------------------------------------------------------------- ツール
class HomingTool(BaseTool):
    label = u"追尾弾"
    category = u"射撃"
    order = 10
    tooltip = (u"ロケータ A (発射位置) / B (経由点) / C (着弾点) と、A から B を経由して"
               u"C まで飛ぶ弾 (球体 / つぶれた球体) をまとめて作成します。\n"
               u"ロケータはルート (dfx_homingN) の子になり、動かすと弾道が追従します。\n"
               u"作成後はルートのチャンネルボックスでパラメータを調整できます。")
    params = [
        Param("length", u"A〜C の距離", 10.0, minimum=0.01,
              tooltip=u"作成時のロケータの配置 (A は原点、C は X 方向にこの距離)"),
        Param("height", u"B の高さ", 2.0,
              tooltip=u"作成時のロケータ B の高さ (A と C の中間)"),
        Param("path_type", u"軌道", PATH_SMOOTH, kind="choice",
              choices=[PATH_SMOOTH, PATH_LINEAR],
              tooltip=u"なめらか: B を通る曲線 / 折れ線: A-B-C を直線で結ぶ"),
        Param("start_frame", u"発射開始フレーム", 1.0, decimals=2),
        Param("end_frame", u"発射終了フレーム", 48.0, decimals=2),
        Param("interval", u"発射間隔 (フレーム)", 2.0, minimum=0.1, step=0.5,
              decimals=2, tooltip=u"何フレームごとに 1 発撃つか (小数可)"),
        Param("travel_frames", u"着弾までのフレーム数", 8.0, minimum=0.1,
              step=1.0, decimals=2, tooltip=u"1 発が A から C に到達するまでの時間"),
        Param("speed_mode", u"減速", SPEED_CONSTANT, kind="choice",
              choices=[SPEED_CONSTANT, SPEED_LINEAR, SPEED_QUADRATIC],
              tooltip=u"着弾までのフレーム数は変えずに、途中の速さを変える。\n"
                      u"リニア: 一定の割合で減速する\n"
                      u"2乗: 最初に大きく減速し、だんだん緩やかになる"),
        Param("min_speed", u"最低速度 (%)", 30.0, minimum=0.0, maximum=100.0,
              step=5.0, decimals=1,
              tooltip=u"着弾時の速さ (発射時の速さに対する割合)。減速なしの時は使わない"),
        Param("shape", u"弾の形状", SHAPE_SPHERE, kind="choice",
              choices=[SHAPE_SPHERE, SHAPE_SQUASHED]),
        Param("radius", u"弾の半径", 0.1, minimum=0.001, step=0.05),
        Param("stretch", u"伸び率", 3.0, minimum=0.01, maximum=20.0, step=0.5,
              tooltip=u"つぶれた球体の時のみ。進行方向 (パスに沿った向き) の長さの倍率。\n"
                      u"1 より大きいとパスに沿って伸び、1 未満だと平たくなる"),
        Param("subdivisions", u"球の分割数", 12, minimum=4, maximum=64),
        Param("color", u"色", (1.0, 0.75, 0.3), kind="color"),
        Param("use_instance", u"インスタンスで作成", True,
              tooltip=u"ON: 弾は元メッシュのインスタンス (軽い。元メッシュを"
                      u"編集すると全弾に反映)"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        frames = check_fire_frames(v)

        root = cmds.group(empty=True, name="dfx_homing#")
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, EFFECT_TYPE)
        mu.add_param_attrs(root, stored_params(), v, LIVE_PARAMS)
        locators = _create_locators(cmds, mu, root, v["length"], v["height"])
        mu.lock_transform(root)

        build_effect(cmds, mu, root, frames, v, locators)
        cmds.select(root)
        _log_effect(ctx, root, frames, v, locators)
        return root


class HomingUpdateTool(BaseTool):
    label = u"追尾弾を更新"
    category = u"射撃"
    order = 15
    tooltip = (u"選択した追尾弾を、ルートのアトリビュートの値で作り直します。\n"
               u"チャンネルボックスで「(更新)」の付いた項目を変えたあとに使います。\n"
               u"ルートか、その中の弾などを選択してください (複数可)。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = _selected_roots(mu, u"更新する")
        return rebuild_effects(cmds, mu, ctx, roots)


class HomingSavePresetTool(BaseTool):
    label = u"追尾弾のパラメータを保存"
    category = u"射撃"
    order = 16
    undoable = False  # シーンは変えない
    tooltip = (u"選択した追尾弾のパラメータ (ルートのアトリビュート) を\n"
               u"プリセットファイル (.json) に保存します。")

    def run(self, ctx, **v):
        from . import _maya_utils as mu

        roots = _selected_roots(mu, u"保存する")
        return mu.save_preset_dialog(ctx, roots, EFFECT_TYPE, stored_params(),
                                     self.label, u"追尾弾")


class HomingLoadPresetTool(BaseTool):
    label = u"追尾弾のパラメータを読み込み"
    category = u"射撃"
    order = 17
    tooltip = (u"プリセットファイル (.json) のパラメータを選択した追尾弾に設定し、\n"
               u"作り直します (複数選択可。ロケータの位置はそのまま)。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = _selected_roots(mu, u"読み込み先の")
        values = mu.load_preset_dialog(ctx, EFFECT_TYPE, stored_params(), self.label)
        if values is None:
            return None
        return rebuild_effects(cmds, mu, ctx, roots, overrides=values)


# ---------------------------------------------------------------- 構築
def stored_params():
    """ルートに保存するパラメータ (即反映 → 要更新の順。配置用は保存しない)。"""
    by_name = dict((p.name, p) for p in HomingTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def _create_locators(cmds, mu, root, length, height):
    """root の子にロケータ A/B/C を作り、root のアトリビュートに接続する。

    接続 (message) で覚えるので、作成後にロケータの名前を変えてもよい。
    ルートは原点から動かないので、ローカル座標 = ワールド座標。
    """
    prefix = root.split("|")[-1]
    specs = [("A", (0.0, 0.0, 0.0)),
             ("B", (length * 0.5, height, 0.0)),
             ("C", (length, 0.0, 0.0))]
    locators = []
    for (suffix, pos), attr in zip(specs, LOCATOR_ATTRS):
        loc = cmds.spaceLocator(name="%s_loc%s" % (prefix, suffix))[0]
        loc = cmds.parent(loc, root)[0]
        loc = cmds.ls(loc, long=True)[0]
        cmds.setAttr(loc + ".translate", *pos, type="double3")
        mu.tag(loc)
        cmds.addAttr(root, longName=attr, attributeType="message")
        cmds.connectAttr(loc + ".message", root + "." + attr)
        locators.append(loc)
    return locators


def _selected_roots(mu, purpose):
    roots = mu.selected_effect_roots(EFFECT_TYPE)
    if not roots:
        raise ToolError(u"%s追尾弾 (dfx_homingN か、その中のロケータや弾) を選択してください"
                        % purpose)
    return roots


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    """roots をアトリビュートの値で作り直す。overrides があれば先にその値を設定する。

    全部確認してから作り直す (途中で失敗して一部だけ消えないように)。
    キーや接続で決まっているアトリビュートには overrides を書き込まない。
    """
    params = stored_params()
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % root.split("|")[-1])
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        frames = check_fire_frames(values)
        locators = _stored_locators(cmds, root)
        _check_distinct(locators)
        jobs.append((root, values, frames, locators, driven))

    for root, values, frames, locators, driven in jobs:
        mu.delete_owned(root)
        # 旧バージョンのアトリビュート (つぶれ率など) を今の構成に合わせる
        mu.sync_param_attrs(root, params, values, LIVE_PARAMS, keep=LOCATOR_ATTRS)
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        build_effect(cmds, mu, root, frames, values, locators)
        _log_effect(ctx, root, frames, values, locators)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s"
                    % u", ".join(ignored))
    result = [job[0] for job in jobs]
    cmds.select(result)
    return result


def _check_distinct(locators):
    if len(set(locators)) != 3:
        raise ToolError(u"A, B, C には別々のロケータを指定してください")


def _stored_locators(cmds, root):
    short = root.split("|")[-1]
    result = []
    for attr, label in zip(LOCATOR_ATTRS, "ABC"):
        conns = cmds.listConnections(root + "." + attr, source=True,
                                     destination=False) or []
        if not conns:
            raise ToolError(u"%s のロケータ%s が見つかりません (削除された可能性があります)"
                            % (short, label))
        result.append(cmds.ls(conns[0], long=True)[0])
    return result


def _log_effect(ctx, root, frames, v, locators):
    ctx.log(u"  %s: %d 発 (フレーム %s 〜 %s, %s フレーム間隔)"
            % (root.split("|")[-1], len(frames), frames[0], frames[-1],
               v["interval"]))
    ctx.log(u"  軌道: %s" % u" → ".join(n.split("|")[-1] for n in locators))


def build_effect(cmds, mu, root, frames, v, locators):
    """root の下に軌道・弾・マテリアルを作る (作成時と更新時で共通)。

    root には add_param_attrs 済みのアトリビュートがある前提で、
    LIVE_PARAMS はそのアトリビュートに接続する。
    """
    def attr(name):
        return "%s.%s%s" % (root, mu.PARAM_PREFIX, name)

    prefix = root.split("|")[-1]
    owned = []

    # --- 軌道カーブ
    plugs = [mu.world_position_plug(n, owned) for n in locators]
    curve = _build_path(cmds, plugs, v["path_type"] == PATH_SMOOTH,
                        prefix, root, owned)
    curve_shape = cmds.listRelatives(curve, shapes=True, fullPath=True)[0]

    src, sphere, sg, scale_plug = build_bullet_source(cmds, mu, root, v, attr, prefix, owned)

    # --- 弾
    bullets_grp = cmds.group(empty=True, name=prefix + "_bullets", parent=root)
    owned.append(bullets_grp)
    travel = v["travel_frames"]
    min_ratio = v["min_speed"] / 100.0
    template = None
    bullets = []
    for i, fire in enumerate(frames):
        bullet = make_bullet(cmds, src, sphere, "%s_bullet_%03d" % (prefix, i + 1),
                             bullets_grp, v["use_instance"])
        bullets.append(bullet)
        cmds.connectAttr(scale_plug, bullet + ".scale")

        motion_path = _attach_motion_path(
            cmds, bullet, curve_shape, fire, fire + travel,
            "%s_motion_%03d" % (prefix, i + 1))
        keys = travel_keys(v["speed_mode"], min_ratio, fire, travel)
        u_curve = _key_travel(cmds, motion_path, keys, template)
        if template is None:
            template = (u_curve, fire)
        owned.extend([motion_path, u_curve])

        # 発射フレームで出現し、着弾の次のフレームで消える。
        # shape=False: 既定ではシェイプの visibility にもキーが入り、
        # インスタンスだと共有シェイプに全弾のキーが重なってしまう
        vis_plug = bullet + ".visibility"
        for t, vis in ((fire - 1, 0), (fire, 1), (fire + travel + 1, 0)):
            cmds.setKeyframe(vis_plug, time=t, value=vis, shape=False)
        cmds.keyTangent(vis_plug, outTangentType="step")

    # インスタンスごとにマテリアルを確実に割り当てる
    cmds.sets(bullets, edit=True, forceElement=sg)
    cmds.setAttr(src + ".visibility", 0)

    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)
    return bullets


def build_bullet_source(cmds, mu, root, v, attr, prefix, owned):
    """弾の元メッシュ・大きさのネットワーク・マテリアルを作る (バルカン砲と共通)。

    分割数・半径・形・伸び率・色はルートのアトリビュートに接続 (ライブ)。
    (元メッシュ, polySphere, シェーディンググループ, 弾の scale に繋ぐプラグ) を返す。
    """
    src, sphere = cmds.polySphere(radius=1.0, subdivisionsAxis=v["subdivisions"],
                                  subdivisionsHeight=max(2, v["subdivisions"] // 2),
                                  name=prefix + "_bulletSrc")
    sphere = cmds.rename(sphere, prefix + "_bulletSphere")
    half = cmds.createNode("multiplyDivide", name=prefix + "_halfDiv")
    cmds.setAttr(half + ".operation", 2)  # divide
    cmds.setAttr(half + ".input2X", 2)
    cmds.connectAttr(attr("subdivisions"), sphere + ".subdivisionsAxis")
    cmds.connectAttr(attr("subdivisions"), half + ".input1X")
    cmds.connectAttr(half + ".outputX", sphere + ".subdivisionsHeight")
    src = cmds.parent(src, root)[0]
    src = cmds.ls(src, long=True)[0]
    owned.extend([src, sphere, half])

    # 大きさと形: 弾の scale = 半径 × (進行方向 X だけ伸び率)
    scale_plug = _build_scale_network(cmds, attr, prefix, owned)

    shader, sg = mu.create_surface_shader(prefix + "_mat", v["color"])
    cmds.connectAttr(attr("color"), shader + ".outColor")
    cmds.sets(src, edit=True, forceElement=sg)
    owned.extend([shader, sg])
    return src, sphere, sg, scale_plug


def make_bullet(cmds, src, sphere, name, parent, use_instance):
    """src のインスタンス (または複製) を parent の下に作る。"""
    if use_instance:
        bullet = cmds.instance(src, name=name)[0]
    else:
        bullet = cmds.duplicate(src, name=name)[0]
        # 複製でも分割数の変更が反映されるよう、元の球の出力をつなぐ
        dup_shape = cmds.listRelatives(bullet, shapes=True, fullPath=True)[0]
        cmds.connectAttr(sphere + ".output", dup_shape + ".inMesh", force=True)
    return cmds.parent(bullet, parent)[0]


def _build_scale_network(cmds, attr, prefix, owned):
    """scale = 半径 × (X: つぶれた球体なら伸び率・球体なら 1, Y: 1, Z: 1)。

    弾の X 軸はモーションパスの follow で常に進行方向を向くので、
    X だけ伸ばすとカーブの向きに沿って伸びた形になる。
    """
    shape_param = dict((p.name, p) for p in HomingTool.params)["shape"]
    stretch_on = cmds.createNode("condition", name=prefix + "_stretchOn")
    cmds.connectAttr(attr("shape"), stretch_on + ".firstTerm")
    cmds.setAttr(stretch_on + ".secondTerm",
                 shape_param.choices.index(SHAPE_SQUASHED))
    cmds.connectAttr(attr("stretch"), stretch_on + ".colorIfTrueR")
    cmds.setAttr(stretch_on + ".colorIfFalseR", 1.0)
    owned.append(stretch_on)

    scale = cmds.createNode("multiplyDivide", name=prefix + "_scale")
    owned.append(scale)
    cmds.connectAttr(stretch_on + ".outColorR", scale + ".input1X")
    cmds.setAttr(scale + ".input1Y", 1.0)
    cmds.setAttr(scale + ".input1Z", 1.0)
    for axis in "XYZ":
        cmds.connectAttr(attr("radius"), scale + ".input2" + axis)
    return scale + ".output"


def _attach_motion_path(cmds, bullet, curve_shape, start, end, name):
    """bullet を curve_shape に沿って動かす motionPath を作る (進み具合は _key_travel)。

    cmds.pathAnimation はアンドゥチャンク内で使うと、アンドゥ時に
    それ以前の操作が戻らなくなる (Maya 2027 で確認) ため、
    motionPath ノードを直接組む。設定は pathAnimation の
    -fractionMode -follow -followAxis x -upAxis y
    -worldUpType vector -worldUpVector 0 1 0 と同じ。
    """
    mp = cmds.createNode("motionPath", name=name)
    cmds.setAttr(mp + ".fractionMode", 1)  # 弧長基準 (等速)
    cmds.setAttr(mp + ".follow", 1)
    cmds.setAttr(mp + ".frontAxis", 0)     # X
    cmds.setAttr(mp + ".upAxis", 1)        # Y
    cmds.setAttr(mp + ".worldUpType", 3)   # vector
    cmds.setAttr(mp + ".worldUpVector", 0.0, 1.0, 0.0, type="double3")
    cmds.connectAttr(curve_shape + ".worldSpace[0]", mp + ".geometryPath")
    cmds.connectAttr(bullet + ".rotateOrder", mp + ".rotateOrder")
    cmds.connectAttr(mp + ".allCoordinates", bullet + ".translate")
    cmds.connectAttr(mp + ".rotate", bullet + ".rotate")
    # Maya のマニピュレータ表示用 (pathAnimation と同じ接続)
    cmds.connectAttr(mp + ".message", bullet + ".specifiedManipLocation")
    return mp


def _key_travel(cmds, mp, keys, template):
    """mp.uValue に進み具合のキーを打ち、そのアニメーションカーブを返す。

    template (1 発目のカーブ) があれば複製して時間をずらすだけにする
    (減速ありはキーが多いので、弾ごとに打つと遅い)。
    """
    if template is None:
        for t, u in keys:
            cmds.setKeyframe(mp, attribute="uValue", time=t, value=u,
                             inTangentType="linear", outTangentType="linear")
        return cmds.listConnections(mp + ".uValue", source=True, destination=False)[0]
    template_curve, template_start = template
    curve = cmds.duplicate(template_curve, name=mp + "_uValue")[0]
    offset = keys[0][0] - template_start
    if offset:
        cmds.keyframe(curve, edit=True, relative=True, timeChange=offset)
    cmds.connectAttr(curve + ".output", mp + ".uValue")
    return curve


def _build_path(cmds, plugs, smooth, prefix, root, owned):
    a, b, c = [cmds.getAttr(p)[0] for p in plugs]
    if smooth:
        mid = bezier_control_through(a, b, c)
        curve = cmds.curve(degree=2, point=[a, mid, c], knot=[0, 0, 1, 1],
                           name=prefix + "_path")
    else:
        curve = cmds.curve(degree=1, point=[a, b, c], knot=[0, 1, 2],
                           name=prefix + "_path")
    curve = cmds.parent(curve, root)[0]
    curve = cmds.ls(curve, long=True)[0]
    owned.append(curve)
    cmds.setAttr(curve + ".inheritsTransform", 0)
    cmds.setAttr(curve + ".overrideEnabled", 1)
    cmds.setAttr(curve + ".overrideDisplayType", 1)  # テンプレート
    shape = cmds.listRelatives(curve, shapes=True, fullPath=True)[0]

    # ロケータとライブ接続 (ロケータを動かすと弾道が追従する)
    cmds.connectAttr(plugs[0], shape + ".controlPoints[0]")
    cmds.connectAttr(plugs[2], shape + ".controlPoints[2]")
    if smooth:
        # P1 = 2B - 0.5(A + C) をノードで組む
        sum_ac = cmds.createNode("plusMinusAverage", name=prefix + "_sumAC")
        cmds.connectAttr(plugs[0], sum_ac + ".input3D[0]")
        cmds.connectAttr(plugs[2], sum_ac + ".input3D[1]")
        half_ac = cmds.createNode("multiplyDivide", name=prefix + "_halfAC")
        cmds.setAttr(half_ac + ".input2", 0.5, 0.5, 0.5)
        cmds.connectAttr(sum_ac + ".output3D", half_ac + ".input1")
        double_b = cmds.createNode("multiplyDivide", name=prefix + "_doubleB")
        cmds.setAttr(double_b + ".input2", 2.0, 2.0, 2.0)
        cmds.connectAttr(plugs[1], double_b + ".input1")
        mid_cv = cmds.createNode("plusMinusAverage", name=prefix + "_midCV")
        cmds.setAttr(mid_cv + ".operation", 2)  # subtract
        cmds.connectAttr(double_b + ".output", mid_cv + ".input3D[0]")
        cmds.connectAttr(half_ac + ".output", mid_cv + ".input3D[1]")
        cmds.connectAttr(mid_cv + ".output3D", shape + ".controlPoints[1]")
        owned.extend([sum_ac, half_ac, double_b, mid_cv])
    else:
        cmds.connectAttr(plugs[1], shape + ".controlPoints[1]")
    return curve
