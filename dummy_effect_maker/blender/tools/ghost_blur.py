# -*- coding: utf-8 -*-
"""お化けブラー (Blender 版): 既に動いているオブジェクトに、速さを表す 3 種類のブラーを付ける。

Maya 版 (tools/ghost_blur.py) と同じパラメータ・同じ計算 (ghost_ratio / speed_factor /
smear_points / trail_layout / trail_taper ...)。元のアニメーションは変えず、過去のフレームの位置を
読んで作る (止まっている時は何も出ない)。

    dfx_afterimageN               ルート (パラメータ。dfx_source = 付けたオブジェクト)
      dfx_afterimageN_ghost_01_<部品>  残像 1 体分 × オブジェクトの中のメッシュ 1 つ分
                                    (ジオメトリーノードで元のメッシュの今の形をコピー。毎フレーム置き直す)
    dfx_smearN                    ルート
      dfx_smearN_lattice          オブジェクトを囲むラティス (オブジェクトの子。非表示。毎フレーム点を置き直す)
                                  オブジェクトのメッシュには、ルートと同じ名前の Lattice モディファイアーを足す
    dfx_trailN                    ルート
      dfx_trailN_a / _b           帯の両端 (オブジェクトの子の Empty。動かして調整。キーも可)
      dfx_trailN_trail            帯 (またはチューブ) のメッシュ (毎フレーム頂点を置き直す)

過去の位置: キー・親で動く物は、毎回キーを読んで計算する (アニメーションを直せばすぐ反映)。
コンストレイント・ドライバー・ボーンの親・NLA などで動く物は、作成 (更新) した時にシーンのフレーム
範囲を 1 フレームずつ動かして行列を覚え、間は補間する (アニメーションを変えたら「更新」)。

メッシュのデータは変えない。スミアはモディファイアーなので、「お化けブラーを外す」で元に戻る。
"""
from __future__ import absolute_import

import math

import bpy
from mathutils import Matrix, Vector

from ...core import BaseTool, ToolError
from ...tools import ghost_blur as mg
from .. import runtime as rt
from . import _build as bd

SOURCE_KEY = "dfx_source"
BAKE_KEY = "dfx_bake"
PARTS_KEY = "dfx_parts"
MODIFIER_KEY = "dfx_modifier"
COPY_GROUP = "dfx_ghost_copy"
COPY_GROUP_VERSION = 1
ROLES = ("a", "b")
BASE_NAMES = {mg.AFTERIMAGE: "dfx_afterimage", mg.SMEAR: "dfx_smear", mg.TRAIL: "dfx_trail"}
TRANSFORM_PATHS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                   "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")

_BAKING = set()   # 動きを覚えている最中のルート (その間は更新しない)


def _tool(maya_tool, tooltip, order=None):
    return dict(label=maya_tool.label, tab=mg.TAB, category=maya_tool.category,
                order=maya_tool.order if order is None else order, hosts=("blender",),
                tooltip=tooltip, params=maya_tool.params)


# ---------------------------------------------------------------- 過去の位置
def _simple(obj):
    """obj の行列を、キー (移動・回転・拡大縮小) と親だけで計算できるか。"""
    if any(not getattr(c, "mute", False) and c.influence > 0.0 for c in obj.constraints):
        return False
    if obj.parent is not None and obj.parent_type != "OBJECT":
        return False
    if (tuple(obj.delta_location) != (0.0, 0.0, 0.0) or tuple(obj.delta_rotation_euler) != (0.0, 0.0, 0.0)
            or tuple(obj.delta_scale) != (1.0, 1.0, 1.0)
            or tuple(obj.delta_rotation_quaternion) != (1.0, 0.0, 0.0, 0.0)):
        return False
    ad = obj.animation_data
    if ad is not None:
        if any(d.data_path in TRANSFORM_PATHS for d in ad.drivers):
            return False
        if any(not t.mute and len(t.strips) for t in ad.nla_tracks):
            return False
        if obj.rotation_mode == "AXIS_ANGLE" and ad.action is not None:
            return False
        if any(fc.data_path.startswith("delta_") for fc in rt._fcurves(obj)):
            return False
    return True


def _chain(objs):
    out = []
    for obj in objs:
        while obj is not None:
            if obj not in out:
                out.append(obj)
            obj = obj.parent
    return out


