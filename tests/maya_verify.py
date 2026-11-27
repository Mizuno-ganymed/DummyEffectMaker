# -*- coding: utf-8 -*-
"""Maya 版の検証 (mayapy で Maya を画面なしで動かす)。全部のエフェクトを作り、動き・更新・ベイク・書き出しを確かめる。

    mayapy tests/maya_verify.py                 全部 (数分かかる)
    mayapy tests/maya_verify.py t53 t45_        名前がこれで始まる節だけ (t53_leaves など)
    mayapy tests/maya_verify.py <リポジトリ> t53  リポジトリの場所を指定する時 (既定はこのファイルの 1 つ上)

mayapy は Maya のインストール先の bin にある (例: C:/Program Files/Autodesk/Maya2027/bin/mayapy.exe)。
日本語を出すので、環境変数 PYTHONIOENCODING=utf-8 を付けて動かす。
一時フォルダだけを使い、ユーザーのフォルダ (マイドキュメント・環境設定) には書かない。
"""
import math
import os
import sys
import tempfile
import traceback

_ARGS = sys.argv[1:]
if _ARGS and os.path.isdir(os.path.join(_ARGS[0], "dummy_effect_maker")):
    REPO = _ARGS.pop(0)
else:
    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONLY = _ARGS   # 動かす節の名前の始まり (空なら全部)
sys.path.insert(0, REPO)

# ランチャーの UI も検証するため、Maya より先に QApplication を作る
# (後だと Maya が QGuiApplication を作ってしまいウィジェットを作れない)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dummy_effect_maker.ui.qt import QtWidgets as _QtWidgets  # noqa: E402
_APP = _QtWidgets.QApplication([])

import maya.standalone
maya.standalone.initialize(name="python")
from maya import cmds

cmds.undoInfo(state=True, infinity=True)
# 本物の環境設定ファイルを読み書きしない
os.environ["DUMMY_EFFECT_MAKER_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from dummy_effect_maker.core import registry, ToolContext, ToolError, execute
from dummy_effect_maker.core import host as host_mod
from dummy_effect_maker.tools import homing, cleanup
from dummy_effect_maker.tools import preset as _preset
from dummy_effect_maker.tools import _maya_utils as mu

RESULTS = []
LOGS = []


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


HOST = host_mod.detect()
CTX = ToolContext(HOST, log=lambda m: LOGS.append(m))


def run_tool(tool_cls, **values):
    tool = tool_cls()
    tool.set_values(values)
    result = execute(tool, CTX)
    if isinstance(result, str) and result.startswith("|"):
        result = result.split("|")[-1]  # テストでは短い名前で扱う
    return result


def create(a=None, b=None, c=None, **values):
    """追尾弾を作り、必要ならロケータを動かす。(root, [locA, locB, locC])"""
    root = run_tool(homing.HomingTool, **values)
    locs = ["%s|%s_loc%s" % (root, root, s) for s in "ABC"]
    for loc, p in zip(locs, (a, b, c)):
        if p is not None:
            cmds.setAttr(loc + ".translate", *p, type="double3")
    return root, locs


def pos(node, t):
    # 時間の移動はアンドゥ履歴に積まれるので、検証用の移動は記録しない
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        cmds.currentTime(t, edit=True)
    finally:
        cmds.undoInfo(stateWithoutFlush=True)
    return cmds.xform(node, q=True, ws=True, t=True)


def vis(node, t):
    return cmds.getAttr(node + ".visibility", time=t)


def seen(node, t):
    """transform とシェイプの両方を見た実際の表示。"""
    s = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
    return bool(cmds.getAttr(node + ".visibility", time=t)) and \
        bool(cmds.getAttr(s + ".visibility", time=t))


def dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def near(a, b, tol=1e-3):
    return dist(a, b) < tol


def new_scene():
    cmds.file(new=True, force=True)


def bullets_of(root):
    return cmds.listRelatives("%s|%s_bullets" % (root, root), children=True,
                              fullPath=True) or []


def confirm_yes(fn):
    orig = cmds.confirmDialog
    cmds.confirmDialog = lambda **kw: u"削除"
    try:
        return fn()
    finally:
        cmds.confirmDialog = orig


def sphere_vertex_count(div):
    cmds.undoInfo(stateWithoutFlush=False)  # 検証用の一時オブジェクトはアンドゥ履歴に残さない
    try:
        tmp = cmds.polySphere(subdivisionsAxis=div, subdivisionsHeight=max(2, div // 2), ch=False)[0]
        n = cmds.polyEvaluate(tmp, vertex=True)
        cmds.delete(tmp)
    finally:
        cmds.undoInfo(stateWithoutFlush=True)
    return n


# =====================================================================
@section
def t01_registry():
    errs = []
    tools = registry.discover("dummy_effect_maker.tools",
                              on_error=lambda m, tb: errs.append((m, tb)))
    labels = [t.label for t in tools]
    check("registry: エラーなし", not errs, str(errs))
    check("registry: 追尾弾 / 追尾弾を更新", u"追尾弾" in labels and u"追尾弾を更新" in labels, str(labels))
    check("registry: ロケータ作成ボタンは無くなった", not [l for l in labels if u"ロケータ" in l])
    check("host: MayaHost", HOST.name == "maya")


@section
def t02_create_everything():
    new_scene()
    other = cmds.polyCube(name="unrelated")[0]
    cmds.select(other)  # 何か選択されていても関係なく作れる
    root = run_tool(homing.HomingTool)
    check("作成: ルート", root == "dfx_homing1", root)
    children = [c.split("|")[-1] for c in cmds.listRelatives(root, children=True)]
    check("構成: ロケータ A/B/C がルートの子 (先頭)", children[:3] == [
        root + "_locA", root + "_locB", root + "_locC"], str(children))
    check("構成: path / bulletSrc / bullets", sorted(children[3:]) == sorted(
        [root + "_path", root + "_bulletSrc", root + "_bullets"]), str(children))
    locs = ["%s|%s_loc%s" % (root, root, s) for s in "ABC"]
    check("ロケータ: locator シェイプ", all(cmds.listRelatives(l, shapes=True, type="locator") for l in locs))
    check("ロケータ: 既定の配置", near(pos(locs[0], 1), (0, 0, 0)) and near(pos(locs[1], 1), (5, 2, 0))
          and near(pos(locs[2], 1), (10, 0, 0)))
    conns = [cmds.listConnections("%s.dfx_locator%s" % (root, s)) for s in "ABC"]
    check("ロケータ: ルートに接続済み", [c[0] for c in conns] == [l.split("|")[-1] for l in locs], str(conns))
    check("アトリビュート: 設定済み", cmds.getAttr(root + ".dfx_interval") == 2.0
          and cmds.getAttr(root + ".dfx_radius") == 0.1
          and cmds.getAttr(root + ".dfx_shape", asString=True) == u"球体")
    attrs = cmds.listAttr(root, userDefined=True)
    check("アトリビュート: 配置用 (距離・高さ) は保存しない",
          "dfx_length" not in attrs and "dfx_height" not in attrs)
    check("作成後: ルートが選択される", cmds.ls(sl=True) == [root])
    check("ルート: 移動ロック", cmds.getAttr(root + ".tx", lock=True))
    check("unrelated は触らない", cmds.objExists(other) and not cmds.listRelatives(other, parent=True))

    bl = bullets_of(root)
    check("弾数 24", len(bl) == 24, str(len(bl)))
    first, last = bl[0], bl[-1]
    check("弾1: f1 で A", near(pos(first, 1), (0, 0, 0)))
    check("弾1: f5 で B", near(pos(first, 5), (5, 2, 0), 1e-2), str(pos(first, 5)))
    check("弾1: f9 で C", near(pos(first, 9), (10, 0, 0)))
    check("最終弾: f47 A / f55 C", near(pos(last, 47), (0, 0, 0)) and near(pos(last, 55), (10, 0, 0)))
    vs = [seen(first, t) for t in (-5, 0, 0.5, 1, 5, 9, 9.5, 10, 30)]
    check("弾1 表示: 1〜9 のみ", vs == [0, 0, 0, 1, 1, 1, 1, 0, 0], str(vs))
    pts = [pos(first, 1 + i * 0.5) for i in range(17)]
    steps = [dist(pts[i], pts[i + 1]) for i in range(16)]
    spread = (max(steps) - min(steps)) / (sum(steps) / len(steps))
    check("等速", spread < 0.03, "spread=%.4f" % spread)
    cmds.currentTime(3)
    m = cmds.xform(first, q=True, ws=True, matrix=True)
    p0, p1 = pos(first, 2.95), pos(first, 3.05)
    tan = [p1[i] - p0[i] for i in range(3)]
    dot = sum(m[i] * tan[i] for i in range(3)) / (math.sqrt(sum(x * x for x in m[0:3])) *
                                                 math.sqrt(sum(t * t for t in tan)))
    check("follow: X 軸が進行方向", dot > 0.999, "%.5f" % dot)
    shp = cmds.listRelatives(first, shapes=True, fullPath=True)[0]
    check("インスタンス: シェイプ共有", len(cmds.listRelatives(shp, allParents=True)) == 25)
    sg = root + "_matSG"
    bad = [b for b in bl if sg not in (cmds.listSets(object=b + "|" + shp.split("|")[-1], type=1) or [])]
    check("マテリアル: 全弾", not bad, str(bad[:2]))
    check("マテリアル: 色", near(cmds.getAttr(root + "_mat.outColor")[0], (1.0, 0.75, 0.3)))

    # 子のロケータを動かすと追従
    cmds.setAttr(locs[1] + ".translate", 5, 6, 3, type="double3")
    check("追従: B を動かす", near(pos(first, 5), (5, 6, 3), 1e-2), str(pos(first, 5)))
    cmds.setAttr(locs[2] + ".translate", 20, 0, -4, type="double3")
    check("追従: C を動かす", near(pos(first, 9), (20, 0, -4)))
    # 名前を変えても更新できる
    new_a = cmds.rename(locs[0], "muzzle")
    cmds.setAttr(root + "|" + new_a + ".translate", 0, 3, 0, type="double3")
    cmds.select(root)
    run_tool(homing.HomingUpdateTool)
    check("ロケータ名を変えても更新できる", near(pos(bullets_of(root)[0], 1), (0, 3, 0)))

    tagged = set(mu.list_tagged())
    for n in (root, root + "_mat", root + "_matSG", root + "|muzzle"):
        check("タグ: %s" % n, cmds.ls(n, long=True)[0] in tagged)


@section
def t03_undo_redo():
    new_scene()
    cmds.flushUndo()
    before = set(cmds.ls())
    root, _ = create()
    cmds.undo()
    left = sorted(set(cmds.ls()) - before)
    check("アンドゥ 1 回でロケータごと全て消える", not cmds.objExists(root) and not left, str(left[:10]))
    cmds.redo()
    bl = bullets_of(root)
    check("リドゥで復元", len(bl) == 24 and cmds.objExists(root + "_locA"))
    check("リドゥ後: f5 で B", near(pos(bl[0], 5), (5, 2, 0), 1e-2))
    check("リドゥ後: 表示", [seen(bl[0], t) for t in (0, 1, 9, 10)] == [0, 1, 1, 0])
    before2 = set(cmds.ls())
    run_tool(homing.HomingTool, path_type=homing.PATH_LINEAR, use_instance=False)
    cmds.undo()
    check("2 回目だけアンドゥ", sorted(set(cmds.ls()) - before2) == [] and len(bullets_of(root)) == 24)
    mps = cmds.ls(root + "_motion_*", type="motionPath")
    check("motionPath: 24 個・タグ付き", len(mps) == 24 and all(
        cmds.attributeQuery(mu.TAG_ATTR, node=m, exists=True) for m in mps))


@section
def t04_options():
    new_scene()
    root, locs = create(length=8, height=5)
    check("配置オプション: 距離 8 / 高さ 5",
          near(pos(locs[1], 1), (4, 5, 0)) and near(pos(locs[2], 1), (8, 0, 0)))
    new_scene()
    root, locs = create(b=(3, 4, 0), path_type=homing.PATH_LINEAR, start_frame=10, end_frame=12,
                        interval=0.5, travel_frames=4, shape=homing.SHAPE_SQUASHED, stretch=4.0,
                        use_instance=False, radius=0.5)
    bl = bullets_of(root)
    check("小数間隔: 5 発", len(bl) == 5)
    check("折れ線: degree 1", cmds.getAttr(root + "_path.degree") == 1)
    lab, lbc = 5.0, math.sqrt(65)
    tb = 10 + 4 * lab / (lab + lbc)
    check("折れ線: 弧長比の時刻で B", near(pos(bl[0], tb), (3, 4, 0), 1e-2), str(pos(bl[0], tb)))
    vs = [seen(bl[1], t) for t in (10, 10.4, 10.5, 14.5, 15.4, 15.5)]
    check("2 発目の表示", vs == [0, 0, 1, 1, 1, 0], str(vs))
    shp = cmds.listRelatives(bl[0], shapes=True, fullPath=True)[0]
    check("複製: シェイプ非共有", len(cmds.listRelatives(shp, allParents=True)) == 1)
    check("伸び率 4・半径 0.5: scale (2, .5, .5)", near(cmds.getAttr(bl[0] + ".scale")[0], (2, .5, .5)))


@section
def t05_errors():
    new_scene()
    for name, vals in ((u"終了 < 開始", dict(start_frame=10, end_frame=5)),
                       (u"弾数超過", dict(start_frame=0, end_frame=1000, interval=0.1))):
        before = set(cmds.ls())
        try:
            run_tool(homing.HomingTool, **vals)
            check("エラー: " + name, False, "例外なし")
        except ToolError as e:
            check("エラー: " + name, not (set(cmds.ls()) - before), u"%s" % e)


@section
def t06_save_and_cleanup():
    new_scene()
    r1, _ = create()
    r2, _ = create(shape=homing.SHAPE_SQUASHED)
    check("2 回実行: 別名", (r1, r2) == ("dfx_homing1", "dfx_homing2"))
    check("2 回目のロケータは dfx_homing2_locA", cmds.objExists("dfx_homing2|dfx_homing2_locA"))
    path = os.path.join(tempfile.gettempdir(), "dfx_homing_verify.ma")
    cmds.file(rename=path)
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(path, open=True, force=True)
    os.remove(path)
    check("保存/再読込: 着弾", near(pos(bullets_of(r1)[0], 9), (10, 0, 0)))
    cmds.select(r2)
    run_tool(homing.HomingUpdateTool)
    check("保存/再読込後も更新できる", len(bullets_of(r2)) == 24)
    cube = cmds.polyCube(name="keepMe")[0]
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: ロケータごと全て消える", not cmds.ls("dfx_homing*") and not mu.list_tagged())
    check("全削除: 関係ないものは残る", cmds.objExists(cube))


@section
def t07_long_travel_visibility():
    for inst in (True, False):
        new_scene()
        root, _ = create(travel_frames=60, use_instance=inst)
        bl = bullets_of(root)
        mode = "instance" if inst else "duplicate"
        keyed = [b for b in bl if cmds.listConnections(
            cmds.listRelatives(b, shapes=True, fullPath=True)[0] + ".visibility")]
        check("着弾60 %s: シェイプにキーなし" % mode, not keyed)
        wrong = []
        for i, b in enumerate(bl):
            fire = 1 + 2 * i
            for f in range(0, 121):
                if seen(b, f) != (fire <= f <= fire + 60):
                    wrong.append((i, f))
        check("着弾60 %s: 発射〜着弾だけ表示 (全弾・全フレーム)" % mode, not wrong, str(wrong[:5]))


@section
def t08_adjust():
    new_scene()
    root, locs = create()
    P = root + ".dfx_"
    bl = bullets_of(root)
    keyable = cmds.listAttr(root, keyable=True) or []
    cb = cmds.listAttr(root, channelBox=True) or []
    live = ["dfx_radius", "dfx_shape", "dfx_stretch", "dfx_subdivisions",
            "dfx_colorR", "dfx_colorG", "dfx_colorB"]
    rebuild = ["dfx_start_frame", "dfx_end_frame", "dfx_interval", "dfx_travel_frames",
               "dfx_path_type", "dfx_use_instance"]
    check("CB: 即反映はキー可", all(a in keyable for a in live))
    check("CB: 要更新は表示のみ", all(a in cb and a not in keyable for a in rebuild))
    check("CB: TRS 非表示", not [a for a in keyable + cb if a[0] in "trs" and len(a) == 2])
    check("CB: (更新) 表示名", cmds.attributeQuery("dfx_interval", node=root, niceName=True).endswith(u"(更新)"))

    cmds.setAttr(P + "radius", 0.3)
    check("即反映: 半径", near(cmds.getAttr(bl[-1] + ".scale")[0], (0.3,) * 3))
    cmds.setAttr(P + "shape", 1); cmds.setAttr(P + "stretch", 2.5)
    check("即反映: 伸び", near(cmds.getAttr(bl[3] + ".scale")[0], (0.75, 0.3, 0.3)))
    cmds.setAttr(P + "shape", 0)
    check("即反映: 球体に戻す", near(cmds.getAttr(bl[3] + ".scale")[0], (0.3,) * 3))
    cmds.setAttr(P + "color", 0, 1, 0, type="float3")
    check("即反映: 色", near(cmds.getAttr(root + "_mat.outColor")[0], (0, 1, 0)))
    cmds.setAttr(P + "subdivisions", 20)
    check("即反映: 分割数", cmds.polyEvaluate(bl[5], vertex=True) == sphere_vertex_count(20))
    cmds.setKeyframe(P + "radius", time=1, value=0.1); cmds.setKeyframe(P + "radius", time=20, value=1.0)
    cmds.currentTime(20)
    check("即反映: 半径にキー", near(cmds.getAttr(bl[0] + ".scale")[0], (1,) * 3))
    cmds.cutKey(root, attribute="dfx_radius"); cmds.setAttr(P + "radius", 0.3)

    cmds.setAttr(P + "interval", 4); cmds.setAttr(P + "travel_frames", 20); cmds.setAttr(P + "path_type", 1)
    cmds.select(locs[1])  # 子のロケータを選んでも更新できる
    run_tool(homing.HomingUpdateTool)
    bl2 = bullets_of(root)
    check("更新: ロケータ選択でもルートが対象", cmds.ls(sl=True) == [root])
    check("更新: 12 発 / 着弾 20 / 折れ線", len(bl2) == 12 and near(pos(bl2[0], 21), (10, 0, 0))
          and cmds.getAttr(root + "_path.degree") == 1)
    check("更新: ロケータはそのまま", [cmds.objExists(l) for l in locs] == [True] * 3
          and near(pos(locs[1], 1), (5, 2, 0)))
    check("更新: 即反映の値は保持", near(cmds.getAttr(bl2[0] + ".scale")[0], (0.3,) * 3)
          and near(cmds.getAttr(root + "_mat.outColor")[0], (0, 1, 0)))
    cmds.select(root)
    run_tool(homing.HomingUpdateTool)
    n1 = len(cmds.ls())
    run_tool(homing.HomingUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))
    cmds.setAttr(P + "interval", 2)
    run_tool(homing.HomingUpdateTool)
    cmds.undo()
    check("更新のアンドゥ", len(bullets_of(root)) == 12)
    cmds.redo()
    cmds.setAttr(P + "use_instance", 0)
    run_tool(homing.HomingUpdateTool)
    bl3 = bullets_of(root)
    cmds.setAttr(P + "subdivisions", 8)
    check("複製に更新 → 分割数の即反映", cmds.polyEvaluate(bl3[4], vertex=True) == sphere_vertex_count(8))

    def expect_error(name, setup):
        setup()
        count = len(bullets_of(root))
        try:
            run_tool(homing.HomingUpdateTool)
            check("更新エラー: " + name, False, "例外なし")
        except ToolError as e:
            check("更新エラー: " + name, len(bullets_of(root)) == count, u"%s" % e)

    cube = cmds.polyCube()[0]
    expect_error(u"何も選択していない", lambda: cmds.select(clear=True))
    expect_error(u"関係ないものを選択", lambda: cmds.select(cube))
    expect_error(u"終了 < 開始", lambda: (cmds.setAttr(P + "end_frame", 0), cmds.select(root)))
    cmds.setAttr(P + "end_frame", 48)
    expect_error(u"ロケータ C を削除", lambda: (cmds.delete(locs[2]), cmds.select(root)))
    for _ in range(3):
        if cmds.objExists(locs[2]):
            break
        cmds.undo()
    cmds.select(root)
    run_tool(homing.HomingUpdateTool)
    check("ロケータを戻せば更新できる", len(bullets_of(root)) == 24)

    root2, _ = create()
    cmds.setAttr(root + ".dfx_interval", 8); cmds.setAttr(root2 + ".dfx_interval", 16)
    cmds.select([bullets_of(root)[0], root2])
    run_tool(homing.HomingUpdateTool)
    check("複数更新", (len(bullets_of(root)), len(bullets_of(root2))) == (6, 3))


@section
def t09_stretch_and_migration():
    new_scene()
    root, locs = create(b=(5, 4, 3), shape=homing.SHAPE_SQUASHED, stretch=3.0, radius=0.2)
    b = bullets_of(root)[0]
    worst = 1.0
    for f in (1.5, 3, 5, 7, 8.5):
        cmds.currentTime(f)
        m = cmds.xform(b, q=True, ws=True, matrix=True)
        axes = [m[0:3], m[4:7], m[8:11]]
        lens = [math.sqrt(sum(x * x for x in a)) for a in axes]
        p0, p1 = pos(b, f - 0.02), pos(b, f + 0.02)
        tan = [p1[i] - p0[i] for i in range(3)]
        tl = math.sqrt(sum(t * t for t in tan))
        la = axes[lens.index(max(lens))]
        worst = min(worst, abs(sum(la[i] * tan[i] for i in range(3))) / (max(lens) * tl))
    check("伸び: 曲がったパスでも長軸が接線方向", worst > 0.9999, "%.6f" % worst)

    # 旧バージョン (つぶれ率・外部ロケータ) のルートを更新で移行
    cmds.deleteAttr(root, attribute="dfx_stretch")
    cmds.addAttr(root, longName="dfx_squash", attributeType="double", defaultValue=0.4)
    cmds.addAttr(root, longName="dfx_squash_axis", attributeType="enum", enumName=u"進行方向:上下:左右")
    for loc in locs:  # 以前はロケータがルートの外にあった
        cmds.parent(loc, world=True)
    cmds.select(root)
    run_tool(homing.HomingUpdateTool)
    attrs = cmds.listAttr(root, userDefined=True) or []
    check("移行: 旧アトリビュート削除・伸び率追加", "dfx_squash" not in attrs and "dfx_squash_axis" not in attrs
          and cmds.getAttr(root + ".dfx_stretch") == 3.0)
    check("移行: 外部ロケータのままでも更新できる", len(bullets_of(root)) == 24 and
          near(pos(bullets_of(root)[0], 5), (5, 4, 3), 1e-2))


class FakeDialog(object):
    """cmds.fileDialog2 の代わり。呼ばれた引数を記録し、決めたパスを返す。"""

    def __init__(self):
        self.calls = []
        self.answer = None

    def __call__(self, **kw):
        self.calls.append(kw)
        return [self.answer] if self.answer else None


@section
def t10_presets():
    import io, json
    from dummy_effect_maker.core import presets
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"  # ユーザーのマイドキュメントを汚さない
    params = homing.stored_params()
    try:
        new_scene()
        r1, _ = create()
        for name, value in (("interval", 4), ("travel_frames", 12), ("radius", 0.3), ("shape", 1),
                            ("stretch", 2.0), ("path_type", 1), ("subdivisions", 16),
                            ("use_instance", 0)):
            cmds.setAttr("%s.dfx_%s" % (r1, name), value)
        cmds.setAttr(r1 + ".dfx_color", 0, 0, 1, type="float3")
        expected = mu.read_param_values(r1, params)

        # --- 保存
        path = os.path.join(tmp, u"青い追尾弾")  # 拡張子なしで返しても .json になる
        dialog.answer = path
        cmds.select(bullets_of(r1)[0])
        undo_before = cmds.undoInfo(q=True, undoName=True)
        saved = run_tool(_preset.PresetSaveTool)
        call = dialog.calls[-1]
        check("保存: .json が付く", saved == path + ".json" and os.path.isfile(saved), str(saved))
        check("保存: 保存ダイアログ (fileMode 0)・既定フォルダ",
              call["fileMode"] == 0 and call["startingDirectory"].replace("\\", "/").endswith(
                  "maya/DummyEffectMaker/presets/homing") and os.path.isdir(call["startingDirectory"]),
              str(call))
        with io.open(saved, encoding="utf-8") as f:
            data = json.load(f)
        check("保存: 中身", data["type"] == "homing" and data["params"]["interval"] == 4.0
              and data["params"]["shape"] == u"つぶれた球体" and data["params"]["color"] == [0.0, 0.0, 1.0]
              and sorted(data["params"]) == sorted(p.name for p in params), str(data["params"]))
        check("保存: アンドゥ履歴を増やさない", cmds.undoInfo(q=True, undoName=True) == undo_before)
        dialog.answer = None
        check("保存: キャンセル", run_tool(_preset.PresetSaveTool) is None)
        r_other, _ = create()
        for sel, name in (([], u"何も選択なし"), ([r1, r_other], u"2 つ選択")):
            cmds.select(sel) if sel else cmds.select(clear=True)
            n_calls = len(dialog.calls)
            try:
                run_tool(_preset.PresetSaveTool)
                check("保存エラー: " + name, False)
            except ToolError as e:
                check("保存エラー: " + name, len(dialog.calls) == n_calls, u"%s" % e)

        # --- 読み込み
        new_scene()
        r2, locs2 = create(c=(10, 0, 5))
        dialog.answer = saved
        cmds.select(locs2[0])
        run_tool(_preset.PresetLoadTool)
        call = dialog.calls[-1]
        check("読み込み: 開くダイアログ (fileMode 1)", call["fileMode"] == 1)
        check("読み込み: アトリビュートが保存時と同じ", mu.read_param_values(r2, params) == expected,
              str(mu.read_param_values(r2, params)))
        bl = bullets_of(r2)
        shp = cmds.listRelatives(bl[0], shapes=True, fullPath=True)[0]
        check("読み込み: 作り直し (12 発・折れ線・複製)", len(bl) == 12
              and cmds.getAttr(r2 + "_path.degree") == 1
              and len(cmds.listRelatives(shp, allParents=True)) == 1)
        check("読み込み: 見た目 (伸び・色・分割数)", near(cmds.getAttr(bl[0] + ".scale")[0], (0.6, 0.3, 0.3))
              and near(cmds.getAttr(r2 + "_mat.outColor")[0], (0, 0, 1))
              and cmds.polyEvaluate(bl[0], vertex=True) == sphere_vertex_count(16))
        check("読み込み: ロケータ位置はそのまま (着弾 f13 で (10,0,5))",
              near(pos(locs2[2], 1), (10, 0, 5)) and near(pos(bl[0], 13), (10, 0, 5)), str(pos(bl[0], 13)))
        cmds.undo()
        check("読み込みのアンドゥ 1 回で元に戻る", len(bullets_of(r2)) == 24
              and cmds.getAttr(r2 + ".dfx_interval") == 2.0
              and near(cmds.getAttr(r2 + "_mat.outColor")[0], (1.0, 0.75, 0.3)))

        # 複数・キーあり・部分的なプリセット
        r3, _ = create()
        cmds.setKeyframe(r3 + ".dfx_radius", time=1, value=0.05)
        cmds.select([r2, r3])
        LOGS[:] = []
        run_tool(_preset.PresetLoadTool)
        check("複数に読み込み", len(bullets_of(r2)) == 12 and len(bullets_of(r3)) == 12)
        check("キーのあるアトリビュートは変えない", abs(cmds.getAttr(r3 + ".dfx_radius") - 0.05) < 1e-6
              and cmds.listConnections(r3 + ".dfx_radius", source=True, destination=False))
        check("キーで変えなかった項目をログに出す", any(u"弾の半径" in l and u"キー" in l for l in LOGS),
              str([l for l in LOGS if u"※" in l]))
        partial = os.path.join(tmp, "partial.json")
        with io.open(partial, "w", encoding="utf-8") as f:
            f.write(json.dumps({"format": presets.FORMAT_NAME, "version": 1, "type": "homing",
                                "params": {"subdivisions": 6}}))
        dialog.answer = partial
        cmds.select(r2)
        run_tool(_preset.PresetLoadTool)
        check("部分的なプリセット: 指定項目だけ変わる", cmds.getAttr(r2 + ".dfx_subdivisions") == 6
              and cmds.getAttr(r2 + ".dfx_interval") == 4.0 and len(bullets_of(r2)) == 12)

        # エラー・キャンセル
        bad = os.path.join(tmp, "bad.json")
        with io.open(bad, "w", encoding="utf-8") as f:
            f.write(u'{"format": "DummyEffectMaker preset", "type": "laser", "params": {}}')
        for answer, name in ((bad, u"種類の違うファイル"), (os.path.join(tmp, "nope.json"), u"存在しないファイル")):
            dialog.answer = answer
            try:
                run_tool(_preset.PresetLoadTool)
                check("読み込みエラー: " + name, False)
            except ToolError as e:
                check("読み込みエラー: " + name, len(bullets_of(r2)) == 12, u"%s" % e)
        dialog.answer = None
        check("読み込み: キャンセルで何もしない", run_tool(_preset.PresetLoadTool) is None
              and len(bullets_of(r2)) == 12)
        cmds.select(clear=True)
        n_calls = len(dialog.calls)
        try:
            run_tool(_preset.PresetLoadTool)
            check("読み込み: 未選択はエラー", False)
        except ToolError:
            check("読み込み: 未選択はダイアログを出す前にエラー", len(dialog.calls) == n_calls)

        # 旧形式のルート (伸び率なし) にも読み込める
        cmds.deleteAttr(r3, attribute="dfx_stretch")
        dialog.answer = saved
        cmds.select(r3)
        run_tool(_preset.PresetLoadTool)
        check("旧形式のルートにも読み込める", cmds.getAttr(r3 + ".dfx_stretch") == 2.0)
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def sample(nodes, frames):
    """{(node, frame): (translate, rotate, scale, visible)} を記録する。"""
    out = {}
    for f in frames:
        pos(nodes[0], f)  # 時間移動 (履歴に残さない)
        for n in nodes:
            out[(n.split("|")[-1], f)] = (
                cmds.xform(n, q=True, ws=True, t=True),
                cmds.xform(n, q=True, ws=True, ro=True),
                cmds.getAttr(n + ".scale")[0],
                bool(cmds.getAttr(n + ".visibility")))
    return out


def same_angles(r1, r2, tol=1e-2):
    """回転が同じ向きか (ベイクのオイラーフィルタで 360° ずれることがあるので、その差は無視)。"""
    return all(abs((a - b + 180.0) % 360.0 - 180.0) < tol for a, b in zip(r1, r2))


def same_motion(a, b, tol=1e-3):
    bad = []
    for key, (t1, r1, s1, v1) in a.items():
        t2, r2, s2, v2 = b[key]
        if not (near(t1, t2, tol) and same_angles(r1, r2) and near(s1, s2, tol) and v1 == v2):
            bad.append((key, (t1, r1, s1, v1), (t2, r2, s2, v2)))
    return bad


@section
def t11_bake():
    from dummy_effect_maker.tools import bake
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=60, animationStartTime=1, animationEndTime=60)
    root, locs = create(b=(5, 4, 3), shape=homing.SHAPE_SQUASHED, stretch=3.0, radius=0.2)
    bl = bullets_of(root)
    frames = [1, 3, 5, 9, 10, 20, 33, 47, 51, 55, 56, 60]
    before = sample(bl, frames)
    n_mp = len(cmds.ls(type="motionPath"))

    cmds.select(locs[0])  # ロケータを選んでもそのエフェクトが対象
    run_tool(bake.BakeTool)
    check("ベイク: 補助ノードが消える", not cmds.ls(type=["motionPath", "condition", "plusMinusAverage",
                                                      "polySphere"]) and n_mp == 24)
    srcs = set()
    for b in bl:
        for a in bake.BAKE_ATTRS:
            for s in cmds.listConnections("%s.%s" % (b, a), source=True, destination=False) or []:
                srcs.add(cmds.nodeType(s))
    check("ベイク: 弾はアニメーションカーブだけで動く", srcs and all(s.startswith("animCurve") for s in srcs), str(srcs))
    keys = cmds.keyframe(bl[0] + ".translateX", query=True, timeChange=True)
    check("ベイク: 毎フレームのキー (1〜60)", keys == [float(f) for f in range(1, 61)], str(keys[:3] + keys[-2:]))
    check("ベイク: 動き・向き・大きさ・表示が同じ", not same_motion(before, sample(bl, frames)),
          str(same_motion(before, sample(bl, frames))[:2]))
    check("ベイク: ルートに印", mu.is_baked(cmds.ls(root, long=True)[0]))
    check("ベイク: 効かないパラメータはロック", cmds.getAttr(root + ".dfx_radius", lock=True)
          and cmds.getAttr(root + ".dfx_interval", lock=True))
    check("ベイク: 色は変えられる", not cmds.getAttr(root + ".dfx_colorR", lock=True))
    cmds.setAttr(root + ".dfx_color", 1, 0, 0, type="float3")
    check("ベイク後も色は反映", near(cmds.getAttr(root + "_mat.outColor")[0], (1, 0, 0)))
    cmds.setAttr(locs[2] + ".translate", 30, 0, 0, type="double3")
    check("ベイク後: ロケータを動かしても弾は動かない", near(pos(bl[0], 9), before[(bl[0].split("|")[-1], 9)][0]))
    cmds.setAttr(locs[2] + ".translate", 10, 0, 0, type="double3")
    shp = cmds.listRelatives(bl[0], shapes=True, fullPath=True)[0]
    check("ベイク: インスタンスのまま・形状は確定", len(cmds.listRelatives(shp, allParents=True)) == 25
          and not cmds.listConnections(shp + ".inMesh"))

    cmds.select(root)
    for tool, name in ((homing.HomingUpdateTool, u"更新"), (bake.BakeTool, u"再ベイク")):
        try:
            run_tool(tool)
            check(u"ベイク後の%sはエラー" % name, False)
        except ToolError as e:
            check(u"ベイク後の%sはエラー" % name, True, u"%s" % e)

    # --- FBX に書き出して読み込み直しても同じ動き
    cmds.loadPlugin("fbxmaya", quiet=True)
    path = os.path.join(tempfile.gettempdir(), "dfx_bake_verify.fbx").replace("\\", "/")
    cmds.select(root)
    cmds.file(path, force=True, exportSelected=True, type="FBX export")
    check("FBX: 書き出し", os.path.isfile(path) and os.path.getsize(path) > 0)
    new_scene()
    cmds.file(path, i=True, type="FBX")
    os.remove(path)
    imported = [cmds.ls(b.split("|")[-1], long=True) for b in bl]
    check("FBX: 弾が読み込まれる", all(len(x) == 1 for x in imported), str(imported[:2]))
    if all(len(x) == 1 for x in imported):
        after = sample([x[0] for x in imported], frames)
        diff = same_motion(before, after)
        moved = [d for d in diff if not near(d[1][0], d[2][0], 1e-3) or not near(d[1][2], d[2][2], 1e-3)]
        check("FBX: 位置・大きさが同じ", not moved, str(moved[:2]))
        vis_diff = [d for d in diff if d[1][3] != d[2][3]]
        check("FBX: 表示/非表示も同じ", not vis_diff, str(vis_diff[:2]))

    # --- アンドゥで戻せる
    new_scene()
    root, _ = create()
    cmds.select(root)
    run_tool(bake.BakeTool)
    cmds.undo()
    check("ベイクのアンドゥ: モーションパスが戻り、更新できる",
          len(cmds.ls(type="motionPath")) == 24 and not mu.is_baked(cmds.ls(root, long=True)[0]))
    cmds.setAttr(root + ".dfx_interval", 4)
    run_tool(homing.HomingUpdateTool)
    check("ベイクのアンドゥ後: 更新できる", len(bullets_of(root)) == 12)

    # --- すべて / 指定範囲 / 未選択
    r2, _ = create()
    cmds.select(clear=True)
    try:
        run_tool(bake.BakeTool)
        check("ベイク: 未選択はエラー", False)
    except ToolError:
        check("ベイク: 未選択はエラー", True)
    run_tool(bake.BakeTool, target=bake.TARGET_ALL, range_mode=bake.RANGE_CUSTOM,
             start_frame=10, end_frame=20)
    check("ベイク (すべて): 2 つともベイク", all(mu.is_baked(cmds.ls(r, long=True)[0]) for r in (root, r2)))
    keys = cmds.keyframe(bullets_of(r2)[0] + ".translateX", query=True, timeChange=True)
    check("ベイク (指定範囲): 10〜20 のキー", keys == [float(f) for f in range(10, 21)], str(keys))
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("ベイク後の全削除", not cmds.ls("dfx_homing*") and not mu.list_tagged())


@section
def t12_settings_and_ui():
    from dummy_effect_maker.core import settings
    tmp = tempfile.mkdtemp()
    prev_env = os.environ.get(settings.ENV_VAR)
    os.environ[settings.ENV_VAR] = os.path.join(tmp, "settings.json")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    dialog = FakeDialog()
    orig_dialog = cmds.fileDialog2
    cmds.fileDialog2 = dialog
    try:
        from dummy_effect_maker.ui.qt import QtWidgets
        from dummy_effect_maker.ui.main_window import MainWindow
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        win = MainWindow(HOST)
        tabs = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        check("UI: 上部のタブ (射撃 / カメラ / 衝撃波 / お化けブラー / 天候 / 爆発 / 布。環境設定タブは無い)",
              tabs == [u"射撃", u"カメラ", u"衝撃波", u"お化けブラー", u"天候", u"爆発", u"布"], str(tabs))
        menus = [a.text() for a in win.menuBar().actions()]
        scene = [a.text() if not a.isSeparator() else "-" for a in win.menus[u"シーン"].actions()]
        check("UI: 上部のメニュー (シーン / 表示 / 開発)", menus == [u"シーン", u"表示", u"開発"], str(menus))
        check("UI: シーン メニューにベイク・書き出し・全削除・環境設定",
              scene == [u"ベイク", u"ベイク (オプション)...", "-", u"書き出し (Unreal 用)",
                        u"書き出し (Unreal 用) (オプション)...", "-",
                        u"ダミーエフェクトを全削除", "-",
                        u"環境設定 (プリセットの保存先)..."], str(scene))
        cam_boxes = [b.title() for b in win.tool_areas[u"カメラ"].findChildren(QtWidgets.QGroupBox)]
        cam_buttons = [b.text() for b in win.tool_areas[u"カメラ"].findChildren(QtWidgets.QPushButton)]
        check("UI: カメラ タブに集中線、スピードライン、画ぶれ",
              cam_boxes == [u"集中線", u"スピードライン", u"画ぶれ", u"手ブレ"]
              and [b for b in cam_buttons if b != u"\u25a1"] ==
              [u"集中線", u"集中線を更新",
               u"スピードライン", u"スピードラインを更新", u"スピードライン (トンネル)", u"スピードライン (トンネル) を更新",
               u"画ぶれ", u"画ぶれを更新",
               u"画ぶれを外す",
               u"手ブレ", u"手ブレを更新",
               u"手ブレを外す"], str(cam_buttons))
        boxes = [b.title() for b in win.tool_areas[u"射撃"].findChildren(QtWidgets.QGroupBox)]
        buttons = [b.text() for b in win.tool_areas[u"射撃"].findChildren(QtWidgets.QPushButton)]
        check("UI: 射撃 タブは追尾弾・バルカン砲・エネルギー弾の項目 (ベイク・全削除はメニューへ移動)",
              boxes == [u"追尾弾", u"バルカン砲", u"エネルギー弾", u"着弾爆発"] and u"着弾爆発を外す" in buttons
              and u"ベイク" not in buttons
              and u"ダミーエフェクトを全削除" not in buttons, str((boxes, buttons)))

        # メニューからベイク・全削除を実行できる
        new_scene()
        root, _ = create()
        cmds.select(root)
        act = dict((a.text(), a) for a in win.menus[u"シーン"].actions())
        act[u"ベイク"].trigger()
        check("UI: シーン → ベイク でベイクされる", mu.is_baked(cmds.ls(root, long=True)[0]),
              win.log_view.toPlainText()[-200:])
        orig_confirm = cmds.confirmDialog
        cmds.confirmDialog = lambda **kw: u"削除"
        try:
            act[u"ダミーエフェクトを全削除"].trigger()
        finally:
            cmds.confirmDialog = orig_confirm
        check("UI: シーン → 全削除 で消える", not cmds.ls("dfx_homing*"))
        check("UI: 環境設定は最初は閉じていて、メニューで開く", not win.settings_dialog.isVisible())
        act[u"環境設定 (プリセットの保存先)..."].trigger()
        check("UI: シーン → 環境設定 で開く", win.settings_dialog.isVisible())

        page = win.settings_page
        check("設定: 初期値は空 (既定)", page.preset_dir_edit.text() == "" and settings.get("preset_dir") == "")
        target = os.path.join(tmp, u"チーム", "presets")
        page.preset_dir_edit.setText(target)
        page.preset_dir_edit.editingFinished.emit()
        check("設定: 入力すると保存される", settings.get("preset_dir") == target)
        check("設定: 状態表示", u"ありません" in page.status_label.text(), page.status_label.text())
        check("設定: ログに出る", u"プリセットの保存先" in win.log_view.toPlainText())
        check("設定: ツールの保存先に反映 (フォルダも作る)",
              mu.preset_dir("homing") == target and os.path.isdir(target))

        new_scene()
        root, _ = create()
        cmds.select(root)
        dialog.answer = None
        run_tool(_preset.PresetSaveTool)
        check("設定: 保存ダイアログがそのフォルダで開く", dialog.calls[-1]["startingDirectory"] == target)

        win2 = MainWindow(HOST)
        check("設定: ウインドウを開き直しても残る", win2.settings_page.preset_dir_edit.text() == target)
        settings.set("preset_dir", os.path.join(tmp, "other"))
        win2.open_settings()
        check("設定: 開くと最新を表示", win2.settings_page.preset_dir_edit.text().endswith("other"))
        page._on_reset()
        check("設定: 既定に戻す", settings.get("preset_dir") == "" and page.preset_dir_edit.text() == ""
              and u"既定" in page.status_label.text())
        for w in (win, win2):
            w.close()
    finally:
        cmds.fileDialog2 = orig_dialog
        os.environ[settings.ENV_VAR] = prev_env
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


@section
def t13_bake_hide_by_scale():
    from dummy_effect_maker.tools import bake
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=60, animationStartTime=1, animationEndTime=60)
    root, _ = create(shape=homing.SHAPE_SQUASHED, stretch=3.0, radius=0.2, use_instance=True)
    bl = bullets_of(root)
    frames = list(range(1, 61))
    before = sample(bl, frames)
    cmds.select(root)
    LOGS[:] = []
    run_tool(bake.BakeTool, hide_by_scale=True)
    after = sample(bl, frames)
    wrong = []
    for key, (t0, r0, s0, v0) in before.items():
        t1, r1, s1, v1 = after[key]
        want = s0 if v0 else (0.0, 0.0, 0.0)
        if not near(s1, want) or v1 != v0 or not near(t1, t0):
            wrong.append((key, v0, s0, s1))
    check("スケール0: 非表示のフレームだけ 0、表示中は元の大きさ (全弾・全フレーム)", not wrong, str(wrong[:3]))
    check("スケール0: 表示/非表示のキーも残る", all(
        cmds.listConnections(b + ".visibility", source=True, destination=False) for b in bl))
    b = bl[1]  # f3 発射 → f11 着弾、f12 で消える
    cmds.undoInfo(stateWithoutFlush=False)
    s_mid_show = cmds.getAttr(b + ".scaleX", time=2.5)
    s_mid_hide = cmds.getAttr(b + ".scaleX", time=11.5)
    cmds.undoInfo(stateWithoutFlush=True)
    check("スケール0: 出現・消滅はぱっと切り替わる (フレームの間で中途半端にならない)",
          abs(s_mid_show) < 1e-6 and abs(s_mid_hide - 0.6) < 1e-4, "%s %s" % (s_mid_show, s_mid_hide))
    check("スケール0: ログ", any(u"スケールを 0" in l for l in LOGS), str(LOGS))

    cmds.loadPlugin("fbxmaya", quiet=True)
    path = os.path.join(tempfile.gettempdir(), "dfx_bake_scale0.fbx").replace("\\", "/")
    cmds.select(root)
    cmds.file(path, force=True, exportSelected=True, type="FBX export")
    new_scene()
    cmds.file(path, i=True, type="FBX")
    os.remove(path)
    imported = [cmds.ls(x.split("|")[-1], long=True)[0] for x in bl]
    fbx = sample(imported, frames)
    wrong = [k for k in after if not near(after[k][2], fbx[k][2]) or not near(after[k][0], fbx[k][0])]
    check("スケール0: FBX でも非表示フレームのスケール 0", not wrong, str(wrong[:3]))

    # 既定 (OFF) ではスケールは変えない
    new_scene()
    root, _ = create()
    cmds.select(root)
    run_tool(bake.BakeTool)
    b = bullets_of(root)[0]
    check("スケール0: 既定は OFF", near(cmds.getAttr(b + ".scale", time=30)[0], (0.1,) * 3))


@section
def t14_deceleration():
    from dummy_effect_maker.tools import bake
    for mode in (homing.SPEED_LINEAR, homing.SPEED_QUADRATIC):
        new_scene()
        # B を A-C の直線上に置き、X 座標 = 進んだ距離 にする
        root, _ = create(b=(5, 0, 0), speed_mode=mode, min_speed=30.0, travel_frames=8)
        bl = bullets_of(root)
        b0 = bl[0]  # f1 発射
        worst = 0.0
        for i in range(0, 33):
            t = 1 + i * 0.25
            want = 10 * homing.travel_fraction(mode, 0.3, (t - 1) / 8.0)
            worst = max(worst, abs(pos(b0, t)[0] - want))
        check(u"減速 %s: 位置が式どおり (誤差 < 0.001)" % mode, worst < 1e-3, "max err %.5f" % worst)
        between = [abs(pos(b0, 1 + (i + 0.5) / 4.0)[0] - 10 * homing.travel_fraction(mode, 0.3, (i + 0.5) / 32.0))
                   for i in range(32)]
        check(u"減速 %s: キーの間も誤差が小さい (< 0.01)" % mode, max(between) < 1e-2, "%.5f" % max(between))
        v_start = pos(b0, 1.05)[0] - pos(b0, 1.0)[0]
        v_end = pos(b0, 9.0)[0] - pos(b0, 8.95)[0]
        check(u"減速 %s: 着弾時の速さ ≒ 発射時の 30%%" % mode, abs(v_end / v_start - 0.3) < 0.02,
              "%.4f" % (v_end / v_start))
        check(u"減速 %s: 着弾フレームで C" % mode, near(pos(b0, 9), (10, 0, 0)))
        check(u"減速 %s: 表示タイミングは変わらない" % mode,
              [seen(b0, f) for f in (0, 1, 9, 10)] == [0, 1, 1, 0])
        b4 = bl[4]  # f9 発射: 1 発目と同じ動きを 8 フレーム遅れで
        same = all(abs(pos(b4, 9 + d)[0] - pos(b0, 1 + d)[0]) < 1e-6 for d in (0.5, 2, 4.25, 7))
        check(u"減速 %s: 全弾が同じ減速 (時間だけずれる)" % mode, same)
        relays = [cmds.listConnections(m + ".uValue", source=True, destination=False)[0]
                  for m in cmds.ls(root + "_motion_*", type="motionPath")]
        curves = set(cmds.listConnections(relays, source=True, destination=False, type="animCurve") or [])
        check(u"減速 %s: 弾ごとに別のカーブ" % mode, len(curves) == 24, str(len(curves)))
        check(u"減速 %s: 軌道に位置マーカー (キーのフレーム番号) を作らない" % mode,
              not cmds.ls(type="positionMarker"), str(len(cmds.ls(type="positionMarker"))))
        cmds.select(root)
        run_tool(homing.HomingUpdateTool)
        cmds.undo()
        cmds.redo()
        check(u"減速 %s: 更新・アンドゥ・リドゥしても位置マーカーは無い" % mode,
              not cmds.ls(type="positionMarker") and len(bullets_of(root)) == 24
              and near(pos(bullets_of(root)[0], 9), (10, 0, 0)))

    # 2乗の方が前半で大きく減速 → 中間フレームで進んだ距離が短い
    new_scene()
    rl, _ = create(b=(5, 0, 0), speed_mode=homing.SPEED_LINEAR, min_speed=30.0)
    rq, _ = create(b=(5, 0, 0), speed_mode=homing.SPEED_QUADRATIC, min_speed=30.0)
    rc, _ = create(b=(5, 0, 0))
    xl, xq, xc = [pos(bullets_of(r)[0], 3)[0] for r in (rl, rq, rc)]
    check("比較: f3 で 等速 < リニア < 2乗 (減速の方が最初は速い)", xc < xl < xq, "%.3f %.3f %.3f" % (xc, xl, xq))
    r100, _ = create(b=(5, 0, 0), speed_mode=homing.SPEED_LINEAR, min_speed=100.0)
    check("最低速度 100% は等速と同じ", abs(pos(bullets_of(r100)[0], 3)[0] - xc) < 1e-6)

    # チャンネルボックスから変えて更新
    nn = cmds.attributeQuery("dfx_speed_mode", node=rc, niceName=True)
    check("CB: 減速は (更新) 項目", nn.endswith(u"(更新)") and
          "dfx_min_speed" in (cmds.listAttr(rc, channelBox=True) or []), nn)
    cmds.setAttr(rc + ".dfx_speed_mode", 2)
    cmds.setAttr(rc + ".dfx_min_speed", 10)
    cmds.select(rc)
    run_tool(homing.HomingUpdateTool)
    want = 10 * homing.travel_fraction(homing.SPEED_QUADRATIC, 0.1, 2 / 8.0)
    check("更新: 2乗・最低 10% に変わる", abs(pos(bullets_of(rc)[0], 3)[0] - want) < 1e-3)
    n1 = len(cmds.ls())
    run_tool(homing.HomingUpdateTool)
    check("更新: 減速ありでもノード数一定", n1 == len(cmds.ls()))

    # 旧形式 (減速アトリビュートなし) のルートは等速として更新できる
    cmds.deleteAttr(rl, attribute="dfx_speed_mode")
    cmds.deleteAttr(rl, attribute="dfx_min_speed")
    cmds.select(rl)
    run_tool(homing.HomingUpdateTool)
    check("旧形式: 減速アトリビュートが追加され等速", cmds.getAttr(rl + ".dfx_speed_mode", asString=True)
          == homing.SPEED_CONSTANT and cmds.getAttr(rl + ".dfx_min_speed") == 30.0
          and abs(pos(bullets_of(rl)[0], 3)[0] - xc) < 1e-6)

    # ベイクしても同じ動き・カーブが残らない
    frames = list(range(1, 61))
    cmds.playbackOptions(minTime=1, maxTime=60)
    before = sample(bullets_of(rq), frames)
    cmds.select(rq)
    run_tool(bake.BakeTool)
    check("ベイク (減速): 動きが同じ", not same_motion(before, sample(bullets_of(rq), frames)))
    dangling = [c for c in cmds.ls(type="animCurve") if not cmds.listConnections(c, destination=True, source=False)]
    check("ベイク (減速): つながっていないカーブが残らない", not dangling, str(dangling[:3]))
    relays = cmds.ls(rq.split("|")[-1] + "_motion_*_uRelay")
    check("ベイク (減速): 進み具合の中継ノードも消える", not relays and cmds.ls("*_uRelay"), str(relays[:3]))


def ndc(cam, point):
    """ワールド座標 → カメラの画面座標 (横幅の半分 = 1、上下は ±1/アスペクト)。"""
    from maya.api import OpenMaya as om
    inv = om.MMatrix(cmds.getAttr(cam + ".worldInverseMatrix[0]"))
    p = om.MPoint(*point) * inv
    shape = cmds.listRelatives(cam, shapes=True, fullPath=True)[0]
    k = cmds.getAttr(shape + ".focalLength") / (cmds.getAttr(shape + ".horizontalFilmAperture") * 12.7)
    return (p.x / -p.z * k, p.y / -p.z * k, -p.z)


def mesh_points(cam, node, count):
    shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0].split("|")[-1]
    return [ndc(cam, cmds.pointPosition("%s|%s.vtx[%d]" % (node, shape, i), world=True))
            for i in range(count)]


def line_points(cam, line):
    """線の本体の (画面の端の中点, 画面の端の 2 点, 先端の中点) の画面座標。

    本体の頂点: 0, 3 = 画面の端 (太さ方向の両端)、1, 2 = 先端 (とがる時は同じ点)。
    """
    vs = mesh_points(cam, line, 4)
    base = ((vs[0][0] + vs[3][0]) / 2, (vs[0][1] + vs[3][1]) / 2)
    tip = ((vs[1][0] + vs[2][0]) / 2, (vs[1][1] + vs[2][1]) / 2, vs[1][2])
    return base, (vs[0], vs[3]), tip


def part_of(line, kind):
    """線の本体 line に対応する延長 (tail) / 丸い先端 (tip)。"""
    short = line.split("|")[-1]
    return cmds.ls(short.replace("_line_", "_%s_" % kind), long=True)[0]


def lines_of(root):
    short = root.split("|")[-1]
    return cmds.listRelatives(cmds.ls("%s_lines" % short, long=True)[0], children=True,
                              fullPath=True) or []


@section
def t15_focus_lines():
    from dummy_effect_maker.tools import bake, focus_lines as fl
    new_scene()
    aspect = cmds.getAttr("defaultResolution.deviceAspectRatio")
    cam = cmds.ls("persp", long=True)[0]
    cmds.xform(cam, ws=True, t=(20, 10, 30), ro=(-15, 30, 0))
    cmds.select(cam)
    root = run_tool(fl.FocusLinesTool, motion=fl.MOTION_STATIC, length=0.5, focus_radius=0.0)
    full = cmds.ls(root, long=True)[0]
    follow = cmds.ls(root + "_follow", long=True)[0]
    check("集中線: ルートはワールド、_follow がカメラにコンストレイント", full.count("|") == 1 and
          cmds.listConnections(follow + ".translateX", source=True, type="parentConstraint")
          and cmds.listConnections(root + ".dfx_camera", source=True) == ["persp"], full)
    lines = lines_of(root)
    check("集中線: 80 本", len(lines) == 80, str(len(lines)))
    layout = fl.line_layout(80, aspect, 1, 0.5, 0.5)

    def outside_frame(p):
        return abs(p[0]) >= 1 - 1e-6 or abs(p[1]) >= 1 / aspect - 1e-6

    def check_layout(name, length, thickness, layout):
        """画面の端で太さ = 線の太さ、先端 = 端 × (1 - 長さ)。延長の外側の角は画面の外。"""
        bad = []
        for line, (angle, edge, lf, tf) in zip(lines_of(root), layout):
            at_edge, (e1, e2), tip = line_points(cam, line)
            r_edge = math.hypot(*at_edge)
            r_tip = math.hypot(tip[0], tip[1])
            width_at_edge = math.hypot(e1[0] - e2[0], e1[1] - e2[1]) / 2
            far = mesh_points(cam, part_of(line, "tail"), 4)[2:]
            if (abs(r_edge - edge) > 1e-4 or abs(r_tip - edge * (1 - length * lf)) > 1e-4
                    or abs(width_at_edge - thickness * tf) > 1e-4
                    or not (outside_frame(far[0]) and outside_frame(far[1]))
                    or (r_tip > 1e-6 and
                        abs(math.atan2(tip[1], tip[0]) % (2 * math.pi) - angle) > 1e-4)):
                bad.append((line.split("|")[-1], r_edge, edge, r_tip, width_at_edge))
        check(name, not bad, str(bad[:2]))

    check_layout("画面上: 端からの長さ・太さ・向きが設定どおり、根元の角は画面の外", 0.5, 0.03, layout)
    z = ndc(cam, cmds.pointPosition(lines[0] + "|" + cmds.listRelatives(lines[0], shapes=True)[0]
                                    + ".vtx[0]", world=True))[2]
    check("カメラから距離 1 の位置", abs(z - 1.0) < 1e-6, str(z))

    before = [line_points(cam, l) for l in lines[:10]]
    cmds.xform(cam, ws=True, t=(-50, 3, 7), ro=(10, -120, 5))
    shape = cmds.listRelatives(cam, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".focalLength", 85)
    cmds.setAttr(root + ".dfx_distance", 5)
    after = [line_points(cam, l) for l in lines[:10]]
    moved = max(abs(a[0][i] - b[0][i]) + abs(a[2][i] - b[2][i]) for a, b in zip(before, after) for i in (0, 1))
    check("張り付き: カメラ移動・回転・焦点距離 85・距離 5 でも画面上で動かない", moved < 1e-5, "%.2e" % moved)

    # すぐ反映
    cmds.setAttr(root + ".dfx_length", 0.8)
    cmds.setAttr(root + ".dfx_thickness", 0.06)
    check_layout("即反映: 長さ 0.8・太さ 0.06", 0.8, 0.06, layout)
    cmds.setAttr(root + ".dfx_length", 1.0)
    check_layout("長さ 1: 先端がちょうど中心 (反対側へ突き抜けない)", 1.0, 0.06, layout)
    cmds.setAttr(root + ".dfx_length", 0.8)
    cmds.setAttr(root + ".dfx_color", 1, 0, 0, type="float3")
    cmds.setAttr(root + ".dfx_opacity", 0.25)
    check("即反映: 色・不透明度 (色はそのまま)",
          near(cmds.getAttr(root + "_mat.outColor")[0], (1, 0, 0))
          and near(cmds.getAttr(root + "_mat.outTransparency")[0], (0.75,) * 3))
    keyable = cmds.listAttr(root, keyable=True) or []
    check("CB: 即反映 5 項目はキー可、本数などは (更新)",
          all("dfx_" + n in keyable or "dfx_colorR" in keyable for n in fl.LIVE_PARAMS)
          and cmds.attributeQuery("dfx_count", node=root, niceName=True).endswith(u"(更新)"))

    # 更新
    cmds.setAttr(root + ".dfx_count", 30)
    cmds.setAttr(root + ".dfx_seed", 9)
    cmds.setAttr(root + ".dfx_length_random", 0.0)
    cmds.select(lines[3])
    run_tool(fl.FocusLinesUpdateTool)
    check("更新: 30 本", len(lines_of(root)) == 30)
    check_layout("更新: 新しい並びで画面の端から", 0.8, 0.06, fl.line_layout(30, aspect, 9, 0.0, 0.5))
    check("更新: 色・不透明度は保持", near(cmds.getAttr(root + "_mat.outColor")[0], (1, 0, 0))
          and near(cmds.getAttr(root + "_mat.outTransparency")[0], (0.75,) * 3))
    n1 = len(cmds.ls())
    run_tool(fl.FocusLinesUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # ちらつき
    cmds.playbackOptions(minTime=1, maxTime=24)
    cmds.setAttr(root + ".dfx_flicker_interval", 2); cmds.setAttr(root + ".dfx_motion", 1)  # ちらつき
    cmds.setAttr(root + ".dfx_length_random", 0.6)
    run_tool(fl.FocusLinesUpdateTool)
    l0 = lines_of(root)[0]
    keys = cmds.keyframe(l0 + ".dfx_len", query=True, timeChange=True)
    check("ちらつき: 2 フレームごとのキー (1〜23)", keys == [float(f) for f in range(1, 25, 2)], str(keys))
    sx = [cmds.getAttr(l0 + ".scaleX", time=t) for t in (1, 2, 3, 4)]
    check("ちらつき: 2 フレームごとに長さが変わる (間はそのまま)",
          abs(sx[0] - sx[1]) < 1e-9 and abs(sx[2] - sx[3]) < 1e-9 and abs(sx[0] - sx[2]) > 1e-6, str(sx))

    # ベイク
    cmds.select(root)
    pts = {}
    for t in (1, 3, 10):
        pos(lines_of(root)[0], t)
        pts[t] = [line_points(cam, l)[2] for l in lines_of(root)]
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=24)
    same = True
    for t in (1, 3, 10):
        pos(lines_of(root)[0], t)
        now = [line_points(cam, l)[2] for l in lines_of(root)]
        same = same and all(abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-4 for a, b in zip(pts[t], now))
    check("ベイク: 画面上の線が同じ (ちらつき含む)", same)
    check("ベイク: コンストレイントが消える", not cmds.ls(type="parentConstraint"))
    check("ベイク: 計算ノードが消え、透明度は変えられる",
          not cmds.ls(type="expression") and cmds.objExists(root + "_transparency"))
    dangling = [c for c in cmds.ls(type="animCurve") if not cmds.listConnections(c, destination=True, source=False)]
    check("ベイク: つながっていないカーブが残らない", not dangling, str(dangling[:3]))
    try:
        cmds.select(root)
        run_tool(fl.FocusLinesUpdateTool)
        check("ベイク後の更新はエラー", False)
    except ToolError:
        check("ベイク後の更新はエラー", True)

    # カメラの選び方・アンドゥ・全削除
    new_scene()
    cmds.select(cmds.listRelatives("persp", shapes=True)[0])
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    root = run_tool(fl.FocusLinesTool)
    check("カメラのシェイプを選んでも作れる", cmds.listConnections(root + ".dfx_camera", source=True) == ["persp"])
    cmds.undo()
    check("アンドゥ 1 回で全て消える", not (set(cmds.ls()) - before_nodes))
    cmds.select(clear=True)
    try:
        run_tool(fl.FocusLinesTool)
        check("カメラ未選択 (バッチ) はエラー", False)
    except ToolError as e:
        check("カメラ未選択 (バッチ) はエラー", True, u"%s" % e)
    cmds.select("persp")
    run_tool(fl.FocusLinesTool, count=12)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 集中線が消え、カメラは残る", not cmds.ls("dfx_focusLines*") and cmds.objExists("persp")
          and not mu.list_tagged())


@section
def t16_focus_lines_grow():
    from dummy_effect_maker.tools import bake, focus_lines as fl
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=60)
    aspect = cmds.getAttr("defaultResolution.deviceAspectRatio")
    cam = cmds.ls("persp", long=True)[0]
    cmds.xform(cam, ws=True, t=(20, 10, 30), ro=(-15, 30, 0))
    cmds.select(cam)
    root = run_tool(fl.FocusLinesTool, count=20, length=0.5, focus_radius=0.0)
    check("伸びて移動: 既定の動き", cmds.getAttr(root + ".dfx_motion", asString=True) == fl.MOTION_GROW)
    lines = lines_of(root)
    schedules = fl.grow_schedule(1, 20, 1, 60, 6, aspect, 0.5)

    def measure(line):
        at_edge, _, tip = line_points(cam, line)
        return (math.hypot(*at_edge), math.hypot(tip[0], tip[1]),
                math.atan2(at_edge[1], at_edge[0]) % (2 * math.pi))

    wrong, moves_per_frame, measured = [], {}, {}
    for f in range(1, 61):
        pos(lines[0], f)
        for i, (line, cycles) in enumerate(zip(lines, schedules)):
            t, angle, edge, lf = [c for c in cycles if c[0] <= f < c[0] + 6][0]
            k = f - t
            r_edge, r_tip, ang = measure(line)
            measured[(i, f)] = (r_edge - r_tip, ang)
            want_tip = edge * (1 - 0.5 * lf * (k + 1) / 6.0)
            if (abs(r_edge - edge) > 1e-4 or abs(r_tip - want_tip) > 1e-4
                    or fl.angle_gap(ang, angle) > 1e-4):
                wrong.append((i, f, k, r_tip, want_tip))
            if k == 0 and f > 1:
                moves_per_frame[f] = moves_per_frame.get(f, 0) + 1
    check("伸びて移動: 全線・全フレームで位置と長さが予定どおり", not wrong, str(wrong[:3]))
    grows = all(measured[(i, f + 1)][0] > measured[(i, f)][0]
                for i, cycles in enumerate(schedules) for (t, _, _, _) in cycles
                for f in range(max(1, t), min(60, t + 5)) if f + 1 < t + 6)
    check("伸びて移動: 伸びている間は毎フレーム長くなる", grows)
    jumps = [fl.angle_gap(measured[(i, t - 1)][1], measured[(i, t)][1])
             for i, cycles in enumerate(schedules) for (t, _, _, _) in cycles if 1 < t <= 60]
    check("伸びて移動: 伸びきった次のフレームで 30° 以上離れた場所へ移る",
          jumps and min(jumps) >= math.radians(30) - 1e-4, "最小 %.1f°" % math.degrees(min(jumps)))
    check("伸びて移動: 毎フレーム 3〜4 本ずつ移動 (一斉には移動しない)",
          set(moves_per_frame.values()) <= {3, 4} and len(moves_per_frame) == 59, str(moves_per_frame))
    gaps = []
    for f in range(1, 61):
        a = sorted(measured[(i, f)][1] for i in range(20))
        gaps.append(max([y - x for x, y in zip(a, a[1:])] + [a[0] + 2 * math.pi - a[-1]]) / (2 * math.pi / 20))
    check("伸びて移動: 画面上でも均等 (最大のすき間 平均 < 2.6 / 最悪 < 5、理想の間隔 = 1)",
          sum(gaps) / 60 < 2.6 and max(gaps) < 5, "平均 %.2f / 最悪 %.2f" % (sum(gaps) / 60, max(gaps)))

    # 長さはすぐ反映 (伸びきった時の長さが変わる)
    cmds.setAttr(root + ".dfx_length", 1.0)
    i, (t, angle, edge, lf) = 0, [c for c in schedules[0] if c[0] >= 1][0]
    pos(lines[0], t + 5)
    r_edge, r_tip, _ = measure(lines[0])
    check("伸びて移動: 線の長さ 1 で伸びきると 端×(1-倍率)", abs(r_tip - edge * (1 - lf)) < 1e-4)
    cmds.setAttr(root + ".dfx_length", 0.5)

    # ベイクしても同じ
    snap = {}
    for f in (1, 7, 13, 30):
        pos(lines[0], f)
        snap[f] = [line_points(cam, l)[2] for l in lines]
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=60)
    ok = True
    for f in (1, 7, 13, 30):
        pos(lines[0], f)
        now = [line_points(cam, l)[2] for l in lines]
        ok = ok and all(abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-4 for a, b in zip(snap[f], now))
    check("伸びて移動: ベイクしても同じ", ok)
    cmds.undo()

    # 伸びるフレーム数を変えて更新
    cmds.setAttr(root + ".dfx_grow_frames", 10)
    cmds.select(root)
    run_tool(fl.FocusLinesUpdateTool)
    l0 = lines_of(root)[0]
    keys = cmds.keyframe(l0 + ".dfx_angle", query=True, timeChange=True)
    check("更新: 伸びるフレーム数 10 で 10 フレームごとに移動",
          all(b - a == 10 for a, b in zip(keys, keys[1:])) and len(keys) >= 6, str(keys))
    cmds.setAttr(root + ".dfx_motion", 0)  # 静止
    run_tool(fl.FocusLinesUpdateTool)
    check("更新: 静止にするとキーなし", not cmds.keyframe(lines_of(root)[0] + ".dfx_angle", query=True))


@section
def t17_energy_ball():
    from dummy_effect_maker.tools import bake, energy_ball as eb
    new_scene()
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    cmds.select(clear=True)
    root = run_tool(eb.EnergyBallTool)
    v = eb.EnergyBallTool.default_values()

    def pairs():
        return eb.existing_pairs(cmds, cmds.ls(root, long=True)[0])

    def ball_of(start):
        return cmds.listRelatives(start, children=True, type="transform", fullPath=True)[0]

    ps = pairs()
    check("エネルギー弾: 3 組", len(ps) == 3, str(len(ps)))
    check("構成: 球は発射位置ロケータの子、目標はルートの子",
          all(ball_of(s).split("|")[-1] == "%s_ball_%02d" % (root, i + 1) and
              cmds.listRelatives(t, parent=True)[0] == root for i, (s, t) in enumerate(ps)))
    check("ロケータ: locator シェイプ", all(cmds.listRelatives(x, shapes=True, type="locator")
                                         for p in ps for x in p))
    keyable = cmds.listAttr(ps[0][0], keyable=True) or []
    check("発射位置: タイミングのアトリビュート (キー可)",
          all(a in keyable for a, _ in eb.BALL_ATTRS) and
          cmds.attributeQuery("dfx_fly_start", node=ps[0][0], niceName=True) == u"発射フレーム")
    check("発射位置: 初期のタイミング", [cmds.getAttr(ps[1][0] + "." + a) for a, _ in eb.BALL_ATTRS]
          == list(eb.default_timing(v, 1)))

    def expected(i, f, start_pos, target_pos, timing, ease=eb.EASE_ACCEL, stay=False, radius=0.5):
        t0, t1, t2 = timing
        p = eb.flight_progress(f, t1, t2, ease)
        pos_ = [start_pos[k] + (target_pos[k] - start_pos[k]) * p for k in range(3)]
        return pos_, radius * eb.appear_scale(f, t0, v["appear_frames"]), (f >= t0) and (stay or f <= t2)

    def check_path(name, **kw):
        bad = []
        for i, (s, t) in enumerate(pairs()):
            b = ball_of(s)
            timing = [cmds.getAttr(s + "." + a) for a, _ in eb.BALL_ATTRS]
            sp_, tp = cmds.xform(s, q=True, ws=True, t=True), cmds.xform(t, q=True, ws=True, t=True)
            for f in range(0, 46):
                want_p, want_s, want_v = expected(i, f, sp_, tp, timing, **kw)
                got_p = pos(b, f)
                got_s = cmds.getAttr(b + ".scaleX")
                got_v = bool(cmds.getAttr(b + ".visibility"))
                if not near(got_p, want_p, 1e-4) or abs(got_s - want_s) > 1e-4 or got_v != want_v:
                    bad.append((i, f, got_p, want_p, got_s, want_s, got_v))
        check(name, not bad, str(bad[:2]))

    cmds.setAttr(root + ".dfx_jitter", 0)
    check_path("動き (ジッター 0): 出現 → 待機 → 加速して目標へ → 着弾後に消える (全球・0〜45 フレーム)")
    b0 = ball_of(ps[0][0])
    check("出現: 最初はスケール 0", cmds.getAttr(b0 + ".scaleX", time=1) == 0.0)

    # ジッター
    cmds.setAttr(root + ".dfx_jitter", 0.15)
    offs = []
    for f in range(8, 17):  # 待機中 (発射位置 (-2,0,0))
        p = pos(b0, f)
        offs.append((p[0] + 2.0, p[1], p[2]))
    amp = max(abs(c) for o in offs for c in o)
    steps = [dist(offs[k], offs[k + 1]) for k in range(len(offs) - 1)]
    check("ジッター: ぶれ幅は 半径 × 強さ (0.075) 以内で、毎フレームぶれる",
          0.02 < amp <= 0.075 + 1e-6 and min(steps) > 1e-3, "最大 %.3f / 最小移動 %.4f" % (amp, min(steps)))
    b1 = ball_of(ps[1][0])
    o1 = [tuple(pos(b1, f)[k] - [0, 0, 0][k] for k in range(3)) for f in range(12, 15)]
    o0 = [tuple(pos(b0, f)[k] - [-2, 0, 0][k] for k in range(3)) for f in range(12, 15)]
    check("ジッター: 球ごとにぶれ方が違う", any(dist(a, b) > 1e-3 for a, b in zip(o0, o1)))
    cmds.setAttr(root + ".dfx_jitter_speed", 0)
    still = [pos(b0, f) for f in (9, 10, 11)]
    check("ジッターの速さ 0: ぶれが止まる", dist(still[0], still[1]) < 1e-6 and dist(still[1], still[2]) < 1e-6)
    cmds.setAttr(root + ".dfx_jitter_speed", 2.7)

    # ロケータ・タイミング・パラメータを変える (すぐ反映)
    cmds.setAttr(root + ".dfx_jitter", 0)
    s0, t0_ = ps[0]
    cmds.xform(s0, ws=True, t=(3, 4, -5), ro=(0, 45, 30))
    cmds.xform(t0_, ws=True, t=(-6, 2, 12))
    cmds.setAttr(ps[1][0] + ".dfx_fly_start", 25)
    cmds.setAttr(ps[1][0] + ".dfx_fly_end", 40)
    check_path("即反映: ロケータ移動・回転、球 2 のタイミング変更")
    check("即反映: 球 1 は動かした目標へ着弾", near(pos(b0, 29), (-6, 2, 12), 1e-4), str(pos(b0, 29)))
    cmds.setAttr(root + ".dfx_fly_ease", 1)
    cmds.setAttr(root + ".dfx_after_hit", 1)
    check_path("即反映: 等速・着弾後も残る", ease=eb.EASE_LINEAR, stay=True)
    cmds.setAttr(root + ".dfx_radius", 1.2)
    cmds.setAttr(root + ".dfx_color", 1, 0.3, 0, type="float3")
    check("即反映: 半径・色", abs(cmds.getAttr(b0 + ".scaleX", time=30) - 1.2) < 1e-6
          and near(cmds.getAttr(root + "_mat.outColor")[0], (1, 0.3, 0)))
    cmds.setAttr(root + ".dfx_fly_ease", 0); cmds.setAttr(root + ".dfx_after_hit", 0)
    cmds.setAttr(root + ".dfx_radius", 0.5)

    # 更新: 個数を増やす / 減らす
    cmds.setAttr(root + ".dfx_count", 5)
    cmds.select(b0)
    run_tool(eb.EnergyBallUpdateTool)
    ps = pairs()
    check("更新: 5 組に増える", len(ps) == 5 and all(cmds.objExists(ball_of(s)) for s, _ in ps))
    check("更新: 既存の位置とタイミングは保持",
          near(cmds.xform(ps[0][0], q=True, ws=True, t=True), (3, 4, -5)) and
          cmds.getAttr(ps[1][0] + ".dfx_fly_start") == 25)
    check("更新: 追加分は初期値のタイミング", [cmds.getAttr(ps[4][0] + "." + a) for a, _ in eb.BALL_ATTRS]
          == list(eb.default_timing(v, 4)))
    check_path("更新後: 全部の球が正しく動く")
    n1 = len(cmds.ls())
    run_tool(eb.EnergyBallUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))
    cmds.setAttr(root + ".dfx_count", 2)
    run_tool(eb.EnergyBallUpdateTool)
    check("更新: 2 組に減る (後ろから削除)", len(pairs()) == 2 and not cmds.objExists(root + "_start_03")
          and not cmds.objExists(root + "_target_05"))
    run_tool(eb.EnergyBallUpdateTool, retime_all=True)
    check("更新: タイミングを全部の球に適用", cmds.getAttr(pairs()[1][0] + ".dfx_fly_start") == 21.0)

    # ベイク
    cmds.setAttr(root + ".dfx_jitter", 0.15)
    bl = [ball_of(s) for s, _ in pairs()]
    frames = [0, 1, 3, 7, 12, 18, 23, 29, 30, 34, 40]
    snap = sample(bl, frames)
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=0, end_frame=45)
    check("ベイク: 動き (ジッター込み)・大きさ・表示が同じ", not same_motion(snap, sample(bl, frames)),
          str(same_motion(snap, sample(bl, frames))[:2]))
    check("ベイク: エクスプレッション・行列ノードが消える",
          not cmds.ls(type=["expression", "multMatrix"]))
    dangling = [c for c in cmds.ls(type="animCurve") if not cmds.listConnections(c, destination=True, source=False)]
    check("ベイク: つながっていないカーブが残らない", not dangling, str(dangling[:3]))
    cmds.undo()

    # アンドゥ・全削除
    new_scene()
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    root = run_tool(eb.EnergyBallTool, count=4)
    cmds.undo()
    check("アンドゥ 1 回で全て消える", not (set(cmds.ls()) - before_nodes))
    run_tool(eb.EnergyBallTool, count=2)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: ロケータごと消える", not cmds.ls("dfx_energy*") and not mu.list_tagged())


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else list(v)


@section
def t18_vulcan():
    from dummy_effect_maker.tools import vulcan as vu
    from dummy_effect_maker.tools import bake
    new_scene()
    cmds.flushUndo()
    labels = [t.label for t in registry.discover("dummy_effect_maker.tools")]
    check("バルカン砲: 2 つのボタン (保存・読み込みはアトリビュートエディタ)", all(l in labels for l in (
        u"バルカン砲", u"バルカン砲を更新")) and u"バルカン砲のパラメータを保存" not in labels)
    root = run_tool(vu.VulcanTool)
    muz, tgt = root + "|" + root + "_muzzle", root + "|" + root + "_target"
    bl = bullets_of(root)
    check("作成: ロケータ 2 つがルートの子", cmds.objExists(muz) and cmds.objExists(tgt))
    check("作成: 48 発 (1〜48, 1 フレーム間隔)", len(bl) == 48, str(len(bl)))
    check("作成: パラメータがルートに付く", all(cmds.attributeQuery("dfx_" + p.name, node=root, exists=True)
                                         for p in vu.stored_params()))
    check("作成: 更新が必要な項目だけ (更新) 表示",
          u"(更新)" in cmds.attributeQuery("dfx_interval", node=root, niceName=True)
          and u"(更新)" not in cmds.attributeQuery("dfx_travel_frames", node=root, niceName=True))

    # 止まったロケータ: 式どおりの位置・表示・向き
    A, C = (1.0, 2.0, 3.0), (11.0, -2.0, 8.0)
    cmds.setAttr(muz + ".translate", *A, type="double3")
    cmds.setAttr(tgt + ".translate", *C, type="double3")
    bad = []
    for i in (0, 4, 20, 47):
        fire = 1.0 + i
        for f in (fire - 1, fire, fire + 2, fire + 6, fire + 6.5, fire + 7):
            p = pos(bl[i], f)
            want_vis = vu.bullet_visible(f, fire, 6.0)
            if bool(cmds.getAttr(bl[i] + ".visibility")) != want_vis:
                bad.append(("vis", i, f))
            if want_vis and not near(p, vu.bullet_position(A, C, f, fire, 6.0)):
                bad.append(("pos", i, f, p))
    check("静止: 位置と表示が式どおり", not bad, str(bad[:3]))
    pos(bl[3], 6)
    m = cmds.xform(bl[3], q=True, ws=True, m=True)
    check("向き: 弾の X 軸が進行方向", near(unit(m[0:3]), unit([c - a for a, c in zip(A, C)]), 1e-4),
          str(m[0:3]))
    check("向き: つぶれた球は進行方向に伸び率倍",
          abs(dist(m[0:3], (0, 0, 0)) - 0.08 * 4.0) < 1e-4 and abs(dist(m[4:7], (0, 0, 0)) - 0.08) < 1e-4)

    # キーで動くロケータ: 発射時点の位置へ飛ぶ
    cmds.setAttr(muz + ".translate", 0, 0, 0, type="double3")
    cmds.setKeyframe(tgt, attribute="translateZ", time=1, value=0)
    cmds.setKeyframe(tgt, attribute="translateZ", time=21, value=20)
    cmds.keyTangent(tgt, attribute="translateZ", inTangentType="linear", outTangentType="linear")
    cmds.setKeyframe(muz, attribute="translateY", time=1, value=0)
    cmds.setKeyframe(muz, attribute="translateY", time=21, value=10)
    cmds.keyTangent(muz, attribute="translateY", inTangentType="linear", outTangentType="linear")
    bad = []
    for i in (0, 4, 10, 15):
        fire = 1.0 + i
        a = (0.0, (fire - 1) * 0.5, 0.0)
        c = (11.0, -2.0, fire - 1)
        for f in (fire, fire + 1, fire + 3, fire + 5, fire + 6):
            if not near(pos(bl[i], f), vu.bullet_position(a, c, f, fire, 6.0)):
                bad.append((i, f, pos(bl[i], f)))
    check("移動するロケータ: 発射時点の位置で飛ぶ", not bad, str(bad[:3]))
    p_before = pos(bl[0], 4)
    cmds.setKeyframe(tgt, attribute="translateX", time=1, value=11)
    cmds.setKeyframe(tgt, attribute="translateX", time=3, value=-30)  # 発射 (1) 後に目標を動かす
    check("発射後に目標が動いても、飛んでいる弾は変わらない", near(pos(bl[0], 4), p_before))
    check("その後に撃った弾は新しい位置へ", near(pos(bl[5], 12), (-30.0, -2.0, 5.0)), str(pos(bl[5], 12)))
    cmds.cutKey(tgt, attribute="translateX")
    cmds.setAttr(tgt + ".translateX", 11)

    # (比較) 追尾弾は発射後に目標を動かすと軌道が変わる
    h_root, h_locs = create()
    hb = bullets_of(h_root)[0]
    h_before = pos(hb, 4)
    cmds.setKeyframe(h_locs[2], attribute="translateZ", time=1, value=0)
    cmds.setKeyframe(h_locs[2], attribute="translateZ", time=4, value=15)
    check("(比較) 追尾弾は発射後でも目標に追従する", not near(pos(hb, 4), h_before))
    cmds.delete(h_root)

    # ルートのパラメータはすぐ反映
    cmds.setAttr(root + ".dfx_travel_frames", 10)
    check("即反映: 着弾までのフレーム数", near(pos(bl[0], 6), vu.bullet_position(
        (0, 0, 0), (11, -2, 0), 6, 1, 10)) and vis(bl[0], 11) and not vis(bl[0], 12))
    cmds.setAttr(root + ".dfx_speed_mode", 1)
    cmds.setAttr(root + ".dfx_min_speed", 30)
    check("即反映: 減速 (リニア 30%)", near(pos(bl[0], 6), vu.bullet_position(
        (0, 0, 0), (11, -2, 0), 6, 1, 10, vu.SPEED_LINEAR, 0.3)))
    cmds.setAttr(root + ".dfx_speed_mode", 2)
    check("即反映: 減速 (2乗 30%)", near(pos(bl[0], 4), vu.bullet_position(
        (0, 0, 0), (11, -2, 0), 4, 1, 10, vu.SPEED_QUADRATIC, 0.3)))
    cmds.setAttr(root + ".dfx_speed_mode", 0)
    cmds.setAttr(root + ".dfx_travel_frames", 6)
    cmds.setAttr(root + ".dfx_radius", 0.2)
    cmds.setAttr(root + ".dfx_shape", 0)
    check("即反映: 半径・形", near(cmds.getAttr(bl[0] + ".scale")[0], (0.2, 0.2, 0.2)))
    cmds.setAttr(root + ".dfx_color", 0, 1, 0, type="float3")
    check("即反映: 色", near(cmds.getAttr(root + "_mat.outColor")[0], (0, 1, 0)))

    # 名前変更・別の親の下へ移動しても動く
    off = cmds.group(empty=True, name="offsetParent")
    cmds.setAttr(off + ".translate", 0, 0, 100, type="double3")
    cmds.setAttr(off + ".rotateY", 90)
    new_tgt = cmds.parent(tgt, off)[0]
    new_tgt = cmds.rename(new_tgt, "myTarget")
    t_world = cmds.getAttr(cmds.listRelatives(new_tgt, shapes=True, fullPath=True)[0]
                           + ".worldPosition", time=5)[0]
    check("名前変更・親の変更後もワールド位置へ飛ぶ",
          near(pos(bl[4], 11), t_world) and near(pos(bl[4], 8), vu.bullet_position(
              (0, 2.0, 0), t_world, 8, 5, 6)), str((pos(bl[4], 11), t_world)))
    tgt = cmds.ls(new_tgt, long=True)[0]

    # 更新
    cmds.setAttr(root + ".dfx_interval", 2)
    cmds.select(root)
    run_tool(vu.VulcanUpdateTool)
    bl = bullets_of(root)
    check("更新: 発射間隔 2 → 24 発", len(bl) == 24, str(len(bl)))
    check("更新: ロケータ (名前・親・キー) はそのまま", cmds.objExists(tgt) and cmds.objExists(muz)
          and cmds.keyframe(muz, attribute="translateY", query=True, keyframeCount=True) == 2)
    check("更新後: 色・半径は保持", near(cmds.getAttr(root + "_mat.outColor")[0], (0, 1, 0))
          and near(cmds.getAttr(bl[0] + ".scale")[0], (0.2, 0.2, 0.2)))
    n1 = len(cmds.ls())
    run_tool(vu.VulcanUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # 並列評価でも同じ
    snap = sample(bl[:6], [1, 3, 5, 8, 11, 14])
    cmds.evaluationManager(mode="parallel")
    try:
        check("並列評価でも同じ動き", not same_motion(snap, sample(bl[:6], [1, 3, 5, 8, 11, 14])))
    finally:
        cmds.evaluationManager(mode="off")

    # プリセット
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        dialog.answer = os.path.join(tmp, "vulcan_preset")
        cmds.select(root)
        run_tool(_preset.PresetSaveTool)
        path = dialog.answer + ".json"
        check("プリセット: 保存", os.path.isfile(path) and "vulcan" in dialog.calls[-1]["startingDirectory"])
        root2 = run_tool(vu.VulcanTool)
        dialog.answer = path
        cmds.select(root2)
        run_tool(_preset.PresetLoadTool)
        check("プリセット: 読み込みで同じ値・弾数", mu.read_param_values(root2, vu.stored_params()) ==
              mu.read_param_values(root, vu.stored_params()) and len(bullets_of(root2)) == 24)
        hr, _ = create()
        cmds.select(hr)
        try:
            run_tool(_preset.PresetLoadTool)
            check("プリセット: 追尾弾には読み込めない", False)
        except ToolError:
            check("プリセット: 追尾弾には読み込めない", True)
        cmds.delete(root2, hr)
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var

    # ベイク
    frames = [1, 2, 4, 7, 9, 12, 15, 20, 26, 33]
    snap = sample(bl, frames)
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=0, end_frame=60)
    check("ベイク: 動き・向き・大きさ・表示が同じ", not same_motion(snap, sample(bl, frames)),
          str(same_motion(snap, sample(bl, frames))[:2]))
    check("ベイク: エクスプレッションなどが消える",
          not cmds.ls(type=["expression", "polySphere", "condition", "multiplyDivide"]))
    cmds.setKeyframe(tgt, attribute="translateZ", time=1, value=-50)
    check("ベイク後: ロケータを動かしても弾は動かない", not same_motion(snap, sample(bl, frames)))

    # エラー
    try:
        run_tool(vu.VulcanTool, start_frame=10, end_frame=5)
        check("エラー: 終了 < 開始", False)
    except ToolError:
        check("エラー: 終了 < 開始", True)
    r3 = run_tool(vu.VulcanTool)
    cmds.delete(r3 + "|" + r3 + "_target")
    cmds.select(r3)
    try:
        run_tool(vu.VulcanUpdateTool)
        check("エラー: 目標が消された", False)
    except ToolError as e:
        check("エラー: 目標が消された", u"目標" in u"%s" % e, u"%s" % e)

    # アンドゥ・全削除
    new_scene()
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    run_tool(vu.VulcanTool)
    cmds.undo()
    check("アンドゥ 1 回で全て消える", not (set(cmds.ls()) - before_nodes))
    run_tool(vu.VulcanTool, use_instance=False)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: ロケータごと消える", not cmds.ls("dfx_vulcan*") and not mu.list_tagged())


@section
def t19_focus_circle():
    from dummy_effect_maker.tools import bake, focus_lines as fl
    new_scene()
    cmds.flushUndo()
    aspect = cmds.getAttr("defaultResolution.deviceAspectRatio")
    cam = cmds.ls("persp", long=True)[0]
    cmds.xform(cam, ws=True, t=(20, 10, 30), ro=(-15, 30, 0))
    cmds.select(cam)
    before_nodes = set(cmds.ls())
    root = run_tool(fl.FocusLinesTool, motion=fl.MOTION_STATIC)
    focus = cmds.ls(root + "_focus", long=True)[0]
    check("円: _frame の子に作られ、ルートにつながる",
          focus.split("|")[-2] == root + "_frame"
          and cmds.listConnections(root + ".dfx_focus", source=True) == [root + "_focus"])
    check("円: 既定の大きさ 0.3、線の長さ 1",
          near(cmds.getAttr(focus + ".scale")[0], (0.3, 0.3, 1.0))
          and cmds.getAttr(root + ".dfx_length") == 1.0)
    check("円: Z 移動・回転はロック", all(cmds.getAttr(focus + "." + a, lock=True)
                                     for a in ("tz", "rx", "ry", "rz", "sz")))
    layout = fl.line_layout(80, aspect, 1, 0.5, 0.5)

    def check_lines(name, center, radius, length=1.0, layout=layout, lengths=None):
        """各線の画面の端の位置と先端が line_geometry どおりで、先端が円の中に入らない。"""
        bad = []
        for i, (line, (angle, _, lf, tf)) in enumerate(zip(lines_of(root), layout)):
            if lengths is not None:
                lf = lengths[i]
            at_edge, _, tip = line_points(cam, line)
            want_edge, want_tip = fl.line_geometry(angle, aspect, center, radius, length * lf)
            # 楕円の中か (少しの誤差は許す)。円が画面の端にかかる方向は長さ 0 で先端 = 端
            has_room = math.hypot(want_edge[0] - want_tip[0], want_edge[1] - want_tip[1]) > 1e-6
            inside = has_room and (((tip[0] - center[0]) / max(radius[0], 1e-9)) ** 2 +
                                   ((tip[1] - center[1]) / max(radius[1], 1e-9)) ** 2 < 1 - 1e-4)
            if (math.hypot(at_edge[0] - want_edge[0], at_edge[1] - want_edge[1]) > 1e-4
                    or math.hypot(tip[0] - want_tip[0], tip[1] - want_tip[1]) > 1e-4 or inside):
                bad.append((line.split("|")[-1], at_edge, want_edge, tip, want_tip))
        check(name, not bad, str(bad[:2]))

    check_lines("既定: 線は円 (半径 0.3) の縁まで、円の中には入らない", (0, 0), (0.3, 0.3))
    l3 = lines_of(root)[3]
    keep = cmds.getAttr(l3 + ".dfx_len")
    cmds.setAttr(l3 + ".dfx_len", 1.0)
    tip = line_points(cam, l3)[2]
    check("長さの倍率 1 の線は円の縁にちょうど届く", abs(math.hypot(tip[0], tip[1]) - 0.3) < 1e-4,
          str(math.hypot(tip[0], tip[1])))
    cmds.setAttr(l3 + ".dfx_len", keep)

    # 円を動かす・楕円にする
    cmds.setAttr(focus + ".tx", 0.4)
    cmds.setAttr(focus + ".ty", -0.1)
    cmds.setAttr(focus + ".scaleX", 0.2)
    cmds.setAttr(focus + ".scaleY", 0.1)
    check_lines("円を (0.4, -0.1) に動かし楕円 (0.2 x 0.1) に: 線がそこへ向かう", (0.4, -0.1), (0.2, 0.1))
    cmds.setAttr(root + ".dfx_length", 0.5)
    check_lines("線の長さ 0.5: 端〜円の縁の半分", (0.4, -0.1), (0.2, 0.1), 0.5)
    cmds.setAttr(root + ".dfx_length", 1.0)

    # 円のアニメーション
    for t, x, y, s in ((1, -0.5, 0.0, 0.1), (11, 0.5, 0.2, 0.4)):
        for a, val in (("translateX", x), ("translateY", y), ("scaleX", s), ("scaleY", s)):
            cmds.setKeyframe(focus, attribute=a, time=t, value=val)
    ok = True
    for f in (1, 4, 8, 11):
        pos(lines_of(root)[0], f)
        c = (cmds.getAttr(focus + ".tx"), cmds.getAttr(focus + ".ty"))
        r = (cmds.getAttr(focus + ".sx"), cmds.getAttr(focus + ".sy"))
        n_before = len([x for x in RESULTS if not x[1]])
        check_lines(u"円のキー: フレーム %d で線が円に合わせて動く" % f, c, r)
        ok = ok and n_before == len([x for x in RESULTS if not x[1]])

    # 名前変更しても動く
    focus = cmds.ls(cmds.rename(focus, "myFocus"), long=True)[0]
    pos(lines_of(root)[0], 11)
    check_lines("円の名前を変えても動く", (0.5, 0.2), (0.4, 0.4))

    # 更新: 円は位置・大きさ・キーごと残る
    cmds.setAttr(root + ".dfx_count", 30)
    cmds.select(root)
    run_tool(fl.FocusLinesUpdateTool)
    focus = cmds.ls("myFocus", long=True)
    check("更新: 円は名前・キーごと残る", len(focus) == 1 and
          cmds.keyframe(focus[0] + ".translateX", query=True, keyframeCount=True) == 2
          and focus[0].split("|")[-2] == root + "_frame", str(focus))
    focus = focus[0]
    pos(lines_of(root)[0], 6)
    check_lines("更新後も円に向かう (30 本)", (cmds.getAttr(focus + ".tx"), cmds.getAttr(focus + ".ty")),
                (cmds.getAttr(focus + ".sx"), cmds.getAttr(focus + ".sy")),
                layout=fl.line_layout(30, aspect, 1, 0.5, 0.5))
    n1 = len(cmds.ls())
    run_tool(fl.FocusLinesUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # 伸びて移動 + 円のアニメーション
    cmds.playbackOptions(minTime=1, maxTime=30)
    cmds.setAttr(root + ".dfx_motion", 2)
    cmds.setAttr(root + ".dfx_count", 20)
    run_tool(fl.FocusLinesUpdateTool)
    schedules = fl.grow_schedule(1, 20, 1, 30, 6, aspect, 0.5)
    for f in (1, 3, 6, 9, 14):
        pos(lines_of(root)[0], f)
        cur = [[c for c in cycles if c[0] <= f < c[0] + 6][0] for cycles in schedules]
        lay = [(c[1], 0, 1, 1) for c in cur]
        lengths = [c[3] * (f - c[0] + 1) / 6.0 for c in cur]
        check_lines(u"伸びて移動 + 円のキー: フレーム %d" % f,
                    (cmds.getAttr(focus + ".tx"), cmds.getAttr(focus + ".ty")),
                    (cmds.getAttr(focus + ".sx"), cmds.getAttr(focus + ".sy")),
                    layout=lay, lengths=lengths)

    # 円が画面の外: エラーにならず、線は長さ 0
    cmds.cutKey(focus, attribute=["translateX", "translateY"])
    cmds.setAttr(focus + ".tx", 3)
    cmds.setAttr(focus + ".ty", 0)
    pos(lines_of(root)[0], 7)
    cur = [[c for c in cycles if c[0] <= 7 < c[0] + 6][0] for cycles in schedules]
    check_lines(u"円が画面の外: 画面の外の円へ向かう (エラーにならない)", (3, 0),
                (cmds.getAttr(focus + ".sx"), cmds.getAttr(focus + ".sy")),
                layout=[(c[1], 0, 1, 1) for c in cur],
                lengths=[c[3] * (7 - c[0] + 1) / 6.0 for c in cur])
    cmds.setAttr(focus + ".tx", 0.2)

    # ベイク
    snap = {}
    for f in (1, 5, 9):
        pos(lines_of(root)[0], f)
        snap[f] = [line_points(cam, l)[2] for l in lines_of(root)]
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=30)
    ok = True
    for f in (1, 5, 9):
        pos(lines_of(root)[0], f)
        now = [line_points(cam, l)[2] for l in lines_of(root)]
        ok = ok and all(abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-4 for a, b in zip(snap[f], now))
    check("ベイク: 円のアニメーション込みで同じ", ok)
    check("ベイク: エクスプレッションが消える", not cmds.ls(type="expression"))
    cmds.setKeyframe(focus, attribute="scaleX", time=5, value=0.9)
    pos(lines_of(root)[0], 5)
    now = [line_points(cam, l)[2] for l in lines_of(root)]
    check("ベイク後: 円を変えても線は動かない",
          all(abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-4 for a, b in zip(snap[5], now)))

    # アンドゥ・全削除
    new_scene()
    cmds.select("persp")
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    run_tool(fl.FocusLinesTool)
    cmds.undo()
    check("アンドゥ 1 回で円も消える", not (set(cmds.ls()) - before_nodes))
    cmds.select("persp")
    run_tool(fl.FocusLinesTool, count=10)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 円も消える", not cmds.ls("dfx_focusLines*") and not mu.list_tagged())


@section
def t20_focus_tail_and_round_tip():
    from dummy_effect_maker.tools import bake, focus_lines as fl
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=30)
    aspect = cmds.getAttr("defaultResolution.deviceAspectRatio")
    cam = cmds.ls("persp", long=True)[0]
    cmds.xform(cam, ws=True, t=(20, 10, 30), ro=(-15, 30, 0))
    cmds.select(cam)
    root = run_tool(fl.FocusLinesTool, count=40)
    lines = lines_of(root)
    check("既定: 先端はとがる・延長 0.5", cmds.getAttr(root + ".dfx_tip_shape", asString=True) == fl.TIP_SHARP
          and cmds.getAttr(root + ".dfx_overscan") == 0.5)

    def outside(p, margin=0.0):
        return abs(p[0]) >= 1 + margin - 1e-6 or abs(p[1]) >= 1 / aspect + margin - 1e-6

    # 伸びて移動の全フレーム: 根元 (延長の外側の角) はいつも画面の外
    bad, shortest = [], 9.0
    for f in range(1, 31):
        pos(lines[0], f)
        for line in lines:
            at_edge, _, tip = line_points(cam, line)
            length = math.hypot(at_edge[0] - tip[0], at_edge[1] - tip[1])
            shortest = min(shortest, length)
            tail = part_of(line, "tail")
            far = mesh_points(cam, tail, 4)[2:]
            if length > 1e-6 and not (outside(far[0]) and outside(far[1])):
                bad.append((f, line.split("|")[-1], far))
    check("延長: 伸び始めの短い線でも、根元は画面の外 (全線・全フレーム)", not bad and shortest < 0.05,
          "最短 %.4f %s" % (shortest, bad[:2]))
    pos(lines[0], 6)
    l0 = lines[0]
    at_edge, (e1, e2), _ = line_points(cam, l0)
    tv = mesh_points(cam, part_of(l0, "tail"), 4)
    far_mid = ((tv[2][0] + tv[3][0]) / 2, (tv[2][1] + tv[3][1]) / 2)
    check("延長: 画面の端から外へ 0.5、太さは本体の根元と同じ",
          abs(math.hypot(far_mid[0] - at_edge[0], far_mid[1] - at_edge[1]) - 0.5) < 1e-4
          and abs(math.hypot(tv[0][0] - tv[1][0], tv[0][1] - tv[1][1])
                  - math.hypot(e1[0] - e2[0], e1[1] - e2[1])) < 1e-4
          and math.hypot(far_mid[0], far_mid[1]) > math.hypot(at_edge[0], at_edge[1]))
    cmds.setAttr(root + ".dfx_overscan", 1.5)
    tv = mesh_points(cam, part_of(l0, "tail"), 4)
    far_mid = ((tv[2][0] + tv[3][0]) / 2, (tv[2][1] + tv[3][1]) / 2)
    check("即反映: 画面の外への延長 1.5",
          abs(math.hypot(far_mid[0] - at_edge[0], far_mid[1] - at_edge[1]) - 1.5) < 1e-4)

    # 丸い先端
    cmds.setAttr(root + ".dfx_motion", 0)  # 静止
    run_tool(fl.FocusLinesUpdateTool)
    lines = lines_of(root)
    layout = fl.line_layout(40, aspect, 1, 0.5, 0.5)
    tips_hidden = all(not cmds.getAttr(part_of(l, "tip") + ".visibility") for l in lines)
    check("とがる: 丸い先端は非表示、本体の先端は点", tips_hidden and all(
        math.hypot(*[a - b for a, b in zip(*mesh_points(cam, l, 4)[1:3])][:2]) < 1e-6 for l in lines[:5]))
    cmds.setAttr(root + ".dfx_thickness", 0.06)
    cmds.setAttr(root + ".dfx_tip_shape", 1)
    cmds.setAttr(root + ".dfx_tip_width", 0.5)

    def check_round(name, tip_width):
        bad = []
        for line, (angle, _, lf, tf) in zip(lines_of(root), layout):
            edge_pt, want_tip = fl.line_geometry(angle, aspect, (0, 0), (0.3, 0.3), lf)
            total = math.hypot(edge_pt[0] - want_tip[0], edge_pt[1] - want_tip[1])
            body_len, radius = fl.tip_layout(total, 0.06 * tf, True, tip_width)
            at_edge, _, tip = line_points(cam, line)
            vs = mesh_points(cam, line, 4)
            tip_half = math.hypot(vs[1][0] - vs[2][0], vs[1][1] - vs[2][1]) / 2
            cap = part_of(line, "tip")
            cv = mesh_points(cam, cap, 32)
            center = (sum(p[0] for p in cv) / 32, sum(p[1] for p in cv) / 32)
            r = [math.hypot(p[0] - center[0], p[1] - center[1]) for p in cv]
            reach = math.hypot(center[0] - at_edge[0], center[1] - at_edge[1]) + max(r)
            if (abs(math.hypot(tip[0] - at_edge[0], tip[1] - at_edge[1]) - body_len) > 1e-4
                    or abs(tip_half - tip_width * 0.06 * tf) > 1e-4
                    or math.hypot(center[0] - tip[0], center[1] - tip[1]) > 1e-4
                    or max(r) - min(r) > 1e-4 or abs(max(r) - radius) > 1e-4
                    or abs(reach - total) > 1e-4 or not cmds.getAttr(cap + ".visibility")):
                bad.append((line.split("|")[-1], body_len, radius, tip_half, max(r), reach, total))
        check(name, not bad, str(bad[:2]))

    check_round("丸い: 先端は円 (潰れない)。太さ = 先端の太さ、線の先は元の長さでちょうど終わる", 0.5)
    cmds.setAttr(root + ".dfx_tip_width", 1.0)
    check_round("即反映: 先端の太さ 1 (同じ太さのまま丸く終わる)", 1.0)
    p = mesh_points(cam, lines_of(root)[0], 4)
    check("先端の太さ 1: 本体は平行 (長方形)", abs(math.hypot(p[0][0] - p[3][0], p[0][1] - p[3][1])
                                       - math.hypot(p[1][0] - p[2][0], p[1][1] - p[2][1])) < 1e-6)
    cmds.setAttr(root + ".dfx_tip_width", 0.5)

    # 短い線: 先端の円が線の長さより大きくならない
    cmds.setAttr(root + ".dfx_length", 0.01)
    bad = []
    for line, (angle, _, lf, tf) in zip(lines_of(root), layout):
        edge_pt, want_tip = fl.line_geometry(angle, aspect, (0, 0), (0.3, 0.3), 0.01 * lf)
        total = math.hypot(edge_pt[0] - want_tip[0], edge_pt[1] - want_tip[1])
        cv = mesh_points(cam, part_of(line, "tip"), 32)
        center = (sum(q[0] for q in cv) / 32, sum(q[1] for q in cv) / 32)
        r = max(math.hypot(q[0] - center[0], q[1] - center[1]) for q in cv)
        if abs(r - min(total, 0.5 * 0.06 * tf)) > 1e-4:
            bad.append((line.split("|")[-1], r, total))
    check("短い線: 先端の円は線の長さまでに縮む", not bad, str(bad[:2]))
    cmds.setAttr(root + ".dfx_length", 1.0)

    # ベイク (丸い先端と延長も)
    cmds.playbackOptions(minTime=1, maxTime=12)
    cmds.setAttr(root + ".dfx_motion", 2)
    run_tool(fl.FocusLinesUpdateTool)
    parts = [n for l in lines_of(root) for n in (l, part_of(l, "tail"), part_of(l, "tip"))]
    frames = [1, 3, 6, 9]
    snap = sample(parts, frames)
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=12)
    check("ベイク: 本体・延長・丸い先端の動きが同じ", not same_motion(snap, sample(parts, frames)),
          str(same_motion(snap, sample(parts, frames))[:2]))
    src = cmds.ls(root + "_lineSrc", long=True)[0]
    check("ベイク: エクスプレッションが消え、先端の太さはそのまま",
          not cmds.ls(type="expression") and abs(cmds.pointPosition(src + ".vtx[1]", local=True)[1] - 0.5) < 1e-6)
    cmds.undo()

    # 古い集中線 (このパラメータが無い) も更新で使えるようになる
    for a in ("dfx_tip_shape", "dfx_tip_width", "dfx_overscan"):
        cmds.deleteAttr(root, attribute=a)
    cmds.select(root)
    run_tool(fl.FocusLinesUpdateTool)
    check("古い集中線: 更新で先端の形・延長が追加される",
          cmds.getAttr(root + ".dfx_tip_shape") == 0 and cmds.getAttr(root + ".dfx_overscan") == 0.5
          and len(lines_of(root)) == 40)


@section
def t21_speed_lines():
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, speed_lines as sl
    new_scene()
    aspect = cmds.getAttr("defaultResolution.deviceAspectRatio")
    cam = cmds.ls("persp", long=True)[0]
    cmds.xform(cam, ws=True, t=(20, 10, 30), ro=(-15, 30, 0))
    cam_shape = cmds.listRelatives(cam, shapes=True, fullPath=True)[0]
    cmds.select(cam)
    cmds.flushUndo()
    root = run_tool(sl.SpeedLinesTool)
    band = cmds.ls(root + "_band", long=True)[0]
    flow = cmds.ls(root + "_flow", long=True)[0]
    check("スピードライン: 四角が _frame の子・ルートにつながる",
          band.split("|")[-2] == root + "_frame"
          and cmds.listConnections(root + ".dfx_band", source=True) == [root + "_band"])
    check("四角: 既定 幅 1・高さ 0.4 (scale 0.5 / 0.2)", near(cmds.getAttr(band + ".scale")[0], (0.5, 0.2, 1)))
    check("四角: X / Y / Z 回転にキーが打てる", all(cmds.getAttr(band + "." + a, keyable=True)
                                            and not cmds.getAttr(band + "." + a, lock=True)
                                            for a in ("rx", "ry", "rz")))
    lines = cmds.listRelatives(flow, children=True, fullPath=True)
    check("40 本", len(lines) == 40)
    v = dict((p.name, p.default) for p in sl.SpeedLinesTool.params)

    def zc():
        return sl.camera_depth(cmds.getAttr(cam_shape + ".focalLength"),
                               cmds.getAttr(cam_shape + ".horizontalFilmAperture"))

    def band_values():
        return [cmds.getAttr(band + "." + a) for a in ("tx", "ty", "rx", "ry", "rz", "sy")]

    def line_local(line):
        """線の頂点を、線が乗る面 (_flow) の座標で。"""
        inv = om.MMatrix(cmds.getAttr(flow + ".worldInverseMatrix[0]"))
        shape = cmds.listRelatives(line, shapes=True, fullPath=True)[0].split("|")[-1]
        out = []
        for i in range(2 * sl.WIDTH_SAMPLES):
            p = om.MPoint(*cmds.pointPosition("%s|%s.vtx[%d]" % (line, shape, i), world=True)) * inv
            out.append((p.x, p.y, p.z))
        return out

    def line_info(line):
        vs = line_local(line)
        n = sl.WIDTH_SAMPLES
        mids = [((vs[j][0] + vs[2 * n - 1 - j][0]) / 2, (vs[j][1] + vs[2 * n - 1 - j][1]) / 2)
                for j in range(n)]
        widths = [math.hypot(vs[j][0] - vs[2 * n - 1 - j][0], vs[j][1] - vs[2 * n - 1 - j][1]) / 2
                  for j in range(n)]
        return mids[0], mids[-1], widths, vs

    def on_screen(p):
        return abs(p[0]) <= 1 and abs(p[1]) <= 1 / aspect and p[2] > 0

    def check_frames(name, frames, values, min_visible=0.4):
        bad, inside, off_lane, flat, visible, region_bad = [], [], [], [], [], []
        for f in frames:
            pos(lines[0], f)
            b = band_values()
            hb = abs(b[5])
            region = sl.plane_region(aspect, zc(), b[:5], values.get("reach"))
            got = [cmds.getAttr(flow + "." + a) for a in sl.REGION_ATTRS]
            if max(abs(x - y) for x, y in zip(region, got)) > 1e-6:
                region_bad.append((f, region, got))
            seen_now = 0
            for i, line in enumerate(lines):
                phase, sf, th = [cmds.getAttr(line + "." + a) for a in
                                 (sl.LINE_PHASE_ATTR, sl.LINE_SPEED_ATTR, sl.LINE_THICK_ATTR)]
                x0, y, length = sl.line_state(f, i, phase, sf, th, values, region, hb)
                start, end, widths, vs = line_info(line)
                if (math.hypot(start[0] - x0, start[1] - y) > 1e-4
                        or abs(end[0] - start[0] - length) > 1e-4 or abs(end[1] - start[1]) > 1e-4):
                    bad.append((f, i, start, (x0, y), end[0] - start[0], length))
                if max(abs(p[2]) for p in vs) > 1e-5:
                    flat.append((f, i))
                if min(abs(p[1]) for p in vs) < hb - 1e-5:
                    inside.append((f, i, min(abs(p[1]) for p in vs)))
                if region[3] > hb and not (region[2] - 1e-5 <= start[1] <= region[3] + 1e-5):
                    off_lane.append((f, i, start[1], region[2], region[3]))
                if any(on_screen(p) for p in mesh_points(cam, line, 2 * sl.WIDTH_SAMPLES)):
                    seen_now += 1
            visible.append(seen_now / float(len(lines)))
        check(name + ": 面の上の画面の範囲 (エクスプレッション = Python)", not region_bad, str(region_bad[:1]))
        check(name + ": 位置・長さが式どおり (四角の X 方向に流れる)", not bad, str(bad[:2]))
        check(name + ": 線は四角の面の上", not flat, str(flat[:2]))
        check(name + ": 四角の上辺と下辺の間には入らない", not inside, str(inside[:2]))
        check(name + ": 線の高さは画面の範囲の中", not off_lane, str(off_lane[:2]))
        check(name + ": 画面に出ている線が多い (平均 %d%% 以上)" % (min_visible * 100),
              sum(visible) / len(visible) >= min_visible,
              "平均 %.0f%%" % (100 * sum(visible) / len(visible)))

    check_frames("既定", range(1, 31), v)

    # 流れる: 次のフレームで右へ (傾き 0・回転 0 なら 速さ × 倍率)
    moved = []
    for line in lines:
        sf = cmds.getAttr(line + "." + sl.LINE_SPEED_ATTR)
        pos(lines[0], 10)
        a = line_info(line)[0]
        pos(lines[0], 11)
        b = line_info(line)[0]
        if b[0] > a[0]:
            moved.append(abs(b[0] - a[0] - 0.3 * sf) < 1e-4 and abs(b[1] - a[1]) < 1e-6)
    check("流れる: 1 フレームで 速さ × 線ごとの倍率 だけ右へ", moved and all(moved), str(moved.count(False)))
    ys = set()
    l0 = lines[0]
    for f in range(1, 200, 3):
        pos(l0, f)
        ys.add(round(line_info(l0)[0][1], 4))
    check("画面を通り過ぎるたびに別の高さ・上下から入り直す", len(ys) >= 4 and min(ys) < 0 < max(ys), str(sorted(ys)))

    # 太さ (入り・中・終わり) はすぐ反映
    for name, val in (("width_start", 0.01), ("width_mid", 0.03), ("width_end", 0.005)):
        cmds.setAttr(root + ".dfx_" + name, val)
        v[name] = val
    pos(l0, 5)
    th = cmds.getAttr(l0 + "." + sl.LINE_THICK_ATTR)
    widths = line_info(l0)[2]
    n = sl.WIDTH_SAMPLES
    want = [sl.width_at(j / float(n - 1), 0.01, 0.03, 0.005) * th for j in range(n)]
    check("太さ: 入り 0.01 / 中 0.03 / 終わり 0.005 (間はなめらか)",
          all(abs(a - b) < 1e-5 for a, b in zip(widths, want)), str([round(w, 4) for w in widths]))

    cmds.setAttr(root + ".dfx_speed", 0.6)
    cmds.setAttr(root + ".dfx_length", 1.2)
    cmds.setAttr(root + ".dfx_length_random", 0.2)
    v.update(speed=0.6, length=1.2, length_random=0.2)
    check_frames("即反映 (速さ 0.6・長さ 1.2・太さ)", [3, 9, 17], v)

    def screen_dir(f):
        """f → f+1 で線の入りの端が画面上で動いた向き (入り直した線は除く)。"""
        n2 = 2 * sl.WIDTH_SAMPLES - 1
        out = []
        for line in lines:
            pts = []
            for t in (f, f + 1):
                pos(lines[0], t)
                vs = mesh_points(cam, line, 2 * sl.WIDTH_SAMPLES)
                pts.append(((vs[0][0] + vs[n2][0]) / 2, (vs[0][1] + vs[n2][1]) / 2))
            d = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            if 1e-6 < math.hypot(*d) < 0.5:
                out.append(math.degrees(math.atan2(d[1], d[0])))
        return out

    cmds.setAttr(band + ".rz", 90)
    dirs = screen_dir(10)
    check("四角を Z 90°: 下から上へ流れる", dirs and all(abs(a - 90) < 1e-3 for a in dirs))
    check_frames("Z 90°", [4, 10], v)
    cmds.setAttr(band + ".rz", 0)

    # 奥行きの傾き (パース)
    cmds.setAttr(band + ".ry", 35)
    check_frames("Y 35° (流れる先が奥へ)", [2, 8, 15, 23], v)
    dirs = screen_dir(8)
    check("Y 35°: 上の線と下の線の向きが違う (流れる先ですぼまる)",
          dirs and max(dirs) - min(dirs) > 5, "%.1f〜%.1f°" % (min(dirs), max(dirs)))
    pos(l0, 8)
    sizes = []
    for line in lines:
        pts = mesh_points(cam, line, 2 * sl.WIDTH_SAMPLES)
        sizes.append((pts[0][2], math.hypot(pts[5][0] - pts[16][0], pts[5][1] - pts[16][1])))
    near_w = [w for z, w in sizes if z < sorted(s[0] for s in sizes)[len(sizes) // 3]]
    far_w = [w for z, w in sizes if z > sorted(s[0] for s in sizes)[2 * len(sizes) // 3]]
    check("Y 35°: 奥の線ほど細く見える", near_w and far_w and
          sum(near_w) / len(near_w) > 1.2 * sum(far_w) / len(far_w))
    cmds.setAttr(band + ".ry", 0)
    cmds.setAttr(band + ".rx", -50)
    check_frames("X -50° (上下の片側が奥へ)", [2, 8, 15], v)
    cmds.setAttr(band + ".rx", 0)
    # 大きく傾けても、線は四角から「奥の届く距離」までに収まる (遠くまで散らばらない)
    cmds.setAttr(band + ".ry", 65)
    pos(l0, 6)
    got = [cmds.getAttr(flow + "." + a) for a in sl.REGION_ATTRS]
    far = max(abs(cmds.getAttr(line + ".translateX")) for line in lines)
    wide = sl.plane_region(aspect, zc(), (0, 0, 0, 65, 0))
    check("Y 65°: 線の範囲は四角から ±奥の届く距離 (4) まで (打ち切らなければ %.1f まで伸びる)" % wide[1],
          got[1] <= v["reach"] + 1e-9 and abs(got[2]) <= v["reach"] + 1e-9 and far <= v["reach"] + v["length"] + 1e-6
          and wide[1] > 10, str((got, far)))
    cmds.setAttr(root + ".dfx_reach", 2.0)
    pos(l0, 7)
    check("奥の届く距離はすぐ反映", abs(cmds.getAttr(flow + ".dfx_umax") - 2.0) < 1e-9)
    cmds.setAttr(root + ".dfx_reach", v["reach"])
    check_frames("Y 65° (打ち切り)", [3, 9], v, min_visible=0.15)   # 大きく傾けると、画面に入る所は手前の一部だけ
    cmds.setAttr(band + ".ry", 0)

    # 四角のキー (移動・回転・傾き・高さ)
    for t, x, y, rx, ry, rz, sy in ((1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1),
                                     (20, 0.3, -0.2, 20.0, -40.0, 45.0, 0.35)):
        for at, val in (("tx", x), ("ty", y), ("rx", rx), ("ry", ry), ("rz", rz), ("sy", sy)):
            cmds.setKeyframe(band, attribute=at, time=t, value=val)
    check_frames("四角のキー (移動・回転・傾き・高さ)", [1, 6, 12, 20], v)
    jumps = []
    for line in lines[:10]:
        prev = None
        for f in range(1, 21):
            pos(lines[0], f)
            x = line_info(line)[0][0]
            if prev is not None and x < prev - 0.5:
                pass  # 画面を通り過ぎて入り直した
            elif prev is not None and (x - prev) > 1.5:
                jumps.append((line.split("|")[-1], f, prev, x))
            prev = x
    check("四角をアニメーションしても線は飛ばない", not jumps, str(jumps[:2]))

    # 極端な傾き (地平線が見える) でもエラーにならない
    cmds.cutKey(band, attribute=["rx", "ry"])
    cmds.setAttr(band + ".rx", 80)
    pos(l0, 25)
    region = [cmds.getAttr(flow + "." + a) for a in sl.REGION_ATTRS]
    check("X 80° (地平線が見える): 範囲は有限", all(abs(x) < 1e4 for x in region), str(region))
    cmds.setAttr(band + ".rx", 0)

    # 更新: 四角は残る
    cmds.setAttr(root + ".dfx_count", 15)
    cmds.select(root)
    run_tool(sl.SpeedLinesUpdateTool)
    flow = cmds.ls(root + "_flow", long=True)[0]
    lines = cmds.listRelatives(flow, children=True, fullPath=True)
    band = cmds.ls(root + "_band", long=True)[0]
    check("更新: 15 本、四角はキーごと残る", len(lines) == 15 and
          cmds.keyframe(band + ".rz", query=True, keyframeCount=True) == 2)
    check_frames("更新後", [1, 12, 20], v)
    n1 = len(cmds.ls())
    run_tool(sl.SpeedLinesUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # ベイク
    cmds.setAttr(band + ".ry", 25)
    frames = [1, 4, 8, 13, 20]
    snap = sample(lines, frames)
    src = cmds.ls(root + "_lineSrc", long=True)[0]
    before_w = cmds.pointPosition(src + ".vtx[5]", local=True)[1]
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=20)
    check("ベイク: 線の動きが同じ (四角の動き・傾きも)", not same_motion(snap, sample(lines, frames)),
          str(same_motion(snap, sample(lines, frames))[:2]))
    check("ベイク: エクスプレッションが消え、線の太さはそのまま", not cmds.ls(type="expression")
          and abs(cmds.pointPosition(src + ".vtx[5]", local=True)[1] - before_w) < 1e-6)

    # アンドゥ・全削除
    new_scene()
    cmds.select("persp")
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    run_tool(sl.SpeedLinesTool)
    cmds.undo()
    check("アンドゥ 1 回で四角も消える", not (set(cmds.ls()) - before_nodes))
    cmds.select("persp")
    run_tool(sl.SpeedLinesTool, count=5)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 四角も消え、カメラは残る", not cmds.ls("dfx_speedLines*") and cmds.objExists("persp")
          and not mu.list_tagged())


@section
def t22_camera_presets():
    from dummy_effect_maker.tools import focus_lines as fl, speed_lines as sl
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        for mod, create, update, save, load, changes, noun, keep in (
                (fl, fl.FocusLinesTool, fl.FocusLinesUpdateTool, _preset.PresetSaveTool,
                 _preset.PresetLoadTool,
                 (("count", 33), ("tip_shape", 1), ("thickness", 0.07), ("motion", 1)), u"集中線",
                 "_focus"),
                (sl, sl.SpeedLinesTool, sl.SpeedLinesUpdateTool, _preset.PresetSaveTool,
                 _preset.PresetLoadTool,
                 (("count", 12), ("speed", 0.9), ("width_mid", 0.05), ("seed", 7)), u"スピードライン",
                 "_band")):
            new_scene()
            cmds.select("persp")
            r1 = run_tool(create)
            for name, val in changes:
                cmds.setAttr("%s.dfx_%s" % (r1, name), val)
            cmds.setAttr(r1 + ".dfx_color", 1, 0, 0, type="float3")
            expected = mu.read_param_values(r1, mod.stored_params())
            dialog.answer = os.path.join(tmp, mod.EFFECT_TYPE + "_p")
            cmds.select(r1)
            run_tool(save)
            path = dialog.answer + ".json"
            check(noun + ": 保存 (既定の保存先は種類ごと)", os.path.isfile(path)
                  and mod.EFFECT_TYPE in dialog.calls[-1]["startingDirectory"])
            cmds.select("persp")
            r2 = run_tool(create)
            ctrl = cmds.ls(r2 + keep, long=True)[0]
            cmds.setAttr(ctrl + ".tx", 0.25)
            cmds.setKeyframe(ctrl, attribute="ty", time=1, value=0.1)
            dialog.answer = path
            cmds.select(r2)
            run_tool(load)
            check(noun + ": 読み込みで同じ値・作り直される", mu.read_param_values(r2, mod.stored_params())
                  == expected and len(cmds.ls(r2 + "_line_*", type="transform")) == dict(changes)["count"],
                  str(len(cmds.ls(r2 + "_line_*", type="transform"))))
            ctrl = cmds.ls(r2 + keep, long=True)
            check(noun + ": 読み込んでも中央の円・四角はそのまま", len(ctrl) == 1
                  and abs(cmds.getAttr(ctrl[0] + ".tx") - 0.25) < 1e-9
                  and cmds.keyframe(ctrl[0] + ".ty", query=True, keyframeCount=True) == 1)
            cmds.undo()
            check(noun + ": 読み込みは Ctrl+Z 1 回で戻る",
                  cmds.getAttr(r2 + ".dfx_count") == create.default_values()["count"])
            dialog.answer = None
            cmds.select(r2)
            check(noun + ": キャンセルなら何もしない", run_tool(load) is None)
            # 別の種類のプリセットは読めない
            other = sl if mod is fl else fl
            dialog.answer = path
            cmds.select("persp")
            r3 = run_tool(other.SpeedLinesTool if other is sl else other.FocusLinesTool)
            cmds.select(r3)
            try:
                run_tool(_preset.PresetLoadTool if other is sl else _preset.PresetLoadTool)
                check(noun + ": 別の種類には読み込めない", False)
            except ToolError:
                check(noun + ": 別の種類には読み込めない", True)
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var

@section
def t23_camera_shake():
    from maya import mel
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, camera_shake as cs
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=40)
    cmds.flushUndo()

    def wm(node, f):
        pos(node, f)
        return om.MMatrix(cmds.getAttr(node + ".worldMatrix[0]"))

    def axes(m):
        return (om.MVector(m[0], m[1], m[2]).normal(), om.MVector(m[4], m[5], m[6]).normal(),
                om.MVector(m[8], m[9], m[10]).normal(), om.MVector(m[12], m[13], m[14]))

    def noise(root, f):
        return [cmds.getAttr(root + "." + a, time=f) for a in cs.NOISE_ATTRS]

    def strengths(root, f):
        return cmds.getAttr(root + ".dfx_strength_x", time=f), cmds.getAttr(root + ".dfx_strength_y", time=f)

    def unshaken(root, node, f):
        """強さ 0 の時のワールド行列 (= ブレのないカメラ)。"""
        keep = [cmds.getAttr(root + ".dfx_strength_" + a) for a in "xy"]
        keyed = [bool(cmds.keyframe(root + ".dfx_strength_" + a, query=True)) for a in "xy"]
        saved = {}
        for a, k in zip("xy", keyed):
            if k:
                saved[a] = cmds.listConnections(root + ".dfx_strength_" + a, plugs=True, source=True, destination=False)[0]
                cmds.disconnectAttr(saved[a], root + ".dfx_strength_" + a)
            cmds.setAttr(root + ".dfx_strength_" + a, 0)
        m = wm(node, f)
        for a, v in zip("xy", keep):
            cmds.setAttr(root + ".dfx_strength_" + a, v)
            if a in saved:
                cmds.connectAttr(saved[a], root + ".dfx_strength_" + a)
        return m

    # ---- 注視点のないカメラ (キー・回転順 zxy・元の rotatePivotTranslate あり)
    cam = cmds.camera(centerOfInterest=12)[0]
    cam = cmds.ls(cam, long=True)[0]
    cmds.setAttr(cam + ".rotateOrder", 2)
    for f, t, r in ((1, (0, 2, 10), (-5, 10, 0)), (40, (8, 4, 2), (-20, 70, 15))):
        cmds.setKeyframe(cam, attribute="translate", time=f, value=0)
        for a, val in zip("XYZ", t):
            cmds.setKeyframe(cam, attribute="translate" + a, time=f, value=val)
        for a, val in zip("XYZ", r):
            cmds.setKeyframe(cam, attribute="rotate" + a, time=f, value=val)
    cmds.setAttr(cam + ".rotatePivotTranslate", 0.1, -0.2, 0.3, type="double3")
    cmds.select(cam)
    root = run_tool(cs.CameraShakeTool, strength_x=0.3, strength_y=0.15)
    check("画ぶれ: カメラのキーはそのまま (translate / rotate はアニメーションカーブのまま)",
          all(cmds.nodeType(cmds.listConnections(cam + "." + a, source=True, destination=False)[0]).startswith("animCurve")
              for a in ("translateX", "rotateY")))
    keys = cs.shake_keys(1, 1, 40, lambda t: 2.0, 0.5, 0.5)
    got = cmds.keyframe(root + ".dfx_noise_tx", query=True, timeChange=True)
    check("乱数のキー: 間隔 2 (ばらつき 0.5) で 1〜40 の範囲",
          [round(t, 6) for t, _ in keys] == [round(t, 6) for t in got] and got[0] == 1 and got[-1] > 40
          and all(0.99 <= b - a <= 3.01 for a, b in zip(got, got[1:])), str(got[:5]))
    bad, rot_bad = [], []
    for f in (1, 2.5, 7, 13, 26, 40):
        m = wm(cam, f)
        base = unshaken(root, cam, f)
        bx, by, bz, bp = axes(base)
        sx, sy, sz, sp = axes(m)
        n = noise(root, f)
        d = sp - bp
        want = (0.3 * n[0], 0.15 * n[1])
        if abs(d * bx - want[0]) > 1e-4 or abs(d * by - want[1]) > 1e-4 or abs(d * bz) > 1e-4:
            bad.append((f, d * bx, d * by, d * bz, want))
        # 回転: 中心までの距離 12 先の点を (ブレX, ブレY) ずらした向き
        want_rx, want_ry = cs.rotation_offset(0.3 * n[2], 0.15 * n[3], 12)
        ra = cmds.getAttr(cam + ".rotateAxis")[0]
        if abs(ra[0] - want_rx) > 1e-4 or abs(ra[1] - want_ry) > 1e-4:
            rot_bad.append((f, ra, (want_rx, want_ry)))
        # 見ている方向のずれ (横・縦) が、中心までの距離先でおおよそ (ブレX, ブレY)
        fwd = -sz
        look = sp + fwd * 12 - (bp + (-bz) * 12)
        if abs(look * bx - (d * bx + 0.3 * n[2])) > 0.05 * max(0.1, abs(0.3 * n[2])) + 0.01:
            rot_bad.append((f, "look", look * bx, d * bx + 0.3 * n[2]))
    check("注視点なし: 移動のブレは画面の横・縦 (カメラの X / Y 軸) に、強さ × 乱数", not bad, str(bad[:2]))
    check("注視点なし・回転 ON: rotateAxis が中心までの距離先のずれ相当の角度", not rot_bad, str(rot_bad[:2]))
    cmds.setAttr(root + ".dfx_rotation", 0)
    pos(cam, 9)
    check("回転 OFF: 回転のブレは無し (元の rotateAxis のまま)", near(cmds.getAttr(cam + ".rotateAxis")[0], (0, 0, 0)))
    cmds.setAttr(root + ".dfx_rotation", 1)

    # 強さのアニメーション (だんだん強く)
    cmds.setKeyframe(root, attribute="dfx_strength_x", time=1, value=0.0)
    cmds.setKeyframe(root, attribute="dfx_strength_x", time=40, value=1.0)
    ok = True
    for f in (1, 10, 30):
        n = noise(root, f)
        d = axes(wm(cam, f))[3] - axes(unshaken(root, cam, f))[3]
        want = cmds.getAttr(root + ".dfx_strength_x", time=f) * n[0]
        ok = ok and abs(d * axes(unshaken(root, cam, f))[0] - want) < 1e-4
    check("強さにキー: だんだん強くなる (すぐ反映)", ok and abs(cmds.getAttr(root + ".dfx_strength_x", time=1)) < 1e-9)
    cmds.cutKey(root, attribute="dfx_strength_x")
    cmds.setAttr(root + ".dfx_strength_x", 0.3)

    # 間隔にキー → 更新でその時々の間隔
    cmds.setKeyframe(root, attribute="dfx_interval", time=1, value=1.0)
    cmds.setKeyframe(root, attribute="dfx_interval", time=40, value=8.0)
    cmds.setAttr(root + ".dfx_interval_random", 0)
    cmds.select(root)
    run_tool(cs.CameraShakeUpdateTool)
    got = cmds.keyframe(root + ".dfx_noise_tx", query=True, timeChange=True)
    gaps = [b - a for a, b in zip(got, got[1:])]
    check("間隔にキー: 後ろほど間隔が広い (更新で反映)", gaps[0] < 1.5 and gaps[-1] > 5, str(gaps))
    n1 = len(cmds.ls())
    cmds.select(cam)  # カメラを選んでも対象になる
    run_tool(cs.CameraShakeUpdateTool)
    check("更新: カメラを選んでもよい・繰り返してもノード数一定", n1 == len(cmds.ls()))
    try:
        cmds.select(cam)
        run_tool(cs.CameraShakeTool)
        check("同じカメラに 2 つ目はエラー", False)
    except ToolError:
        check("同じカメラに 2 つ目はエラー", True)

    # ベイク: カメラの translate / rotate のキーになり、見え方は同じ
    frames = [1, 3, 8, 15, 22, 31, 40]
    before = [wm(cam, f) for f in frames]
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=40)
    after = [wm(cam, f) for f in frames]
    diff = max(abs(a[i] - b[i]) for a, b in zip(before, after) for i in range(16))
    check("ベイク: カメラの見え方 (ワールド行列) が同じ", diff < 1e-4, "%.2e" % diff)
    check("ベイク: ブレ用チャンネルは元に戻り、毎フレームのキー",
          near(cmds.getAttr(cam + ".rotateAxis")[0], (0, 0, 0))
          and near(cmds.getAttr(cam + ".rotatePivotTranslate")[0], (0.1, -0.2, 0.3))
          and cmds.keyframe(cam + ".rotateY", query=True, keyframeCount=True) == 40
          and not cmds.ls(type="expression") and mu.is_baked(cmds.ls(root, long=True)[0]))
    cmds.undo()
    check("ベイクは Ctrl+Z で戻る", cmds.ls(type="expression")
          and cmds.keyframe(cam + ".rotateY", query=True, keyframeCount=True) == 2)

    # 外す: カメラは元どおり
    base = [unshaken(root, cam, f) for f in (5, 20)]
    cmds.select(root)
    run_tool(cs.CameraShakeRemoveTool)
    now = [wm(cam, f) for f in (5, 20)]
    check("外す: カメラは元どおり (元の rotatePivotTranslate も)",
          max(abs(a[i] - b[i]) for a, b in zip(base, now) for i in range(16)) < 1e-6
          and near(cmds.getAttr(cam + ".rotatePivotTranslate")[0], (0.1, -0.2, 0.3))
          and not cmds.objExists(root))

    # ---- 注視点のあるカメラ
    c2 = cmds.camera()[0]
    mel.eval('cameraMakeNode 2 "%s"' % c2)
    c2 = cmds.ls(c2, long=True)[0]
    aim = cs.find_aim(cmds, c2)
    cmds.setAttr(c2 + ".translate", 3, 2, 9, type="double3")
    cmds.setKeyframe(aim, attribute="translateX", time=1, value=0)
    cmds.setKeyframe(aim, attribute="translateX", time=40, value=6)
    cmds.select(c2)
    r2 = run_tool(cs.CameraShakeTool, rotation=False, strength_x=0.4, strength_y=0.2)
    check("注視点あり: 注視点を見つけてつなぐ", cmds.listConnections(r2 + ".dfx_aim") == [aim.split("|")[-1]])
    bad = []
    for f in (2, 9, 17, 33):
        m = wm(c2, f)
        b = unshaken(r2, c2, f)
        bx, by, bz, bp = axes(b)
        sx, sy, sz, sp = axes(m)
        n = noise(r2, f)
        d = sp - bp
        if (abs(d * bx - 0.4 * n[0]) > 1e-4 or abs(d * by - 0.2 * n[1]) > 1e-4
                or (sz - bz).length() > 1e-6):
            bad.append((f, d * bx, 0.4 * n[0], d * by, 0.2 * n[1], (sz - bz).length()))
    check("注視点あり・回転 OFF: 画面の横・縦に動き、向きは変わらない (注視点も同じだけ動く)", not bad, str(bad[:2]))
    cmds.setAttr(r2 + ".dfx_rotation", 1)
    turned, bad = [], []
    for f in (2, 9, 17, 33):
        m = wm(c2, f)
        b = unshaken(r2, c2, f)
        n = noise(r2, f)
        turned.append((axes(m)[2] - axes(b)[2]).length())
        a_off = om.MVector(*cmds.getAttr(aim + ".rotatePivotTranslate", time=f)[0])
        bx, by, _, _ = axes(b)
        if abs(a_off * bx - 0.4 * n[2]) > 1e-3 or abs(a_off * by - 0.2 * n[3]) > 1e-3:
            bad.append((f, a_off * bx, 0.4 * n[2], a_off * by, 0.2 * n[3]))
    check("注視点あり・回転 ON: 注視点も別の乱数でぶれて、向きが変わる", max(turned) > 1e-3 and not bad,
          str(bad[:2]))
    frames = [1, 4, 11, 25, 40]
    before = [wm(c2, f) for f in frames]
    cmds.select(r2)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=40)
    after = [wm(c2, f) for f in frames]
    diff = max(abs(a[i] - b[i]) for a, b in zip(before, after) for i in range(16))
    check("注視点あり: ベイクしても見え方が同じ (注視点の translate にキー、lookAt はそのまま)",
          diff < 1e-4 and cmds.keyframe(aim + ".translateX", query=True, keyframeCount=True) == 40
          and cmds.listConnections(c2 + ".rotateX", type="aimConstraint"), "%.2e" % diff)
    cmds.undo()

    # プリセット
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        cmds.setAttr(r2 + ".dfx_interval", 5)
        cmds.setAttr(r2 + ".dfx_interpolation", 2)
        dialog.answer = os.path.join(tmp, "shake")
        cmds.select(r2)
        run_tool(_preset.PresetSaveTool)
        c3 = cmds.camera()[0]
        cmds.select(c3)
        r3 = run_tool(cs.CameraShakeTool)
        dialog.answer = os.path.join(tmp, "shake.json")
        cmds.select(r3)
        run_tool(_preset.PresetLoadTool)
        check("プリセット: 保存・読み込み", mu.read_param_values(r3, cs.stored_params())
              == mu.read_param_values(r2, cs.stored_params())
              and "camera_shake" in dialog.calls[0]["startingDirectory"])
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var

    # 全削除・アンドゥ: カメラは元どおり
    base = unshaken(r2, c2, 12)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 画ぶれが消え、カメラ・注視点は元どおり",
          not cmds.ls("dfx_cameraShake*") and cmds.objExists(c2)
          and max(abs(a - b) for a, b in zip(base, wm(c2, 12))) < 1e-6
          and near(cmds.getAttr(aim + ".rotatePivotTranslate")[0], (0, 0, 0)))
    new_scene()
    c4 = cmds.camera()[0]
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    cmds.select(c4)
    run_tool(cs.CameraShakeTool)
    cmds.undo()
    check("アンドゥ 1 回で全て消え、カメラも元どおり", not (set(cmds.ls()) - before_nodes)
          and near(cmds.getAttr(c4 + ".rotatePivotTranslate")[0], (0, 0, 0))
          and near(cmds.getAttr(c4 + ".rotateAxis")[0], (0, 0, 0)))


@section
def t24_shake_any_object():
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, camera_shake as cs
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=30)
    cmds.flushUndo()

    def wm(node, f):
        pos(node, f)
        return om.MMatrix(cmds.getAttr(node + ".worldMatrix[0]"))

    def axes(m):
        return (om.MVector(m[0], m[1], m[2]).normal(), om.MVector(m[4], m[5], m[6]).normal(),
                om.MVector(m[8], m[9], m[10]).normal(), om.MVector(m[12], m[13], m[14]))

    def still(root, node, f):
        keep = [cmds.getAttr(root + ".dfx_strength_" + a) for a in "xy"]
        for a in "xy":
            cmds.setAttr(root + ".dfx_strength_" + a, 0)
        m = wm(node, f)
        for a, v in zip("xy", keep):
            cmds.setAttr(root + ".dfx_strength_" + a, v)
        return m

    sphere = cmds.ls(cmds.polySphere(name="ball")[0], long=True)[0]
    cmds.setAttr(sphere + ".rotate", 20, 45, -10, type="double3")
    cmds.setKeyframe(sphere, attribute="translateY", time=1, value=0)
    cmds.setKeyframe(sphere, attribute="translateY", time=30, value=5)
    loc = cmds.ls(cmds.spaceLocator(name="loc")[0], long=True)[0]
    cmds.setAttr(loc + ".translate", 3, 1, -2, type="double3")
    # シェイプを選んでも、その transform がぶれる
    cmds.select([cmds.listRelatives(sphere, shapes=True, fullPath=True)[0], loc])
    roots = run_tool(cs.CameraShakeTool, strength_x=0.3, strength_y=0.2, rotation_distance=4.0)
    check("複数選択: 1 つずつ画ぶれができる (球・ロケータ)", isinstance(roots, list) and len(roots) == 2
          and [cmds.listConnections(r + ".dfx_camera")[0] for r in roots] == ["ball", "loc"], str(roots))
    rs, rl = roots
    check("複数選択: シードを 1 ずつずらす (違うぶれ方)",
          cmds.getAttr(rl + ".dfx_seed") == cmds.getAttr(rs + ".dfx_seed") + 1
          and cmds.keyframe(rs + ".dfx_noise_tx", query=True, valueChange=True)
          != cmds.keyframe(rl + ".dfx_noise_tx", query=True, valueChange=True))
    bad, rot_bad = [], []
    for f in (1, 4, 11, 23):
        m = wm(sphere, f)
        b = still(rs, sphere, f)
        bx, by, bz, bp = axes(b)
        d = axes(m)[3] - bp
        n = [cmds.getAttr(rs + "." + a, time=f) for a in cs.NOISE_ATTRS]
        if abs(d * bx - 0.3 * n[0]) > 1e-4 or abs(d * by - 0.2 * n[1]) > 1e-4 or abs(d * bz) > 1e-4:
            bad.append((f, d * bx, 0.3 * n[0], d * by, 0.2 * n[1]))
        want = cs.rotation_offset(0.3 * n[2], 0.2 * n[3], 4.0)
        ra = cmds.getAttr(sphere + ".rotateAxis")[0]
        if abs(ra[0] - want[0]) > 1e-4 or abs(ra[1] - want[1]) > 1e-4:
            rot_bad.append((f, ra, want))
    check("球: 球の X / Y 軸の向きに 強さ × 乱数 ぶれる (キーはそのまま)", not bad
          and cmds.keyframe(sphere + ".translateY", query=True, keyframeCount=True) == 2, str(bad[:2]))
    check("球: 回転のブレは「回転の基準距離」先のずれ相当の角度", not rot_bad, str(rot_bad[:2]))
    cmds.setAttr(rs + ".dfx_rotation_distance", 1.0)
    pos(sphere, 11)
    n = [cmds.getAttr(rs + "." + a) for a in cs.NOISE_ATTRS]
    check("回転の基準距離はすぐ反映", abs(cmds.getAttr(sphere + ".rotateAxisX")
                                   - cs.rotation_offset(0.3 * n[2], 0.2 * n[3], 1.0)[0]) < 1e-4)
    pos(loc, 7)
    check("ロケータもぶれる", (axes(wm(loc, 7))[3] - axes(still(rl, loc, 7))[3]).length() > 1e-4)

    # ぶらしているオブジェクトを選んで外す・ベイク
    frames = [1, 6, 14, 30]
    before = [wm(sphere, f) for f in frames]
    cmds.select(sphere)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=30)
    after = [wm(sphere, f) for f in frames]
    check("球のベイク: 見え方が同じ・rotateAxis は元に戻る",
          max(abs(a[i] - b[i]) for a, b in zip(before, after) for i in range(16)) < 1e-4
          and near(cmds.getAttr(sphere + ".rotateAxis")[0], (0, 0, 0)))
    cmds.undo()
    base = still(rl, loc, 9)
    cmds.select(loc)
    run_tool(cs.CameraShakeRemoveTool)
    check("ロケータを選んで「画ぶれを外す」: 元どおり", not cmds.objExists(rl)
          and max(abs(a - b) for a, b in zip(base, wm(loc, 9))) < 1e-9)

    # 画ぶれのルートを選んで押しても、ルート自身はぶらさない
    cmds.select(rs)
    try:
        run_tool(cs.CameraShakeTool)
        check("画ぶれのルートだけ選んだ時はぶらさない", False)
    except ToolError:
        check("画ぶれのルートだけ選んだ時はぶらさない", True)
    # 1 つでも付けられないものがあれば、何も作らない
    cube = cmds.polyCube(name="box")[0]
    cmds.setAttr(cube + ".rotatePivotTranslateX", lock=True)
    n_before = len(cmds.ls())
    cmds.select([cube, loc])
    try:
        run_tool(cs.CameraShakeTool)
        check("ロックされたものがあればエラー (何も作らない)", False)
    except ToolError:
        check("ロックされたものがあればエラー (何も作らない)", len(cmds.ls()) == n_before)


@section
def t25_hand_shake():
    from maya import mel
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, camera_shake as cs, hand_shake as hs
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=60)
    cmds.flushUndo()

    def wm(node, f):
        pos(node, f)
        return om.MMatrix(cmds.getAttr(node + ".worldMatrix[0]"))

    def axes(m):
        return (om.MVector(m[0], m[1], m[2]).normal(), om.MVector(m[4], m[5], m[6]).normal(),
                om.MVector(m[8], m[9], m[10]).normal(), om.MVector(m[12], m[13], m[14]))

    def offsets(root, node, frames):
        """強さ 0 の時との差 (オブジェクトの X / Y 軸方向の位置のずれ) と rotateAxis Z。"""
        keep = [cmds.getAttr(root + ".dfx_strength_" + a) for a in "xy"]
        out = []
        for f in frames:
            m = wm(node, f)
            ra_z = cmds.getAttr(node + ".rotateAxisZ")
            for a in "xy":
                cmds.setAttr(root + ".dfx_strength_" + a, 0)
            b = wm(node, f)
            for a, v in zip("xy", keep):
                cmds.setAttr(root + ".dfx_strength_" + a, v)
            bx, by, _, bp = axes(b)
            d = axes(m)[3] - bp
            out.append((d * bx, d * by, ra_z))
        return out

    cam = cmds.ls(cmds.camera()[0], long=True)[0]
    cmds.setAttr(cam + ".rotate", -10, 30, 0, type="double3")
    cmds.setKeyframe(cam, attribute="translateX", time=1, value=0)
    cmds.setKeyframe(cam, attribute="translateX", time=60, value=10)
    cmds.select(cam)
    root = run_tool(hs.HandShakeTool, strength_x=0.1, strength_y=0.06, roll=2.0, period=30, detail=0.0)
    check("手ブレ: ルート dfx_handShakeN ができ、キーは打たない", root.startswith("dfx_handShake")
          and not cmds.keyframe(root, query=True) and
          cmds.keyframe(cam + ".translateX", query=True, keyframeCount=True) == 2)
    frames = list(range(1, 61))
    o = offsets(root, cam, frames)
    xs, ys, rolls = [a for a, _, _ in o], [b for _, b, _ in o], [c for _, _, c in o]
    steps = [abs(b - a) for a, b in zip(xs, xs[1:])]
    check("手ブレ: 横・縦・傾きとも強さの範囲で揺れる",
          max(abs(x) for x in xs) <= 0.1 + 1e-6 and max(abs(y) for y in ys) <= 0.06 + 1e-6
          and max(abs(r) for r in rolls) <= 2.0 + 1e-6
          and max(abs(x) for x in xs) > 0.02 and max(abs(r) for r in rolls) > 0.3,
          "x %.3f y %.3f roll %.2f" % (max(abs(x) for x in xs), max(abs(y) for y in ys),
                                       max(abs(r) for r in rolls)))
    check("手ブレ: なめらか (1 フレームの変化は強さの 1/5 以下)", max(steps) < 0.1 / 5,
          "%.4f" % max(steps))
    check("手ブレ: 横と縦は別の揺れ", any(abs(a / 0.1 - b / 0.06) > 0.2 for a, b, _ in o))
    # パラメータはすぐ反映
    cmds.setAttr(root + ".dfx_period", 5)
    fast = offsets(root, cam, frames)
    fast_steps = [abs(b[0] - a[0]) for a, b in zip(fast, fast[1:])]
    check("周期を短くすると速く揺れる (すぐ反映)", sum(fast_steps) > 2.5 * sum(steps),
          "%.3f → %.3f" % (sum(steps), sum(fast_steps)))
    cmds.setAttr(root + ".dfx_period", 30)
    cmds.setAttr(root + ".dfx_detail", 1.0)
    detail = offsets(root, cam, frames)
    check("細かい揺れを増やすと細かくなる", sum(abs(b[0] - a[0]) for a, b in zip(detail, detail[1:]))
          > 1.3 * sum(steps))
    cmds.setAttr(root + ".dfx_detail", 0.0)
    cmds.setAttr(root + ".dfx_seed", 7)
    check("乱数シードで揺れ方が変わる", offsets(root, cam, [20])[0][0] != o[19][0])
    cmds.setAttr(root + ".dfx_seed", 1)
    cmds.setKeyframe(root, attribute="dfx_strength_x", time=1, value=0)
    cmds.setKeyframe(root, attribute="dfx_strength_x", time=60, value=0.2)
    # キーがあると強さを一時的に 0 にできないので、カメラの X 軸 (回転だけから計算) で測る
    cam_x = om.MEulerRotation(*[math.radians(a) for a in cmds.getAttr(cam + ".rotate")[0]]) \
        .asMatrix()
    cam_x = om.MVector(cam_x[0], cam_x[1], cam_x[2])
    ramp = []
    for f in (1, 30):
        pos(cam, f)
        ramp.append(om.MVector(*cmds.getAttr(cam + ".rotatePivotTranslate")[0]) * cam_x)
    check("強さにキー: だんだん強くなる", abs(ramp[0]) < 1e-9
          and abs(ramp[1] - o[29][0] / 0.1 * cmds.getAttr(root + ".dfx_strength_x", time=30)) < 1e-4,
          str((ramp, o[29][0])))
    cmds.cutKey(root, attribute="dfx_strength_x")
    cmds.setAttr(root + ".dfx_strength_x", 0.1)
    cmds.setAttr(root + ".dfx_rotation", 0)
    pos(cam, 20)
    check("回転のブレ OFF: 回転・傾きは無し", near(cmds.getAttr(cam + ".rotateAxis")[0], (0, 0, 0)))
    cmds.setAttr(root + ".dfx_rotation", 1)

    # 画ぶれと手ブレは 1 つのオブジェクトにどちらか 1 つ
    cmds.select(cam)
    try:
        run_tool(cs.CameraShakeTool)
        check("手ブレのあるカメラに画ぶれはエラー", False)
    except ToolError as e:
        check("手ブレのあるカメラに画ぶれはエラー", u"手ブレ" in u"%s" % e, u"%s" % e)

    # ベイク (カメラを選んで)
    bf = [1, 9, 22, 40, 60]
    before = [wm(cam, f) for f in bf]
    cmds.select(cam)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=60)
    after = [wm(cam, f) for f in bf]
    check("手ブレのベイク: 見え方が同じ (傾きも rotate に入る)",
          max(abs(a[i] - b[i]) for a, b in zip(before, after) for i in range(16)) < 1e-4
          and near(cmds.getAttr(cam + ".rotateAxis")[0], (0, 0, 0)))
    cmds.undo()

    # 注視点のあるカメラ: 傾きは lookAt の twist
    c2 = cmds.camera()[0]
    mel.eval('cameraMakeNode 2 "%s"' % c2)
    c2 = cmds.ls(c2, long=True)[0]
    con = cs.find_aim_constraint(cmds, c2)
    aim = cs.find_aim(cmds, c2)
    cmds.setAttr(con + ".twist", 3.0)
    cmds.setAttr(c2 + ".translate", 2, 1, 8, type="double3")
    cmds.select(c2)
    r2 = run_tool(hs.HandShakeTool, roll=1.5)
    tw = [cmds.getAttr(con + ".twist", time=f) for f in range(1, 61, 3)]
    check("注視点あり: 傾きは lookAt の twist (元の値 3 を中心に ±1.5)",
          all(abs(t - 3.0) <= 1.5 + 1e-6 for t in tw) and max(tw) - min(tw) > 0.3, str(tw[:4]))
    cmds.setAttr(r2 + ".dfx_rotation", 0)
    pos(c2, 17)
    check("注視点あり・回転 OFF: 注視点はカメラと同じだけ動き、傾きなし",
          near(cmds.getAttr(aim + ".rotatePivotTranslate")[0], cmds.getAttr(c2 + ".rotatePivotTranslate")[0])
          and abs(cmds.getAttr(con + ".twist") - 3.0) < 1e-9)
    cmds.setAttr(r2 + ".dfx_rotation", 1)
    before = [wm(c2, f) for f in bf]
    cmds.select(r2)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=60)
    after = [wm(c2, f) for f in bf]
    check("注視点あり: ベイクしても見え方が同じ (twist にキー)",
          max(abs(a[i] - b[i]) for a, b in zip(before, after) for i in range(16)) < 1e-4
          and cmds.keyframe(con + ".twist", query=True, keyframeCount=True) == 60)
    cmds.undo()

    # 球にも付けられる・外す・プリセット
    ball = cmds.ls(cmds.polySphere(name="hsBall")[0], long=True)[0]
    cmds.select(ball)
    r3 = run_tool(hs.HandShakeTool, strength_x=0.2)
    check("球にも付けられる", (axes(wm(ball, 13))[3] - om.MVector(0, 0, 0)).length() > 1e-4)
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        cmds.setAttr(r3 + ".dfx_period", 12)
        dialog.answer = os.path.join(tmp, "hand")
        cmds.select(ball)
        run_tool(_preset.PresetSaveTool)
        cmds.select(r2)
        dialog.answer = os.path.join(tmp, "hand.json")
        run_tool(_preset.PresetLoadTool)
        check("プリセット: 保存・読み込み (球の手ブレ → カメラの手ブレ)",
              mu.read_param_values(r2, hs.stored_params()) == mu.read_param_values(r3, hs.stored_params())
              and "hand_shake" in dialog.calls[0]["startingDirectory"])
        from dummy_effect_maker.tools import camera_shake as _cs
        cube = cmds.polyCube(name="csCube")[0]
        cmds.select(cube)
        cs_root = run_tool(_cs.CameraShakeTool)
        cmds.select(cs_root)
        try:
            run_tool(_preset.PresetLoadTool)
            check("手ブレのプリセットは画ぶれに読み込めない", False)
        except ToolError:
            check("手ブレのプリセットは画ぶれに読み込めない", True)
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var
    cmds.select(ball)
    run_tool(hs.HandShakeRemoveTool)
    check("球を選んで「手ブレを外す」: 元どおり", not cmds.objExists(r3)
          and near(cmds.getAttr(ball + ".rotatePivotTranslate")[0], (0, 0, 0))
          and near(cmds.getAttr(ball + ".rotateAxis")[0], (0, 0, 0)))
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: twist も元の値に戻る", not cmds.ls("dfx_handShake*")
          and abs(cmds.getAttr(con + ".twist") - 3.0) < 1e-9
          and near(cmds.getAttr(cam + ".rotateAxis")[0], (0, 0, 0)))
    new_scene()
    c4 = cmds.camera()[0]
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    cmds.select(c4)
    run_tool(hs.HandShakeTool)
    cmds.undo()
    check("アンドゥ 1 回で全て消え、カメラも元どおり", not (set(cmds.ls()) - before_nodes)
          and near(cmds.getAttr(c4 + ".rotateAxis")[0], (0, 0, 0)))


@section
def t26_camera_shake_z():
    from maya import mel
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, camera_shake as cs
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=30)

    def wm(node, f):
        pos(node, f)
        return om.MMatrix(cmds.getAttr(node + ".worldMatrix[0]"))

    def axes(m):
        return (om.MVector(m[0], m[1], m[2]).normal(), om.MVector(m[4], m[5], m[6]).normal(),
                om.MVector(m[8], m[9], m[10]).normal(), om.MVector(m[12], m[13], m[14]))

    def still(root, node, f):
        keep = [cmds.getAttr(root + ".dfx_strength_" + a) for a in "xyz"]
        for a in "xyz":
            cmds.setAttr(root + ".dfx_strength_" + a, 0)
        m = wm(node, f)
        for a, v in zip("xyz", keep):
            cmds.setAttr(root + ".dfx_strength_" + a, v)
        return m

    # 既定は Z 0 (前と同じぶれ方)。X / Y の乱数は Z を追加する前と同じ
    keys = cs.shake_keys(1, 1, 30, lambda t: 2.0, 0.5, 0.5)
    check("Z を追加: キーは 5 チャンネル (移動 X / Y・回転 X / Y・移動 Z)", all(len(v) == 5 for _, v in keys))
    cam = cmds.ls(cmds.camera(centerOfInterest=8)[0], long=True)[0]
    cmds.setAttr(cam + ".rotate", -15, 40, 5, type="double3")
    cmds.select(cam)
    root = run_tool(cs.CameraShakeTool)
    check("Z (奥行き) の強さ: 既定 0・キーを打てる", cmds.getAttr(root + ".dfx_strength_z") == 0
          and cmds.getAttr(root + ".dfx_strength_z", keyable=True))
    d = axes(wm(cam, 9))[3] - axes(still(root, cam, 9))[3]
    check("Z 0 ならカメラの前後には動かない", abs(d * axes(still(root, cam, 9))[2]) < 1e-9)

    cmds.setAttr(root + ".dfx_strength_z", 0.5)
    bad = []
    for f in (1, 4, 9, 17, 26):
        m = wm(cam, f)
        b = still(root, cam, f)
        bx, by, bz, bp = axes(b)
        dd = axes(m)[3] - bp
        n = [cmds.getAttr(root + "." + a, time=f) for a in cs.NOISE_ATTRS]
        if (abs(dd * bz - 0.5 * n[4]) > 1e-4 or abs(dd * bx - 0.2 * n[0]) > 1e-4
                or abs(dd * by - 0.2 * n[1]) > 1e-4):
            bad.append((f, dd * bz, 0.5 * n[4]))
    check("注視点なし: Z の強さ × 乱数だけ、カメラの前後 (Z 軸) にぶれる (すぐ反映)", not bad, str(bad[:2]))
    zs = [cmds.getAttr(root + ".dfx_noise_tz", time=f) for f in range(1, 31)]
    xs = [cmds.getAttr(root + ".dfx_noise_tx", time=f) for f in range(1, 31)]
    check("Z は X / Y とは別の揺れ", any(abs(a - b) > 0.2 for a, b in zip(zs, xs)))
    frames = [1, 7, 15, 30]
    before = [wm(cam, f) for f in frames]
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=30)
    after = [wm(cam, f) for f in frames]
    check("Z 込みでベイクしても見え方が同じ",
          max(abs(a[i] - b[i]) for a, b in zip(before, after) for i in range(16)) < 1e-4)
    cmds.undo()

    # 注視点のあるカメラ
    c2 = cmds.camera()[0]
    mel.eval('cameraMakeNode 2 "%s"' % c2)
    c2 = cmds.ls(c2, long=True)[0]
    aim = cs.find_aim(cmds, c2)
    cmds.setAttr(c2 + ".translate", 4, 2, 9, type="double3")
    cmds.select(c2)
    r2 = run_tool(cs.CameraShakeTool, rotation=False, strength_z=0.6)
    bad = []
    for f in (2, 8, 19):
        m = wm(c2, f)
        b = still(r2, c2, f)
        bx, by, bz, bp = axes(b)
        dd = axes(m)[3] - bp
        n = [cmds.getAttr(r2 + "." + a, time=f) for a in cs.NOISE_ATTRS]
        if abs(dd * bz - 0.6 * n[4]) > 1e-4 or (axes(m)[2] - bz).length() > 1e-6:
            bad.append((f, dd * bz, 0.6 * n[4], (axes(m)[2] - bz).length()))
    check("注視点あり・回転 OFF: 前後にもぶれ、注視点も同じだけ動くので向きは変わらない", not bad, str(bad[:2]))
    cmds.setAttr(r2 + ".dfx_rotation", 1)
    cmds.setAttr(r2 + ".dfx_strength_x", 0)
    cmds.setAttr(r2 + ".dfx_strength_y", 0)
    pos(c2, 8)
    a_off = om.MVector(*cmds.getAttr(aim + ".rotatePivotTranslate")[0])
    check("注視点あり・回転 ON: Z のブレでは注視点は動かない (カメラだけ前後)", a_off.length() < 1e-9
          and om.MVector(*cmds.getAttr(c2 + ".rotatePivotTranslate")[0]).length() > 1e-4)
    cmds.select(cmds.polySphere()[0])
    rs = run_tool(cs.CameraShakeTool, strength_x=0, strength_y=0, strength_z=0.3)
    sp = cmds.listConnections(rs + ".dfx_camera")[0]
    pos(sp, 5)
    rt = cmds.getAttr(sp + ".rotatePivotTranslate")[0]
    check("球: Z の強さで球の Z 軸方向にぶれる", abs(rt[0]) < 1e-9 and abs(rt[1]) < 1e-9
          and abs(abs(rt[2]) - 0.3 * abs(cmds.getAttr(rs + ".dfx_noise_tz"))) < 1e-6)

    # 古い画ぶれ (Z の無いもの) も更新で Z が使えるようになる
    cmds.deleteAttr(rs, attribute="dfx_strength_z")
    cmds.deleteAttr(rs, attribute="dfx_noise_tz")
    cmds.select(rs)
    run_tool(cs.CameraShakeUpdateTool)
    check("古い画ぶれ: 更新で Z (奥行き) の強さとキーが追加される",
          cmds.attributeQuery("dfx_strength_z", node=rs, exists=True)
          and cmds.keyframe(rs + ".dfx_noise_tz", query=True, keyframeCount=True) > 3)


@section
def t27_hand_shake_z():
    from maya import mel
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, camera_shake as cs, hand_shake as hs
    new_scene()
    cmds.playbackOptions(minTime=1, maxTime=60)

    def wm(node, f):
        pos(node, f)
        return om.MMatrix(cmds.getAttr(node + ".worldMatrix[0]"))

    def axes(m):
        return (om.MVector(m[0], m[1], m[2]).normal(), om.MVector(m[4], m[5], m[6]).normal(),
                om.MVector(m[8], m[9], m[10]).normal(), om.MVector(m[12], m[13], m[14]))

    def z_offsets(root, node, frames):
        out = []
        for f in frames:
            m = wm(node, f)
            keep = [cmds.getAttr(root + ".dfx_strength_" + a) for a in "xyz"]
            for a in "xyz":
                cmds.setAttr(root + ".dfx_strength_" + a, 0)
            b = wm(node, f)
            for a, v in zip("xyz", keep):
                cmds.setAttr(root + ".dfx_strength_" + a, v)
            bx, by, bz, bp = axes(b)
            d = axes(m)[3] - bp
            out.append((d * bx, d * by, d * bz, (axes(m)[2] - bz).length()))
        return out

    cam = cmds.ls(cmds.camera()[0], long=True)[0]
    cmds.setAttr(cam + ".rotate", -20, 35, 0, type="double3")
    cmds.select(cam)
    root = run_tool(hs.HandShakeTool, strength_x=0.0, strength_y=0.0)
    check("手ブレ Z: 既定 0・キーを打てる", cmds.getAttr(root + ".dfx_strength_z") == 0
          and cmds.getAttr(root + ".dfx_strength_z", keyable=True))
    check("Z 0 なら前後には揺れない", all(abs(o[2]) < 1e-9 for o in z_offsets(root, cam, [5, 30])))
    cmds.setAttr(root + ".dfx_strength_z", 0.3)
    frames = list(range(1, 61))
    o = z_offsets(root, cam, frames)
    zs = [z for _, _, z, _ in o]
    check("手ブレ Z: カメラの前後に、強さの範囲でなめらかに揺れる (すぐ反映)",
          max(abs(z) for z in zs) <= 0.3 + 1e-6 and max(abs(z) for z in zs) > 0.05
          and max(abs(b - a) for a, b in zip(zs, zs[1:])) < 0.3 / 4
          and all(abs(x) < 1e-9 and abs(y) < 1e-9 for x, y, _, _ in o),
          "最大 %.3f" % max(abs(z) for z in zs))
    cmds.setAttr(root + ".dfx_strength_x", 0.3)
    xs = [x for x, _, _, _ in z_offsets(root, cam, frames)]
    check("Z は X とは別の揺れ", any(abs(a - b) > 0.05 for a, b in zip(xs, zs)))
    before = [wm(cam, f) for f in (1, 20, 45)]
    cmds.select(cam)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=60)
    after = [wm(cam, f) for f in (1, 20, 45)]
    check("手ブレ Z 込みでベイクしても見え方が同じ",
          max(abs(a[i] - b[i]) for a, b in zip(before, after) for i in range(16)) < 1e-4)
    cmds.undo()

    # 注視点のあるカメラ・回転 OFF: 注視点も一緒に前後するので向きは変わらない
    c2 = cmds.camera()[0]
    mel.eval('cameraMakeNode 2 "%s"' % c2)
    c2 = cmds.ls(c2, long=True)[0]
    cmds.setAttr(c2 + ".translate", 3, 1, 9, type="double3")
    cmds.select(c2)
    r2 = run_tool(hs.HandShakeTool, rotation=False, strength_z=0.4)
    o2 = z_offsets(r2, c2, [3, 17, 38])
    check("注視点あり・回転 OFF: 前後にも揺れ、向きは変わらない",
          max(abs(z) for _, _, z, _ in o2) > 0.02 and max(t for _, _, _, t in o2) < 1e-6, str(o2))

    # 古い手ブレ (Z の無いもの) も更新で Z が使えるようになる
    cmds.deleteAttr(r2, attribute="dfx_strength_z")
    cmds.select(r2)
    run_tool(hs.HandShakeUpdateTool)
    check("古い手ブレ: 更新で Z (奥行き) の強さが追加される (既定 0)",
          cmds.attributeQuery("dfx_strength_z", node=r2, exists=True)
          and cmds.getAttr(r2 + ".dfx_strength_z") == 0)


@section
def t28_shockwave():
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, shockwave as sw
    new_scene()
    cmds.flushUndo()
    LOGS[:] = []
    root = run_tool(sw.ShockwaveTool, count=2, interval=3.0)
    center = cmds.ls(root + "_center", long=True)[0]
    edge = cmds.ls(root + "_edge", long=True)[0]
    rings = [cmds.ls("%s_ring_%02d" % (root, i), long=True)[0] for i in (1, 2)]
    check("衝撃波: ロケータはルートの子、輪は中心のロケータの子",
          center.split("|")[-2] == root and edge.split("|")[-2] == root
          and all(r.split("|")[-2] == root + "_center" for r in rings))
    check("ログ: 端まで何フレームで広がるか", any(u"フレームで広がる" in m for m in LOGS), str(LOGS[-2:]))
    v = sw.ShockwaveTool.default_values()
    v.update(count=2, interval=3.0)

    def measure(ring):
        """中心のロケータから見た (半径, チューブの太さ, 縦の太さ, 面からのずれ)。"""
        inv = om.MMatrix(cmds.getAttr(center + ".worldInverseMatrix[0]"))
        pts = cmds.xform(ring + ".vtx[*]", query=True, worldSpace=True, translation=True)
        local = [om.MPoint(pts[i], pts[i + 1], pts[i + 2]) * inv for i in range(0, len(pts), 3)]
        d = [math.hypot(p.x, p.z) for p in local]
        return (max(d) + min(d)) / 2, (max(d) - min(d)) / 2, max(abs(p.y) for p in local)

    def check_rings(name, values, distance, frames):
        bad = []
        for f in frames:
            pos(rings[0], f)
            for i, ring in enumerate(rings):
                r, w, vis = sw.ring_state(f - sw.ring_start(values, i), values, distance)
                if bool(cmds.getAttr(ring + ".visibility")) != vis:
                    bad.append((f, i, "vis", vis))
                    continue
                if not vis or r < 1e-3:
                    continue
                mr, mw, my = measure(ring)
                tube = sw.actual_tube(r, w)
                # 縦の太さはチューブの断面の頂点の一番上 (分割が少ないと sin(角度) < 1)
                sub_y = max(4, int(cmds.getAttr(root + ".dfx_subdivisions")) // 4)
                top = max(abs(math.sin(2 * math.pi * j / sub_y)) for j in range(sub_y))
                if (abs(mr - r) > 1e-3 or abs(mw - tube) > 1e-3
                        or abs(my - tube * values["flatten"] * top) > 1e-3):
                    bad.append((f, i, (mr, mw, my), (r, tube)))
        check(name, not bad, str(bad[:2]))

    frames = [0, 1, 2, 3, 5, 8, 11, 14, 17, 20, 25]
    check_rings("広がり方: 初速と減衰率どおり、端 (10) で止まって細くなって消える (2 つ目は 3 フレーム後)",
                v, 10.0, frames)
    pos(rings[0], 14)
    check("端に届いた輪の半径は端までの距離", abs(measure(rings[0])[0] - 10.0) < 1e-3)

    # すぐ反映
    for name, val in (("speed", 3.0), ("decay", 0.25), ("thickness", 0.8), ("thickness_end", 1.0),
                      ("flatten", 0.3), ("fade_frames", 3.0), ("start_frame", 4.0), ("interval", 6.0)):
        cmds.setAttr(root + ".dfx_" + name, val)
        v[name] = val
    check_rings("即反映: 初速・減衰率・太さ・平たさ・消えるフレーム数・開始・間隔", v, 10.0,
                [4, 5, 7, 10, 13, 16, 20, 24])
    cmds.setAttr(edge + ".translate", 0, 3, 4, type="double3")   # 距離 5
    check_rings("端のロケータを動かすと広がる大きさが変わる", v, 5.0, [5, 8, 12, 18])
    cmds.setAttr(center + ".translate", 2, 1, -3, type="double3")
    cmds.setAttr(center + ".rotate", 90, 0, 0, type="double3")
    cmds.setAttr(edge + ".translate", 2, 1, 7, type="double3")   # 中心から 10
    check_rings("中心のロケータを動かす・回転させると輪の位置と向きが変わる", v, 10.0, [6, 10, 15])
    pos(rings[0], 10)
    m = om.MMatrix(cmds.getAttr(rings[0] + ".worldMatrix[0]"))
    normal = om.MVector(m[4], m[5], m[6]).normal()
    check("輪の面は中心のロケータの XZ 面 (90° 回すと縦になる)", abs(abs(normal.z) - 1) < 1e-6, str(normal))

    # 減衰が強すぎて届かない
    LOGS[:] = []
    cmds.setAttr(root + ".dfx_decay", 0.6)
    v["decay"] = 0.6
    target, t_reach, ok = sw.reach(10.0, 3.0, 0.6)
    check_rings("届かない時: 最大の半径の 99% まで広がって消える", v, 10.0, [5, 6, 8, 10, 14, 20])
    check("届かない時は届く所まで (計算)", not ok and abs(target - 0.99 * 3.0 / -math.log(0.4)) < 1e-9)
    cmds.select(root)
    run_tool(sw.ShockwaveUpdateTool)
    check("届かない時はログで知らせる", any(u"届きません" in m for m in LOGS), str(LOGS[-2:]))
    cmds.setAttr(root + ".dfx_decay", 0.25)
    v["decay"] = 0.25

    # 更新: 輪の数・分割数
    cmds.setAttr(root + ".dfx_count", 3)
    cmds.setAttr(root + ".dfx_subdivisions", 24)
    cmds.select(edge)  # ロケータを選んでもよい
    run_tool(sw.ShockwaveUpdateTool)
    rings = [cmds.ls("%s_ring_%02d" % (root, i), long=True)[0] for i in (1, 2, 3)]
    check("更新: 輪 3 個・分割数 24、ロケータの位置はそのまま",
          cmds.polyEvaluate(rings[0], vertex=True) == 24 * 6
          and near(cmds.getAttr(center + ".translate")[0], (2, 1, -3)))
    v["count"] = 3
    check_rings("更新後も正しく広がる", v, 10.0, [4, 9, 16, 22])
    n1 = len(cmds.ls())
    run_tool(sw.ShockwaveUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # ベイク: ブレンドシェイプは残して、ウェイトと大きさ・表示をキーに
    shapes = {}
    for f in (4, 7, 11, 16, 21):
        pos(rings[0], f)
        shapes[f] = [measure(r) for r in rings]
        shapes[f] = [(s, cmds.getAttr(r + ".visibility")) for s, r in zip(shapes[f], rings)]
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=30)
    same = True
    for f, before in shapes.items():
        pos(rings[0], f)
        for (s, vis), r in zip(before, rings):
            if vis != cmds.getAttr(r + ".visibility"):
                same = False
            elif vis and max(abs(a - b) for a, b in zip(s, measure(r))) > 1e-3:
                same = False
    bs = cmds.ls(type="blendShape")
    check("ベイク: 輪の形 (半径・太さ・表示) が同じ", same)
    check("ベイク: ブレンドシェイプは残り、ウェイトは毎フレームのキー・エクスプレッションは消える",
          len(bs) == 3 and cmds.keyframe(bs[0] + ".weight[0]", query=True, keyframeCount=True) == 30
          and not cmds.ls(type=["expression", "multMatrix", "decomposeMatrix"]))
    cmds.undo()

    # プリセット・アンドゥ・全削除
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        dialog.answer = os.path.join(tmp, "shock")
        cmds.select(root)
        run_tool(_preset.PresetSaveTool)
        r2 = run_tool(sw.ShockwaveTool)
        dialog.answer = os.path.join(tmp, "shock.json")
        cmds.select(r2)
        run_tool(_preset.PresetLoadTool)
        check("プリセット: 保存・読み込み", mu.read_param_values(r2, sw.stored_params())
              == mu.read_param_values(root, sw.stored_params())
              and len(cmds.ls(r2 + "_ring_*", type="transform")) == 3
              and "shockwave" in dialog.calls[0]["startingDirectory"])
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var
    new_scene()
    cmds.flushUndo()
    before_nodes = set(cmds.ls())
    run_tool(sw.ShockwaveTool, count=2)
    cmds.undo()
    check("アンドゥ 1 回で全て消える", not (set(cmds.ls()) - before_nodes))
    run_tool(sw.ShockwaveTool)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: ロケータ・輪・ブレンドシェイプごと消える", not cmds.ls("dfx_shockwave*")
          and not cmds.ls(type="blendShape") and not mu.list_tagged())


@section
def t38_straight_colors():
    """半透明のマテリアルの色はそのまま (ビューポート・Hardware 2.0・Arnold の重ね方)。"""
    from dummy_effect_maker.tools import ghost_blur as gb, shockwave as sw, speed_lines as sl
    new_scene()
    root = run_tool(sw.ShockwaveTool, color=(1.0, 0.5, 0.0), opacity=0.4)
    check("衝撃波: 色はそのまま、不透明度は透明度に",
          near(cmds.getAttr(root + "_mat.outColor")[0], (1.0, 0.5, 0.0))
          and near(cmds.getAttr(root + "_mat.outTransparency")[0], (0.6, 0.6, 0.6)))
    cmds.setAttr(root + ".dfx_opacity", 1.0)
    check("衝撃波: 不透明度を変えるとすぐ反映", near(cmds.getAttr(root + "_mat.outTransparency")[0], (0, 0, 0)))
    cam = cmds.camera()[0]
    cmds.select(cam)
    sp = run_tool(sl.SpeedLinesTool, color=(0.0, 1.0, 1.0), opacity=0.5)
    check("スピードライン: 色はそのまま", near(cmds.getAttr(sp + "_mat.outColor")[0], (0.0, 1.0, 1.0)))
    ball = cmds.polySphere()[0]
    cmds.setKeyframe(ball, attribute="translateX", time=1, value=0)
    cmds.setKeyframe(ball, attribute="translateX", time=11, value=10)
    cmds.select(ball)
    tr = run_tool(gb.TrailTool, color_start=(1.0, 1.0, 1.0), opacity_start=0.8,
                  color_end=(0.0, 0.0, 1.0), opacity_end=0.5)
    ramp = cmds.ls(tr + "_colorRamp")[0]
    check("軌跡: ランプの色もそのまま",
          near(cmds.getAttr(ramp + ".colorEntryList[0].color")[0], (1.0, 1.0, 1.0))
          and near(cmds.getAttr(ramp + ".colorEntryList[1].color")[0], (0.0, 0.0, 1.0)))


def _errors_of(fn):
    try:
        fn()
    except ToolError as e:
        return u"%s" % e
    return None


def _animated_ball(name="ball", hold=True):
    """曲がって動き、最後は止まる球 (回転もする)。"""
    ball = cmds.polySphere(name=name, subdivisionsAxis=12, subdivisionsHeight=8)[0]
    for t, x, y in ((1, 0, 0), (11, 10, 3), (21, 10, 10)):
        cmds.setKeyframe(ball, attribute="translateX", time=t, value=x)
        cmds.setKeyframe(ball, attribute="translateY", time=t, value=y)
    cmds.setKeyframe(ball, attribute="rotateZ", time=1, value=0)
    cmds.setKeyframe(ball, attribute="rotateZ", time=21, value=200)
    return cmds.ls(ball, long=True)[0]


def _mat_diff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


@section
def t29_ghost_afterimage():
    from dummy_effect_maker.tools import bake, ghost_blur as gb
    new_scene()
    cmds.flushUndo()
    ball = _animated_ball()
    check("エラー: 何も選ばずに残像", _errors_of(lambda: (cmds.select(clear=True),
                                                         run_tool(gb.AfterimageTool))) is not None)
    loc = cmds.spaceLocator(name="justLoc")[0]
    cmds.select(loc)
    err = _errors_of(lambda: run_tool(gb.AfterimageTool))
    check("エラー: メッシュの無いもの (ロケータ) には残像を付けられない", err and u"メッシュ" in err, err)
    cmds.select(ball)
    root = run_tool(gb.AfterimageTool, count=4, interval=1.5)
    check("作成: dfx_afterimage1 (残像 4 体)", root == "dfx_afterimage1"
          and len(cmds.ls(root + "_ghost_0?", type="transform")) == 4)
    v = gb.AfterimageTool.default_values()
    v.update(count=4, interval=1.5)

    def ghost_part(i, part="ball"):
        return cmds.ls("%s_ghost_%02d_%s" % (root, i + 1, part), long=True)[0]

    def expected(i, f, values):
        dt = values["interval"] * (i + 1)
        now = cmds.getAttr(ball + ".worldMatrix[0]", time=f)
        was = cmds.getAttr(ball + ".worldMatrix[0]", time=f - dt)
        spd = math.sqrt(sum((now[12 + a] - was[12 + a]) ** 2 for a in range(3))) / dt
        a = gb.ghost_ratio(i, values["count"])
        op = gb.lerp(values["opacity_start"], values["opacity_end"], a) * \
            gb.speed_factor(spd, values["min_speed"], values["full_speed"])
        return was, op, 1.0 + (values["scale_end"] - 1.0) * a

    def check_ghosts(name, values, frames, part="ball", target=None):
        target = target or ball
        bad = []
        for f in frames:
            pos(ball, f)
            for i in range(values["count"]):
                was, op, sc = expected(i, f, values)
                if target != ball:
                    was = cmds.getAttr(target + ".worldMatrix[0]", time=f - values["interval"] * (i + 1))
                g = cmds.ls("%s_ghost_%02d" % (root, i + 1), long=True)[0]
                m = cmds.getAttr(ghost_part(i, part) + ".worldMatrix[0]")
                scaled = [x * sc for x in was[:12]] + list(was[12:])
                for r in (3, 7, 11):
                    scaled[r] = was[r]
                tr = cmds.getAttr("%s_ghost_%02d_mat.outTransparencyR" % (root, i + 1))
                if (_mat_diff(m, scaled) > 1e-4 or abs(tr - (1 - op)) > 1e-4
                        or bool(cmds.getAttr(g + ".visibility")) != (op > 0.001)):
                    bad.append((f, i, _mat_diff(m, scaled), tr, 1 - op))
        check(name, not bad, str(bad[:2]))

    check_ghosts("残像: 間隔ごとに前のフレームの位置・向き、後ろほど薄く、速さで濃さが変わる",
                 v, [2, 3, 6, 9, 14, 18, 22, 26])
    pos(ball, 30)
    check("止まっていると残像は出ない",
          not any(cmds.getAttr("%s_ghost_%02d.visibility" % (root, i)) for i in range(1, 5)))
    for name, val in (("interval", 0.5), ("opacity_start", 1.0), ("opacity_end", 0.5),
                      ("scale_end", 0.4), ("min_speed", 0.2), ("full_speed", 0.6)):
        cmds.setAttr(root + ".dfx_" + name, val)
        v[name] = val
    check_ghosts("即反映: 間隔・不透明度・大きさ・速さのしきい値", v, [3, 7, 12, 19])
    cmds.setAttr(root + ".dfx_color_end", 1, 0, 0, type="float3")
    pos(ball, 5)
    op = 1 - cmds.getAttr("%s_ghost_04_mat.outTransparencyR" % root)
    check("即反映: 色 (最後の残像は最後の色)",
          op > 0.1 and near(cmds.getAttr("%s_ghost_04_mat.outColor" % root)[0], (1, 0, 0)), str(op))
    cmds.setKeyframe(root, attribute="dfx_opacity_start", time=1, value=0.0)
    cmds.setKeyframe(root, attribute="dfx_opacity_start", time=11, value=1.0)
    pos(ball, 3)
    a = cmds.getAttr("%s_ghost_01_mat.outTransparencyR" % root)
    pos(ball, 9)
    b = cmds.getAttr("%s_ghost_01_mat.outTransparencyR" % root)
    check("不透明度にキーを打てば、だんだん濃くできる", a > b, "%s %s" % (a, b))
    cmds.cutKey(root, attribute="dfx_opacity_start")
    cmds.setAttr(root + ".dfx_opacity_start", 1.0)

    # 形は元のメッシュとつながっている
    hist = cmds.ls(cmds.listHistory(ball), type="polySphere")[0]
    cmds.setAttr(hist + ".subdivisionsAxis", 16)
    check("残像の形は元のメッシュに合わせて変わる",
          cmds.polyEvaluate(ghost_part(0), vertex=True) == cmds.polyEvaluate(ball, vertex=True))

    # グループ (メッシュ 2 つ) に付ける
    grp = cmds.group(cmds.polyCube(name="blade")[0], cmds.polyCube(name="handle")[0], name="sword")
    cmds.setAttr("handle.translateY", -1.5)
    cmds.setKeyframe(grp, attribute="translateZ", time=1, value=0)
    cmds.setKeyframe(grp, attribute="translateZ", time=11, value=-12)
    cmds.setKeyframe(grp, attribute="rotateX", time=1, value=0)
    cmds.setKeyframe(grp, attribute="rotateX", time=11, value=120)
    cmds.select(grp)
    r2 = run_tool(gb.AfterimageTool, count=3)
    parts = sorted(c.split("|")[-1] for c in cmds.listRelatives(r2 + "_ghost_01", children=True))
    bad = []
    for f in (4, 8):
        pos(ball, f)
        for i in range(3):
            for part in ("blade", "handle"):
                m = cmds.getAttr("%s_ghost_%02d_%s.worldMatrix[0]" % (r2, i + 1, part))
                was = cmds.getAttr(part + ".worldMatrix[0]", time=f - (i + 1))
                if _mat_diff(m, was) > 1e-4:
                    bad.append((f, i, part))
    check("グループ: 中のメッシュごとに残像 (それぞれの前の位置)",
          parts == [r2 + "_ghost_01_blade", r2 + "_ghost_01_handle"] and not bad, str(bad[:2]))

    # 更新 (付けたオブジェクトを選んでもよい)
    cmds.setAttr(root + ".dfx_count", 6)
    cmds.select(ball)
    run_tool(gb.GhostBlurUpdateTool)
    v["count"] = 6
    check("更新: 残像 6 体", len(cmds.ls(root + "_ghost_0?", type="transform")) == 6)
    check_ghosts("更新後も正しい", v, [4, 10])
    n1 = len(cmds.ls())
    run_tool(gb.GhostBlurUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # ベイク
    frames = [3, 7, 12, 18, 25]
    before = {}
    for f in frames:
        pos(ball, f)
        before[f] = [(cmds.getAttr(ghost_part(i) + ".worldMatrix[0]"),
                      cmds.getAttr("%s_ghost_%02d_mat.outTransparencyR" % (root, i + 1)),
                      cmds.getAttr("%s_ghost_%02d.visibility" % (root, i + 1))) for i in range(6)]
    cmds.select(ball)  # 付けたオブジェクトを選んでベイク
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=30)
    same = True
    for f in frames:
        pos(ball, f)
        for i, (m, tr, vis_) in enumerate(before[f]):
            if (_mat_diff(m, cmds.getAttr(ghost_part(i) + ".worldMatrix[0]")) > 1e-3
                    or abs(tr - cmds.getAttr("%s_ghost_%02d_mat.outTransparencyR" % (root, i + 1))) > 1e-4
                    or vis_ != cmds.getAttr("%s_ghost_%02d.visibility" % (root, i + 1))):
                same = False
    check("ベイク: 残像の位置・濃さ・表示が同じ", same)
    check("ベイク: エクスプレッションは消え、マテリアルの濃さもキー、メッシュは元から切り離す",
          not cmds.ls(cmds.listConnections(root + ".dfxOwned") or [], type="expression")
          and cmds.keyframe("%s_ghost_01_mat.outTransparencyR" % root, query=True, keyframeCount=True) == 30
          and not cmds.listConnections(cmds.listRelatives(ghost_part(0), shapes=True, fullPath=True)[0]
                                       + ".inMesh", source=True, destination=False)
          and cmds.objExists(ball))
    cmds.undo()

    # アンドゥ・全削除
    new_scene()
    cmds.flushUndo()
    ball = _animated_ball()
    before_nodes = set(cmds.ls())
    cmds.select(ball)
    run_tool(gb.AfterimageTool)
    cmds.undo()
    check("アンドゥ 1 回で全て消える", not (set(cmds.ls()) - before_nodes))
    cmds.select(ball)
    run_tool(gb.AfterimageTool)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 残像が消え、元のオブジェクトは残る", not cmds.ls("dfx_afterimage*")
          and not mu.list_tagged() and cmds.objExists(ball))


@section
def t30_ghost_smear():
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, ghost_blur as gb
    new_scene()
    cmds.flushUndo()
    ball = _animated_ball()
    cmds.select(ball)
    root = run_tool(gb.SmearTool)
    lattice = cmds.ls(root + "_lattice", long=True)[0]
    lat_shape = cmds.listRelatives(lattice, shapes=True, fullPath=True)[0]
    check("作成: ラティスはルートの子・非表示、元のオブジェクトにラティスが付く",
          lattice.split("|")[-2] == root and not cmds.getAttr(lattice + ".visibility")
          and cmds.ls(cmds.listHistory(ball), type="ffd"))
    v = gb.SmearTool.default_values()
    err = _errors_of(lambda: (cmds.select(ball), run_tool(gb.SmearTool)))
    check("エラー: スミアは 1 つのオブジェクトに 1 つまで", err and u"既に" in err, err)

    def expected_points(values, f):
        n = int(values["divisions"])
        size = cmds.getAttr(lattice + ".scale")[0]
        center = cmds.getAttr(lattice + ".translate")[0]
        obj = max(size) / (1 + 2 * gb.LATTICE_PADDING)
        k = int(values["samples"])
        length = max(0.0, values["frames"]) * max(0.0, values["amount"])
        dt = length / k
        mats = [cmds.getAttr(ball + ".worldMatrix[0]", time=f - s * dt) for s in range(k + 1)]
        inv = cmds.getAttr(ball + ".worldInverseMatrix[0]", time=f)
        return gb.smear_points(mats, inv, center, size, (n, n, n), values,
                               gb.smear_cap(values, obj))

    def check_points(name, values, frames):
        bad = []
        n = int(values["divisions"]) ** 3
        for f in frames:
            pos(ball, f)
            got = []
            for i in range(n):
                got.extend(cmds.getAttr("%s.controlPoints[%d]" % (lat_shape, i))[0])
            exp = expected_points(values, f)
            if len(got) != len(exp) or max(abs(a - b) for a, b in zip(got, exp)) > 1e-4:
                bad.append((f, max(abs(a - b) for a, b in zip(got, exp)) if len(got) == len(exp) else "len"))
        check(name, not bad, str(bad[:3]))

    check_points("スミア: ラティスの点 = 後ろほど前のフレームの位置 (計算どおり)", v, [3, 7, 12, 16, 22, 30])

    def radii(f):
        pos(ball, f)
        c = cmds.xform(ball, q=True, ws=True, t=True)
        pts = cmds.xform(ball + ".vtx[*]", q=True, ws=True, t=True)
        return [dist(c, pts[i:i + 3]) for i in range(0, len(pts), 3)]

    r = radii(30)
    check("止まっていると元の形", max(abs(x - 1) for x in r) < 1e-4, str((min(r), max(r))))
    r = radii(8)
    check("動いていると後ろへ伸びる", max(r) > 2.5, str(max(r)))
    for name, val in (("frames", 5.0), ("amount", 0.6), ("start", 0.5), ("falloff", 2.0),
                      ("samples", 9)):
        cmds.setAttr(root + ".dfx_" + name, val)
        v[name] = val
    check_points("即反映: 伸びるフレーム数・強さ・伸び始める位置・カーブ・なめらかさ", v, [5, 9, 15])
    cmds.setAttr(root + ".dfx_amount", 0.0)
    r = radii(8)
    check("強さ 0 で元の形", max(abs(x - 1) for x in r) < 1e-4)
    cmds.setAttr(root + ".dfx_amount", 1.0)
    v["amount"] = 1.0

    # まっすぐ等速で動く球: 前はほぼそのまま、後ろが伸びる。上限で止まる
    lin = cmds.polySphere(name="lin")[0]
    cmds.setKeyframe(lin, attribute="translateX", time=1, value=0)
    cmds.setKeyframe(lin, attribute="translateX", time=21, value=20)
    cmds.keyTangent(lin, inTangentType="linear", outTangentType="linear")
    cmds.select(lin)
    r2 = run_tool(gb.SmearTool, frames=3.0, start=0.3)

    def extent(f):
        pos(lin, f)
        c = cmds.xform(lin, q=True, ws=True, t=True)
        xs = [p - c[0] for p in cmds.xform(lin + ".vtx[*]", q=True, ws=True, t=True)[0::3]]
        return max(xs), min(xs)
    front, back = extent(10)
    check("等速: 前はほぼそのまま・後ろは約 3 フレーム分 (3) 伸びる",
          abs(front - 1) < 0.02 and -4.2 < back < -3.5, "%s %s" % (front, back))
    cmds.setAttr(r2 + ".dfx_max_stretch", 0.5)
    front, back = extent(10)
    check("最大の伸び: 大きさ (2) × 0.5 = 1 までしか伸びない", back > -2.05, str(back))

    # 更新
    cmds.setAttr(root + ".dfx_divisions", 4)
    cmds.select(root)
    run_tool(gb.GhostBlurUpdateTool)
    lattice = cmds.ls(root + "_lattice", long=True)[0]
    lat_shape = cmds.listRelatives(lattice, shapes=True, fullPath=True)[0]
    v["divisions"] = 4
    check("更新: ラティスの分割数 4 (点 64 個)", cmds.getAttr(lat_shape + ".controlPoints", size=True) == 64
          and len(cmds.ls(cmds.listHistory(ball), type="ffd")) == 1)
    check_points("更新後も正しい", v, [6, 13])
    n1 = len(cmds.ls())
    run_tool(gb.GhostBlurUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # ベイク
    frames = [4, 8, 14, 20]
    before = {}
    for f in frames:
        pos(ball, f)
        before[f] = cmds.xform(ball + ".vtx[*]", q=True, ws=True, t=True)
    cmds.select(root)
    LOGS[:] = []
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=30)
    same = True
    for f in frames:
        pos(ball, f)
        if max(abs(a - b) for a, b in zip(before[f], cmds.xform(ball + ".vtx[*]", q=True, ws=True, t=True))) > 1e-3:
            same = False
    check("ベイク: 伸び方が同じ (ラティスの点のキー)", same)
    check("ベイク: エクスプレッションは消える・FBX の注意をログに出す",
          not cmds.ls(cmds.listConnections(root + ".dfxOwned") or [], type="expression")
          and cmds.keyframe(lat_shape + ".controlPoints[5].xValue", query=True, keyframeCount=True) == 30
          and any(u"Alembic" in m for m in LOGS), str(LOGS[-2:]))
    cmds.undo()

    # 削除すると元に戻る
    cmds.delete(root)
    r = radii(8)
    check("ルートを削除するとラティスも外れて元の形", max(abs(x - 1) for x in r) < 1e-4
          and not cmds.ls(cmds.listHistory(ball), type="ffd"))
    new_scene()
    cmds.flushUndo()
    ball = _animated_ball()
    before_nodes = set(cmds.ls())
    cmds.select(ball)
    run_tool(gb.SmearTool)
    cmds.undo()
    r = radii(8)
    check("アンドゥ 1 回で全て消え、元の形に戻る", not (set(cmds.ls()) - before_nodes)
          and max(abs(x - 1) for x in r) < 1e-4)
    cmds.select(ball)
    run_tool(gb.SmearTool)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    r = radii(8)
    check("全削除: スミアが外れて元の形", not cmds.ls("dfx_smear*") and not mu.list_tagged()
          and max(abs(x - 1) for x in r) < 1e-4)


@section
def t31_ghost_trail():
    from dummy_effect_maker.tools import bake, ghost_blur as gb
    new_scene()
    cmds.flushUndo()
    ball = _animated_ball()
    cmds.select(ball)
    root = run_tool(gb.TrailTool)
    a, b = [cmds.ls("%s_%s" % (root, s), long=True)[0] for s in "ab"]
    mesh = cmds.ls(root + "_trail", long=True)[0]
    pos(ball, 1)
    check("作成: 帯の両端のロケータは球の上下 (球に付いて動く)",
          near(pos(a, 1), (0, 1, 0)) and near(pos(b, 1), (0, -1, 0))
          and a.split("|")[-2] == root)
    pos(ball, 11)
    m = cmds.getAttr(ball + ".worldMatrix[0]")
    exp_a = (m[4] + m[12], m[5] + m[13], m[6] + m[14])
    check("ロケータは球と一緒に動く・回る", near(pos(a, 11), exp_a), str((pos(a, 11), exp_a)))
    v = gb.TrailTool.default_values()

    def rows(values, f):
        segs = values["segments"]
        dt = values["length"] / float(segs)
        sa = cmds.listRelatives(a, shapes=True, fullPath=True)[0]
        sb = cmds.listRelatives(b, shapes=True, fullPath=True)[0]
        out = []
        for j in range(segs + 1):
            pa = cmds.getAttr(sa + ".worldPosition[0]", time=f - j * dt)[0]
            pb = cmds.getAttr(sb + ".worldPosition[0]", time=f - j * dt)[0]
            out.append((pa, pb))
        return out

    def check_ribbon(name, values, frames):
        bad = []
        for f in frames:
            pos(ball, f)
            pts = cmds.xform(mesh + ".vtx[*]", q=True, ws=True, t=True)
            for j, (pa, pb) in enumerate(rows(values, f)):
                mid = [(x + y) / 2 for x, y in zip(pa, pb)]
                half = [(y - x) / 2 * gb.trail_taper(j, values["segments"], values["width_start"],
                                                     values["width_end"]) for x, y in zip(pa, pb)]
                top = [m_ + h for m_, h in zip(mid, half)]
                bottom = [m_ - h for m_, h in zip(mid, half)]
                if not (near(pts[6 * j:6 * j + 3], top) and near(pts[6 * j + 3:6 * j + 6], bottom)):
                    bad.append((f, j, pts[6 * j:6 * j + 3], top))
        check(name, not bad, str(bad[:2]))

    check_ribbon("帯: 行ごとに少し前の両端の点を結び、先ほど細くなる", v, [3, 8, 14, 19])
    pos(ball, 30)
    check("止まっていると帯は出ない", not cmds.getAttr(mesh + ".visibility"))
    pos(ball, 8)
    check("動いていると帯が出る", cmds.getAttr(mesh + ".visibility"))
    for name, val in (("length", 4.0), ("width_start", 0.5), ("width_end", 0.8)):
        cmds.setAttr(root + ".dfx_" + name, val)
        v[name] = val
    check_ribbon("即反映: 長さ・根元と先の太さ", v, [6, 15])
    cmds.setAttr(a + ".translate", 0, 2, 0.5, type="double3")
    check_ribbon("端のロケータを動かすと帯が変わる", v, [9])
    shader = cmds.listConnections(cmds.listConnections(cmds.listRelatives(mesh, shapes=True)[0],
                                                      type="shadingEngine")[0] + ".surfaceShader")[0]
    cmds.setAttr(root + ".dfx_opacity_start", 0.3)
    tramp = cmds.listConnections(shader + ".outTransparency")[0]
    check("即反映: 不透明度 (根元の透明度 = 1 - 0.3)",
          near(cmds.getAttr(tramp + ".colorEntryList[0].color")[0], (0.7, 0.7, 0.7)))

    # チューブ
    cmds.select(ball)
    r2 = run_tool(gb.TrailTool, shape=gb.SHAPE_TUBE, segments=16, sides=8)
    tube = cmds.ls(r2 + "_trail", long=True)[0]
    v2 = gb.TrailTool.default_values()
    v2.update(segments=16, sides=8)
    a2, b2 = [cmds.ls("%s_%s" % (r2, s), long=True)[0] for s in "ab"]
    a, b = a2, b2
    bad = []
    for f in (6, 12, 17):
        pos(ball, f)
        pts = cmds.xform(tube + ".vtx[*]", q=True, ws=True, t=True)
        rs = rows(v2, f)
        mids = [[(x + y) / 2 for x, y in zip(pa, pb)] for pa, pb in rs]
        for j, (pa, pb) in enumerate(rs):
            radius = dist(pa, pb) / 2 * gb.trail_taper(j, 16, 1.0, 0.0)
            tan = [p - q for p, q in zip(mids[max(0, j - 1)], mids[min(16, j + 1)])]
            tl = math.sqrt(sum(x * x for x in tan))
            for k in range(8):
                p = pts[(j * 8 + k) * 3:(j * 8 + k) * 3 + 3]
                off = [x - y for x, y in zip(p, mids[j])]
                if abs(dist(p, mids[j]) - radius) > 1e-3 or (
                        tl > 1e-6 and abs(sum(x * y for x, y in zip(off, tan)) / tl) > 1e-3):
                    bad.append((f, j, k))
    check("チューブ: 行ごとに少し前の中心を囲む輪 (直径 = 2 点の距離、進む向きに垂直)",
          not bad and cmds.polyEvaluate(tube, vertex=True) == 17 * 8, str(bad[:3]))
    worst = 0.0
    for f in (6, 12, 17):
        pos(ball, f)
        pts = cmds.xform(tube + ".vtx[*]", q=True, ws=True, t=True)
        rs = rows(v2, f)
        mids = [[(x + y) / 2 for x, y in zip(pa, pb)] for pa, pb in rs]
        for j in range(12):  # 先の方は細くて向きが不安定なので見ない
            for k in range(8):
                u0 = [x - y for x, y in zip(pts[(j * 8 + k) * 3:(j * 8 + k) * 3 + 3], mids[j])]
                u1 = [x - y for x, y in zip(pts[((j + 1) * 8 + k) * 3:((j + 1) * 8 + k) * 3 + 3], mids[j + 1])]
                l0, l1 = math.sqrt(sum(x * x for x in u0)), math.sqrt(sum(x * x for x in u1))
                if l0 > 1e-6 and l1 > 1e-6:
                    c = max(-1.0, min(1.0, sum(x * y for x, y in zip(u0, u1)) / (l0 * l1)))
                    worst = max(worst, math.degrees(math.acos(c)))
    check("チューブ: 球が回っても輪がねじれない (隣の輪の同じ頂点の向きの差が小さい)", worst < 30, str(worst))

    # 付けられる物: ロケータ (メッシュ無し) にも付けられる
    loc = cmds.spaceLocator(name="mover")[0]
    cmds.setKeyframe(loc, attribute="translateZ", time=1, value=0)
    cmds.setKeyframe(loc, attribute="translateZ", time=11, value=10)
    cmds.select(loc)
    r3 = run_tool(gb.TrailTool)
    check("ロケータにも軌跡を付けられる (幅 1 の帯)",
          near(pos("%s_a" % r3, 1), (0, 0.5, 0)) and near(pos("%s_b" % r3, 1), (0, -0.5, 0)))

    # 更新: 分割数を変えてもロケータの位置はそのまま
    a, b = [cmds.ls("%s_%s" % (root, s), long=True)[0] for s in "ab"]
    cmds.setAttr(root + ".dfx_segments", 10)
    cmds.select(root)
    run_tool(gb.GhostBlurUpdateTool)
    mesh = cmds.ls(root + "_trail", long=True)[0]
    v["segments"] = 10
    check("更新: 分割数 10 (頂点 22)、ロケータはそのまま",
          cmds.polyEvaluate(mesh, vertex=True) == 22
          and near(cmds.getAttr(a + ".translate")[0], (0, 2, 0.5)))
    check_ribbon("更新後も正しい", v, [7, 12])
    n1 = len(cmds.ls())
    run_tool(gb.GhostBlurUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # ベイク (ジョイントのキー、スキンは残る)
    frames = [3, 8, 13, 19, 26]
    before = {}
    for f in frames:
        pos(ball, f)
        before[f] = (cmds.xform(mesh + ".vtx[*]", q=True, ws=True, t=True), cmds.getAttr(mesh + ".visibility"))
    cmds.select(root)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=30)
    same = True
    for f in frames:
        pos(ball, f)
        pts = cmds.xform(mesh + ".vtx[*]", q=True, ws=True, t=True)
        if (max(abs(x - y) for x, y in zip(before[f][0], pts)) > 1e-3
                or before[f][1] != cmds.getAttr(mesh + ".visibility")):
            same = False
    joints = cmds.listRelatives(root + "_joints", children=True, fullPath=True)
    check("ベイク: 帯の形が同じ", same)
    check("ベイク: ジョイントのキーになり、スキンは残る (FBX 用)",
          cmds.ls(cmds.listHistory(mesh), type="skinCluster")
          and cmds.keyframe(joints[3] + ".translateX", query=True, keyframeCount=True) == 30
          and not cmds.ls(cmds.listConnections(root + ".dfxOwned") or [], type="expression"))
    cmds.undo()

    # アンドゥ・全削除
    new_scene()
    cmds.flushUndo()
    ball = _animated_ball()
    before_nodes = set(cmds.ls())
    cmds.select(ball)
    run_tool(gb.TrailTool, shape=gb.SHAPE_TUBE)
    cmds.undo()
    check("アンドゥ 1 回で全て消える", not (set(cmds.ls()) - before_nodes))
    cmds.select(ball)
    run_tool(gb.TrailTool)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 軌跡が消え、元のオブジェクトは残る", not cmds.ls("dfx_trail*")
          and not mu.list_tagged() and cmds.objExists(ball))


@section
def t32_ghost_common():
    from dummy_effect_maker.tools import ghost_blur as gb
    new_scene()
    cmds.flushUndo()
    ball = _animated_ball()
    other = _animated_ball("ball2")
    cmds.select(ball, other)
    roots = run_tool(gb.AfterimageTool)
    check("複数選ぶと 1 つずつ作る", isinstance(roots, list) and len(roots) == 2)
    cmds.select(ball)
    run_tool(gb.SmearTool)
    cmds.select(ball)
    run_tool(gb.TrailTool)
    cmds.select(ball)
    check("1 つのオブジェクトに 3 種類とも付けられる",
          len(gb.roots_of_selected_targets(cmds, mu)) == 3)
    cmds.select(ball)
    LOGS[:] = []
    res = run_tool(gb.GhostBlurUpdateTool)
    check("付けたオブジェクトを選んで更新すると、付いている物全部", len(res) == 3, str(res))
    err = _errors_of(lambda: (cmds.select(clear=True), run_tool(gb.GhostBlurUpdateTool)))
    check("エラー: 何も選ばずに更新", err is not None)

    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        a1 = "|dfx_afterimage1"
        cmds.setAttr(a1 + ".dfx_count", 7)
        cmds.setAttr(a1 + ".dfx_opacity_end", 0.33)
        dialog.answer = os.path.join(tmp, "ghost")
        cmds.select(a1)
        run_tool(_preset.PresetSaveTool)
        dialog.answer = os.path.join(tmp, "ghost.json")
        cmds.select("dfx_afterimage2")
        run_tool(_preset.PresetLoadTool)
        kind = gb.KINDS[gb.AFTERIMAGE]
        check("プリセット: 残像の保存・読み込み (作り直される)",
              mu.read_param_values("dfx_afterimage2", kind.stored_params())
              == mu.read_param_values(a1, kind.stored_params())
              and len(cmds.ls("dfx_afterimage2_ghost_0?", type="transform")) == 7
              and "afterimage" in dialog.calls[0]["startingDirectory"])
        cmds.select("dfx_smear1")
        err = _errors_of(lambda: run_tool(_preset.PresetLoadTool))
        check("プリセット: 種類の違う物には読み込めない", err is not None, err)
        cmds.select("dfx_smear1", "dfx_trail1")
        err = _errors_of(lambda: run_tool(_preset.PresetLoadTool))
        check("プリセット: 違う種類を同時に選ぶとエラー", err and u"同じ種類" in err, err)
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var

    # 名前を変えても・親を変えても動く (エクスプレッションはルートからたどり直す)
    new_scene()
    ball = _animated_ball()
    cmds.select(ball)
    ar = run_tool(gb.AfterimageTool, count=2)
    cmds.select(ball)
    tr = run_tool(gb.TrailTool)
    cmds.select(ball)
    run_tool(gb.SmearTool)
    ball = cmds.rename(ball, "hero")
    holder = cmds.group(empty=True, name="holder")
    cmds.setAttr(holder + ".translateZ", 5)
    ball = cmds.ls(cmds.parent(ball, holder, relative=True)[0], long=True)[0]
    cmds.select(clear=True)
    LOGS[:] = []
    pos(ball, 7)
    m = cmds.getAttr("%s_ghost_01_ball.worldMatrix[0]" % ar)
    was = cmds.getAttr(ball + ".worldMatrix[0]", time=6)
    pts = cmds.xform(ball + ".vtx[*]", q=True, ws=True, t=True)
    trail_pts = cmds.xform(tr + "_trail.vtx[0]", q=True, ws=True, t=True)
    check("付けたオブジェクトの名前・親を変えても残像・軌跡・スミアが付いて動く",
          _mat_diff(m, was) < 1e-4 and abs(trail_pts[2] - 5.0) < 1e-3
          and max(dist(cmds.xform(ball, q=True, ws=True, t=True), pts[i:i + 3])
                  for i in range(0, len(pts), 3)) > 2.0, str((_mat_diff(m, was), trail_pts)))

    # エクスプレッションが評価される時に ls を使わない (外側の ls の結果を壊さない)
    pos(ball, 3)
    cmds.select(ball)
    cmds.currentTime(9)
    first = cmds.ls("*." + mu.EFFECT_TYPE_ATTR, objectsOnly=True, recursive=True, long=True)
    check("評価の途中でも ls の結果が正しい", sorted(first) == sorted(mu.all_effect_roots())
          and all(r.startswith("|dfx_") for r in first), str(first))


_WEATHER_WS = []


def _weather_scene(frames=(1, 60)):
    """一時フォルダのプロジェクト (テクスチャをそこに作る) と、カメラ。"""
    import io as _io
    if not _WEATHER_WS:
        ws = tempfile.mkdtemp().replace("\\", "/")
        _io.open(ws + "/workspace.mel", "w").write(u'workspace -fr "sourceImages" "sourceimages";\n')
        _WEATHER_WS.append(ws)
    new_scene()
    cmds.flushUndo()
    cmds.workspace(_WEATHER_WS[0], openWorkspace=True)
    cmds.playbackOptions(minTime=frames[0], maxTime=frames[1])
    cam = cmds.camera(name="shotCam")[0]
    cmds.xform(cam, ws=True, t=(0, 3, 22))
    cmds.viewLookAt(cam, pos=(0, 2.5, 0))
    return cmds.ls(cam, long=True)[0]


def _rows(node):
    m = cmds.getAttr(node + ".worldMatrix[0]")
    return [m[0:3], m[4:7], m[8:11], m[12:15]]


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else [0.0, 0.0, 0.0]


def _children(root, group):
    return cmds.listRelatives("%s|%s_%s" % (root, root, group), children=True, fullPath=True) or []


@section
def t33_weather_wind():
    from dummy_effect_maker.tools import _weather as wx, wind, weather
    cam = _weather_scene()
    cmds.select(cam)
    rain = run_tool(weather.RainTool, count=20)
    check("風が無い時は、天候は風につながらない", not wx.find_link(cmds, "|" + rain, wx.WIND_ATTR))
    LOGS[:] = []
    cmds.select(clear=True)
    w = run_tool(wind.WindTool, strength=0.2, gust=0.0, turbulence=0.0)
    check("風を作ると、風のない天候をつなぐ",
          wx.find_link(cmds, "|" + rain, wx.WIND_ATTR) == "|" + w
          and any(u"つなぎました" in m for m in LOGS), str(LOGS[-2:]))
    start = cmds.getAttr(w + "." + wx.DRIFT_START_ATTR)
    vx = cmds.getAttr(w + ".dfxWindX")
    dx = cmds.getAttr(w + ".dfxDriftX")
    check("風の表: タイムスライダーの前後も入る",
          start == 1 - wx.WIND_PREROLL and len(vx) == 60 + wx.WIND_PREROLL + wx.WIND_POSTROLL)
    check("突風・乱れが 0 なら、矢印の向き (+X) に一定の強さ",
          all(abs(x - 0.2) < 1e-9 for x in vx) and abs(dx[100] - 0.2 * 100) < 1e-6, str(vx[:3]))
    cmds.setAttr(w + ".rotateY", 90)
    cmds.setKeyframe(w, attribute="dfx_strength", time=1, value=0.0)
    cmds.setKeyframe(w, attribute="dfx_strength", time=31, value=0.6)
    cmds.select(clear=True)
    run_tool(wind.WindUpdateTool)
    vz = cmds.getAttr(w + ".dfxWindZ")
    vx = cmds.getAttr(w + ".dfxWindX")
    i31 = int(31 - start)
    check("風を更新: 矢印を回す (Y 90 で -Z へ) と強さのキーが反映",
          abs(vz[i31] + 0.6) < 1e-6 and abs(vx[i31]) < 1e-6 and abs(vz[int(1 - start)]) < 1e-9,
          str((vz[i31], vx[i31])))
    drift = cmds.getAttr(w + ".dfxDriftZ")
    integ = wx.integrate([(0, 0, z) for z in vz])
    check("流された距離 = 風の積分", max(abs(a - b[2]) for a, b in zip(drift, integ)) < 1e-6)
    # 突風あり: 強さが時々増える
    cmds.cutKey(w, attribute="dfx_strength")
    cmds.setAttr(w + ".rotateY", 0)
    cmds.setAttr(w + ".dfx_strength", 0.2)
    cmds.setAttr(w + ".dfx_gust", 1.5)
    cmds.select(w)
    run_tool(wind.WindUpdateTool)
    vx = cmds.getAttr(w + ".dfxWindX")
    check("突風: 時々 強さ × (1 + 突風の強さ) くらいまで強くなる",
          min(vx) >= 0.2 - 1e-9 and max(vx) > 0.2 * 2.0, str((min(vx), max(vx))))
    err = None
    new_scene()
    try:
        run_tool(wind.WindUpdateTool)
    except ToolError as e:
        err = u"%s" % e
    check("エラー: 風が無いのに風を更新", err is not None)


@section
def t34_weather_rain_snow():
    from dummy_effect_maker.tools import _weather as wx, wind, weather
    cam = _weather_scene()
    cmds.select(cam)
    root = run_tool(weather.RainTool, count=60, follow_camera=False, area_width=20.0,
                    area_height=10.0)
    area = cmds.ls(root + "_area", long=True)[0]
    drops = _children(root, "drops")
    splashes = _children(root, "splashes")
    check("雨: 粒 60・はね 60、範囲の箱 (幅 20・高さ 10、底が地面)",
          len(drops) == 60 and len(splashes) == 60
          and near(cmds.getAttr(area + ".scale")[0], (20, 10, 20))
          and near(cmds.getAttr(area + ".translate")[0], (0, 5, 0)))
    v = weather.RainTool.default_values()

    def inside(p, margin=1e-3):
        return abs(p[0]) <= 10 + margin and abs(p[2]) <= 10 + margin and -margin <= p[1] <= 10 + margin

    bad = []
    for f in (1, 7, 15, 33):
        pos(drops[0], f)
        for d in drops:
            if cmds.getAttr(d + ".visibility") and not inside(cmds.xform(d, q=True, ws=True, t=True)):
                bad.append((f, d))
    check("雨: 粒はいつも範囲の箱の中", not bad, str(bad[:2]))
    falls, aligned = [], True
    for d in drops[:20]:
        a = pos(d, 10)
        b = pos(d, 11)
        if b[1] < a[1]:
            falls.append(a[1] - b[1])
            y = _unit(_rows(d)[1])
            if abs(y[1] + 1) > 1e-6:
                aligned = False
    check("雨: 1 フレームに 落ちる速さ (1.5 ± ばらつき) だけ落ち、線は真下を向く (風なし)",
          falls and all(1.5 * 0.8 - 1e-6 <= x <= 1.5 * 1.2 + 1e-6 for x in falls) and aligned,
          str(falls[:4]))
    pos(drops[0], 12)
    p1 = [cmds.xform(d, q=True, ws=True, t=True) for d in drops]
    pos(drops[0], 40)
    pos(drops[0], 12)
    p2 = [cmds.xform(d, q=True, ws=True, t=True) for d in drops]
    check("雨: どのフレームに飛んでも同じ位置 (シミュレーションではない)",
          all(near(a, b, 1e-6) for a, b in zip(p1, p2)))
    shown = []
    for f in range(5, 25):
        pos(drops[0], f)
        for s in splashes:
            if cmds.getAttr(s + ".visibility"):
                shown.append(cmds.xform(s, q=True, ws=True, t=True))
    check("雨: はねは地面 (箱の底) に出る", shown and all(abs(p[1]) < 1e-6 for p in shown),
          str(len(shown)))
    cmds.setAttr(root + ".dfx_amount", 0.0)
    pos(drops[0], 9)
    check("雨: 量 0 で降らない (キーで降り始め・止みを作れる)",
          not any(cmds.getAttr(d + ".visibility") for d in drops + splashes))
    cmds.setAttr(root + ".dfx_amount", 1.0)
    # 風: X に 0.3 / フレーム → 線が傾き、横に流れる
    cmds.select(clear=True)
    run_tool(wind.WindTool, strength=0.3, gust=0.0, turbulence=0.0)
    shifts, tilt = [], True
    for d in drops[:20]:
        a = pos(d, 10)
        b = pos(d, 11)
        if b[1] < a[1] and abs(b[0] - a[0]) < 5:
            shifts.append(b[0] - a[0])
            y = _unit(_rows(d)[1])
            if not (y[0] > 0.05 and y[1] < -0.9):
                tilt = False
    check("雨: 風で横に流され (1 フレーム 0.3)、線も風の向きに傾く",
          shifts and all(abs(x - 0.3) < 1e-6 for x in shifts) and tilt, str(shifts[:3]))
    # カメラに付いて動く
    cmds.select(cam)
    r2 = run_tool(weather.RainTool, count=10, follow_camera=True)
    a2 = cmds.ls(r2 + "_area", long=True)[0]
    cmds.setAttr(cam + ".translateX", 50)
    check("雨: カメラに付いて動く範囲 (カメラの X / Z)",
          abs(cmds.xform(a2, q=True, ws=True, t=True)[0] - 50) < 1e-6
          and abs(cmds.xform(a2, q=True, ws=True, t=True)[1] - 10) < 1e-6)
    cmds.setAttr(cam + ".translateX", 0)

    # フレームを変えずに箱・カメラを動かしても、すぐ付いてくる (位置を本当の接続で読む)
    cmds.select(cam)
    r3 = run_tool(weather.RainTool, count=30)
    a3 = cmds.ls(r3 + "_area", long=True)[0]
    d3 = _children(r3, "drops")
    pos(d3[0], 8)

    def center(nodes):
        ps = [cmds.xform(n, q=True, ws=True, t=True) for n in nodes]
        return [sum(p[k] for p in ps) / len(ps) for k in range(3)]
    check("雨: 既定では範囲の箱は原点 (カメラに付いて動かない)",
          near(cmds.xform(a3, q=True, ws=True, t=True), (0, 10, 0)) and abs(center(d3)[0]) < 3)
    cmds.setAttr(a3 + ".translate", 40, 10, -5, type="double3")
    c = center(d3)
    check("雨: フレームを変えずに箱を動かしても、すぐ付いてくる", abs(c[0] - 40) < 3 and abs(c[2] + 5) < 3, str(c))
    cmds.select(cam)
    r4 = run_tool(weather.RainTool, count=30, follow_camera=True)
    d4 = _children(r4, "drops")
    pos(d4[0], 8)
    cmds.setAttr(cam + ".translate", -30, 3, 50, type="double3")
    c = center(d4)
    check("雨 (カメラに付いて動く): フレームを変えずにカメラを動かしても、すぐ付いてくる",
          abs(c[0] + 30) < 3 and abs(c[2] - 50) < 3, str(c))
    cmds.setAttr(cam + ".translate", 0, 3, 22, type="double3")

    # 雪
    cmds.select(cam)
    snow = run_tool(weather.SnowTool, count=40, follow_camera=False)
    flakes = _children(snow, "flakes")
    bad = []
    for f in (3, 20):
        pos(flakes[0], f)
        cp = cmds.xform(cam, q=True, ws=True, t=True)
        for fl in flakes:
            r = _rows(fl)
            z = _unit(r[2])
            to_cam = _unit([c - p for c, p in zip(cp, r[3])])
            s = cmds.getAttr(fl + ".scaleX")
            if sum(a * b for a, b in zip(z, to_cam)) < 0.999 or not (0.04 - 1e-6 <= s <= 0.12 + 1e-6):
                bad.append((f, fl, s))
    check("雪: 丸はカメラを向き、大きさは 粒の大きさ ± ばらつき", not bad, str(bad[:2]))
    cmds.setAttr(cam + ".translate", 15, 8, 5, type="double3")
    cp = cmds.xform(cam, q=True, ws=True, t=True)
    fl = flakes[0]
    r = _rows(fl)
    z = _unit(r[2])
    to_cam = _unit([c - p for c, p in zip(cp, r[3])])
    check("雪: フレームを変えずにカメラを動かしても、すぐ向き直る",
          sum(a * b for a, b in zip(z, to_cam)) > 0.999)
    cmds.setAttr(cam + ".translate", 0, 3, 22, type="double3")


@section
def t35_weather_dust_sparks():
    from dummy_effect_maker.tools import _weather as wx, weather
    cam = _weather_scene()
    car = cmds.polyCube(name="car")[0]
    cmds.setKeyframe(car, attribute="translateX", time=1, value=0)
    cmds.setKeyframe(car, attribute="translateX", time=21, value=20)
    cmds.select(car)
    root = run_tool(weather.DustTool, count=10, start_frame=5.0, duration=10.0, life=20.0,
                    life_random=0.0, size_random=0.0)
    em = cmds.ls(root + "_emitter", long=True)[0]
    puffs = _children(root, "puffs")
    pos(em, 15)
    check("土煙: 出る場所は選んだオブジェクトに付いて動く", near(pos(em, 15), pos(car, 15)))
    v = weather.DustTool.default_values()
    # 粒 i は 5 + 10 * i / 9 に、その時の出る場所から出る
    i = 3
    ts = 5 + 10.0 * i / 9
    before = math.floor(ts) - 0.0
    pos(puffs[i], before)
    hidden = not cmds.getAttr(puffs[i] + ".visibility")
    pos(puffs[i], ts)
    start_p = cmds.xform(puffs[i], q=True, ws=True, t=True)
    emit_p = cmds.getAttr(em + ".worldMatrix[0]", time=ts)[12:15]
    size0 = cmds.getAttr(puffs[i] + ".scaleX")
    check("土煙: 出る前は出ず、出た時はその時の出る場所 (+ 大きさ分 上)",
          hidden and near(start_p, (emit_p[0], emit_p[1] + 0.4 * 0.35, emit_p[2]), 1e-4)
          and abs(size0 - 0.4) < 1e-6, str((start_p, emit_p, size0)))
    sizes = []
    for f in (ts + 2, ts + 8, ts + 13.5, ts + 19.9, ts + 20.5):
        pos(puffs[i], f)
        sizes.append((cmds.getAttr(puffs[i] + ".scaleX"), cmds.getAttr(puffs[i] + ".visibility")))
    check("土煙: 大きくなってから、最後は小さくなって消える",
          sizes[0][0] < sizes[1][0] and sizes[3][0] < 0.2 and not sizes[4][1], str(sizes))
    cmds.select(clear=True)
    mesh_root = run_tool(weather.DustMeshTool, count=5)
    check("土煙 (もこもこ): 立体の塊 (丸 5 つ) のインスタンス",
          len(_children(mesh_root, "puffs")) == 5
          and cmds.polyEvaluate(_children(mesh_root, "puffs")[0], shell=True) == 5)

    # 火花: 計算どおり (風なし) に飛び、地面で跳ねる
    cmds.select(cam)
    sp = run_tool(weather.SparksTool, count=30, start_frame=2.0, ground=0.0)
    em = cmds.ls(sp + "_emitter", long=True)[0]
    cmds.setAttr(em + ".translate", 1, 2, -1, type="double3")
    v = weather.SparksTool.default_values()
    v.update(count=30, start_frame=2.0)
    seed = 1
    sparks = _children(sp, "sparks")
    bad, below = [], []
    for f in (3, 8, 15, 22, 30):
        pos(sparks[0], f)
        for i, s in enumerate(sparks):
            life = v["life"] * (1 + (wx.hash01(i, 0, 1, seed) - 0.5) * 2 * v["life_random"])
            life = max(life, 1)
            t0 = v["start_frame"] + wx.hash01(i, 0, 2, seed) * 2.0
            age = f - t0
            vis = 0 <= age <= life
            if bool(cmds.getAttr(s + ".visibility")) != vis:
                bad.append((f, i, "vis"))
                continue
            if not vis:
                continue
            d = weather.cone_direction((0, 1, 0), wx.hash01(i, 0, 3, seed), wx.hash01(i, 0, 4, seed),
                                       v["spread"])
            spd = v["speed"] * (1 + (wx.hash01(i, 0, 5, seed) - 0.5) * 2 * v["speed_random"])
            p, _ = weather.spark_motion((1, 2, -1), [x * spd for x in d], v["gravity"], v["ground"],
                                        v["bounce"], age)
            got = cmds.xform(s, q=True, ws=True, t=True)
            if not near(got, p, 1e-4):
                bad.append((f, i, got, p))
            if got[1] < -1e-6:
                below.append((f, i))
    check("火花: 計算どおりの位置 (重力・地面ではね返り)", not bad, str(bad[:2]))
    check("火花: 地面より下に行かない", not below, str(below[:2]))
    cmds.select(cam)
    tr = run_tool(weather.SparkTrailTool, count=6, segments=4, start_frame=2.0)
    joints = _children(tr, "joints")
    trails = cmds.ls(tr + "_trails", long=True)[0]
    check("火花 (尾を引く): 火花 6 × 5 行のジョイント、帯はスキン",
          len(joints) == 30 and cmds.polyEvaluate(trails, vertex=True) == 60
          and cmds.ls(cmds.listHistory(trails), type="skinCluster"))
    pos(joints[0], 6)
    head = cmds.xform(joints[0], q=True, ws=True, t=True)
    tail = cmds.xform(joints[4], q=True, ws=True, t=True)
    check("火花 (尾を引く): 先頭の行は火花の位置、尾は出た所の方へ伸びる",
          dist(head, tail) > 0.1 and cmds.getAttr(joints[4] + ".scaleX") < cmds.getAttr(joints[0] + ".scaleX"),
          str((head, tail)))


@section
def t36_weather_layers():
    from dummy_effect_maker.tools import _weather as wx, weather_layers as wl
    cam = _weather_scene()
    cmds.select(cam)
    root = run_tool(wl.RainFarTool)
    layers = _children(root, "layers")
    pos(layers[0], 10)
    grp = cmds.ls(root + "_layers", long=True)[0]
    place = cmds.ls(root + "_layer_01_mat_place")[0]
    ru = cmds.getAttr(place + ".repeatU")
    v1 = cmds.getAttr(place + ".offsetV")
    pos(layers[0], 11)
    v2 = cmds.getAttr(place + ".offsetV")
    tex = cmds.getAttr(cmds.ls(root + "_layer_01_mat_file")[0] + ".fileTextureName")
    check("雨 (遠景の筒): 3 重の筒がカメラの位置に、模様は周りに整数回・1 フレームに 速さ / 模様の高さ 流れる",
          len(layers) == 3 and near(cmds.xform(grp, q=True, ws=True, t=True), (0, 3, 22))
          and abs(ru - round(ru)) < 1e-9 and abs((v2 - v1) - 1.5 / (3.0 * 2.0)) < 1e-6
          and os.path.isfile(tex) and "sourceimages/dfx_weather" in tex, str((ru, v2 - v1, tex)))
    alpha = [cmds.getAttr(cmds.ls("%s_layer_%02d_mat_file" % (root, k))[0] + ".alphaGain") for k in (1, 2, 3)]
    check("雨 (遠景の筒): 外側ほど薄い", alpha[0] > alpha[1] > alpha[2] and abs(alpha[0] - 0.5) < 1e-6,
          str(alpha))

    cmds.select(cam)
    fog = run_tool(wl.FogTool, count=12, area_width=40.0, area_height=6.0)
    cards = _children(fog, "cards")
    pos(cards[0], 5)
    cp = cmds.xform(cam, q=True, ws=True, t=True)
    bad = []
    for i, c in enumerate(cards):
        p = cmds.xform(c, q=True, ws=True, t=True)
        gain = cmds.getAttr(cmds.ls("%s_card_%03d_mat_file" % (fog, i + 1))[0] + ".alphaGain")
        cg = cmds.getAttr(cmds.ls("%s_card_%03d_mat_file" % (fog, i + 1))[0] + ".colorGainR")
        if abs(p[0]) > 20 + 1e-6 or abs(p[2]) > 20 + 1e-6 or gain > 0.4 + 1e-6 \
                or abs(cg - 0.88) > 1e-6:
            bad.append((i, p, gain))
        if dist(p, cp) < 4 and gain > 1e-6:
            bad.append((i, "near"))
    check("霧の板: 範囲の中、濃さ ≤ 不透明度、色はそのまま、カメラの近くでは消える",
          not bad, str(bad[:2]))
    cmds.setAttr(cam + ".translate", 0, 3, 0, type="double3")
    pos(cards[0], 6)
    gains = [cmds.getAttr(cmds.ls("%s_card_%03d_mat_file" % (fog, i + 1))[0] + ".alphaGain")
             for i in range(len(cards))]
    near_cards = [i for i, c in enumerate(cards) if dist(cmds.xform(c, q=True, ws=True, t=True), (0, 3, 0)) < 4]
    check("霧の板: カメラが中に入っても、近い板は消える", all(gains[i] < 1e-6 for i in near_cards))
    cmds.setAttr(cam + ".translate", 0, 3, 22, type="double3")

    cmds.select(cam)
    depth = run_tool(wl.FogDepthTool, layers=5, start=10.0, end=50.0, density=0.8)
    frame = cmds.ls(depth + "_frame", long=True)[0]
    pos(frame, 3)
    planes = _children(depth, "frame")
    fm = cmds.getAttr(frame + ".worldMatrix[0]")
    cm = cmds.getAttr(cam + ".worldMatrix[0]")
    tz = [cmds.getAttr(p + ".translateZ") for p in planes]
    tr = cmds.getAttr(cmds.ls(depth + "_mat")[0] + ".outTransparencyR")
    check("霧 (遠景の重ね板): カメラに付き、10〜50 に 5 枚、全部重ねると濃さ 0.8",
          _mat_diff(fm, cm) < 1e-6 and near(tz, (-10, -20, -30, -40, -50))
          and abs((1 - tr ** 5) - 0.8) < 1e-5 and abs(1 - tr - wl.layer_opacity(0.8, 5)) < 1e-6,
          str((tz, tr)))

    cmds.select(clear=True)
    gf = run_tool(wl.GroundFogTool, layers=4)
    area = cmds.ls(gf + "_area", long=True)[0]
    slices = [c for c in cmds.listRelatives(area, children=True, fullPath=True)
              if cmds.listRelatives(c, shapes=True, type="mesh")]
    pos(slices[0], 4)
    ops = [cmds.getAttr(cmds.ls("%s_layer_%02d_mask" % (gf, k + 1))[0] + ".colorEntryList[0].colorR")
           for k in range(4)]
    exp = [wl.ground_layer_opacity(0.6, 1.5, k, 4) for k in range(4)]
    check("地霧 (重ね板): 箱の中に高さ違いの板 4 枚、上ほど薄い", len(slices) == 4
          and max(abs(a - b) for a, b in zip(ops, exp)) < 1e-6, str(ops))
    cmds.select(clear=True)
    gc = run_tool(wl.GroundCrawlTool, count=8)
    cards = _children(gc, "cards")
    pos(cards[0], 4)
    check("地霧 (這う霧): 平たい板 (縦 = 大きさ × 平たさ)",
          all(abs(cmds.getAttr(c + ".scaleY") / cmds.getAttr(c + ".scaleX") - 0.3) < 1e-6 for c in cards))


@section
def t37_weather_common():
    from dummy_effect_maker.tools import _weather as wx, bake, wind, weather, weather_layers as wl
    cam = _weather_scene()
    cmds.select(clear=True)
    w = run_tool(wind.WindTool)
    cmds.select(cam)
    rain = run_tool(weather.RainTool, count=30)
    area = cmds.ls(rain + "_area", long=True)[0]
    cmds.setAttr(area + ".scaleX", 12)
    cmds.setAttr(rain + ".dfx_count", 50)
    cmds.select(area)  # 範囲の箱を選んでもよい
    run_tool(weather.WeatherUpdateTool)
    check("更新: 粒の数が変わり、範囲の箱・カメラ・風のつながりはそのまま",
          len(_children(rain, "drops")) == 50 and cmds.getAttr(area + ".scaleX") == 12
          and wx.find_link(cmds, "|" + rain, wx.WIND_ATTR) == "|" + w
          and wx.find_link(cmds, "|" + rain, wx.CAMERA_ATTR) == cam)
    n1 = len(cmds.ls())
    run_tool(weather.WeatherUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        cmds.setAttr(rain + ".dfx_speed", 2.5)
        dialog.answer = os.path.join(tmp, "rain")
        cmds.select(rain)
        run_tool(_preset.PresetSaveTool)
        cmds.select(cam)
        r2 = run_tool(weather.RainTool, count=10)
        dialog.answer = os.path.join(tmp, "rain.json")
        cmds.select(r2)
        run_tool(_preset.PresetLoadTool)
        kind = wx.KINDS["rain"]
        check("プリセット: 保存・読み込み (数も反映して作り直す)",
              mu.read_param_values(r2, kind.stored_params()) == mu.read_param_values(rain, kind.stored_params())
              and len(_children(r2, "drops")) == 50 and "rain" in dialog.calls[0]["startingDirectory"])
        cmds.select(w)
        run_tool(_preset.PresetSaveTool)
        check("プリセット: 風も保存できる", "wind" in dialog.calls[-1]["startingDirectory"])
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var

    # ベイク: 雨・霧の板・火花 (尾を引く)
    cmds.select(cam)
    fog = run_tool(wl.FogTool, count=6)
    cmds.select(cam)
    tr = run_tool(weather.SparkTrailTool, count=4, start_frame=2.0)
    targets = {"rain": _children(rain, "drops")[:5], "fog": _children(fog, "cards"),
               "trail": _children(tr, "joints")[:8]}
    gains = [cmds.ls("%s_card_%03d_mat_file" % (fog, i + 1))[0] for i in range(6)]
    before = {}
    for f in (3, 9, 17):
        pos(targets["rain"][0], f)
        before[f] = ([cmds.getAttr(n + ".worldMatrix[0]") for k in targets for n in targets[k]],
                     [cmds.getAttr(g + ".alphaGain") for g in gains])
    cmds.select(rain, fog, tr)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=20)
    same = True
    for f, (mats, gs) in before.items():
        pos(targets["rain"][0], f)
        now = [cmds.getAttr(n + ".worldMatrix[0]") for k in targets for n in targets[k]]
        if max(_mat_diff(a, b) for a, b in zip(mats, now)) > 1e-3 or \
                max(abs(a - cmds.getAttr(g + ".alphaGain")) for a, g in zip(gs, gains)) > 1e-4:
            same = False
    check("ベイク: 粒・霧の板 (濃さも)・尾のジョイントが同じ動き", same)
    owned = cmds.listConnections(["|" + r + ".dfxOwned" for r in (rain, fog, tr)]) or []
    check("ベイク: エクスプレッションは消え、尾のスキンは残る",
          not cmds.ls(owned, type="expression")
          and cmds.ls(cmds.listHistory(cmds.ls(tr + "_trails")[0]), type="skinCluster"))
    cmds.undo()

    new_scene()
    cam = _weather_scene()
    before_nodes = set(cmds.ls())
    cmds.select(cam)
    run_tool(weather.RainTool, count=10)
    cmds.undo()
    check("アンドゥ 1 回で全て消える", not (set(cmds.ls()) - before_nodes))
    cmds.select(clear=True)
    run_tool(wind.WindTool)
    for tool in (weather.RainTool, weather.SnowTool, weather.DustTool, weather.DustMeshTool,
                 weather.SparksTool, weather.SparkTrailTool, wl.RainFarTool, wl.SnowFarTool,
                 wl.FogTool, wl.FogDepthTool, wl.GroundFogTool, wl.GroundCrawlTool):
        cmds.select(cam)
        run_tool(tool, **({"count": 5} if "count" in [p.name for p in tool.params] else {}))
    check("12 種類と風を全部作れる", len(wx.weather_roots(cmds, mu)) == 13)
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 天候と風が全部消える (カメラは残る)", not mu.list_tagged() and cmds.objExists(cam))


@section
def t39_weather_custom_texture():
    from dummy_effect_maker.tools import _weather as wx, weather, weather_layers as wl
    from dummy_effect_maker.ui.qt import QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    cam = _weather_scene()
    tmp = tempfile.mkdtemp().replace("\\", "/")
    mine = tmp + "/my_puff.png"
    img = QtGui.QImage(64, 32, QtGui.QImage.Format_ARGB32)
    img.fill(QtGui.QColor(255, 255, 255, 200))
    img.save(mine)
    other = tmp + "/other.png"
    img.save(other)
    cmds.select(cam)
    root = run_tool(weather.DustTool, count=4, texture=mine)
    tex = cmds.ls(root + "_mat_file")[0]
    check("テクスチャ: 指定したファイルを貼る", cmds.getAttr(tex + ".fileTextureName") == mine,
          cmds.getAttr(tex + ".fileTextureName"))
    check("テクスチャ: ルートに文字列で保存 (表示名に「(更新)」)",
          cmds.getAttr(root + ".dfx_texture") == mine
          and cmds.attributeQuery("dfx_texture", node=root, niceName=True).endswith(u"(更新)"))
    cmds.setAttr(root + ".dfx_texture", other, type="string")
    cmds.select(root)
    run_tool(weather.WeatherUpdateTool)
    tex = cmds.ls(root + "_mat_file")[0]
    check("テクスチャ: ファイルを変えて「天候を更新」で差し替わる",
          cmds.getAttr(tex + ".fileTextureName") == other)
    cmds.setAttr(root + ".dfx_texture", "", type="string")
    run_tool(weather.WeatherUpdateTool)
    tex = cmds.ls(root + "_mat_file")[0]
    check("テクスチャ: 空欄に戻すと既定の絵", cmds.getAttr(tex + ".fileTextureName").endswith(__import__("dummy_effect_maker.textures", fromlist=["x"]).file_name("puff")))
    err = None
    try:
        cmds.select(cam)
        run_tool(weather.DustTool, count=2, texture=tmp + "/nothing.png")
    except ToolError as e:
        err = u"%s" % e
    check("エラー: 無いファイルを指定", err and u"見つかりません" in err, err)
    # 遠景の筒: 繰り返しの高さは絵の縦横比から (64x32 → 0.5)
    cmds.select(cam)
    far = run_tool(wl.RainFarTool, texture=mine)
    place = cmds.ls(far + "_layer_01_mat_place")[0]
    pos(cam, 3)
    check("遠景の筒: 自分の絵の縦横比で繰り返す (高さ 40 / (模様 3 × 0.5))",
          abs(cmds.getAttr(place + ".repeatV") - 40 / (3.0 * 0.5)) < 1e-6,
          str(cmds.getAttr(place + ".repeatV")))
    for tool in (wl.SnowFarTool, wl.FogTool, wl.GroundFogTool, wl.GroundCrawlTool):
        cmds.select(cam)
        r = run_tool(tool, texture=mine, **({"count": 3} if tool in (wl.FogTool, wl.GroundCrawlTool) else {}))
        files = [f for f in cmds.ls(type="file") if f.startswith(r + "_")]
        check(u"テクスチャ: %s も指定したファイル" % tool.label,
              files and all(cmds.getAttr(f + ".fileTextureName") == mine for f in files))
    # オプションダイアログに「参照...」ボタン
    from dummy_effect_maker.ui.param_dialog import ParamDialog
    dlg = ParamDialog(weather.DustTool())
    buttons = [b.text() for b in dlg.findChildren(QtWidgets.QPushButton)]
    check("ダイアログ: テクスチャのファイルに「参照...」", u"参照..." in buttons, str(buttons))

    # 透明度のアルゴリズムが「アルファ カット」なら、作成時にログで知らせる
    from dummy_effect_maker.tools import ghost_blur as gb, shockwave as sw
    prev = cmds.getAttr("hardwareRenderingGlobals.transparencyAlgorithm")
    try:
        cmds.setAttr("hardwareRenderingGlobals.transparencyAlgorithm", 5)
        warned = []
        for tool, kw, sel in ((wl.FogTool, {"count": 2}, cam), (sw.ShockwaveTool, {}, None)):
            LOGS[:] = []
            cmds.select(sel) if sel else cmds.select(clear=True)
            run_tool(tool, **kw)
            warned.append(any(u"アルファ カット" in m for m in LOGS))
        cmds.setAttr("hardwareRenderingGlobals.transparencyAlgorithm", 1)
        LOGS[:] = []
        cmds.select(cam)
        run_tool(wl.FogTool, count=2)
        quiet = not any(u"アルファ カット" in m for m in LOGS)
        LOGS[:] = []
        cmds.setAttr("hardwareRenderingGlobals.transparencyAlgorithm", 5)
        cmds.select(cam)
        run_tool(weather.SparksTool, count=2)
        sparks_quiet = not any(u"アルファ カット" in m for m in LOGS)
    finally:
        cmds.setAttr("hardwareRenderingGlobals.transparencyAlgorithm", prev)
    check("アルファ カット: 霧・衝撃波の作成時にログで知らせ、オブジェクトのソートや火花では出ない",
          all(warned) and quiet and sparks_quiet, str((warned, quiet, sparks_quiet)))


@section
def t40_explosion():
    from maya.api import OpenMaya as om
    from dummy_effect_maker.tools import bake, energy_ball, explosion as ex, vulcan
    cam = _weather_scene()
    err = _errors_of(lambda: (cmds.select(clear=True), run_tool(ex.ColliderSetTool)))
    check("エラー: 何も選ばずにコリジョンに指定", err is not None)
    loc = cmds.spaceLocator()[0]
    cmds.select(loc)
    err = _errors_of(lambda: run_tool(ex.ColliderSetTool))
    check("エラー: メッシュの無いものはコリジョンにできない", err and u"メッシュ" in err, err)
    vr = run_tool(vulcan.VulcanTool, start_frame=1.0, end_frame=20.0, interval=5.0)
    cmds.select(vr)
    err = _errors_of(lambda: run_tool(ex.ExplosionTool))
    check("エラー: コリジョンが無いのに爆発", err and u"コリジョン" in err, err)

    wall = cmds.polyCube(w=1, h=4, d=6, name="wall")[0]
    cmds.move(10, 0, 0, wall)
    other = cmds.polyCube(w=1, h=4, d=6, name="far_wall")[0]
    cmds.move(16, 0, 0, other)
    cmds.select(wall, other)
    run_tool(ex.ColliderSetTool)
    check("コリジョンに指定: 2 つ (属性が付く)", sorted(ex.colliders(cmds)) == sorted(["|wall", "|far_wall"]))
    cmds.select(vr)
    er = run_tool(ex.ExplosionTool)
    hits = ex.read_hits(cmds, "|" + er)
    bullets = ex.bullets_of(cmds, "|" + vr)
    bad = []
    for h in hits:
        b = bullets[h["index"]]
        p = cmds.getAttr(b + ".worldMatrix[0]", time=h["frame"])[12:15]
        if not near(p, h["pos"], 1e-3) or abs(h["pos"][0] - 9.5) > 1e-3 \
                or not near(h["normal"], (-1, 0, 0), 1e-6):
            bad.append((h, p))
    check("当たり: 弾 4 発とも手前の壁の面 (x = 9.5) に、壁の外向き (-X) の向きで当たる",
          len(hits) == 4 and len(bullets) == 4 and not bad, str(bad[:1]))
    h0 = hits[0]
    b0 = bullets[0]
    pos(b0, h0["frame"] - 0.5)
    before = cmds.getAttr(b0 + ".lodVisibility")
    pos(b0, h0["frame"] + 0.5)
    after = cmds.getAttr(b0 + ".lodVisibility")
    check("当たった弾は、当たったフレームから消える", before and not after)
    cmds.setAttr(er + ".dfx_hide_bullet", False)
    check("「当たった弾を消す」OFF なら消えない", cmds.getAttr(b0 + ".lodVisibility"))
    cmds.setAttr(er + ".dfx_hide_bullet", True)

    def group(name):
        return cmds.listRelatives("%s|%s_%s" % (er, er, name), children=True, fullPath=True) or []
    counts = [len(group(n)) for n in ("flashes", "rings", "fire", "smoke", "sparks")]
    check("部品: 当たり 4 つ × (閃光 1・輪 1・火の玉 6・煙 6・火花 10)", counts == [4, 4, 24, 24, 40], str(counts))
    flash, ring = group("flashes")[0], group("rings")[0]
    fires = group("fire")[:6]
    pos(flash, h0["frame"] - 0.5)
    hidden = not cmds.getAttr(flash + ".visibility") and not any(cmds.getAttr(f + ".visibility") for f in fires)
    pos(flash, h0["frame"] + 1)
    shown = cmds.getAttr(flash + ".visibility") and cmds.getAttr(ring + ".visibility")
    fpos = cmds.xform(flash, q=True, ws=True, t=True)
    rz = _unit(_rows(ring)[2])
    pos(flash, h0["frame"] + 5)
    flash_gone = not cmds.getAttr(flash + ".visibility")
    check("爆発: 当たるまで出ず、当たった所の手前に閃光と、面に沿った輪が出て、閃光はすぐ消える",
          hidden and shown and flash_gone and abs(fpos[0] - (9.5 - 0.3)) < 1e-3
          and near(rz, (-1, 0, 0), 1e-6), str((fpos, rz)))
    pos(flash, h0["frame"] + 4)
    fire_x = [cmds.xform(f, q=True, ws=True, t=True)[0] for f in fires if cmds.getAttr(f + ".visibility")]
    check("火の玉は壁の手前 (弾の来た方) に広がる", fire_x and all(x < 9.5 for x in fire_x), str(fire_x))
    cmds.setAttr(er + ".dfx_size", 2.0)
    pos(flash, h0["frame"] + 1)
    s2 = cmds.getAttr(flash + ".scaleX")
    cmds.setAttr(er + ".dfx_size", 1.0)
    s1 = cmds.getAttr(flash + ".scaleX")
    check("即反映: 大きさ 2 で閃光も 2 倍", abs(s2 - 2 * s1) < 1e-6, str((s1, s2)))

    # コリジョンを動かして更新 (アニメーションする壁)
    cmds.setKeyframe(wall, attribute="translateX", time=1, value=12)
    cmds.setKeyframe(wall, attribute="translateX", time=20, value=6)
    cmds.setKeyframe(wall, attribute="rotateY", time=1, value=0)
    cmds.setKeyframe(wall, attribute="rotateY", time=20, value=40)
    cmds.select(vr)  # 弾を選んで更新してもよい
    run_tool(ex.ExplosionUpdateTool)
    hits = ex.read_hits(cmds, "|" + er)
    bad = []
    for h in hits:
        inv = om.MMatrix(cmds.getAttr("wall.worldInverseMatrix[0]", time=h["frame"]))
        q = om.MPoint(*h["pos"]) * inv
        if abs(abs(q.x) - 0.5) > 1e-2 and abs(abs(q.z) - 3) > 1e-2:
            bad.append((h["frame"], q))
    check("動く・回る壁: 当たった所は、その時の壁の面の上 (誤差 0.01 以内)", len(hits) == 4 and not bad, str(bad[:1]))
    n1 = len(cmds.ls())
    run_tool(ex.ExplosionUpdateTool)
    check("更新: 繰り返してもノード数一定", n1 == len(cmds.ls()))

    # 手前の壁をコリジョンから外すと、奥の壁に当たる
    cmds.cutKey(wall)
    cmds.move(10, 0, 0, wall)
    cmds.setAttr(wall + ".rotateY", 0)
    cmds.select(wall)
    run_tool(ex.ColliderClearTool)
    cmds.select(er)
    run_tool(ex.ExplosionUpdateTool)
    hits = ex.read_hits(cmds, "|" + er)
    check("コリジョンを解除すると、その先の壁に当たる",
          len(hits) == 4 and all(abs(h["pos"][0] - 15.5) < 1e-3 for h in hits), str([h["pos"] for h in hits][:1]))

    # 当たらない時
    cmds.select(other)
    run_tool(ex.ColliderClearTool)
    ground = cmds.polyCube(w=1, h=1, d=1, name="box")[0]
    cmds.move(0, 10, 0, ground)
    cmds.select(ground)
    run_tool(ex.ColliderSetTool)
    LOGS[:] = []
    cmds.select(er)
    run_tool(ex.ExplosionUpdateTool)
    check("当たらない時はログで知らせ、弾は消えない",
          not ex.read_hits(cmds, "|" + er) and any(u"当たりません" in m for m in LOGS)
          and all(cmds.getAttr(b + ".lodVisibility") for b in ex.bullets_of(cmds, "|" + vr)))
    cmds.delete(ground)
    cmds.select(wall)
    run_tool(ex.ColliderSetTool)
    cmds.select(er)
    run_tool(ex.ExplosionUpdateTool)

    # エネルギー弾: 着弾までの間に壁を置く
    eb = run_tool(energy_ball.EnergyBallTool, count=2)
    cmds.setAttr(wall + ".rotateY", 90)
    cmds.move(0, 0, 10, wall)
    cmds.select(eb)
    e2 = run_tool(ex.ExplosionTool)
    eh = ex.read_hits(cmds, "|" + e2)
    check("エネルギー弾も壁に当たって爆発 (2 個)",
          len(eh) == 2 and all(abs(h["pos"][2] - 9.5) < 1e-3 for h in eh), str([h["pos"] for h in eh]))
    cmds.select(eb)
    LOGS[:] = []
    run_tool(ex.ExplosionTool)
    check("もう一度「爆発」を押すと、新しく作らず計算し直す",
          len(ex.explosions_of(cmds, mu, "|" + eb)) == 1 and any(u"計算し直します" in m for m in LOGS))

    # 外す
    eballs = ex.bullets_of(cmds, "|" + eb)
    pos(eballs[0], eh[0]["frame"] + 2)
    hidden_before = not cmds.getAttr(eballs[0] + ".lodVisibility")
    cmds.select(e2)
    run_tool(ex.ExplosionRemoveTool)
    check("爆発を外す: 爆発が消え、隠れていた弾が元に戻る",
          hidden_before and not cmds.objExists(e2) and cmds.getAttr(eballs[0] + ".lodVisibility"))

    # ベイク (弾と一緒に): 当たって消えるのは visibility のキーにまとまる
    cmds.move(10, 0, 0, wall)
    cmds.setAttr(wall + ".rotateY", 0)
    cmds.select(er)
    run_tool(ex.ExplosionUpdateTool)
    hits = ex.read_hits(cmds, "|" + er)
    frames = [int(hits[0]["frame"]) + d for d in (0, 1, 3, 8)]
    parts = group("flashes")[:2] + group("sparks")[:5] + group("fire")[:3]
    before = {}
    for f in frames:
        pos(b0, f)
        before[f] = ([cmds.getAttr(n + ".worldMatrix[0]") for n in parts],
                     [cmds.getAttr(n + ".visibility") for n in parts],
                     cmds.getAttr(b0 + ".visibility") and cmds.getAttr(b0 + ".lodVisibility"))
    cmds.select(vr, er)
    run_tool(bake.BakeTool, range_mode=bake.RANGE_CUSTOM, start_frame=1, end_frame=40)
    same = True
    for f, (mats, vis, bvis) in before.items():
        pos(b0, f)
        if (max(_mat_diff(a, cmds.getAttr(n + ".worldMatrix[0]")) for a, n in zip(mats, parts)) > 1e-3
                or vis != [cmds.getAttr(n + ".visibility") for n in parts]
                or bvis != (cmds.getAttr(b0 + ".visibility") and cmds.getAttr(b0 + ".lodVisibility"))):
            same = False
    check("ベイク: 爆発の動き・表示と、当たって消える弾が同じ", same)
    check("ベイク: 弾の消え方は visibility のキーにまとまる (lodVisibility は 1・キーなし)",
          cmds.getAttr(b0 + ".lodVisibility") and not cmds.keyframe(b0 + ".lodVisibility", query=True)
          and not cmds.ls(cmds.listConnections("|" + er + ".dfxOwned") or [], type="expression"))
    cmds.undo()

    # アンドゥ・全削除
    before_nodes = set(cmds.ls())
    cmds.select(vr)
    run_tool(ex.ExplosionUpdateTool)
    cmds.undo()
    check("アンドゥ 1 回で元に戻る", not (set(cmds.ls()) - before_nodes))
    confirm_yes(lambda: run_tool(cleanup.DeleteAllDummiesTool))
    check("全削除: 爆発も消え、コリジョンのモデルは残る",
          not cmds.ls("dfx_explosion*") and cmds.objExists(wall) and ex.colliders(cmds) == ["|wall"])


@section
def t41_export_shot():
    from dummy_effect_maker.core import exchange as xc
    from dummy_effect_maker.tools import (camera_shake as cs, explosion as ex, export_shot as es,
                                          vulcan, weather, wind)
    cam = _weather_scene(frames=(1, 30))
    cmds.setKeyframe(cam, attribute="translateX", time=1, value=0)
    cmds.setKeyframe(cam, attribute="translateX", time=30, value=300)
    cmds.setKeyframe(cmds.listRelatives(cam, shapes=True)[0], attribute="focalLength", time=1, value=35)
    cmds.setKeyframe(cmds.listRelatives(cam, shapes=True)[0], attribute="focalLength", time=30, value=50)
    vr = run_tool(vulcan.VulcanTool, start_frame=1.0, end_frame=10.0, interval=5.0)
    target = cmds.ls(vr + "_target", long=True)[0]
    cmds.setAttr(target + ".translate", 2000, 500, -100, type="double3")   # センチ
    wall = cmds.polyCube(w=1, h=2000, d=2000, name="wall")[0]
    cmds.move(1000, 0, 0, wall)
    cmds.select(wall)
    run_tool(ex.ColliderSetTool)
    cmds.select(vr)
    er = run_tool(ex.ExplosionTool)
    cmds.select(clear=True)
    w = run_tool(wind.WindTool, strength=2.0, gust=0.0, turbulence=0.0)
    cmds.select(cam)
    rain = run_tool(weather.RainTool, count=5)
    cmds.select(cam)
    shake = run_tool(cs.CameraShakeTool)

    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig = cmds.fileDialog2
    cmds.fileDialog2 = dialog
    try:
        dialog.answer = os.path.join(tmp, "shot01")
        LOGS[:] = []
        cmds.select(clear=True)
        path = run_tool(es.ExportShotTool)
    finally:
        cmds.fileDialog2 = orig
    check("書き出し: ファイルができる (.json が付く)、ログに数",
          path.endswith("shot01.json") and os.path.isfile(path) and any(u"書き出しました" in m for m in LOGS),
          str(LOGS[-1:]))
    shot = xc.read_shot(path)
    effects = dict((e["name"], e) for e in shot["effects"])
    check("書き出し: 形式・空間・時間", shot["space"] == xc.SPACE and shot["time"]["start"] == 1
          and shot["time"]["end"] == 30 and shot["time"]["fps"] == 24.0
          and shot["source"]["app"] == "maya" and shot["source"]["linear_unit"] == "cm")
    check("書き出し: エフェクト全部 (バルカン砲・爆発・風・雨・画ぶれ) と種類",
          set(effects) == set([vr, er, w, rain, shake])
          and effects[vr]["type"] == "vulcan" and effects[er]["type"] == "explosion"
          and effects[vr]["group"] == "bullets", str(sorted(effects)))
    v = effects[vr]
    check("パラメータ: ルートの値と、定義 (種類・範囲)",
          v["params"]["interval"] == 5.0 and v["params"]["color"] == list(cmds.getAttr(vr + ".dfx_color")[0])
          and v["param_info"]["interval"]["kind"] == "float" and v["param_info"]["color"]["kind"] == "color")
    ctrl = dict((c["name"], c) for c in v["controls"])
    tgt = ctrl[target.split("|")[-1]]
    check("動かす物: ロケータは共通の空間 (Z が上・メートル) で (2000, 500, -100) cm → (20, 1, 5) m",
          tgt["track"]["static"] and near(tgt["track"]["t"], (20, 1, 5), 1e-6) and tgt["shape"] == "locator",
          str(tgt["track"]))
    bullets = v["data"]["bullets"]
    hits = effects[er]["data"]["hits"]
    b0 = bullets[0]["track"]
    i = b0["frames"].index(3)
    p = cmds.getAttr(ex.bullets_of(cmds, "|" + vr)[0] + ".worldMatrix[0]", time=3)[12:15]
    check("弾: 毎フレームの位置 (共通の空間) と表示",
          len(bullets) == 2 and near(b0["t"][i], (p[0] / 100, -p[2] / 100, p[1] / 100), 1e-5)
          and b0["visible"][b0["frames"].index(1)] is True, str(b0["t"][i]))
    h0 = hits[0]
    hf = int(h0["frame"]) + 1
    check("爆発: 当たった所 (壁の面 x = 9.995 m) と向き (-X)・その後の弾は表示なし",
          len(hits) == 2 and abs(h0["position"][0] - 9.995) < 1e-4 and near(h0["normal"], (-1, 0, 0), 1e-6)
          and b0["visible"][b0["frames"].index(hf)] is False, str(h0))
    wtrack = effects[w]["data"]["wind"]
    check("風: 毎フレームの風 (2 cm/フレーム → 0.02 m) と流された距離",
          wtrack["static"] is False and near(xc.track_value(wtrack, "velocity", 5), (0.02, 0, 0), 1e-9)
          and abs(xc.track_value(wtrack, "drift", 11)[0] - xc.track_value(wtrack, "drift", 1)[0] - 0.2) < 1e-6)
    check("つながり: 雨 → 風・カメラ・範囲の箱 (短い名前)",
          effects[rain]["links"].get("dfx_wind") == w and effects[rain]["links"].get("dfx_camera") == "shotCam1"
          and any(c["role"] in ("child", "dfx_area") and c["shape"] == "nurbsCurve"
                  for c in effects[rain]["controls"]))
    cams = dict((c["name"], c) for c in shot["cameras"])
    ct = cams["shotCam1"]["track"]
    m = cmds.getAttr(cam + ".worldMatrix[0]", time=15)
    ok_pos = near(xc.track_value(ct, "t", 15), (m[12] / 100, -m[14] / 100, m[13] / 100), 1e-5)
    rows = xc.rows_from_quaternion(xc.track_value(ct, "q", 15))
    fwd_maya = _unit([-m[8], -m[9], -m[10]])
    fwd = [-r for r in rows[2]]
    check("カメラ: 動き (画ぶれ込み)・向き (ローカル -Z が前)・焦点距離",
          ok_pos and near(fwd, (fwd_maya[0], -fwd_maya[2], fwd_maya[1]), 1e-5)
          and abs(xc.track_value(ct, "focal_length", 30) - 50) < 1e-6
          and abs(cams["shotCam1"]["film_aperture_mm"][0] - cmds.getAttr(cam + ".horizontalFilmAperture") * 25.4) < 1e-6)
    check("書き出しはシーンを変えない (アンドゥ不要・時間もそのまま)", cmds.currentTime(query=True) == 1)

    # Z が上のシーン
    new_scene()
    cmds.upAxis(axis="z")
    try:
        r = run_tool(vulcan.VulcanTool, start_frame=1.0, end_frame=2.0)
        t = cmds.ls(r + "_target", long=True)[0]
        cmds.setAttr(t + ".translate", 100, 200, 300, type="double3")
        shot = es.build_shot(cmds, mu, mu.all_effect_roots(), 1, 2)
        c = [c for c in shot["effects"][0]["controls"] if c["name"] == t.split("|")[-1]][0]
        check("Z が上のシーン: 軸はそのまま、センチ → メートル", near(c["track"]["t"], (1, 2, 3), 1e-6),
              str(c["track"]["t"]))
    finally:
        cmds.upAxis(axis="y")
    err = None
    new_scene()
    try:
        run_tool(es.ExportShotTool)
    except ToolError as e:
        err = u"%s" % e
    check("エラー: エフェクトが無いのに書き出し", err is not None)


def _mag(v):
    return math.sqrt(sum(x * x for x in v))


@section
def t42_motion_matches_maya():
    """motion.py (Python 版の計算) が、Maya 版のエクスプレッションと同じ動きになるか。"""
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import _weather as wx, explosion as ex, vulcan, weather, weather_layers as wl, wind
    cam = _weather_scene()
    cmds.setKeyframe(cam, attribute="translateX", time=1, value=-5)
    cmds.setKeyframe(cam, attribute="translateX", time=30, value=5)
    cmds.select(clear=True)
    w = run_tool(wind.WindTool, strength=0.2, gust=1.0)

    def wind_of(root):
        wr = wx.find_link(cmds, "|" + root, wx.WIND_ATTR)
        if not wr:
            return mo.Wind()
        tabs = [cmds.getAttr(wr + "." + a) for a in wx.DRIFT_ATTRS + wx.WIND_VEC_ATTRS]
        return mo.Wind(list(zip(*tabs[:3])), list(zip(*tabs[3:])),
                       cmds.getAttr(wr + "." + wx.DRIFT_START_ATTR), cmds.getAttr(root + ".dfx_wind_influence"))

    def cam_at(camera=None):
        m = cmds.getAttr((camera or cam) + ".worldMatrix[0]")
        return (m[12], m[13], m[14]), (-m[8], -m[9], -m[10])

    def same_xform(node, p, rows=None, scale=None, tol=1e-4):
        m = cmds.getAttr(node + ".worldMatrix[0]")
        if not near((m[12], m[13], m[14]), p, tol):
            return False
        if rows is not None:
            for r in range(3):
                got = _unit(m[4 * r:4 * r + 3])
                if scale is not None and abs(scale[r]) < 1e-9:
                    continue
                if not near(got, rows[r], 1e-3):
                    return False
        if scale is not None:
            for r in range(3):
                if abs(_mag(m[4 * r:4 * r + 3]) - abs(scale[r])) > 1e-4:
                    return False
        return True

    def values(root, tool):
        names = [p.name for p in tool.params]
        return dict((n, mu.read_param_values(root, [p for p in tool.params if p.name == n])[n])
                    if cmds.attributeQuery("dfx_" + n, node=root, exists=True) else
                    (n, dict((p.name, p.default) for p in tool.params)[n]) for n in names)

    # 雨・はね
    cmds.select(cam)
    rain = run_tool(weather.RainTool, count=12, follow_camera=False)
    area = cmds.ls(rain + "_area", long=True)[0]
    cmds.setAttr(area + ".rotateY", 25)
    v = values(rain, weather.RainTool)
    wd = wind_of(rain)
    bad = []
    for f in (3, 9, 17):
        pos(cam, f)
        am = cmds.getAttr(area + ".worldMatrix[0]")
        ai = mo.inverse(am)
        cp, cdir = cam_at()
        for i, d in enumerate(_children(rain, "drops")):
            st = mo.falling(i, f, v, am, ai, wd)
            rows = mo.streak(st["p"], st["vel"], cp, cdir)
            if bool(cmds.getAttr(d + ".visibility")) != st["vis"] or \
                    not same_xform(d, st["p"], rows, (v["width"], v["length"], 1)):
                bad.append(("drop", f, i))
        for i, s in enumerate(_children(rain, "splashes")):
            p, size, vis = mo.splash(i, f, v, am, ai, wd)
            if bool(cmds.getAttr(s + ".visibility")) != vis or (vis and not same_xform(s, p, scale=(size,) * 3)):
                bad.append(("splash", f, i))
    check("Python 版の計算: 雨の線とはね (風・回した範囲の箱)", not bad, str(bad[:3]))

    # 雪
    cmds.select(cam)
    snow = run_tool(weather.SnowTool, count=10, follow_camera=False)
    area = cmds.ls(snow + "_area", long=True)[0]
    v = values(snow, weather.SnowTool)
    wd = wind_of(snow)
    bad = []
    for f in (4, 20):
        pos(cam, f)
        am = cmds.getAttr(area + ".worldMatrix[0]")
        cp, cdir = cam_at()
        for i, fl in enumerate(_children(snow, "flakes")):
            st = mo.falling(i, f, v, am, mo.inverse(am), wd, sway=True)
            sz = mo.snow_size(i, v)
            if not same_xform(fl, st["p"], mo.billboard(st["p"], cp, cdir), (sz, sz, sz)):
                bad.append((f, i))
    check("Python 版の計算: 雪 (揺れ・カメラを向く)", not bad, str(bad[:3]))

    # 土煙 (スプライト・もこもこ)・火花・尾
    for tool, kw in ((weather.DustTool, {"count": 8, "start_frame": 2.0, "duration": 6.0}),
                     (weather.DustMeshTool, {"count": 6, "start_frame": 2.0}),
                     (weather.SparksTool, {"count": 8, "start_frame": 2.0}),
                     (weather.SparkTrailTool, {"count": 3, "start_frame": 2.0, "segments": 4})):
        cmds.select(cam)
        r = run_tool(tool, **kw)
        em = cmds.ls(r + "_emitter", long=True)[0]
        cmds.setKeyframe(em, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(em, attribute="translateX", time=12, value=6)
        cmds.setAttr(em + ".rotateZ", 20)
        v = values(r, tool)
        wd = wind_of(r)
        emitter_at = (lambda e: (lambda t: cmds.getAttr(e + ".worldMatrix[0]", time=t)))(em)
        count = int(v["count"])
        bad = []
        for f in (3, 7, 12):
            pos(cam, f)
            cp, cdir = cam_at()
            if tool is weather.SparkTrailTool:
                joints = _children(r, "joints")
                rows_n = int(v["segments"]) + 1
                for i in range(count):
                    for j, (p, rows, wdt) in enumerate(mo.spark_trail_rows(i, f, v, count, emitter_at, wd, cp, cdir)):
                        if not same_xform(joints[i * rows_n + j], p, rows if wdt > 1e-9 else None, (wdt,) * 3):
                            bad.append((f, i, j))
                continue
            for i, n in enumerate(_children(r, "sparks" if tool is weather.SparksTool else "puffs")):
                if tool is weather.SparksTool:
                    p, vel, vis, fade, s = mo.spark(i, f, v, count, emitter_at, wd)
                    wdt = v["width"] * fade
                    rows, scale = mo.streak(p, vel, cp, cdir), (wdt, max(mo.mag(vel) * v["length"], wdt), 1)
                else:
                    p, sz, vis, a = mo.dust(i, f, v, count, emitter_at, wd)
                    scale = (sz, sz, sz)
                    if tool is weather.DustTool:
                        rows = mo.billboard(p, cp, cdir, mo.dust_roll(i, v, a))
                    else:
                        rx, ry, rz = mo.dust_euler(i, v, a)
                        rows = None
                        if not near(cmds.getAttr(n + ".rotate")[0], (rx, ry, rz), 1e-3):
                            bad.append((f, i, "rot"))
                if bool(cmds.getAttr(n + ".visibility")) != vis or \
                        (vis and not same_xform(n, p, rows if min(abs(x) for x in scale) > 1e-9 else None, scale)):
                    bad.append((f, i))
        check(u"Python 版の計算: %s" % tool.label, not bad, str(bad[:3]))

    # 霧の板
    cmds.select(cam)
    fog = run_tool(wl.FogTool, count=8)
    area = cmds.ls(fog + "_area", long=True)[0]
    v = values(fog, wl.FogTool)
    wd = wind_of(fog)
    bad = []
    for f in (5, 15):
        pos(cam, f)
        am = cmds.getAttr(area + ".worldMatrix[0]")
        cp, cdir = cam_at()
        for i, c in enumerate(_children(fog, "cards")):
            p, (sx, sy), alpha, roll = mo.fog_card(i, f, v, am, mo.inverse(am), wd, cp)
            gain = cmds.getAttr(cmds.ls("%s_card_%03d_mat_file" % (fog, i + 1))[0] + ".alphaGain")
            if abs(gain - alpha) > 1e-5 or (alpha > 0.001 and not same_xform(c, p, mo.billboard(p, cp, cdir, roll), (sx, sy, sx))):
                bad.append((f, i, gain, alpha))
    check("Python 版の計算: 霧の板 (位置・向き・濃さ)", not bad, str(bad[:3]))

    # 爆発 (閃光・輪・火の玉・煙・火花)
    vr = run_tool(vulcan.VulcanTool, start_frame=1.0, end_frame=6.0, interval=5.0)
    wall = cmds.polyCube(w=1, h=6, d=6, name="wall")[0]
    cmds.move(10, 0, 0, wall)
    cmds.rotate(0, 20, 0, wall)
    cmds.select(wall)
    run_tool(ex.ColliderSetTool)
    cmds.select(vr)
    er = run_tool(ex.ExplosionTool)
    v = values(er, ex.ExplosionTool)
    hits = [dict(frame=hh["frame"], pos=hh["pos"], normal=hh["normal"]) for hh in ex.read_hits(cmds, "|" + er)]
    groups = dict((n, cmds.listRelatives("%s|%s_%s" % (er, er, n), children=True, fullPath=True))
                  for n in ("flashes", "rings", "fire", "smoke", "sparks"))
    bad = []
    ecam = wx.find_link(cmds, "|" + er, wx.CAMERA_ATTR)
    for f in (int(hits[0]["frame"]) + d for d in (1, 3, 6, 12)):
        pos(cam, f)
        cp, cdir = cam_at(ecam)
        for e, hit in enumerate(hits):
            p, sz, vis, roll = mo.explosion_flash(e, f, hit, v)
            n = groups["flashes"][e]
            if bool(cmds.getAttr(n + ".visibility")) != vis or (vis and not same_xform(n, p, mo.billboard(p, cp, cdir, roll), (sz,) * 3)):
                bad.append(("flash", f, e))
            p, rows, sz, vis = mo.explosion_ring(e, f, hit, v)
            n = groups["rings"][e]
            if bool(cmds.getAttr(n + ".visibility")) != vis or (vis and sz > 1e-6 and not same_xform(n, p, rows, (sz,) * 3)):
                bad.append(("ring", f, e))
            for part, fn, per in (("fire", mo.explosion_fire, int(v["fire_count"])),
                                  ("smoke", mo.explosion_smoke, int(v["smoke_count"]))):
                for j in range(per):
                    p, sz, vis, rot = fn(e, j, f, hit, v)
                    n = groups[part][e * per + j]
                    if bool(cmds.getAttr(n + ".visibility")) != vis or \
                            (vis and (not same_xform(n, p, scale=(sz,) * 3) or not near(cmds.getAttr(n + ".rotate")[0], rot, 1e-3))):
                        bad.append((part, f, e, j))
            per = int(v["spark_count"])
            for j in range(per):
                p, vel, scale, vis = mo.explosion_spark(e, j, f, hit, v)
                n = groups["sparks"][e * per + j]
                if bool(cmds.getAttr(n + ".visibility")) != vis or (vis and not same_xform(n, p, mo.streak(p, vel, cp, cdir), scale)):
                    bad.append(("spark", f, e, j))
    check("Python 版の計算: 爆発 (閃光・輪・火の玉・煙・火花)", not bad, str(bad[:3]))


@section
def t43_nuke():
    """核爆発: 単体で作れる・motion.py と同じ動き・色の変わり方・更新・ベイク・書き出し。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import nuke, _weather as wx, export_shot, bake
    from dummy_effect_maker.tools.ghost_blur import rotation_rows
    cam = _weather_scene()
    cmds.select(clear=True)
    target = cmds.polyCube(name="nukeSpot")[0]
    cmds.move(4, 0, -6, target)
    cmds.select(target, cam)
    root = run_tool(nuke.NukeTool, start_frame=3.0, stem_count=6, cap_count=8, base_count=5,
                    rise_frames=30.0, life_frames=60.0, cool_frames=20.0, shock_frames=12.0,
                    fireball_frames=20.0)
    root = cmds.ls(root, long=True)[0]
    center = cmds.ls(wx.short(root) + "_center", long=True)[0]
    check("核爆発: 単体で作れる (爆心のロケータは選んだ物の位置。カメラとつながる)",
          near(cmds.xform(center, q=True, ws=True, t=True), (4, 0, -6))
          and wx.find_link(cmds, root, wx.CAMERA_ATTR) is not None
          and catalog.spec("nuke").group == catalog.BURST)
    cmds.setAttr(center + ".rotateZ", 15)
    cmds.setAttr(center + ".rotateY", 30)
    v = mu.read_param_values(root, nuke.KIND.stored_params())

    def group(name):
        return cmds.listRelatives("%s|%s_%s" % (root, wx.short(root), name), children=True, fullPath=True) or []

    def same(node, p, rows, size, vis, tol=2e-3):
        if bool(cmds.getAttr(node + ".visibility")) != bool(vis):
            return False
        if not vis or size < 1e-6:
            return True
        m = cmds.getAttr(node + ".worldMatrix[0]")
        if not near((m[12], m[13], m[14]), p, tol):
            return False
        for r in range(3):
            row = m[4 * r:4 * r + 3]
            if abs(_mag(row) - size) > tol:
                return False
            if rows is not None and not near(_unit(row), rows[r], 1e-3):
                return False
        return True

    bad = []
    for f in (3, 4, 6, 9, 15, 25, 40, 58, 70):
        pos(cam, f)
        m = cmds.getAttr(center + ".worldMatrix[0]")
        p0 = (m[12], m[13], m[14])
        axes = tuple(_unit(m[4 * r:4 * r + 3]) for r in range(3))
        cm = cmds.getAttr(cam + ".worldMatrix[0]")
        cp, cdir = (cm[12], cm[13], cm[14]), (-cm[8], -cm[9], -cm[10])
        p, sz, vis = mo.nuke_flash(f, v, p0, axes)
        if not same(group("flash")[0], p, mo.billboard(p, cp, cdir), sz, vis):
            bad.append(("flash", f))
        p, rows, sz, vis, _ = mo.nuke_shock(f, v, p0, axes)
        if not same(group("shock")[0], p, rows, sz, vis):
            bad.append(("shock", f))
        p, rows, sz, vis, _ = mo.nuke_dome(f, v, p0, axes)
        if not same(group("dome")[0], p, rows, sz, vis):
            bad.append(("dome", f))
        p, sz, vis, rot = mo.nuke_fireball(f, v, p0, axes)
        if not same(group("fireball")[0], p, rotation_rows(*rot), sz, vis):
            bad.append(("fireball", f))
        for name, fn, count in (("stem", mo.nuke_stem, 6), ("cap", mo.nuke_cap, 8), ("base", mo.nuke_base, 5)):
            nodes = group(name)
            if len(nodes) != count:
                bad.append((name, "count", len(nodes)))
                continue
            for j in range(count):
                p, sz, vis, rot = fn(j, count, f, v, p0, axes)
                if not same(nodes[j], p, rotation_rows(*rot), sz, vis):
                    bad.append((name, f, j))
    check("核爆発: 閃光・衝撃波・ドーム・火の玉・柱・かさ・根元が motion.py と同じ (回した爆心)", not bad, str(bad[:4]))

    pos(cam, 40)
    cap = [cmds.getAttr(n + ".worldMatrix[0]")[13] for n in group("cap")]
    stem = [cmds.getAttr(n + ".worldMatrix[0]")[13] for n in group("stem")]
    check("核爆発: かさは柱より上にある (キノコ雲)", min(cap) > max(stem) * 0.6, str((min(cap), max(stem))))
    pos(cam, 1)
    hidden = all(not cmds.getAttr(n + ".visibility") for n in group("cap") + group("flash") + group("stem"))
    check("核爆発: 開始フレームより前は何も見えない", hidden)

    ramp = cmds.ls(wx.short(root) + "_cloud_mat_toon")[0]
    shader = cmds.ls(wx.short(root) + "_cloud_mat")[0]
    bad = []
    for f in (3, 13, 30, 52):
        pos(cam, f)
        color, shade, alpha = mo.nuke_colors(f, v)
        got_c = cmds.getAttr(ramp + ".colorEntryList[1].color")[0]
        got_s = cmds.getAttr(ramp + ".colorEntryList[0].color")[0]
        got_t = cmds.getAttr(shader + ".outTransparencyR")
        if not near(got_c, color, 1e-4) or not near(got_s, shade, 1e-4) or abs(got_t - (1 - alpha)) > 1e-4:
            bad.append((f, got_c, color, got_t, alpha))
    check("核爆発: 雲の色は火の色から煙の色へ、最後に薄れる (motion.py と同じ)", not bad, str(bad[:2]))
    pos(cam, 6)
    shock_t = cmds.getAttr(cmds.ls(wx.short(root) + "_shock_mat")[0] + ".outTransparencyR")
    check("核爆発: 衝撃波の輪は広がりながら薄れる", abs(shock_t - (1 - mo.nuke_shock(6, v, (0, 0, 0), ((1, 0, 0), (0, 1, 0), (0, 0, 1)))[4])) < 1e-4)

    cmds.setAttr(root + ".dfx_size", 20.0)
    pos(cam, 25)
    v2 = dict(v, size=20.0)
    m = cmds.getAttr(center + ".worldMatrix[0]")
    p, sz, vis, rot = mo.nuke_cap(0, 8, 25, v2, (m[12], m[13], m[14]), tuple(_unit(m[4 * r:4 * r + 3]) for r in range(3)))
    check("核爆発: 大きさをすぐ反映 (チャンネルボックス)", same(group("cap")[0], p, None, sz, vis))
    cmds.setAttr(root + ".dfx_cap_count", 12)
    cmds.select(root)
    run_tool(nuke.NukeUpdateTool)
    check("核爆発: 更新で煙の数が変わる (爆心のロケータはそのまま)", len(group("cap")) == 12
          and near(cmds.getAttr(center + ".translate")[0], (4, 0, -6)) and abs(cmds.getAttr(center + ".rotateY") - 30) < 1e-6,
          str((len(group("cap")), cmds.getAttr(center + ".translate")[0], cmds.getAttr(center + ".rotateY"))))

    cmds.playbackOptions(minTime=1, maxTime=60)
    shot = export_shot.build_shot(cmds, mu, [root], 1, 60)
    eff = shot["effects"][0]
    check("核爆発: 書き出し (種類・パラメータ・爆心)", eff["type"] == "nuke" and eff["params"]["size"] == 20.0
          and any(c["name"].endswith("_center") for c in eff["controls"]))
    cmds.select(root)
    run_tool(bake.BakeTool)
    baked = mu.is_baked(root)
    pos(cam, 30)
    exprs = cmds.ls(type="expression") or []
    check("核爆発: ベイクできる (エクスプレッションが無くなり、雲の色もキーになる)",
          baked and not [e for e in exprs if e.startswith(wx.short(root))]
          and cmds.keyframe(ramp + ".colorEntryList[1].colorR", query=True, keyframeCount=True) > 0,
          str((baked, [e for e in exprs if e.startswith(wx.short(root))][:3])))


@section
def t44_lightning():
    """稲妻 (実写・アニメ): motion.py と同じ形・伸び方・ちらつき、落ちる所、更新、ベイク。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import lightning as lt, _weather as wx, bake
    cam = _weather_scene()
    target = cmds.polyCube(name="strikeSpot")[0]
    cmds.move(3, 0, -4, target)
    for tool, kind, style, kw in ((lt.LightningRealTool, lt.REAL, mo.STYLE_REAL,
                                   dict(count=4, interval=10.0, segments=20, branches=3, branch_segments=6,
                                        flicker=0.5)),
                                  (lt.LightningAnimeTool, lt.ANIME, mo.STYLE_ANIME,
                                   dict(count=4, interval=10.0))):
        cmds.select(target, cam)
        root = cmds.ls(run_tool(tool, **kw), long=True)[0]
        r = wx.short(root)
        top = cmds.ls(r + "_top", long=True)[0]
        gnd = cmds.ls(r + "_ground", long=True)[0]
        v = mu.read_param_values(root, kind.stored_params())
        n, nb, m = int(v["segments"]), int(v["branches"]), int(v["branch_segments"])
        rn, rm = mo.strand_rows(style, n), mo.strand_rows(style, m)
        joints = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
        check(u"%s: 作成 (雲の点・落ちる所 (選んだ物の位置)・帯 2 本・点の数)" % tool.label,
              near(cmds.xform(gnd, q=True, ws=True, t=True), (3, 0, -4))
              and near(cmds.xform(top, q=True, ws=True, t=True), (3, 40.0, -4))
              and len(joints) == rn + nb * rm and cmds.objExists(r + "_bolt")
              and cmds.objExists(r + "_glow"))
        if kind is lt.ANIME:
            check("稲妻 (アニメ): 円柱 (点ごとに分割数の輪。両端はふた)",
                  cmds.polyEvaluate(r + "_bolt", vertex=True) == len(joints) * int(v["sides"])
                  and cmds.polyEvaluate(r + "_bolt", face=True) == (rn - 1 + nb * (rm - 1)) * int(v["sides"]) + 2 * (1 + nb))
        cmds.setAttr(top + ".translateX", -2)
        bad = []
        frames = [1, 2, 3, 4, 6, 9, 12, 13, 15, 22, 33, 45]
        visible_frames = 0
        for f in frames:
            pos(cam, f)
            tp = cmds.xform(top, q=True, ws=True, t=True)
            gp = cmds.xform(gnd, q=True, ws=True, t=True)
            cm = cmds.getAttr(cam + ".worldMatrix[0]")
            rows, alpha = mo.lightning_rows(style, f, v, tuple(tp), tuple(gp), (cm[12], cm[13], cm[14]),
                                            (-cm[8], -cm[9], -cm[10]), n, nb, m)
            if alpha > 0:
                visible_frames += 1
            for j, (p, axes, w) in enumerate(rows):
                mm = cmds.getAttr(joints[j] + ".worldMatrix[0]")
                got_w = _mag(mm[0:3])
                if abs(got_w - w) > 1e-4:
                    bad.append(("w", f, j, got_w, w))
                    continue
                if w > 1e-6 and (not near((mm[12], mm[13], mm[14]), p, 2e-3)
                                 or not all(near(_unit(mm[4 * k:4 * k + 3]), axes[k], 1e-3) for k in range(3))):
                    bad.append(("p", f, j))
            shader = cmds.ls(r + "_bolt_mat")[0]
            if abs(cmds.getAttr(shader + ".outTransparencyR") - (1 - alpha)) > 1e-4:
                bad.append(("alpha", f))
            glow = cmds.ls(r + "_glow_mat")[0]
            if abs(cmds.getAttr(glow + ".outTransparencyR") - (1 - alpha * v["glow_opacity"])) > 1e-4:
                bad.append(("glow", f))
            if kind is lt.ANIME and alpha > 0:
                # 芯の帯は円柱の表面のすぐ前 (カメラの方)
                for j, (p, axes, w) in enumerate(rows[:rn]):
                    if w < 1e-6:
                        continue
                    q = cmds.pointPosition("%s.vtx[%d]" % (r + "_glow", 2 * j), world=True)
                    d = [a - b for a, b in zip(q, p)]
                    if abs(sum(a * b for a, b in zip(d, axes[2])) - lt.CORE_FRONT * w) > 2e-3:
                        bad.append(("core", f, j))
                        break
                # 円柱: 輪の頂点は点から太さの半分の所、進む向きに垂直 (どこから見ても太さがある)
                sides = int(v["sides"])
                for j, (p, axes, w) in enumerate(rows[:rn]):
                    if w < 1e-6:
                        continue
                    for s in range(sides):
                        q = cmds.pointPosition("%s.vtx[%d]" % (r + "_bolt", j * sides + s), world=True)
                        d = [a - b for a, b in zip(q, p)]
                        if abs(_mag(d) - w / 2) > 2e-3 or abs(sum(a * b for a, b in zip(d, axes[0]))) > 2e-3:
                            bad.append(("ring", f, j, s))
                            break
        check(u"%s: 形・伸び方・太さ・ちらつきが motion.py と同じ (動かした雲の点)" % tool.label,
              not bad and visible_frames >= 4, str((bad[:3], visible_frames)))
        if kind is lt.REAL:
            real_root = root
    # 伸び方: 1 フレーム目は途中まで、伸び切ると地面に届く
    v = mu.read_param_values(real_root, lt.REAL.stored_params())
    r = wx.short(real_root)
    joints = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
    pos(cam, 1)
    tip1 = [j for j in joints[:20] if _mag(cmds.getAttr(j + ".worldMatrix[0]")[0:3]) > 1e-6]
    pos(cam, 3)
    tip3 = [j for j in joints[:20] if _mag(cmds.getAttr(j + ".worldMatrix[0]")[0:3]) > 1e-6]
    low = cmds.getAttr(joints[19] + ".worldMatrix[0]")[13]
    check("稲妻: 雲から伸びて (最初は途中まで)、伸び切ると地面に届く", len(tip1) == 1 and len(tip3) == 20 and abs(low) < 1e-3,
          str((len(tip1), len(tip3), low)))
    pos(cam, 8)
    check("稲妻: 光っていない間は見えない (太さ 0・透明)",
          all(_mag(cmds.getAttr(j + ".worldMatrix[0]")[0:3]) < 1e-9 for j in joints)
          and cmds.getAttr(r + "_bolt_mat.outTransparencyR") == 1.0)
    cmds.setAttr(real_root + ".dfx_segments", 30)
    cmds.select(real_root)
    run_tool(__import__("dummy_effect_maker.tools.weather", fromlist=["x"]).WeatherUpdateTool)
    joints = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
    check("稲妻: 「天候を更新」で点の数が変わる (雲の点・落ちる所はそのまま)",
          len(joints) == 30 + 3 * 6 and abs(cmds.getAttr(r + "_top.translateX") + 2) < 1e-9)
    check("稲妻: カタログ (天候の粒・アルファ カットの注意の対象)",
          catalog.spec("lightning_real").group == catalog.PARTICLES and "lightning_anime" in wx.TRANSLUCENT_TYPES)
    cmds.select(real_root)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(r)]
    check("稲妻: ベイクできる (エクスプレッションが無くなり、濃さもキーになる)", mu.is_baked(real_root) and not exprs
          and cmds.keyframe(r + "_bolt_mat.outTransparencyR", query=True, keyframeCount=True) > 0, str(exprs[:2]))


@section
def t45_attribute_editor():
    """専用のアトリビュートエディタ: 選んだエフェクトの表示・書き込み・アンドゥ・キー・更新・つながり。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from dummy_effect_maker.ui.qt import QtWidgets
    from dummy_effect_maker.ui.main_window import MainWindow
    from dummy_effect_maker.tools import weather, _weather as wx, bake
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    cam = _weather_scene()
    cmds.select(cam)
    rain = cmds.ls(run_tool(weather.RainTool, count=20), long=True)[0]
    win = MainWindow(HOST)
    win.show()
    ed = win.attribute_editor
    check("エディタ: ランチャーの右にある (表示メニューで出し入れ)", win.side_splitter.indexOf(ed) == 1
          and ed.backend is not None and win.attribute_editor_action.isChecked())
    cmds.select(clear=True)
    ed.refresh()
    check("エディタ: 何も選ばなければ案内だけ", ed.root is None and not ed.rows and u"選ぶと" in ed.note.text())
    area = cmds.ls(wx.short(rain) + "_area", long=True)[0]
    cmds.select(area)   # 中の物を選んでもそのルート
    ed.refresh()
    p_speed = ed.rows["speed"]
    check("エディタ: 中の物を選んでもルートのパラメータ (日本語の名前・(更新) の区別)",
          ed.root == rain and u"雨" in ed.header.text() and "count" in ed.rows and "speed" in ed.rows
          and ed.rows["count"][4] is None and ed.rows["speed"][4] is not None
          and abs(p_speed[1]() - cmds.getAttr(rain + ".dfx_speed")) < 1e-6)
    # すぐ反映の数値を変える → アトリビュートが変わる。1 回のアンドゥで戻る
    p_speed[2](2.5)
    ed.flush()
    ok = abs(cmds.getAttr(rain + ".dfx_speed") - 2.5) < 1e-9
    cmds.undo()
    ed.refresh()
    check("エディタ: 数値を変えるとすぐ反映、1 回のアンドゥで戻る (表示も戻る)",
          ok and abs(cmds.getAttr(rain + ".dfx_speed") - 1.5) < 1e-9 and abs(ed.rows["speed"][1]() - 1.5) < 1e-9)
    # 色・選択肢・チェック
    ed.rows["color"][2]((1.0, 0.0, 0.0))
    ed._commit("color")
    check("エディタ: 色も書ける", near(cmds.getAttr(rain + ".dfx_color")[0], (1.0, 0.0, 0.0), 1e-6))
    # チャンネルボックスなどで変えた値を拾う
    cmds.setAttr(rain + ".dfx_opacity", 0.3)
    ed.refresh()
    check("エディタ: ほかで変えた値も表示に出る", abs(ed.rows["opacity"][1]() - 0.3) < 1e-6)
    # キー: ◆ で打つ / 消す、色で分かる
    ed._on_key(ed.rows["amount"][0])
    state1 = ed.rows["amount"][4].property("dfx_state")
    cmds.currentTime(10)
    ed.refresh()
    state2 = ed.rows["amount"][4].property("dfx_state")
    cmds.currentTime(1)
    ed.refresh()
    ed._on_key(ed.rows["amount"][0])
    check("エディタ: ◆ でキー (このフレーム = 赤、ほかのフレーム = 桃、もう一度で消す)",
          state1 == "key" and state2 == "animated"
          and not cmds.keyframe(rain + ".dfx_amount", query=True, keyframeCount=True), str((state1, state2)))
    # 「(更新)」の項目 → 更新ボタンで作り直す
    ed.rows["count"][2](8)
    ed._commit("count")
    pending = u"更新" in ed.note.text()
    ed._on_update()
    drops = cmds.listRelatives(rain + "|" + wx.short(rain) + "_drops", children=True) or []
    check("エディタ: 「(更新)」を変えて「更新」ボタンで作り直す (範囲の箱はそのまま)",
          pending and len(drops) == 8 and cmds.objExists(area) and ed.root == rain, str(len(drops)))
    # つながり: 範囲の箱・カメラを選べる
    texts = [ed.links_layout.itemAt(i).widget().text() for i in range(ed.links_layout.count())]
    check("エディタ: 動かす物・つながった物のボタン", any(t.endswith("_area") for t in texts)
          and any(u"カメラ" in t for t in texts), str(texts))
    # 2 つ選ぶと最初の 1 つ
    cmds.select(cam)
    snow = cmds.ls(run_tool(weather.SnowTool, count=5), long=True)[0]
    cmds.select(rain, snow)
    ed.refresh()
    check("エディタ: 複数選ぶと最初の 1 つ (と、その旨)", ed.root == rain and u"2 個" in ed.note.text())
    cmds.select(snow)
    ed.refresh()
    check("エディタ: 選び直すと切り替わる", ed.root == snow and u"雪" in ed.header.text() and "sway" in ed.rows)
    cmds.select(snow)
    run_tool(bake.BakeTool)
    cmds.select(snow)
    ed.refresh()
    check("エディタ: ベイク済みは変えられない (その旨を表示)", u"ベイク済み" in ed.note.text()
          and not ed.rows["sway"][3].isEnabled() and not ed.update_button.isEnabled())
    # ロック: 選択を外しても・変えても、そのエフェクトを表示し続ける
    from dummy_effect_maker.ui import attribute_editor as ae
    cmds.select(clear=True)
    ed.refresh()
    disabled = not ed.lock_button.isEnabled()
    cmds.select(rain)
    ed.refresh()
    ed.lock_button.click()
    cmds.select(clear=True)
    ed.refresh()
    kept = ed.root == rain and "speed" in ed.rows
    cmds.select(snow)
    ed.refresh()
    kept = kept and ed.root == rain
    style = ed.panel.styleSheet()
    check("ロック: 選択を外しても・変えても表示を続ける (黄色い枠・ボタンは黄色で「ロック中」)",
          disabled and kept and ed.is_locked() and ae.LOCK_YELLOW in style and "border" in style
          and ed.lock_button.text() == ae.LOCKED_TEXT and ae.LOCK_YELLOW in ed.lock_button.styleSheet()
          and u"ロック中" in ed.note.text(), str((disabled, kept, style)))
    ed.rows["speed"][2](3.0)
    ed.flush()
    check("ロック: 選んでいなくても書き込める", abs(cmds.getAttr(rain + ".dfx_speed") - 3.0) < 1e-9)
    ed.lock_button.click()
    ed.refresh()
    check("ロック: 外すと選択 (雪) に戻り、黄色い枠も消える", not ed.is_locked() and ed.root == snow
          and ae.LOCK_YELLOW not in ed.panel.styleSheet() and ed.lock_button.text() == ae.LOCK_TEXT)
    cmds.select(rain)
    ed.refresh()
    ed.lock_button.click()
    cmds.select(clear=True)
    cmds.delete(rain)
    ed.refresh()
    check("ロック: ロックしたエフェクトを消すと、自動で外れる", not ed.is_locked() and ed.root is None
          and not ed.lock_button.isChecked())
    # 保存・読み込み (ロックの隣のボタン。全部の種類で共通)
    import shutil
    app.processEvents()
    g = [b.geometry() for b in (ed.save_button, ed.load_button, ed.lock_button)]
    check("保存・読み込み: ロックの隣にボタン (保存 → 読み込み → ロックの順)",
          g[0].x() < g[1].x() < g[2].x() and g[0].y() == g[1].y() == g[2].y()
          and ed.save_button.parentWidget() is ed.lock_button.parentWidget(), str([(r.x(), r.y()) for r in g]))
    cmds.select(snow)
    ed.refresh()
    check("保存・読み込み: ベイク済みは保存だけできる", ed.save_button.isEnabled() and not ed.load_button.isEnabled())
    cmds.select(clear=True)
    ed.refresh()
    check("保存・読み込み: 何も表示していなければ押せない",
          not ed.save_button.isEnabled() and not ed.load_button.isEnabled())
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"   # ユーザーのマイドキュメントを汚さない
    try:
        cmds.select(cam)
        rain = cmds.ls(run_tool(weather.RainTool, count=10), long=True)[0]
        cmds.setAttr(rain + ".dfx_speed", 5.0)
        cmds.select(cam)
        rain2 = cmds.ls(run_tool(weather.RainTool, count=10), long=True)[0]
        cmds.select(rain, rain2)   # 2 つ選んでいても、保存は表示中の 1 つ
        ed.refresh()
        dialog.answer = os.path.join(tmp, "fast_rain")
        ed.save_button.click()
        saved = dialog.answer + ".json"
        call = dialog.calls[-1]
        check("保存・読み込み: 「保存」で表示中のエフェクトを保存 (種類ごとのフォルダ)", os.path.isfile(saved)
              and call["fileMode"] == 0 and call["startingDirectory"].replace("\\", "/").endswith("presets/rain"),
              str(call))
        cmds.setAttr(rain + ".dfx_speed", 1.0)
        cmds.setAttr(rain2 + ".dfx_speed", 2.0)
        cmds.select(rain, rain2)
        ed.refresh()
        dialog.answer = saved
        ed.load_button.click()
        both = (cmds.getAttr(rain + ".dfx_speed"), cmds.getAttr(rain2 + ".dfx_speed"))
        cmds.undo()
        undone = (cmds.getAttr(rain + ".dfx_speed"), cmds.getAttr(rain2 + ".dfx_speed"))
        check("保存・読み込み: 「読み込み」で選んでいる同じ種類全部に読み込み、1 回のアンドゥで戻る",
              both == (5.0, 5.0) and undone == (1.0, 2.0) and dialog.calls[-1]["fileMode"] == 1, str((both, undone)))
        cmds.select(rain)
        ed.refresh()
        ed.lock_button.click()
        cmds.select(rain2)
        ed.refresh()
        ed.load_button.click()
        check("保存・読み込み: ロック中は表示中の 1 つだけに読み込む",
              cmds.getAttr(rain + ".dfx_speed") == 5.0 and cmds.getAttr(rain2 + ".dfx_speed") == 2.0
              and ed.is_locked() and ed.root == rain)
        ed.lock_button.click()
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var
        shutil.rmtree(tmp, ignore_errors=True)
    # ほかの種類も表示できる
    from dummy_effect_maker import catalog
    from dummy_effect_maker.tools import homing, vulcan, energy_ball, shockwave, nuke, lightning, camera_shake, focus_lines
    from dummy_effect_maker.tools._attr_backend import update_tool_name
    bad = []
    for tool in (homing.HomingTool, vulcan.VulcanTool, energy_ball.EnergyBallTool, shockwave.ShockwaveTool,
                 nuke.NukeTool, lightning.LightningAnimeTool, camera_shake.CameraShakeTool, focus_lines.FocusLinesTool):
        cmds.select(cam)
        result = run_tool(tool)
        root = cmds.ls(result[0] if isinstance(result, list) else result, long=True)[0]
        cmds.select(root)
        ed.refresh()
        effect_type = cmds.getAttr(root + ".dfxEffectType")
        spec = catalog.spec(effect_type)
        names = [p.name for p in spec.params if cmds.attributeQuery("dfx_" + p.name, node=root, exists=True)]
        tool_names = [type(t).__name__ for t in win.tools]
        if ed.root != root or sorted(ed.rows) != sorted(names) or update_tool_name(effect_type) not in tool_names:
            bad.append((effect_type, len(ed.rows), len(names)))
    check("エディタ: 追尾弾・バルカン砲・エネルギー弾・衝撃波・核爆発・稲妻・画ぶれ・集中線も表示 (更新ツールもある)",
          not bad, str(bad))
    win.close()
    check("エディタ: 閉じると監視をやめる (scriptJob が残らない)", not ed.backend._jobs)


@section
def t46_explosion_single():
    """爆発 (単体): コリジョン無しで作れる・motion.py と同じ・爆心を動かすと付いてくる・更新・ベイク。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import explosion as ex, explosion_single as es, _weather as wx, bake
    cam = _weather_scene()
    check("爆発 (単体): シーンにコリジョンが無い", not ex.colliders(cmds))
    spot = cmds.polyCube(name="burstSpot")[0]
    cmds.move(2, 1, -3, spot)
    cmds.select(spot, cam)
    root = cmds.ls(run_tool(es.ExplosionSingleTool, start_frame=4.0, fire_count=5, smoke_count=4, spark_count=6),
                   long=True)[0]
    r = wx.short(root)
    center = cmds.ls(r + "_center", long=True)[0]
    check("爆発 (単体): 作れる (爆心は選んだ物の位置・カメラとつながる)",
          near(cmds.xform(center, q=True, ws=True, t=True), (2, 1, -3))
          and wx.find_link(cmds, root, wx.CAMERA_ATTR) is not None and catalog.spec(es.EFFECT_TYPE).group == catalog.BURST)
    cmds.setAttr(center + ".rotateX", 90)   # 横 (+Z) を向けて爆発させる
    v = mu.read_param_values(root, es.KIND.stored_params())

    def group(name):
        return cmds.listRelatives("%s|%s_%s" % (root, r, name), children=True, fullPath=True) or []

    def same(node, p, rows, size_rows, vis, tol=2e-3):
        if bool(cmds.getAttr(node + ".visibility")) != bool(vis):
            return False
        if not vis:
            return True
        m = cmds.getAttr(node + ".worldMatrix[0]")
        if min(abs(x) for x in size_rows) < 1e-6:   # 出た瞬間 (大きさ 0)
            return all(_mag(m[4 * k:4 * k + 3]) < tol for k in range(3) if abs(size_rows[k]) < 1e-6)
        if not near((m[12], m[13], m[14]), p, tol):
            return False
        for k in range(3):
            row = m[4 * k:4 * k + 3]
            if abs(_mag(row) - abs(size_rows[k])) > tol:
                return False
            if rows is not None and abs(size_rows[k]) > 1e-6 and not near(_unit(row), rows[k], 1e-3):
                return False
        return True

    from dummy_effect_maker.tools.ghost_blur import rotation_rows
    bad = []
    for f in (3, 4, 5, 7, 10, 16, 30):
        pos(cam, f)
        m = cmds.getAttr(center + ".worldMatrix[0]")
        hit = es.hit_of(v, (m[12], m[13], m[14]), _unit(m[4:7]))
        cm = cmds.getAttr(cam + ".worldMatrix[0]")
        cp, cdir = (cm[12], cm[13], cm[14]), (-cm[8], -cm[9], -cm[10])
        p, sz, vis, roll = mo.explosion_flash(0, f, hit, v)
        if not same(group("flashes")[0], p, mo.billboard(p, cp, cdir, roll), (sz,) * 3, vis):
            bad.append(("flash", f))
        p, rows, sz, vis = mo.explosion_ring(0, f, hit, v)
        if not same(group("rings")[0], p, rows, (sz,) * 3, vis):
            bad.append(("ring", f))
        for part, fn, per in (("fire", mo.explosion_fire, 5), ("smoke", mo.explosion_smoke, 4)):
            nodes = group(part)
            for j in range(per):
                p, sz, vis, rot = fn(0, j, f, hit, v)
                if not same(nodes[j], p, rotation_rows(*rot), (sz,) * 3, vis):
                    bad.append((part, f, j))
        for j, n in enumerate(group("sparks")):
            p, vel, scale, vis = mo.explosion_spark(0, j, f, hit, v)
            if not same(n, p, mo.streak(p, vel, cp, cdir), scale, vis):
                bad.append(("spark", f, j))
    check("爆発 (単体): 閃光・輪・火の玉・煙・火花が motion.py と同じ (回した爆心)", not bad, str(bad[:3]))
    pos(cam, 6)
    ring = cmds.getAttr(group("rings")[0] + ".worldMatrix[0]")
    check("爆発 (単体): 輪は爆心の上の向き (回した +Z) に沿って広がる", near(_unit(ring[8:11]), (0, 0, 1), 1e-3),
          str(ring[8:11]))
    pos(cam, 2)
    check("爆発 (単体): 開始フレームより前は見えない",
          not any(cmds.getAttr(n + ".visibility") for n in group("flashes") + group("fire") + group("sparks")))
    cmds.move(10, 0, 0, center)
    pos(cam, 8)
    p0 = cmds.getAttr(group("rings")[0] + ".worldMatrix[0]")[12:15]
    check("爆発 (単体): 爆心を動かすと付いてくる (作り直さなくても)", abs(p0[0] - 10) < 0.2, str(p0))
    cmds.setAttr(root + ".dfx_fire_count", 9)
    cmds.select(root)
    run_tool(ex.ExplosionUpdateTool)
    check("爆発 (単体): 「着弾爆発を更新」で選んでも数が変わる (爆心はそのまま)", len(group("fire")) == 9
          and abs(cmds.getAttr(center + ".translateX") - 10) < 1e-9 and not ex.colliders(cmds))
    from dummy_effect_maker.tools import explosion_single as _es
    from dummy_effect_maker.tools._attr_backend import update_tool_name
    cmds.setAttr(root + ".dfx_fire_count", 4)
    cmds.select(root)
    run_tool(_es.ExplosionSingleUpdateTool)
    check("爆発 (単体): 爆発タブの「爆発 (単体) を更新」で数が変わる (アトリビュートエディタの更新もこれ)",
          len(group("fire")) == 4 and abs(cmds.getAttr(center + ".translateX") - 10) < 1e-9
          and update_tool_name("explosion_single") == "ExplosionSingleUpdateTool"
          and _es.ExplosionSingleUpdateTool.tab == u"爆発")
    cmds.select(root)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(r)]
    check("爆発 (単体): ベイクできる", mu.is_baked(root) and not exprs, str(exprs[:2]))


@section
def t47_discharge():
    """放電: 2 つのロケータの間に流れ続ける (キーで動く)。終端の側がコリジョンに当たると小爆発。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import lightning as lt, explosion as ex, _weather as wx, weather, export_shot, bake
    from dummy_effect_maker.tools.ghost_blur import rotation_rows
    cam = _weather_scene()
    cmds.select(cam)
    lone = cmds.ls(run_tool(lt.DischargeRealTool, end_frame=20.0), long=True)[0]
    check("放電: コリジョンが無くても作れる (爆発は無し)", not ex.read_hits(cmds, lone)
          and not cmds.ls(wx.short(lone) + "_flashes"))
    cmds.delete(lone)
    ground = cmds.polyPlane(width=60, height=60, name="ground")[0]
    cmds.select(ground)
    run_tool(ex.ColliderSetTool)
    cmds.select(cam)
    root = cmds.ls(run_tool(lt.DischargeAnimeTool, start_frame=1.0, end_frame=60.0, burst_interval=4.0, reach=0.5,
                            fire_count=3, smoke_count=2, spark_count=3), long=True)[0]
    r = wx.short(root)
    start = cmds.ls(r + "_start", long=True)[0]
    end = cmds.ls(r + "_end", long=True)[0]
    # 発生は空中を少し動き、終端は地面の少し下を横に走ってから (41 フレーム〜) 地面から上がる
    for t, sx, ex_, ey in ((1, -12, -10, -0.2), (40, -8, 10, -0.2), (41, -8, 10, 3.0), (60, -8, 10, 3.0)):
        cmds.setKeyframe(start, attribute="translateX", time=t, value=sx)
        cmds.setKeyframe(start, attribute="translateY", time=t, value=8)
        cmds.setKeyframe(end, attribute="translateX", time=t, value=ex_)
        cmds.setKeyframe(end, attribute="translateY", time=t, value=ey)
    cmds.keyTangent([start, end], inTangentType="linear", outTangentType="linear")
    cmds.select(root)
    run_tool(weather.WeatherUpdateTool)
    v = mu.read_param_values(root, lt.DISCHARGE_ANIME.stored_params())
    hits = ex.read_hits(cmds, root)
    expect = []
    for t in mo.discharge_samples(v):
        a = cmds.getAttr(start + ".worldMatrix[0]", time=t)[12:15]
        b = cmds.getAttr(end + ".worldMatrix[0]", time=t)[12:15]
        p0, p1 = mo.discharge_probe(tuple(a), tuple(b), v["reach"])
        if p0[1] > 0 >= p1[1]:
            k = p0[1] / (p0[1] - p1[1])
            expect.append((t, tuple(x + (y - x) * k for x, y in zip(p0, p1))))
    ok = len(hits) == len(expect) and len(hits) >= 8 and all(
        abs(h["frame"] - t) < 1e-6 and near(h["pos"], p, 1e-3) and near(h["normal"], (0, 1, 0), 1e-4)
        for h, (t, p) in zip(hits, expect))
    check("放電: 終端の側が地面に当たっている間、爆発の間隔ごとに当たる (位置・向き・フレーム)", ok,
          str((len(hits), len(expect), hits[:1], expect[:1])))
    check("放電: 地面から離れた後 (41 フレーム〜) は当たらない", all(h["frame"] < 41 for h in hits))
    joints = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
    n, nb, m = int(v["segments"]), int(v["branches"]), int(v["branch_segments"])
    bad = []
    for f in (1, 2, 5, 17, 33, 47, 60, 61):
        pos(cam, f)
        tp = cmds.xform(start, q=True, ws=True, t=True)
        gp = cmds.xform(end, q=True, ws=True, t=True)
        cm = cmds.getAttr(cam + ".worldMatrix[0]")
        rows, alpha = mo.discharge_rows(mo.STYLE_ANIME, f, v, tuple(tp), tuple(gp), (cm[12], cm[13], cm[14]),
                                        (-cm[8], -cm[9], -cm[10]), n, nb, m)
        if alpha > 0 and not (1 <= f <= 60):   # 放電の間だけ光る (間でもちらつきで消えるフレームはある)
            bad.append(("alpha", f))
        for j, (p, axes, w) in enumerate(rows):
            mm = cmds.getAttr(joints[j] + ".worldMatrix[0]")
            if abs(_mag(mm[0:3]) - w) > 1e-4 or (w > 1e-6 and not near((mm[12], mm[13], mm[14]), p, 2e-3)):
                bad.append((f, j))
                break
        if abs(cmds.getAttr(r + "_bolt_mat.outTransparencyR") - (1 - alpha)) > 1e-4:
            bad.append(("t", f))
    pos(cam, 61)
    gone = all(_mag(cmds.getAttr(j + ".worldMatrix[0]")[0:3]) < 1e-9 for j in joints)
    check("放電: 流れ続ける形・太さ・濃さが motion.py と同じ (動くロケータ)、終わったら消える", not bad and gone, str(bad[:3]))
    pos(cam, 2)
    first = cmds.getAttr(joints[0] + ".worldMatrix[0]")[12:15]
    last = cmds.getAttr(joints[mo.strand_rows(mo.STYLE_ANIME, n) - 1] + ".worldMatrix[0]")[12:15]
    check("放電: 発生から終端までつながる (伸びるのを待たない)",
          _mag([a - b for a, b in zip(first, cmds.xform(start, q=True, ws=True, t=True))]) < v["spread"] + 1e-3
          and _mag([a - b for a, b in zip(last, cmds.xform(end, q=True, ws=True, t=True))]) < 2 * v["spread"] + 1e-3)
    # 小爆発は爆発と同じ部品・同じ計算
    flashes = cmds.listRelatives("%s|%s_flashes" % (root, r), children=True, fullPath=True)
    fire = cmds.listRelatives("%s|%s_fire" % (root, r), children=True, fullPath=True)
    bad = []
    for f in (int(hits[0]["frame"]) + d for d in (1, 2, 5)):
        pos(cam, f)
        cm = cmds.getAttr(cam + ".worldMatrix[0]")
        cp, cdir = (cm[12], cm[13], cm[14]), (-cm[8], -cm[9], -cm[10])
        for e, hit in enumerate(hits[:3]):
            p, sz, vis, roll = mo.explosion_flash(e, f, hit, v)
            mm = cmds.getAttr(flashes[e] + ".worldMatrix[0]")
            if bool(cmds.getAttr(flashes[e] + ".visibility")) != vis or (vis and sz > 1e-6 and not near(mm[12:15], p, 2e-3)):
                bad.append(("flash", f, e))
            for j in range(3):
                p, sz, vis, rot = mo.explosion_fire(e, j, f, hit, v)
                mm = cmds.getAttr(fire[e * 3 + j] + ".worldMatrix[0]")
                if bool(cmds.getAttr(fire[e * 3 + j] + ".visibility")) != vis or \
                        (vis and sz > 1e-6 and (not near(mm[12:15], p, 2e-3) or abs(_mag(mm[0:3]) - sz) > 2e-3)):
                    bad.append(("fire", f, e, j))
    check("放電: 小爆発は爆発と同じ部品・同じ計算 (当たった所ごと)", len(flashes) == len(hits) and not bad, str(bad[:3]))
    # 終端の動きを変えて更新 → 爆発の場所が変わる
    cmds.setKeyframe(end, attribute="translateY", time=1, value=3.0)
    cmds.setKeyframe(end, attribute="translateY", time=20, value=3.0)
    cmds.setKeyframe(end, attribute="translateY", time=21, value=-0.2)
    cmds.select(root)
    run_tool(weather.WeatherUpdateTool)
    hits2 = ex.read_hits(cmds, root)
    check("放電: ロケータを動かして「天候を更新」で、当たる所を計算し直す", hits2 and min(h["frame"] for h in hits2) >= 21,
          str([h["frame"] for h in hits2][:5]))
    # 放電を選んで爆発タブの「爆発」「爆発を更新」を押しても、エラーにならず当たりを計算し直す
    ok = True
    for tool in (ex.ExplosionTool, ex.ExplosionUpdateTool):
        cmds.select(root)
        try:
            run_tool(tool)
        except ToolError:
            ok = False
    check("放電: 選んで「爆発」「爆発を更新」を押すと、当たりを計算し直す (弾を選べとは言わない)",
          ok and len(ex.read_hits(cmds, root)) == len(hits2) and not cmds.ls("dfx_explosion*", type="transform"))
    shot = export_shot.build_shot(cmds, mu, [root], 1, 60)
    eff = shot["effects"][0]
    check("放電: 書き出し (当たり・発生と終端のロケータ)", len(eff["data"]["hits"]) == len(hits2)
          and set(c["name"].split("_")[-1] for c in eff["controls"]) >= {"start", "end"}
          and catalog.spec("discharge_anime").group == catalog.PARTICLES)
    cmds.select(root)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(r)]
    check("放電: ベイクできる", mu.is_baked(root) and not exprs, str(exprs[:2]))


@section
def t48_wisp():
    """細い煙: motion.py と同じ動き (動く出る場所・風)、根元はまっすぐ、絵・UV、更新、ベイク。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import wisp, wind, weather, _weather as wx, bake
    cam = _weather_scene()
    cmds.select(clear=True)
    run_tool(wind.WindTool, strength=0.05, gust=0.0)
    stick = cmds.polyCube(name="incense", w=0.1, h=1, d=0.1)[0]
    cmds.setKeyframe(stick, attribute="translateX", time=1, value=0)
    cmds.setKeyframe(stick, attribute="translateX", time=60, value=3)
    cmds.select(stick, cam)
    root = cmds.ls(run_tool(wisp.WispTool, count=2, points=16, life=40.0, laminar=10.0), long=True)[0]
    r = wx.short(root)
    em = cmds.ls(r + "_emitter", long=True)[0]
    v = mu.read_param_values(root, wisp.KIND.stored_params())
    joints = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
    mesh = cmds.ls(r + "_strands", long=True)[0]
    shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
    tex = cmds.getAttr(cmds.ls(r + "_mat_file")[0] + ".fileTextureName")
    uvs = cmds.polyEditUV(mesh + ".map[0:%d]" % (2 * 16 - 1), query=True)
    check("細い煙: 作れる (筋 2 本 × 点 16・煙の絵・UV は根元 0 → 先 1・風とつながる)",
          len(joints) == 32 and tex.endswith("dfx_wisp_v2.png") and os.path.isfile(tex)
          and abs(uvs[0]) < 1e-6 and abs(uvs[-2] - 1) < 1e-6
          and wx.find_link(cmds, root, wx.WIND_ATTR) is not None and catalog.spec("wisp").group == catalog.PARTICLES,
          str((len(joints), tex, uvs[:2], uvs[-2:])))

    def wind_of():
        wr = wx.find_link(cmds, "|" + r, wx.WIND_ATTR)
        tabs = [cmds.getAttr(wr + "." + a) for a in wx.DRIFT_ATTRS + wx.WIND_VEC_ATTRS]
        return mo.Wind(list(zip(*tabs[:3])), list(zip(*tabs[3:])), cmds.getAttr(wr + "." + wx.DRIFT_START_ATTR),
                       cmds.getAttr(root + ".dfx_wind_influence"))

    at = lambda t: cmds.getAttr(em + ".worldMatrix[0]", time=t)  # noqa: E731
    wd = wind_of()
    bad = []
    for f in (1, 5, 20, 45, 59):
        pos(cam, f)
        cm = cmds.getAttr(cam + ".worldMatrix[0]")
        cp, cdir = (cm[12], cm[13], cm[14]), (-cm[8], -cm[9], -cm[10])
        for s in range(2):
            for j, (p, axes, w) in enumerate(mo.wisp_rows(s, f, v, 16, at, wd, cp, cdir)):
                mm = cmds.getAttr(joints[s * 16 + j] + ".worldMatrix[0]")
                if abs(_mag(mm[0:3]) - w) > 1e-4 or (w > 1e-6 and (not near(mm[12:15], p, 2e-3)
                                                                   or not near(_unit(mm[4:7]), axes[1], 1e-3))):
                    bad.append((f, s, j))
                    break
    check("細い煙: 筋の点 (位置・向き・太さ) が motion.py と同じ (キーで動く出る場所・風)", not bad, str(bad[:3]))
    pos(cam, 50)
    base = cmds.xform(em, q=True, ws=True, t=True)
    early = [cmds.getAttr(joints[j] + ".worldMatrix[0]")[12:15] for j in range(3)]
    tip = cmds.getAttr(joints[15] + ".worldMatrix[0]")[12:15]
    check("細い煙: 根元はまっすぐ上がり (横のずれは風と出る場所の動きだけ)、先ほど高く太い",
          early[0][1] < early[2][1] < tip[1] and abs(early[0][0] - base[0]) < 0.06
          and _mag(cmds.getAttr(joints[15] + ".worldMatrix[0]")[0:3]) > _mag(cmds.getAttr(joints[1] + ".worldMatrix[0]")[0:3]))
    cmds.setAttr(root + ".dfx_count", 3)
    cmds.setAttr(root + ".dfx_style", 1)
    cmds.select(root)
    run_tool(weather.WeatherUpdateTool)
    joints = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
    tex = cmds.getAttr(cmds.ls(r + "_mat_file")[0] + ".fileTextureName")
    check("細い煙: 「天候を更新」で筋の数・見た目 (アニメの絵) が変わる (出る場所はそのまま)",
          len(joints) == 48 and tex.endswith("dfx_wisp_anime_v2.png") and cmds.objExists(em), tex)
    cmds.select(root)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(r)]
    check("細い煙: ベイクできる", mu.is_baked(root) and not exprs, str(exprs[:2]))


@section
def t49_cloth():
    """波打つ布: 格子の点が motion.py と同じ、舞台でまとめて動く、両端、色・透明度、更新、ベイク。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import cloth, _weather as wx, bake
    cmds.file(new=True, force=True)
    cmds.playbackOptions(minTime=1, maxTime=60)
    spot = cmds.spaceLocator(name="seaSpot")[0]
    cmds.move(5, 0, -2, spot)
    cmds.select(spot)
    root = cmds.ls(run_tool(cloth.ClothTool, layers=3, divisions=8), long=True)[0]
    r = wx.short(root)
    stage = cmds.ls(r + "_stage", long=True)[0]
    v = mu.read_param_values(root, cloth.KIND.stored_params())
    layers = [cmds.ls("%s_layer_%02d" % (r, l + 1), long=True)[0] for l in range(3)]
    check("波打つ布: 布タブにある・舞台は選んだ物の位置・布 3 枚 (板・格子)",
          cloth.ClothTool.tab == u"布" and near(cmds.xform(stage, q=True, ws=True, t=True), (5, 0, -2))
          and all(cmds.listRelatives(g, parent=True, fullPath=True)[0] == stage for g in layers)
          and catalog.spec("cloth_waves").label == u"波打つ布")
    bad = []
    for f in (1, 7, 23, 50):
        cmds.currentTime(f)
        for l in range(3):
            shape = cmds.listRelatives(cmds.ls("%s_lattice_%02d" % (r, l + 1), long=True)[0], shapes=True, fullPath=True)[0]
            exp = mo.cloth_lattice(l, f, v, 8, cloth.ACROSS)
            for i in range(8):
                for k in range(cloth.ACROSS):
                    for j, base in ((0, -0.5), (1, 0.5)):
                        got = cmds.getAttr("%s.pt[%d][%d][%d].yValue" % (shape, i, j, k))
                        if abs(got - (base + exp[i][k])) > 1e-5:
                            bad.append((f, l, i, j, k, got, base + exp[i][k]))
            off = cmds.getAttr(layers[l] + ".translate")[0]
            if not near(off, mo.cloth_layer_offset(l, v), 1e-6):
                bad.append(("offset", f, l))
            col = cmds.getAttr("%s_mat_%02d.outColor" % (r, l + 1))[0]
            if not near(col, mo.cloth_color(l, 3, v), 1e-5) or \
                    abs(cmds.getAttr("%s_mat_%02d.outTransparencyR" % (r, l + 1)) - (1 - v["opacity"])) > 1e-6:
                bad.append(("color", f, l))
    check("波打つ布: 格子の点の高さ・布の置き場所・色・透明度が motion.py と同じ", not bad, str(bad[:3]))
    # 板が実際に波打つ (格子で曲がる)
    cmds.currentTime(12)
    plane = cmds.ls(r + "_cloth_01", long=True)[0]
    ys = [cmds.pointPosition("%s.vtx[%d]" % (plane, i), world=True)[1] for i in range(0, 61 * 9, 3)]
    check("波打つ布: 板が波打つ (格子の 8 点でも、波の高さの半分以上は曲がる)", max(ys) - min(ys) > v["amplitude"],
          str((min(ys), max(ys))))
    # 舞台を回す・拡大縮小すると、布の束がまとめて動く
    before = cmds.pointPosition(plane + ".vtx[0]", world=True)
    cmds.setAttr(stage + ".rotateY", 90)
    cmds.setAttr(stage + ".scale", 2, 2, 2, type="double3")
    after = cmds.pointPosition(plane + ".vtx[0]", world=True)
    rel_b = [a - b for a, b in zip(before, (5, 0, -2))]
    rel_a = [a - b for a, b in zip(after, (5, 0, -2))]
    check("波打つ布: 舞台を回す・拡大縮小すると、布の束がまとめて動く",
          near(rel_a, (2 * rel_b[2], 2 * rel_b[1], -2 * rel_b[0]), 1e-3), str((rel_b, rel_a)))
    cmds.setAttr(root + ".dfx_pin_ends", True)
    cmds.currentTime(13)
    shape = cmds.listRelatives(cmds.ls(r + "_lattice_01", long=True)[0], shapes=True, fullPath=True)[0]
    ends = [cmds.getAttr("%s.pt[%d][1][%d].yValue" % (shape, i, k)) for i in (0, 7) for k in range(cloth.ACROSS)]
    check("波打つ布: 両端を押さえると端は波打たない (すぐ反映)", all(abs(e - 0.5) < 1e-6 for e in ends), str(ends))
    cmds.setAttr(root + ".dfx_layers", 5)
    cmds.select(root)
    run_tool(cloth.ClothUpdateTool)
    check("波打つ布: 「波打つ布を更新」で枚数が変わる (舞台はそのまま)",
          len(cmds.ls(r + "_layer_*", type="transform")) == 5 and cmds.getAttr(stage + ".rotateY") == 90)
    cmds.select(root)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(r)]
    check("波打つ布: ベイクできる", mu.is_baked(root) and not exprs, str(exprs[:2]))


@section
def t50_tunnel_lines():
    """スピードライン (トンネル): 消失点の向き・流れ方・消え方・向き・形が motion.py と同じ、更新、ベイク。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import tunnel_lines as tl, _weather as wx, bake
    from dummy_effect_maker.tools.ghost_blur import rotation_rows
    cmds.file(new=True, force=True)
    cmds.playbackOptions(minTime=1, maxTime=60)
    cam = cmds.camera(name="tunnelCam")[0]
    cmds.setKeyframe(cam, attribute="translateZ", time=1, value=10)
    cmds.setKeyframe(cam, attribute="translateZ", time=60, value=-30)
    cmds.setAttr(cam + ".rotateY", 25)
    cmds.select(cam)
    root = cmds.ls(run_tool(tl.TunnelLinesTool, count=12), long=True)[0]
    r = wx.short(root)
    vanish = cmds.ls(r + "_vanish", long=True)[0]
    tunnel = cmds.ls(r + "_tunnel", long=True)[0]
    items = cmds.listRelatives(cmds.ls(r + "_items", long=True)[0], children=True, fullPath=True)
    check("トンネル: カメラタブにあり、消失点のロケータ・トンネル・線 12 本ができる",
          tl.TunnelLinesTool.tab == u"カメラ" and len(items) == 12 and catalog.spec("tunnel_lines").group == catalog.CAMERA
          and wx.find_link(cmds, root, wx.CAMERA_ATTR) is not None)
    cmds.setAttr(vanish + ".translate", 3.0, 1.5, -20.0, type="double3")
    v = mu.read_param_values(root, tl.KIND.stored_params())

    def rows(m):
        return [m[0:3], m[4:7], m[8:11]]

    bad = []
    for f in (1, 6, 17, 40):
        cmds.currentTime(f)
        # トンネルの向き: カメラの空間で tunnel_basis(消失点)
        cm = cmds.getAttr(cam + ".worldMatrix[0]")
        tm = cmds.getAttr(tunnel + ".worldMatrix[0]")
        exp = mo.tunnel_basis((3.0, 1.5, -20.0))
        for k in range(3):
            world = tuple(sum(exp[k][j] * cm[4 * j + c] for j in range(3)) for c in range(3))
            if not near(_unit(tm[4 * k:4 * k + 3]), world, 1e-4):
                bad.append(("axis", f, k))
        if not near(tm[12:15], cm[12:15], 1e-4):
            bad.append(("origin", f))
        for i, node in enumerate(items):
            p, axes, scale = mo.tunnel_item(i, f, v, True)
            m = cmds.getAttr(node + ".matrix")
            vis = cmds.getAttr(node + ".visibility")
            if bool(vis) != (scale[0] > 0):
                bad.append(("vis", f, i))
                continue
            if scale[0] <= 0:
                continue
            if not near(m[12:15], p, 1e-4) or any(abs(_mag(rows(m)[k]) - scale[k]) > 1e-4 for k in range(3)) \
                    or not all(near(_unit(rows(m)[k]), axes[k], 1e-3) for k in range(3)):
                bad.append((f, i))
    check("トンネル: 向き (消失点へ)・線の位置・向き・長さ・太さが motion.py と同じ (動くカメラ)", not bad, str(bad[:3]))
    # 線の先は消失点の方へ (奥へ): 時間とともに遠ざかる
    cmds.currentTime(10)
    d0 = [-cmds.getAttr(n + ".translateZ") for n in items]
    cmds.currentTime(11)
    d1 = [-cmds.getAttr(n + ".translateZ") for n in items]
    away = sum(1 for a, b in zip(d0, d1) if b > a)
    cmds.setAttr(root + ".dfx_direction", 1)   # 手前へ
    cmds.currentTime(10)
    t0 = [-cmds.getAttr(n + ".translateZ") for n in items]
    cmds.currentTime(11)
    t1 = [-cmds.getAttr(n + ".translateZ") for n in items]
    toward = sum(1 for a, b in zip(t0, t1) if b < a)
    check("トンネル: 流れる向き (奥へ = 遠ざかる / 手前へ = 近づく。すぐ反映)", away >= 10 and toward >= 10, str((away, toward)))
    # 消える距離: 完全に消える距離より奥・手前の端より手前には大きさがない
    cmds.setAttr(root + ".dfx_fade_start", 5.0)
    cmds.setAttr(root + ".dfx_fade_end", 8.0)
    bad = []
    for f in (3, 21, 44):
        cmds.currentTime(f)
        for n in items:
            d = -cmds.getAttr(n + ".translateZ")
            s = cmds.getAttr(n + ".scaleX")
            if d > 8.0 + 1e-6 or (d > 5.0 and s > (8.0 - d) / 3.0 + 1e-6):
                bad.append((f, d, s))
    check("トンネル: 消え始める距離 → 完全に消える距離で小さくなる (すぐ反映)", not bad, str(bad[:3]))
    # トンネルの回転 (キーを打った回転 + 回り続ける速さ) と、線の太さ (入り・中・終わり)
    cmds.setAttr(root + ".dfx_fade_start", 25.0)
    cmds.setAttr(root + ".dfx_fade_end", 40.0)
    cmds.setAttr(root + ".dfx_direction", 0)
    cmds.setAttr(root + ".dfx_roll_speed", 5.0)
    cmds.setKeyframe(root, attribute="dfx_roll", time=1, value=0.0)
    cmds.setKeyframe(root, attribute="dfx_roll", time=40, value=120.0)
    cmds.setAttr(root + ".dfx_width_start", 0.02)
    cmds.setAttr(root + ".dfx_width_mid", 0.07)
    cmds.setAttr(root + ".dfx_width_end", 0.01)
    src = cmds.ls(r + "_itemSrc", long=True)[0]
    bad = []
    for f in (4, 19, 33):
        cmds.currentTime(f)
        v = mu.read_param_values(root, tl.KIND.stored_params())
        for i, node in enumerate(items):
            p, axes, scale = mo.tunnel_item(i, f, v, True)
            m = cmds.getAttr(node + ".matrix")
            if scale[0] > 0 and (not near(m[12:15], p, 1e-4)
                                 or any(abs(_mag(rows(m)[k]) - scale[k]) > 1e-4 for k in range(3))
                                 or not all(near(_unit(rows(m)[k]), axes[k], 1e-3) for k in range(3))):
                bad.append((f, i))
        pts = cmds.xform(src + ".vtx[*]", query=True, objectSpace=True, translation=True)
        tmpl = tl.shape_points(tl.SHAPE_LINE)[0]
        for k, t in enumerate(tmpl):
            want = t[0] * mo.tunnel_width_at(t[1] + 1.0, v)
            if abs(pts[3 * k] - want) > 1e-5 or abs(pts[3 * k + 1] - t[1]) > 1e-6:
                bad.append(("width", f, k, pts[3 * k], want))
                break
    check("トンネル: 回転 (キー + 回り続ける速さ) でらせんに流れ、線の太さ (入り・中・終わり) が motion.py と同じ",
          not bad and abs(v["roll"] - 120.0 * 32 / 39.0) < 30, str(bad[:3]))
    cmds.cutKey(root, attribute="dfx_roll")
    cmds.setAttr(root + ".dfx_roll", 0.0)
    cmds.setAttr(root + ".dfx_roll_speed", 0.0)
    cmds.setAttr(root + ".dfx_shape", tl.SHAPES.index(tl.SHAPE_HEART))
    cmds.setAttr(root + ".dfx_count", 20)
    cmds.select(root)
    run_tool(tl.TunnelLinesUpdateTool)
    items = cmds.listRelatives(cmds.ls(r + "_items", long=True)[0], children=True, fullPath=True)
    src = cmds.ls(r + "_itemSrc", long=True)[0]
    v = mu.read_param_values(root, tl.KIND.stored_params())
    bad = []
    cmds.currentTime(9)
    for i, node in enumerate(items):
        p, axes, scale = mo.tunnel_item(i, 9, v, False)
        m = cmds.getAttr(node + ".matrix")
        if scale[0] > 0 and (not near(m[12:15], p, 1e-4) or abs(_mag(m[0:3]) - scale[0]) > 1e-4
                             or not all(near(_unit(m[4 * k:4 * k + 3]), axes[k], 1e-3) for k in range(3))):
            bad.append(i)
    check("トンネル: 形をハートにして更新 (20 個・ハートの面・壁に貼り付いて流れる。消失点はそのまま)",
          len(items) == 20 and cmds.polyEvaluate(src, face=True) == 24 and not bad
          and near(cmds.getAttr(vanish + ".translate")[0], (3.0, 1.5, -20.0)), str(bad[:3]))
    # 残像と尾 (星): 残像の段ごとのグループ (前の位置で小さく・後ろほど薄い) と尾の線
    cmds.setAttr(root + ".dfx_shape", tl.SHAPES.index(tl.SHAPE_STAR))
    cmds.setAttr(root + ".dfx_trail", tl.TRAILS.index(tl.TRAIL_BOTH))
    cmds.setAttr(root + ".dfx_ghost_count", 3)
    cmds.setAttr(root + ".dfx_roll_speed", 3.0)
    cmds.select(root)
    run_tool(tl.TunnelLinesUpdateTool)
    v = mu.read_param_values(root, tl.KIND.stored_params())
    groups = [(k, cmds.ls("%s_ghost%02d" % (r, k), long=True)) for k in (1, 2, 3)] + [("tail", cmds.ls(r + "_tails", long=True))]
    bad = []
    cmds.currentTime(23)
    for kind, grp in groups:
        if not grp:
            bad.append(("missing", kind))
            continue
        nodes = cmds.listRelatives(grp[0], children=True, fullPath=True) or []
        if len(nodes) != 20:
            bad.append(("count", kind, len(nodes)))
        for i, node in enumerate(nodes):
            if kind == "tail":
                p, axes, scale = mo.tunnel_item(i, 23, v, True)
            else:
                p, axes, scale = mo.tunnel_ghost(i, 23, kind, v, False)
            m = cmds.getAttr(node + ".matrix")
            if bool(cmds.getAttr(node + ".visibility")) != (scale[0] > 0):
                bad.append(("vis", kind, i))
            elif scale[0] > 0 and (not near(m[12:15], p, 1e-4)
                                   or any(abs(_mag(rows(m)[k]) - scale[k]) > 1e-4 for k in range(2))
                                   or not all(near(_unit(rows(m)[k]), axes[k], 1e-3) for k in range(2))):
                bad.append((kind, i))
    trans = []
    for k in (1, 2, 3):
        gsrc = cmds.ls("%s_ghostSrc%02d" % (r, k), long=True)[0]
        sh = cmds.listConnections(cmds.listRelatives(gsrc, shapes=True, fullPath=True)[0], type="shadingEngine") or []
        mat = (cmds.listConnections(sh[0] + ".surfaceShader") or [None])[0] if sh else None
        trans.append(cmds.getAttr(mat + ".outTransparencyR") if mat else None)
    want = [1 - mo.tunnel_ghost_opacity(k, v) for k in (1, 2, 3)]
    tail_src = cmds.ls(r + "_tailSrc", long=True)[0]
    tp = cmds.xform(tail_src + ".vtx[%d]" % (tl.WIDTH_SAMPLES // 2), query=True, objectSpace=True, translation=True)
    check("トンネル: 残像と尾 (残像 3 段は前の位置で小さく・後ろほど薄い、尾は形の後ろの太さの付いた線)",
          not bad and None not in trans and all(abs(a - b) < 1e-5 for a, b in zip(trans, want))
          and abs(tp[0] + mo.tunnel_width_at(0.5, v)) < 1e-5, str((bad[:3], trans, want, tp)))
    cmds.select(root)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(r)]
    check("トンネル: ベイクできる", mu.is_baked(root) and not exprs, str(exprs[:2]))


@section
def t51_electric():
    """電撃: 中心のロケータ (オブジェクトに付いて動く) のまわりにまとわりつく / 外へ放電。motion.py と同じ。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import electric as el, _weather as wx, weather, export_shot, bake
    cam = _weather_scene()
    ball = cmds.polySphere(radius=1.0, name="ball")[0]
    cmds.setAttr(ball + ".scale", 2.0, 1.0, 1.0, type="double3")
    cmds.setAttr(ball + ".rotateY", 30)
    cmds.setKeyframe(ball, attribute="translateX", time=1, value=-4)
    cmds.setKeyframe(ball, attribute="translateX", time=60, value=6)
    cmds.setKeyframe(ball, attribute="translateY", time=1, value=2)
    cmds.select([ball, cam])
    root = cmds.ls(run_tool(el.ElectricAnimeTool, count=5, start_frame=1.0, end_frame=60.0), long=True)[0]
    r = wx.short(root)
    center = cmds.ls(r + "_center", long=True)[0]
    joints = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
    v = mu.read_param_values(root, el.ANIME.stored_params())
    st = {"n": int(v["segments"]), "count": int(v["count"]), "joints": joints, "center": center}
    rn = mo.strand_rows(mo.STYLE_ANIME, st["n"])
    check("電撃: 稲妻の所にあり、中心のロケータと線 5 本分のジョイントができる",
          el.ElectricAnimeTool.category == u"稲妻" and len(joints) == rn * st["count"]
          and catalog.spec("electric_anime").group == catalog.PARTICLES
          and wx.find_link(cmds, root, wx.EMITTER_ATTR) == center)
    pos(cam, 30)
    cm = cmds.getAttr(center + ".worldMatrix[0]")
    bm = cmds.getAttr(ball + ".worldMatrix[0]")
    check("電撃: 中心はオブジェクトに付いて動き、大きさはオブジェクトの箱 (半径 1 の球の X 2 倍)",
          near(cm[12:15], bm[12:15], 1e-4) and near(cm[0:3], bm[0:3], 1e-4) and near(cm[4:7], bm[4:7], 1e-4)
          and near(cm[8:11], bm[8:11], 1e-4))

    def compare(style, frames, vv):
        bad = []
        for f in frames:
            pos(cam, f)
            m = cmds.getAttr(st["center"] + ".worldMatrix[0]")
            camm = cmds.getAttr(cam + ".worldMatrix[0]")
            rows = mo.electric_rows(style, f, vv, tuple(m[12:15]), (tuple(m[0:3]), tuple(m[4:7]), tuple(m[8:11])),
                                    (camm[12], camm[13], camm[14]), (-camm[8], -camm[9], -camm[10]),
                                    st["count"], st["n"])
            for j, (p, axes, w) in enumerate(rows):
                mm = cmds.getAttr(st["joints"][j] + ".worldMatrix[0]")
                if abs(_mag(mm[0:3]) - w) > 1e-4 or (w > 1e-6 and not near((mm[12], mm[13], mm[14]), p, 2e-3)):
                    bad.append((f, j, w, _mag(mm[0:3]), p, mm[12:15]))
                    break
        return bad

    frames = (1, 2, 5, 17, 33, 47, 60, 61)
    bad = compare(mo.STYLE_ANIME, frames, v)
    check("電撃: まとわりつく形・太さが motion.py と同じ (動くオブジェクト)", not bad, str(bad[:3]))
    pos(cam, 61)
    check("電撃: 終わったら消える", all(_mag(cmds.getAttr(j + ".worldMatrix[0]")[0:3]) < 1e-9 for j in joints))
    cmds.setAttr(root + ".dfx_mode", 1)   # 外へ放電 (作り直さずに切り替わる)
    cmds.setAttr(root + ".dfx_inner", 0.5)
    v = mu.read_param_values(root, el.ANIME.stored_params())
    bad = compare(mo.STYLE_ANIME, frames, v)
    check("電撃: 外へ放電に切り替えても motion.py と同じ (作り直さない)", v["mode"] == mo.ELECTRIC_OUT and not bad,
          str(bad[:3]))
    cmds.setAttr(root + ".dfx_opacity", 0.6)
    pos(cam, 10)
    check("電撃: 不透明度がシェーダーに入る",
          abs(cmds.getAttr(r + "_bolt_mat.outTransparencyR") - 0.4) < 1e-4
          and abs(cmds.getAttr(r + "_glow_mat.outTransparencyR") - (1 - 0.6 * v["glow_opacity"])) < 1e-4)
    # 本数・点の数を変えて更新
    cmds.setAttr(root + ".dfx_count", 3)
    cmds.setAttr(root + ".dfx_segments", 6)
    cmds.select(root)
    run_tool(weather.WeatherUpdateTool)
    st["joints"] = cmds.listRelatives(cmds.ls(r + "_joints", long=True)[0], children=True, fullPath=True)
    v = mu.read_param_values(root, el.ANIME.stored_params())
    st["n"], st["count"] = 6, 3
    bad = compare(mo.STYLE_ANIME, (3, 20), v)
    check("電撃: 本数・点の数を変えて「天候を更新」",
          len(st["joints"]) == mo.strand_rows(mo.STYLE_ANIME, 6) * 3 and not bad
          and cmds.ls(r + "_center", long=True) == [center], str(bad[:3]))
    # 実写: オブジェクトなしで作る (中心はワールドの (0, 1, 0))
    cmds.select(cam)
    real = cmds.ls(run_tool(el.ElectricRealTool, count=4), long=True)[0]
    rr = wx.short(real)
    st["center"] = cmds.ls(rr + "_center", long=True)[0]
    st["joints"] = cmds.listRelatives(cmds.ls(rr + "_joints", long=True)[0], children=True, fullPath=True)
    v = mu.read_param_values(real, el.REAL.stored_params())
    st["n"], st["count"] = int(v["segments"]), 4
    bad = compare(mo.STYLE_REAL, (1, 9, 40), v)
    check("電撃 (実写): オブジェクトなしでも作れ、motion.py と同じ",
          near(cmds.xform(st["center"], q=True, ws=True, t=True), (0, 1, 0), 1e-6) and not bad, str(bad[:3]))
    shot = export_shot.build_shot(cmds, mu, [root], 1, 60)
    eff = shot["effects"][0]
    check("電撃: 書き出し (中心のロケータ)", eff["type"] == "electric_anime"
          and "center" in set(c["name"].split("_")[-1] for c in eff["controls"]))
    cmds.select(root)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(r + "_")]
    check("電撃: ベイクできる", mu.is_baked(root) and not exprs, str(exprs[:2]))



@section
def t52_presets_all_types():
    """共通の保存・読み込み: 今までボタンの無かった種類 (エネルギー弾・トンネル・布・電撃・風・核爆発) も使える。"""
    import shutil
    from dummy_effect_maker import catalog
    from dummy_effect_maker.tools import energy_ball, tunnel_lines, cloth, electric, wind, nuke
    cam = _weather_scene()
    tmp = tempfile.mkdtemp()
    dialog = FakeDialog()
    orig_dialog, orig_var = cmds.fileDialog2, cmds.internalVar
    cmds.fileDialog2 = dialog
    cmds.internalVar = lambda **kw: tmp + "/maya/"
    try:
        for tool, name, saved_value, other in ((energy_ball.EnergyBallTool, "count", 3, 5),
                                               (tunnel_lines.TunnelLinesTool, "count", 12, 30),
                                               (cloth.ClothTool, "layers", 2, 5),
                                               (electric.ElectricAnimeTool, "count", 4, 7),
                                               (wind.WindTool, "turbulence", 0.25, 0.75),
                                               (nuke.NukeTool, "seed", 7, 9)):
            cmds.select(cam)
            result = run_tool(tool)
            root = cmds.ls(result[0] if isinstance(result, list) else result, long=True)[0]
            effect_type = cmds.getAttr(root + ".dfxEffectType")
            spec = catalog.spec(effect_type)
            cmds.setAttr(root + ".dfx_" + name, saved_value)
            cmds.select(root)
            dialog.answer = os.path.join(tmp, effect_type)
            saved = run_tool(_preset.PresetSaveTool)
            cmds.setAttr(root + ".dfx_" + name, other)
            cmds.select(root)
            dialog.answer = saved
            run_tool(_preset.PresetLoadTool)
            got = cmds.getAttr(root + ".dfx_" + name)
            check(u"共通の保存・読み込み: %s (%s)" % (spec.label, name),
                  saved and saved.replace("\\", "/").endswith(effect_type + ".json") and abs(got - saved_value) < 1e-6
                  and cmds.objExists(root), str((saved, got)))
        # 種類の違うものを一緒に選ぶと、ダイアログを出す前にエラー
        cmds.select(cmds.ls("dfx_tunnel*", type="transform")[:1] + cmds.ls("dfx_electricAnime*", type="transform")[:1])
        n = len(dialog.calls)
        try:
            run_tool(_preset.PresetLoadTool)
            check("共通の保存・読み込み: 種類の違うものを一緒に選ぶとエラー", False)
        except ToolError as e:
            check("共通の保存・読み込み: 種類の違うものを一緒に選ぶとエラー", len(dialog.calls) == n, u"%s" % e)
    finally:
        cmds.fileDialog2, cmds.internalVar = orig_dialog, orig_var
        shutil.rmtree(tmp, ignore_errors=True)



@section
def t53_leaves():
    """落ち葉: 舞い散る (風・回した範囲の箱) と地面の渦 (回した・付いて動くロケータ) が motion.py と同じ。"""
    from dummy_effect_maker import motion as mo, catalog
    from dummy_effect_maker.tools import leaves as lv, _weather as wx, wind, weather, bake
    cam = _weather_scene()
    cmds.select(clear=True)
    run_tool(wind.WindTool, strength=0.2, gust=1.0)

    def wind_of(root):
        wr = wx.find_link(cmds, root, wx.WIND_ATTR)
        if not wr:
            return mo.Wind()
        tabs = [cmds.getAttr(wr + "." + a) for a in wx.DRIFT_ATTRS + wx.WIND_VEC_ATTRS]
        return mo.Wind(list(zip(*tabs[:3])), list(zip(*tabs[3:])),
                       cmds.getAttr(wr + "." + wx.DRIFT_START_ATTR), cmds.getAttr(root + ".dfx_wind_influence"))

    def groups(root):
        r = wx.short(root)
        return [cmds.listRelatives(cmds.ls("%s_leaves%d" % (r, g + 1), long=True)[0], children=True, fullPath=True)
                for g in range(mo.LEAF_COLORS)]

    def same(node, p, axes, sz, tol=1e-4):
        m = cmds.getAttr(node + ".worldMatrix[0]")
        if not near(m[12:15], p, tol):
            return False
        return all(abs(_mag(m[4 * r:4 * r + 3]) - sz) < 1e-4 and near(_unit(m[4 * r:4 * r + 3]), axes[r], 1e-3)
                   for r in range(3))

    def compare(root, frames, place):
        bad = []
        for f in frames:
            pos(cam, f)
            for g, nodes in enumerate(groups(root)):
                for idx, node in enumerate(nodes):
                    p, axes, sz, vis = place(idx * mo.LEAF_COLORS + g, f)
                    if bool(cmds.getAttr(node + ".visibility")) != vis or (vis and not same(node, p, axes, sz)):
                        bad.append((f, g, idx, vis, p, cmds.getAttr(node + ".worldMatrix[0]")[12:15]))
                        break
        return bad

    # 舞い散る
    cmds.select(cam)
    fall = cmds.ls(run_tool(lv.LeafFallTool, count=14, ground_frames=30.0), long=True)[0]
    area = cmds.ls(wx.short(fall) + "_area", long=True)[0]
    cmds.setAttr(area + ".rotateY", 20)
    gs = groups(fall)
    check("落ち葉 (舞い散る): 天候タブの落ち葉にあり、色ごとに 3 つのグループ (14 枚 = 5 + 5 + 4)・風とつながる",
          lv.LeafFallTool.category == u"落ち葉" and [len(x) for x in gs] == [5, 5, 4]
          and wx.find_link(cmds, fall, wx.WIND_ATTR) and catalog.spec("leaves_fall").group == catalog.PARTICLES)
    v = mu.read_param_values(fall, lv.FALL.stored_params())
    wd = wind_of(fall)

    def place_fall(j, f):
        am = cmds.getAttr(area + ".worldMatrix[0]")
        return mo.leaf_fall(j, f, v, am, mo.inverse(am), wd)
    bad = compare(fall, (1, 7, 40, 133, 251), place_fall)
    check("落ち葉 (舞い散る): 位置・向き・大きさ・表示が motion.py と同じ (風・回した範囲の箱)", not bad, str(bad[:2]))
    landed = 0
    for f in range(1, 400, 7):
        pos(cam, f)
        for nodes in gs:
            for node in nodes:
                m = cmds.getAttr(node + ".worldMatrix[0]")
                if cmds.getAttr(node + ".visibility") and abs(m[13]) < 1e-4 and _unit(m[4:7])[1] > 0.9999:
                    landed += 1
    check("落ち葉 (舞い散る): 地面に着いた葉は平たく寝ている", landed > 10, str(landed))
    # 色はグループごとのシェーダー
    sh = []
    for g in range(3):
        src = cmds.ls("%s_leafSrc%d" % (wx.short(fall), g + 1), long=True)[0]
        sg = cmds.listConnections(cmds.listRelatives(src, shapes=True, fullPath=True)[0], type="shadingEngine")[0]
        sh.append(tuple(round(x, 3) for x in cmds.getAttr(cmds.listConnections(sg + ".surfaceShader")[0] + ".outColor")[0]))
    check("落ち葉: 色・色 2・色 3 がグループごとのシェーダーに入る",
          sh == [tuple(round(x, 3) for x in v[c]) for c in lv.COLOR_PARAMS], str(sh))

    # 地面の渦 (オブジェクトに付いて動く・回したロケータ)
    post = cmds.polyCube(name="post")[0]
    cmds.setKeyframe(post, attribute="translateX", time=1, value=-3)
    cmds.setKeyframe(post, attribute="translateX", time=80, value=2)
    cmds.setAttr(post + ".rotateY", 30)
    cmds.select(post)
    vort = cmds.ls(run_tool(lv.LeafVortexTool, count=20, shed_interval=8.0, life=40.0, ground_frames=10.0), long=True)[0]
    em = cmds.ls(wx.short(vort) + "_emitter", long=True)[0]
    check("落ち葉 (地面の渦): 出る場所のロケータがオブジェクトに付いて動く、風とはつながらない",
          near(cmds.getAttr(em + ".worldMatrix[0]")[12:15], cmds.getAttr(post + ".worldMatrix[0]")[12:15], 1e-6)
          and not wx.find_link(cmds, vort, wx.WIND_ATTR))
    v = mu.read_param_values(vort, lv.VORTEX.stored_params())

    def place_vortex(j, f):
        m = cmds.getAttr(em + ".worldMatrix[0]")
        p, axes, sz, vis = mo.leaf_vortex(j, f, v)
        p, axes = mo.leaf_place(m, p, axes)
        return p, axes, sz, vis
    bad = compare(vort, (1, 4, 12, 25, 46, 61, 90), place_vortex)
    check("落ち葉 (地面の渦): 位置・向き・大きさ・表示が motion.py と同じ (動いて回ったロケータ)", not bad, str(bad[:2]))
    # パラメータをすぐ変えられる (作り直さない)
    cmds.setAttr(vort + ".dfx_lift", 2.0)
    v = mu.read_param_values(vort, lv.VORTEX.stored_params())
    bad = compare(vort, (15,), place_vortex)
    check("落ち葉 (地面の渦): 巻き上げる高さはすぐ反映", not bad, str(bad[:2]))
    cmds.setAttr(vort + ".dfx_count", 9)
    cmds.select(vort)
    run_tool(weather.WeatherUpdateTool)
    check("落ち葉 (地面の渦): 葉の数を変えて「天候を更新」", [len(x) for x in groups(vort)] == [3, 3, 3]
          and cmds.ls(wx.short(vort) + "_emitter", long=True) == [em])
    cmds.select(vort)
    run_tool(bake.BakeTool)
    exprs = [e for e in cmds.ls(type="expression") or [] if e.startswith(wx.short(vort) + "_")]
    check("落ち葉 (地面の渦): ベイクできる", mu.is_baked(vort) and not exprs, str(exprs[:2]))



SECTIONS = (t01_registry, t02_create_everything, t03_undo_redo, t04_options, t05_errors,
           t06_save_and_cleanup, t07_long_travel_visibility, t08_adjust,
           t09_stretch_and_migration, t10_presets, t11_bake, t12_settings_and_ui,
           t13_bake_hide_by_scale, t14_deceleration, t15_focus_lines,
           t16_focus_lines_grow, t17_energy_ball, t18_vulcan, t19_focus_circle,
           t20_focus_tail_and_round_tip, t21_speed_lines,
           t22_camera_presets, t23_camera_shake,
           t24_shake_any_object, t25_hand_shake,
           t26_camera_shake_z, t27_hand_shake_z,
           t28_shockwave, t29_ghost_afterimage, t30_ghost_smear,
           t31_ghost_trail, t32_ghost_common, t33_weather_wind,
           t34_weather_rain_snow, t35_weather_dust_sparks, t36_weather_layers,
           t37_weather_common, t38_straight_colors,
           t39_weather_custom_texture, t40_explosion, t41_export_shot,
           t42_motion_matches_maya, t43_nuke, t44_lightning, t45_attribute_editor, t46_explosion_single,
           t47_discharge, t48_wisp, t49_cloth, t50_tunnel_lines, t51_electric, t52_presets_all_types, t53_leaves)
for fn in SECTIONS:
    if not ONLY or any(fn.__name__.startswith(o) for o in ONLY):
        fn()

fails = [r for r in RESULTS if not r[1]]
print("\nSUMMARY: %d passed, %d failed" % (len(RESULTS) - len(fails), len(fails)))
for name, _, detail in fails:
    print("  FAIL:", name, detail)
maya.standalone.uninitialize()
sys.exit(1 if fails else 0)
