# -*- coding: utf-8 -*-
"""Blender 版の検証 (Blender を画面なしで動かす)。

    blender -b --factory-startup --python tests/blender_verify.py -- <リポジトリのフォルダ>

Maya 版と同じ計算になるか (弾の位置・衝撃波の広がり)、更新・書き出し・全削除を確かめる。
"""
import math
import os
import sys
import tempfile
import traceback

REPO = sys.argv[sys.argv.index("--") + 1]
sys.path.insert(0, REPO)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

RESULTS = []
TMP = tempfile.mkdtemp(prefix="dfx_blender_")


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
    # 保存しておく (ツールが作る絵は .blend の隣に置かれる。ユーザーのフォルダに書かない)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(TMP, "verify.blend"))


import dummy_effect_maker.blender as dfx  # noqa: E402
from dummy_effect_maker.blender import runtime as rt, ui  # noqa: E402

dfx.register()
from dummy_effect_maker.blender.tools import scene as bscene, shockwave as bsw, vulcan as bv  # noqa: E402
from dummy_effect_maker.tools import homing, shockwave as sw, vulcan as mv  # noqa: E402
from dummy_effect_maker.core import exchange as xc  # noqa: E402


def op(tool_cls, **values):
    """ツールのオペレーターを、オプションを指定して実行する (ボタンと同じ)。"""
    tool = [t for t in ui._TOOLS if isinstance(t, tool_cls)][0]
    tool.reset_values()
    tool.set_values(values)
    idname = ui.operator_idname(tool)
    cat, name = idname.split(".")
    return getattr(getattr(bpy.ops, cat), name)()


@section
def b01_register():
    labels = [t.label for t in ui._TOOLS]
    check("登録: バルカン砲・衝撃波・シーンのツール",
          set([u"バルカン砲", u"衝撃波", u"エフェクトを更新", u"ダミーエフェクトを全削除", u"書き出し (Unreal 用)"])
          <= set(labels), str(labels))
    check("登録: ホストは blender", __import__("dummy_effect_maker.core.host", fromlist=["x"]).detect().name == "blender")
    ops = [ui.operator_idname(t) for t in ui._TOOLS]
    check("登録: オペレーターができる", all(hasattr(getattr(bpy.ops, o.split(".")[0]), o.split(".")[1]) for o in ops))


@section
def b02_vulcan():
    new_scene()
    res = op(bv.VulcanTool, start_frame=1.0, end_frame=20.0, interval=5.0, length=20.0)
    root = bpy.data.objects.get("dfx_vulcan1")
    check("作成: ルート・コントロール・弾 4 発", res == {"FINISHED"} and root is not None
          and rt.control(root, "muzzle") and rt.control(root, "target") and len(bv.bullets(root)) == 4)
    check("作成: パラメータはカスタムプロパティ (色・選択肢も)",
          root["dfx_interval"] == 5.0 and list(root["dfx_color"]) == list(mv.VulcanTool.default_values()["color"])
          and root["dfx_shape"] == homing.SHAPE_SQUASHED)
    target = rt.control(root, "target")
    # 目標にキー: 発射時点の位置へ飛ぶ (後から動かしても飛んでいる弾は変わらない)
    target.location = (20, 0, 0)
    target.keyframe_insert("location", frame=1)
    target.location = (20, 10, 0)
    target.keyframe_insert("location", frame=11)
    v = mv.VulcanTool.default_values()
    v.update(start_frame=1.0, end_frame=20.0, interval=5.0)
    bad = []
    for f in (1, 3, 6, 8, 12, 15, 19):
        bpy.context.scene.frame_set(f)
        for i, b in enumerate(bv.bullets(root)):
            fire = 1 + 5 * i
            vis = mv.bullet_visible(f, fire, v["travel_frames"])
            if vis != (b.scale.length > 1e-9):
                bad.append((f, i, "vis"))
                continue
            if not vis:
                continue
            t0 = (20, 10 * (fire - 1) / 10.0 if fire < 11 else 10, 0)
            p = mv.bullet_position((0, 0, 0), t0, f, fire, v["travel_frames"], v["speed_mode"],
                                   v["min_speed"] / 100.0)
            if not near(b.matrix_world.translation, p, 1e-4):
                bad.append((f, i, tuple(b.matrix_world.translation), p))
    check("バルカン砲: Maya 版と同じ計算 (発射時点の目標へまっすぐ。表示も同じ)", not bad, str(bad[:2]))
    bpy.context.scene.frame_set(3)
    b0 = bv.bullets(root)[0]
    fwd = b0.matrix_world.to_3x3().col[0].normalized()
    check("弾の向き: 進む向きにローカル X", near(fwd, (Vector((20, 2, 0)) - Vector((0, 0, 0))).normalized(), 1e-3)
          or near(fwd, Vector((20, 0, 0)).normalized(), 1e-3), str(tuple(fwd)))
    root["dfx_radius"] = 0.5
    root["dfx_shape"] = homing.SHAPE_SPHERE
    bpy.context.scene.frame_set(4)
    check("即反映: 半径・形 (カスタムプロパティを変えて次のフレーム)", near(b0.scale, (0.5, 0.5, 0.5)), str(tuple(b0.scale)))
    root["dfx_interval"] = 2.0
    for o in bpy.context.selected_objects:
        o.select_set(False)
    root.select_set(True)
    bpy.context.view_layer.objects.active = root
    op(bscene.UpdateEffectsTool)
    check("更新: 間隔 2 で弾 10 発、目標のキーはそのまま",
          len(bv.bullets(root)) == 10 and rt.control(root, "target").animation_data is not None)
    n_objects = len(bpy.data.objects)
    n_meshes = len(bpy.data.meshes)
    op(bscene.UpdateEffectsTool)
    check("更新: 繰り返しても数が増えない", n_objects == len(bpy.data.objects) and n_meshes == len(bpy.data.meshes))


