# -*- coding: utf-8 -*-
"""爆発: 追尾弾・バルカン砲・エネルギー弾が、コリジョンに指定したモデルに当たったら爆発する。

使い方:
    1. ぶつける相手のモデル (ローポリで自由にモデリングしてよい。複数可) を選んで「コリジョンに指定」
       移動・回転・拡大縮小のアニメーションをしていてよい (変形するメッシュは作成時の形で判定)
    2. 追尾弾・バルカン砲・エネルギー弾 (ルートか、その中の弾) を選んで「爆発」
       弾の通り道を 1 フレームを細かく分けてたどり、最初に当たった所で爆発する。当たった弾は消える
    3. 弾やコリジョンを動かした・アニメーションを変えたら「爆発を更新」(当たる所を計算し直す)

構成:
    dfx_explosionN        ルート (パラメータ。弾のルートと「dfx_source」でつながる)
      _flashes            閃光 (カメラを向く丸。当たり 1 つにつき 1 つ)
      _rings              衝撃波の輪 (当たった面に沿って広がる)
      _fire / _smoke      火の玉と煙 (もこもこの塊。アニメ調の 2 色)
      _sparks             火花 (進む向きに伸びた線。重力で落ちる)

当たった弾は lodVisibility で隠す (弾のアニメーションには手を加えない)。
"""
from __future__ import absolute_import

import math
import re

from ..core import BaseTool, Param, ToolError
from . import _weather as wx

EFFECT_TYPE = "explosion"
TAB = u"爆発"
BULLET_TYPES = ("homing", "vulcan", "energy_ball")
COLLIDER_ATTR = "dfxCollider"
SOURCE_ATTR = "dfx_source"
HITS_ATTR = "dfxHits"        # 当たりの表 (8 個ずつ: フレーム, 弾の番号, 位置 xyz, 法線 xyz)
HIT_STRIDE = 8
BULLET_NAME = re.compile(r"_(bullet|ball)_\d+$")
H = wx.mel_hash

LIVE_PARAMS = ("size", "hide_bullet", "flash_size", "flash_frames", "flash_color",
               "ring_size", "ring_frames", "ring_color",
               "fire_size", "fire_spread", "fire_frames", "fire_color", "fire_shade",
               "smoke_size", "smoke_spread", "smoke_frames", "smoke_rise", "smoke_color",
               "smoke_shade", "spark_speed", "spark_frames", "gravity", "spark_color",
               "opacity", "seed")
REBUILD_PARAMS = ("fire_count", "smoke_count", "spark_count", "substeps")


# ---------------------------------------------------------------- 純粋な計算
def segment_param(a, b, hit):
    """a→b の線分上の hit の位置 (0〜1)。"""
    d = [y - x for x, y in zip(a, b)]
    n = sum(x * x for x in d)
    if n < 1e-18:
        return 0.0
    return min(1.0, max(0.0, sum((h - x) * y for h, x, y in zip(hit, a, d)) / n))


def sample_times(start, end, substeps):
    """start〜end を 1 フレームあたり substeps に分けた時刻。"""
    n = int(round((end - start) * substeps))
    return [start + k / float(substeps) for k in range(n + 1)]


