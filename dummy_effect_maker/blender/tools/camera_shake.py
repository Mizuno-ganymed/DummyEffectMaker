# -*- coding: utf-8 -*-
"""画ぶれ・手ブレ (Blender 版): カメラ (や選択したオブジェクト) に、キーを壊さずにブレを加える。

Maya 版 (tools/camera_shake.py / hand_shake.py) と同じパラメータ・同じ考え方。
Maya の足し算用チャンネル (rotatePivotTranslate / rotateAxis) の代わりに、Blender の
デルタ トランスフォーム (delta_location / delta_rotation。キーの値に足される) にブレを入れる。

    dfx_cameraShakeN   ルート (パラメータ。dfx_camera = ぶらす物、dfx_aim = 注視点)
                       画ぶれは乱数のキー (dfx_noise_tx / ty / rx / ry / tz) をルートに打つ
    dfx_handShakeN     手ブレはキーを打たず、なめらかな乱数の波 (ノイズ) で毎フレーム計算する

注視点のあるカメラ (トラック (Track To / Damped Track / Locked Track) コンストレイントの
ターゲット) は、Maya 版と同じく注視点もぶらす。傾き (手ブレのロール) は、注視点のないカメラ・
オブジェクトだけ (コンストレイントが向きを決めるため)。
回転のブレの基準距離: カメラは被写界深度の焦点距離 (無ければ 10)、それ以外はルートの「回転の基準距離」。
"""
from __future__ import absolute_import

import math

import bpy
from mathutils import Euler, Vector, noise

from ...core import BaseTool, ToolError
from ...tools import camera_shake as mc, hand_shake as mh
from .. import runtime as rt
from . import _build as bd

TARGET_KEY = "dfx_camera"
AIM_KEY = mc.AIM_ATTR
BASE_LOC = "dfxBaseDeltaLoc"
BASE_ROT = "dfxBaseDeltaRot"        # デルタ回転 (オイラー) か、クォータニオン (4 つ)
BASE_AIM = "dfxBaseAimDeltaLoc"
TRACK_TYPES = ("TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK")
SHAKE_TYPES = (mc.EFFECT_TYPE, mh.EFFECT_TYPE)
DEFAULT_COI = 10.0


def _tool(maya_tool, order):
    return dict(label=maya_tool.label, tab=mc.TAB, category=maya_tool.category, order=order,
                hosts=("blender",))


# ---------------------------------------------------------------- 対象
def resolve_targets(context):
    targets = [o for o in context.selected_objects
               if o.get(rt.TYPE_KEY) not in SHAKE_TYPES and o.get(rt.OWNER_KEY) is None]
    if targets:
        return targets
    if context.scene.camera is not None:
        return [context.scene.camera]
    raise ToolError(u"ぶらすオブジェクト (カメラ・球・Empty など) を選択してください")


def find_aim(obj):
    """カメラの向きを決めている注視点 (トラックのコンストレイントのターゲット)。無ければ None。"""
    if obj.type != "CAMERA":
        return None
    for con in obj.constraints:
        if con.type in TRACK_TYPES and con.target is not None and not con.mute:
            return con.target
    return None


def shake_roots():
    return [r for r in rt.effect_roots() if r.get(rt.TYPE_KEY) in SHAKE_TYPES]


def roots_of(context, effect_type):
    """選択したルートと、選択したオブジェクト・注視点に付いているもの。"""
    selected = set(context.selected_objects)
    if context.active_object is not None:
        selected.add(context.active_object)
    roots = [r for r in rt.selected_roots(context, (effect_type,))]
    for r in shake_roots():
        if r.get(rt.TYPE_KEY) == effect_type and r not in roots and \
                selected & set(filter(None, (r.get(TARGET_KEY), r.get(AIM_KEY)))):
            roots.append(r)
    return roots


