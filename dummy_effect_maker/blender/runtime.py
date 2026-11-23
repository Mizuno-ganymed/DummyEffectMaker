# -*- coding: utf-8 -*-
"""Blender 版の土台: エフェクトのルート・パラメータ・毎フレームの更新。

Maya 版のエクスプレッションの代わりに、フレームが変わるたび (と、ルートやロケータを動かしたり
パラメータを変えたりした時) に Python で位置などを計算し直す。計算は Maya 版と同じ関数を使う。

構成 (Maya 版と同じ考え方):
    dfx_vulcan1            ルート (Empty。パラメータをカスタムプロパティ dfx_* で持つ。移動ロック)
      dfx_vulcan1_muzzle   動かして決める物 (コントロール。Empty)
      dfx_vulcan1_bullet_001 ...  ツールが作る物 (dfx_owner でルートを指す。作り直す時に消す)
"""
from __future__ import absolute_import

import re

import bpy
from bpy.app.handlers import persistent
from mathutils import Euler, Matrix, Quaternion, Vector

TYPE_KEY = "dfxEffectType"
TAG_KEY = "dfxDummy"
OWNER_KEY = "dfx_owner"          # ツールが作った物 → ルート
CONTROL_KEY = "dfx_control_of"   # 動かして決める物 → ルート
ROLE_KEY = "dfx_role"
PARAM_PREFIX = "dfx_"
REBUILD_SUFFIX = u" (更新)"
COLLECTION = "DummyEffects"

UPDATERS = {}   # effect_type → update(root, frame)
_BUSY = [False]


# ---------------------------------------------------------------- ルートと持ち物
def register_updater(effect_type, fn):
    UPDATERS[effect_type] = fn


def collection():
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
        bpy.context.scene.collection.children.link(coll)
    return coll


def unique_name(base):
    """dfx_vulcan → dfx_vulcan1, dfx_vulcan2 ... (使われていない番号)。"""
    n = 1
    while bpy.data.objects.get("%s%d" % (base, n)):
        n += 1
    return "%s%d" % (base, n)


def new_empty(name, display="PLAIN_AXES", size=1.0, parent=None):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = display
    obj.empty_display_size = size
    collection().objects.link(obj)
    if parent is not None:
        obj.parent = parent
    obj[TAG_KEY] = True
    return obj


def new_root(effect_type, base):
    root = new_empty(unique_name(base), display="CUBE", size=0.2)
    root[TYPE_KEY] = effect_type
    root.lock_location = (True, True, True)
    root.lock_rotation = (True, True, True)
    root.lock_scale = (True, True, True)
    return root


def add_control(root, role, location=(0.0, 0.0, 0.0), display="SPHERE", size=0.3):
    obj = new_empty("%s_%s" % (root.name, role), display=display, size=size, parent=root)
    obj.location = location
    obj[CONTROL_KEY] = root
    obj[ROLE_KEY] = role
    return obj


def own(root, *ids):
    for idb in ids:
        idb[OWNER_KEY] = root
        idb[TAG_KEY] = True


def owned(root):
    """root が作った物 (オブジェクト・メッシュ・マテリアル)。"""
    out = []
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials):
        out += [idb for idb in coll if idb.get(OWNER_KEY) == root]
    return out


def controls(root):
    return [o for o in bpy.data.objects if o.get(CONTROL_KEY) == root]


def control(root, role):
    for o in controls(root):
        if o.get(ROLE_KEY) == role:
            return o
    return None


def delete_owned(root):
    for idb in owned(root):
        if isinstance(idb, bpy.types.Object):
            bpy.data.objects.remove(idb, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials):
        for idb in list(coll):
            if idb.get(OWNER_KEY) == root and idb.users == 0:
                coll.remove(idb)


def effect_roots():
    return [o for o in bpy.data.objects if o.get(TYPE_KEY)]


def root_of(obj):
    """obj (ルート・コントロール・作った物、またはその子) のルート。"""
    while obj is not None:
        if obj.get(TYPE_KEY):
            return obj
        for key in (CONTROL_KEY, OWNER_KEY):
            r = obj.get(key)
            if r is not None and getattr(r, "get", None) and r.get(TYPE_KEY):
                return r
        obj = obj.parent
    return None


def selected_roots(context, effect_types=None):
    roots = []
    for obj in list(context.selected_objects) + ([context.active_object] if context.active_object else []):
        r = root_of(obj)
        if r is not None and r not in roots and (effect_types is None or r[TYPE_KEY] in effect_types):
            roots.append(r)
    return roots


# ---------------------------------------------------------------- パラメータ (カスタムプロパティ)
def add_params(root, params, values, live):
    for p in params:
        key = PARAM_PREFIX + p.name
        value = values[p.name]
        if p.kind == "color":
            root[key] = [float(c) for c in value]
        elif p.kind in ("str", "file", "choice"):
            root[key] = u"%s" % value
        elif p.kind == "bool":
            root[key] = bool(value)
        elif p.kind == "int":
            root[key] = int(value)
        else:
            root[key] = float(value)
        ui = root.id_properties_ui(key)
        kw = {"description": (p.tooltip or p.label)}
        if p.kind in ("int", "float"):
            if p.minimum is not None:
                kw["min"] = p.minimum
                kw["soft_min"] = p.minimum
            if p.maximum is not None:
                kw["max"] = p.maximum
                kw["soft_max"] = p.maximum
        if p.kind == "color":
            kw.update(subtype="COLOR", min=0.0, max=1.0)
        try:
            ui.update(**kw)
        except TypeError:
            pass
    root["dfx_live"] = list(live)


