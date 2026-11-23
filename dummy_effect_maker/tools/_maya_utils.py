# -*- coding: utf-8 -*-
"""Maya 用ツールの共通処理。

ファイル名が "_" で始まるのでツールとしては登録されない。
maya.cmds を import するため、ツール側では run() の中で import すること
(スタンドアロンでもツール一覧の読み込み自体は成功させるため)。
"""
from __future__ import absolute_import

import os

from maya import cmds

from ..core import ToolError
from ..core import settings

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
    """移動・回転・スケールをロックし、チャンネルボックスからも隠す。"""
    for attr in ("t", "r", "s"):
        for axis in "xyz":
            plug = "%s.%s%s" % (node, attr, axis)
            cmds.setAttr(plug, lock=True)
            cmds.setAttr(plug, keyable=False, channelBox=False)


# ------------------------------------------------- 作成後に調整できるエフェクト
# エフェクトのルートには種類 (EFFECT_TYPE_ATTR)、パラメータ (PARAM_PREFIX + 名前)、
# 自分が作ったノード (OWNED_ATTR へのメッセージ接続) を持たせる。
# パラメータはチャンネルボックスで編集でき、作り直しはルートの値から行う。
EFFECT_TYPE_ATTR = "dfxEffectType"
OWNED_ATTR = "dfxOwned"
PARAM_PREFIX = "dfx_"
REBUILD_SUFFIX = u" (更新)"


def param_plug(node, param):
    return "%s.%s%s" % (node, PARAM_PREFIX, param.name)


def mark_effect_root(node, effect_type):
    cmds.addAttr(node, longName=EFFECT_TYPE_ATTR, dataType="string")
    cmds.setAttr(node + "." + EFFECT_TYPE_ATTR, effect_type, type="string")
    cmds.addAttr(node, longName=OWNED_ATTR, attributeType="message",
                 multi=True, indexMatters=False)


def add_param_attrs(node, params, values, live_names):
    """params をアトリビュートとして node に追加し、values を設定する。

    live_names に含まれるものはキー設定可能 (変更が即反映される前提)、
    それ以外はチャンネルボックス表示のみで、表示名に「(更新)」を付ける。
    """
    for p in params:
        live = p.name in live_names
        long_name = PARAM_PREFIX + p.name
        nice = p.label if live else p.label + REBUILD_SUFFIX
        plug = param_plug(node, p)
        value = values[p.name]
        if p.kind == "color":
            cmds.addAttr(node, longName=long_name, niceName=nice,
                         attributeType="float3", usedAsColor=True)
            for c in "RGB":
                cmds.addAttr(node, longName=long_name + c, niceName=nice + " " + c,
                             attributeType="float", parent=long_name)
            cmds.setAttr(plug, *value, type="float3")
            for c in "RGB":
                _show_in_channel_box(plug + c, live)
            continue

        kw = {}
        if p.kind in ("int", "float"):
            kw["attributeType"] = "long" if p.kind == "int" else "double"
            if p.minimum is not None:
                kw["minValue"] = p.minimum
            if p.maximum is not None:
                kw["maxValue"] = p.maximum
        elif p.kind == "bool":
            kw["attributeType"] = "bool"
        elif p.kind == "choice":
            kw["attributeType"] = "enum"
            kw["enumName"] = ":".join(p.choices)
        else:  # str
            kw["dataType"] = "string"
        cmds.addAttr(node, longName=long_name, niceName=nice, **kw)

        if p.kind == "choice":
            cmds.setAttr(plug, p.choices.index(value))
        elif p.kind == "str":
            cmds.setAttr(plug, value, type="string")
        else:
            cmds.setAttr(plug, value)
        if p.kind != "str":
            _show_in_channel_box(plug, live)


def _show_in_channel_box(plug, keyable):
    if keyable:
        cmds.setAttr(plug, keyable=True)
    else:
        cmds.setAttr(plug, keyable=False, channelBox=True)