def create(ctx, v, effect_type, base, stored, live):
    targets = resolve_targets(bpy.context)
    for target in targets:
        for other in shake_roots():
            if other.get(TARGET_KEY) == target:
                raise ToolError(u"%s には既に%s (%s) が付いています (画ぶれと手ブレは 1 つのオブジェクトに"
                                u"どちらか 1 つ)" % (target.name, _noun(other), other.name))
    roots = []
    for i, target in enumerate(targets):
        values = dict(v, seed=v["seed"] + i)   # 複数のオブジェクトが同じぶれ方にならないように
        root = rt.new_root(effect_type, base)
        rt.add_params(root, stored(), values, live)
        root[TARGET_KEY] = target
        aim = find_aim(target)
        if aim is not None:
            root[AIM_KEY] = aim
        _store_base(root, target, aim)
        BUILD[effect_type](root, values)
        rt.update_root(root)
        msg = u"  %s: %s" % (root.name, target.name)
        if aim is not None:
            msg += u" (注視点 %s もぶらす)" % aim.name
        ctx.log(msg)
        roots.append(root)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for r in roots:
        r.select_set(True)
    bpy.context.view_layer.objects.active = roots[0]
    return [r.name for r in roots]


def _noun(root):
    return u"画ぶれ" if root.get(rt.TYPE_KEY) == mc.EFFECT_TYPE else u"手ブレ"


def _store_base(root, target, aim):
    root[BASE_LOC] = list(target.delta_location)
    if target.rotation_mode == "QUATERNION":
        root[BASE_ROT] = list(target.delta_rotation_quaternion)
    else:
        root[BASE_ROT] = list(target.delta_rotation_euler)
    root[BASE_AIM] = list(aim.delta_location) if aim is not None else [0.0, 0.0, 0.0]


def release(root):
    """ぶらしていた物 (と注視点) のデルタ トランスフォームを、作成前の値に戻す。"""
    target, aim = root.get(TARGET_KEY), root.get(AIM_KEY)
    if target is not None and BASE_LOC in root:
        target.delta_location = list(root[BASE_LOC])
        base = list(root[BASE_ROT])
        if len(base) == 4:
            target.delta_rotation_quaternion = base
        else:
            target.delta_rotation_euler = base
    if aim is not None and BASE_AIM in root:
        aim.delta_location = list(root[BASE_AIM])


# ---------------------------------------------------------------- 画ぶれ: 乱数のキー
def _fcurve_collection(obj):
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return None
    legacy = getattr(ad.action, "fcurves", None)
    if legacy is not None:
        return legacy
    from bpy_extras import anim_utils
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return bag.fcurves if bag else None


def remove_noise_keys(root):
    coll = _fcurve_collection(root)
    if coll is None:
        return
    paths = set('["%s"]' % a for a in mc.NOISE_ATTRS)
    for fc in [fc for fc in coll if fc.data_path in paths]:
        coll.remove(fc)


def build_camera_shake(root, v):
    remove_noise_keys(root)
    scene = bpy.context.scene
    keys = mc.shake_keys(v["seed"], scene.frame_start, scene.frame_end,
                         lambda t: max(mc.MIN_STEP, rt.prop_at(root, "dfx_interval", t) or v["interval"]),
                         v["interval_random"], v["strength_random"])
    interp = {mc.INTERP_SMOOTH: "BEZIER", mc.INTERP_LINEAR: "LINEAR",
              mc.INTERP_STEP: "CONSTANT"}[v["interpolation"]]
    for attr in mc.NOISE_ATTRS:
        root[attr] = 0.0
    for t, values in keys:
        for attr, value in zip(mc.NOISE_ATTRS, values):
            root[attr] = value
            root.keyframe_insert('["%s"]' % attr, frame=t)
    paths = set('["%s"]' % a for a in mc.NOISE_ATTRS)
    for fc in rt._fcurves(root):
        if fc.data_path in paths:
            for k in fc.keyframe_points:
                k.interpolation = interp
                k.handle_left_type = k.handle_right_type = "AUTO_CLAMPED"
            fc.update()


