# -*- coding: utf-8 -*-
"""画ぶれ: カメラ (や選択したオブジェクト) にランダムなブレを加える (アニメーションは壊さない)。

カメラ以外 (球・ロケータなど) は「注視点のないカメラ」と同じ扱い: そのオブジェクトの X / Y 軸の
向きにぶれ、回転のブレの基準距離はルートの「回転の基準距離」を使う。

構成:
    dfx_cameraShakeN     ルート (移動ロック)。パラメータと、ブレの元になる乱数のキーを持つ
                         (dfx_noise_tx / ty: カメラの移動、dfx_noise_rx / ry: 回転・注視点)

ブレはカメラの translate / rotate ではなく、足し算用のチャンネルに入れる:
    カメラの移動のブレ  → カメラの rotatePivotTranslate (translate に足される)
    カメラの回転のブレ  → カメラの rotateAxis (カメラ自身の向きの手前で効く回転)
    注視点のブレ        → 注視点の rotatePivotTranslate
なので、カメラや注視点に打ってあるキーはそのまま使える。ブレの向きは画面の横 (X) と縦 (Y)。

1 つのエクスプレッションが毎フレーム計算する:
    ずれ = 強さ (ルート。キーを打てる) × 乱数のキー (-1〜1)
    注視点のないカメラ:
        移動 = カメラの X 軸 × ずれX + Y 軸 × ずれY (カメラの今の向きで計算)
        回転 ON なら rotateAxis = 中心までの距離 (centerOfInterest) 先の点を、
        別の乱数のずれだけ動かした時の角度
    注視点のあるカメラ (Camera and Aim など):
        移動 = (カメラ → 注視点の向きから求めた) 画面の横・縦 × ずれ
        回転 ON なら注視点も別の乱数でずらす (向きがぶれる)
        回転 OFF なら注視点もカメラと同じだけずらす (向きは変わらない)

乱数のキーは「間隔」ごと (ばらつきあり) に、作成・更新した時点のタイムスライダーの範囲に打つ。
「間隔」にキーを打ってあれば、その時々の間隔で打つ (更新で反映)。
強さはエクスプレッションが直接読むので、キーを打てばだんだん強く・弱くできる (すぐ反映)。
"""
from __future__ import absolute_import

import math
import random

from ..core import BaseTool, Param, ToolError
from . import focus_lines as fl

EFFECT_TYPE = "camera_shake"
TAB = fl.TAB
CATEGORY = u"画ぶれ"
AIM_ATTR = "dfx_aim"
BASE_TWIST_ATTR = "dfxBaseTwist"   # 注視点のあるカメラの、作成前の lookAt の twist
NOISE_ATTRS = ("dfx_noise_tx", "dfx_noise_ty", "dfx_noise_rx", "dfx_noise_ry", "dfx_noise_tz")
# 作成前にカメラ・注視点に入っていた値 (ブレはこれに足す。外すと元に戻す)
BASE_ATTRS = (("dfxBaseCamRT", "rotatePivotTranslate", "camera"),
              ("dfxBaseCamRA", "rotateAxis", "camera"),
              ("dfxBaseAimRT", "rotatePivotTranslate", "aim"))

INTERP_SMOOTH = u"なめらか"
INTERP_LINEAR = u"直線"
INTERP_STEP = u"カクカク (ステップ)"
MIN_STEP = 0.25        # キーの間隔の最小 (フレーム)
FLIP_CHANCE = 0.75     # 次のキーで向きが反対になる確率 (ぶるぶる震える感じ)

LIVE_PARAMS = ("rotation", "strength_x", "strength_y", "strength_z", "rotation_distance")
REBUILD_PARAMS = ("interval", "interval_random", "strength_random", "interpolation", "seed")