def read_params(root, params):
    """ルートの値 (無い・壊れた値は既定値)。Maya 版の read_param_values と同じ。"""
    out = {}
    for p in params:
        key = PARAM_PREFIX + p.name
        if key not in root:
            out[p.name] = p.default
            continue
        value = root[key]
        if p.kind == "color":
            value = tuple(value)
        try:
            out[p.name] = p.coerce(value)
        except (TypeError, ValueError):
            out[p.name] = p.default
    return out


def write_params(root, params, values):
    for p in params:
        if p.name in values:
            v = values[p.name]
            root[PARAM_PREFIX + p.name] = list(v) if p.kind == "color" else v


def is_live(root, name):
    return name in list(root.get("dfx_live", []))


# ---------------------------------------------------------------- 時間を指定した位置
def _fcurves(obj):
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return []
    action = ad.action
    legacy = getattr(action, "fcurves", None)
    if legacy is not None and len(legacy):
        return list(legacy)
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(action, ad.action_slot)
        return list(bag.fcurves) if bag else []
    except Exception:
        return []


def matrix_basis_at(obj, frame):
    """obj のローカルの行列 (移動・回転・拡大縮小のキーを frame で読む)。"""
    loc, rot, scl = list(obj.location), list(obj.rotation_euler), list(obj.scale)
    quat = list(obj.rotation_quaternion)
    for fc in _fcurves(obj):
        i = fc.array_index
        if fc.data_path == "location":
            loc[i] = fc.evaluate(frame)
        elif fc.data_path == "rotation_euler":
            rot[i] = fc.evaluate(frame)
        elif fc.data_path == "scale":
            scl[i] = fc.evaluate(frame)
        elif fc.data_path == "rotation_quaternion":
            quat[i] = fc.evaluate(frame)
    if obj.rotation_mode == "QUATERNION":
        r = Quaternion(quat).to_matrix().to_4x4()
    elif obj.rotation_mode in ("AXIS_ANGLE",):
        r = obj.matrix_basis.to_3x3().normalized().to_4x4()
    else:
        r = Euler(rot, obj.rotation_mode).to_matrix().to_4x4()
    return Matrix.Translation(Vector(loc)) @ r @ Matrix.Diagonal(Vector(scl + [1.0]))


def matrix_world_at(obj, frame):
    """obj のワールドの行列 (親もたどる。コンストレイント・ドライバーは見ない)。"""
    m = matrix_basis_at(obj, frame)
    if obj.parent is not None:
        m = matrix_world_at(obj.parent, frame) @ obj.matrix_parent_inverse @ m
    return m


# ---------------------------------------------------------------- 毎フレームの更新
def current_frame(scene=None):
    scene = scene or bpy.context.scene
    return scene.frame_current + scene.frame_subframe


def update_root(root, frame=None):
    fn = UPDATERS.get(root.get(TYPE_KEY))
    if fn is None:
        return
    _BUSY[0] = True
    try:
        fn(root, current_frame() if frame is None else frame)
    finally:
        _BUSY[0] = False


def update_all(scene=None):
    frame = current_frame(scene)
    for root in effect_roots():
        update_root(root, frame)


@persistent
def _on_frame(scene, depsgraph=None):
    update_all(scene)


@persistent
def _on_depsgraph(scene, depsgraph):
    """ルートのパラメータ・コントロールを変えた時に計算し直す (作った物の変化は無視)。"""
    if _BUSY[0]:
        return
    roots = []
    for u in depsgraph.updates:
        idb = getattr(u.id, "original", u.id)
        if not isinstance(idb, bpy.types.Object):
            continue
        if idb.get(OWNER_KEY) is not None:
            continue
        r = root_of(idb)
        if r is not None and r not in roots:
            roots.append(r)
    frame = current_frame(scene)
    for r in roots:
        update_root(r, frame)


@persistent
def _on_load(*args):
    update_all()


def register():
    for lst, fn in ((bpy.app.handlers.frame_change_post, _on_frame),
                    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        for h in [h for h in lst if getattr(h, "__name__", "") == fn.__name__]:
            lst.remove(h)
        lst.append(fn)


def unregister():
    for lst, fn in ((bpy.app.handlers.frame_change_post, _on_frame),
                    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        for h in [h for h in lst if getattr(h, "__name__", "") == fn.__name__]:
            lst.remove(h)


# ---------------------------------------------------------------- 形とマテリアル
def flat_material(name, color, opacity=1.0, owner=None):
    """光の影響を受けない色 (Emission) と不透明度のマテリアル。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    emit.name, mix.name = "dfx_emission", "dfx_mix"
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    set_material(mat, color, opacity)
    if owner is not None:
        own(owner, mat)
    return mat


def set_material(mat, color, opacity=1.0):
    nt = mat.node_tree
    nt.nodes["dfx_emission"].inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    nt.nodes["dfx_mix"].inputs[0].default_value = opacity
    mat.diffuse_color = (color[0], color[1], color[2], opacity)


def safe_name(text):
    return re.sub(r"[^0-9A-Za-z_]", "_", text)