def camera_offsets(root, frame):
    """画ぶれの (dx, dy, dz, ax, ay, roll, on)。強さ × 乱数のキー。"""
    def p(name):
        return rt.prop_at(root, name, frame) or 0.0
    sx, sy, sz = p("dfx_strength_x"), p("dfx_strength_y"), p("dfx_strength_z")
    nx, ny, nrx, nry, nz = [p(a) for a in mc.NOISE_ATTRS]
    return sx * nx, sy * ny, sz * nz, sx * nrx, sy * nry, 0.0, bool(p("dfx_rotation"))


# ---------------------------------------------------------------- 手ブレ: なめらかな乱数の波
def wave(x):
    """なめらかな乱数 (-1〜1 くらい)。Maya の noise() の代わり。"""
    return noise.noise(Vector((x, 0.37, 0.71)))


def hand_offsets(root, frame):
    def p(name):
        return rt.prop_at(root, name, frame) or 0.0
    kt = frame / max(p("dfx_period"), 1.0)
    det = p("dfx_detail")
    sd = p("dfx_seed") * 17.31
    w = [(wave(kt + sd + off) + det * wave(kt * 3.1 + sd + off + 53.7)) / (1 + det)
         for off in mh.CHANNEL_OFFSETS]
    on = bool(p("dfx_rotation"))
    sx, sy, sz = p("dfx_strength_x"), p("dfx_strength_y"), p("dfx_strength_z")
    return (sx * w[0], sy * w[1], sz * w[5], sx * w[2], sy * w[3],
            (p("dfx_roll") * w[4]) if on else 0.0, on)


# ---------------------------------------------------------------- 毎フレーム
def _coi(root, target):
    if target.type == "CAMERA":
        d = target.data.dof.focus_distance
        return d if d > 1e-3 else DEFAULT_COI
    return rt.prop_at(root, "dfx_rotation_distance", bpy.context.scene.frame_current) or 5.0


def _base_position(obj, frame):
    """デルタ (ブレ) を含まないワールドの位置 (キー・親は見る)。"""
    m = rt.matrix_basis_at(obj, frame)
    if obj.parent is not None:
        m = rt.matrix_world_at(obj.parent, frame) @ obj.matrix_parent_inverse @ m
    return m.translation


def _to_parent(obj, frame, vec):
    """ワールドの向き → obj の親の空間 (デルタ移動は親の空間)。"""
    if obj.parent is None:
        return vec
    m = (rt.matrix_world_at(obj.parent, frame) @ obj.matrix_parent_inverse).to_3x3()
    return m.inverted_safe() @ vec


def apply(root, frame, offsets):
    target, aim = root.get(TARGET_KEY), root.get(AIM_KEY)
    if target is None:
        return
    dx, dy, dz, ax, ay, roll, on = offsets
    base_loc = Vector(root[BASE_LOC])
    if aim is not None:
        c = _base_position(target, frame)
        a = _base_position(aim, frame)
        f = a - c
        if f.length > 1e-9:
            f.normalize()
            right = Vector((f.y, -f.x, 0.0))
            x = right.normalized() if right.length > 1e-9 else Vector((1.0, 0.0, 0.0))
            y = x.cross(f)
            z = -f
        else:
            x, y, z = Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0))
        off = x * dx + y * dy + z * dz
        target.delta_location = base_loc + _to_parent(target, frame, off)
        aim_off = (x * ax + y * ay) if on else off
        aim.delta_location = Vector(root[BASE_AIM]) + _to_parent(aim, frame, aim_off)
        return
    basis = rt.matrix_basis_at(target, frame)
    rot = basis.to_3x3().normalized()
    target.delta_location = base_loc + rot @ Vector((dx, dy, dz))
    coi = max(_coi(root, target), 1e-3)
    shake = Euler((math.atan2(ay, coi) if on else 0.0, -math.atan2(ax, coi) if on else 0.0,
                   math.radians(roll)), "XYZ").to_matrix()
    base = list(root[BASE_ROT])
    if len(base) == 4:
        from mathutils import Quaternion
        base_d = Quaternion(base).to_matrix()
    else:
        base_d = Euler(base, target.rotation_mode if target.rotation_mode not in ("QUATERNION", "AXIS_ANGLE")
                       else "XYZ").to_matrix()
    # Blender の回転 = デルタ × 回転。ブレはローカル (回転のあと) に掛けたいので、デルタに直す
    delta = base_d @ rot @ shake @ rot.inverted_safe()
    if target.rotation_mode == "QUATERNION":
        target.delta_rotation_quaternion = delta.to_quaternion()
    elif target.rotation_mode == "AXIS_ANGLE":
        target.delta_rotation_euler = delta.to_euler("XYZ")
    else:
        target.delta_rotation_euler = delta.to_euler(target.rotation_mode)