def _flat(m):
    return [m[r][c] for r in range(4) for c in range(4)]


def _matrix(flat, i):
    o = i * 16
    return Matrix([flat[o:o + 4], flat[o + 4:o + 8], flat[o + 8:o + 12], flat[o + 12:o + 16]])


def bake_motion(root, needed):
    """needed (と親) のうち、キーと親だけでは計算できない物の行列を、フレーム範囲ぶん覚える。

    覚えた物の数を返す (0 なら何も覚えない)。
    """
    if BAKE_KEY in root:
        del root[BAKE_KEY]
    complex_objs = [o for o in _chain(needed) if not _simple(o)]
    if not complex_objs:
        return 0
    scene = bpy.context.scene
    start, end = int(scene.frame_start), int(scene.frame_end)
    cur, sub = scene.frame_current, scene.frame_subframe
    data = [[] for _ in complex_objs]
    _BAKING.add(root.name)
    try:
        for f in range(start, end + 1):
            scene.frame_set(f)
            for d, obj in zip(data, complex_objs):
                d.extend(_flat(obj.matrix_world))
    finally:
        scene.frame_set(cur, subframe=sub)
        _BAKING.discard(root.name)
    root[BAKE_KEY] = {"start": float(start),
                      "items": dict(("%d" % i, {"obj": o, "m": d})
                                    for i, (o, d) in enumerate(zip(complex_objs, data)))}
    return len(complex_objs)


class Motion(object):
    """フレームを指定したワールド行列 (今のフレームは今の matrix_world)。"""

    def __init__(self, root, now):
        self.now = now
        self.cache = {}
        self.baked = {}
        bake = root.get(BAKE_KEY)
        if bake is not None:
            start = bake.get("start", 1.0)
            for item in bake.get("items", {}).values():
                obj = item.get("obj")
                if obj is not None:
                    self.baked[obj.name] = (start, item["m"].to_list())

    def world(self, obj, t):
        key = (obj.name, t)
        if key in self.cache:
            return self.cache[key]
        if abs(t - self.now) < 1e-9:
            m = obj.matrix_world.copy()
        elif obj.name in self.baked:
            m = _baked_at(self.baked[obj.name], t)
        elif not _simple(obj):
            m = obj.matrix_world.copy()   # 作った後にコンストレイントを付けた (更新すると覚える)
        else:
            m = rt.matrix_basis_at(obj, t)
            if obj.parent is not None:
                m = self.world(obj.parent, t) @ obj.matrix_parent_inverse @ m
        self.cache[key] = m
        return m


def _baked_at(bake, t):
    start, flat = bake
    n = len(flat) // 16
    x = min(max(t - start, 0.0), n - 1.0)
    i0 = int(math.floor(x))
    fr = x - i0
    if fr < 1e-9 or i0 >= n - 1:
        return _matrix(flat, min(i0, n - 1))
    l0, q0, s0 = _matrix(flat, i0).decompose()
    l1, q1, s1 = _matrix(flat, i0 + 1).decompose()
    return Matrix.LocRotScale(l0.lerp(l1, fr), q0.slerp(q1, fr), s0.lerp(s1, fr))


def _rows(m):
    """Blender の行列 (列ベクトル) → Maya 版の計算用の 16 個 (行ベクトル)。"""
    return [m[0][0], m[1][0], m[2][0], 0.0, m[0][1], m[1][1], m[2][1], 0.0,
            m[0][2], m[1][2], m[2][2], 0.0, m[0][3], m[1][3], m[2][3], 1.0]


# ---------------------------------------------------------------- 共通
def ghost_roots():
    return [r for r in rt.effect_roots() if r.get(rt.TYPE_KEY) in mg.GHOST_TYPES]


def source_of(root):
    return root.get(SOURCE_KEY)


def mesh_parts(target):
    """target (と子孫) のメッシュ (ダミーエフェクトの物は除く)。"""
    out = [target] if target.type == "MESH" else []
    out += [o for o in target.children_recursive if o.type == "MESH" and rt.root_of(o) is None]
    return out


