# -*- coding: utf-8 -*-
"""Blender 版の天候・爆発の共通処理。

計算は motion.py (Maya 版と同じ式。Y が上)。Blender (Z が上) の値は Y が上に直して渡し、
結果 (位置・軸) を Z が上に戻して置く。

粒は 1 つずつのオブジェクトにせず、同じ形 (型) を数だけ並べた 1 つのメッシュにして、毎フレーム
頂点を書き換える (オブジェクトが何百も並ばず、速い)。型は Maya 版と同じローカルの向きで作る
(板は XY 面で Z を向く、はねの輪は XZ 面)。隠す粒は大きさ 0 (1 点に縮める)。
"""
from __future__ import absolute_import

import math
import os

import bpy

from ... import motion as mo, textures
from ...core import ToolError
from .. import runtime as rt
from . import _build as bd

TAB = u"天候"
WIND_TYPE = "wind"
TABLE_KEYS = ("dfxDriftX", "dfxDriftY", "dfxDriftZ", "dfxWindX", "dfxWindY", "dfxWindZ")
DRIFT_START_KEY = "dfxDriftStart"
CAMERA_KEY = "dfx_camera"
WIND_KEY = "dfx_wind"


# ---------------------------------------------------------------- 空間
def rows(m):
    """Blender の行列 → 行ベクトルの 16 個 (Z が上のまま)。"""
    return [m[0][0], m[1][0], m[2][0], 0.0, m[0][1], m[1][1], m[2][1], 0.0,
            m[0][2], m[1][2], m[2][2], 0.0, m[0][3], m[1][3], m[2][3], 1.0]


def yup(m):
    """Blender の行列 → motion.py 用 (Y が上。ローカルの上も Z → Y)。"""
    return mo.yup_matrix(rows(m))


def euler_axes(rx, ry, rz):
    """Maya の回転 (xyz、度) → 軸 (Y が上の空間)。"""
    from ...tools.ghost_blur import rotation_rows
    return rotation_rows(rx, ry, rz)


IDENTITY_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


# ---------------------------------------------------------------- カメラ・風
def choose_camera(context):
    for obj in context.selected_objects:
        if obj.type == "CAMERA":
            return obj
    return context.scene.camera


def camera_of(root):
    cam = root.get(CAMERA_KEY)
    return cam if cam is not None else bpy.context.scene.camera


def camera_yup(root):
    """root のカメラの (位置, 向き) (Y が上)。カメラが無ければ上から見下ろす。"""
    cam = camera_of(root)
    if cam is None:
        return (0.0, 10.0, 0.0), (0.0, -1.0, 0.0)
    m = cam.matrix_world
    fwd = -m.col[2].xyz
    return mo.yup_vector(tuple(m.translation)), mo.yup_vector(tuple(fwd))


def wind_roots():
    return [o for o in rt.effect_roots() if o.get(rt.TYPE_KEY) == WIND_TYPE]


def choose_wind(context):
    for r in rt.selected_roots(context, (WIND_TYPE,)):
        return r
    winds = wind_roots()
    return winds[0] if winds else None


def wind_of(root):
    """root につながった風の表 (Y が上に直したもの)。"""
    w = root.get(WIND_KEY)
    if w is None or TABLE_KEYS[0] not in w:
        return mo.Wind()
    tabs = [list(w[k]) for k in TABLE_KEYS]
    drift = [mo.yup_vector(v) for v in zip(*tabs[:3])]
    vel = [mo.yup_vector(v) for v in zip(*tabs[3:])]
    return mo.Wind(drift, vel, w.get(DRIFT_START_KEY, 0.0), root.get("dfx_wind_influence", 1.0))


# ---------------------------------------------------------------- 範囲の箱・出る場所
def add_area(root, width, height, follow_camera, camera, ground=0.0):
    """範囲の箱 (Empty の立方体。大きさ 1 の箱を拡大)。底が地面。"""
    area = rt.add_control(root, "area", (0.0, 0.0, ground + height / 2.0), display="CUBE", size=0.5)
    area.scale = (width, width, height)
    if follow_camera and camera is not None:
        con = area.constraints.new("COPY_LOCATION")
        con.target = camera
        con.use_z = False
        con.use_offset = True
    return area