def update_camera_shake(root, frame):
    apply(root, frame, camera_offsets(root, frame))


def update_hand_shake(root, frame):
    apply(root, frame, hand_offsets(root, frame))


BUILD = {mc.EFFECT_TYPE: build_camera_shake, mh.EFFECT_TYPE: lambda root, v: None}


# ---------------------------------------------------------------- ツール
class CameraShakeTool(BaseTool):
    locals().update(_tool(mc.CameraShakeTool, 640))
    params = mc.CameraShakeTool.params
    tooltip = (u"選択したオブジェクトにランダムなブレを加えます (カメラ・球・Empty など。複数選択可)。\n"
               u"何も選択していなければシーンのカメラ。キーはそのまま、上乗せ (デルタ トランスフォーム) でぶれます。\n"
               u"強さにキーを打つと、だんだん強く・弱くできます。注視点のあるカメラ (Track To など) は注視点もぶらします。")

    def run(self, ctx, **v):
        return create(ctx, v, mc.EFFECT_TYPE, "dfx_cameraShake", mc.stored_params, mc.LIVE_PARAMS)


class CameraShakeRemoveTool(BaseTool):
    locals().update(_tool(mc.CameraShakeRemoveTool, 656))
    tooltip = (u"選択した画ぶれを削除し、カメラ (と注視点) を元に戻します。\n"
               u"カメラを選択しても、そのカメラの画ぶれが対象になります。")

    def run(self, ctx, **v):
        return remove(ctx, mc.EFFECT_TYPE)


class HandShakeTool(BaseTool):
    locals().update(_tool(mh.HandShakeTool, 670))
    params = mh.HandShakeTool.params
    tooltip = (u"選択したオブジェクトに、手持ちカメラのようなゆっくりした揺れを加えます\n"
               u"(カメラ・球・Empty など。複数選択可。何も選択していなければシーンのカメラ)。\n"
               u"キーはそのまま、上乗せで揺れます。パラメータはすべてすぐ反映され、キーも打てます。")

    def run(self, ctx, **v):
        return create(ctx, v, mh.EFFECT_TYPE, "dfx_handShake", mh.stored_params, mh.LIVE_PARAMS)


class HandShakeRemoveTool(BaseTool):
    locals().update(_tool(mh.HandShakeRemoveTool, 686))
    tooltip = (u"選択した手ブレを削除し、オブジェクト (と注視点) を元に戻します。\n"
               u"揺らしているオブジェクトを選択しても、その手ブレが対象になります。")

    def run(self, ctx, **v):
        return remove(ctx, mh.EFFECT_TYPE)


def remove(ctx, effect_type):
    roots = roots_of(bpy.context, effect_type)
    if not roots:
        raise ToolError(u"外す%s (またはぶらしているオブジェクト) を選択してください"
                        % (u"画ぶれ" if effect_type == mc.EFFECT_TYPE else u"手ブレ"))
    for root in roots:
        release(root)
        name = root.name
        rt.delete_owned(root)
        bpy.data.objects.remove(root, do_unlink=True)
        ctx.log(u"  %s を外しました" % name)
    return len(roots)


bd.register_builder(mc.EFFECT_TYPE, mc.stored_params, mc.LIVE_PARAMS, build_camera_shake, update_camera_shake)
bd.register_builder(mh.EFFECT_TYPE, mh.stored_params, mh.LIVE_PARAMS, BUILD[mh.EFFECT_TYPE], update_hand_shake)
bd.register_releaser(mc.EFFECT_TYPE, release)
bd.register_releaser(mh.EFFECT_TYPE, release)