def local_bbox(target, parts):
    """parts のメッシュ (今の形) を囲む箱 (target の空間)。(中心, 大きさ)。メッシュが無ければ None。"""
    import numpy as np
    dg = bpy.context.evaluated_depsgraph_get()
    inv = target.matrix_world.inverted_safe()
    lo, hi = None, None
    for part in parts:
        ev = part.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            n = len(me.vertices)
            if not n:
                continue
            co = np.empty(n * 3)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            m = np.array(inv @ part.matrix_world)
            q = co @ m[:3, :3].T + m[:3, 3]
            a, b = q.min(axis=0), q.max(axis=0)
            lo = a if lo is None else np.minimum(lo, a)
            hi = b if hi is None else np.maximum(hi, b)
        finally:
            ev.to_mesh_clear()
    if lo is None:
        return None
    return (tuple(float(x) for x in (lo + hi) / 2.0), tuple(float(x) for x in hi - lo))


def resolve_targets(kind):
    """選択中のオブジェクト (ダミーエフェクトの物は除く)。"""
    targets = []
    for obj in bpy.context.selected_objects:
        if obj not in targets and rt.root_of(obj) is None:
            targets.append(obj)
    if not targets:
        raise ToolError(u"%sを付ける (動いている) オブジェクトを選択してください" % kind.noun)
    for target in targets:
        if kind.need_mesh and not mesh_parts(target):
            raise ToolError(u"%s にはメッシュがありません (%sはメッシュにだけ付けられます)"
                            % (target.name, kind.noun))
        if kind.one_per_target:
            for other in ghost_roots():
                if other.get(rt.TYPE_KEY) == kind.effect_type and source_of(other) == target:
                    raise ToolError(u"%s には既に%s (%s) が付いています" % (target.name, kind.noun, other.name))
    return targets


def selected_ghost_roots(purpose):
    """選択したお化けブラー (ルート・その中の物) と、選択したオブジェクトに付いているお化けブラー。"""
    roots = rt.selected_roots(bpy.context, mg.GHOST_TYPES)
    selected = set(bpy.context.selected_objects)
    roots += [r for r in ghost_roots() if source_of(r) in selected and r not in roots]
    if not roots:
        raise ToolError(u"%sお化けブラー (dfx_afterimageN / dfx_smearN / dfx_trailN、"
                        u"または付けたオブジェクト) を選択してください" % purpose)
    return roots


def _owned_objects(root, key):
    return sorted((o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and key in o),
                  key=lambda o: o.name)


def _log(ctx, root, target, kind, v):
    if kind.effect_type == mg.AFTERIMAGE:
        detail = u"%d 体 (%s フレームおき)" % (v["count"], v["interval"])
    elif kind.effect_type == mg.SMEAR:
        detail = u"%s フレーム分伸びる" % v["frames"]
    else:
        detail = u"%s / %s フレーム分" % (v["shape"], v["length"])
    ctx.log(u"  %s: %s に%s %s" % (root.name, target.name, kind.noun, detail))
    bake = root.get(BAKE_KEY)
    if bake is not None:
        names = [i["obj"].name for i in bake["items"].values() if i.get("obj") is not None]
        ctx.log(u"  ※ %s はコンストレイントなどで動くため、フレーム範囲の動きを読み込みました "
                u"(アニメーションを変えたら「お化けブラーを更新」)" % u", ".join(names))


def _select(roots):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for r in roots:
        r.select_set(True)
    bpy.context.view_layer.objects.active = roots[0]


def create_effects(ctx, kind, v):
    """選択したオブジェクト 1 つにつき 1 つ作る。"""
    targets = resolve_targets(kind)
    roots = []
    for target in targets:
        root = rt.new_root(kind.effect_type, BASE_NAMES[kind.effect_type])
        rt.add_params(root, kind.stored_params(), v, kind.live)
        root[SOURCE_KEY] = target
        if kind.effect_type == mg.TRAIL:
            _create_trail_points(root, target)
        BUILDERS[kind.effect_type](root, v)
        rt.update_root(root)
        _log(ctx, root, target, kind, v)
        roots.append(root)
    _select(roots)
    return roots[0].name if len(roots) == 1 else [r.name for r in roots]


def _check_source(root):
    target = source_of(root)
    if target is None:
        raise ToolError(u"%s を付けたオブジェクトが見つかりません (削除された可能性があります)" % root.name)
    return target


