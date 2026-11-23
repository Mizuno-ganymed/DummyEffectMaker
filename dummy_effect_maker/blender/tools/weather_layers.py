# -*- coding: utf-8 -*-
"""天候の大きな板 (Blender 版): 遠景の雨・雪、霧の板、遠景の重ね板、地霧、這う霧。

Maya 版 (tools/weather_layers.py) と同じパラメータ・同じ計算 (motion.py と Maya 版の純粋な関数)。
Maya 版のエクスプレッションの代わりに、毎フレーム (とパラメータ・箱を変えた時) に計算し直す。

    dfx_rainFarN            ルート
      dfx_rainFarN_layers   カメラの位置に付いて動く Empty (回転しない)
        dfx_rainFarN_layer_01 ...  筒 (1 つずつのオブジェクト。マテリアルの UV をずらして流す)
    dfx_fogN                ルート
      dfx_fogN_area         範囲の箱
      dfx_fogN_cards        霧の板 (全部で 1 つのメッシュ。板ごとの濃さは頂点の属性 dfx_alpha)
    dfx_fogDepthN           ルート
      dfx_fogDepthN_frame   カメラと同じ位置・向きの Empty
        dfx_fogDepthN_layer_01 ...  カメラの前に並ぶ板
    dfx_groundFogN          ルート
      dfx_groundFogN_area   範囲の箱
        dfx_groundFogN_layer_01 ... 箱の中に高さ違いで重ねた板 (箱の子)
"""
from __future__ import absolute_import

import math

from ... import motion as mo
from ...core import BaseTool
from ...tools import _weather as mx
from ...tools import weather_layers as ml
from .. import runtime as rt
from . import _build as bd
from . import _wx as wx
from .weather import BUILDERS_BY_TYPE, _register, _tool, create

PART_KEY = "dfx_part"     # 作った物の役目 (layers / frame / layer)
LAYER_KEY = "dfx_layer"   # 何枚目 (何重目) か
ALPHA_ATTR = "dfx_alpha"  # 霧の板の、板ごとの濃さ (頂点の属性)


# ---------------------------------------------------------------- 共通
def _empty(root, name, part, parent=None):
    obj = bd.new_object(root, name, None, parent)
    obj.empty_display_type = "PLAIN_AXES"
    obj.empty_display_size = 0.5
    obj[PART_KEY] = part
    return obj


def _piece(root, part):
    import bpy
    for o in bpy.data.objects:
        if o.get(rt.OWNER_KEY) == root and o.get(PART_KEY) == part:
            return o
    return None


def _layers(root):
    """作った板・筒 (何枚目かの順)。"""
    import bpy
    out = [o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and LAYER_KEY in o]
    return sorted(out, key=lambda o: o[LAYER_KEY])


def _mesh(root, name, pts, faces, uvs=None, uv_ids=None):
    """頂点・面 (Z が上) からメッシュ。uv_ids を渡すと面の角ごとの UV の番号。"""
    import bpy
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(pts, [], faces)
    if uvs:
        layer = mesh.uv_layers.new()
        ids = uv_ids if uv_ids is not None else [i for f in faces for i in f]
        for poly in mesh.polygons:
            for j in range(poly.loop_total):
                layer.data[poly.loop_start + j].uv = uvs[ids[poly.loop_start + j]]
    mesh.update()
    rt.own(root, mesh)
    return mesh


def _follow_camera(obj, cam, rotation):
    """カメラの位置 (rotation なら向きも) に付いて動く (フレームを変えずにカメラを動かしても)。"""
    if cam is None:
        return
    con = obj.constraints.new("COPY_LOCATION")
    con.target = cam
    if rotation:
        con = obj.constraints.new("COPY_ROTATION")
        con.target = cam


def _place_at_camera(obj, cam, rotation):
    """コンストレイントと同じ位置・向きを直接入れておく (コンストレイントが無い時のため)。"""
    if cam is None:
        return
    m = cam.matrix_world
    obj.location = m.translation
    if rotation:
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = m.to_3x3().normalized().to_quaternion()


def _seed(v):
    return v.get("seed", 1)


# ---------------------------------------------------------------- 遠景の筒 (雨・雪)
def cylinder_mesh(root, name):
    """Maya 版の cylinder_layout を Z が上に直したもの (半径 1、高さ 1 (z = -0.5〜0.5))。"""
    pts, faces, uvs, uv_ids = ml.cylinder_layout()
    pts = [mo.zup_vector(p) for p in pts]
    return _mesh(root, name, pts, [tuple(f) for f in faces], uvs, uv_ids)


