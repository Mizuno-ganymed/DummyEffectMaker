# -*- coding: utf-8 -*-
"""集中線 (Blender 版): カメラに張り付き、画面の端から「中心の円」へ向かう線を並べる。

Maya 版 (tools/focus_lines.py) と同じパラメータ・同じ計算 (edge_distance / ellipse_radius /
tip_layout / line_layout / grow_schedule / flicker_lengths)。
    dfx_focusLinesN            ルート (パラメータ。dfx_camera = カメラ)
      dfx_focusLinesN_frame    カメラ前方「カメラからの距離」の位置。画面の大きさに合わせて拡大
                               (カメラに Child Of コンストレイントで付き、位置と大きさはドライバー)
        dfx_focusLinesN_focus  中心の円 (Empty。X / Y の移動と拡大縮小で楕円にもできる。キーも打てる)
        dfx_focusLinesN_lines  線 (全部で 1 つのメッシュ。毎フレーム頂点を置き直す)

_frame の中は Maya 版と同じ「画面の横幅の半分 = 1」の座標 (Z = 0 が線の面、+Z がカメラ側)。
_frame はコンストレイントとドライバーで動くので、カメラを動かしてもズーム (焦点距離・センサー・
解像度の変更) しても、そのまま画面を覆い続ける。線の頂点は _frame の座標で書くので、
計算は Maya 版のエクスプレッションと同じ数になる。

線 1 本は「本体 (画面の端 → 先端)・画面の外への延長・丸い先端 (円)」の 3 つの面 (Maya 版と同じ形)。
隠す部分は 1 点に縮める。動き (ちらつき・伸びて移動) は Maya 版がキーを打つ代わりに、
作成・更新した時点のフレーム範囲で同じキーの値を計算して線のオブジェクトに持たせ、毎フレーム読む。
円 (_focus) は更新しても (位置・大きさ・キーごと) そのまま残る。
"""
from __future__ import absolute_import

import bisect
import math

import bpy
from mathutils import Matrix

from ...core import BaseTool, ToolError
from ...tools import focus_lines as mfl
from .. import runtime as rt
from . import _build as bd, _wx as wx

FOCUS_ROLE = "focus"
PART_KEY = "dfx_part"
FRAME_PART = "frame"
LINES_PART = "lines"
# 中心の円・中央の四角の奥行き (Z) の大きさ (平らに見せる。Z の拡大縮小はロック)
FLAT_Z = 0.01
CIRCLE_SIDES = 32
# 線 1 本の頂点数: 本体 4 + 延長 4 + 丸い先端
LINE_VERTS = 8 + CIRCLE_SIDES
# 正投影のカメラの時の「カメラの位置」(_frame の座標。視線がほぼ平行になる遠さ)
ORTHO_DEPTH = 1.0e6


# ---------------------------------------------------------------- カメラと画面 (スピードラインと共通)
def choose_camera(what):
    """選択中のカメラ → シーンのカメラ の順に探す。"""
    cam = wx.choose_camera(bpy.context)
    if cam is None or cam.type != "CAMERA":
        raise ToolError(u"%sを付けるカメラを選択してください (シーンのカメラもありません)" % what)
    return cam


def camera_of(root):
    cam = root.get(mfl.CAMERA_ATTR)
    if cam is None or getattr(cam, "type", None) != "CAMERA":
        return None
    return cam


def _value_at(idb, path, frame):
    """idb のプロパティ path の frame の値 (キーがあればキーから)。"""
    for fc in rt._fcurves(idb):
        if fc.data_path == path and fc.array_index == 0:
            return fc.evaluate(frame)
    return getattr(idb, path)


def render_aspect(scene=None):
    """画面の縦横比 (横 / 縦。ピクセルの縦横比込み。Maya の deviceAspectRatio)。"""
    r = (scene or bpy.context.scene).render
    return max((r.resolution_x * r.pixel_aspect_x) / max(r.resolution_y * r.pixel_aspect_y, 1e-6), 0.001)


def half_width(cam, distance, scene=None, frame=None):
    """カメラから distance の所の、画面の横幅の半分 (_frame の大きさ。ドライバーと同じ式)。"""
    scene = scene or bpy.context.scene
    data = cam.data
    aspect = render_aspect(scene)
    fit = data.sensor_fit
    horizontal = fit == "HORIZONTAL" or (fit == "AUTO" and aspect >= 1.0)
    if data.type == "ORTHO":
        half = (_value_at(data, "ortho_scale", frame) if frame is not None else data.ortho_scale) / 2.0
    else:
        lens = _value_at(data, "lens", frame) if frame is not None else data.lens
        sensor = data.sensor_height if fit == "VERTICAL" else data.sensor_width
        half = distance * sensor / (2.0 * max(lens, 1e-6))
    return half if horizontal else half * aspect