# ---------------------------------------------------------------- 残像
def copy_group():
    """元のメッシュの今の形 (モディファイアー・アーマチュアの後) をコピーし、マテリアルを付けるノード。"""
    ng = bpy.data.node_groups.get(COPY_GROUP)
    if ng is not None and ng.get("dfx_version") == COPY_GROUP_VERSION:
        return ng
    ng = bpy.data.node_groups.new(COPY_GROUP, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Object", in_out="INPUT", socket_type="NodeSocketObject")
    ng.interface.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    gi = ng.nodes.new("NodeGroupInput")
    go = ng.nodes.new("NodeGroupOutput")
    info = ng.nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"
    setmat = ng.nodes.new("GeometryNodeSetMaterial")
    ng.links.new(gi.outputs["Object"], info.inputs["Object"])
    ng.links.new(info.outputs["Geometry"], setmat.inputs["Geometry"])
    ng.links.new(gi.outputs["Material"], setmat.inputs["Material"])
    ng.links.new(setmat.outputs["Geometry"], go.inputs["Geometry"])
    gi.location, info.location, setmat.location, go.location = (-500, 0), (-300, 0), (-100, 0), (100, 0)
    ng[rt.TAG_KEY] = True
    ng["dfx_version"] = COPY_GROUP_VERSION
    return ng


def _socket_id(ng, name):
    for item in ng.interface.items_tree:
        if getattr(item, "in_out", None) == "INPUT" and item.name == name:
            return item.identifier
    raise KeyError(name)


def build_afterimage(root, v):
    target = _check_source(root)
    parts = mesh_parts(target)
    if not parts:
        raise ToolError(u"%s にはメッシュがありません" % target.name)
    ng = copy_group()
    sid_obj, sid_mat = _socket_id(ng, "Object"), _socket_id(ng, "Material")
    empty = bpy.data.meshes.new(root.name + "_ghostMesh")
    rt.own(root, empty)
    count = int(v["count"])
    for i in range(count):
        mat = rt.flat_material("%s_ghost_%02d_mat" % (root.name, i + 1), v["color_start"],
                               v["opacity_start"], owner=root)
        mat.use_backface_culling = False
        for part in parts:
            obj = bd.new_object(root, "%s_ghost_%02d_%s" % (root.name, i + 1, rt.safe_name(part.name)), empty)
            mod = obj.modifiers.new("dfx_copy", "NODES")
            mod.node_group = ng
            mod[sid_obj] = part
            mod[sid_mat] = mat
            obj["dfx_part"] = part
            obj["dfx_index"] = i
            obj["dfx_mat"] = mat
            if hasattr(obj, "visible_shadow"):
                obj.visible_shadow = False
            obj.scale = (0.0, 0.0, 0.0)
    root["dfx_ghost_count"] = count
    bake_motion(root, [target] + parts)


def afterimage_states(root, v, frame, motion=None):
    """残像ごとの (フレーム, 不透明度, 大きさ, 色)。Maya 版のエクスプレッションと同じ式。"""
    target = source_of(root)
    motion = motion or Motion(root, frame)
    count = int(root.get("dfx_ghost_count", v["count"]))
    now = motion.world(target, frame).translation
    out = []
    for i in range(count):
        dt = max(v["interval"], 0.001) * (i + 1)
        t = frame - dt
        was = motion.world(target, t).translation
        f = mg.speed_factor((now - was).length / dt, v["min_speed"], v["full_speed"])
        a = mg.ghost_ratio(i, count)
        op = mg.lerp(v["opacity_start"], v["opacity_end"], a) * f
        sc = 1.0 + (v["scale_end"] - 1.0) * a
        color = tuple(mg.lerp(c0, c1, a) for c0, c1 in zip(v["color_start"], v["color_end"]))
        out.append((t, op, sc, color))
    return out


def update_afterimage(root, frame):
    if root.name in _BAKING or source_of(root) is None:
        return
    ghosts = _owned_objects(root, "dfx_part")
    if not ghosts:
        return
    v = rt.read_params(root, mg.KINDS[mg.AFTERIMAGE].stored_params())
    motion = Motion(root, frame)
    states = afterimage_states(root, v, frame, motion)
    done = set()
    inv_root = root.matrix_world.inverted_safe()
    for g in ghosts:
        i = g["dfx_index"]
        if i >= len(states):
            continue
        t, op, sc, color = states[i]
        mat = g.get("dfx_mat")
        if mat is not None and mat.name not in done:
            rt.set_material(mat, color, max(0.0, min(1.0, op)))
            done.add(mat.name)
        part = g.get("dfx_part")
        if part is None or op <= 0.001:
            g.scale = (0.0, 0.0, 0.0)
            continue
        g.matrix_basis = inv_root @ motion.world(part, t) @ Matrix.Diagonal((sc, sc, sc, 1.0))
        if sc <= 0.0:
            g.scale = (0.0, 0.0, 0.0)


class AfterimageTool(BaseTool):
    locals().update(_tool(mg.AfterimageTool,
                          u"選択した (動いている) オブジェクトの少し前の位置に、コピーを何体も並べます。\n"
                          u"後ろほど薄く (小さく) なり、速く動いた時だけ出ます。\n"
                          u"オブジェクトの中のメッシュ全部を、ジオメトリーノードで今の形のままコピーします\n"
                          u"(元のメッシュは変えません)。"))

    def run(self, ctx, **v):
        return create_effects(ctx, mg.KINDS[mg.AFTERIMAGE], v)


# ---------------------------------------------------------------- スミア
def _strip_modifiers(root):
    """スミアがオブジェクトに足した Lattice モディファイアーを外す。"""
    name = root.get(MODIFIER_KEY)
    if not name:
        return
    parts = [p for p in root.get(PARTS_KEY, {}).values() if p is not None]
    for part in parts:
        mod = part.modifiers.get(name)
        if mod is not None and mod.type == "LATTICE":
            part.modifiers.remove(mod)
    del root[MODIFIER_KEY]
    if PARTS_KEY in root:
        del root[PARTS_KEY]


def _clean_lattices(root):
    for lat in list(bpy.data.lattices):
        if lat.get(rt.OWNER_KEY) == root and lat.users == 0:
            bpy.data.lattices.remove(lat)


def build_smear(root, v):
    target = _check_source(root)
    _strip_modifiers(root)
    _clean_lattices(root)
    parts = mesh_parts(target)
    if not parts:
        raise ToolError(u"%s にはメッシュがありません" % target.name)
    for part in parts:
        if part.library is not None or getattr(part, "override_library", None) is not None:
            raise ToolError(u"%s はリンクされたオブジェクトのため、スミアを付けられません" % part.name)
    bpy.context.view_layer.update()
    box = local_bbox(target, parts)
    if box is None:
        raise ToolError(u"%s のメッシュに頂点がありません" % target.name)
    center, size = box
    obj_size = max(size)
    size = tuple(max(s, obj_size * 0.01, 1e-4) * (1.0 + 2.0 * mg.LATTICE_PADDING) for s in size)
    n = int(v["divisions"])
    lat = bpy.data.lattices.new(root.name + "_latticeShape")
    lat.points_u = lat.points_v = lat.points_w = n
    rt.own(root, lat)
    co = [tuple(p.co) for p in lat.points]
    lo = [min(c[a] for c in co) for a in range(3)]
    hi = [max(c[a] for c in co) for a in range(3)]
    span = [max(h - l, 1e-9) for l, h in zip(lo, hi)]
    mid = [(l + h) / 2.0 for l, h in zip(lo, hi)]
    obj = bpy.data.objects.new(root.name + "_lattice", lat)
    rt.collection().objects.link(obj)
    obj.parent = target
    obj.matrix_parent_inverse.identity()
    obj.scale = tuple(s / sp for s, sp in zip(size, span))
    obj.location = tuple(c - m * s for c, m, s in zip(center, mid, obj.scale))
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True
    obj["dfx_center"] = list(center)
    obj["dfx_size"] = list(size)
    obj["dfx_obj_size"] = float(obj_size)
    obj["dfx_span"] = span
    obj["dfx_mid"] = mid
    obj["dfx_divisions"] = n
    rt.own(root, obj)
    root["dfx_lattice"] = obj
    name = root.name
    for part in parts:
        old = part.modifiers.get(name)
        if old is not None:
            part.modifiers.remove(old)
        mod = part.modifiers.new(name, "LATTICE")
        mod.object = obj
    root[MODIFIER_KEY] = name
    root[PARTS_KEY] = dict(("%d" % i, p) for i, p in enumerate(parts))
    bake_motion(root, [target])


def smear_lattice_points(root, v, frame, motion=None):
    """ラティスの点 (正規化した位置 -0.5〜0.5、3 個ずつ)。Maya 版の smear_points そのもの。"""
    target = source_of(root)
    lat = root.get("dfx_lattice")
    motion = motion or Motion(root, frame)
    k = max(1, int(v["samples"]))
    length = max(0.0, v["frames"]) * max(0.0, v["amount"])
    dt = length / k
    mats = [_rows(motion.world(target, frame - s * dt)) for s in range(k + 1)]
    inv = _rows(motion.world(target, frame).inverted_safe())
    n = int(lat["dfx_divisions"])
    return mg.smear_points(mats, inv, tuple(lat["dfx_center"]), tuple(lat["dfx_size"]), (n, n, n), v,
                           mg.smear_cap(v, lat["dfx_obj_size"]))


def update_smear(root, frame):
    if root.name in _BAKING or source_of(root) is None:
        return
    lat = root.get("dfx_lattice")
    if lat is None or lat.data is None:
        return
    v = rt.read_params(root, mg.KINDS[mg.SMEAR].stored_params())
    out = smear_lattice_points(root, v, frame)
    span, mid = list(lat["dfx_span"]), list(lat["dfx_mid"])
    co = [mid[i % 3] + x * span[i % 3] for i, x in enumerate(out)]
    lat.data.points.foreach_set("co_deform", co)
    lat.data.update_tag()


class SmearTool(BaseTool):
    locals().update(_tool(mg.SmearTool,
                          u"選択した (動いている) オブジェクト自体を、進行方向の後ろへ引き伸ばします。\n"
                          u"後ろ側ほど、少し前のフレームの位置に残ります (曲がって動けば曲がって伸びる)。\n"
                          u"オブジェクトを囲むラティスと Lattice モディファイアーで変形します\n"
                          u"(メッシュのデータは変えません。「お化けブラーを外す」で元に戻ります)。"))

    def run(self, ctx, **v):
        return create_effects(ctx, mg.KINDS[mg.SMEAR], v)


# ---------------------------------------------------------------- 軌跡
def _create_trail_points(root, target):
    """帯の両端の Empty (作成時だけ。更新しても位置はそのまま)。オブジェクトの子 (ローカルの上 = Z)。"""
    parts = mesh_parts(target)
    box = local_bbox(target, parts) if parts else None
    center, size = box if box else ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    half = (size[2] or max(size) or 1.0) / 2.0
    for role, dz in zip(ROLES, (half, -half)):
        ctrl = rt.add_control(root, role, display="SPHERE", size=max(half * 0.3, 0.05))
        ctrl.parent = target
        ctrl.matrix_parent_inverse.identity()
        ctrl.location = (center[0], center[1], center[2] + dz)


def trail_points(root):
    out = []
    for role in ROLES:
        ctrl = rt.control(root, role)
        if ctrl is None:
            raise ToolError(u"%s の帯の端の Empty (_%s) が見つかりません" % (root.name, role))
        out.append(ctrl)
    return out


def _trail_material(name, owner):
    """根元 (U = 0) → 先 (U = 1) に色・不透明度が変わる、光の影響を受けないマテリアル。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    uv = nt.nodes.new("ShaderNodeUVMap")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    color = nt.nodes.new("ShaderNodeMix")
    color.data_type = "RGBA"
    color.clamp_factor = True
    color.name = "dfx_color"
    alpha = nt.nodes.new("ShaderNodeMix")
    alpha.data_type = "FLOAT"
    alpha.clamp_factor = True
    alpha.name = "dfx_alpha"
    emit = nt.nodes.new("ShaderNodeEmission")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(uv.outputs["UV"], sep.inputs[0])
    nt.links.new(sep.outputs[0], color.inputs[0])
    nt.links.new(sep.outputs[0], alpha.inputs[0])
    nt.links.new(color.outputs[2], emit.inputs["Color"])
    nt.links.new(alpha.outputs[0], mix.inputs[0])
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    rt.own(owner, mat)
    return mat


def set_trail_material(mat, v):
    nt = mat.node_tree
    color, alpha = nt.nodes["dfx_color"], nt.nodes["dfx_alpha"]
    color.inputs[6].default_value = tuple(v["color_start"]) + (1.0,)
    color.inputs[7].default_value = tuple(v["color_end"]) + (1.0,)
    alpha.inputs[2].default_value = v["opacity_start"]
    alpha.inputs[3].default_value = v["opacity_end"]
    c = v["color_start"]
    mat.diffuse_color = (c[0], c[1], c[2], max(0.0, min(1.0, v["opacity_start"])))


def build_trail(root, v):
    target = _check_source(root)
    trail_points(root)
    segments, sides, shape = int(v["segments"]), int(v["sides"]), v["shape"]
    points, counts, connects, us, vs, uv_ids, per_row = mg.trail_layout(shape, segments, sides)
    faces, k = [], 0
    for c in counts:
        faces.append(tuple(connects[k:k + c]))
        k += c
    mesh = bpy.data.meshes.new(root.name + "_trailShape")
    mesh.from_pydata(points, [], faces)
    layer = mesh.uv_layers.new(name="UVMap")
    for i, loop in enumerate(mesh.loops):
        layer.data[i].uv = (us[uv_ids[i]], vs[uv_ids[i]])
    if shape == mg.SHAPE_TUBE:
        for poly in mesh.polygons:
            poly.use_smooth = True
    mesh["dfx_offsets"] = [c for p in points for c in (0.0, p[1], p[2])]
    mesh["dfx_per_row"] = per_row
    mesh["dfx_segments"] = segments
    mesh["dfx_tube"] = shape == mg.SHAPE_TUBE
    rt.own(root, mesh)
    mat = _trail_material(root.name + "_mat", root)
    set_trail_material(mat, v)
    mesh.materials.append(mat)
    obj = bd.new_object(root, root.name + "_trail", mesh)
    obj["dfx_trail"] = True
    if hasattr(obj, "visible_shadow"):
        obj.visible_shadow = False
    bake_motion(root, list(trail_points(root)) + [target])


def _ortho(primary, cand):
    """cand から primary の成分を除いて正規化 (平行なら適当な直交する向き)。Maya 版と同じ。"""
    d = cand - cand.dot(primary) * primary
    if d.length < 1e-6:
        d = primary.cross(Vector((0.0, 0.0, 1.0)))
    if d.length < 1e-6:
        d = primary.cross(Vector((1.0, 0.0, 0.0)))
    return d.normalized()


def trail_rows(a_pts, b_pts, v, tube):
    """行ごとの (中心, X, Y, Z 軸, 太さ) と通った距離。Maya 版のエクスプレッションと同じ計算。

    Maya 版の上 (Y) を使う所 (幅 0 の時の向き) は Blender の上 (Z)。
    """
    segments = len(a_pts) - 1
    mid = [(a + b) * 0.5 for a, b in zip(a_pts, b_pts)]
    path = sum((mid[j] - mid[j + 1]).length for j in range(segments))
    prev_x, prev_y = Vector((1.0, 0.0, 0.0)), Vector((0.0, 0.0, 1.0))
    rows = []
    for j in range(segments + 1):
        t = mid[max(j - 1, 0)] - mid[min(j + 1, segments)]
        y = b_pts[j] - a_pts[j]
        wid = y.length
        if tube:
            x = t.normalized() if t.length > 1e-9 else prev_x
            yn = _ortho(x, y if j == 0 else prev_y)
        else:
            yn = y.normalized() if wid > 1e-9 else Vector((0.0, 0.0, 1.0))
            x = _ortho(yn, t if t.length > 1e-9 else prev_x)
        z = x.cross(yn)
        prev_x, prev_y = x, yn
        rows.append((mid[j], x, yn, z, wid * mg.trail_taper(j, segments, v["width_start"], v["width_end"])))
    return rows, path


def trail_samples(root, v, frame, motion=None):
    """帯の両端の、行ごと (今 → length フレーム前) のワールドの位置。"""
    a, b = trail_points(root)
    obj = _owned_objects(root, "dfx_trail")[0]
    segments = int(obj.data["dfx_segments"])
    motion = motion or Motion(root, frame)
    dt = max(0.0, v["length"]) / segments
    ts = [frame - j * dt for j in range(segments + 1)]
    return ([motion.world(a, t).translation.copy() for t in ts],
            [motion.world(b, t).translation.copy() for t in ts])


def update_trail(root, frame):
    import numpy as np
    if root.name in _BAKING or source_of(root) is None:
        return
    objs = _owned_objects(root, "dfx_trail")
    if not objs or rt.control(root, "a") is None or rt.control(root, "b") is None:
        return
    obj = objs[0]
    mesh = obj.data
    v = rt.read_params(root, mg.KINDS[mg.TRAIL].stored_params())
    if len(mesh.materials) and mesh.materials[0] is not None:
        set_trail_material(mesh.materials[0], v)
    a_pts, b_pts = trail_samples(root, v, frame)
    rows, path = trail_rows(a_pts, b_pts, v, bool(mesh["dfx_tube"]))
    per_row = int(mesh["dfx_per_row"])
    off = np.array(mesh["dfx_offsets"], dtype=np.float64).reshape(len(rows), per_row, 3)
    pos = np.array([tuple(r[0]) for r in rows])
    if path > 1e-4:
        axes = np.array([[tuple(r[1] * r[4]), tuple(r[2] * r[4]), tuple(r[3] * r[4])] for r in rows])
        co = pos[:, None, :] + np.einsum("rvk,rkj->rvj", off, axes)
    else:
        co = np.repeat(pos[:1][:, None, :], len(rows), axis=0).repeat(per_row, axis=1)
    inv = np.array(root.matrix_world.inverted_safe())
    co = co.reshape(-1, 3) @ inv[:3, :3].T + inv[:3, 3]
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()


class TrailTool(BaseTool):
    locals().update(_tool(mg.TrailTool,
                          u"選択した (動いている) オブジェクトの通った跡に、先が細くなる帯を残します。\n"
                          u"帯の両端はオブジェクトの子の Empty (_a / _b) で、オブジェクトに付いて動きます\n"
                          u"(剣なら根元と先に動かす)。チューブは 2 点の間の距離が太さになります。"))

    def run(self, ctx, **v):
        return create_effects(ctx, mg.KINDS[mg.TRAIL], v)


# ---------------------------------------------------------------- 調整
class GhostBlurUpdateTool(BaseTool):
    locals().update(_tool(mg.GhostBlurUpdateTool,
                          u"選択したお化けブラー (ルートか、付けたオブジェクト) を、ルートのパラメータの値で作り直します。\n"
                          u"「(更新)」の付いた項目を変えた時や、オブジェクトの形・大きさ・コンストレイントを変えた時に使います。"))

    def run(self, ctx, **v):
        roots = selected_ghost_roots(u"更新する")
        for root in roots:
            _check_source(root)
        for root in roots:
            bd.rebuild(root)
            kind = mg.KINDS[root[rt.TYPE_KEY]]
            _log(ctx, root, source_of(root), kind, rt.read_params(root, kind.stored_params()))
        _select(roots)
        return [r.name for r in roots]


class GhostBlurRemoveTool(BaseTool):
    label = u"お化けブラーを外す"
    tab = mg.TAB
    category = mg.CATEGORY_EDIT
    order = 866
    hosts = ("blender",)
    tooltip = (u"選択したお化けブラー (ルートか、付けたオブジェクト) を削除し、オブジェクトを元に戻します\n"
               u"(スミアの Lattice モディファイアー・軌跡の両端の Empty も消します)。")

    def run(self, ctx, **v):
        roots = selected_ghost_roots(u"外す")
        names = []
        for root in roots:
            names.append(remove_effect(root))
            ctx.log(u"  %s を外しました" % names[-1])
        return names


def remove_effect(root):
    name = root.name
    _strip_modifiers(root)
    for ctrl in rt.controls(root):
        bpy.data.objects.remove(ctrl, do_unlink=True)
    rt.delete_owned(root)
    _clean_lattices(root)
    bpy.data.objects.remove(root, do_unlink=True)
    return name


# ---------------------------------------------------------------- 登録
BUILDERS = {mg.AFTERIMAGE: build_afterimage, mg.SMEAR: build_smear, mg.TRAIL: build_trail}
UPDATERS = {mg.AFTERIMAGE: update_afterimage, mg.SMEAR: update_smear, mg.TRAIL: update_trail}

for _type, _kind in mg.KINDS.items():
    bd.register_builder(_type, _kind.stored_params, _kind.live, BUILDERS[_type], UPDATERS[_type])
bd.register_releaser(mg.SMEAR, _strip_modifiers)   # 全削除の時も、元のオブジェクトのモディファイアーを外す
