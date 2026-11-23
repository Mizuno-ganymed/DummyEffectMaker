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


@section
def b05_delete_all():
    op(bscene.DeleteAllTool)
    check("全削除: ツールで作った物が全部消える (カメラは残る)",
          not [o for o in bpy.data.objects if o.get(rt.TAG_KEY)]
          and not [m for m in bpy.data.meshes if m.get(rt.TAG_KEY)]
          and bpy.data.objects.get("shotCam") is not None)


for fn in (b01_register, b02_vulcan, b03_shockwave, b04_export, b05_delete_all):
    fn()
fails = [r for r in RESULTS if not r[1]]
print("\nSUMMARY: %d passed, %d failed" % (len(RESULTS) - len(fails), len(fails)))
for name, _, detail in fails:
    print("  FAIL:", name, detail)
sys.stdout.flush()
os._exit(1 if fails else 0)