def camera_depth(cam, scene=None, frame=None):
    """_frame の座標でのカメラの位置 (Z)。Maya 版の camera_depth と同じ意味。"""
    if cam.data.type == "ORTHO":
        return ORTHO_DEPTH
    return 1.0 / max(half_width(cam, 1.0, scene, frame), 1e-9)


# _frame の大きさのドライバー (half_width と同じ式。enum は数: sensor_fit AUTO=0 / HORIZONTAL=1 /
# VERTICAL=2、type PERSP=0 / ORTHO=1)。単純な式なので Python の自動実行が切ってあっても動く
_HALF_WIDTH_EXPR = ("(os / 2 if typ == 1 else d * (sh if fit == 2 else sw) / (2 * lens))"
                    " * (1 if (fit == 1 or (fit == 0 and rx * px >= ry * py)) else rx * px / (ry * py))")


def _driver_var(drv, name, id_type, idb, path):
    var = drv.variables.new()
    var.name = name
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = id_type
    var.targets[0].id = idb
    var.targets[0].data_path = path


def build_frame(root, cam):
    """root の下に、カメラに付いて動く _frame を作る (中は「画面の横幅の半分 = 1」)。

    カメラの位置・向き (大きさは使わない) に Child Of で付き、ローカルの Z = -距離、
    大きさ = その距離での画面の横幅の半分 (ドライバー)。
    """
    frame = rt.new_empty(root.name + "_frame", display="PLAIN_AXES", size=0.05, parent=root)
    frame[PART_KEY] = FRAME_PART
    frame.hide_select = True
    rt.own(root, frame)
    con = frame.constraints.new("CHILD_OF")
    con.name = "dfx_followCamera"
    con.target = cam
    con.use_scale_x = con.use_scale_y = con.use_scale_z = False
    con.inverse_matrix = Matrix.Identity(4)
    scene = bpy.context.scene
    dist_path = '["%sdistance"]' % rt.PARAM_PREFIX
    fc = frame.driver_add("location", 2)
    _driver_var(fc.driver, "d", "OBJECT", root, dist_path)
    fc.driver.expression = "-d"
    for axis in range(3):
        drv = frame.driver_add("scale", axis).driver
        drv.type = "SCRIPTED"
        _driver_var(drv, "d", "OBJECT", root, dist_path)
        for name, path in (("lens", "lens"), ("sw", "sensor_width"), ("sh", "sensor_height"),
                           ("fit", "sensor_fit"), ("typ", "type"), ("os", "ortho_scale")):
            _driver_var(drv, name, "CAMERA", cam.data, path)
        for name, path in (("rx", "render.resolution_x"), ("ry", "render.resolution_y"),
                           ("px", "render.pixel_aspect_x"), ("py", "render.pixel_aspect_y")):
            _driver_var(drv, name, "SCENE", scene, path)
        drv.expression = _HALF_WIDTH_EXPR
    return frame


def part(root, name):
    for o in bpy.data.objects:
        if o.get(rt.OWNER_KEY) == root and o.get(PART_KEY) == name:
            return o
    return None


def check_frame(root, cam, distance):
    """_frame のドライバーが古い値のままなら計算し直させる。

    サイドバーで変えた時は Blender が計算し直すが、Python からカスタムプロパティを書き換えた時は
    ドライバーが動かないので、ルートに更新の印を付ける (同じ値では 1 回だけ。印を付け続けない)。
    """
    frame = part(root, FRAME_PART)
    if frame is None or cam is None:
        return
    hw = half_width(cam, distance)
    if abs(frame.scale[0] - hw) <= 1e-5 * max(1.0, hw) and \
            abs(frame.location[2] + distance) <= 1e-5 * max(1.0, distance):
        return
    last = list(frame.get("dfx_retag", ()))
    if len(last) == 2 and abs(last[0] - distance) < 1e-12 and abs(last[1] - hw) < 1e-12:
        return
    frame["dfx_retag"] = [distance, hw]
    root.update_tag()


