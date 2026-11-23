# -*- coding: utf-8 -*-
"""Blender 版のお化けブラー (残像・スミア・軌跡) の検証 (Blender を画面なしで動かす)。

    blender -b --factory-startup --python tests/blender_verify_ghost.py -- <リポジトリのフォルダ>

正解は Blender 自身にフレームを動かして読ませた行列 (scene.frame_set) と、Maya 版の計算
(ghost_ratio / speed_factor / smear_points / trail_taper ...)。
"""
import math
import os
import sys
import tempfile
import traceback

REPO = sys.argv[sys.argv.index("--") + 1]
sys.path.insert(0, REPO)

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

RESULTS = []
TMP = tempfile.mkdtemp(prefix="dfx_blender_ghost_")


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


def near_m(a, b, tol=1e-4):
    return all(abs(a[r][c] - b[r][c]) <= tol for r in range(4) for c in range(4))


def new_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import dummy_effect_maker.blender as dfx
    dfx.register()
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 60
    # 保存しておく (ユーザーのフォルダに書かない)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(TMP, "verify_ghost.blend"))


import dummy_effect_maker.blender as dfx  # noqa: E402
from dummy_effect_maker.blender import runtime as rt, ui  # noqa: E402

dfx.register()
from dummy_effect_maker.blender.tools import ghost_blur as bg, scene as bscene  # noqa: E402
from dummy_effect_maker.tools import ghost_blur as mg  # noqa: E402


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


def linear(obj):
    for fc in rt._fcurves(obj):
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"


