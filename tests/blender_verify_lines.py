# -*- coding: utf-8 -*-
"""Blender 版の集中線・スピードラインの検証 (Blender を画面なしで動かす)。

    blender -b --factory-startup --python tests/blender_verify_lines.py -- <リポジトリのフォルダ>

Maya 版と同じ計算になるか (線の位置・向き・長さ・動き)、カメラの画面を覆うか、
円・四角を動かした時・パラメータを変えた時・更新・ズームを確かめる。
"""
import math
import os
import sys
import tempfile
import traceback

REPO = sys.argv[sys.argv.index("--") + 1]
sys.path.insert(0, REPO)

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402

RESULTS = []
TMP = tempfile.mkdtemp(prefix="dfx_blender_lines_")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))


def section(fn):
    def wrapper():
        print("\n=== %s ===" % fn.__name__)
        try:
            fn()
        except Exception:
            check(fn.__name__ + " (例外)", False, traceback.format_exc())
    wrapper.__name__ = fn.__name__
    return wrapper


def near(a, b, tol=1e-4):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def new_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import dummy_effect_maker.blender as dfx
    dfx.register()
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 60
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(TMP, "verify_lines.blend"))


import dummy_effect_maker.blender as dfx  # noqa: E402
from dummy_effect_maker.blender import runtime as rt, ui  # noqa: E402

dfx.register()
from dummy_effect_maker.blender.tools import focus_lines as bfl, scene as bscene, speed_lines as bsl  # noqa: E402
from dummy_effect_maker.tools import focus_lines as mfl, speed_lines as msl  # noqa: E402


def op(tool_cls, **values):
    """ツールのオペレーターを、オプションを指定して実行する (ボタンと同じ)。"""
    tool = [t for t in ui._TOOLS if isinstance(t, tool_cls)][0]
    tool.reset_values()
    tool.set_values(values)
    idname = ui.operator_idname(tool)
    cat, name = idname.split(".")
    return getattr(getattr(bpy.ops, cat), name)()


def select(*objs):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def scene_camera(loc=(3, -12, 4), rot=(math.radians(80), 0, math.radians(10))):
    cam = bpy.data.objects.new("shotCam", bpy.data.cameras.new("cam"))
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    cam.location = loc
    cam.rotation_euler = rot
    return cam


def goto(f):
    bpy.context.scene.frame_set(f)
    bpy.context.view_layer.update()


def aspect():
    r = bpy.context.scene.render
    return (r.resolution_x * r.pixel_aspect_x) / float(r.resolution_y * r.pixel_aspect_y)


def frame_verts(obj):
    return bfl.read_vertices(obj)


def world_verts(obj):
    mw = obj.matrix_world
    return [mw @ Vector(p) for p in frame_verts(obj)]


def project(cam, p):
    return world_to_camera_view(bpy.context.scene, cam, p)


def on_border(q, tol=2e-3):
    """画面の正規化座標 q (0〜1) がゲートの縁にあるか。"""
    inside = -tol <= q.x <= 1 + tol and -tol <= q.y <= 1 + tol
    return inside and min(abs(q.x), abs(q.x - 1), abs(q.y), abs(q.y - 1)) < tol


# ---------------------------------------------------------------- 集中線の期待値 (Maya 版の関数)
def expected_focus(v, center, radius, angles, lens, thick):
    """各線の (画面の端, 先端 (本体の先), 丸い先端の中心, 半径) を Maya 版の関数で。"""
    out = []
    asp = aspect()
    for a, lf, tf in zip(angles, lens, thick):
        edge, tip = mfl.line_geometry(a, asp, center, radius, v["length"] * lf)
        length = math.hypot(tip[0] - edge[0], tip[1] - edge[1])
        body, cap = mfl.tip_layout(length, v["thickness"] * tf, v["tip_shape"] == mfl.TIP_ROUND, v["tip_width"])
        c, s = math.cos(a), math.sin(a)
        out.append((edge, (edge[0] - c * body, edge[1] - s * body), cap, v["thickness"] * tf))
    return out