def attach_control(ctrl, frame):
    """コントロール (円・四角) を _frame の子にする (ローカルの位置・大きさ・キーはそのまま)。"""
    ctrl.parent = frame
    ctrl.matrix_parent_inverse = Matrix.Identity(4)


def basis_at(obj, frame):
    """obj のローカルの (位置, 回転 (ラジアン), 大きさ) の frame の値。

    今のフレームなら今の値 (キーの値。キーを打たずに動かした時もそれに付いてくる)、
    ほかのフレームならキーから読む。
    """
    loc, rot, scl = list(obj.location), list(obj.rotation_euler), list(obj.scale)
    if abs(frame - rt.current_frame()) < 1e-6:
        return loc, rot, scl
    for fc in rt._fcurves(obj):
        i = fc.array_index
        if fc.data_path == "location":
            loc[i] = fc.evaluate(frame)
        elif fc.data_path == "rotation_euler":
            rot[i] = fc.evaluate(frame)
        elif fc.data_path == "scale":
            scl[i] = fc.evaluate(frame)
    return loc, rot, scl


def frame_range(scene=None):
    """動きのキーの範囲 (Maya のタイムスライダーの範囲。プレビュー範囲があればそちら)。"""
    scene = scene or bpy.context.scene
    if scene.use_preview_range:
        return float(scene.frame_preview_start), float(scene.frame_preview_end)
    return float(scene.frame_start), float(scene.frame_end)


def mesh_object(root, name, frame, verts_per_line, faces_per_line, count, material):
    """線を count 本並べた 1 つのメッシュ (_frame の子。頂点は毎フレーム書き直す)。"""
    faces = []
    for i in range(count):
        base = i * verts_per_line
        faces += [tuple(base + k for k in f) for f in faces_per_line]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0.0, 0.0, 0.0)] * (verts_per_line * count), [], faces)
    mesh.materials.append(material)
    rt.own(root, mesh)
    obj = bd.new_object(root, name, mesh, parent=frame)
    obj[PART_KEY] = LINES_PART
    obj["dfx_count"] = count
    if hasattr(obj, "visible_shadow"):
        obj.visible_shadow = False   # 影を落とさない (Maya 版と同じ)
    return obj


def write_vertices(obj, co):
    """co: (頂点数, 2 か 3) の numpy 配列 (_frame の座標)。"""
    import numpy as np
    co = np.asarray(co, dtype=np.float64)
    if co.shape[1] == 2:
        co = np.concatenate([co, np.zeros((len(co), 1))], axis=1)
    mesh = obj.data
    mesh.vertices.foreach_set("co", co.astype(np.float32).ravel())
    mesh.update()


def read_vertices(obj):
    """頂点 (_frame の座標)。検証用。"""
    import numpy as np
    co = np.zeros(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3).astype(np.float64)


def select_only(root):
    wx.select_only(root)


# ---------------------------------------------------------------- ツール
class FocusLinesTool(BaseTool):
    label = mfl.FocusLinesTool.label
    tab = mfl.TAB
    category = mfl.CATEGORY
    order = mfl.FocusLinesTool.order
    hosts = ("blender",)
    tooltip = (u"カメラに張り付く集中線を作成します。\n"
               u"カメラを選択して押してください (未選択ならシーンのカメラ)。\n"
               u"線は中心の円 (dfx_focusLinesN_focus の Empty) に向かい、円の中には入りません。\n"
               u"円を動かす・拡大縮小すると、線の向かう先と空ける大きさが変わります (キーも打てます)。\n"
               u"作成後はサイドバーの「選択したエフェクト」で調整できます。")
    params = mfl.FocusLinesTool.params

    def run(self, ctx, **v):
        cam = choose_camera(u"集中線")
        root = rt.new_root(mfl.EFFECT_TYPE, "dfx_focusLines")
        rt.add_params(root, mfl.stored_params(), v, mfl.LIVE_PARAMS)
        root[mfl.CAMERA_ATTR] = cam
        r = v["focus_radius"]
        focus = rt.add_control(root, FOCUS_ROLE, (0.0, 0.0, 0.0), display="SPHERE", size=1.0)
        focus.scale = (r, r, FLAT_Z)
        focus.lock_location = (False, False, True)
        focus.lock_rotation = (True, True, True)
        focus.lock_scale = (False, False, True)
        build(root, v)
        rt.update_root(root)
        select_only(root)
        ctx.log(u"  %s: %d 本 (カメラ: %s)" % (root.name, v["count"], cam.name))
        return root.name