def cube(name, location=(0, 0, 0), size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def make_scene():
    """mover: 1→21 で +X に 20 動き Z に 90° 回る (後は止まる)。
    carrier: 1→31 で +Y に 15 動く Empty。kid: carrier の子 (親の逆行列あり) で X に回る。
    follower: carrier にコピーロケーション (コンストレイントで動く)。
    """
    new_scene()
    mover = cube("mover")
    mover.keyframe_insert("location", frame=1)
    mover.keyframe_insert("rotation_euler", frame=1)
    mover.location = (20, 0, 0)
    mover.rotation_euler = (0, 0, math.radians(90))
    mover.keyframe_insert("location", frame=21)
    mover.keyframe_insert("rotation_euler", frame=21)
    linear(mover)
    carrier = bpy.data.objects.new("carrier", None)
    bpy.context.scene.collection.objects.link(carrier)
    carrier.keyframe_insert("location", frame=1)
    carrier.location = (0, 15, 0)
    carrier.keyframe_insert("location", frame=31)
    linear(carrier)
    kid = cube("kid", size=0.8)
    kid.parent = carrier
    kid.matrix_parent_inverse = Matrix.Translation((1.0, 0.0, 0.5))
    kid.location = (2, 0, 1)
    kid.keyframe_insert("rotation_euler", frame=1)
    kid.rotation_euler = (math.radians(180), 0, 0)
    kid.keyframe_insert("rotation_euler", frame=31)
    linear(kid)
    follower = cube("follower", size=0.6)
    con = follower.constraints.new("COPY_LOCATION")
    con.target = carrier
    con.use_offset = True
    follower.location = (0, -5, 0)
    bpy.context.scene.frame_set(1)
    return mover, carrier, kid, follower


def truth(obj, t):
    """Blender にフレーム t (小数可) を評価させた、obj の行列。今のフレームは戻す。"""
    scene = bpy.context.scene
    cur, sub = scene.frame_current, scene.frame_subframe
    f = math.floor(t)
    scene.frame_set(int(f), subframe=t - f)
    m = obj.matrix_world.copy()
    scene.frame_set(cur, subframe=sub)
    return m


def mesh_co(obj):
    return [tuple(v.co) for v in obj.data.vertices]


def eval_world(obj):
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    out = [obj.matrix_world @ v.co for v in me.vertices]
    ev.to_mesh_clear()
    return out


# ---------------------------------------------------------------- 登録
@section
def g01_register():
    labels = [t.label for t in ui._TOOLS]
    want = set([u"残像", u"スミア (引き伸ばし)", u"軌跡", u"お化けブラーを更新", u"お化けブラーを外す"])
    check("登録: お化けブラーのツール", want <= set(labels), str(labels))
    tabs = set(t.tab for t in ui._TOOLS if t.label in want)
    check("登録: タブは「お化けブラー」", tabs == set([mg.TAB]), str(tabs))
    check("登録: パラメータは Maya 版と同じ",
          bg.AfterimageTool.params is mg.AfterimageTool.params and bg.SmearTool.params is mg.SmearTool.params
          and bg.TrailTool.params is mg.TrailTool.params)
    from dummy_effect_maker.blender.tools import _build as bd
    check("登録: 作り直し・毎フレームの更新", all(t in bd.BUILDERS and t in rt.UPDATERS for t in mg.GHOST_TYPES))


# ---------------------------------------------------------------- 残像
def afterimage_expect(root, target, frame):
    """残像ごとの (index, 期待する行列 or None, 不透明度, 色) (正解の行列と Maya 版の式)。"""
    v = rt.read_params(root, mg.KINDS[mg.AFTERIMAGE].stored_params())
    count = int(root["dfx_ghost_count"])
    now = truth(target, frame).translation
    out = {}
    for i in range(count):
        dt = max(v["interval"], 0.001) * (i + 1)
        t = frame - dt
        was = truth(target, t).translation
        f = mg.speed_factor((now - was).length / dt, v["min_speed"], v["full_speed"])
        a = mg.ghost_ratio(i, count)
        op_ = mg.lerp(v["opacity_start"], v["opacity_end"], a) * f
        sc = 1.0 + (v["scale_end"] - 1.0) * a
        color = tuple(mg.lerp(c0, c1, a) for c0, c1 in zip(v["color_start"], v["color_end"]))
        out[i] = (t, op_, sc, color)
    return out


def check_afterimage(root, target, frames, label, tol=1e-4):
    ghosts = bg._owned_objects(root, "dfx_part")
    bad = []
    seen_visible = False
    for f in frames:
        exp = afterimage_expect(root, target, f)
        exp_m = {}
        for g in ghosts:
            t, op_, sc, _ = exp[g["dfx_index"]]
            exp_m[g.name] = (truth(g["dfx_part"], t) @ Matrix.Diagonal((sc, sc, sc, 1.0))) if op_ > 0.001 else None
        bpy.context.scene.frame_set(f)
        for g in ghosts:
            t, op_, sc, color = exp[g["dfx_index"]]
            mat = g["dfx_mat"]
            mix = mat.node_tree.nodes["dfx_mix"].inputs[0].default_value
            emit = tuple(mat.node_tree.nodes["dfx_emission"].inputs["Color"].default_value)[:3]
            if abs(mix - op_) > 1e-5 or not near(emit, color, 1e-5):
                bad.append((f, g.name, "mat", mix, op_))
            if exp_m[g.name] is None:
                if g.matrix_world.to_scale().length > 1e-9:
                    bad.append((f, g.name, "shown"))
                continue
            seen_visible = True
            if not near_m(g.matrix_world, exp_m[g.name], tol):
                bad.append((f, g.name, tuple(g.matrix_world.translation), tuple(exp_m[g.name].translation)))
    check(u"残像 (%s): 過去の位置・回転・大きさ、不透明度・色が Maya 版の式どおり" % label,
          not bad and seen_visible, str(bad[:3]))


@section
def g02_afterimage():
    mover, carrier, kid, follower = make_scene()
    before = mesh_co(mover)
    select(mover)
    res = op(bg.AfterimageTool, count=4, interval=2.0)
    root = bpy.data.objects.get("dfx_afterimage1")
    ghosts = bg._owned_objects(root, "dfx_part") if root else []
    check("残像: ルート・残像 4 体 (付けたオブジェクトを dfx_source で指す)",
          res == {"FINISHED"} and root is not None and len(ghosts) == 4 and root.get("dfx_source") == mover
          and bg.BAKE_KEY not in root)
    check_afterimage(root, mover, (5, 10, 15, 22, 24, 30), u"キーで動く・回る")
    bpy.context.scene.frame_set(10)
    ev = eval_world(ghosts[0])
    check("残像: コピーは元のメッシュの形 (ジオメトリーノード)", len(ev) == len(mover.data.vertices))
    bpy.context.scene.frame_set(30)
    check("残像: 止まっている時は出ない", all(g.matrix_world.to_scale().length < 1e-9 for g in ghosts))
    # すぐ反映
    root["dfx_opacity_start"] = 0.9
    root["dfx_scale_end"] = 0.5
    root["dfx_interval"] = 1.5
    check_afterimage(root, mover, (9, 12), u"パラメータを変えた次のフレーム")
    # 更新で数が変わる
    root["dfx_count"] = 2
    select(root)
    op(bscene.UpdateEffectsTool)
    ghosts = bg._owned_objects(root, "dfx_part")
    check("残像: 「エフェクトを更新」で 2 体", len(ghosts) == 2 and root["dfx_ghost_count"] == 2)
    n_obj, n_mesh, n_mat = len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)
    op(bscene.UpdateEffectsTool)
    check("残像: 更新を繰り返しても数が増えない", (n_obj, n_mesh, n_mat) ==
          (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)))
    check_afterimage(root, mover, (8,), u"更新後")

    # 親が動く子 (親の逆行列あり)
    select(kid)
    op(bg.AfterimageTool, count=3, interval=1.5)
    kroot = bpy.data.objects.get("dfx_afterimage2")
    check_afterimage(kroot, kid, (6, 13, 20), u"動く Empty の子")
    # 子のメッシュを持つ Empty
    select(carrier)
    op(bg.AfterimageTool, count=2, interval=2.0)
    croot = bpy.data.objects.get("dfx_afterimage3")
    check("残像: Empty に付けると子のメッシュをコピー",
          croot is not None and all(g["dfx_part"] == kid for g in bg._owned_objects(croot, "dfx_part")))
    check_afterimage(croot, carrier, (7, 16), u"Empty (子のメッシュ)")
    # コンストレイントで動く物 (フレーム範囲を読んで覚える)
    select(follower)
    op(bg.AfterimageTool, count=3, interval=1.0)
    froot = bpy.data.objects.get("dfx_afterimage4")
    check("残像: コンストレイントで動く物は動きを覚える", froot is not None and bg.BAKE_KEY in froot)
    check_afterimage(froot, follower, (4, 11, 25), u"コンストレイント")
    froot["dfx_interval"] = 1.5
    check_afterimage(froot, follower, (12,), u"コンストレイント・小数の間隔 (補間)", tol=1e-3)
    # 外す
    select(root)
    op(bg.GhostBlurRemoveTool)
    check("残像を外す: ルート・残像が消え、元のメッシュ・キーはそのまま",
          bpy.data.objects.get("dfx_afterimage1") is None and mesh_co(mover) == before
          and not mover.modifiers and mover.animation_data is not None
          and not [o for o in bpy.data.objects if o.name.startswith("dfx_afterimage1_")])


