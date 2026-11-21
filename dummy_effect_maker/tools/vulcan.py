# -*- coding: utf-8 -*-
"""バルカン砲: ロケータ A → B (経由) → C へ弾を一定間隔で撃ち続ける。

構成:
    dfx_vulcanN                  ルート (移動ロック)
      dfx_vulcanN_path           軌道カーブ (A/B/C にライブ接続。テンプレート表示)
      dfx_vulcanN_bulletSrc      弾の元メッシュ (非表示。インスタンス元)
      dfx_vulcanN_bullets
        dfx_vulcanN_bullet_001   モーションパスで飛ぶ弾
        ...

軌道カーブはロケータに接続されているので、作成後にロケータを動かすと
弾道も追従する。弾は等速 (弧長基準) で飛び、発射フレームに出現し、
C に到達した次のフレームで消える。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param, ToolError

PATH_SMOOTH = u"なめらか"
PATH_LINEAR = u"折れ線"
SHAPE_SPHERE = u"球体"
SHAPE_SQUASHED = u"つぶれた球体"
# 弾はモーションパスの follow で X 軸が進行方向、Y 軸が上を向く
SQUASH_AXES = [(u"進行方向", "x"), (u"上下", "y"), (u"左右", "z")]

MAX_BULLETS = 2000


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


# ---------------------------------------------------------------- ツール
class VulcanTool(BaseTool):
    label = u"バルカン砲"
    category = u"射撃"
    order = 10
    tooltip = (u"ロケータ A から B を経由して C まで、弾 (球体 / つぶれた球体) を"
               u"指定フレームごとに撃ち続けます。\n"
               u"ロケータ名が空欄なら A→B→C の順に選択したものを使います。")
    params = [
        Param("locator_a", u"ロケータA (発射位置)", "",
              tooltip=u"3 つとも空欄なら選択順 (A→B→C) を使用"),
        Param("locator_b", u"ロケータB (経由点)", ""),
        Param("locator_c", u"ロケータC (着弾点)", ""),
        Param("path_type", u"軌道", PATH_SMOOTH, kind="choice",
              choices=[PATH_SMOOTH, PATH_LINEAR],
              tooltip=u"なめらか: B を通る曲線 / 折れ線: A-B-C を直線で結ぶ"),
        Param("start_frame", u"発射開始フレーム", 1.0, decimals=2),
        Param("end_frame", u"発射終了フレーム", 48.0, decimals=2),
        Param("interval", u"発射間隔 (フレーム)", 2.0, minimum=0.1, step=0.5,
              decimals=2, tooltip=u"何フレームごとに 1 発撃つか (小数可)"),
        Param("travel_frames", u"着弾までのフレーム数", 8.0, minimum=0.1,
              step=1.0, decimals=2, tooltip=u"1 発が A から C に到達するまでの時間"),
        Param("shape", u"弾の形状", SHAPE_SPHERE, kind="choice",
              choices=[SHAPE_SPHERE, SHAPE_SQUASHED]),
        Param("radius", u"弾の半径", 0.1, minimum=0.001, step=0.05),
        Param("squash", u"つぶれ率", 0.4, minimum=0.01, maximum=10.0, step=0.1,
              tooltip=u"つぶれた球体の時のみ。1 未満でつぶれ、1 より大きいと伸びる"),
        Param("squash_axis", u"つぶれ方向", SQUASH_AXES[0][0], kind="choice",
              choices=[label for label, _ in SQUASH_AXES]),
        Param("subdivisions", u"球の分割数", 12, minimum=4, maximum=64),
        Param("color", u"色", (1.0, 0.75, 0.3), kind="color"),
        Param("use_instance", u"インスタンスで作成", True,
              tooltip=u"ON: 弾は元メッシュのインスタンス (軽い。元メッシュを"
                      u"編集すると全弾に反映)"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        if v["end_frame"] < v["start_frame"]:
            raise ToolError(u"発射終了フレームが開始フレームより前になっています")
        frames = compute_fire_frames(v["start_frame"], v["end_frame"],
                                     v["interval"])
        if len(frames) > MAX_BULLETS:
            raise ToolError(u"弾数が多すぎます (%d 発 / 上限 %d)。発射間隔を確認してください"
                            % (len(frames), MAX_BULLETS))

        locators = mu.resolve_transforms(
            [v["locator_a"], v["locator_b"], v["locator_c"]], 3)
        if len(set(locators)) != 3:
            raise ToolError(u"A, B, C には別々のロケータを指定してください")

        created = []  # 一括削除用にタグを付けるノード
        root = cmds.group(empty=True, name="dfx_vulcan#")
        prefix = root
        mu.tag(root)

        # --- 軌道カーブ
        plugs = [mu.world_position_plug(n, created) for n in locators]
        curve = self._build_path(cmds, plugs, v["path_type"] == PATH_SMOOTH,
                                 prefix, root, created)

        # --- 弾の元メッシュとマテリアル
        _, sg = mu.create_surface_shader(prefix + "_mat", v["color"])
        src = self._build_bullet_source(cmds, v, prefix, root)
        cmds.sets(src, edit=True, forceElement=sg)

        # --- 弾
        bullets_grp = cmds.group(empty=True, name=prefix + "_bullets",
                                 parent=root)
        travel = v["travel_frames"]
        bullets = []
        for i, fire in enumerate(frames):
            name = "%s_bullet_%03d" % (prefix, i + 1)
            if v["use_instance"]:
                bullet = cmds.instance(src, name=name)[0]
            else:
                bullet = cmds.duplicate(src, name=name)[0]
            bullet = cmds.parent(bullet, bullets_grp)[0]
            bullets.append(bullet)

            motion_path = cmds.pathAnimation(
                bullet, curve=curve, fractionMode=True, follow=True,
                followAxis="x", upAxis="y", worldUpType="vector",
                worldUpVector=(0, 1, 0),
                startTimeU=fire, endTimeU=fire + travel)
            cmds.keyTangent(motion_path, attribute="uValue",
                            inTangentType="linear", outTangentType="linear")
            created.append(motion_path)

            # 発射フレームで出現し、着弾の次のフレームで消える
            for t, vis in ((fire - 1, 0), (fire, 1), (fire + travel + 1, 0)):
                cmds.setKeyframe(bullet, attribute="visibility", time=t,
                                 value=vis)
            cmds.keyTangent(bullet, attribute="visibility",
                            outTangentType="step")

        # インスタンスごとにマテリアルを確実に割り当てる
        cmds.sets(bullets, edit=True, forceElement=sg)
        cmds.setAttr(src + ".visibility", 0)

        mu.tag(*created)
        mu.lock_transform(root)
        cmds.select(root)
        ctx.log(u"  %s: %d 発 (フレーム %s 〜 %s, %s フレーム間隔)"
                % (root, len(frames), frames[0], frames[-1], v["interval"]))
        ctx.log(u"  軌道: %s" % u" → ".join(n.split("|")[-1] for n in locators))
        return root

    # ------------------------------------------------------------------
    @staticmethod
    def _build_path(cmds, plugs, smooth, prefix, root, created):
        a, b, c = [cmds.getAttr(p)[0] for p in plugs]
        if smooth:
            mid = bezier_control_through(a, b, c)
            curve = cmds.curve(degree=2, point=[a, mid, c], knot=[0, 0, 1, 1],
                               name=prefix + "_path")
        else:
            curve = cmds.curve(degree=1, point=[a, b, c], knot=[0, 1, 2],
                               name=prefix + "_path")
        curve = cmds.parent(curve, root)[0]
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
            created.extend([sum_ac, half_ac, double_b, mid_cv])
        else:
            cmds.connectAttr(plugs[1], shape + ".controlPoints[1]")
        return curve

    @staticmethod
    def _build_bullet_source(cmds, v, prefix, root):
        div = v["subdivisions"]
        src = cmds.polySphere(radius=v["radius"], subdivisionsAxis=div,
                              subdivisionsHeight=max(2, div // 2),
                              constructionHistory=False,
                              name=prefix + "_bulletSrc")[0]
        if v["shape"] == SHAPE_SQUASHED:
            axis = dict(SQUASH_AXES)[v["squash_axis"]]
            scale = [1.0, 1.0, 1.0]
            scale["xyz".index(axis)] = v["squash"]
            cmds.setAttr(src + ".scale", *scale, type="double3")
            # インスタンスでも形状が共有されるよう頂点に焼き込む
            cmds.makeIdentity(src, apply=True, scale=True)
        return cmds.parent(src, root)[0]


class VulcanLocatorsTool(BaseTool):
    label = u"バルカン用ロケータ作成"
    category = u"射撃"
    order = 20
    tooltip = (u"A (発射位置) / B (経由点) / C (着弾点) のロケータを作成し、"
               u"A→B→C の順に選択した状態にします。")
    params = [
        Param("length", u"A〜C の距離", 10.0, minimum=0.01),
        Param("height", u"B の高さ", 2.0),
    ]

    def run(self, ctx, **v):
        from maya import cmds

        length, height = v["length"], v["height"]
        specs = [("A", (0.0, 0.0, 0.0)),
                 ("B", (length * 0.5, height, 0.0)),
                 ("C", (length, 0.0, 0.0))]
        locs = []
        for suffix, pos in specs:
            loc = cmds.spaceLocator(name="vulcan_loc%s#" % suffix)[0]
            cmds.xform(loc, worldSpace=True, translation=pos)
            locs.append(loc)
        cmds.select(locs, replace=True)
        ctx.log(u"  作成: %s" % ", ".join(locs))
        return locs