def read_param_values(node, params):
    """add_param_attrs で保存した値を {name: value} で返す。

    アトリビュートが無いもの (後から追加されたパラメータ) は既定値を使う。
    """
    values = {}
    for p in params:
        plug = param_plug(node, p)
        if not cmds.attributeQuery(PARAM_PREFIX + p.name, node=node, exists=True):
            values[p.name] = p.default
            continue
        if p.kind == "color":
            value = cmds.getAttr(plug)[0]
        elif p.kind == "choice":
            value = cmds.getAttr(plug, asString=True)
        elif p.kind == "str":
            value = cmds.getAttr(plug) or ""
        else:
            value = cmds.getAttr(plug)
        values[p.name] = p.coerce(value)
    return values


def write_param_values(node, params, values, skip=()):
    """values の値を node のアトリビュートに書き込む (add_param_attrs の逆)。

    values に無いもの、skip に含まれるもの、アトリビュートが無いものは触らない。
    """
    for p in params:
        if p.name not in values or p.name in skip:
            continue
        if not cmds.attributeQuery(PARAM_PREFIX + p.name, node=node, exists=True):
            continue
        plug = param_plug(node, p)
        value = values[p.name]
        if p.kind == "color":
            cmds.setAttr(plug, *value, type="float3")
        elif p.kind == "choice":
            cmds.setAttr(plug, p.choices.index(value))
        elif p.kind == "str":
            cmds.setAttr(plug, value, type="string")
        else:
            cmds.setAttr(plug, value)


def driven_params(node, params):
    """キーや接続で値が決まっていて、書き込んでも効かないパラメータ名。"""
    names = []
    for p in params:
        if not cmds.attributeQuery(PARAM_PREFIX + p.name, node=node, exists=True):
            continue
        plug = param_plug(node, p)
        plugs = [plug] + ([plug + c for c in "RGB"] if p.kind == "color" else [])
        if any(cmds.listConnections(x, source=True, destination=False) for x in plugs):
            names.append(p.name)
    return names


def default_preset_dir(effect_type):
    """環境設定が空のときのプリセット保存先。例: ~/Documents/maya/DummyEffectMaker/presets/homing"""
    return os.path.join(cmds.internalVar(userAppDir=True), "DummyEffectMaker",
                        "presets", effect_type)


def preset_dir(effect_type):
    """プリセットの保存先 (環境設定 → 無ければ既定)。フォルダが無ければ作る。"""
    path = settings.get("preset_dir") or default_preset_dir(effect_type)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def ask_file(save, directory, caption, file_filter):
    """Maya のファイルダイアログ。キャンセルなら None。"""
    result = cmds.fileDialog2(fileFilter=file_filter, dialogStyle=2,
                              fileMode=0 if save else 1, caption=caption,
                              startingDirectory=directory)
    return result[0] if result else None


def save_preset_dialog(ctx, roots, effect_type, params, caption, noun):
    """roots (1 つだけ) のパラメータをダイアログで選んだファイルに保存する。"""
    from ..core import presets
    if len(roots) > 1:
        raise ToolError(u"保存する%sを 1 つだけ選択してください (現在 %d 個)" % (noun, len(roots)))
    root = roots[0]
    path = ask_file(True, preset_dir(effect_type), caption, presets.FILE_FILTER)
    if not path:
        ctx.log(u"  キャンセルしました")
        return None
    if not path.lower().endswith(".json"):
        path += ".json"
    presets.save_preset(path, effect_type, params, read_param_values(root, params))
    ctx.log(u"  %s のパラメータを保存しました: %s" % (root.split("|")[-1], path))
    return path


def load_preset_dialog(ctx, effect_type, params, caption):
    """ダイアログで選んだプリセットの値を返す。キャンセルなら None。"""
    from ..core import presets
    path = ask_file(False, preset_dir(effect_type), caption, presets.FILE_FILTER)
    if not path:
        ctx.log(u"  キャンセルしました")
        return None
    values = presets.load_preset(path, effect_type, params)
    ctx.log(u"  読み込み: %s (%d 項目)" % (path, len(values)))
    return values


