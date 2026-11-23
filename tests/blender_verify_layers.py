# -*- coding: utf-8 -*-
"""Blender 版の天候の大きな板 (遠景の雨・雪、霧の板、遠景の重ね板、地霧、這う霧) の検証。

    blender -b --factory-startup --python tests/blender_verify_layers.py -- <リポジトリのフォルダ>

Maya 版 (tools/weather_layers.py) と同じ計算・同じ作りになるかを確かめる。
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
TMP = tempfile.mkdtemp(prefix="dfx_blender_layers_")


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
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(TMP, "verify_layers.blend"))


import dummy_effect_maker.blender as dfx  # noqa: E402
from dummy_effect_maker.blender import runtime as rt, ui  # noqa: E402

dfx.register()
from dummy_effect_maker import motion as mo, textures  # noqa: E402
from dummy_effect_maker.blender.tools import _wx, scene as bscene, weather as bw, wind as bwind  # noqa: E402
from dummy_effect_maker.blender.tools import weather_layers as bl  # noqa: E402
from dummy_effect_maker.tools import _weather as mx, weather_layers as ml  # noqa: E402


def op(tool_cls, **values):
    """ツールのオペレーターを、オプションを指定して実行する (ボタンと同じ)。"""
    tool = [t for t in ui._TOOLS if isinstance(t, tool_cls)][0]
    tool.reset_values()
    tool.set_values(values)
    idname = ui.operator_idname(tool)
    cat, name = idname.split(".")
    return getattr(getattr(bpy.ops, cat), name)()


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


def frame(f):
    bpy.context.scene.frame_set(f)
    bpy.context.view_layer.update()


def expected_verts(obj, placement):
    """粒 1 つの頂点の期待値 (Y が上の計算結果を Z が上に直して、型を置く)。"""
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
    got = _wx.read_particle(obj, i)
    return all(near(a, b, tol) for a, b in zip(got, expected_verts(obj, placement)))


def particle_size(obj, i):
    pts = [Vector(p) for p in _wx.read_particle(obj, i)]
    return max((p - pts[0]).length for p in pts)


def mapping(mat):
    mp = mat.node_tree.nodes["dfx_mapping"]
    return tuple(mp.inputs["Scale"].default_value)[:2], tuple(mp.inputs["Location"].default_value)[:2]


def texture_ok(mat, name):
    """ツールが作った絵 (.blend の隣) を貼っている。"""
    img = mat.node_tree.nodes["dfx_image"].image
    path = bpy.path.abspath(img.filepath) if img else ""
    return (img is not None and os.path.isfile(path) and path.startswith(TMP)
            and os.path.basename(path) == textures.file_name(name)), path


def tagged_counts():
    return (len([o for o in bpy.data.objects if o.get(rt.TAG_KEY)]),
            len([m for m in bpy.data.meshes if m.get(rt.TAG_KEY)]),
            len([m for m in bpy.data.materials if m.get(rt.TAG_KEY)]))


LABELS = {bl.RainFarTool: u"雨 (遠景の筒)", bl.SnowFarTool: u"雪 (遠景の筒)", bl.FogTool: u"霧 (霧の板)",
          bl.FogDepthTool: u"霧 (遠景の重ね板)", bl.GroundFogTool: u"地霧 (重ね板)",
          bl.GroundCrawlTool: u"地霧 (這う霧)"}


@section
def l01_register():
    tools = dict((type(t), t) for t in ui._TOOLS)
    ok = all(cls in tools and tools[cls].label == label and tools[cls].tab == u"天候"
             and tools[cls].hosts == ("blender",) for cls, label in LABELS.items())
    check("登録: 6 つのツール (Maya 版と同じ名前・天候タブ)", ok, str([t.label for t in ui._TOOLS]))
    same = all([p.name for p in cls.params] == [p.name for p in maya.params]
               for cls, maya in ((bl.RainFarTool, ml.RainFarTool), (bl.SnowFarTool, ml.SnowFarTool),
                                 (bl.FogTool, ml.FogTool), (bl.FogDepthTool, ml.FogDepthTool),
                                 (bl.GroundFogTool, ml.GroundFogTool),
                                 (bl.GroundCrawlTool, ml.GroundCrawlTool)))
    check("登録: パラメータは Maya 版と同じ", same)
    ops = [ui.operator_idname(tools[c]) for c in LABELS]
    check("登録: オペレーターができる", all(hasattr(getattr(bpy.ops, o.split(".")[0]), o.split(".")[1]) for o in ops))
    from dummy_effect_maker.blender.tools import _build as bd
    check("登録: 作り直しの登録・風を受ける種類", all(k.effect_type in bd.BUILDERS for k in
                                          (ml.RAIN_FAR, ml.SNOW_FAR, ml.FOG, ml.FOG_DEPTH, ml.GROUND_FOG,
                                           ml.GROUND_CRAWL))
          and {"rain_far", "snow_far", "fog", "ground_fog", "ground_crawl"} <= bw.WEATHER_TYPES
          and "fog_depth" not in bw.WEATHER_TYPES)


# ---------------------------------------------------------------- 遠景の筒
def far_expected(root, kind, k, n, f, snow, aspect):
    """Maya 版の build_far のエクスプレッションと同じ式 (筒 k の 大きさ・繰り返し・ずらし・濃さ)。"""
    v = rt.read_params(root, kind.stored_params())
    sd = v["seed"]
    tile = max(v["tile"], 0.001)
    rad = v["radius"] + v["radius_step"] * k
    ru = max(1, math.floor(6.2831853 * rad / tile + 0.5))
    rv = v["height"] / (tile * aspect)
    cp, cdir = _wx.camera_yup(root)
    c = mo.cross(cdir, (0, 1, 0))
    right = mo.unit(c) if mo.mag(c) >= 1e-6 else (1, 0, 0)
    wind = _wx.wind_of(root)
    side = mo.dot(mo.mul(wind.drift_at(f), wind.influence), right)
    sway = v["sway"] * math.sin(6.2831853 * f / 90.0 + k) / tile if snow else 0.0
    ou = -side / tile + sway + mx.hash01(k, 0, 1, sd)
    ov = v["speed"] * f / (tile * aspect) + mx.hash01(k, 0, 2, sd)
    op_ = v["opacity_near"] + (v["opacity_far"] - v["opacity_near"]) * (k / float(max(n - 1, 1)))
    return (rad, rad, v["height"]), (ru, rv), (ou, ov), op_, v["color"]


def check_far(root, kind, snow, frames, label):
    layers = bl._layers(root)
    grp = bl._piece(root, "layers")
    aspect = grp["dfx_aspect"]
    cam = _wx.camera_of(root)
    bad = []
    for f in frames:
        frame(f)
        if not near(grp.matrix_world.translation, cam.matrix_world.translation, 1e-4):
            bad.append(("follow", f, tuple(grp.matrix_world.translation)))
        for k, cyl in enumerate(layers):
            scale, rep, off, op_, color = far_expected(root, kind, k, len(layers), f, snow, aspect)
            mat = _wx.material_of(cyl)
            got_rep, got_off = mapping(mat)
            nt = mat.node_tree
            if not near(cyl.scale, scale, 1e-5) or not near(got_rep, rep, 1e-4) or not near(got_off, off, 1e-4):
                bad.append((f, k, tuple(cyl.scale), got_rep, got_off, rep, off))
            if abs(nt.nodes["dfx_alpha"].inputs[1].default_value - op_) > 1e-5 or \
                    not near(nt.nodes["dfx_tint"].inputs[7].default_value[:3], color, 1e-5):
                bad.append((f, k, "opacity/color"))
    check(u"%s: Maya 版と同じ計算 (筒の大きさ・模様の繰り返し・流れ・濃さ・色、カメラに付いて動く)" % label,
          not bad, str(bad[:2]))


@section
def l02_far():
    new_scene()
    cam = scene_camera()
    select(cam)
    op(bl.RainFarTool)
    root = bpy.data.objects.get("dfx_rainFar1")
    layers = bl._layers(root)
    grp = bl._piece(root, "layers")
    check("遠景の雨: ルート・カメラに付く入れ物・筒 3 本 (入れ物の子)", root is not None
          and root.get("dfx_camera") == cam and grp is not None and len(layers) == 3
          and all(c.parent == grp for c in layers) and len(layers[0].data.polygons) == 48)
    ok, path = texture_ok(_wx.material_of(layers[0]), "rain")
    check("遠景の雨: 雨の筋の絵 (ツールが作った PNG、繰り返し)", ok and
          _wx.material_of(layers[0]).node_tree.nodes["dfx_image"].extension == "REPEAT", path)
    check("遠景の雨: 筒ごとに別のマテリアル", len(set(_wx.material_of(c) for c in layers)) == 3)
    check("遠景の雨: 模様の縦横比 (絵の 縦 / 横)", abs(grp["dfx_aspect"] - ml.texture_aspect(path)) < 1e-9,
          str(grp["dfx_aspect"]))
    # 筒の形: 半径 1、z = -0.5〜0.5
    vs = [v.co for v in layers[0].data.vertices]
    check("遠景の雨: 筒の形 (半径 1・高さ 1、Z が上)", all(abs(Vector((c.x, c.y)).length - 1) < 1e-6 for c in vs)
          and min(c.z for c in vs) == -0.5 and max(c.z for c in vs) == 0.5)
    # カメラを動かすアニメーション
    cam.keyframe_insert("location", frame=1)
    cam.location = (10, -15, 6)
    cam.keyframe_insert("location", frame=21)
    check_far(root, ml.RAIN_FAR, False, (1, 7, 15, 30), u"遠景の雨")
    m0 = _wx.material_of(layers[0])
    frame(10)
    off10 = mapping(m0)[1]
    frame(14)
    off14 = mapping(m0)[1]
    v = rt.read_params(root, ml.RAIN_FAR.stored_params())
    check("遠景の雨: 模様が下に流れる (1 フレームに 落ちる速さ / 模様の高さ)",
          abs((off14[1] - off10[1]) - 4 * v["speed"] / (v["tile"] * grp["dfx_aspect"])) < 1e-6
          and abs(off14[0] - off10[0]) < 1e-9, str((off10, off14)))
    # 風: 横に流れる
    select()
    op(bwind.WindTool, strength=0.2, gust=0.0, turbulence=0.0)
    wind = bpy.data.objects.get("dfx_wind1")
    check("遠景の雨: 風を作るとつながる", root.get("dfx_wind") == wind)
    frame(10)
    w10 = mapping(m0)[1]
    frame(14)
    w14 = mapping(m0)[1]
    # 風は +X (カメラの右)。模様は U が減る向き (右) へ: 4 フレームで 0.2 * 4 / 模様の大きさ
    check("遠景の雨: 風で模様が横に流れる", abs((w14[0] - w10[0]) + 0.2 * 4 / v["tile"]) < 1e-6, str((w10, w14)))
    check_far(root, ml.RAIN_FAR, False, (12, 25), u"遠景の雨 (風)")
    # 即反映
    root["dfx_radius"] = 20.0
    root["dfx_opacity_far"] = 0.05
    root["dfx_color"] = (1.0, 0.5, 0.25)
    frame(16)
    check("遠景の雨: パラメータを変えると次のフレームで反映", abs(layers[0].scale[0] - 20) < 1e-6
          and abs(layers[2].scale[0] - 40) < 1e-6
          and abs(_wx.material_of(layers[2]).node_tree.nodes["dfx_alpha"].inputs[1].default_value - 0.05) < 1e-6)
    check_far(root, ml.RAIN_FAR, False, (16,), u"遠景の雨 (変えた後)")
    # 更新: 筒の数
    root["dfx_layers"] = 5
    select(root)
    counts = tagged_counts()
    op(bscene.UpdateEffectsTool)
    layers = bl._layers(root)
    check("遠景の雨: 更新で筒 5 本", len(layers) == 5 and bl._piece(root, "layers") is not None)
    op(bscene.UpdateEffectsTool)
    check("遠景の雨: 更新を繰り返しても増えない", tagged_counts() == (counts[0] + 2, counts[1] + 2, counts[2] + 2),
          str((counts, tagged_counts())))
    check_far(root, ml.RAIN_FAR, False, (9,), u"遠景の雨 (更新後)")

    select(cam)
    op(bl.SnowFarTool, layers=2)
    snow = bpy.data.objects.get("dfx_snowFar1")
    sl = bl._layers(snow)
    ok, path = texture_ok(_wx.material_of(sl[0]), "snow")
    check("遠景の雪: 筒 2 本・雪の粒の絵・風につながる", len(sl) == 2 and ok and snow.get("dfx_wind") == wind, path)
    check_far(snow, ml.SNOW_FAR, True, (3, 20, 41), u"遠景の雪 (揺れ・風)")


# ---------------------------------------------------------------- 霧の板
def check_cards(root, kind, frames, label):
    cards = _wx.part(root, "_cards")
    area = rt.control(root, "area")
    count = cards.data["dfx_count"]
    bad = []
    shown = 0
    for f in frames:
        frame(f)
        v = rt.read_params(root, kind.stored_params())
        am = _wx.yup(area.matrix_world)
        ai = mo.inverse(am)
        cp, cdir = _wx.camera_yup(root)
        wind = _wx.wind_of(root)
        attr = cards.data.attributes[bl.ALPHA_ATTR].data
        for i in range(count):
            p, (sx, sy), alpha, roll = mo.fog_card(i, f, v, am, ai, wind, cp)
            vis = alpha > 0.001
            pl = (p, mo.billboard(p, cp, cdir, roll), (sx, sy, sx)) if vis else _wx.hidden(p)
            if not same_particle(cards, i, pl):
                bad.append(("place", f, i))
            want = alpha if vis else 0.0
            if any(abs(attr[4 * i + j].value - want) > 1e-5 for j in range(4)):
                bad.append(("alpha", f, i, attr[4 * i].value, want))
            shown += vis
    check(u"%s: Maya 版と同じ計算 (motion.fog_card: 位置・カメラを向く・大きさ・回転・濃さ)" % label,
          not bad and shown > 0, str(bad[:3]))


@section
def l03_fog_cards():
    new_scene()
    cam = scene_camera()
    select(cam)
    op(bl.FogTool, count=12, area_width=30.0, area_height=6.0)
    root = bpy.data.objects.get("dfx_fog1")
    cards = _wx.part(root, "_cards")
    area = rt.control(root, "area")
    check("霧の板: ルート・範囲の箱・板 12 枚 (1 つのメッシュ)・濃さの属性", root is not None and area is not None
          and cards.data["dfx_count"] == 12 and len(cards.data.vertices) == 48
          and bl.ALPHA_ATTR in cards.data.attributes and root.get("dfx_camera") == cam)
    check("霧の板: 範囲の箱 (底が地面)", near(area.location, (0, 0, 3)) and near(area.scale, (30, 30, 6)))
    mat = _wx.material_of(cards)
    nt = mat.node_tree
    ok, path = texture_ok(mat, "fog_card")
    mix_in = [l for l in nt.links if l.to_node.bl_idname == "ShaderNodeMixShader" and l.to_socket == l.to_node.inputs[0]]
    check("霧の板: 霧の板の絵 (端は繰り返さない)", ok and nt.nodes["dfx_image"].extension == "CLIP", path)
    check("霧の板: 板ごとの濃さはマテリアルが頂点の属性 dfx_alpha を読む",
          nt.nodes["dfx_card_alpha"].attribute_name == bl.ALPHA_ATTR and mix_in
          and mix_in[0].from_node.name == "dfx_card_mul")
    check_cards(root, ml.FOG, (1, 9, 23), u"霧の板")
    # 箱を回して動かす・カメラを近づける・風
    area.location = (4, 6, 2)
    area.rotation_euler = (0, 0, math.radians(25))
    select()
    op(bwind.WindTool, strength=0.3)
    check("霧の板: 風を作るとつながる", root.get("dfx_wind") == bpy.data.objects.get("dfx_wind1"))
    cam.location = (2, 3, 3)
    check_cards(root, ml.FOG, (5, 17, 40), u"霧の板 (回した箱・風・近いカメラ)")
    frame(17)
    i = [k for k in range(12) if particle_size(cards, k) > 1e-6][0]
    pts = [Vector(p) for p in _wx.read_particle(cards, i)]
    n = (pts[1] - pts[0]).cross(pts[3] - pts[0]).normalized()
    to_cam = (cam.matrix_world.translation - sum(pts, Vector()) / 4).normalized()
    check("霧の板: 板はカメラを向く", n.dot(to_cam) > 0.999, str(n.dot(to_cam)))
    # 即反映
    root["dfx_opacity"] = 0.9
    root["dfx_size"] = 3.0
    root["dfx_near_fade"] = 0.0
    check_cards(root, ml.FOG, (18,), u"霧の板 (パラメータを変えた後)")
    root["dfx_opacity"] = 0.0
    frame(19)
    check("霧の板: 不透明度 0 で全部消える", all(particle_size(cards, k) < 1e-9 for k in range(12)))
    root["dfx_count"] = 5
    select(root)
    op(bscene.UpdateEffectsTool)
    check("霧の板: 更新で 5 枚 (箱はそのまま)", _wx.part(root, "_cards").data["dfx_count"] == 5
          and near(rt.control(root, "area").location, (4, 6, 2)))

    select(cam)
    op(bl.GroundCrawlTool, count=10)
    crawl = bpy.data.objects.get("dfx_groundCrawl1")
    carea = rt.control(crawl, "area")
    check("這う霧: 低い範囲の箱 (高さ 1.5)・板 10 枚・風につながる", near(carea.scale, (40, 40, 1.5))
          and _wx.part(crawl, "_cards").data["dfx_count"] == 10
          and crawl.get("dfx_wind") == bpy.data.objects.get("dfx_wind1"))
    check_cards(crawl, ml.GROUND_CRAWL, (2, 30), u"這う霧")


# ---------------------------------------------------------------- 霧 (遠景の重ね板)
@section
def l04_fog_depth():
    new_scene()
    cam = scene_camera()
    cam.keyframe_insert("location", frame=1)
    cam.keyframe_insert("rotation_euler", frame=1)
    cam.location = (12, -10, 7)
    cam.rotation_euler = (math.radians(70), 0, math.radians(30))
    cam.keyframe_insert("location", frame=21)
    cam.keyframe_insert("rotation_euler", frame=21)
    select(cam)
    op(bl.FogDepthTool, layers=6, start=10.0, end=60.0, density=0.8)
    root = bpy.data.objects.get("dfx_fogDepth1")
    layers = bl._layers(root)
    fr = bl._piece(root, "frame")
    check("遠景の重ね板: ルート・カメラに付く入れ物・板 6 枚", root is not None and fr is not None and len(layers) == 6
          and all(p.parent == fr for p in layers) and root.get("dfx_camera") == cam)
    mat = _wx.material_of(layers[0])
    check("遠景の重ね板: 全部の板で 1 つのマテリアル (色・1 枚の濃さ)",
          all(_wx.material_of(p) == mat for p in layers)
          and abs(mat.node_tree.nodes["dfx_mix"].inputs[0].default_value - ml.layer_opacity(0.8, 6)) < 1e-6)
    tanh = cam.data.sensor_width * 0.5 / cam.data.lens
    bad = []
    for f in (1, 6, 13, 30):
        frame(f)
        cm = cam.matrix_world
        fwd = -(cm.to_3x3().col[2]).normalized()
        for k, p in enumerate(layers):
            d = 10 + 50 * k / 5.0
            w = 2 * d * tanh * 1.4
            mw = p.matrix_world
            if not near(mw.translation, cm @ Vector((0, 0, -d)), 1e-3):
                bad.append(("pos", f, k, tuple(mw.translation), tuple(cm @ Vector((0, 0, -d)))))
            if abs(mw.to_3x3().col[0].length - w) > 1e-3 or abs(mw.to_3x3().col[1].length - w) > 1e-3:
                bad.append(("size", f, k))
            if mw.to_3x3().col[2].normalized().dot(-fwd) < 0.9999:
                bad.append(("facing", f, k))
    check("遠景の重ね板: カメラに付いて動き、カメラの前に並ぶ (Maya 版と同じ距離・幅)", not bad, str(bad[:2]))
    root["dfx_density"] = 0.5
    root["dfx_end"] = 100.0
    root["dfx_color"] = (0.5, 0.6, 0.7)
    frame(8)
    check("遠景の重ね板: パラメータを変えると次のフレームで反映",
          abs(mat.node_tree.nodes["dfx_mix"].inputs[0].default_value - ml.layer_opacity(0.5, 6)) < 1e-6
          and abs(layers[5].location.z + 100) < 1e-6
          and near(mat.node_tree.nodes["dfx_emission"].inputs["Color"].default_value[:3], (0.5, 0.6, 0.7), 1e-6))
    # 風を受けない
    select()
    op(bwind.WindTool)
    check("遠景の重ね板: 風にはつながない", root.get("dfx_wind") is None)
    # フレームを変えずにカメラを動かしても付いてくる (コンストレイント)
    cam.animation_data_clear()
    cam.location = (0, 0, 50)
    bpy.context.view_layer.update()
    check("遠景の重ね板: フレームを変えずにカメラを動かしても付いてくる",
          near(fr.matrix_world.translation, (0, 0, 50), 1e-4), str(tuple(fr.matrix_world.translation)))
    root["dfx_layers"] = 3
    select(root)
    op(bscene.UpdateEffectsTool)
    check("遠景の重ね板: 更新で 3 枚", len(bl._layers(root)) == 3)


# ---------------------------------------------------------------- 地霧 (重ね板)
def ground_expected(root, k, n, f):
    """Maya 版の build_ground_fog のエクスプレッションと同じ式。"""
    v = rt.read_params(root, ml.GROUND_FOG.stored_params())
    sd = v["seed"]
    am, _ = _wx.area_yup(root)
    sw = mo.mag(am[0:3])
    sdp = mo.mag(am[8:11])
    ax, az = mo.unit(am[0:3]), mo.unit(am[8:11])
    tile = max(v["tile"], 0.001)
    wind = _wx.wind_of(root)
    dw = mo.mul(wind.drift_at(f), wind.influence)
    du, dv = mo.dot(dw, ax), -mo.dot(dw, az)
    a = v["opacity"] * math.pow(1 - k / float(n), v["falloff"])
    ou = -du / tile + v["flow"] * f * (1 + k * 0.37) + mx.hash01(k, 0, 1, sd)
    ov = -dv / tile + v["flow"] * f * (0.5 - k * 0.21) + mx.hash01(k, 0, 2, sd)
    edge = min(0.99, max(0.0, 1 - v["edge"]))
    return a, (sw / tile, sdp / tile), (ou, ov), edge, v["color"]


def check_ground(root, frames, label):
    layers = bl._layers(root)
    area = rt.control(root, "area")
    bad = []
    for f in frames:
        frame(f)
        for k, p in enumerate(layers):
            a, rep, off, edge, color = ground_expected(root, k, len(layers), f)
            nt = _wx.material_of(p).node_tree
            got_rep, got_off = mapping(_wx.material_of(p))
            if abs(nt.nodes["dfx_layer_alpha"].inputs[1].default_value - a) > 1e-6:
                bad.append(("alpha", f, k))
            if abs(nt.nodes["dfx_edge"].inputs[1].default_value - (1 - edge)) > 1e-6:
                bad.append(("edge", f, k))
            if not near(got_rep, rep, 1e-4) or not near(got_off, off, 1e-4):
                bad.append(("uv", f, k, got_rep, rep, got_off, off))
            if not near(nt.nodes["dfx_emission"].inputs["Color"].default_value[:3], color, 1e-6):
                bad.append(("color", f, k))
            # 板は箱の中に下から等間隔 (箱の子)
            want = area.matrix_world @ Vector((0, 0, -0.5 + (k + 0.5) / len(layers)))
            if not near(p.matrix_world.translation, want, 1e-4) or p.parent != area:
                bad.append(("height", f, k))
    check(u"%s: Maya 版と同じ (高さ違いに重ねる・上ほど薄い・模様の流れ・端のぼかし)" % label, not bad, str(bad[:2]))


def mask_value(edge_node_value, u, v):
    """マテリアルの箱のぼかしの式 (検証用に Python で同じ計算)。"""
    b = 2 * max(abs(u - 0.5), abs(v - 0.5))
    return min(1.0, max(0.0, (1 - b) / edge_node_value))


@section
def l05_ground_fog():
    new_scene()
    select()
    op(bl.GroundFogTool, layers=5, area_width=20.0, area_height=2.0)
    root = bpy.data.objects.get("dfx_groundFog1")
    area = rt.control(root, "area")
    layers = bl._layers(root)
    check("地霧: ルート・範囲の箱 (底が地面)・板 5 枚 (箱の子)", root is not None and len(layers) == 5
          and near(area.location, (0, 0, 1)) and near(area.scale, (20, 20, 2)) and root.get("dfx_camera") is None)
    frame(1)
    zs = [p.matrix_world.translation.z for p in layers]
    check("地霧: 板は地面の上に重なる (高さ 0.2, 0.6, 1.0, 1.4, 1.8)", near(zs, (0.2, 0.6, 1.0, 1.4, 1.8), 1e-6), str(zs))
    vs = [p.matrix_world @ v.co for v in layers[0].data.vertices for p in layers[:1]]
    check("地霧: 板は地面と平行で箱いっぱい", all(abs(v.z - 0.2) < 1e-6 for v in vs)
          and abs(max(v.x for v in vs) - 10) < 1e-6 and abs(min(v.y for v in vs) + 10) < 1e-6)
    mats = [_wx.material_of(p) for p in layers]
    ok, path = texture_ok(mats[0], "fog_tile")
    check("地霧: 霧の模様の絵 (繰り返し)・板ごとのマテリアル", ok and mats[0].node_tree.nodes["dfx_image"].extension == "REPEAT"
          and len(set(mats)) == 5, path)
    alphas = [m.node_tree.nodes["dfx_layer_alpha"].inputs[1].default_value for m in mats]
    v = rt.read_params(root, ml.GROUND_FOG.stored_params())
    check("地霧: 上ほど薄い (Maya 版の ground_layer_opacity)",
          near(alphas, [ml.ground_layer_opacity(v["opacity"], v["falloff"], k, 5) for k in range(5)], 1e-6)
          and all(a > b for a, b in zip(alphas, alphas[1:])), str(alphas))
    e = mats[0].node_tree.nodes["dfx_edge"].inputs[1].default_value
    check("地霧: 端のぼかし (中は同じ濃さ、端で 0)", abs(mask_value(e, 0.5, 0.5) - 1) < 1e-9
          and abs(mask_value(e, 0.8, 0.5) - 1) < 1e-9 and abs(mask_value(e, 1.0, 0.3)) < 1e-9
          and abs(mask_value(e, 0.925, 0.5) - 0.5) < 1e-6, str(e))
    check_ground(root, (1, 12, 33), u"地霧")
    # 箱を回して広げる・風
    area.location = (5, -3, 1)
    area.rotation_euler = (0, 0, math.radians(40))
    area.scale = (30, 12, 2)
    op(bwind.WindTool, strength=0.25)
    check("地霧: 風を作るとつながる", root.get("dfx_wind") == bpy.data.objects.get("dfx_wind1"))
    check_ground(root, (4, 21), u"地霧 (回した箱・風)")
    root["dfx_opacity"] = 0.3
    root["dfx_falloff"] = 0.0
    root["dfx_edge"] = 0.6
    root["dfx_tile"] = 4.0
    frame(22)
    check("地霧: パラメータを変えると次のフレームで反映 (落ち方 0 で全部同じ濃さ)",
          all(abs(_wx.material_of(p).node_tree.nodes["dfx_layer_alpha"].inputs[1].default_value - 0.3) < 1e-6
              for p in layers))
    check_ground(root, (22,), u"地霧 (変えた後)")
    root["dfx_layers"] = 3
    select(root)
    counts = tagged_counts()
    op(bscene.UpdateEffectsTool)
    area = rt.control(root, "area")
    check("地霧: 更新で 3 枚 (箱の位置・回転・大きさはそのまま)", len(bl._layers(root)) == 3
          and near(area.location, (5, -3, 1)) and near(area.scale, (30, 12, 2))
          and tagged_counts() == (counts[0] - 2, counts[1] - 2, counts[2] - 2), str((counts, tagged_counts())))
    check_ground(root, (7,), u"地霧 (更新後)")


@section
def l06_delete_all():
    op(bscene.DeleteAllTool)
    check("全削除: ツールで作った物が全部消える",
          not [o for o in bpy.data.objects if o.get(rt.TAG_KEY)]
          and not [m for m in bpy.data.meshes if m.get(rt.TAG_KEY)])


for fn in (l01_register, l02_far, l03_fog_cards, l04_fog_depth, l05_ground_fog, l06_delete_all):
    fn()
fails = [r for r in RESULTS if not r[1]]
print("\nSUMMARY: %d passed, %d failed" % (len(RESULTS) - len(fails), len(fails)))
for name, _, detail in fails:
    print("  FAIL:", name, detail)
sys.stdout.flush()
os._exit(1 if fails else 0)
