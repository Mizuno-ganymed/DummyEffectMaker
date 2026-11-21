# -*- coding: utf-8 -*-
"""Maya 用ツールの共通処理。

ファイル名が "_" で始まるのでツールとしては登録されない。
maya.cmds を import するため、ツール側では run() の中で import すること
(スタンドアロンでもツール一覧の読み込み自体は成功させるため)。
"""
from __future__ import absolute_import

from maya import cmds

from ..core import ToolError

# このツール群が作ったノードに付ける目印。一括削除に使う
TAG_ATTR = "dfxDummy"


def tag(*nodes):
    for node in nodes:
        if not cmds.attributeQuery(TAG_ATTR, node=node, exists=True):
            cmds.addAttr(node, longName=TAG_ATTR, attributeType="bool",
                         defaultValue=True)


def list_tagged():
    return cmds.ls("*." + TAG_ATTR, objectsOnly=True, recursive=True,
                   long=True) or []


def lock_transform(node):
    for attr in ("t", "r", "s"):
        for axis in "xyz":
            cmds.setAttr("%s.%s%s" % (node, attr, axis), lock=True)


def create_surface_shader(name, color, transparency=0.0):
    """ライティングの影響を受けない surfaceShader を作る。(shader, shadingGroup)"""
    shader = cmds.shadingNode("surfaceShader", asShader=True, name=name)
    cmds.setAttr(shader + ".outColor", *color, type="double3")
    t = float(transparency)
    cmds.setAttr(shader + ".outTransparency", t, t, t, type="double3")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                   name=shader + "SG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    tag(shader, sg)
    return shader, sg


def resolve_transforms(names, count, label="ロケータ"):
    """names が全て空なら選択順で、全て指定されていればその名前で transform を返す。"""
    names = [n.strip() for n in names]
    if all(names):
        result = []
        for n in names:
            found = cmds.ls(n, type="transform", long=True)
            if not found:
                raise ToolError(u"%s '%s' が見つかりません" % (label, n))
            if len(found) > 1:
                raise ToolError(u"名前 '%s' に一致するノードが複数あります" % n)
            result.append(found[0])
        return result
    if any(names):
        raise ToolError(u"%sは全て名前で指定するか、全て空欄 (選択順) にしてください" % label)

    selection = []
    for node in cmds.ls(orderedSelection=True, long=True) or []:
        if not cmds.objectType(node, isAType="transform"):
            # シェイプが選択されていたら親の transform を使う
            parents = cmds.listRelatives(node, parent=True, fullPath=True)
            if not parents:
                continue
            node = parents[0]
        if node not in selection:
            selection.append(node)
    if len(selection) != count:
        raise ToolError(u"%sを %d 個、順番に選択してください (現在 %d 個)"
                        % (label, count, len(selection)))
    return selection


def world_position_plug(node, created):
    """node のワールド座標を出力するプラグを返す。

    ロケータならシェイプの worldPosition、それ以外は decomposeMatrix を作る。
    作ったノードは created に追加する。
    """
    shapes = cmds.listRelatives(node, shapes=True, type="locator",
                                fullPath=True) or []
    if shapes:
        return shapes[0] + ".worldPosition[0]"
    try:
        cmds.loadPlugin("matrixNodes", quiet=True)
    except RuntimeError:
        pass
    short = node.split("|")[-1]
    dm = cmds.createNode("decomposeMatrix", name=short + "_dfxWorldPos")
    cmds.connectAttr(node + ".worldMatrix[0]", dm + ".inputMatrix")
    created.append(dm)
    return dm + ".outputTranslate"