# ---------------------------------------------------------------- ツール
def _explosion_params():
    return [
        Param("size", u"大きさ", 1.0, minimum=0.01, step=0.25,
              tooltip=u"爆発全体の大きさの倍率"),
        Param("hide_bullet", u"当たった弾を消す", True),
        Param("substeps", u"当たり判定の細かさ", 4, minimum=1, maximum=20,
              tooltip=u"1 フレームを何回に分けて当たりを調べるか。速い弾や薄いモデルは多めに"),
        Param("flash_size", u"閃光の大きさ", 2.5, minimum=0.0, step=0.25,
              tooltip=u"当たった瞬間の光る丸 (0 で出さない)"),
        Param("flash_frames", u"閃光のフレーム数", 4.0, minimum=0.5, step=1.0, decimals=1),
        Param("flash_color", u"閃光の色", (1.0, 1.0, 0.85), kind="color"),
        Param("ring_size", u"衝撃波の大きさ", 2.5, minimum=0.0, step=0.25,
              tooltip=u"当たった面に沿って広がる輪の半径 (0 で出さない)"),
        Param("ring_frames", u"衝撃波のフレーム数", 8.0, minimum=0.5, step=1.0, decimals=1),
        Param("ring_color", u"衝撃波の色", (1.0, 0.95, 0.8), kind="color"),
        Param("fire_count", u"火の玉の数", 6, minimum=0, maximum=40),
        Param("fire_size", u"火の玉の大きさ", 1.2, minimum=0.0, step=0.1),
        Param("fire_spread", u"火の玉の広がり", 1.0, minimum=0.0, step=0.1),
        Param("fire_frames", u"火の玉のフレーム数", 10.0, minimum=1.0, step=1.0, decimals=1),
        Param("fire_color", u"火の玉の色", (1.0, 0.8, 0.3), kind="color"),
        Param("fire_shade", u"火の玉の影の色", (0.95, 0.35, 0.1), kind="color"),
        Param("smoke_count", u"煙の数", 6, minimum=0, maximum=40),
        Param("smoke_size", u"煙の大きさ", 1.5, minimum=0.0, step=0.1),
        Param("smoke_spread", u"煙の広がり", 1.6, minimum=0.0, step=0.1),
        Param("smoke_frames", u"煙のフレーム数", 30.0, minimum=1.0, step=5.0, decimals=1),
        Param("smoke_rise", u"煙の上がる速さ", 0.04, step=0.01, decimals=3),
        Param("smoke_color", u"煙の色", (0.62, 0.58, 0.55), kind="color"),
        Param("smoke_shade", u"煙の影の色", (0.38, 0.35, 0.33), kind="color"),
        Param("spark_count", u"火花の数", 10, minimum=0, maximum=60),
        Param("spark_speed", u"火花の速さ", 0.6, minimum=0.0, step=0.1),
        Param("spark_frames", u"火花のフレーム数", 12.0, minimum=1.0, step=1.0, decimals=1),
        Param("gravity", u"火花の重力", 0.03, minimum=0.0, step=0.01, decimals=3),
        Param("spark_color", u"火花の色", (1.0, 0.85, 0.4), kind="color"),
        Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1),
        Param("seed", u"シード", 1, minimum=0, maximum=99999),
    ]


class ColliderSetTool(BaseTool):
    label = u"コリジョンに指定"
    tab = TAB
    category = u"コリジョン"
    order = 902
    tooltip = (u"選択したモデルを、弾がぶつかるコリジョンにします (複数可。グループなら中のメッシュ全部)。\n"
               u"ローポリで自由にモデリングしてよく、移動・回転・拡大縮小のアニメーションにも対応します。")

    def run(self, ctx, **v):
        from maya import cmds
        nodes = selected_meshes(cmds)
        for node in nodes:
            if not cmds.attributeQuery(COLLIDER_ATTR, node=node, exists=True):
                cmds.addAttr(node, longName=COLLIDER_ATTR, attributeType="bool", defaultValue=True)
            cmds.setAttr(node + "." + COLLIDER_ATTR, True)
            ctx.log(u"  %s をコリジョンにしました" % wx.short(node))
        ctx.log(u"  弾 (追尾弾・バルカン砲・エネルギー弾) を選んで「爆発」(作成済みなら「爆発を更新」) を押すと当たりを計算します")
        return nodes


class ColliderClearTool(BaseTool):
    label = u"コリジョンを解除"
    tab = TAB
    category = u"コリジョン"
    order = 903
    tooltip = u"選択したモデルをコリジョンから外します (何も選ばなければシーンの全部)。"

    def run(self, ctx, **v):
        from maya import cmds
        nodes = [n for n in cmds.ls(selection=True, long=True, objectsOnly=True) or []]
        nodes = [n if cmds.objectType(n, isAType="transform") else
                 (cmds.listRelatives(n, parent=True, fullPath=True) or [n])[0] for n in nodes]
        nodes = [n for n in nodes if cmds.attributeQuery(COLLIDER_ATTR, node=n, exists=True)] \
            if nodes else colliders(cmds)
        if not nodes:
            raise ToolError(u"コリジョンに指定したモデルがありません")
        for node in nodes:
            cmds.deleteAttr(node, attribute=COLLIDER_ATTR)
            ctx.log(u"  %s をコリジョンから外しました" % wx.short(node))
        return nodes