# ---------------------------------------------------------------- スミア
def smear_expect(root, target, frame):
    v = rt.read_params(root, mg.KINDS[mg.SMEAR].stored_params())
    lat = root["dfx_lattice"]
    k = max(1, int(v["samples"]))
    dt = max(0.0, v["frames"]) * max(0.0, v["amount"]) / k
    mats = [bg._rows(truth(target, frame - s * dt)) for s in range(k + 1)]
    inv = bg._rows(truth(target, frame).inverted())
    n = int(lat["dfx_divisions"])
    out = mg.smear_points(mats, inv, tuple(lat["dfx_center"]), tuple(lat["dfx_size"]), (n, n, n), v,
                          mg.smear_cap(v, lat["dfx_obj_size"]))
    span, mid = lat["dfx_span"], lat["dfx_mid"]
    return [mid[i % 3] + x * span[i % 3] for i, x in enumerate(out)]


def lattice_co(root):
    lat = root["dfx_lattice"].data
    return [c for p in lat.points for c in p.co_deform]


def check_smear(root, target, frames, label):
    bad = []
    for f in frames:
        exp = smear_expect(root, target, f)
        bpy.context.scene.frame_set(f)
        got = lattice_co(root)
        if len(got) != len(exp) or not near(got, exp, 1e-4):
            bad.append((f, max(abs(a - b) for a, b in zip(got, exp))))
    check(u"スミア (%s): ラティスの点が Maya 版の smear_points どおり" % label, not bad, str(bad[:3]))


def extent(points, origin, direction):
    proj = [(p - origin).dot(direction) for p in points]
    return min(proj), max(proj)