def build_far(root, v, snow=False):
    texture = wx.resolve_texture(v, "snow" if snow else "rain")
    grp = _empty(root, root.name + "_layers", "layers")
    grp["dfx_aspect"] = ml.texture_aspect(texture)
    cam = wx.camera_of(root)
    _follow_camera(grp, cam, rotation=False)
    _place_at_camera(grp, cam, rotation=False)
    for k in range(int(v["layers"])):
        name = "%s_layer_%02d" % (root.name, k + 1)
        mat = wx.texture_material(name + "_mat", texture, v["color"], v["opacity_near"], root,
                                  extension="REPEAT", mapping=(1.0, 1.0, 0.0, 0.0))
        mesh = cylinder_mesh(root, name)
        mesh.materials.append(mat)
        cyl = bd.new_object(root, name, mesh, parent=grp)
        cyl[LAYER_KEY] = k


def far_state(k, n, frame, v, aspect, cp, cdir, wind, snow):
    """筒 k (n 本のうち) の (半径, 繰り返し u, v, ずらし u, v, 不透明度)。Maya 版の build_far の式。"""
    tile = max(v["tile"], 0.001)
    rad = v["radius"] + v["radius_step"] * k
    ru, rv = ml.far_repeat(rad, v["height"], tile, aspect)
    c = mo.cross(cdir, (0.0, 1.0, 0.0))
    right = mo.unit(c) if mo.mag(c) >= 1e-6 else (1.0, 0.0, 0.0)
    side = mo.dot(mo.mul(wind.drift_at(frame), wind.influence), right)
    sway = v["sway"] * math.sin(mo.TAU * frame / 90.0 + k) / tile if snow else 0.0
    sd = _seed(v)
    ou = -side / tile + sway + mx.hash01(k, 0, 1, sd)
    ov = v["speed"] * frame / (tile * aspect) + mx.hash01(k, 0, 2, sd)
    ratio = k / float(max(n - 1, 1))
    op = v["opacity_near"] + (v["opacity_far"] - v["opacity_near"]) * ratio
    return rad, ru, rv, ou, ov, op


def _update_far(root, frame, kind, snow):
    v = rt.read_params(root, kind.stored_params())
    grp = _piece(root, "layers")
    if grp is None:
        return
    _place_at_camera(grp, wx.camera_of(root), rotation=False)
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    aspect = grp.get("dfx_aspect", 1.0)
    layers = _layers(root)
    for k, cyl in enumerate(layers):
        rad, ru, rv, ou, ov, op = far_state(k, len(layers), frame, v, aspect, cp, cdir, wind, snow)
        cyl.scale = (rad, rad, v["height"])
        mat = wx.material_of(cyl)
        if mat is not None:
            wx.set_mapping(mat, ru, rv, ou, ov)
            wx.set_texture_material(mat, v["color"], op)


def update_rain_far(root, frame):
    _update_far(root, frame, ml.RAIN_FAR, False)


def update_snow_far(root, frame):
    _update_far(root, frame, ml.SNOW_FAR, True)


class RainFarTool(BaseTool):
    locals().update(_tool(ml.RainFarTool, ml.RainFarTool.order))

    def run(self, ctx, **v):
        return create(ctx, ml.RAIN_FAR, v, "dfx_rainFar")


class SnowFarTool(BaseTool):
    locals().update(_tool(ml.SnowFarTool, ml.SnowFarTool.order))

    def run(self, ctx, **v):
        return create(ctx, ml.SNOW_FAR, v, "dfx_snowFar")


# ---------------------------------------------------------------- 霧の板 (霧・這う霧)
def card_material(name, texture, color, owner):
    """霧の板のマテリアル: 絵 × 色、濃さ = 絵の透明度 × 頂点の属性 dfx_alpha (板ごとの濃さ)。

    板を 1 つのメッシュにまとめるので、Maya 版の板ごとのマテリアル (alphaGain) の代わりに
    頂点の属性で板ごとの濃さを持つ。
    """
    mat = wx.texture_material(name, texture, color, 1.0, owner)
    nt = mat.node_tree
    alpha = nt.nodes["dfx_alpha"]
    link = [l for l in nt.links if l.from_node == alpha][0]
    to_socket = link.to_socket
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.name = "dfx_card_alpha"
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = ALPHA_ATTR
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.name = "dfx_card_mul"
    nt.links.remove(link)
    nt.links.new(alpha.outputs[0], mul.inputs[0])
    nt.links.new(attr.outputs["Fac"], mul.inputs[1])
    nt.links.new(mul.outputs[0], to_socket)
    return mat