class ExplosionTool(BaseTool):
    label = u"爆発"
    tab = TAB
    category = u"爆発"
    order = 904
    tooltip = (u"選択した追尾弾・バルカン砲・エネルギー弾が、コリジョンに当たった所で爆発します。\n"
               u"当たった弾は消えます。閃光・衝撃波の輪・火の玉・煙・火花の大きさや色は後から変えられます。\n"
               u"弾やコリジョンを動かしたら「爆発を更新」で当たりを計算し直します。")
    params = _explosion_params()

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        sources = bullet_roots(cmds, mu)
        if not colliders(cmds):
            raise ToolError(u"コリジョンがありません。ぶつける相手のモデルを選んで「コリジョンに指定」を押してください")
        camera = wx.choose_camera(cmds, ctx)
        roots = []
        for source in sources:
            existing = explosions_of(cmds, mu, source)
            if existing:
                ctx.log(u"  %s には既に爆発 (%s) があるので、当たりを計算し直します"
                        % (wx.short(source), wx.short(existing[0])))
                roots += rebuild_effects(cmds, mu, ctx, existing[:1])
                continue
            root = cmds.group(empty=True, name="dfx_explosion#", world=True)
            root = cmds.ls(root, long=True)[0]
            mu.tag(root)
            mu.mark_effect_root(root, EFFECT_TYPE)
            mu.add_param_attrs(root, stored_params(), v, LIVE_PARAMS)
            mu.lock_transform(root)
            wx.link(cmds, root, SOURCE_ATTR, source)
            wx.link(cmds, root, wx.CAMERA_ATTR, camera)
            cmds.addAttr(root, longName=HITS_ATTR, dataType="doubleArray")
            hits = build_effect(cmds, mu, root, v)
            _log(ctx, root, source, hits)
            roots.append(root)
        cmds.select(roots)
        return roots[0] if len(roots) == 1 else roots


class ExplosionUpdateTool(BaseTool):
    label = u"爆発を更新"
    tab = TAB
    category = u"爆発"
    order = 906
    tooltip = (u"選択した爆発 (または弾) の当たりを計算し直して作り直します (爆発 (単体) も作り直します)。\n"
               u"弾・コリジョンを動かした時や、「(更新)」の付いた項目 (数など) を変えた時に使います。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        from . import explosion_single
        singles = explosion_single.selected(cmds, mu)   # 爆発 (単体) も作り直す
        others = [r for r in mu.selected_effect_roots()
                  if r not in singles and cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in (EFFECT_TYPE,) + BULLET_TYPES]
        result = wx.rebuild_effects(cmds, mu, ctx, singles) if singles else []
        if others or not singles:
            result += rebuild_effects(cmds, mu, ctx, selected_roots(cmds, mu, u"更新する"))
        cmds.select(result)
        return result


class ExplosionRemoveTool(BaseTool):
    label = u"爆発を外す"
    tab = TAB
    category = u"爆発"
    order = 907
    tooltip = u"選択した爆発を削除し、当たって消えていた弾を元に戻します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        roots = selected_roots(cmds, mu, u"外す")
        for root in roots:
            release(cmds, mu, root)
            ctx.log(u"  %s を外しました" % wx.short(root))
        return len(roots)


class ExplosionSavePresetTool(BaseTool):
    label = u"爆発のパラメータを保存"
    tab = TAB
    category = u"爆発"
    order = 908
    undoable = False
    tooltip = u"選択した爆発 1 つのパラメータをプリセットファイル (.json) に保存します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return mu.save_preset_dialog(ctx, selected_roots(cmds, mu, u"保存する"), EFFECT_TYPE,
                                     stored_params(), self.label, u"爆発")