def area_yup(root):
    """範囲の箱の今の行列と逆行列 (Y が上)。"""
    area = rt.control(root, "area")
    if area is None:
        return None, None
    am = yup(area.matrix_world)
    return am, mo.inverse(am)


def add_emitter(root, target):
    """出る場所 (矢印の Empty。上 (Z) が出る向き)。target があれば、それに付いて動く。"""
    em = rt.add_control(root, "emitter", (0.0, 0.0, 0.0), display="SINGLE_ARROW", size=0.6)
    if target is not None:
        em.parent = target
        em.matrix_parent_inverse.identity()
    return em


def emitter_at(root):
    """出る場所の、フレームを指定した行列 (Y が上) を返す関数。"""
    em = rt.control(root, "emitter")
    cache = {}

    def at(t):
        if em is None:
            return mo.yup_matrix(rows(_identity()))
        if t not in cache:
            cache[t] = yup(rt.matrix_world_at(em, t))
        return cache[t]
    return at


def _identity():
    from mathutils import Matrix
    return Matrix.Identity(4)


def selected_target(context):
    for obj in context.selected_objects:
        if obj.type != "CAMERA" and rt.root_of(obj) is None:
            return obj
    return None


# ---------------------------------------------------------------- 型 (Maya 版と同じローカルの形)
def card_template(y0=-0.5, y1=0.5):
    """XY 面の板 (Z を向く)。幅 1、y0〜y1。"""
    pts = [(-0.5, y0, 0.0), (0.5, y0, 0.0), (0.5, y1, 0.0), (-0.5, y1, 0.0)]
    return pts, [(0, 1, 2, 3)], [(0, 0), (1, 0), (1, 1), (0, 1)]


def disc_template(sides=12, radius=0.5, inner=0.0, flat_y=False):
    """円 (inner > 0 なら輪)。XY 面 (flat_y なら XZ 面 = 地面に寝かせる)。"""
    pts = []
    for k in range(sides):
        a = 2 * math.pi * k / sides
        pts.append((radius * math.cos(a), radius * math.sin(a)))
        if inner > 0:
            pts.append((inner * math.cos(a), inner * math.sin(a)))
    if inner > 0:
        faces = [(2 * k, 2 * ((k + 1) % sides), 2 * ((k + 1) % sides) + 1, 2 * k + 1)
                 for k in range(sides)]
    else:
        faces = [tuple(range(sides))]
    uvs = [(0.5 + x / (2 * radius), 0.5 + y / (2 * radius)) for x, y in pts]
    pts = [(x, 0.0, y) if flat_y else (x, y, 0.0) for x, y in pts]
    return pts, faces, uvs


def puff_template():
    """丸を 5 つ集めた、もこもこの塊 (Maya 版と同じ配置)。"""
    import bmesh
    bm = bmesh.new()
    for x, y, z, r in ((0, 0, 0, 0.42), (0.28, -0.05, 0.1, 0.3), (-0.27, -0.04, -0.08, 0.3),
                       (0.05, 0.22, -0.05, 0.3), (-0.05, -0.02, 0.3, 0.26)):
        geom = bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=r)
        bmesh.ops.translate(bm, vec=(x, y, z), verts=geom["verts"])
    bm.verts.index_update()
    pts = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    return pts, faces, None