@section
def g03_smear():
    mover, carrier, kid, follower = make_scene()
    before = mesh_co(mover)
    select(mover)
    op(bg.SmearTool)
    root = bpy.data.objects.get("dfx_smear1")
    lat = root.get("dfx_lattice") if root else None
    mod = mover.modifiers.get(root.name) if root else None
    check("スミア: ルート・ラティス (オブジェクトの子)・Lattice モディファイアー",
          root is not None and lat is not None and lat.parent == mover and mod is not None
          and mod.type == "LATTICE" and mod.object == lat and len(lat.data.points) == 6 ** 3)
    check("スミア: ラティスは大きさ 1 + 余白 (中心は原点)",
          near(lat["dfx_center"], (0, 0, 0), 1e-6) and near(lat["dfx_size"], (1.1, 1.1, 1.1), 1e-6)
          and abs(lat["dfx_obj_size"] - 1.0) < 1e-6, str((list(lat["dfx_center"]), list(lat["dfx_size"]))))
    check_smear(root, mover, (4, 10, 18, 23, 40), u"キーで動く・回る")
    # 形: 進行方向の後ろへ伸びる (前はほぼそのまま)
    bpy.context.scene.frame_set(10)
    m = mover.matrix_world
    vel = (truth(mover, 10).translation - truth(mover, 9).translation).normalized()
    rest = [m @ Vector(c) for c in before]
    got = eval_world(mover)
    r_lo, r_hi = extent(rest, m.translation, vel)
    g_lo, g_hi = extent(got, m.translation, vel)
    check("スミア: 動いている時、後ろへ伸びる (前はほぼそのまま)", g_lo < r_lo - 0.8 and abs(g_hi - r_hi) < 0.1,
          str(((r_lo, r_hi), (g_lo, g_hi))))
    bpy.context.scene.frame_set(40)
    got = eval_world(mover)
    rest = [mover.matrix_world @ Vector(c) for c in before]
    check("スミア: 止まっている時は変形しない", all((a - b).length < 1e-4 for a, b in zip(got, rest)))
    check("スミア: 元のメッシュのデータは変えない", mesh_co(mover) == before)
    # すぐ反映
    root["dfx_amount"] = 0.0
    bpy.context.scene.frame_set(11)
    got = eval_world(mover)
    rest = [mover.matrix_world @ Vector(c) for c in before]
    check("スミア: 強さ 0 (次のフレーム) で変形しない", all((a - b).length < 1e-4 for a, b in zip(got, rest)))
    root["dfx_amount"] = 2.0
    root["dfx_start"] = 0.5
    root["dfx_samples"] = 3
    check_smear(root, mover, (12,), u"パラメータを変えた次のフレーム")
    root["dfx_max_stretch"] = 0.5
    bpy.context.scene.frame_set(13)
    got = eval_world(mover)
    r_lo = extent([mover.matrix_world @ Vector(c) for c in before], mover.matrix_world.translation, vel)[0]
    g_lo = extent(got, mover.matrix_world.translation, vel)[0]
    check("スミア: 最大の伸びで伸びすぎない", r_lo - 0.6 < g_lo < r_lo - 0.05, str((r_lo, g_lo)))
    check_smear(root, mover, (14,), u"最大の伸び")
    # 1 つまで
    select(mover)
    try:
        res = op(bg.SmearTool)
    except RuntimeError:
        res = None
    check("スミア: 1 つのオブジェクトに 1 つまで", res != {"FINISHED"} and bpy.data.objects.get("dfx_smear2") is None)
    # 更新 (分割数)
    root["dfx_divisions"] = 4
    select(root)
    op(bscene.UpdateEffectsTool)
    lat = root.get("dfx_lattice")
    mods = [md for md in mover.modifiers if md.type == "LATTICE"]
    check("スミア: 更新で分割数 4 (モディファイアーは 1 つのまま)",
          lat is not None and len(lat.data.points) == 64 and len(mods) == 1 and mods[0].object == lat
          and len([l for l in bpy.data.lattices if l.get(rt.OWNER_KEY) == root]) == 1)
    check_smear(root, mover, (9,), u"更新後")
    # 親が動く子
    select(kid)
    op(bg.SmearTool, frames=2.0, samples=4)
    kroot = bpy.data.objects.get("dfx_smear2")
    check_smear(kroot, kid, (5, 17, 28), u"動く Empty の子")
    # コンストレイント
    select(follower)
    op(bg.SmearTool, frames=2.0, samples=2)
    froot = bpy.data.objects.get("dfx_smear3")
    check("スミア: コンストレイントで動く物は動きを覚える", bg.BAKE_KEY in froot)
    check_smear(froot, follower, (8, 20), u"コンストレイント")
    # 外す
    select(root)
    op(bg.GhostBlurRemoveTool)
    bpy.context.scene.frame_set(10)
    got = eval_world(mover)
    rest = [mover.matrix_world @ Vector(c) for c in before]
    check("スミアを外す: モディファイアー・ラティスが消え、元の形に戻る",
          bpy.data.objects.get("dfx_smear1") is None and not mover.modifiers
          and not [l for l in bpy.data.lattices if l.name.startswith("dfx_smear1")]
          and mesh_co(mover) == before and all((a - b).length < 1e-6 for a, b in zip(got, rest)))
    select(kid)   # 付けたオブジェクトを選んでも外せる
    op(bg.GhostBlurRemoveTool)
    check("スミアを外す: 付けたオブジェクトを選んでも外せる",
          bpy.data.objects.get("dfx_smear2") is None and not kid.modifiers)