class ExplosionLoadPresetTool(BaseTool):
    label = u"爆発のパラメータを読み込み"
    tab = TAB
    category = u"爆発"
    order = 909
    tooltip = u"プリセットファイル (.json) のパラメータを選択した爆発に設定し、作り直します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        roots = selected_roots(cmds, mu, u"読み込み先の")
        values = mu.load_preset_dialog(ctx, EFFECT_TYPE, stored_params(), self.label)
        if values is None:
            return None
        return rebuild_effects(cmds, mu, ctx, roots, overrides=values)


# ---------------------------------------------------------------- 選択・つながり
def stored_params():
    by_name = dict((p.name, p) for p in ExplosionTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def selected_meshes(cmds):
    nodes = []
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if not cmds.objectType(node, isAType="transform"):
            node = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
        if node and node not in nodes:
            nodes.append(node)
    if not nodes:
        raise ToolError(u"コリジョンにするモデルを選択してください")
    for node in nodes:
        if not collider_meshes(cmds, node):
            raise ToolError(u"%s にはメッシュがありません" % wx.short(node))
    return nodes


def colliders(cmds):
    nodes = cmds.ls("*." + COLLIDER_ATTR, objectsOnly=True, recursive=True, long=True) or []
    return [n for n in nodes if cmds.getAttr(n + "." + COLLIDER_ATTR)]


def collider_meshes(cmds, node):
    shapes = cmds.listRelatives(node, allDescendents=True, type="mesh", fullPath=True) or []
    return [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]


def bullet_roots(cmds, mu):
    roots = [r for r in mu.selected_effect_roots()
             if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in BULLET_TYPES]
    if not roots:
        raise ToolError(u"爆発させる弾 (追尾弾・バルカン砲・エネルギー弾のルートか、その中の弾) を選択してください")
    return roots


def bullets_of(cmds, root):
    nodes = cmds.listRelatives(root, allDescendents=True, type="transform", fullPath=True) or []
    found = [n for n in nodes if BULLET_NAME.search(n.split("|")[-1])
             and cmds.listRelatives(n, shapes=True, type="mesh")]
    return sorted(found, key=lambda n: n.split("|")[-1])


def explosion_roots(cmds, mu):
    return [r for r in mu.all_effect_roots()
            if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) == EFFECT_TYPE]


def explosions_of(cmds, mu, source):
    return [r for r in explosion_roots(cmds, mu) if wx.find_link(cmds, r, SOURCE_ATTR) == source]


def selected_roots(cmds, mu, purpose):
    roots = []
    for r in mu.selected_effect_roots():
        kind = cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR)
        if kind == EFFECT_TYPE:
            found = [r]
        elif kind in BULLET_TYPES:
            found = explosions_of(cmds, mu, r)
        else:
            found = []
        roots += [x for x in found if x not in roots]
    if not roots:
        raise ToolError(u"%s爆発 (dfx_explosionN、または爆発させた弾) を選択してください" % purpose)
    return roots


def _log(ctx, root, source, hits):
    if hits:
        frames = sorted(h["frame"] for h in hits)
        ctx.log(u"  %s: %s の弾 %d 発がコリジョンに当たります (フレーム %.1f 〜 %.1f)"
                % (wx.short(root), wx.short(source), len(hits), frames[0], frames[-1]))
    else:
        ctx.log(u"  %s: %s の弾はコリジョンに当たりません (弾の通り道とコリジョンを確かめてください)"
                % (wx.short(root), wx.short(source)))


# ---------------------------------------------------------------- 当たり判定
def find_hits(cmds, bullets, collider_nodes, start, end, substeps):
    """弾ごとに、最初にコリジョンに当たった所。[{bullet, index, frame, pos, normal, collider}]

    弾の表示されている間を 1 フレーム substeps 回に分けてたどり、前の位置 → 今の位置 の線分が
    コリジョンのメッシュと交わるかを調べる。コリジョンの動きは、線分の両端をそれぞれの時刻の
    コリジョンの空間に直して扱う (動く・回る・拡大縮小するコリジョンに当たる)。
    """
    from maya.api import OpenMaya as om
    meshes = []
    for node in collider_nodes:
        for shape in collider_meshes(cmds, node):
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            xform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            meshes.append((node, xform, fn, fn.autoUniformGridParams()))
    cache = {}

    def matrices(xform, t):
        key = (xform, t)
        if key not in cache:
            m = om.MMatrix(cmds.getAttr(xform + ".worldMatrix[0]", time=t))
            cache[key] = (m, m.inverse())
        return cache[key]

    hits = []
    times = sample_times(start, end, substeps)
    for index, bullet in enumerate(bullets):
        prev = None
        for t in times:
            visible = cmds.getAttr(bullet + ".visibility", time=t)
            pos = cmds.getAttr(bullet + ".worldMatrix[0]", time=t)[12:15]
            if visible and prev is not None:
                hit = _first_hit(om, meshes, matrices, prev[0], prev[1], t, pos)
                if hit:
                    hit.update(bullet=bullet, index=index)
                    hits.append(hit)
                    break
            prev = (t, pos) if visible else None
    return hits