# ---------------------------------------------------------------- 純粋な計算
def shake_keys(seed, start, end, interval_at, interval_random, strength_random):
    """乱数のキー [(フレーム, (移動X, 移動Y, 回転X, 回転Y, 移動Z))]。値は -1〜1。

    interval_at(t) はフレーム t での「間隔」(キーを打ってあれば変わる)。
    次のキーまでの間隔 = 間隔 × (1 ± 間隔のばらつき)、値の大きさ = 1 - 強さのばらつき × 乱数。
    向きは FLIP_CHANCE の確率で前のキーと反対にする。
    移動Z は後から追加したので別の乱数の列で作る (前に作った画ぶれの X / Y のぶれ方が変わらないように)。
    """
    rng = random.Random("%s-shake" % seed)
    rng_z = random.Random("%s-shake-z" % seed)
    signs = [1.0, -1.0, 1.0, -1.0]
    sign_z = 1.0
    keys = []
    t = float(start)
    while True:
        values = []
        for c in range(4):
            if rng.random() < FLIP_CHANCE:
                signs[c] = -signs[c]
            values.append(signs[c] * (1.0 - strength_random * rng.random()))
        if rng_z.random() < FLIP_CHANCE:
            sign_z = -sign_z
        values.append(sign_z * (1.0 - strength_random * rng_z.random()))
        keys.append((t, tuple(values)))
        if t > end:
            break
        step = interval_at(t) * (1.0 + interval_random * (2.0 * rng.random() - 1.0))
        t += max(MIN_STEP, step)
    return keys


def rotation_offset(offset_x, offset_y, center_of_interest):
    """中心までの距離先の点を (offset_x, offset_y) ずらした時の (回転X, 回転Y) [度]。"""
    coi = max(center_of_interest, 1e-3)
    return (math.degrees(math.atan2(offset_y, coi)), -math.degrees(math.atan2(offset_x, coi)))


def aim_axes(camera_pos, aim_pos):
    """カメラ → 注視点の向きから、画面の横 (X) と縦 (Y) の向き (カメラの親の座標)。"""
    f = [a - c for a, c in zip(aim_pos, camera_pos)]
    n = math.sqrt(sum(x * x for x in f))
    if n < 1e-9:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    f = [x / n for x in f]
    right = (-f[2], 0.0, f[0])
    rn = math.hypot(right[0], right[2])
    if rn < 1e-9:  # 真上・真下を向いている
        right = (1.0, 0.0, 0.0)
    else:
        right = (right[0] / rn, 0.0, right[2] / rn)
    up = (right[1] * f[2] - right[2] * f[1], right[2] * f[0] - right[0] * f[2],
          right[0] * f[1] - right[1] * f[0])
    return right, up