# ---------------------------------------------------------------- 軌跡
def trail_expect(root, frame):
    """行ごとの (中心, 帯の向きの半分のベクトル (帯) / 半径 (チューブ), 接線) (正解の位置と trail_taper)。"""
    v = rt.read_params(root, mg.KINDS[mg.TRAIL].stored_params())
    obj = bg._owned_objects(root, "dfx_trail")[0]
    segs = int(obj.data["dfx_segments"])
    a, b = rt.control(root, "a"), rt.control(root, "b")
    dt = max(0.0, v["length"]) / segs
    A = [truth(a, frame - j * dt).translation for j in range(segs + 1)]
    B = [truth(b, frame - j * dt).translation for j in range(segs + 1)]
    mid = [(p + q) / 2 for p, q in zip(A, B)]
    rows = []
    for j in range(segs + 1):
        taper = mg.trail_taper(j, segs, v["width_start"], v["width_end"])
        tan = mid[max(j - 1, 0)] - mid[min(j + 1, segs)]
        rows.append((mid[j], (B[j] - A[j]) * (0.5 * taper), tan))
    path = sum((mid[j] - mid[j + 1]).length for j in range(segs))
    return rows, path


def trail_verts(root):
    obj = bg._owned_objects(root, "dfx_trail")[0]
    return [obj.matrix_world @ v.co for v in obj.data.vertices], obj.data["dfx_per_row"]


def check_trail(root, frames, label, tube=False):
    bad = []
    for f in frames:
        rows, path = trail_expect(root, f)
        bpy.context.scene.frame_set(f)
        verts, per = trail_verts(root)
        if path <= 1e-4:
            if max((p - verts[0]).length for p in verts) > 1e-9:
                bad.append((f, "shown"))
            continue
        for j, (mid, half, tan) in enumerate(rows):
            ring = verts[j * per:(j + 1) * per]
            if tube:
                c = sum(ring, Vector()) / len(ring)
                radius = half.length
                if (c - mid).length > 1e-4 or any(abs((p - mid).length - radius) > 1e-4 for p in ring):
                    bad.append((f, j, "ring", tuple(c), tuple(mid)))
                elif tan.length > 1e-6 and any(abs((p - mid).dot(tan.normalized())) > 1e-4 for p in ring):
                    bad.append((f, j, "perp"))
            else:
                if not (near(ring[0], mid + half) and near(ring[1], mid - half)) and \
                        not (near(ring[0], mid - half) and near(ring[1], mid + half)):
                    bad.append((f, j, tuple(ring[0]), tuple(mid + half)))
    check(u"軌跡 (%s): 通った跡に沿い、太さは trail_taper どおり" % label, not bad, str(bad[:3]))