def set_card_material(mat, color, opacity):
    wx.set_texture_material(mat, color, 1.0)
    # ソリッド表示は頂点の属性を読まないので、全体の不透明度で
    mat.diffuse_color = (color[0], color[1], color[2], max(0.0, min(1.0, opacity)))


def build_cards(root, v):
    count = int(v["count"])
    texture = wx.resolve_texture(v, "fog_card")
    mat = card_material(root.name + "_mat", texture, v["color"], root)
    set_card_material(mat, v["color"], v["opacity"])
    obj = wx.particle_object(root, root.name + "_cards", wx.card_template(), count, mat)
    obj.data.attributes.new(ALPHA_ATTR, "FLOAT", "POINT")


def card_placement(i, frame, v, am, ai, wind, cp, cdir):
    """霧の板 i の ((位置, 軸, 大きさ), 濃さ)。Maya 版の build_cards と同じ (motion.fog_card)。"""
    p, (sx, sy), alpha, roll = mo.fog_card(i, frame, v, am, ai, wind, cp)
    if alpha <= 0.001:
        return wx.hidden(p), 0.0
    return (p, mo.billboard(p, cp, cdir, roll), (sx, sy, sx)), alpha


def _update_cards(root, frame, kind):
    v = rt.read_params(root, kind.stored_params())
    am, ai = wx.area_yup(root)
    cards = wx.part(root, "_cards")
    if am is None or cards is None:
        return
    set_card_material(wx.material_of(cards), v["color"], v["opacity"])
    cp, cdir = wx.camera_yup(root)
    wind = wx.wind_of(root)
    out, alphas = [], []
    for i in range(cards.data["dfx_count"]):
        pl, a = card_placement(i, frame, v, am, ai, wind, cp, cdir)
        out.append(pl)
        alphas.append(a)
    wx.write_particles(cards, out)
    mesh = cards.data
    n = len(mesh["dfx_template"]) // 3
    attr = mesh.attributes.get(ALPHA_ATTR)
    if attr is not None:
        attr.data.foreach_set("value", [a for a in alphas for _ in range(n)])
        mesh.update()


def update_fog(root, frame):
    _update_cards(root, frame, ml.FOG)


def update_ground_crawl(root, frame):
    _update_cards(root, frame, ml.GROUND_CRAWL)


class FogTool(BaseTool):
    locals().update(_tool(ml.FogTool, ml.FogTool.order))

    def run(self, ctx, **v):
        return create(ctx, ml.FOG, v, "dfx_fog", area=(v["area_width"], v["area_height"]))


class GroundCrawlTool(BaseTool):
    locals().update(_tool(ml.GroundCrawlTool, ml.GroundCrawlTool.order))

    def run(self, ctx, **v):
        return create(ctx, ml.GROUND_CRAWL, v, "dfx_groundCrawl",
                      area=(v["area_width"], v["area_height"]))


# ---------------------------------------------------------------- 霧 (遠景の重ね板)
def camera_tan(cam):
    """カメラの横の画角の半分の tan (Maya 版の horizontalFilmAperture * 12.7 / focalLength)。"""
    data = getattr(cam, "data", None)
    if data is None or getattr(data, "type", "PERSP") != "PERSP":
        return 1.0
    return data.sensor_width * 0.5 / max(data.lens, 0.001)


def build_fog_depth(root, v):
    frame = _empty(root, root.name + "_frame", "frame")
    cam = wx.camera_of(root)
    _follow_camera(frame, cam, rotation=True)
    _place_at_camera(frame, cam, rotation=True)
    mat = rt.flat_material(root.name + "_mat", v["color"],
                           ml.layer_opacity(v["density"], int(v["layers"])), owner=root)
    pts, faces, uvs = wx.card_template()
    mesh = _mesh(root, root.name + "_card", pts, faces, uvs)
    mesh.materials.append(mat)
    for k in range(int(v["layers"])):
        plane = bd.new_object(root, "%s_layer_%02d" % (root.name, k + 1), mesh, parent=frame)
        plane[LAYER_KEY] = k