# ---------------------------------------------------------------- ツール
class CameraShakeTool(BaseTool):
    label = u"画ぶれ"
    tab = TAB
    category = CATEGORY
    order = 640
    tooltip = (u"選択したオブジェクトにランダムなブレを加えます (カメラ・球・ロケータなど。複数選択可)。\n"
               u"何も選択していなければアクティブなビューのカメラ。\n"
               u"キーはそのまま、上乗せでぶれます。強さにキーを打つと、だんだん強く・弱くできます。\n"
               u"注視点のあるカメラ (Camera and Aim) は注視点もぶらします。")
    params = [
        Param("rotation", u"カメラの回転のブレ", True,
              tooltip=u"ON: 向きもぶれる (注視点のあるカメラは注視点を、それ以外は回転 X / Y をぶらす)\n"
                      u"OFF: 位置だけぶれる (注視点のあるカメラは注視点もカメラと同じだけ動かす)"),
        Param("rotation_distance", u"回転の基準距離 (カメラ以外)", 5.0, minimum=0.001, step=1.0,
              tooltip=u"カメラ以外のオブジェクトの回転のブレの大きさ。この距離だけ先の点が\n"
                      u"「強さ」ぶんずれる角度で回る (小さいほど大きく回る)。\n"
                      u"カメラはカメラの「中心までの距離」(centerOfInterest) を使う"),
        Param("strength_x", u"X (横) の強さ", 0.2, minimum=0.0, step=0.1,
              tooltip=u"画面の横方向のブレの大きさ (シーンの単位)。キーを打てる"),
        Param("strength_y", u"Y (縦) の強さ", 0.2, minimum=0.0, step=0.1,
              tooltip=u"画面の縦方向のブレの大きさ (シーンの単位)。キーを打てる"),
        Param("strength_z", u"Z (奥行き) の強さ", 0.0, minimum=0.0, step=0.1,
              tooltip=u"奥行き方向 (カメラの前後・オブジェクトの Z 軸) のブレの大きさ (シーンの単位)。\n"
                      u"キーを打てる。注視点のあるカメラで回転のブレ OFF の時は、注視点も同じだけ動く"),
        Param("interval", u"間隔 (フレーム)", 2.0, minimum=MIN_STEP, step=0.5, decimals=2,
              tooltip=u"何フレームごとにブレの向きが変わるか。小さいほど細かくぶれる\n"
                      u"(キーを打ってあれば、その時々の間隔で作る。変えたら「画ぶれを更新」)"),
        Param("interval_random", u"間隔のばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"0 で一定。1 で 0〜2 倍の間でばらつく"),
        Param("strength_random", u"強さのばらつき", 0.5, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"0 で毎回「強さ」いっぱいにぶれる。1 で 0〜強さ の間でばらつく"),
        Param("interpolation", u"動き方", INTERP_SMOOTH, kind="choice",
              choices=[INTERP_SMOOTH, INTERP_LINEAR, INTERP_STEP],
              tooltip=u"なめらか: 手持ちカメラのように揺れる\n直線: キーの間をまっすぐ動く\n"
                      u"カクカク: キーのフレームでぱっと動く (衝撃など)"),
        Param("seed", u"乱数シード", 1, minimum=0, maximum=99999),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return create_shakes(cmds, mu, ctx, v, kind_of(EFFECT_TYPE))


class CameraShakeUpdateTool(BaseTool):
    label = u"画ぶれを更新"
    tab = TAB
    category = CATEGORY
    order = 650
    tooltip = (u"選択した画ぶれを、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (間隔など) を変えたあと、タイムスライダーの範囲を変えたあとに使います。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return rebuild_effects(cmds, mu, ctx, _selected_roots(cmds, mu, u"更新する", EFFECT_TYPE))


class CameraShakeRemoveTool(BaseTool):
    label = u"画ぶれを外す"
    tab = TAB
    category = CATEGORY
    order = 656
    tooltip = (u"選択した画ぶれを削除し、カメラ (と注視点) を元に戻します。\n"
               u"カメラを選択しても、そのカメラの画ぶれが対象になります。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = _selected_roots(cmds, mu, u"外す", EFFECT_TYPE)
        for root in roots:
            release(cmds, mu, root)
            ctx.log(u"  %s を外しました" % _short(root))
            cmds.delete(root)
        return len(roots)


class CameraShakeSavePresetTool(BaseTool):
    label = u"画ぶれのパラメータを保存"
    tab = TAB
    category = CATEGORY
    order = 652
    undoable = False
    tooltip = u"選択した画ぶれのパラメータをプリセットファイル (.json) に保存します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = _selected_roots(cmds, mu, u"保存する", EFFECT_TYPE)
        return mu.save_preset_dialog(ctx, roots, EFFECT_TYPE, stored_params(), self.label, u"画ぶれ")


class CameraShakeLoadPresetTool(BaseTool):
    label = u"画ぶれのパラメータを読み込み"
    tab = TAB
    category = CATEGORY
    order = 654
    tooltip = (u"プリセットファイル (.json) のパラメータを選択した画ぶれに設定し、作り直します。\n"
               u"キーを打ってある項目 (強さなど) は変更しません。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = _selected_roots(cmds, mu, u"読み込み先の", EFFECT_TYPE)
        values = mu.load_preset_dialog(ctx, EFFECT_TYPE, stored_params(), self.label)
        if values is None:
            return None
        return rebuild_effects(cmds, mu, ctx, roots, overrides=values)


# ---------------------------------------------------------------- 種類 (画ぶれ / 手ブレ)
SHAKE_TYPES = (EFFECT_TYPE, "hand_shake")


class ShakeKind(object):
    """画ぶれ・手ブレで違うところ (名前・パラメータ・エクスプレッションの作り方)。"""

    def __init__(self, effect_type, root_name, noun, params_fn, live_params, build_fn):
        self.effect_type = effect_type
        self.root_name = root_name
        self.noun = noun
        self.stored_params = params_fn
        self.live_params = live_params
        self.build = build_fn


def kind_of(effect_type):
    if effect_type == EFFECT_TYPE:
        return ShakeKind(EFFECT_TYPE, "dfx_cameraShake#", u"画ぶれ", stored_params, LIVE_PARAMS,
                         build_effect)
    from . import hand_shake
    return hand_shake.KIND


def _shake_roots(cmds, mu, effect_types=SHAKE_TYPES):
    return [r for r in mu.all_effect_roots()
            if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in effect_types]


def create_shakes(cmds, mu, ctx, v, kind):
    """選択したオブジェクト (無ければアクティブなビューのカメラ) 1 つにつき 1 つ作る。"""
    targets = resolve_targets(cmds, mu, kind.noun)
    # 全部確認してから作る (途中で失敗して一部だけ付かないように)
    for target in targets:
        for other in _shake_roots(cmds, mu):
            if not mu.is_baked(other) and _stored(cmds, other, fl.CAMERA_ATTR) == target:
                other_kind = kind_of(cmds.getAttr(other + "." + mu.EFFECT_TYPE_ATTR))
                raise ToolError(u"%s には既に%s (%s) が付いています (画ぶれと手ブレは 1 つのオブジェクトに"
                                u"どちらか 1 つ)" % (_short(target), other_kind.noun, _short(other)))
        _check_channels(cmds, target, find_aim(cmds, target), kind.noun)
    roots = []
    for i, target in enumerate(targets):
        # 複数のオブジェクトが同じぶれ方にならないよう、シードを 1 ずつずらす
        values = dict(v, seed=v["seed"] + i)
        root = cmds.group(empty=True, name=kind.root_name, world=True)
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, kind.effect_type)
        mu.add_param_attrs(root, kind.stored_params(), values, kind.live_params)
        fl.attach_camera(cmds, root, target)
        mu.lock_transform(root)
        aim = find_aim(cmds, target)
        if aim:
            cmds.addAttr(root, longName=AIM_ATTR, attributeType="message")
            cmds.connectAttr(aim + ".message", root + "." + AIM_ATTR)
        _store_base(cmds, root, target, aim)
        kind.build(cmds, mu, root, values, target, aim)
        _log(ctx, root, target, aim)
        roots.append(root)
    cmds.select(roots)
    return roots[0] if len(roots) == 1 else roots


# ---------------------------------------------------------------- 構築
def stored_params():
    by_name = dict((p.name, p) for p in CameraShakeTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def _short(node):
    return node.split("|")[-1]


def _log(ctx, root, target, aim):
    from maya import cmds
    if camera_shape(cmds, target):
        ctx.log(u"  %s: カメラ %s%s" % (_short(root), _short(target),
                                     u" (注視点 %s)" % _short(aim) if aim else u" (注視点なし)"))
    else:
        ctx.log(u"  %s: %s" % (_short(root), _short(target)))


def camera_shape(cmds, node):
    shapes = cmds.listRelatives(node, shapes=True, type="camera", fullPath=True) or []
    return shapes[0] if shapes else None


def resolve_targets(cmds, mu, noun=u"画ぶれ"):
    """ぶらすオブジェクト: 選択中の transform (シェイプを選んだらその親)。
    何も選んでいなければアクティブなビューのカメラ。画ぶれのルート自身は除く。
    """
    targets = []
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if not cmds.objectType(node, isAType="transform"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            node = parents[0] if parents else None
        if (not node or node in targets
                or (cmds.attributeQuery(mu.EFFECT_TYPE_ATTR, node=node, exists=True)
                    and cmds.getAttr(node + "." + mu.EFFECT_TYPE_ATTR) in SHAKE_TYPES)):
            continue
        targets.append(node)
    if targets:
        return targets
    try:
        return [fl.resolve_camera(cmds, noun)]
    except ToolError:
        raise ToolError(u"ぶらすオブジェクト (カメラ・球・ロケータなど) を選択してください")


def _stored(cmds, root, attr):
    if not cmds.attributeQuery(attr, node=root, exists=True):
        return None
    conns = cmds.listConnections(root + "." + attr, source=True, destination=False) or []
    return cmds.ls(conns[0], long=True)[0] if conns else None


def roots_of_selected_targets(cmds, mu, effect_types=SHAKE_TYPES):
    """選択したオブジェクト (ぶらしている物・注視点。シェイプならその親) に付いている画ぶれ。"""
    selected = set()
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        selected.add(node)
        selected.update(cmds.listRelatives(node, parent=True, fullPath=True) or [])
    roots = []
    for root in _shake_roots(cmds, mu, effect_types):
        if selected & set(filter(None, (_stored(cmds, root, fl.CAMERA_ATTR),
                                        _stored(cmds, root, AIM_ATTR)))):
            roots.append(root)
    return roots


def _selected_roots(cmds, mu, purpose, effect_type):
    """選択したルートと、選択したオブジェクト・注視点に付いている、effect_type のもの。"""
    roots = mu.selected_effect_roots(effect_type)
    roots += [r for r in roots_of_selected_targets(cmds, mu, (effect_type,)) if r not in roots]
    if not roots:
        kind = kind_of(effect_type)
        raise ToolError(u"%s%s (%s、またはぶらしているオブジェクト) を選択してください"
                        % (purpose, kind.noun, kind.root_name.replace("#", "N")))
    return roots


def find_aim_constraint(cmds, camera):
    """カメラの向きを決めている lookAt / aimConstraint。無ければ None。"""
    if not camera_shape(cmds, camera):
        return None
    cons = cmds.listConnections(camera + ".rotateX", source=True, destination=False,
                                type="aimConstraint") or []
    return cmds.ls(cons[0], long=True)[0] if cons else None


def find_aim(cmds, camera):
    """カメラの向きを決めている注視点 (lookAt / aimConstraint のターゲット)。無ければ None。

    カメラ以外のオブジェクトは注視点なしとして扱う (注視点のないカメラと同じ)。
    """
    if not camera_shape(cmds, camera):
        return None
    for con in cmds.listConnections(camera + ".rotateX", source=True, destination=False,
                                    type="aimConstraint") or []:
        targets = cmds.listConnections(con + ".target[0].targetTranslate", source=True,
                                       destination=False) or []
        if not targets:
            targets = cmds.listConnections(con + ".target[0].targetTranslateX", source=True,
                                           destination=False) or []
        if targets:
            return cmds.ls(targets[0], long=True)[0]
    return None


def _check_channels(cmds, camera, aim, noun=u"画ぶれ"):
    plugs = [camera + "." + a for a in ("rotatePivotTranslateX", "rotatePivotTranslateY",
                                         "rotatePivotTranslateZ", "rotateAxisX", "rotateAxisY",
                                         "rotateAxisZ")]
    if aim:
        plugs += [aim + "." + a for a in ("rotatePivotTranslateX", "rotatePivotTranslateY",
                                           "rotatePivotTranslateZ")]
        con = find_aim_constraint(cmds, camera)
        if con and cmds.attributeQuery("twist", node=con, exists=True):
            plugs.append(con + ".twist")
    for plug in plugs:
        if cmds.getAttr(plug, lock=True) or cmds.listConnections(plug, source=True, destination=False):
            raise ToolError(u"%s はロックされているか、ほかのものにつながっているので%sを付けられません"
                            % (_short(plug), noun))


def _store_base(cmds, root, camera, aim):
    for attr, channel, who in BASE_ATTRS:
        node = camera if who == "camera" else aim
        value = cmds.getAttr(node + "." + channel)[0] if node else (0.0, 0.0, 0.0)
        cmds.addAttr(root, longName=attr, attributeType="double3")
        for axis in "XYZ":
            cmds.addAttr(root, longName=attr + axis, attributeType="double", parent=attr)
        cmds.setAttr(root + "." + attr, *value, type="double3")
    con = find_aim_constraint(cmds, camera) if aim else None
    if con and cmds.attributeQuery("twist", node=con, exists=True):
        cmds.addAttr(root, longName=BASE_TWIST_ATTR, attributeType="double")
        cmds.setAttr(root + "." + BASE_TWIST_ATTR, cmds.getAttr(con + ".twist"))


def _base(cmds, root, attr):
    return cmds.getAttr(root + "." + attr)[0]


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    """roots をアトリビュートの値で作り直す (画ぶれ・手ブレ共通)。"""
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % _short(root))
        camera = _stored(cmds, root, fl.CAMERA_ATTR)
        if not camera:
            raise ToolError(u"%s のぶらすオブジェクトが見つかりません" % _short(root))
        jobs.append((root, camera, _stored(cmds, root, AIM_ATTR)))
    for root, camera, aim in jobs:
        kind = kind_of(cmds.getAttr(root + "." + mu.EFFECT_TYPE_ATTR))
        params = kind.stored_params()
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        release(cmds, mu, root, delete_keys=True)
        mu.sync_param_attrs(root, params, values, kind.live_params,
                            keep=(fl.CAMERA_ATTR, AIM_ATTR) + NOISE_ATTRS)
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        kind.build(cmds, mu, root, values, camera, aim)
        _log(ctx, root, camera, aim)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s" % u", ".join(ignored))
    result = [job[0] for job in jobs]
    cmds.select(result)
    return result


def release(cmds, mu, root, delete_keys=False):
    """エクスプレッションを消して、カメラ・注視点のブレ用チャンネルを作成前の値に戻す。"""
    mu.delete_owned(root)
    camera = _stored(cmds, root, fl.CAMERA_ATTR)
    aim = _stored(cmds, root, AIM_ATTR)
    for attr, channel, who in BASE_ATTRS:
        node = camera if who == "camera" else aim
        if node and cmds.attributeQuery(attr, node=root, exists=True):
            plug = node + "." + channel
            if not cmds.listConnections(plug, source=True, destination=False):
                cmds.setAttr(plug, *_base(cmds, root, attr), type="double3")
    con = find_aim_constraint(cmds, camera) if (camera and aim) else None
    if con and cmds.attributeQuery(BASE_TWIST_ATTR, node=root, exists=True):
        if not cmds.listConnections(con + ".twist", source=True, destination=False):
            cmds.setAttr(con + ".twist", cmds.getAttr(root + "." + BASE_TWIST_ATTR))
    if delete_keys:
        for attr in NOISE_ATTRS:
            if cmds.attributeQuery(attr, node=root, exists=True):
                cmds.cutKey(root, attribute=attr, clear=True)


def _noise_attrs(cmds, root):
    for attr in NOISE_ATTRS:
        if not cmds.attributeQuery(attr, node=root, exists=True):
            cmds.addAttr(root, longName=attr, attributeType="double")


def build_effect(cmds, mu, root, v, camera, aim):
    """乱数のキーとエクスプレッションを作る (作成時と更新時で共通)。"""
    _noise_attrs(cmds, root)
    start = cmds.playbackOptions(query=True, minTime=True)
    end = cmds.playbackOptions(query=True, maxTime=True)
    interval_plug = root + ".dfx_interval"
    keys = shake_keys(v["seed"], start, end,
                      lambda t: max(MIN_STEP, cmds.getAttr(interval_plug, time=t)),
                      v["interval_random"], v["strength_random"])
    tangent = {INTERP_SMOOTH: ("auto", "auto"), INTERP_LINEAR: ("linear", "linear"),
               INTERP_STEP: ("linear", "step")}[v["interpolation"]]
    for t, values in keys:
        for attr, value in zip(NOISE_ATTRS, values):
            cmds.setKeyframe(root, attribute=attr, time=t, value=value,
                             inTangentType=tangent[0], outTangentType=tangent[1])
    shape = camera_shape(cmds, camera)
    expr = cmds.expression(name=_short(root) + "_shake", alwaysEvaluate=True, unitConversion="all",
                           string=_expression(root, camera, shape, aim))
    mu.tag(expr)
    mu.own(root, expr)
    return expr


def _expression(root, camera, shape, aim):
    """画ぶれのエクスプレッション: ずれ = 強さ × 乱数のキー。"""
    r = _short(root)
    head = u"""
float $dx = {r}.dfx_strength_x * {r}.dfx_noise_tx;
float $dy = {r}.dfx_strength_y * {r}.dfx_noise_ty;
float $ax = {r}.dfx_strength_x * {r}.dfx_noise_rx;
float $ay = {r}.dfx_strength_y * {r}.dfx_noise_ry;
float $dz = {r}.dfx_strength_z * {r}.dfx_noise_tz;
float $on = {r}.dfx_rotation;
""".format(r=r)
    return head + apply_expression(root, camera, shape, aim)


def apply_expression(root, camera, shape, aim, roll=False, con=None):
    """ずれをオブジェクト (と注視点) に入れる部分 (画ぶれ・手ブレ共通)。

    前に $dx $dy $dz (位置のずれ。Z は奥行き)、$ax $ay (回転・注視点のずれ)、$on (回転のブレ ON) を、
    roll なら $roll (傾き [度]) を計算しておく。shape はカメラのシェイプ (カメラ以外は None)、
    con は注視点のあるカメラの lookAt (傾きは twist に入れる)。
    """
    r, c = _short(root), _short(camera)
    # 回転のブレの基準距離: カメラは中心までの距離、それ以外はルートの「回転の基準距離」
    coi = (u"%s.centerOfInterest" % _short(shape)) if shape else (u"%s.dfx_rotation_distance" % r)
    if aim:
        a = _short(aim)
        body = u"""
float $fx = {a}.translateX - {c}.translateX;
float $fy = {a}.translateY - {c}.translateY;
float $fz = {a}.translateZ - {c}.translateZ;
float $n = sqrt($fx * $fx + $fy * $fy + $fz * $fz);
float $X[] = {{1.0, 0.0, 0.0}};
float $Y[] = {{0.0, 1.0, 0.0}};
float $Z[] = {{0.0, 0.0, 1.0}};
if ($n > 1e-9) {{
    $fx /= $n; $fy /= $n; $fz /= $n;
    float $rn = sqrt($fz * $fz + $fx * $fx);
    if ($rn > 1e-9) $X = {{-$fz / $rn, 0.0, $fx / $rn}};
    $Y = {{$X[1] * $fz - $X[2] * $fy, $X[2] * $fx - $X[0] * $fz, $X[0] * $fy - $X[1] * $fx}};
    $Z = {{-$fx, -$fy, -$fz}};
}}
float $ox = $dx * $X[0] + $dy * $Y[0] + $dz * $Z[0];
float $oy = $dx * $X[1] + $dy * $Y[1] + $dz * $Z[1];
float $oz = $dx * $X[2] + $dy * $Y[2] + $dz * $Z[2];
{c}.rotatePivotTranslateX = {r}.dfxBaseCamRTX + $ox;
{c}.rotatePivotTranslateY = {r}.dfxBaseCamRTY + $oy;
{c}.rotatePivotTranslateZ = {r}.dfxBaseCamRTZ + $oz;
float $px = $ox;
float $py = $oy;
float $pz = $oz;
if ($on) {{
    $px = $ax * $X[0] + $ay * $Y[0];
    $py = $ax * $X[1] + $ay * $Y[1];
    $pz = $ax * $X[2] + $ay * $Y[2];
}}
{a}.rotatePivotTranslateX = {r}.dfxBaseAimRTX + $px;
{a}.rotatePivotTranslateY = {r}.dfxBaseAimRTY + $py;
{a}.rotatePivotTranslateZ = {r}.dfxBaseAimRTZ + $pz;
""".format(r=r, c=c, a=a)
        if roll and con:
            body += u"{t}.twist = {r}.{b} + $roll;\n".format(t=_short(con), r=r, b=BASE_TWIST_ATTR)
    else:
        body = u"""
float $cx = cosd({c}.rotateX); float $sx = sind({c}.rotateX);
float $cy = cosd({c}.rotateY); float $sy = sind({c}.rotateY);
float $cz = cosd({c}.rotateZ); float $sz = sind({c}.rotateZ);
float $RX[] = {{1.0, 0.0, 0.0, 0.0, $cx, $sx, 0.0, -$sx, $cx}};
float $RY[] = {{$cy, 0.0, -$sy, 0.0, 1.0, 0.0, $sy, 0.0, $cy}};
float $RZ[] = {{$cz, $sz, 0.0, -$sz, $cz, 0.0, 0.0, 0.0, 1.0}};
int $ord = {c}.rotateOrder;
float $A[] = $RX; float $B[] = $RY; float $C[] = $RZ;
if ($ord == 1) {{ $A = $RY; $B = $RZ; $C = $RX; }}
else if ($ord == 2) {{ $A = $RZ; $B = $RX; $C = $RY; }}
else if ($ord == 3) {{ $A = $RX; $B = $RZ; $C = $RY; }}
else if ($ord == 4) {{ $A = $RY; $B = $RX; $C = $RZ; }}
else if ($ord == 5) {{ $A = $RZ; $B = $RY; $C = $RX; }}
float $T[] = {{0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}};
float $M[] = {{0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}};
int $i; int $j; int $k;
for ($i = 0; $i < 3; $i++) for ($j = 0; $j < 3; $j++) for ($k = 0; $k < 3; $k++)
    $T[$i * 3 + $j] += $A[$i * 3 + $k] * $B[$k * 3 + $j];
for ($i = 0; $i < 3; $i++) for ($j = 0; $j < 3; $j++) for ($k = 0; $k < 3; $k++)
    $M[$i * 3 + $j] += $T[$i * 3 + $k] * $C[$k * 3 + $j];
{c}.rotatePivotTranslateX = {r}.dfxBaseCamRTX + $dx * $M[0] + $dy * $M[3] + $dz * $M[6];
{c}.rotatePivotTranslateY = {r}.dfxBaseCamRTY + $dx * $M[1] + $dy * $M[4] + $dz * $M[7];
{c}.rotatePivotTranslateZ = {r}.dfxBaseCamRTZ + $dx * $M[2] + $dy * $M[5] + $dz * $M[8];
float $coi = max({coi}, 0.001);
{c}.rotateAxisX = {r}.dfxBaseCamRAX + $on * atan2d($ay, $coi);
{c}.rotateAxisY = {r}.dfxBaseCamRAY - $on * atan2d($ax, $coi);
""".format(r=r, c=c, coi=coi)
        if roll:
            body += u"{c}.rotateAxisZ = {r}.dfxBaseCamRAZ + $roll;\n".format(c=c, r=r)
    return body


# ---------------------------------------------------------------- ベイク
def bake_effect(cmds, mu, root, start, end):
    """ブレをカメラ (と注視点) の translate / rotate の毎フレームのキーにして、画ぶれを外す。

    注視点のないカメラは、回転のブレ (rotateAxis) も rotate に入れる (向きが同じになるように計算)。
    """
    from maya.api import OpenMaya as om

    camera = _stored(cmds, root, fl.CAMERA_ATTR)
    aim = _stored(cmds, root, AIM_ATTR)
    base_rt = om.MVector(*_base(cmds, root, "dfxBaseCamRT"))
    base_ra = _base(cmds, root, "dfxBaseCamRA")
    base_aim = om.MVector(*_base(cmds, root, "dfxBaseAimRT"))
    order = cmds.getAttr(camera + ".rotateOrder")
    frames = []
    f = start
    while f <= end + 1e-6:
        frames.append(f)
        f += 1.0
    cam_t, cam_r, aim_t, twist = [], [], [], []
    # 手ブレの傾き (注視点のあるカメラは lookAt の twist に入れている)
    con = find_aim_constraint(cmds, camera) if aim else None
    # (角度なので unitConversion ノードを挟んでつながっている)
    twist_driven = bool(con and cmds.listConnections(con + ".twist", source=True, destination=False,
                                                     skipConversionNodes=True, type="expression"))
    prev = None
    for f in frames:
        if twist_driven:
            twist.append(cmds.getAttr(con + ".twist", time=f))
        t = om.MVector(*cmds.getAttr(camera + ".translate", time=f)[0])
        rt = om.MVector(*cmds.getAttr(camera + ".rotatePivotTranslate", time=f)[0])
        cam_t.append(t + rt - base_rt)
        if not aim:
            r = [math.radians(x) for x in cmds.getAttr(camera + ".rotate", time=f)[0]]
            ra = [math.radians(x) for x in cmds.getAttr(camera + ".rotateAxis", time=f)[0]]
            ra0 = [math.radians(x) for x in base_ra]
            # Maya の行列は rotateAxis → rotate の順に効く (行ベクトル: RA * R)
            m = (om.MEulerRotation(ra[0], ra[1], ra[2], om.MEulerRotation.kXYZ).asMatrix()
                 * om.MEulerRotation(r[0], r[1], r[2], order).asMatrix())
            # 元の rotateAxis に戻した時に同じ向きになる rotate
            m = (om.MEulerRotation(ra0[0], ra0[1], ra0[2], om.MEulerRotation.kXYZ)
                 .asMatrix().inverse() * m)
            e = om.MEulerRotation.decompose(m, order)
            if prev is not None:
                e = e.closestSolution(prev)
            prev = e
            cam_r.append([math.degrees(x) for x in (e.x, e.y, e.z)])
        else:
            at = om.MVector(*cmds.getAttr(aim + ".translate", time=f)[0])
            art = om.MVector(*cmds.getAttr(aim + ".rotatePivotTranslate", time=f)[0])
            aim_t.append(at + art - base_aim)
    release(cmds, mu, root)
    _write_keys(cmds, camera, "translate", frames, cam_t, start, end)
    if cam_r:
        _write_keys(cmds, camera, "rotate", frames, cam_r, start, end)
    if aim:
        _write_keys(cmds, aim, "translate", frames, aim_t, start, end)
    if twist:
        cmds.cutKey(con + ".twist", time=(start, end), clear=True)
        for f, value in zip(frames, twist):
            cmds.setKeyframe(con + ".twist", time=f, value=value, inTangentType="linear",
                             outTangentType="linear")
    return len(frames)


def _write_keys(cmds, node, attr, frames, values, start, end):
    for i, axis in enumerate("XYZ"):
        plug = node + "." + attr + axis
        cmds.cutKey(plug, time=(start, end), clear=True)
        for f, v in zip(frames, values):
            cmds.setKeyframe(plug, time=f, value=v[i], inTangentType="linear",
                             outTangentType="linear")
