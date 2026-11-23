# -*- coding: utf-8 -*-
"""ダミーエフェクトを毎フレームのキーにベイクする (FBX で他のツールへ持っていく用)。

モーションパスやノード接続で動いているもの (弾の移動・回転・大きさ・表示) を
フレームごとのキーに置き換え、動きを作っていた補助ノードと履歴を削除する。
ベイク後はキーだけで動くので、FBX に書き出せば他のツールでも同じ動きになる。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param, ToolError

TARGET_SELECTED = u"選択したエフェクト"
TARGET_ALL = u"シーン内のすべてのエフェクト"
RANGE_TIMELINE = u"タイムスライダーの範囲"
RANGE_CUSTOM = u"指定した範囲"

BAKE_ATTRS = ("translateX", "translateY", "translateZ",
              "rotateX", "rotateY", "rotateZ",
              "scaleX", "scaleY", "scaleZ", "visibility")
# トランスフォーム以外で、エクスプレッションが動かしていればベイクするノードの種類
EXPRESSION_BAKE_NODE_TYPES = ("surfaceShader", "lattice", "file", "place2dTexture", "ramp")
# ベイク後に不要になる (動きを作っていた) ノードの種類
RIG_NODE_TYPES = ("motionPath", "condition", "multiplyDivide", "plusMinusAverage",
                  "decomposeMatrix", "polySphere", "parentConstraint",
                  "expression", "multMatrix")


class BakeTool(BaseTool):
    label = u"ベイク"
    menu = u"シーン"
    category = u"シーン"
    order = 500
    tooltip = (u"作成したエフェクトの動きを毎フレームのキーにベイクします。\n"
               u"FBX に書き出して他のツールへ持っていくときに使います。\n"
               u"ベイク後はパラメータの更新・読み込みはできません (Ctrl+Z で戻せます)。")
    params = [
        Param("target", u"対象", TARGET_SELECTED, kind="choice",
              choices=[TARGET_SELECTED, TARGET_ALL],
              tooltip=u"選択したエフェクト: ルートか、その中のロケータや弾を選択"),
        Param("range_mode", u"フレーム範囲", RANGE_TIMELINE, kind="choice",
              choices=[RANGE_TIMELINE, RANGE_CUSTOM]),
        Param("start_frame", u"開始フレーム", 1.0, decimals=2,
              tooltip=u"「指定した範囲」の時のみ"),
        Param("end_frame", u"終了フレーム", 120.0, decimals=2,
              tooltip=u"「指定した範囲」の時のみ"),
        Param("hide_by_scale", u"非表示の間はスケール 0", False,
              tooltip=u"表示/非表示のアニメーションを読まないツール (多くのゲームエンジン) 向け。\n"
                      u"非表示のフレームはスケールを 0 にして見えなくする。\n"
                      u"表示/非表示のキーもそのまま残る"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        if v["target"] == TARGET_ALL:
            roots = mu.all_effect_roots()
            if not roots:
                raise ToolError(u"シーンにベイクできるエフェクトがありません")
        else:
            from . import camera_shake, ghost_blur
            roots = mu.selected_effect_roots()
            # 画ぶれ・お化けブラーは、付けたオブジェクト (カメラなど) を選んでもよい
            roots += [r for r in camera_shake.roots_of_selected_targets(cmds, mu) if r not in roots]
            roots += [r for r in ghost_blur.roots_of_selected_targets(cmds, mu) if r not in roots]
            if not roots:
                raise ToolError(u"ベイクするエフェクト (dfx_homingN など、またはその中の"
                                u"ロケータや弾) を選択してください")
        todo = [r for r in roots if not mu.is_baked(r)]
        for root in roots:
            if root not in todo:
                ctx.log(u"  %s はベイク済みなので飛ばします" % _short(root))
        if not todo:
            raise ToolError(u"選択したエフェクトはすべてベイク済みです")

        if v["range_mode"] == RANGE_TIMELINE:
            start = cmds.playbackOptions(query=True, minTime=True)
            end = cmds.playbackOptions(query=True, maxTime=True)
        else:
            start, end = v["start_frame"], v["end_frame"]
        if end < start:
            raise ToolError(u"終了フレームが開始フレームより前になっています")

        from . import camera_shake
        # 画ぶれ・手ブレはカメラ側のキーにする (専用の処理)
        shakes = [r for r in todo
                  if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in camera_shake.SHAKE_TYPES]
        for root in shakes:
            count = camera_shake.bake_effect(cmds, mu, root, start, end)
            mu.mark_baked(root)
            ctx.log(u"  %s: カメラの translate / rotate にベイクしました (フレーム %s 〜 %s / %d キー)"
                    % (_short(root), start, end, count))
        todo = [r for r in todo if r not in shakes]
        if not todo:
            cmds.select(shakes)
            return shakes

        plugs = []
        for root in todo:
            plugs.extend(driven_plugs(cmds, root))
        hidden_nodes = []
        if v["hide_by_scale"]:
            # 表示が動くものはスケールもベイクして、非表示のフレームを 0 にする
            hidden_nodes = sorted(set(p.rsplit(".", 1)[0] for p in plugs
                                      if p.endswith(".visibility")))
            for node in hidden_nodes:
                for axis in "XYZ":
                    plug = "%s.scale%s" % (node, axis)
                    if plug not in plugs:
                        plugs.append(plug)
        if plugs:
            cmds.bakeResults(plugs, time=(start, end), sampleBy=1.0,
                             simulation=True, disableImplicitControl=True,
                             preserveOutsideKeys=False, sparseAnimCurveBake=False,
                             minimizeRotation=True)
        # 爆発で隠した弾: 弾の visibility も一緒にベイクした時は、visibility のキーにまとめる
        baked_vis = set(cmds.ls(p.rsplit(".", 1)[0], long=True)[0] for p in plugs
                        if p.endswith(".visibility") and cmds.objExists(p))
        for node in sorted(set(p.rsplit(".", 1)[0] for p in plugs if p.endswith(".lodVisibility"))):
            if cmds.objExists(node) and cmds.ls(node, long=True)[0] in baked_vis:
                fold_lod_visibility(cmds, node)
        if hidden_nodes:
            zeroed = sum(zero_scale_while_hidden(cmds, n) for n in hidden_nodes)
            ctx.log(u"  非表示の間のスケールを 0 にしました (%d 個 / のべ %d フレーム)"
                    % (len(hidden_nodes), zeroed))

        for root in todo:
            removed = cleanup_after_bake(cmds, mu, root)
            mu.mark_baked(root)
            ctx.log(u"  %s: ベイクしました (フレーム %s 〜 %s / 補助ノード %d 個を削除)"
                    % (_short(root), start, end, removed))
            if cmds.getAttr(root + "." + mu.EFFECT_TYPE_ATTR) == "smear":
                ctx.log(u"  ※ スミアはラティスの点のキーになります。ラティスは FBX では書き出せないため、"
                        u"他のツールへは Alembic (ジオメトリキャッシュ) で持っていってください")
        ctx.log(u"  ルートを選択して ファイル → 選択項目の書き出し (FBX) で書き出せます")
        cmds.select(todo + shakes)
        return todo + shakes


def _short(node):
    return node.split("|")[-1]


def driven_plugs(cmds, root):
    """root の下で、キーや接続で動いている transform のチャンネル。"""
    nodes = cmds.listRelatives(root, allDescendents=True, type="transform",
                               fullPath=True) or []
    plugs = []
    for node in nodes:
        for attr in BAKE_ATTRS:
            plug = "%s.%s" % (node, attr)
            # モーションパスなどは translate / rotate / scale (親) にまとめて接続される
            parent = "%s.%s" % (node, attr[:-1]) if attr[-1] in "XYZ" else None
            if (cmds.listConnections(plug, source=True, destination=False)
                    or (parent and cmds.listConnections(parent, source=True,
                                                        destination=False))):
                plugs.append(plug)
    # ブレンドシェイプのウェイト (衝撃波の太さなど) も動いていればベイクする
    for bs in _blend_shapes(cmds, root):
        for i in cmds.getAttr(bs + ".weight", multiIndices=True) or []:
            plug = "%s.weight[%d]" % (bs, i)
            if cmds.listConnections(plug, source=True, destination=False):
                plugs.append(plug)
    # エクスプレッションが動かしているマテリアルの色 (残像)、ラティスの点 (スミア)、
    # テクスチャの濃さ・流れ (天候)
    from . import _maya_utils as mu
    owned = cmds.listConnections(root + "." + mu.OWNED_ATTR, source=True,
                                 destination=False) or []
    for expr in cmds.ls(owned, type="expression") or []:
        for plug in cmds.listConnections(expr, source=False, destination=True, plugs=True,
                                         skipConversionNodes=True) or []:
            if (cmds.nodeType(plug.split(".")[0]) in EXPRESSION_BAKE_NODE_TYPES
                    or plug.endswith(".lodVisibility")) and plug not in plugs:
                # lodVisibility: 爆発が隠している、当たった弾 (別のルートの物)
                plugs.append(plug)
    return plugs


def fold_lod_visibility(cmds, node):
    """ベイクした lodVisibility (当たった弾を隠す) を visibility のキーにまとめる (FBX 用)。

    visibility もキーになっている時だけ。まとめたら lodVisibility のキーは消して 1 に戻す。
    """
    lod = node + ".lodVisibility"
    vis = node + ".visibility"
    times = cmds.keyframe(lod, query=True, timeChange=True) or []
    if not times or not cmds.listConnections(vis, source=True, destination=False,
                                             type="animCurve"):
        return False
    values = [cmds.getAttr(lod, time=t) for t in times]
    shown = [cmds.getAttr(vis, time=t) for t in times]
    cmds.cutKey(lod)
    cmds.setAttr(lod, True)
    for t, a, b in zip(times, values, shown):
        cmds.setKeyframe(vis, time=t, value=float(bool(a) and bool(b)))
    return True


def _blend_shapes(cmds, root):
    meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    found = []
    for mesh in meshes:
        for bs in cmds.ls(cmds.listHistory(mesh) or [], type="blendShape") or []:
            if bs not in found:
                found.append(bs)
    return found


def zero_scale_while_hidden(cmds, node):
    """ベイク済みの visibility が 0 のフレームで scale のキーを 0 にする。

    表示が切り替わる直前のキーは出力タンジェントをステップにして、
    フレームの間で大きさが徐々に変わらない (ぱっと出て、ぱっと消える) ようにする。
    戻り値は 0 にしたフレーム数。
    """
    times = cmds.keyframe(node + ".visibility", query=True, timeChange=True) or []
    values = cmds.keyframe(node + ".visibility", query=True, valueChange=True) or []
    hidden = [v < 0.5 for v in values]
    scale_plugs = ["%s.scale%s" % (node, axis) for axis in "XYZ"]
    count = 0
    for t, is_hidden in zip(times, hidden):
        if is_hidden:
            for plug in scale_plugs:
                cmds.setKeyframe(plug, time=t, value=0.0)
            count += 1
    for i in range(len(times) - 1):
        if hidden[i] != hidden[i + 1]:
            for plug in scale_plugs:
                cmds.keyTangent(plug, time=(times[i], times[i]),
                                outTangentType="step")
    return count


def _driven_values(cmds, nodes):
    """nodes が出力でつないでいる数値プラグの [(プラグ, 今の値)]。"""
    result = []
    if not nodes:
        return result
    for plug in cmds.listConnections(nodes, source=False, destination=True,
                                     plugs=True) or []:
        try:
            value = cmds.getAttr(plug)
        except (RuntimeError, ValueError):
            continue
        if isinstance(value, (bool, int, float)):
            result.append((plug, value))
    return result


def cleanup_after_bake(cmds, mu, root):
    """動きを作っていた補助ノードと履歴を消し、効かなくなったパラメータをロックする。"""
    removed = 0
    owned = cmds.listConnections(root + "." + mu.OWNED_ATTR, source=True,
                                 destination=False) or []
    rig = [n for n in cmds.ls(owned, long=True) or []
           if cmds.objExists(n) and cmds.nodeType(n) in RIG_NODE_TYPES]
    # 補助ノードがベイク対象以外 (集中線の先端の太さ = メッシュの頂点など) を動かしていたら、
    # 消すと値が初期値に戻ってしまうので、今の値を覚えておいて消したあとに戻す
    frozen = _driven_values(cmds, rig)

    # メッシュの履歴 (球の分割数など) を確定
    meshes = cmds.listRelatives(root, allDescendents=True, type="mesh",
                                fullPath=True) or []
    if meshes:
        transforms = sorted(set(cmds.listRelatives(meshes, parent=True, fullPath=True) or []))
        # ブレンドシェイプ (衝撃波の太さ) とスキン (軌跡) は FBX でも使えるので残す
        # (それ以外の履歴だけ確定)
        deformed = [t for t in transforms
                    if cmds.ls(cmds.listHistory(t) or [], type=["blendShape", "skinCluster"])]
        plain = [t for t in transforms if t not in deformed]
        if plain:
            cmds.delete(plain, constructionHistory=True)
        if deformed:
            cmds.bakePartialHistory(deformed, prePostDeformers=True)

    # ガイドのカーブがロケータに追従し続けないよう切り離す (形はそのまま)
    curves = cmds.listRelatives(root, allDescendents=True, type="nurbsCurve",
                                fullPath=True) or []
    for shape in curves:
        conns = cmds.listConnections(shape, source=True, destination=False,
                                     plugs=True, connections=True) or []
        for dst, src in zip(conns[0::2], conns[1::2]):
            cmds.disconnectAttr(src, dst)

    rig = [n for n in rig if cmds.objExists(n)]
    if rig:
        cmds.delete(rig)
        removed = len(rig)
    for plug, value in frozen:
        if (cmds.objExists(plug) and not cmds.getAttr(plug, lock=True)
                and not cmds.listConnections(plug, source=True, destination=False)):
            cmds.setAttr(plug, value)
    # 補助ノードを動かしていたカーブ (弾の進み具合など) が残っていれば消す
    leftovers = [n for n in cmds.ls(owned, long=True) or []
                 if cmds.objExists(n) and cmds.nodeType(n).startswith("animCurve")
                 and not cmds.listConnections(n, source=False, destination=True)]
    if leftovers:
        cmds.delete(leftovers)
        removed += len(leftovers)

    # どこにもつながっていないパラメータ (= 変えても効かない) をロック
    for attr in cmds.listAttr(root, userDefined=True) or []:
        if not attr.startswith(mu.PARAM_PREFIX) or attr.startswith("dfx_locator"):
            continue
        plug = root + "." + attr
        if cmds.attributeQuery(attr, node=root, listChildren=True):
            continue  # 色などの親は子で判断
        parent = cmds.attributeQuery(attr, node=root, listParent=True)
        check = plug if not parent else root + "." + parent[0]
        if not cmds.listConnections(check, source=False, destination=True):
            cmds.setAttr(plug, lock=True)
    return removed