def fog_depth_layer(k, n, v, tanh):
    """板 k (n 枚のうち) のカメラからの距離と幅。Maya 版の build_fog_depth の式。"""
    d0 = v["start"]
    d1 = max(v["end"], d0 + 0.001)
    d = d0 + (d1 - d0) * (k / float(max(n - 1, 1)))
    return d, 2 * d * tanh * 1.4


def update_fog_depth(root, frame):
    v = rt.read_params(root, ml.FOG_DEPTH.stored_params())
    grp = _piece(root, "frame")
    cam = wx.camera_of(root)
    if grp is None:
        return
    _place_at_camera(grp, cam, rotation=True)
    layers = _layers(root)
    n = len(layers)
    if not n:
        return
    rt.set_material(wx.material_of(layers[0]), v["color"], ml.layer_opacity(v["density"], n))
    tanh = camera_tan(cam)
    for k, plane in enumerate(layers):
        d, w = fog_depth_layer(k, n, v, tanh)
        plane.location = (0.0, 0.0, -d)
        plane.scale = (w, w, 1.0)


class FogDepthTool(BaseTool):
    locals().update(_tool(ml.FogDepthTool, ml.FogDepthTool.order))

    def run(self, ctx, **v):
        return create(ctx, ml.FOG_DEPTH, v, "dfx_fogDepth")


# ---------------------------------------------------------------- 地霧 (重ね板)
def ground_material(name, texture, owner):
    """霧の模様 (流れる) × 端が消える箱のぼかし (流れない) = 濃さ。色はルートの色そのまま。

    Maya 版の file × ボックスランプ (中心 0 → 端 1、端の手前 (1 - 端のぼかし) までは同じ濃さ、
    そこから端で 0 まで直線) と同じ。
    """
    import bpy
    mat = bpy.data.materials.new(name)
    nt = wx._clear_nodes(mat)
    N, L = nt.nodes.new, nt.links.new

    def math_node(op, a=None, b=None, name=None, clamp=False):
        node = N("ShaderNodeMath")
        node.operation = op
        node.use_clamp = clamp
        if name:
            node.name = name
        for idx, val in ((0, a), (1, b)):
            if val is None:
                continue
            if isinstance(val, (int, float)):
                node.inputs[idx].default_value = val
            else:
                L(val, node.inputs[idx])
        return node

    out = N("ShaderNodeOutputMaterial")
    uv = N("ShaderNodeTexCoord")
    mp = N("ShaderNodeMapping")
    mp.name = "dfx_mapping"
    tex = N("ShaderNodeTexImage")
    tex.name = "dfx_image"
    tex.image = bpy.data.images.load(texture, check_existing=True)
    tex.extension = "REPEAT"
    L(uv.outputs["UV"], mp.inputs["Vector"])
    L(mp.outputs["Vector"], tex.inputs["Vector"])
    # 箱のぼかし: b = 2 max(|u - 0.5|, |v - 0.5|)、mask = clamp((1 - b) / (1 - 端の位置))
    sep = N("ShaderNodeSeparateXYZ")
    L(uv.outputs["UV"], sep.inputs[0])
    du = math_node("ABSOLUTE", math_node("SUBTRACT", sep.outputs[0], 0.5).outputs[0])
    dv = math_node("ABSOLUTE", math_node("SUBTRACT", sep.outputs[1], 0.5).outputs[0])
    b = math_node("MULTIPLY", math_node("MAXIMUM", du.outputs[0], dv.outputs[0]).outputs[0], 2.0)
    inner = math_node("SUBTRACT", 1.0, b.outputs[0])
    mask = math_node("DIVIDE", inner.outputs[0], 1.0, name="dfx_edge", clamp=True)
    layer = math_node("MULTIPLY", mask.outputs[0], 1.0, name="dfx_layer_alpha")
    alpha = math_node("MULTIPLY", layer.outputs[0], tex.outputs["Alpha"])
    emit = N("ShaderNodeEmission")
    emit.name = "dfx_emission"
    clear = N("ShaderNodeBsdfTransparent")
    mix = N("ShaderNodeMixShader")
    L(alpha.outputs[0], mix.inputs[0])
    L(clear.outputs[0], mix.inputs[1])
    L(emit.outputs[0], mix.inputs[2])
    L(mix.outputs[0], out.inputs["Surface"])
    rt.own(owner, mat)
    return mat