# ---------------------------------------------------------------- 粒のメッシュ
def particle_object(root, name, template, count, material=None, smooth=False, faces=None):
    """型を count 個並べた 1 つのメッシュのオブジェクト (faces を渡すと面はそれだけにする)。"""
    pts, tfaces, uvs = template
    n = len(pts)
    all_pts, all_faces = [], []
    for i in range(count):
        all_pts += pts
        all_faces += [tuple(k + i * n for k in f) for f in tfaces]
    if faces is not None:
        all_faces = faces
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(all_pts, [], all_faces)
    if uvs:
        layer = mesh.uv_layers.new()
        for loop in mesh.loops:
            layer.data[loop.index].uv = uvs[loop.vertex_index % n]
    if smooth:
        for poly in mesh.polygons:
            poly.use_smooth = True
    mesh["dfx_template"] = [c for p in pts for c in p]
    mesh["dfx_count"] = count
    rt.own(root, mesh)
    if material is not None:
        mesh.materials.append(material)
    obj = bd.new_object(root, name, mesh)
    obj["dfx_particles"] = True
    return obj


def particle_objects(root):
    return [o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and o.get("dfx_particles")]


def part(root, suffix):
    for o in particle_objects(root):
        if o.name.endswith(suffix):
            return o
    return None


def write_particles(obj, placements):
    """粒を置く。placements: [(位置, (X, Y, Z) 軸, (大きさ x, y, z))] (Y が上の空間)。

    大きさが 0 の粒は 1 点に縮める (見えない)。
    """
    import numpy as np
    mesh = obj.data
    tmpl = np.array(mesh["dfx_template"], dtype=np.float64).reshape(-1, 3)
    count = len(placements)
    pos = np.zeros((count, 3))
    axes = np.zeros((count, 3, 3))
    for i, (p, ax, s) in enumerate(placements):
        pos[i] = mo.zup_vector(p)
        for k in range(3):
            axes[i, k] = mo.zup_vector(ax[k])
            axes[i, k] *= s[k]
    co = pos[:, None, :] + np.einsum("vk,nkj->nvj", tmpl, axes)
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()


def read_particle(obj, i):
    """粒 i の頂点 (ワールド。検証用)。"""
    mesh = obj.data
    n = len(mesh["dfx_template"]) // 3
    mw = obj.matrix_world
    return [tuple(mw @ mesh.vertices[i * n + k].co) for k in range(n)]


def hidden(p):
    return (p, IDENTITY_AXES, (0.0, 0.0, 0.0))


# ---------------------------------------------------------------- マテリアル
def texture_path(name):
    """ツールが作る絵のパス (.blend と同じフォルダの textures/dfx_weather。保存前はユーザーのフォルダ)。"""
    if bpy.data.filepath:
        folder = os.path.join(bpy.path.abspath("//"), "textures", "dfx_weather")
    else:
        folder = bpy.utils.user_resource("DATAFILES", path="dummy_effect_maker/textures", create=True)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, textures.file_name(name))
    if not os.path.isfile(path):
        textures.make(name, path)
    return path


def resolve_texture(v, default_name):
    path = (v.get("texture") or u"").strip()
    if not path:
        return texture_path(default_name)
    path = bpy.path.abspath(path)
    if not os.path.isfile(path):
        raise ToolError(u"テクスチャのファイルが見つかりません: %s" % path)
    return path


def _clear_nodes(mat):
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    return nt


def texture_material(name, image_path, color, opacity, owner, extension="CLIP", mapping=None):
    """絵 × 色、不透明度 = 絵の透明度 × 不透明度 のマテリアル (光の影響を受けない)。

    mapping: None 以外なら UV を (繰り返し x, y, ずらし x, y) で変える (遠景の重ね板用)。
    """
    mat = bpy.data.materials.new(name)
    nt = _clear_nodes(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.name = "dfx_image"
    tex.image = bpy.data.images.load(image_path, check_existing=True)
    tex.extension = extension
    if mapping is not None:
        uv = nt.nodes.new("ShaderNodeTexCoord")
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.name = "dfx_mapping"
        nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], tex.inputs["Vector"])
    tint = nt.nodes.new("ShaderNodeMix")
    tint.data_type = "RGBA"
    tint.blend_type = "MULTIPLY"
    tint.name = "dfx_tint"
    tint.inputs[0].default_value = 1.0
    alpha = nt.nodes.new("ShaderNodeMath")
    alpha.operation = "MULTIPLY"
    alpha.name = "dfx_alpha"
    emit = nt.nodes.new("ShaderNodeEmission")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(tex.outputs["Color"], tint.inputs[6])
    nt.links.new(tint.outputs[2], emit.inputs["Color"])
    nt.links.new(tex.outputs["Alpha"], alpha.inputs[0])
    nt.links.new(alpha.outputs[0], mix.inputs[0])
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    rt.own(owner, mat)
    set_texture_material(mat, color, opacity)
    if mapping is not None:
        set_mapping(mat, *mapping)
    return mat