def _first_hit(om, meshes, matrices, t0, p0, t1, p1):
    best = None
    for node, xform, fn, accel in meshes:
        m0, inv0 = matrices(xform, t0)
        m1, inv1 = matrices(xform, t1)
        a = om.MPoint(*p0) * inv0
        b = om.MPoint(*p1) * inv1
        d = b - a
        if d.length() < 1e-9:
            continue
        r = fn.closestIntersection(om.MFloatPoint(a), om.MFloatVector(d), om.MSpace.kObject,
                                   1.0, False, accelParams=accel)
        if r is None:
            continue
        param = r[1]
        if param < 0 or param > 1 or (best and param >= best["param"]):
            continue
        n = om.MVector(fn.getPolygonNormal(r[2], om.MSpace.kObject))
        n = (n * inv1.transpose()).normalize()
        if n * om.MVector(*[y - x for x, y in zip(p0, p1)]) > 0:
            n = -n  # 裏から当たった時も、弾の来た方へ向ける
        best = {"param": param, "frame": t0 + (t1 - t0) * param,
                "pos": tuple(x + (y - x) * param for x, y in zip(p0, p1)),
                "normal": (n.x, n.y, n.z), "collider": node}
    return best


def collider_shapes(cmds, om, collider_nodes):
    """コリジョンのメッシュ [(ノード, transform, MFnMesh, 加速)]。"""
    meshes = []
    for node in collider_nodes:
        for shape in collider_meshes(cmds, node):
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            xform = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            meshes.append((node, xform, fn, fn.autoUniformGridParams()))
    return meshes


def probe_hits(cmds, collider_nodes, probes):
    """線分 [(フレーム, 始まり, 終わり)] ごとに、コリジョンに当たった所 (放電の爆発)。

    その時刻のコリジョンの形・位置で調べる。当たらない線分は飛ばす。
    """
    from maya.api import OpenMaya as om
    meshes = collider_shapes(cmds, om, collider_nodes)
    cache = {}

    def matrices(xform, t):
        key = (xform, t)
        if key not in cache:
            m = om.MMatrix(cmds.getAttr(xform + ".worldMatrix[0]", time=t))
            cache[key] = (m, m.inverse())
        return cache[key]

    hits = []
    for t, a, b in probes:
        hit = _first_hit(om, meshes, matrices, t, a, t, b)
        if hit:
            hit["frame"] = t
            hit["index"] = len(hits)
            hits.append(hit)
    return hits


def store_hits(cmds, root, hits):
    flat = []
    for h in hits:
        flat += [h["frame"], float(h["index"])] + list(h["pos"]) + list(h["normal"])
    cmds.setAttr(root + "." + HITS_ATTR, flat, type="doubleArray")


def read_hits(cmds, root):
    data = cmds.getAttr(root + "." + HITS_ATTR) or []
    return [{"frame": data[i], "index": int(data[i + 1]), "pos": tuple(data[i + 2:i + 5]),
             "normal": tuple(data[i + 5:i + 8])} for i in range(0, len(data), HIT_STRIDE)]