def set_ground_material(mat, color, alpha, edge, repeat_u, repeat_v, offset_u, offset_v):
    nt = mat.node_tree
    nt.nodes["dfx_emission"].inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
    nt.nodes["dfx_layer_alpha"].inputs[1].default_value = alpha
    # 端の位置 (Maya 版のランプの 2 つ目の位置) = clamp(0, 0.99, 1 - 端のぼかし)
    nt.nodes["dfx_edge"].inputs[1].default_value = 1.0 - mo.clamp(0.0, 0.99, 1.0 - edge)
    wx.set_mapping(mat, repeat_u, repeat_v, offset_u, offset_v)
    mat.diffuse_color = (color[0], color[1], color[2], max(0.0, min(1.0, alpha)))


# 地面に寝た板 (Maya 版の XZ 面の板を Z が上に直したもの。UV の V は奥 (+Y) へ)
GROUND_PLANE = ([(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
                [(0, 1, 2, 3)], [(0, 0), (1, 0), (1, 1), (0, 1)])


def ground_height(k, n):
    """板 k (下から、0 始まり) の箱の中の高さ (-0.5〜0.5)。"""
    return -0.5 + (k + 0.5) / float(n)


def build_ground_fog(root, v):
    area = rt.control(root, "area")
    texture = wx.resolve_texture(v, "fog_tile")
    layers = int(v["layers"])
    pts, faces, uvs = GROUND_PLANE
    for k in range(layers):
        name = "%s_layer_%02d" % (root.name, k + 1)
        mesh = _mesh(root, name, pts, faces, uvs)
        mesh.materials.append(ground_material(name + "_mat", texture, root))
        plane = bd.new_object(root, name, mesh, parent=area)
        plane.location = (0.0, 0.0, ground_height(k, layers))
        plane[LAYER_KEY] = k


def ground_state(k, n, frame, v, am, wind):
    """板 k の (濃さ, 繰り返し u, v, ずらし u, v)。Maya 版の build_ground_fog の式。"""
    row0, row2 = (am[0], am[1], am[2]), (am[8], am[9], am[10])
    tile = max(v["tile"], 0.001)
    dw = mo.mul(wind.drift_at(frame), wind.influence)
    du = mo.dot(dw, mo.unit(row0))
    dv = -mo.dot(dw, mo.unit(row2))
    sd = _seed(v)
    a = ml.ground_layer_opacity(v["opacity"], v["falloff"], k, n)
    ou = -du / tile + v["flow"] * frame * (1 + k * 0.37) + mx.hash01(k, 0, 1, sd)
    ov = -dv / tile + v["flow"] * frame * (0.5 - k * 0.21) + mx.hash01(k, 0, 2, sd)
    return a, mo.mag(row0) / tile, mo.mag(row2) / tile, ou, ov


def update_ground_fog(root, frame):
    v = rt.read_params(root, ml.GROUND_FOG.stored_params())
    am, _ = wx.area_yup(root)
    if am is None:
        return
    wind = wx.wind_of(root)
    layers = _layers(root)
    for k, plane in enumerate(layers):
        a, ru, rv, ou, ov = ground_state(k, len(layers), frame, v, am, wind)
        mat = wx.material_of(plane)
        if mat is not None:
            set_ground_material(mat, v["color"], a, v["edge"], ru, rv, ou, ov)


class GroundFogTool(BaseTool):
    locals().update(_tool(ml.GroundFogTool, ml.GroundFogTool.order))

    def run(self, ctx, **v):
        return create(ctx, ml.GROUND_FOG, v, "dfx_groundFog", area=(v["area_width"], v["area_height"]))


# ---------------------------------------------------------------- 種類の登録
for _kind, _build_fn, _update_fn in ((ml.RAIN_FAR, build_far, update_rain_far),
                                     (ml.SNOW_FAR, lambda r, v: build_far(r, v, snow=True), update_snow_far),
                                     (ml.FOG, build_cards, update_fog),
                                     (ml.FOG_DEPTH, build_fog_depth, update_fog_depth),
                                     (ml.GROUND_FOG, build_ground_fog, update_ground_fog),
                                     (ml.GROUND_CRAWL, build_cards, update_ground_crawl)):
    BUILDERS_BY_TYPE[_kind.effect_type] = _build_fn
    if _kind.need_wind:
        _register(_kind, _build_fn, _update_fn)
    else:
        # 風を受けない種類 (遠景の重ね板) は、風を作っても風につながない
        bd.register_builder(_kind.effect_type, _kind.stored_params, _kind.live, _build_fn, _update_fn)