# ---------------------------------------------------------------- 構築
def build(root, v):
    cam = camera_of(root)
    if cam is None:
        cam = choose_camera(u"集中線")
        root[mfl.CAMERA_ATTR] = cam
    frame = build_frame(root, cam)
    focus = rt.control(root, FOCUS_ROLE)
    if focus is None:   # 消された時は作り直す
        focus = rt.add_control(root, FOCUS_ROLE, (0.0, 0.0, 0.0), display="SPHERE", size=1.0)
        focus.scale = (mfl.FOCUS_DEFAULT_RADIUS, mfl.FOCUS_DEFAULT_RADIUS, FLAT_Z)
    attach_control(focus, frame)

    count = int(v["count"])
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    circle = list(range(8, 8 + CIRCLE_SIDES))
    lines = mesh_object(root, root.name + "_lines", frame, LINE_VERTS,
                        [(0, 1, 2, 3), (4, 5, 6, 7), tuple(circle)], count, mat)

    # 線ごとの値 (Maya 版の線のアトリビュートと同じ)
    aspect = render_aspect()
    layout = mfl.line_layout(count, aspect, v["seed"], v["length_random"], v["thickness_random"])
    lines["dfx_angle"] = [a for a, _, _, _ in layout]       # ラジアン
    lines["dfx_len"] = [l for _, _, l, _ in layout]
    lines["dfx_thick"] = [t for _, _, _, t in layout]
    motion = v["motion"]
    lines["dfx_motion"] = motion
    start, end = frame_range()
    lines["dfx_range"] = [start, end]
    if motion == mfl.MOTION_FLICKER:
        times = mfl._frames(start, end, v["flicker_interval"])
        lines["dfx_flicker_times"] = times
        flat = []
        for i in range(count):
            flat += mfl.flicker_lengths(v["seed"], i, len(times), v["length_random"])
        lines["dfx_flicker"] = flat
    elif motion == mfl.MOTION_GROW:
        g = int(v["grow_frames"])
        sched = mfl.grow_schedule(v["seed"], count, start, end, g, aspect, v["length_random"])
        lines["dfx_grow_frames"] = g
        lines["dfx_grow_n"] = [len(c) for c in sched]
        lines["dfx_grow_t"] = [float(t) for c in sched for t, _, _, _ in c]
        lines["dfx_grow_a"] = [a for c in sched for _, a, _, _ in c]
        lines["dfx_grow_l"] = [l for c in sched for _, _, _, l in c]
    return lines


# ---------------------------------------------------------------- 毎フレーム
def grow_value(cycles, g, frame):
    """伸びて移動のキー (Maya 版の _key_grow) の frame の (角度, 長さの倍率)。

    cycles: [(開始フレーム, 角度, 長さの倍率), ...]。向きはステップ、長さは開始で 倍率 / g、
    g - 1 フレーム後に倍率まで直線で伸び、次の開始まで止まる。最初のキーより前は最初の値。
    """
    times = [c[0] for c in cycles]
    k = bisect.bisect_right(times, frame + 1e-9) - 1
    if k < 0:
        t, a, l = cycles[0]
        return a, l / g
    t, a, l = cycles[k]
    dt = frame - t
    if dt >= g - 1:
        return a, l
    return a, l / g + dt * (l - l / g) / (g - 1)


def line_values(lines, frame):
    """各線の (角度, 長さの倍率, 太さの倍率) の frame の値 (動きのキーを読む)。"""
    angles = list(lines["dfx_angle"])
    lens = list(lines["dfx_len"])
    thick = list(lines["dfx_thick"])
    motion = lines.get("dfx_motion")
    if motion == mfl.MOTION_FLICKER and "dfx_flicker_times" in lines:
        times = list(lines["dfx_flicker_times"])
        flat = list(lines["dfx_flicker"])
        n = len(times)
        k = max(0, bisect.bisect_right(times, frame + 1e-9) - 1)
        lens = [flat[i * n + k] for i in range(len(angles))]
    elif motion == mfl.MOTION_GROW and "dfx_grow_n" in lines:
        g = int(lines["dfx_grow_frames"])
        ns = list(lines["dfx_grow_n"])
        ts, as_, ls = list(lines["dfx_grow_t"]), list(lines["dfx_grow_a"]), list(lines["dfx_grow_l"])
        pos = 0
        for i, n in enumerate(ns):
            cycles = list(zip(ts[pos:pos + n], as_[pos:pos + n], ls[pos:pos + n]))
            pos += n
            angles[i], lens[i] = grow_value(cycles, g, frame)
    return angles, lens, thick