def set_texture_material(mat, color, opacity):
    nt = mat.node_tree
    nt.nodes["dfx_tint"].inputs[7].default_value = (color[0], color[1], color[2], 1.0)
    nt.nodes["dfx_alpha"].inputs[1].default_value = opacity
    mat.diffuse_color = (color[0], color[1], color[2], max(0.0, min(1.0, opacity)))


def set_mapping(mat, repeat_u, repeat_v, offset_u, offset_v):
    mp = mat.node_tree.nodes["dfx_mapping"]
    mp.inputs["Location"].default_value = (offset_u, offset_v, 0.0)
    mp.inputs["Scale"].default_value = (repeat_u, repeat_v, 1.0)


def toon_material(name, color, shade, opacity, owner):
    """アニメ調の 2 色 (正面は色、ふちは影の色)。Maya 版の facingRatio と同じ考え方。"""
    mat = bpy.data.materials.new(name)
    nt = _clear_nodes(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    lw = nt.nodes.new("ShaderNodeLayerWeight")
    inv = nt.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.name = "dfx_toon"
    ramp.color_ramp.interpolation = "CONSTANT"
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[1].position = 0.45
    emit = nt.nodes.new("ShaderNodeEmission")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.name = "dfx_mix"
    # Layer Weight の Facing は正面で 0。Maya の facingRatio (正面で 1) に合わせて 1 - Facing
    nt.links.new(lw.outputs["Facing"], inv.inputs[1])
    nt.links.new(inv.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], emit.inputs["Color"])
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    rt.own(owner, mat)
    set_toon_material(mat, color, shade, opacity)
    return mat


def set_toon_material(mat, color, shade, opacity):
    ramp = mat.node_tree.nodes["dfx_toon"].color_ramp
    ramp.elements[0].color = (shade[0], shade[1], shade[2], 1.0)
    ramp.elements[1].color = (color[0], color[1], color[2], 1.0)
    mat.node_tree.nodes["dfx_mix"].inputs[0].default_value = opacity
    mat.diffuse_color = (color[0], color[1], color[2], max(0.0, min(1.0, opacity)))


def material_of(obj):
    mats = obj.data.materials if obj is not None else []
    return mats[0] if len(mats) else None


# ---------------------------------------------------------------- 作成の共通処理
def create_root(context, ctx, effect_type, base, params, live, v, need_camera=True, need_wind=True):
    """天候のルートを作る (カメラ・風のつながりもここで決める)。(root, camera, target) を返す。"""
    cam = choose_camera(context) if need_camera else None
    wroot = choose_wind(context) if need_wind else None
    target = selected_target(context)
    root = rt.new_root(effect_type, base)
    rt.add_params(root, params, v, live)
    if cam is not None:
        root[CAMERA_KEY] = cam
    if wroot is not None:
        root[WIND_KEY] = wroot
    msg = [u"  %s" % root.name]
    if cam is not None:
        msg.append(u"カメラ %s" % cam.name)
    elif need_camera:
        msg.append(u"カメラなし (シーンのカメラを作ると、そちらを向きます)")
    if need_wind:
        msg.append(u"風 %s" % wroot.name if wroot else u"風なし (「風」を作ると流れます)")
    ctx.log(u" / ".join(msg))
    return root, cam, target


def select_only(root):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    root.select_set(True)
    bpy.context.view_layer.objects.active = root