@section
def b03_shockwave():
    new_scene()
    op(bsw.ShockwaveTool, count=2, interval=3.0, distance=10.0)
    root = bpy.data.objects.get("dfx_shockwave1")
    rings = bsw.rings(root)
    check("作成: 中心・端・輪 2 つ (中心の子)", len(rings) == 2 and all(r.parent == rt.control(root, "center") for r in rings))
    v = sw.ShockwaveTool.default_values()
    v.update(count=2, interval=3.0)
    bad = []
    for f in (1, 2, 4, 6, 9, 13, 20):
        bpy.context.scene.frame_set(f)
        for i, ring in enumerate(rings):
            r, w, vis = sw.ring_state(f - sw.ring_start(v, i), v, 10.0)
            if not vis or r < 1e-6:
                if ring.scale.length > 1e-9:
                    bad.append((f, i, "shown"))
                continue
            s = max(r, 1e-4)
            key = ring.data.shape_keys.key_blocks["thick"].value
            if not near(ring.scale, (s, s, s * v["flatten"])) or abs(key - min(1, max(0, (w / s - sw.BASE_TUBE) / (1 - sw.BASE_TUBE)))) > 1e-4:
                bad.append((f, i, tuple(ring.scale), r))
    check("衝撃波: Maya 版と同じ広がり方 (半径・太さ・表示)", not bad, str(bad[:2]))
    edge = rt.control(root, "edge")
    edge.location = (0, 5, 0)
    bpy.context.scene.frame_set(30)
    bpy.context.scene.frame_set(25)
    r, w, vis = sw.ring_state(25 - 1, v, 5.0)
    check("端を動かすと広がる大きさが変わる", not vis or near(rings[0].scale[:2], (r, r)))