# ---------------------------------------------------------------- 作成・作り直し
def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    if not colliders(cmds):
        raise ToolError(u"コリジョンがありません。「コリジョンに指定」でぶつける相手を決めてください")
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません" % wx.short(root))
        source = wx.find_link(cmds, root, SOURCE_ATTR)
        if not source:
            raise ToolError(u"%s の弾が見つかりません (削除された可能性があります)" % wx.short(root))
        jobs.append((root, source))
    params = stored_params()
    for root, source in jobs:
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        hidden = _release_bullets(cmds, mu, root)
        mu.delete_owned(root)
        _show(cmds, hidden)
        mu.sync_param_attrs(root, params, values, LIVE_PARAMS,
                            keep=(SOURCE_ATTR, wx.CAMERA_ATTR))
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        hits = build_effect(cmds, mu, root, values)
        _log(ctx, root, source, hits)
    result = [r for r, _ in jobs]
    cmds.select(result)
    return result


def _show(cmds, plugs):
    for plug in plugs:
        if cmds.objExists(plug) and not cmds.listConnections(plug, source=True, destination=False):
            cmds.setAttr(plug, True)


def _release_bullets(cmds, mu, root):
    """root が隠している弾の lodVisibility のプラグ (削除した後で 1 に戻す)。"""
    owned = cmds.listConnections(root + "." + mu.OWNED_ATTR, source=True, destination=False) or []
    plugs = []
    for expr in cmds.ls(owned, type="expression") or []:
        plugs += [p for p in cmds.listConnections(expr, source=False, destination=True, plugs=True,
                                                  skipConversionNodes=True) or []
                  if p.endswith(".lodVisibility")]
    return plugs


def release(cmds, mu, root):
    """爆発を削除し、隠していた弾を元に戻す。"""
    plugs = _release_bullets(cmds, mu, root)
    mu.delete_owned(root)
    _show(cmds, plugs)
    cmds.delete(root)


def build_effect(cmds, mu, root, v):
    """当たりを計算し、爆発の部品とエクスプレッションを作る。当たりの一覧を返す。"""
    source = wx.find_link(cmds, root, SOURCE_ATTR)
    bullets = bullets_of(cmds, source)
    start = cmds.playbackOptions(query=True, minTime=True)
    end = cmds.playbackOptions(query=True, maxTime=True)
    hits = find_hits(cmds, bullets, colliders(cmds), start, end, int(v["substeps"]))
    store_hits(cmds, root, hits)

    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    build_parts(cmds, mu, root, v, hits, owned)

    # 当たった弾を、当たったフレームから隠す
    if hits:
        lines = [u"{b}.lodVisibility = ({r}.dfx_hide_bullet == 0) || (frame < {f!r});\n".format(
            b=wx.short(h["bullet"]), r=prefix, f=float(h["frame"])) for h in hits]
        owned.append(cmds.expression(name=prefix + "_hideBullets", alwaysEvaluate=True,
                                     string=u"".join(lines)))
    wx.finish(cmds, mu, root, owned)
    return hits


def build_parts(cmds, mu, root, v, hits, owned):
    """当たりごとの部品 (閃光・輪・火の玉・煙・火花) とエクスプレッション (放電の爆発も使う)。

    カメラの decomposeMatrix (wx.track_links) は作ってある前提。
    """
    from . import weather
    prefix = wx.short(root)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    pts, faces = wx.disc_points(16, 0.5)
    flash_src = wx.make_mesh(cmds, prefix + "_flashSrc", src_grp, pts, faces)
    ring_pts, ring_faces = wx.disc_points(24, 0.5, 0.42)
    ring_src = wx.make_mesh(cmds, prefix + "_ringSrc", src_grp, ring_pts, ring_faces)
    cpts, cfaces, cuvs = wx.card_points(-1.0, 0.0)
    spark_src = wx.make_mesh(cmds, prefix + "_sparkSrc", src_grp, cpts, cfaces, cuvs)
    fire_src = weather._puff_mesh(cmds, prefix + "_fireSrc", src_grp)
    smoke_src = weather._puff_mesh(cmds, prefix + "_smokeSrc", src_grp)
    cmds.setAttr(src_grp + ".visibility", 0)
    for src, color in ((flash_src, "flash_color"), (ring_src, "ring_color"),
                       (spark_src, "spark_color")):
        _, sg = wx.flat_shader(cmds, mu, root, "%s_%s_mat" % (prefix, color.split("_")[0]), owned,
                               color_attr=color, opacity_attr="opacity")
        cmds.sets(src, edit=True, forceElement=sg)
    for src, part in ((fire_src, "fire"), (smoke_src, "smoke")):
        sg = weather._toon_shader(cmds, mu, root, "%s_%s_mat" % (prefix, part), owned,
                                  color=part + "_color", shade=part + "_shade")
        cmds.sets(src, edit=True, forceElement=sg)

    n = len(hits)
    parts = (("flashes", flash_src, 1, _flash_body),
             ("rings", ring_src, 1, _ring_body),
             ("fire", fire_src, int(v["fire_count"]), _fire_body),
             ("smoke", smoke_src, int(v["smoke_count"]), _smoke_body),
             ("sparks", spark_src, int(v["spark_count"]), _spark_body))
    head = wx.mel_head(root, need_camera=True, need_wind=False)
    for name, src, per, body in parts:
        if per <= 0 or n == 0:
            continue
        nodes = wx.make_instances(cmds, root, src, "%s_%s" % (prefix, name), n * per, owned)
        _part_expressions(cmds, prefix, name, nodes, per, hits, head, body, owned)