def focus_mismatch(lines, v, center, radius, angles, lens, thick, tol=1e-5):
    co = frame_verts(lines)
    bad = []
    for i, (edge, tip, cap, th) in enumerate(expected_focus(v, center, radius, angles, lens, thick)):
        b = co[i * bfl.LINE_VERTS:(i + 1) * bfl.LINE_VERTS]
        got_edge = (b[0] + b[3]) / 2
        got_tip = (b[1] + b[2]) / 2
        got_th = math.hypot(*(b[0] - b[3])[:2]) / 2
        if not (near(got_edge, edge, tol) and near(got_tip, tip, tol) and abs(got_th - th) < tol
                and abs(b[:, 2]).max() < 1e-9):
            bad.append((i, tuple(got_edge[:2]), edge, tuple(got_tip[:2]), tip))
            continue
        if cap > 1e-6:
            r = math.hypot(*(b[8] - (b[8] + b[8 + bfl.CIRCLE_SIDES // 2]) / 2)[:2])
            if abs(r - cap) > tol:
                bad.append((i, "cap", r, cap))
    return bad


# ---------------------------------------------------------------- 登録
@section
def l01_register():
    labels = [t.label for t in ui._TOOLS]
    check("登録: 集中線・スピードライン", set([u"集中線", u"スピードライン"]) <= set(labels), str(labels))
    tools = dict((t.label, t) for t in ui._TOOLS)
    check("登録: タブ「カメラ」・Maya 版と同じパラメータ",
          tools[u"集中線"].tab == mfl.TAB and tools[u"スピードライン"].tab == mfl.TAB
          and [p.name for p in tools[u"集中線"].params] == [p.name for p in mfl.FocusLinesTool.params]
          and [p.name for p in tools[u"スピードライン"].params] == [p.name for p in msl.SpeedLinesTool.params])
    ops = [ui.operator_idname(t) for t in ui._TOOLS]
    check("登録: オペレーターができる", all(hasattr(getattr(bpy.ops, o.split(".")[0]), o.split(".")[1]) for o in ops))


# ---------------------------------------------------------------- 集中線
@section
def l02_focus_create():
    new_scene()
    select()
    bpy.context.scene.camera = None
    try:
        res = op(bfl.FocusLinesTool)
    except RuntimeError as e:
        res = str(e)
    check("集中線: カメラが無いとエラー", res != {"FINISHED"} and not rt.effect_roots(), str(res))
    cam = scene_camera()
    select(cam)
    res = op(bfl.FocusLinesTool, count=24, motion=mfl.MOTION_STATIC, length=1.0, length_random=0.0,
             thickness_random=0.5, focus_radius=0.3)
    root = bpy.data.objects.get("dfx_focusLines1")
    focus = rt.control(root, "focus") if root else None
    lines = bfl.part(root, bfl.LINES_PART) if root else None
    frame = bfl.part(root, bfl.FRAME_PART) if root else None
    check("集中線: ルート・円・線 (24 本で 1 つのメッシュ)・カメラ", res == {"FINISHED"} and focus is not None
          and lines is not None and len(lines.data.vertices) == 24 * bfl.LINE_VERTS
          and root.get("dfx_camera") == cam and focus.parent == frame and lines.parent == frame)
    check("集中線: パラメータはカスタムプロパティ (作成時だけの円の大きさは持たない)",
          root["dfx_count"] == 24 and root["dfx_motion"] == mfl.MOTION_STATIC and "dfx_focus_radius" not in root
          and near(focus.scale[:2], (0.3, 0.3)))
    drv = [fc.driver for fc in frame.animation_data.drivers]
    check("集中線: _frame のドライバーは単純な式 (Python の自動実行なしで動く)",
          len(drv) == 4 and all(d.is_simple_expression and d.is_valid for d in drv))
    goto(1)
    v = rt.read_params(root, mfl.stored_params())
    layout = mfl.line_layout(24, aspect(), v["seed"], v["length_random"], v["thickness_random"])
    angles = [l[0] for l in layout]
    thick = [l[3] for l in layout]
    bad = focus_mismatch(lines, v, (0.0, 0.0), (0.3, 0.3), angles, [1.0] * 24, thick)
    check("集中線: Maya 版と同じ形 (画面の端・先端・太さ)", not bad, str(bad[:2]))
    # カメラの画面で: 根元はゲートの縁、延長は外、先端は円の縁 (長さ 1)、全部カメラから距離 1 の面
    wv = world_verts(lines)
    hw = bfl.half_width(cam, 1.0)
    border, outside, depth, on_circle = [], [], [], []
    for i in range(24):
        b = wv[i * bfl.LINE_VERTS:(i + 1) * bfl.LINE_VERTS]
        border.append(on_border(project(cam, (b[0] + b[3]) / 2)))
        q = project(cam, (b[6] + b[7]) / 2)
        outside.append(not (0 < q.x < 1 and 0 < q.y < 1))
        depth.extend(abs(project(cam, p).z - 1.0) < 1e-4 for p in b)
        tip = cam.matrix_world.inverted() @ ((b[1] + b[2]) / 2)
        on_circle.append(abs(math.hypot(tip.x, tip.y) / hw - 0.3) < 1e-4)
    check("集中線: 根元は画面の端 (ゲートの縁)", all(border), str(border))
    check("集中線: 延長は画面の外", all(outside), str(outside))
    check("集中線: 線はカメラの前、距離 1 の面", all(depth))
    check("集中線: 先端は円の縁 (長さ 1 なら円の中に入らない)", all(on_circle), str(on_circle))

    # 円を動かす・キーを打つ (作り直さずに向きが変わる)
    ptr = lines.as_pointer()
    focus.location = (0.4, 0.1, 0.0)
    focus.scale = (0.2, 0.4, focus.scale[2])
    focus.keyframe_insert("location", frame=1)
    focus.keyframe_insert("scale", frame=1)
    focus.location = (-0.3, -0.2, 0.0)
    focus.scale = (0.5, 0.25, focus.scale[2])
    focus.keyframe_insert("location", frame=11)
    focus.keyframe_insert("scale", frame=11)
    goto(6)
    center = (focus.location.x, focus.location.y)
    radius = (focus.scale.x, focus.scale.y)
    check("集中線: キーの途中 (フレーム 6) の円", not near(center, (0.4, 0.1)) and not near(center, (-0.3, -0.2)),
          str(center))
    bad = focus_mismatch(lines, v, center, radius, angles, [1.0] * 24, thick)
    check("集中線: 円にキー → 線の向かう先が付いてくる (楕円も)", not bad and bfl.part(root, bfl.LINES_PART).as_pointer() == ptr,
          str(bad[:2]))
    co = frame_verts(lines)
    inside = []
    for i in range(24):
        t = (co[i * bfl.LINE_VERTS + 1] + co[i * bfl.LINE_VERTS + 2]) / 2
        inside.append(((t[0] - center[0]) / radius[0]) ** 2 + ((t[1] - center[1]) / radius[1]) ** 2 < 1 - 1e-4)
    check("集中線: 楕円の中には入らない", not any(inside))
    # キーの無い円をフレームを変えずに動かす
    focus.animation_data_clear()
    focus.location = (0.1, -0.1, 0.0)
    focus.scale = (0.3, 0.3, focus.scale[2])
    bpy.context.view_layer.update()
    bad = focus_mismatch(lines, v, (0.1, -0.1), (0.3, 0.3), angles, [1.0] * 24, thick)
    check("集中線: 円を動かすと (フレームを変えなくても) 付いてくる", not bad, str(bad[:2]))

    # すぐ反映: 長さ・太さ・先端の形・色・距離
    root["dfx_length"] = 0.5
    root["dfx_thickness"] = 0.05
    root["dfx_tip_shape"] = mfl.TIP_ROUND
    root["dfx_tip_width"] = 0.6
    root["dfx_color"] = (1.0, 0.0, 0.0)
    root["dfx_opacity"] = 0.4
    root["dfx_distance"] = 3.0
    goto(7)
    v = rt.read_params(root, mfl.stored_params())
    bad = focus_mismatch(lines, v, (0.1, -0.1), (0.3, 0.3), angles, [1.0] * 24, thick)
    check("即反映: 長さ・太さ・丸い先端 (次のフレーム)", not bad, str(bad[:2]))
    mat = lines.data.materials[0]
    col = mat.node_tree.nodes["dfx_emission"].inputs["Color"].default_value
    check("即反映: 色・不透明度 (光の影響を受けない)", near(col[:3], (1, 0, 0))
          and abs(mat.node_tree.nodes["dfx_mix"].inputs[0].default_value - 0.4) < 1e-6)
    wv = world_verts(lines)
    check("即反映: カメラからの距離 3 (画面を覆ったまま)",
          all(abs(project(cam, p).z - 3.0) < 1e-3 for p in wv)
          and all(on_border(project(cam, (wv[i * bfl.LINE_VERTS] + wv[i * bfl.LINE_VERTS + 3]) / 2)) for i in range(24)))

    # ズーム: 焦点距離を変えても (キーでも) 画面の端を覆う
    cam.data.lens = 50
    cam.data.keyframe_insert("lens", frame=1)
    cam.data.lens = 20
    cam.data.keyframe_insert("lens", frame=21)
    for f in (3, 15):
        goto(f)
        wv = world_verts(lines)
        ok = all(on_border(project(cam, (wv[i * bfl.LINE_VERTS] + wv[i * bfl.LINE_VERTS + 3]) / 2)) for i in range(24))
        check("ズーム: 焦点距離 %.1f でも根元はゲートの縁" % cam.data.lens, ok)
    # 縦長の画面・正投影
    bpy.context.scene.render.resolution_x = 1080
    bpy.context.scene.render.resolution_y = 1920
    goto(16)
    wv = world_verts(lines)
    check("縦長の画面 (センサーは縦に合う) でも根元はゲートの縁",
          all(on_border(project(cam, (wv[i * bfl.LINE_VERTS] + wv[i * bfl.LINE_VERTS + 3]) / 2)) for i in range(24)))
    bpy.context.scene.render.resolution_x = 1920
    bpy.context.scene.render.resolution_y = 1080
    cam.data.type = "ORTHO"
    goto(17)
    wv = world_verts(lines)
    check("正投影のカメラでも根元はゲートの縁",
          all(on_border(project(cam, (wv[i * bfl.LINE_VERTS] + wv[i * bfl.LINE_VERTS + 3]) / 2)) for i in range(24)))
    cam.data.type = "PERSP"
    # カメラを動かすと付いてくる (フレームを変えなくても)
    cam.animation_data_clear()
    cam.data.animation_data_clear()
    cam.location.x += 5
    cam.rotation_euler.z += 0.3
    bpy.context.view_layer.update()
    wv = world_verts(lines)
    check("カメラを動かすと付いてくる",
          all(on_border(project(cam, (wv[i * bfl.LINE_VERTS] + wv[i * bfl.LINE_VERTS + 3]) / 2)) for i in range(24))
          and all(abs(project(cam, p).z - 3.0) < 1e-3 for p in wv))

    # 更新: 本数を変えて作り直す (円はそのまま)
    focus.keyframe_insert("location", frame=1)
    root["dfx_count"] = 10
    select(root)
    op(bscene.UpdateEffectsTool)
    lines = bfl.part(root, bfl.LINES_PART)
    focus2 = rt.control(root, "focus")
    check("更新: 10 本で作り直し、円 (位置・キー) はそのまま新しい _frame の子",
          len(lines.data.vertices) == 10 * bfl.LINE_VERTS and focus2 == focus
          and focus.parent == bfl.part(root, bfl.FRAME_PART) and near(focus.location[:2], (0.1, -0.1))
          and focus.animation_data is not None)
    n_objects, n_meshes, n_mats = len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)
    op(bscene.UpdateEffectsTool)
    check("更新: 繰り返しても数が増えない", (n_objects, n_meshes, n_mats)
          == (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)))
    lines = bfl.part(root, bfl.LINES_PART)
    goto(2)
    v = rt.read_params(root, mfl.stored_params())
    layout = mfl.line_layout(10, aspect(), v["seed"], v["length_random"], v["thickness_random"])
    bad = focus_mismatch(lines, v, (0.1, -0.1), (0.3, 0.3), [l[0] for l in layout], [l[2] for l in layout],
                         [l[3] for l in layout])
    check("更新後も Maya 版と同じ形", not bad, str(bad[:2]))


@section
def l03_focus_motion():
    new_scene()
    cam = scene_camera()
    select(cam)
    op(bfl.FocusLinesTool, count=16, motion=mfl.MOTION_FLICKER, flicker_interval=2, length_random=0.8,
       length=0.9, seed=7)
    root = bpy.data.objects.get("dfx_focusLines1")
    lines = bfl.part(root, bfl.LINES_PART)
    v = rt.read_params(root, mfl.stored_params())
    layout = mfl.line_layout(16, aspect(), v["seed"], v["length_random"], v["thickness_random"])
    angles, thick = [l[0] for l in layout], [l[3] for l in layout]
    times = mfl._frames(1, 60, 2)
    bad, tips = [], {}
    for f in (1, 2, 3, 8, 59, 60):
        goto(f)
        k = (f - 1) // 2
        lens = [mfl.flicker_lengths(v["seed"], i, len(times), v["length_random"])[k] for i in range(16)]
        bad += [(f,) + b for b in focus_mismatch(lines, v, (0.0, 0.0), (0.3, 0.3), angles, lens, thick)]
        tips[f] = frame_verts(lines)[1::bfl.LINE_VERTS].copy()
    check("ちらつき: Maya 版のキーと同じ長さ (間隔 2 フレーム)", not bad, str(bad[:2]))
    check("ちらつき: 間隔の中は同じ・次の間隔で変わる",
          near(tips[1].ravel(), tips[2].ravel(), 1e-9) and not near(tips[2].ravel(), tips[3].ravel(), 1e-6))

    op(bfl.FocusLinesTool, count=12, motion=mfl.MOTION_GROW, grow_frames=6, length_random=0.3, seed=3)
    root = bpy.data.objects.get("dfx_focusLines2")
    lines = bfl.part(root, bfl.LINES_PART)
    v = rt.read_params(root, mfl.stored_params())
    layout = mfl.line_layout(12, aspect(), v["seed"], v["length_random"], v["thickness_random"])
    thick = [l[3] for l in layout]
    sched = mfl.grow_schedule(v["seed"], 12, 1, 60, 6, aspect(), v["length_random"])
    bad, state = [], {}
    for f in (1, 2, 4, 7, 12, 33, 60):
        goto(f)
        angles, lens = [], []
        for cycles in sched:
            t, a, _, l = [c for c in cycles if c[0] <= f][-1]
            k = f - t
            angles.append(a)
            lens.append(l * (k + 1) / 6.0 if k < 6 else l)
        state[f] = (angles, lens)
        bad += [(f,) + b for b in focus_mismatch(lines, v, (0.0, 0.0), (0.3, 0.3), angles, lens, thick)]
    check("伸びて移動: Maya 版のキーと同じ (伸びて、伸びきったら別の向きへ)", not bad, str(bad[:2]))
    moved = sum(1 for a, b in zip(state[1][0], state[12][0]) if abs(a - b) > 1e-6)
    grew = sum(1 for a, b in zip(state[1][1], state[2][1]) if b > a)
    check("伸びて移動: 向きが変わる線・伸びる線がある", moved == 12 and grew >= 6, str((moved, grew)))


# ---------------------------------------------------------------- スピードライン
def expected_speed(v, f, band, hb, zc, seeds):
    """各線の頂点 (上側・下側) を Maya 版の関数で。"""
    region = msl.plane_region(aspect(), zc, band, v.get("reach"))
    ex, ey, _ = msl.plane_axes(band[2], band[3], band[4])
    n = msl.WIDTH_SAMPLES
    out = []
    for i, (ph, sp, th) in enumerate(seeds):
        u0, y, length = msl.line_state(f, i, ph, sp, th, v, region, hb)
        pts = []
        for side in (1, -1):
            for j in range(n):
                x = j / float(n - 1)
                u = u0 + x * length
                w = y + side * msl.width_at(x, v["width_start"], v["width_mid"], v["width_end"]) * th
                pts.append(tuple(band[k] * (k < 2) + ex[k] * u + ey[k] * w for k in range(3)))
        out.append(pts)
    return out, region


def speed_mismatch(lines, v, f, band, hb, zc, seeds, tol=1e-5):
    co = frame_verts(lines)
    exp, _ = expected_speed(v, f, band, hb, zc, seeds)
    bad = []
    for i, pts in enumerate(exp):
        got = co[i * bsl.LINE_VERTS:(i + 1) * bsl.LINE_VERTS]
        if not all(near(g, e, tol) for g, e in zip(got, pts)):
            bad.append((i, tuple(got[0]), pts[0]))
    return bad


def band_tuple(band):
    r = band.rotation_euler
    return (band.location.x, band.location.y, math.degrees(r.x), math.degrees(r.y), math.degrees(r.z))


@section
def l04_speed_lines():
    new_scene()
    cam = scene_camera()
    select(cam)
    res = op(bsl.SpeedLinesTool, count=20, band_width=1.0, band_height=0.4, speed=0.3, seed=5)
    root = bpy.data.objects.get("dfx_speedLines1")
    band = rt.control(root, "band") if root else None
    lines = bfl.part(root, bfl.LINES_PART) if root else None
    check("スピードライン: ルート・四角・線 (20 本で 1 つのメッシュ)", res == {"FINISHED"} and band is not None
          and lines is not None and len(lines.data.vertices) == 20 * bsl.LINE_VERTS
          and near(band.scale[:2], (0.5, 0.2)) and band.parent == bfl.part(root, bfl.FRAME_PART))
    v = rt.read_params(root, msl.stored_params())
    seeds = msl.line_seeds(20, v["seed"], v["speed_random"], v["thickness_random"])
    zc = msl.camera_depth(cam.data.lens, cam.data.sensor_width / 25.4)
    check("スピードライン: カメラの位置 (_frame の座標) は Maya 版の camera_depth と同じ",
          abs(bfl.camera_depth(cam) - zc) < 1e-9)
    bad, u_first = [], {}
    for f in (1, 2, 3, 9, 40):
        goto(f)
        bad += [(f,) + b for b in speed_mismatch(lines, v, f, band_tuple(band), 0.2, zc, seeds)]
        u_first[f] = frame_verts(lines)[0::bsl.LINE_VERTS, 0].copy()
    check("スピードライン: Maya 版と同じ位置・長さ・太さ", not bad, str(bad[:2]))
    fwd = sum(1 for a, b in zip(u_first[1], u_first[2]) if b > a)
    check("スピードライン: フレームごとに左 → 右へ流れる (通り過ぎたら左から)", fwd >= 15, str(fwd))
    goto(9)
    _, region = expected_speed(v, 9, band_tuple(band), 0.2, zc, seeds)
    co = frame_verts(lines)
    wmax = max(v["width_start"], v["width_mid"], v["width_end"])
    outside_band, in_region = [], []
    for i, (_, _, th) in enumerate(seeds):
        b = co[i * bsl.LINE_VERTS:(i + 1) * bsl.LINE_VERTS]
        y = (b[0][1] + b[-1][1]) / 2
        outside_band.append(abs(y) >= 0.2 + wmax * th - 1e-6)
        in_region.append(region[2] - 1e-6 <= y <= region[3] + 1e-6)
    check("スピードライン: 四角の帯の中には入らない", all(outside_band), str(outside_band))
    check("スピードライン: 上下は画面の範囲の中", all(in_region), str(in_region))
    wv = world_verts(lines)
    check("スピードライン: 線はカメラの前、距離 1 の面", all(abs(project(cam, p).z - 1.0) < 1e-4 for p in wv))
    hit = 0
    for f in range(1, 30):
        goto(f)
        wv = world_verts(lines)
        hit += sum(1 for p in wv if 0 < project(cam, p).x < 1 and 0 < project(cam, p).y < 1)
    check("スピードライン: 線が画面の中を通る", hit > 0, str(hit))

    # 四角を回す・傾ける・キーを打つ (作り直さずに流れる向きが変わる)
    band.rotation_euler = (0.0, 0.0, math.radians(90))
    goto(10)
    bad = speed_mismatch(lines, v, 10, band_tuple(band), 0.2, zc, seeds)
    check("スピードライン: 四角を Z 回転 90° → 下から上へ流れる", not bad, str(bad[:2]))
    band.rotation_euler = (math.radians(10), math.radians(20), math.radians(30))
    m = band.matrix_basis.to_3x3()
    axes = msl.plane_axes(10, 20, 30)
    check("スピードライン: 四角の軸 = plane_axes (回転 XYZ)",
          all(near(m.col[k].normalized(), axes[k], 1e-6) for k in range(3)))
    band.location = (0.2, 0.1, 0.0)
    band.rotation_euler = (math.radians(25), math.radians(-30), math.radians(15))
    band.keyframe_insert("location", frame=1)
    band.keyframe_insert("rotation_euler", frame=1)
    band.location = (-0.2, 0.0, 0.0)
    band.rotation_euler = (math.radians(-10), math.radians(35), math.radians(-20))
    band.scale = (0.6, 0.3, band.scale[2])
    band.keyframe_insert("location", frame=21)
    band.keyframe_insert("rotation_euler", frame=21)
    band.keyframe_insert("scale", frame=21)
    bad = []
    for f in (5, 14):
        goto(f)
        bad += [(f,) + b for b in speed_mismatch(lines, v, f, band_tuple(band), abs(band.scale.y), zc, seeds)]
    check("スピードライン: 四角を傾ける・キーを打つと付いてくる (パース)", not bad, str(bad[:2]))
    wv = world_verts(lines)
    check("スピードライン: 傾けても線はカメラの前", all(project(cam, p).z > 0 for p in wv))

    # すぐ反映: 速さ・長さ・太さ・距離
    root["dfx_speed"] = 0.5
    root["dfx_length"] = 1.5
    root["dfx_width_mid"] = 0.03
    root["dfx_length_random"] = 0.0
    root["dfx_distance"] = 2.0
    goto(15)
    v = rt.read_params(root, msl.stored_params())
    bad = speed_mismatch(lines, v, 15, band_tuple(band), abs(band.scale.y), zc, seeds)
    check("即反映: 速さ・長さ・太さ (次のフレーム)", not bad, str(bad[:2]))
    check("即反映: カメラからの距離 2", (bfl.part(root, bfl.FRAME_PART).matrix_world.translation
                                     - cam.matrix_world @ Vector((0, 0, -2))).length < 1e-4)

    # ズーム: 焦点距離を変えると面の上の画面の範囲も変わる
    cam.data.lens = 24
    goto(16)
    zc2 = msl.camera_depth(24, cam.data.sensor_width / 25.4)
    bad = speed_mismatch(lines, v, 16, band_tuple(band), abs(band.scale.y), zc2, seeds)
    check("ズーム: 焦点距離 24 でも Maya 版と同じ (画面の範囲を計算し直す)", not bad, str(bad[:2]))

    # 更新: 本数を変えて作り直す (四角はそのまま)
    root["dfx_count"] = 8
    root["dfx_seed"] = 9
    select(root)
    op(bscene.UpdateEffectsTool)
    lines = bfl.part(root, bfl.LINES_PART)
    check("更新: 8 本で作り直し、四角 (回転・キー) はそのまま",
          len(lines.data.vertices) == 8 * bsl.LINE_VERTS and rt.control(root, "band") == band
          and band.animation_data is not None and band.parent == bfl.part(root, bfl.FRAME_PART))
    goto(18)
    v = rt.read_params(root, msl.stored_params())
    seeds = msl.line_seeds(8, v["seed"], v["speed_random"], v["thickness_random"])
    bad = speed_mismatch(lines, v, 18, band_tuple(band), abs(band.scale.y), zc2, seeds)
    check("更新後も Maya 版と同じ", not bad, str(bad[:2]))


@section
def l06_settles():
    """更新が更新を呼び続けない (画面のある Blender で計算し続けないか)。"""
    calls = []
    saved = dict(rt.UPDATERS)
    try:
        for key, fn in saved.items():
            rt.UPDATERS[key] = (lambda f: lambda root, frame: (calls.append(root.name), f(root, frame)))(fn)
        roots = [r for r in rt.effect_roots() if r[rt.TYPE_KEY] in (mfl.EFFECT_TYPE, msl.EFFECT_TYPE)]
        cam = bpy.data.objects.get("shotCam")
        for r in roots:
            r["dfx_distance"] = 1.7   # Python から書き換え (ドライバーは自動では動かない)
        goto(20)
        for _ in range(3):
            bpy.context.view_layer.update()
        del calls[:]
        for _ in range(3):
            bpy.context.view_layer.update()
        settled = not calls
        frames_ok = all((bfl.part(r, bfl.FRAME_PART).matrix_world.translation
                            - cam.matrix_world @ Vector((0, 0, -1.7))).length < 1e-4 for r in roots)
        rt.control(roots[0], "focus" if roots[0][rt.TYPE_KEY] == mfl.EFFECT_TYPE else "band").location.x += 0.05
        bpy.context.view_layer.update()
        moved = len(calls)
        del calls[:]
        for _ in range(3):
            bpy.context.view_layer.update()
        check("更新が落ち着く (ずっと計算し続けない)", roots and settled and moved > 0 and not calls, str((moved, calls)))
        check("Python から変えた距離も _frame に反映", frames_ok)
    finally:
        rt.UPDATERS.clear()
        rt.UPDATERS.update(saved)


@section
def l05_delete_all():
    op(bfl.FocusLinesTool, count=5)
    op(bscene.DeleteAllTool)
    check("全削除: 集中線・スピードラインで作った物が全部消える (カメラは残る)",
          not [o for o in bpy.data.objects if o.get(rt.TAG_KEY)]
          and not [m for m in bpy.data.meshes if m.get(rt.TAG_KEY)]
          and bpy.data.objects.get("shotCam") is not None)
    bpy.ops.wm.save_mainfile()
    check("保存できる", os.path.isfile(os.path.join(TMP, "verify_lines.blend")))


for fn in (l01_register, l02_focus_create, l03_focus_motion, l04_speed_lines, l06_settles, l05_delete_all):
    fn()
fails = [r for r in RESULTS if not r[1]]
print("\nSUMMARY: %d passed, %d failed" % (len(RESULTS) - len(fails), len(fails)))
for name, _, detail in fails:
    print("  FAIL:", name, detail)
sys.stdout.flush()
os._exit(1 if fails else 0)