@section
def b04_export():
    new_scene()
    op(bv.VulcanTool, start_frame=1.0, end_frame=10.0, interval=5.0, length=20.0)
    op(bsw.ShockwaveTool)
    root = bpy.data.objects.get("dfx_vulcan1")
    rt.control(root, "target").location = (20, 3, 4)
    center = rt.control(bpy.data.objects.get("dfx_shockwave1"), "center")
    center.rotation_euler = (math.radians(90), 0, 0)
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("shotCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    cam.location = (0, -20, 3)
    path = os.path.join(tempfile.mkdtemp(), "shot")
    bpy.context.scene.frame_set(1)
    op(bscene.ExportShotTool, path=path, range_mode=bscene.RANGE_CUSTOM, start_frame=1.0, end_frame=12.0)
    shot = xc.read_shot(path + ".json")
    effects = dict((e["name"], e) for e in shot["effects"])
    v = effects["dfx_vulcan1"]
    tgt = [c for c in v["controls"] if c["role"] == "target"][0]
    check("書き出し: Blender から同じ形式 (アプリ・空間・時間)", shot["source"]["app"] == "blender"
          and shot["space"] == xc.SPACE and shot["time"]["start"] == 1 and shot["time"]["end"] == 12)
    check("書き出し: 種類・パラメータ・コントロール (Z が上・メートルのまま)",
          v["type"] == "vulcan" and v["params"]["interval"] == 5.0 and tgt["track"]["static"]
          and near(tgt["track"]["t"], (20, 3, 4), 1e-6), str(tgt["track"].get("t")))
    b = v["data"]["bullets"][0]["track"]
    i = b["frames"].index(3)
    p = mv.bullet_position((0, 0, 0), (20, 3, 4), 3, 1, 6.0, mv.VulcanTool.default_values()["speed_mode"], 1.0)
    check("書き出し: 弾の通り道と表示", near(b["t"][i], p, 1e-4) and b["visible"][i] is True
          and b["visible"][b["frames"].index(12)] is False, str(b["t"][i]))
    n = effects["dfx_shockwave1"]["data"]["ring_normal"]
    check("書き出し: 衝撃波の輪の向き (中心の X 90° → -Y)", n["static"] and near(n["normal"], (0, -1, 0), 1e-6),
          str(n.get("normal")))
    c = shot["cameras"][0]
    check("書き出し: カメラ (位置・焦点距離)", c["name"] == "shotCam" and near(c["track"]["t"], (0, -20, 3), 1e-6)
          and abs(c["track"]["focal_length"] - cam_data.lens) < 1e-6)
    check("書き出し後もフレームは元のまま", bpy.context.scene.frame_current == 1)


# ---------------------------------------------------------------- 天候
def scene_camera(loc=(0, -25, 4), rot=(math.radians(85), 0, 0)):
    cam = bpy.data.objects.new("shotCam", bpy.data.cameras.new("cam"))
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    cam.location = loc
    cam.rotation_euler = rot
    return cam


def select(*objs):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def expected_verts(obj, placement):
    """粒 1 つの頂点の期待値 (Y が上の計算結果を Z が上に直して、型を置く)。"""
    from dummy_effect_maker import motion as mo
    p, axes, s = placement
    tmpl = list(obj.data["dfx_template"])
    out = []
    for k in range(len(tmpl) // 3):
        t = tmpl[3 * k:3 * k + 3]
        v = Vector(mo.zup_vector(p))
        for a in range(3):
            v += Vector(mo.zup_vector(axes[a])) * (s[a] * t[a])
        out.append(tuple(v))
    return out


def same_particle(obj, i, placement, tol=1e-4):
    from dummy_effect_maker.blender.tools import _wx
    got = _wx.read_particle(obj, i)
    return all(near(a, b, tol) for a, b in zip(got, expected_verts(obj, placement)))


def particle_size(obj, i):
    pts = [Vector(p) for p in __import__("dummy_effect_maker.blender.tools._wx", fromlist=["x"]).read_particle(obj, i)]
    return max((p - pts[0]).length for p in pts)


@section
def b06_wind():
    from dummy_effect_maker.blender.tools import _wx, wind as bwind
    new_scene()
    scene_camera()
    op(bwind.WindTool, strength=0.2, gust=0.0, turbulence=0.0)
    w = bpy.data.objects.get("dfx_wind1")
    check("風: ルート (動かせる)・矢印・表 (Z が上)", w is not None and not w.lock_rotation[2]
          and bpy.data.objects.get("dfx_wind1_arrow") is not None
          and len(w["dfxWindX"]) == 60 + 400 + 60 and w["dfxDriftStart"] == 1 - 400)
    check("風: 向き +X、強さ 0.2", abs(w["dfxWindX"][10] - 0.2) < 1e-9 and abs(w["dfxWindY"][10]) < 1e-9
          and abs(w["dfxWindZ"][10]) < 1e-9, str((w["dfxWindX"][10], w["dfxWindY"][10])))
    w.rotation_euler = (0, 0, math.radians(90))
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(2)
    check("風: 矢印を回すと自動で計算し直す (+Y へ)", abs(w["dfxWindY"][10] - 0.2) < 1e-6
          and abs(w["dfxWindX"][10]) < 1e-6, str((w["dfxWindX"][10], w["dfxWindY"][10])))
    w["dfx_strength"] = 0.1
    w.keyframe_insert('["dfx_strength"]', frame=1)
    w["dfx_strength"] = 0.5
    w.keyframe_insert('["dfx_strength"]', frame=31)
    bpy.context.scene.frame_set(3)
    i31 = 31 - int(w["dfxDriftStart"])
    check("風: 強さのキーを読む", abs(w["dfxWindY"][i31] - 0.5) < 1e-6, str(w["dfxWindY"][i31]))
    wd = _wx.wind_of({"dfx_wind": w, "dfx_wind_influence": 1.0})
    check("風: Y が上に直して読む (+Y → -Z)", near(wd.velocity_at(31), (0, 0, -0.5), 1e-6), str(wd.velocity_at(31)))


@section
def b07_rain_snow():
    from dummy_effect_maker.blender.tools import _wx, weather as bw, wind as bwind
    from dummy_effect_maker.tools import weather as mw
    from dummy_effect_maker import motion as mo
    new_scene()
    cam = scene_camera()
    select(cam)
    op(bw.RainTool, count=30, area_width=10.0, area_height=8.0)
    root = bpy.data.objects.get("dfx_rain1")
    drops, splashes = _wx.part(root, "_drops"), _wx.part(root, "_splashes")
    check("雨: ルート・範囲の箱・線とはね (1 つのメッシュに 30 粒ずつ)", root is not None and rt.control(root, "area")
          and drops.data["dfx_count"] == 30 and len(drops.data.vertices) == 120
          and root.get("dfx_camera") == cam)
    area = rt.control(root, "area")
    bpy.context.scene.frame_set(5)
    inside = all(-5.6 <= v.co.x <= 5.6 and -5.6 <= v.co.y <= 5.6 and -0.01 <= v.co.z <= 8 + 1.2 + 0.01
                 for v in drops.data.vertices)
    check("雨: 範囲の箱の中に降る", inside)
    vis = [i for i in range(30) if particle_size(drops, i) > 1e-6]
    d = Vector(_wx.read_particle(drops, vis[0])[3]) - Vector(_wx.read_particle(drops, vis[0])[0])
    check("雨: 線は下 (-Z) に伸びる (風なし)", near(d.normalized(), (0, 0, -1), 1e-6) and abs(d.length - 1.2) < 1e-6,
          str(tuple(d)))
    rings = [i for i in range(30) if particle_size(splashes, i) > 1e-6]
    check("雨: はねは地面 (Z = 0) に寝た輪", rings and all(abs(p[2]) < 1e-6 for i in rings for p in _wx.read_particle(splashes, i)),
          str(len(rings)))
    # 箱を回して動かす・風をつなぐ → motion.py と同じ (Y が上に直した計算)
    area.location = (3, 2, 4)
    area.rotation_euler = (0, 0, math.radians(30))
    select()
    op(bwind.WindTool, strength=0.15)
    check("風を作ると、風のない雨がつながる", root.get("dfx_wind") == bpy.data.objects.get("dfx_wind1"))
    v = rt.read_params(root, mw.RAIN.stored_params())
    bad = []
    for f in (4, 11, 23):
        bpy.context.scene.frame_set(f)
        am = _wx.yup(area.matrix_world)
        ai = mo.inverse(am)
        cp, cdir = _wx.camera_yup(root)
        wind = _wx.wind_of(root)
        for i in range(30):
            st = mo.falling(i, f, v, am, ai, wind)
            pl = (st["p"], mo.streak(st["p"], st["vel"], cp, cdir), (v["width"], v["length"], 1)) if st["vis"] else _wx.hidden(st["p"])
            if not same_particle(drops, i, pl):
                bad.append(("drop", f, i))
            p, size, sv = mo.splash(i, f, v, am, ai, wind)
            if not same_particle(splashes, i, (p, _wx.IDENTITY_AXES, (size,) * 3) if sv else _wx.hidden(p)):
                bad.append(("splash", f, i))
    check("雨: Maya 版と同じ計算 (回した箱・風)", not bad, str(bad[:3]))
    before = _wx.read_particle(drops, vis[0])[0]
    area.location.x += 5
    bpy.context.view_layer.update()
    after = _wx.read_particle(drops, vis[0])[0]
    check("雨: 箱を動かすと (フレームを変えなくても) 付いてくる",
          abs((Vector(after) - Vector(before)).length - 5) < 1e-3 or abs(after[0] - before[0] - 5) < 1e-3,
          str((before, after)))
    root["dfx_amount"] = 0.0
    bpy.context.scene.frame_set(24)
    check("雨: 量 0 で全部消える", all(particle_size(drops, i) < 1e-9 for i in range(30)))
    root["dfx_count"] = 12
    select(root)
    op(bscene.UpdateEffectsTool)
    check("雨: 更新で数が変わる (箱はそのまま)", _wx.part(root, "_drops").data["dfx_count"] == 12
          and abs(rt.control(root, "area").location.x - 8) < 1e-6)

    select(cam)
    op(bw.SnowTool, count=20)
    snow = bpy.data.objects.get("dfx_snow1")
    flakes = _wx.part(snow, "_flakes")
    v = rt.read_params(snow, mw.SNOW.stored_params())
    bad = []
    for f in (6, 17):
        bpy.context.scene.frame_set(f)
        am = _wx.yup(rt.control(snow, "area").matrix_world)
        cp, cdir = _wx.camera_yup(snow)
        for i in range(20):
            st = mo.falling(i, f, v, am, mo.inverse(am), _wx.wind_of(snow), sway=True)
            sz = mo.snow_size(i, v)
            if not same_particle(flakes, i, (st["p"], mo.billboard(st["p"], cp, cdir), (sz,) * 3)):
                bad.append((f, i))
    check("雪: Maya 版と同じ計算 (揺れ・風)", not bad, str(bad[:3]))
    pts = [Vector(p) for p in _wx.read_particle(flakes, 0)]
    n = (pts[1] - pts[0]).cross(pts[2] - pts[0]).normalized()
    to_cam = (cam.matrix_world.translation - sum(pts, Vector()) / len(pts)).normalized()
    check("雪: 粒はカメラを向く", n.dot(to_cam) > 0.999, str(n.dot(to_cam)))


@section
def b08_dust_sparks():
    from dummy_effect_maker.blender.tools import _wx, weather as bw
    from dummy_effect_maker.tools import weather as mw
    from dummy_effect_maker import motion as mo
    new_scene()
    cam = scene_camera()
    wheel = bpy.data.objects.new("wheel", None)
    bpy.context.scene.collection.objects.link(wheel)
    wheel.location = (0, 0, 0)
    wheel.keyframe_insert("location", frame=1)
    wheel.location = (12, 0, 0)
    wheel.keyframe_insert("location", frame=13)
    for fc in rt._fcurves(wheel):
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
    wheel.rotation_euler = (0, math.radians(20), 0)
    cases = ((bw.DustTool, mw.DUST, "dfx_dust1", "_puffs", {"count": 8, "start_frame": 2.0, "duration": 6.0}),
             (bw.DustMeshTool, mw.DUST_MESH, "dfx_dustMesh1", "_puffs", {"count": 6, "start_frame": 2.0, "duration": 4.0}),
             (bw.SparksTool, mw.SPARKS, "dfx_sparks1", "_sparks", {"count": 8, "start_frame": 2.0, "duration": 7.0}),
             (bw.SparkTrailTool, mw.SPARK_TRAIL, "dfx_sparkTrail1", "_trails", {"count": 3, "start_frame": 2.0, "segments": 4}))
    for tool, kind, name, suffix, kw in cases:
        select(wheel, cam)
        op(tool, **kw)
        root = bpy.data.objects.get(name)
        em = rt.control(root, "emitter")
        obj = _wx.part(root, suffix)
        v = rt.read_params(root, kind.stored_params())
        count = int(v["count"])
        at = _wx.emitter_at(root)
        bad = []
        if em.parent != wheel:
            bad.append("parent")
        for f in (3, 7, 12):
            bpy.context.scene.frame_set(f)
            cp, cdir = _wx.camera_yup(root)
            wind = _wx.wind_of(root)
            if tool is bw.SparkTrailTool:
                rows = int(v["segments"]) + 1
                for i in range(count):
                    for j, (p, axes, w) in enumerate(mo.spark_trail_rows(i, f, v, count, at, wind, cp, cdir)):
                        if not same_particle(obj, i * rows + j, (p, axes, (w,) * 3) if w > 1e-9 else _wx.hidden(p)):
                            bad.append((f, i, j))
                continue
            for i in range(count):
                if tool is bw.SparksTool:
                    p, vel, vis, fade, _ = mo.spark(i, f, v, count, at, wind)
                    w = v["width"] * fade
                    pl = (p, mo.streak(p, vel, cp, cdir), (w, max(mo.mag(vel) * v["length"], w), 1)) if vis and w > 1e-9 else _wx.hidden(p)
                else:
                    p, sz, vis, a = mo.dust(i, f, v, count, at, wind)
                    if not vis or sz <= 1e-9:
                        pl = _wx.hidden(p)
                    elif tool is bw.DustMeshTool:
                        pl = (p, _wx.euler_axes(*mo.dust_euler(i, v, a)), (sz,) * 3)
                    else:
                        pl = (p, mo.billboard(p, cp, cdir, mo.dust_roll(i, v, a)), (sz,) * 3)
                if not same_particle(obj, i, pl):
                    bad.append((f, i))
        check(u"%s: Maya 版と同じ計算 (付いて動く出る場所)" % tool.label, not bad, str(bad[:3]))
    # 火花: 出た瞬間は出る場所 (車輪) の位置
    sparks = _wx.part(bpy.data.objects.get("dfx_sparks1"), "_sparks")
    bpy.context.scene.frame_set(5)   # 粒 3 の出る時 = 2 + 7 * 3 / 7
    pts = _wx.read_particle(sparks, 3)
    mid = (Vector(pts[2]) + Vector(pts[3])) / 2
    check("火花: 出た瞬間は出る場所 (キーで動く車輪) の位置", near(mid, (4, 0, 0), 1e-4), str(tuple(mid)))
    bpy.context.scene.frame_set(12)
    check("火花: 地面 (高さ 0) より下に行かない", all(v.co.z > -0.05 for v in sparks.data.vertices))
    mat = _wx.material_of(_wx.part(bpy.data.objects.get("dfx_dust1"), "_puffs"))
    img = mat.node_tree.nodes["dfx_image"].image
    check("土煙: 煙の絵を貼る (ツールが作った PNG)", img is not None and os.path.isfile(bpy.path.abspath(img.filepath))
          and img.size[0] == 256 and bpy.path.abspath(img.filepath).startswith(TMP), str(img and img.filepath))


# ---------------------------------------------------------------- 弾・爆発
@section
def b09_homing_energy():
    from dummy_effect_maker.blender.tools import homing as bh, energy_ball as be
    from dummy_effect_maker.tools import homing as mh, energy_ball as me
    new_scene()
    op(bh.HomingTool, length=10.0, height=2.0, start_frame=1.0, end_frame=9.0, interval=4.0, travel_frames=8.0)
    root = bpy.data.objects.get("dfx_homing1")
    bl = bh.bullets(root)
    check("追尾弾: A / B / C・弾 3 発・軌道の表示", len(bl) == 3 and all(rt.control(root, r) for r in bh.ROLES)
          and bpy.data.objects.get("dfx_homing1_path") is not None
          and near(rt.control(root, "locB").location, (5, 0, 2)))
    bpy.context.scene.frame_set(1)
    p0 = tuple(bl[0].matrix_world.translation)
    bpy.context.scene.frame_set(5)
    p_mid = tuple(bl[0].matrix_world.translation)
    fwd = bl[0].matrix_world.to_3x3().col[0].normalized()
    bpy.context.scene.frame_set(9)
    p_end = tuple(bl[0].matrix_world.translation)
    check("追尾弾: A から出て、半分の時間で B (なめらか・等速)、C に着く", near(p0, (0, 0, 0)) and near(p_mid, (5, 0, 2), 2e-3)
          and near(p_end, (10, 0, 0)), str((p0, p_mid, p_end)))
    check("追尾弾: 進む向きにローカル X (B では水平)", near(fwd, (1, 0, 0), 1e-2), str(tuple(fwd)))
    vis9 = bl[0].scale.length > 0
    bpy.context.scene.frame_set(10)
    check("追尾弾: 着弾 (C) のフレームまで見え、次のフレームで消える", vis9 and bl[0].scale.length == 0)
    root["dfx_path_type"] = mh.PATH_LINEAR
    root["dfx_speed_mode"] = mh.SPEED_CONSTANT
    rt.control(root, "locB").location = (5, 0, 5)
    bpy.context.scene.frame_set(3)
    # 折れ線 A(0,0,0)-B(5,0,5)-C(10,0,0): 全長 2 * sqrt(50)、2 / 8 進んだ所
    d = 2 * math.sqrt(50) * 2 / 8.0
    exp = Vector((5, 0, 5)).normalized() * d
    check("追尾弾: 折れ線・B を動かすと付いてくる (弧長で等速)", near(bl[0].matrix_world.translation, exp, 1e-4),
          str((tuple(bl[0].matrix_world.translation), tuple(exp))))

    op(be.EnergyBallTool, count=2, jitter=0.0, fly_ease=me.EASE_LINEAR)
    root = bpy.data.objects.get("dfx_energy1")
    pr = be.pairs(root)
    check("エネルギー弾: 発射位置・目標 2 組 (目標は手前 -Y)、球は発射位置の子", len(pr) == 2
          and near(pr[0][1].location, (-1, -10, 0)) and len(be.balls(root)) == 2
          and be.balls(root)[0].parent == pr[0][0])
    s, t = pr[0]
    v = me.EnergyBallTool.default_values()
    t0, t1, t2 = me.default_timing(v, 0)
    bpy.context.scene.frame_set(int(t0) + 3)
    ball = be.balls(root)[0]
    grow = me.appear_scale(int(t0) + 3, t0, v["appear_frames"])
    check("エネルギー弾: 大きさ 0 から出現 (Maya 版と同じ式)", near(ball.scale, (0.5 * grow,) * 3), str(tuple(ball.scale)))
    mid = (t1 + t2) / 2.0
    bpy.context.scene.frame_set(int(mid))
    p = me.flight_progress(int(mid), t1, t2, me.EASE_LINEAR)
    exp = s.matrix_world.translation.lerp(t.matrix_world.translation, p)
    check("エネルギー弾: 目標へ飛ぶ (ジッター 0)", near(ball.matrix_world.translation, exp, 1e-4),
          str((tuple(ball.matrix_world.translation), tuple(exp))))
    bpy.context.scene.frame_set(int(t2) + 1)
    check("エネルギー弾: 着弾後に消える", ball.scale.length == 0)
    s["dfx_fly_end"] = t2 + 10
    bpy.context.scene.frame_set(int(t2) + 2)
    check("エネルギー弾: 球ごとのタイミング (発射位置のカスタムプロパティ) がすぐ反映", ball.scale.length > 0)
    s.location = (-1, 0, 3)
    root["dfx_count"] = 3
    select(root)
    op(bscene.UpdateEffectsTool)
    pr = be.pairs(root)
    check("エネルギー弾: 更新で 3 組 (動かした発射位置はそのまま)", len(pr) == 3 and len(be.balls(root)) == 3
          and near(pr[0][0].location, (-1, 0, 3)))


def _wall(name="wall", x=10.0, angle=20.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, 0, 0))
    wall = bpy.context.active_object
    wall.name = name
    wall.scale = (1, 6, 6)
    wall.rotation_euler = (0, 0, math.radians(angle))
    return wall


@section
def b10_explosion():
    from dummy_effect_maker.blender.tools import _wx, _bullets as _bl, explosion as bex, energy_ball as be
    from dummy_effect_maker.tools import explosion as mx, vulcan as mvv
    from dummy_effect_maker import motion as mo
    new_scene()
    cam = scene_camera()
    op(bv.VulcanTool, start_frame=1.0, end_frame=11.0, interval=5.0, length=20.0)
    vr = bpy.data.objects.get("dfx_vulcan1")
    wall = _wall()
    select(wall)
    op(bex.ColliderSetTool)
    check("コリジョンに指定 (カスタムプロパティ)", wall.get(mx.COLLIDER_ATTR) is True)
    select(vr, cam)
    op(bex.ExplosionTool)
    er = bpy.data.objects.get("dfx_explosion1")
    hits = _bl.read_hits(er)
    v = rt.read_params(vr, mvv.stored_params())
    travel = v["travel_frames"]
    c20, s20 = math.cos(math.radians(20)), math.sin(math.radians(20))
    x_hit = 10 - 0.5 / c20
    ok = len(hits) == 3 and all(near(h["pos"], (x_hit, 0, 0), 1e-4) and near(h["normal"], (-c20, -s20, 0), 1e-4)
                                and abs(h["frame"] - (1 + 5 * h["index"] + travel * x_hit / 20.0)) < 1e-4
                                for h in hits)
    check("爆発: 3 発とも、回した壁の手前の面に当たる (位置・法線・フレーム)", ok, str(hits[:1]))
    check("爆発: 弾のルート・カメラとつながる", er.get("dfx_source") == vr and er.get("dfx_camera") == cam)
    b0 = bv.bullets(vr)[0]
    f_hit = hits[0]["frame"]
    bpy.context.scene.frame_set(int(math.floor(f_hit)))
    before = b0.scale.length > 0
    bpy.context.scene.frame_set(int(math.ceil(f_hit)))
    check("爆発: 当たった弾は当たったフレームから消える", before and b0.scale.length == 0)
    er["dfx_hide_bullet"] = False
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(int(math.ceil(f_hit)))
    check("爆発: 「当たった弾を消す」OFF で消えない", b0.scale.length > 0)
    er["dfx_hide_bullet"] = True
    ev = rt.read_params(er, mx.stored_params())
    yh = bex.yup_hits(er)
    bad = []
    for f in (int(f_hit) + d for d in (1, 3, 6, 12)):
        bpy.context.scene.frame_set(f)
        cp, cdir = _wx.camera_yup(er)
        for e, hit in enumerate(yh):
            p, sz, vis, roll = mo.explosion_flash(e, f, hit, ev)
            if not same_particle(_wx.part(er, "_flashes"), e, (p, mo.billboard(p, cp, cdir, roll), (sz,) * 3) if vis and sz > 1e-9 else _wx.hidden(p)):
                bad.append(("flash", f, e))
            p, axes, sz, vis = mo.explosion_ring(e, f, hit, ev)
            if not same_particle(_wx.part(er, "_rings"), e, (p, axes, (sz,) * 3) if vis and sz > 1e-9 else _wx.hidden(p)):
                bad.append(("ring", f, e))
            for part, fn, per in (("_fire", mo.explosion_fire, int(ev["fire_count"])),
                                  ("_smoke", mo.explosion_smoke, int(ev["smoke_count"]))):
                for j in range(per):
                    p, sz, vis, rot = fn(e, j, f, hit, ev)
                    if not same_particle(_wx.part(er, part), e * per + j,
                                         (p, _wx.euler_axes(*rot), (sz,) * 3) if vis and sz > 1e-9 else _wx.hidden(p)):
                        bad.append((part, f, e, j))
            per = int(ev["spark_count"])
            for j in range(per):
                p, vel, scale, vis = mo.explosion_spark(e, j, f, hit, ev)
                if not same_particle(_wx.part(er, "_sparks"), e * per + j,
                                     (p, mo.streak(p, vel, cp, cdir), scale) if vis and scale[0] > 1e-9 else _wx.hidden(p)):
                    bad.append(("spark", f, e, j))
    check("爆発: Maya 版と同じ計算 (閃光・輪・火の玉・煙・火花)", not bad, str(bad[:3]))
    bpy.context.scene.frame_set(int(f_hit) + 2)
    ring = [Vector(p) for p in _wx.read_particle(_wx.part(er, "_rings"), 0)]
    n = (ring[1] - ring[0]).cross(ring[2] - ring[0]).normalized()
    check("爆発: 衝撃波の輪は当たった面に沿う", abs(abs(n.dot(Vector((c20, s20, 0)))) - 1) < 1e-4, str(tuple(n)))
    # 壁を動かすアニメーション → 更新で当たりが変わる
    wall.location = (8, 0, 0)
    wall.keyframe_insert("location", frame=1)
    wall.location = (14, 0, 0)
    wall.keyframe_insert("location", frame=21)
    for fc in rt._fcurves(wall):
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
    select(er)
    op(bex.ExplosionUpdateTool)
    hits = _bl.read_hits(er)
    bad = [h for h in hits if abs(h["pos"][0] - (8 + 6 * (h["frame"] - 1) / 20.0 - 0.5 / c20)) > 0.02]
    check("爆発: 動く壁にも当たる (更新で計算し直す)", len(hits) == 3 and not bad, str([(h["frame"], h["pos"]) for h in hits]))
    select(er)
    op(bex.ExplosionRemoveTool)
    bpy.context.scene.frame_set(int(math.ceil(hits[0]["frame"])) + 1)
    check("爆発を外す: 弾が元に戻る", bpy.data.objects.get("dfx_explosion1") is None and b0.scale.length > 0)

    op(be.EnergyBallTool, count=1, jitter=0.3)
    eb = bpy.data.objects.get("dfx_energy1")
    s, t = be.pairs(eb)[0]
    s.location = (0, 0, 0)
    t.location = (20, 0, 0)
    select(eb)
    op(bex.ExplosionTool)
    er2 = [r for r in rt.effect_roots() if r.get("dfx_source") == eb][0]
    hits = _bl.read_hits(er2)
    check("爆発: エネルギー弾も当たる (ぶれていても壁の面で)", len(hits) == 1 and 7 < hits[0]["pos"][0] < 15,
          str(hits))
    bpy.context.scene.frame_set(int(math.ceil(hits[0]["frame"])) if hits else 1)
    check("爆発: 当たったエネルギー弾は消える", hits and be.balls(eb)[0].scale.length == 0)


# ---------------------------------------------------------------- 画ぶれ・手ブレ
@section
def b11_shakes():
    from dummy_effect_maker.blender.tools import camera_shake as bcs
    from dummy_effect_maker.tools import camera_shake as mc, hand_shake as mhs
    from mathutils import Euler
    new_scene()
    cam = scene_camera(loc=(0, -20, 3))
    cam.keyframe_insert("location", frame=1)
    cam.location = (10, -20, 3)
    cam.keyframe_insert("location", frame=31)
    cam.rotation_euler = (math.radians(80), 0, math.radians(15))
    cam.data.dof.focus_distance = 8.0
    select(cam)
    op(bcs.CameraShakeTool, strength_x=0.3, strength_y=0.2, strength_z=0.1, interval=2.0)
    root = bpy.data.objects.get("dfx_cameraShake1")
    paths = set(fc.data_path for fc in rt._fcurves(root))
    noise_fc = [fc for fc in rt._fcurves(root) if fc.data_path == '["dfx_noise_tx"]']
    check("画ぶれ: ルート・乱数のキー 5 本 (タイムスライダーの範囲)", root is not None and root.get("dfx_camera") == cam
          and all('["%s"]' % a in paths for a in mc.NOISE_ATTRS) and noise_fc
          and 20 <= len(noise_fc[0].keyframe_points) <= 70
          and all(-1 <= k.co[1] <= 1 for k in noise_fc[0].keyframe_points), str(len(noise_fc[0].keyframe_points) if noise_fc else 0))
    bad = []
    for f in (3, 10, 22):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        dx, dy, dz, ax, ay, _, on = bcs.camera_offsets(root, f)
        basis = rt.matrix_basis_at(cam, f)
        rot = basis.to_3x3().normalized()
        exp_loc = basis.translation + rot @ Vector((dx, dy, dz))
        exp_rot = rot @ Euler((math.atan2(ay, 8.0), -math.atan2(ax, 8.0), 0), "XYZ").to_matrix()
        got = cam.matrix_world
        if not near(got.translation, exp_loc, 1e-4):
            bad.append(("loc", f, tuple(got.translation), tuple(exp_loc)))
        if not all(near(got.to_3x3().col[k], exp_rot.col[k], 1e-4) for k in range(3)):
            bad.append(("rot", f))
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            bad.append(("zero", f))
    check("画ぶれ: キーの上にぶれる (ずれはカメラの向き、回転はカメラのローカル)", not bad, str(bad[:2]))
    check("画ぶれ: カメラのキーはそのまま", near(cam.location, rt.matrix_basis_at(cam, 22).translation, 1e-6))
    root["dfx_strength_x"] = 0.0
    root["dfx_strength_y"] = 0.0
    root["dfx_strength_z"] = 0.0
    bpy.context.scene.frame_set(12)
    check("画ぶれ: 強さ 0 でぶれない (すぐ反映)", near(cam.delta_location, (0, 0, 0), 1e-6)
          and near(cam.delta_rotation_euler, (0, 0, 0), 1e-6), str((tuple(cam.delta_location), tuple(cam.delta_rotation_euler))))
    root["dfx_strength_x"] = 0.3
    root["dfx_interval"] = 6.0
    select(root)
    op(bscene.UpdateEffectsTool)
    n = len([fc for fc in rt._fcurves(root) if fc.data_path == '["dfx_noise_tx"]'][0].keyframe_points)
    check("画ぶれ: 更新で乱数のキーを作り直す (間隔 6 で少なく)", 5 <= n <= 25, str(n))
    select(cam)
    try:
        res = op(bcs.HandShakeTool)
    except RuntimeError:
        res = {"CANCELLED"}
    check("同じカメラに手ブレは付けられない (画ぶれが付いている)", res == {"CANCELLED"}
          and bpy.data.objects.get("dfx_handShake1") is None)
    select(cam)
    op(bcs.CameraShakeRemoveTool)
    bpy.context.scene.frame_set(14)
    check("画ぶれを外す: カメラが元に戻る", bpy.data.objects.get("dfx_cameraShake1") is None
          and near(cam.delta_location, (0, 0, 0), 1e-9) and near(cam.delta_rotation_euler, (0, 0, 0), 1e-6))

    # 手ブレ (オブジェクト・傾き)
    ball = bpy.data.objects.new("ball", None)
    bpy.context.scene.collection.objects.link(ball)
    ball.location = (2, 3, 1)
    ball.rotation_euler = (0, 0, math.radians(40))
    select(ball)
    op(bcs.HandShakeTool, strength_x=0.2, strength_y=0.1, roll=3.0, rotation_distance=4.0)
    hroot = bpy.data.objects.get("dfx_handShake1")
    bad = []
    prev = None
    for f in (2, 9, 17, 33):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        dx, dy, dz, ax, ay, roll, on = bcs.hand_offsets(hroot, f)
        rot = Euler((0, 0, math.radians(40))).to_matrix()
        exp_loc = Vector((2, 3, 1)) + rot @ Vector((dx, dy, dz))
        exp_rot = rot @ Euler((math.atan2(ay, 4.0), -math.atan2(ax, 4.0), math.radians(roll)), "XYZ").to_matrix()
        got = ball.matrix_world
        if not near(got.translation, exp_loc, 1e-4) or not all(near(got.to_3x3().col[k], exp_rot.col[k], 1e-4) for k in range(3)):
            bad.append(f)
        if prev is not None and near(prev, got.translation, 1e-7):
            bad.append(("still", f))
        prev = got.translation.copy()
    check("手ブレ: オブジェクトがゆっくり揺れる (位置・回転・傾き)", not bad, str(bad[:2]))
    hroot["dfx_rotation"] = False
    bpy.context.scene.frame_set(20)
    check("手ブレ: 回転のブレ OFF で向きは変わらない", near(ball.delta_rotation_euler, (0, 0, 0), 1e-6))

    # 注視点のあるカメラ
    aim = bpy.data.objects.new("camAim", None)
    bpy.context.scene.collection.objects.link(aim)
    aim.location = (0, 0, 1)
    cam2 = bpy.data.objects.new("aimCam", bpy.data.cameras.new("aimCam"))
    bpy.context.scene.collection.objects.link(cam2)
    cam2.location = (0, -12, 2)
    con = cam2.constraints.new("TRACK_TO")
    con.target = aim
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    select(cam2)
    op(bcs.CameraShakeTool, strength_x=0.3, strength_y=0.2)
    aroot = bpy.data.objects.get("dfx_cameraShake1")
    bpy.context.scene.frame_set(7)
    dx, dy, dz, ax, ay, _, on = bcs.camera_offsets(aroot, 7)
    f = (Vector((0, 0, 1)) - Vector((0, -12, 2))).normalized()
    x = Vector((f.y, -f.x, 0)).normalized()
    y = x.cross(f)
    check("画ぶれ: 注視点のあるカメラは画面の横・縦にぶれ、注視点もぶれる", aroot.get("dfx_aim") == aim
          and near(cam2.delta_location, x * dx + y * dy, 1e-5) and near(aim.delta_location, x * ax + y * ay, 1e-5),
          str((tuple(cam2.delta_location), tuple(x * dx + y * dy))))
    op(bscene.DeleteAllTool)
    check("全削除で、ぶらしていた物も元に戻る", near(cam2.delta_location, (0, 0, 0)) and near(aim.delta_location, (0, 0, 0))
          and near(ball.delta_location, (0, 0, 0)) and near(ball.delta_rotation_euler, (0, 0, 0)))


@section
def b12_export_more():
    from dummy_effect_maker.blender.tools import _bullets as _bl, explosion as bex, energy_ball as be
    from dummy_effect_maker.blender.tools import weather as bw, wind as bwind, camera_shake as bcs
    new_scene()
    cam = scene_camera()
    select()
    op(bwind.WindTool, strength=0.2, gust=0.0, turbulence=0.0)
    select(cam)
    op(bw.RainTool, count=10)
    op(bv.VulcanTool, start_frame=1.0, end_frame=6.0, interval=5.0, length=20.0)
    wall = _wall()
    select(wall)
    op(bex.ColliderSetTool)
    select(bpy.data.objects.get("dfx_vulcan1"), cam)
    op(bex.ExplosionTool)
    select()
    op(be.EnergyBallTool, count=1)
    select(cam)
    op(bcs.HandShakeTool)
    path = os.path.join(TMP, "shot_more")
    op(bscene.ExportShotTool, path=path, range_mode=bscene.RANGE_CUSTOM, start_frame=1.0, end_frame=20.0)
    shot = xc.read_shot(path + ".json")
    eff = dict((e["name"], e) for e in shot["effects"])
    w = eff["dfx_wind1"]["data"]["wind"]
    i = w["frames"].index(10)
    check("書き出し: 風の流れ (速さ・流された距離。Z が上)", near(w["velocity"][i], (0.2, 0, 0), 1e-6)
          and w["drift"][i][0] > w["drift"][0][0], str(w["velocity"][i]))
    rain = eff["dfx_rain1"]
    check("書き出し: 雨のつながり (カメラ・風) と範囲の箱", rain["links"].get("dfx_camera") == "shotCam"
          and rain["links"].get("dfx_wind") == "dfx_wind1"
          and [c["role"] for c in rain["controls"]] == ["area"], str((rain["links"], [c["role"] for c in rain["controls"]])))
    ex = eff["dfx_explosion1"]
    hits = _bl.read_hits(bpy.data.objects.get("dfx_explosion1"))
    check("書き出し: 爆発の当たり (フレーム・弾・位置・法線)", len(ex["data"]["hits"]) == len(hits) == 2
          and near(ex["data"]["hits"][0]["position"], hits[0]["pos"], 1e-6)
          and ex["links"].get("dfx_source") == "dfx_vulcan1", str(ex["data"].get("hits", [])[:1]))
    vb = eff["dfx_vulcan1"]["data"]["bullets"][0]["track"]
    hf = int(math.ceil(hits[0]["frame"]))
    check("書き出し: 当たった弾は当たったフレームから見えない", vb["visible"][vb["frames"].index(hf)] is False
          and vb["visible"][vb["frames"].index(hf - 1)] is True)
    en = eff["dfx_energy1"]
    check("書き出し: エネルギー弾の球の通り道と、発射位置・目標", len(en["data"]["bullets"]) == 1
          and sorted(c["role"] for c in en["controls"]) == ["start_01", "target_01"])
    hs = eff["dfx_handShake1"]
    c = shot["cameras"][0]
    moving = len(set(tuple(t) for t in c["track"]["t"])) > 5 if not c["track"].get("static") else False
    check("書き出し: 手ブレの付いたカメラ (揺れた動き)", hs["links"].get("dfx_camera") == "shotCam"
          and c["name"] == "shotCam" and moving)


@section
def b05_delete_all():
    op(bscene.DeleteAllTool)
    check("全削除: ツールで作った物が全部消える (カメラは残る)",
          not [o for o in bpy.data.objects if o.get(rt.TAG_KEY)]
          and not [m for m in bpy.data.meshes if m.get(rt.TAG_KEY)]
          and bpy.data.objects.get("shotCam") is not None)


for fn in (b01_register, b02_vulcan, b03_shockwave, b04_export, b05_delete_all, b06_wind,
           b07_rain_snow, b08_dust_sparks, b09_homing_energy, b10_explosion,
           b11_shakes, b12_export_more):
    fn()
fails = [r for r in RESULTS if not r[1]]
print("\nSUMMARY: %d passed, %d failed" % (len(RESULTS) - len(fails), len(fails)))
for name, _, detail in fails:
    print("  FAIL:", name, detail)
sys.stdout.flush()
os._exit(1 if fails else 0)
