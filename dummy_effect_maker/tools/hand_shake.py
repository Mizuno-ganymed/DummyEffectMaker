# -*- coding: utf-8 -*-
"""手ブレ: 手持ちカメラのような、ゆっくりふらふらした揺れを加える。

仕組みは画ぶれ (camera_shake) と同じで、カメラ (やほかのオブジェクト) の足し算用チャンネル
(rotatePivotTranslate・rotateAxis、注視点の rotatePivotTranslate) にずれを入れる。
キーは打たず、エクスプレッションの noise() (なめらかな乱数の波) で毎フレーム計算するので、
パラメータはすべてすぐ反映され、タイムスライダーの範囲にも関係ない。

    波 = (noise(フレーム / 周期) + 細かい揺れ × noise(フレーム / 周期 × 3.1)) / (1 + 細かい揺れ)
    位置のずれ   = 強さ X / Y / Z × 波 (チャンネルごとに別の波。Z は奥行き = カメラの前後)
    回転のずれ   = 注視点のあるカメラは注視点を、それ以外は回転 X / Y を (画ぶれと同じ)
    傾き         = 傾きの強さ [度] × 波 (注視点のないカメラ・オブジェクトは rotateAxis Z、
                   注視点のあるカメラは lookAt の twist)
    回転のブレ OFF なら回転と傾きは無し (位置だけ。注視点はカメラと同じだけ動かす)

1 つのオブジェクトに付けられるのは、画ぶれか手ブレのどちらか 1 つ。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param
from . import camera_shake as cs

EFFECT_TYPE = "hand_shake"
TAB = cs.TAB
CATEGORY = u"手ブレ"

LIVE_PARAMS = ("rotation", "strength_x", "strength_y", "strength_z", "roll", "period", "detail",
               "rotation_distance", "seed")
REBUILD_PARAMS = ()
# 波の位相をチャンネルごとにずらす量 (同じ動きにならないように)
# 位置 X / 位置 Y / 回転 X / 回転 Y / 傾き / 位置 Z (Z は後から追加。前の 5 つは変えない)
CHANNEL_OFFSETS = (0.0, 101.3, 203.7, 307.1, 409.9, 511.3)


class HandShakeTool(BaseTool):
    label = u"手ブレ"
    tab = TAB
    category = CATEGORY
    order = 670
    tooltip = (u"選択したオブジェクトに、手持ちカメラのようなゆっくりした揺れを加えます\n"
               u"(カメラ・球・ロケータなど。複数選択可。何も選択していなければアクティブなビューのカメラ)。\n"
               u"キーはそのまま、上乗せで揺れます。強さなどにキーを打つと、だんだん強く・弱くできます。")
    params = [
        Param("rotation", u"カメラの回転のブレ", True,
              tooltip=u"ON: 向きと傾きも揺れる (注視点のあるカメラは注視点を、それ以外は回転 X / Y / Z を揺らす)\n"
                      u"OFF: 位置だけ揺れる (注視点のあるカメラは注視点もカメラと同じだけ動かす)"),
        Param("strength_x", u"X (横) の強さ", 0.05, minimum=0.0, step=0.01, decimals=4,
              tooltip=u"画面の横方向の揺れの大きさ (シーンの単位)。キーを打てる"),
        Param("strength_y", u"Y (縦) の強さ", 0.05, minimum=0.0, step=0.01, decimals=4,
              tooltip=u"画面の縦方向の揺れの大きさ (シーンの単位)。キーを打てる"),
        Param("strength_z", u"Z (奥行き) の強さ", 0.0, minimum=0.0, step=0.01, decimals=4,
              tooltip=u"奥行き方向 (カメラの前後・オブジェクトの Z 軸) の揺れの大きさ (シーンの単位)。\n"
                      u"キーを打てる。注視点のあるカメラで回転のブレ OFF の時は、注視点も同じだけ動く"),
        Param("roll", u"傾き (Z 回転) の強さ (度)", 0.5, minimum=0.0, step=0.1,
              tooltip=u"画面の傾き (ロール) の揺れの大きさ [度]。回転のブレ ON の時だけ"),
        Param("period", u"揺れの周期 (フレーム)", 24.0, minimum=1.0, step=5.0,
              tooltip=u"ゆっくりした揺れの、だいたいの周期。小さいほど速く揺れる"),
        Param("detail", u"細かい揺れ", 0.5, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"ゆっくりした揺れに重ねる、3 倍くらい速い細かい揺れの割合 (0 でなめらか)"),
        Param("rotation_distance", u"回転の基準距離 (カメラ以外)", 5.0, minimum=0.001, step=1.0,
              tooltip=u"カメラ以外のオブジェクトの回転の揺れの大きさ。この距離だけ先の点が\n"
                      u"「強さ」ぶんずれる角度で回る。カメラは「中心までの距離」を使う"),
        Param("seed", u"乱数シード", 1, minimum=0, maximum=99999,
              tooltip=u"変えると揺れ方が変わる"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return cs.create_shakes(cmds, mu, ctx, v, KIND)


class HandShakeUpdateTool(BaseTool):
    label = u"手ブレを更新"
    tab = TAB
    category = CATEGORY
    order = 680
    tooltip = (u"選択した手ブレを作り直します (パラメータはすべてすぐ反映されるので、\n"
               u"ふだんは使わなくてよい。古いバージョンで作った手ブレを今の仕組みにする時などに)。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return cs.rebuild_effects(cmds, mu, ctx,
                                  cs._selected_roots(cmds, mu, u"更新する", EFFECT_TYPE))


class HandShakeRemoveTool(BaseTool):
    label = u"手ブレを外す"
    tab = TAB
    category = CATEGORY
    order = 686
    tooltip = (u"選択した手ブレを削除し、オブジェクト (と注視点) を元に戻します。\n"
               u"揺らしているオブジェクトを選択しても、その手ブレが対象になります。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = cs._selected_roots(cmds, mu, u"外す", EFFECT_TYPE)
        for root in roots:
            cs.release(cmds, mu, root)
            ctx.log(u"  %s を外しました" % cs._short(root))
            cmds.delete(root)
        return len(roots)


# ---------------------------------------------------------------- 構築
def stored_params():
    by_name = dict((p.name, p) for p in HandShakeTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def build_effect(cmds, mu, root, v, target, aim):
    """エクスプレッションを作る (作成時と更新時で共通)。"""
    shape = cs.camera_shape(cmds, target)
    con = cs.find_aim_constraint(cmds, target) if aim else None
    if con and not cmds.attributeQuery(cs.BASE_TWIST_ATTR, node=root, exists=True):
        cmds.addAttr(root, longName=cs.BASE_TWIST_ATTR, attributeType="double")
        cmds.setAttr(root + "." + cs.BASE_TWIST_ATTR, cmds.getAttr(con + ".twist"))
    expr = cmds.expression(
        name=cs._short(root) + "_handShake", alwaysEvaluate=True, unitConversion="all",
        string=wave_expression(root) + cs.apply_expression(root, target, shape, aim, roll=True,
                                                            con=con))
    mu.tag(expr)
    mu.own(root, expr)
    return expr


def wave_expression(root):
    """$dx $dy $ax $ay $roll $on を、なめらかな乱数の波で計算する部分。"""
    r = cs._short(root)
    lines = [u"float $kt = frame / max({r}.dfx_period, 1);".format(r=r),
             u"float $det = {r}.dfx_detail;".format(r=r),
             u"float $sd = {r}.dfx_seed * 17.31;".format(r=r),
             u"float $w[];"]
    for i, off in enumerate(CHANNEL_OFFSETS):
        lines.append(u"$w[{i}] = (noise($kt + $sd + {o}) + $det * noise($kt * 3.1 + $sd + {o2}))"
                     u" / (1 + $det);".format(i=i, o=repr(off), o2=repr(off + 53.7)))
    lines += [u"float $on = {r}.dfx_rotation;".format(r=r),
              u"float $dx = {r}.dfx_strength_x * $w[0];".format(r=r),
              u"float $dy = {r}.dfx_strength_y * $w[1];".format(r=r),
              u"float $dz = {r}.dfx_strength_z * $w[5];".format(r=r),
              u"float $ax = {r}.dfx_strength_x * $w[2];".format(r=r),
              u"float $ay = {r}.dfx_strength_y * $w[3];".format(r=r),
              u"float $roll = $on * {r}.dfx_roll * $w[4];".format(r=r)]
    return u"\n" + u"\n".join(lines) + u"\n"


KIND = cs.ShakeKind(EFFECT_TYPE, "dfx_handShake#", u"手ブレ", stored_params, LIVE_PARAMS,
                    build_effect)