def _part_expressions(cmds, prefix, name, nodes, per, hits, head, body, owned):
    """部品 (閃光など) を CHUNK 個ずつのエクスプレッションで動かす。当たりの値は定数で埋め込む。"""
    for a in range(0, len(nodes), wx.CHUNK):
        b = min(len(nodes), a + wx.CHUNK)
        e0, e1 = a // per, (b - 1) // per
        evs = hits[e0:e1 + 1]
        table = (u"float $evT[] = {%s};\n" % u", ".join(repr(float(h["frame"])) for h in evs)
                 + u"vector $evP[] = {%s};\n" % u", ".join(u"<<%r, %r, %r>>" % tuple(h["pos"]) for h in evs)
                 + u"vector $evN[] = {%s};\n" % u", ".join(u"<<%r, %r, %r>>" % tuple(h["normal"]) for h in evs))
        code = (head + table + u"float $S0 = %s.dfx_size;\nfloat $o[];\nint $i;\n" % prefix
                + u"for ($i = %d; $i < %d; $i++) {\n" % (a, b)
                + u"""int $e = $i / {per};
int $j = $i % {per};
float $t0 = $evT[$e - {e0}];
vector $P = $evP[$e - {e0}];
vector $N = $evN[$e - {e0}];
float $age = frame - $t0;
""".format(per=per, e0=e0) + body(prefix, a) + u"}\n" + wx.mel_writes(nodes[a:b], wx.TRANSFORM_OUTPUTS))
        owned.append(cmds.expression(name="%s_%s_%02d" % (prefix, name, a // wx.CHUNK + 1),
                                     alwaysEvaluate=True, unitConversion="all", string=code))


def _cone(spread, h1, h2):
    """$N を中心に spread 度までの向き $dir。"""
    return wx.mel_ortho("$A", "$N", "<<0, 1, 0>>") + u"""vector $B = cross($N, $A);
float $th = deg_to_rad({s} * {h1});
float $ph = 6.2831853 * {h2};
vector $dir = $N * cos($th) + ($A * cos($ph) + $B * sin($ph)) * sin($th);
""".format(s=spread, h1=h1, h2=h2)


def _flash_body(r, a):
    return (u"""float $life = max({r}.dfx_flash_frames, 0.01);
int $vis = ($age >= 0) && ($age <= $life) && ({r}.dfx_flash_size > 0);
float $sz = $S0 * {r}.dfx_flash_size * sin(3.1415927 * clamp(0, 1, $age / $life));
vector $p = $P + $N * ($S0 * 0.3);
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r) + wx.mel_billboard("$p", u"{h} * 360".format(h=H("$e", 0, 1))) + wx.mel_store_transform(a))


def _ring_body(r, a):
    return (u"""float $life = max({r}.dfx_ring_frames, 0.01);
float $k = clamp(0, 1, $age / $life);
int $vis = ($age >= 0) && ($age <= $life) && ({r}.dfx_ring_size > 0);
float $sz = 2 * $S0 * {r}.dfx_ring_size * (1 - pow(1 - $k, 3));
vector $p = $P + $N * ($S0 * 0.03);
vector $Z = $N;
""".format(r=r) + wx.mel_ortho("$X", "$Z", "<<0, 1, 0>>") + u"""vector $Y = cross($Z, $X);
vector $S = <<$sz, $sz, $sz>>;
""" + wx.mel_store_transform(a))


def _puff_rotation(r, k):
    return u"""float $rx = {h1} * 360 + 4 * $age;
float $ry = {h2} * 360;
float $rz = {h3} * 360 + 2 * $age;
""".format(h1=H("$e", "$j", k), h2=H("$e", "$j", k + 1), h3=H("$e", "$j", k + 2))


def _fire_body(r, a):
    from .weather import _store_euler
    return (u"""float $life = {r}.dfx_fire_frames * (0.7 + 0.6 * {h0});
""".format(r=r, h0=H("$e", "$j", 1)) + _cone(75, H("$e", "$j", 2), H("$e", "$j", 3)) + u"""
float $dist = $S0 * {r}.dfx_fire_spread * (1 - exp(-5 * max($age, 0) / $life));
vector $p = $P + $N * ($S0 * 0.25) + $dir * $dist;
float $sz = $S0 * {r}.dfx_fire_size * (0.5 + 0.5 * {h4}) * (1 - exp(-8 * max($age, 0) / $life));
if ($age > 0.6 * $life) $sz = $sz * max(0, ($life - $age) / (0.4 * $life));
int $vis = ($age >= 0) && ($age <= $life);
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r, h4=H("$e", "$j", 4)) + _puff_rotation(r, 5) + _store_euler(a))