def line_shapes(v, aspect, center, radius, angles, lens, thick):
    """各線の (画面の端の位置, 向き (cos, sin), 本体の長さ, 太さ, 丸い先端の半径, 長さ)。

    Maya 版の線のエクスプレッションと同じ式 (_frame の座標)。
    """
    tip_round = v["tip_shape"] == mfl.TIP_ROUND
    out = []
    for ang, lf, tf in zip(angles, lens, thick):
        d = mfl.edge_distance(ang, aspect, center)
        room = max(d - mfl.ellipse_radius(ang, radius[0], radius[1]), 0.0)
        length = v["length"] * lf * room
        th = v["thickness"] * tf
        body, cap = mfl.tip_layout(length, th, tip_round, v["tip_width"])
        c, s = math.cos(ang), math.sin(ang)
        out.append(((center[0] + c * d, center[1] + s * d), (c, s), body, th, cap, length))
    return out


def line_vertices(v, shapes):
    """線の頂点 (線の数 × LINE_VERTS, 2)。本体・延長・丸い先端の順。隠す部分は 1 点に縮める。"""
    import numpy as np
    n = len(shapes)
    if not n:
        return np.zeros((0, 2))
    tip_round = v["tip_shape"] == mfl.TIP_ROUND
    k = min(1.0, max(0.01, v["tip_width"])) if tip_round else 0.0
    e = np.array([s[0] for s in shapes])
    d = np.array([s[1] for s in shapes])
    nrm = np.stack([-d[:, 1], d[:, 0]], axis=1)
    body = np.array([s[2] for s in shapes])
    th = np.array([s[3] for s in shapes])
    cap = np.array([s[4] for s in shapes])
    length = np.array([s[5] for s in shapes])
    vis = length > 1e-6
    over = np.where(vis, v["overscan"], 0.0)
    tth = np.where(vis, th, 0.0)
    cap = np.where(cap > 1e-6, cap, 0.0)

    def place(origin, lx, ly, sx, sy):
        lx, ly = np.asarray(lx, dtype=float), np.asarray(ly, dtype=float)
        return (origin[:, None, :] + (lx[None, :, None] * sx[:, None, None]) * d[:, None, :]
                + (ly[None, :, None] * sy[:, None, None]) * nrm[:, None, :])

    body_pts = place(e, (0, -1, -1, 0), (1, k, -k, -1), body, th)
    tail_pts = place(e, (0, 0, 1, 1), (1, -1, -1, 1), over, tth)
    tip_c = e - d * body[:, None]
    a = 2.0 * math.pi * np.arange(CIRCLE_SIDES) / CIRCLE_SIDES
    tip_pts = tip_c[:, None, :] + cap[:, None, None] * np.stack([np.cos(a), np.sin(a)], axis=1)[None]
    return np.concatenate([body_pts, tail_pts, tip_pts], axis=1).reshape(-1, 2)


def focus_state(root, frame):
    """中心の円の (中心, 半径 (X, Y)) の frame の値 (_frame の座標)。"""
    focus = rt.control(root, FOCUS_ROLE)
    if focus is None:
        return (0.0, 0.0), (mfl.FOCUS_DEFAULT_RADIUS, mfl.FOCUS_DEFAULT_RADIUS)
    loc, _, scl = basis_at(focus, frame)
    return (loc[0], loc[1]), (abs(scl[0]), abs(scl[1]))


def update(root, frame):
    lines = part(root, LINES_PART)
    if lines is None:
        return
    v = rt.read_params(root, mfl.stored_params())
    mats = lines.data.materials
    if len(mats) and mats[0] is not None:
        rt.set_material(mats[0], v["color"], v["opacity"])
    check_frame(root, camera_of(root), v["distance"])
    center, radius = focus_state(root, frame)
    angles, lens, thick = line_values(lines, frame)
    shapes = line_shapes(v, render_aspect(), center, radius, angles, lens, thick)
    write_vertices(lines, line_vertices(v, shapes))


bd.register_builder(mfl.EFFECT_TYPE, mfl.stored_params, mfl.LIVE_PARAMS, build, update)