@section
def g04_trail():
    mover, carrier, kid, follower = make_scene()
    before = mesh_co(mover)
    select(mover)
    op(bg.TrailTool, length=8.0, segments=8)
    root = bpy.data.objects.get("dfx_trail1")
    a, b = rt.control(root, "a"), rt.control(root, "b")
    check("軌跡: ルート・両端の Empty (オブジェクトの子、ローカルの上 ±半分)",
          root is not None and a.parent == mover and b.parent == mover
          and near(a.location, (0, 0, 0.5), 1e-6) and near(b.location, (0, 0, -0.5), 1e-6))
    obj = bg._owned_objects(root, "dfx_trail")[0]
    check("軌跡: 帯のメッシュ (9 行 × 2 頂点、UV あり)", len(obj.data.vertices) == 18
          and len(obj.data.polygons) == 8 and len(obj.data.uv_layers) == 1)
    check_trail(root, (3, 9, 16, 25, 40), u"帯・キーで動く・回る")
    # すぐ反映
    root["dfx_width_start"] = 0.5
    root["dfx_width_end"] = 1.5
    root["dfx_length"] = 4.0
    root["dfx_opacity_start"] = 0.3
    check_trail(root, (12,), u"パラメータを変えた次のフレーム")
    alpha = obj.data.materials[0].node_tree.nodes["dfx_alpha"]
    check("軌跡: 不透明度 (根元 → 先) がすぐ反映", abs(alpha.inputs[2].default_value - 0.3) < 1e-6
          and abs(alpha.inputs[3].default_value - 0.0) < 1e-6)
    # 端を動かす (キーも可)
    a.location = (0, 0, 1.0)
    a.keyframe_insert("location", frame=1)
    a.location = (0, 0, 2.0)
    a.keyframe_insert("location", frame=21)
    linear(a)
    check_trail(root, (14,), u"端の Empty を動かす・キー")
    # 更新 (形・分割数)
    root["dfx_shape"] = mg.SHAPE_TUBE
    root["dfx_segments"] = 4
    root["dfx_sides"] = 6
    select(root)
    op(bscene.UpdateEffectsTool)
    obj = bg._owned_objects(root, "dfx_trail")[0]
    check("軌跡: 更新でチューブ 5 行 × 6 (端の Empty はそのまま)",
          len(obj.data.vertices) == 30 and rt.control(root, "a") == a and a.animation_data is not None)
    check_trail(root, (10, 18), u"チューブ", tube=True)
    check("軌跡: 元のメッシュのデータ・モディファイアーは変えない", mesh_co(mover) == before and not mover.modifiers)
    # 親が動く子・コンストレイント
    select(kid)
    op(bg.TrailTool, shape=mg.SHAPE_TUBE, length=6.0, segments=6, sides=8)
    kroot = bpy.data.objects.get("dfx_trail2")
    check_trail(kroot, (8, 20), u"動く Empty の子・チューブ", tube=True)
    select(follower)
    op(bg.TrailTool, length=5.0, segments=5)
    froot = bpy.data.objects.get("dfx_trail3")
    check("軌跡: コンストレイントで動く物は動きを覚える", bg.BAKE_KEY in froot)
    check_trail(froot, (9, 22), u"コンストレイント")
    # 外す
    select(root)
    op(bg.GhostBlurRemoveTool)
    check("軌跡を外す: ルート・帯・両端の Empty が消える",
          bpy.data.objects.get("dfx_trail1") is None and bpy.data.objects.get("dfx_trail1_a") is None
          and bpy.data.objects.get("dfx_trail1_trail") is None and not mover.children)


@section
def g05_errors():
    make_scene()
    select()
    try:
        res = op(bg.AfterimageTool)
    except RuntimeError:
        res = None
    check("何も選ばないとエラー", res != {"FINISHED"} and not bg.ghost_roots())
    emp = bpy.data.objects.new("lonely", None)
    bpy.context.scene.collection.objects.link(emp)
    select(emp)
    try:
        res = op(bg.SmearTool)
    except RuntimeError:
        res = None
    check("メッシュの無い物にスミアはエラー", res != {"FINISHED"} and not bg.ghost_roots())
    op(bg.TrailTool)
    check("軌跡はメッシュの無い物にも付く", len(bg.ghost_roots()) == 1)


for fn in (g01_register, g02_afterimage, g03_smear, g04_trail, g05_errors):
    fn()
fails = [r for r in RESULTS if not r[1]]
print("\nSUMMARY: %d passed, %d failed" % (len(RESULTS) - len(fails), len(fails)))
for name, _, detail in fails:
    print("  FAIL:", name, detail)
sys.stdout.flush()
os._exit(1 if fails else 0)