def _smoke_body(r, a):
    from .weather import _store_euler
    return (u"""float $life = {r}.dfx_smoke_frames * (0.7 + 0.6 * {h0});
float $as = $age - (1.5 + 2.0 * {h1});
""".format(r=r, h0=H("$e", "$j", 11), h1=H("$e", "$j", 12)) + _cone(85, H("$e", "$j", 13), H("$e", "$j", 14)) + u"""
float $dist = $S0 * {r}.dfx_smoke_spread * (1 - exp(-3 * max($as, 0) / $life));
vector $p = $P + $N * ($S0 * 0.4) + $dir * $dist + <<0, {r}.dfx_smoke_rise * $S0 * max($as, 0), 0>>;
float $sz = $S0 * {r}.dfx_smoke_size * (0.4 + 0.6 * {h4}) * (0.3 + 0.7 * (1 - exp(-4 * max($as, 0) / $life)));
if ($as > 0.7 * $life) $sz = $sz * max(0, ($life - $as) / (0.3 * $life));
int $vis = ($as >= 0) && ($as <= $life);
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r, h4=H("$e", "$j", 15)) + _puff_rotation(r, 16).replace("$age", "$as") + _store_euler(a))


def _spark_body(r, a):
    return (u"""float $life = {r}.dfx_spark_frames * (0.6 + 0.8 * {h0});
""".format(r=r, h0=H("$e", "$j", 21)) + _cone(80, H("$e", "$j", 22), H("$e", "$j", 23)) + u"""
float $ag = max($age, 0);
float $g = {r}.dfx_gravity * $S0;
vector $v0 = $dir * ({r}.dfx_spark_speed * $S0 * (0.5 + {h4}));
vector $p = $P + $N * ($S0 * 0.05) + $v0 * $ag + <<0, -0.5 * $g * $ag * $ag, 0>>;
vector $vel = $v0 + <<0, -$g * $ag, 0>>;
float $fade = 1;
if ($ag > 0.7 * $life) $fade = max(0, ($life - $ag) / (0.3 * $life));
float $w = 0.04 * $S0 * $fade;
int $vis = ($age >= 0) && ($age <= $life);
vector $S = <<$w, max(mag($vel) * 1.5, $w), 1>>;
""".format(r=r, h4=H("$e", "$j", 24)) + wx.mel_streak("$p", "$vel") + wx.mel_store_transform(a))