def sync_param_attrs(node, params, values, live_names, keep=()):
    """古いバージョンで作ったルートのアトリビュートを今の params に合わせる。

    params に無くなった PARAM_PREFIX 付きアトリビュートは削除し (keep は残す)、
    足りないものは values の値で追加する。
    """
    known = set(PARAM_PREFIX + p.name for p in params) | set(keep)
    for attr in cmds.listAttr(node, userDefined=True) or []:
        if not attr.startswith(PARAM_PREFIX) or attr in known:
            continue
        if not cmds.attributeQuery(attr, node=node, exists=True):
            continue  # 親と一緒に削除済み
        if cmds.attributeQuery(attr, node=node, listParent=True):
            continue  # dfx_colorR などの子は親で判断する
        cmds.deleteAttr(node, attribute=attr)
    missing = [p for p in params
               if not cmds.attributeQuery(PARAM_PREFIX + p.name, node=node, exists=True)]
    add_param_attrs(node, missing, values, live_names)


def own(root, *nodes):
    """nodes を root のエフェクトが作ったノードとして登録する (作り直し時に削除)。"""
    for node in nodes:
        cmds.connectAttr(node + ".message", root + "." + OWNED_ATTR,
                         nextAvailable=True)


def delete_owned(root):
    nodes = cmds.listConnections(root + "." + OWNED_ATTR, source=True,
                                 destination=False) or []
    nodes = cmds.ls(nodes, long=True) or []
    dag = [n for n in nodes if cmds.objectType(n, isAType="dagNode")]
    if dag:
        cmds.delete(dag)
    rest = [n for n in nodes if cmds.objExists(n)]
    if rest:
        cmds.delete(rest)


def selected_effect_roots(effect_type=None):
    """選択中のノード (またはその親) のうち effect_type のルートを返す。None なら全種類。"""
    roots = []
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        parts = node.split("|")
        for i in range(len(parts), 1, -1):
            candidate = "|".join(parts[:i])
            if (cmds.attributeQuery(EFFECT_TYPE_ATTR, node=candidate, exists=True)
                    and (effect_type is None or
                         cmds.getAttr(candidate + "." + EFFECT_TYPE_ATTR) == effect_type)):
                if candidate not in roots:
                    roots.append(candidate)
                break
    return roots


def all_effect_roots():
    return cmds.ls("*." + EFFECT_TYPE_ATTR, objectsOnly=True, recursive=True,
                   long=True) or []


# ------------------------------------------------------------------ ベイク
BAKED_ATTR = "dfxBaked"


def is_baked(root):
    return (cmds.attributeQuery(BAKED_ATTR, node=root, exists=True)
            and cmds.getAttr(root + "." + BAKED_ATTR))


def mark_baked(root):
    if not cmds.attributeQuery(BAKED_ATTR, node=root, exists=True):
        cmds.addAttr(root, longName=BAKED_ATTR, attributeType="bool")
    cmds.setAttr(root + "." + BAKED_ATTR, True)


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


def premultiply(color_plug, opacity_plug, name, owned):
    """色 × 不透明度 (blendColors) の出力プラグ。作ったノードは owned に足す。

    surfaceShader の outColor は不透明度を掛けた色 (乗算済み) にする決まり。掛けないと、
    半透明の所で後ろの色に足されて明るくなる (白っぽく光る)。テクスチャ (file) の色は元から乗算済み。
    """
    blend = cmds.createNode("blendColors", name=name)
    cmds.connectAttr(color_plug, blend + ".color1")
    cmds.setAttr(blend + ".color2", 0, 0, 0, type="double3")
    cmds.connectAttr(opacity_plug, blend + ".blender")
    tag(blend)
    owned.append(blend)
    return blend + ".output"


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


def mel_find_node(var, path, fallback_plug, suffix=""):
    """エクスプレッション (MEL) で、ノードの名前を文字列変数 var に入れるコード。

    作った時の名前 path で探し、無ければ (名前や親を変えた時) fallback_plug
    (ルートのメッセージアトリビュート) につながっているノード + suffix を使う。
    エクスプレッションの中では ls を使わない (外側の ls の途中で評価されると、その結果が壊れる)。
    """
    return (u'string %s = "%s";\n'
            u'if (!`objExists %s`) {\n'
            u'    string $found[] = `listConnections -source 1 -destination 0 "%s"`;\n'
            u'    if (size($found) > 0) %s = $found[0] + "%s";\n'
            u'}\n') % (var, path, var, fallback_plug, var, suffix)


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
