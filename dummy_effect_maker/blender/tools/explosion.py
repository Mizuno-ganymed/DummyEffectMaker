# -*- coding: utf-8 -*-
"""爆発 (Blender 版): 追尾弾・バルカン砲・エネルギー弾が、コリジョンのモデルに当たったら爆発する。

Maya 版 (tools/explosion.py) と同じパラメータ・同じ計算 (当たり判定の考え方と motion.py)。
    1. ぶつける相手のメッシュ (複数可。動く・回る・拡大縮小するアニメーション可) を選んで「コリジョンに指定」
    2. 弾 (ルートか、その中の物) を選んで「爆発」。弾の通り道を 1 フレームを細かく分けてたどり、
       最初に当たった所で爆発する。当たった弾は消える
    3. 弾やコリジョンを動かしたら「爆発を更新」(当たる所を計算し直す)

    dfx_explosionN             ルート (パラメータ。dfx_source = 弾のルート、当たりの表 dfxHits)
      dfx_explosionN_flashes   閃光 / _rings 衝撃波の輪 / _fire 火の玉 / _smoke 煙 / _sparks 火花
                               (どれも 1 つのメッシュ。毎フレーム頂点を置き直す)
"""
from __future__ import absolute_import

import bpy
from mathutils.bvhtree import BVHTree

from ... import motion as mo
from ...core import BaseTool, ToolError
from ...tools import explosion as mx
from .. import runtime as rt
from . import _build as bd, _bullets as _bl, _wx as wx

COLLIDER_KEY = mx.COLLIDER_ATTR
PARTS = ("flashes", "rings", "fire", "smoke", "sparks")


def _tool(maya_tool, order):
    return dict(label=maya_tool.label, tab=mx.TAB, category=maya_tool.category, order=order,
                hosts=("blender",), params=maya_tool.params)


# ---------------------------------------------------------------- コリジョン
def collider_meshes(obj):
    """obj (と、その子) のメッシュ。"""
    out = [obj] if obj.type == "MESH" else []
    for child in obj.children_recursive:
        if child.type == "MESH":
            out.append(child)
    return out


def colliders():
    return [o for o in bpy.data.objects if o.get(COLLIDER_KEY)]


class ColliderSetTool(BaseTool):
    locals().update(_tool(mx.ColliderSetTool, 902))
    tooltip = (u"選択したモデルを、弾がぶつかるコリジョンにします (複数可。親なら子のメッシュも)。\n"
               u"ローポリで自由にモデリングしてよく、移動・回転・拡大縮小のアニメーションにも対応します。")

    def run(self, ctx, **v):
        objs = [o for o in bpy.context.selected_objects if rt.root_of(o) is None]
        if not objs:
            raise ToolError(u"コリジョンにするモデルを選択してください")
        for o in objs:
            if not collider_meshes(o):
                raise ToolError(u"%s にはメッシュがありません" % o.name)
        for o in objs:
            o[COLLIDER_KEY] = True
            ctx.log(u"  %s をコリジョンにしました" % o.name)
        ctx.log(u"  弾 (追尾弾・バルカン砲・エネルギー弾) を選んで「爆発」(作成済みなら「爆発を更新」) を押すと当たりを計算します")
        return [o.name for o in objs]


class ColliderClearTool(BaseTool):
    locals().update(_tool(mx.ColliderClearTool, 903))
    tooltip = mx.ColliderClearTool.tooltip

    def run(self, ctx, **v):
        objs = [o for o in bpy.context.selected_objects if o.get(COLLIDER_KEY) is not None] \
            if bpy.context.selected_objects else colliders()
        if not objs:
            raise ToolError(u"コリジョンに指定したモデルがありません")
        for o in objs:
            del o[COLLIDER_KEY]
            ctx.log(u"  %s をコリジョンから外しました" % o.name)
        return [o.name for o in objs]


# ---------------------------------------------------------------- 当たり判定
def find_hits(sample, count, collider_objs, start, end, substeps):
    """弾ごとに、最初にコリジョンに当たった所 [{index, frame, pos, normal, collider}] (Blender のワールド)。

    前の位置 → 今の位置 の線分を、それぞれの時刻のコリジョンの空間に直して調べる (Maya 版と同じ)。
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    meshes = []
    for node in collider_objs:
        for obj in collider_meshes(node):
            meshes.append((node, obj, BVHTree.FromObject(obj, depsgraph)))
    cache = {}

    def matrices(obj, t):
        key = (obj.name, t)
        if key not in cache:
            m = rt.matrix_world_at(obj, t)
            cache[key] = (m, m.inverted_safe())
        return cache[key]

    hits = []
    found = set()
    prev = [None] * count
    for t in mx.sample_times(start, end, substeps):
        states = sample(t)
        for index, (pos, vis) in enumerate(states):
            if index in found:
                continue
            if vis and prev[index] is not None:
                hit = _first_hit(meshes, matrices, prev[index][0], prev[index][1], t, pos)
                if hit:
                    hit["index"] = index
                    hits.append(hit)
                    found.add(index)
                    continue
            prev[index] = (t, pos.copy()) if vis else None
    hits.sort(key=lambda h: h["index"])
    return hits


def _first_hit(meshes, matrices, t0, p0, t1, p1):
    best = None
    for node, obj, tree in meshes:
        m0, inv0 = matrices(obj, t0)
        m1, inv1 = matrices(obj, t1)
        a = inv0 @ p0
        b = inv1 @ p1
        d = b - a
        if d.length < 1e-9:
            continue
        loc, normal, _, dist = tree.ray_cast(a, d.normalized(), d.length)
        if loc is None:
            continue
        param = dist / d.length
        if param < 0 or param > 1 or (best and param >= best["param"]):
            continue
        n = (inv1.to_3x3().transposed() @ normal).normalized()
        if n.dot(p1 - p0) > 0:
            n = -n   # 裏から当たった時も、弾の来た方へ向ける
        best = {"param": param, "frame": t0 + (t1 - t0) * param, "pos": tuple(p0.lerp(p1, param)),
                "normal": tuple(n), "collider": node.name}
    return best


def store_hits(root, hits):
    flat = []
    for h in hits:
        flat += [h["frame"], float(h["index"])] + list(h["pos"]) + list(h["normal"])
    root[_bl.HITS_KEY] = flat


# ---------------------------------------------------------------- ツール
class ExplosionTool(BaseTool):
    locals().update(_tool(mx.ExplosionTool, 904))
    tooltip = mx.ExplosionTool.tooltip

    def run(self, ctx, **v):
        sources = [r for r in rt.selected_roots(bpy.context) if r[rt.TYPE_KEY] in mx.BULLET_TYPES]
        if not sources:
            raise ToolError(u"爆発させる弾 (追尾弾・バルカン砲・エネルギー弾のルートか、その中の弾) を選択してください")
        if not colliders():
            raise ToolError(u"コリジョンがありません。ぶつける相手のモデルを選んで「コリジョンに指定」を押してください")
        cam = wx.choose_camera(bpy.context)
        roots = []
        for source in sources:
            existing = _bl.explosions_of(source)
            if existing:
                ctx.log(u"  %s には既に爆発 (%s) があるので、当たりを計算し直します" % (source.name, existing[0].name))
                bd.rebuild(existing[0])
                _log(ctx, existing[0])
                roots.append(existing[0])
                continue
            root = rt.new_root(mx.EFFECT_TYPE, "dfx_explosion")
            rt.add_params(root, mx.stored_params(), v, mx.LIVE_PARAMS)
            root[_bl.SOURCE_KEY] = source
            if cam is not None:
                root[wx.CAMERA_KEY] = cam
            build(root, v)
            rt.update_root(root)
            rt.update_root(source)
            _log(ctx, root)
            roots.append(root)
        wx.select_only(roots[0])
        return [r.name for r in roots]


class ExplosionUpdateTool(BaseTool):
    locals().update(_tool(mx.ExplosionUpdateTool, 906))
    tooltip = mx.ExplosionUpdateTool.tooltip

    def run(self, ctx, **v):
        roots = selected_explosions(u"更新する")
        for root in roots:
            bd.rebuild(root)
            rt.update_root(root[_bl.SOURCE_KEY])
            _log(ctx, root)
        return [r.name for r in roots]


class ExplosionRemoveTool(BaseTool):
    locals().update(_tool(mx.ExplosionRemoveTool, 907))
    tooltip = mx.ExplosionRemoveTool.tooltip

    def run(self, ctx, **v):
        roots = selected_explosions(u"外す")
        for root in roots:
            source = root.get(_bl.SOURCE_KEY)
            name = root.name
            rt.delete_owned(root)
            bpy.data.objects.remove(root, do_unlink=True)
            if source is not None:
                rt.update_root(source)
            ctx.log(u"  %s を外しました" % name)
        return len(roots)


def selected_explosions(purpose):
    roots = []
    for r in rt.selected_roots(bpy.context):
        if r[rt.TYPE_KEY] == mx.EFFECT_TYPE:
            found = [r]
        elif r[rt.TYPE_KEY] in mx.BULLET_TYPES:
            found = _bl.explosions_of(r)
        else:
            found = []
        roots += [x for x in found if x not in roots]
    if not roots:
        raise ToolError(u"%s爆発 (dfx_explosionN、または爆発させた弾) を選択してください" % purpose)
    return roots


def _log(ctx, root):
    hits = _bl.read_hits(root)
    source = root.get(_bl.SOURCE_KEY)
    name = source.name if source is not None else u"?"
    if hits:
        frames = sorted(h["frame"] for h in hits)
        ctx.log(u"  %s: %s の弾 %d 発がコリジョンに当たります (フレーム %.1f 〜 %.1f)"
                % (root.name, name, len(hits), frames[0], frames[-1]))
    else:
        ctx.log(u"  %s: %s の弾はコリジョンに当たりません (弾の通り道とコリジョンを確かめてください)"
                % (root.name, name))


# ---------------------------------------------------------------- 作成・毎フレーム
def build(root, v):
    """当たりを計算し、爆発の部品を作る。"""
    source = root.get(_bl.SOURCE_KEY)
    if source is None:
        raise ToolError(u"%s の弾が見つかりません (削除された可能性があります)" % root.name)
    if not colliders():
        raise ToolError(u"コリジョンがありません。「コリジョンに指定」でぶつける相手を決めてください")
    sampler = _bl.SAMPLERS[source[rt.TYPE_KEY]](source)
    count = len(sampler(bpy.context.scene.frame_start))
    scene = bpy.context.scene
    hits = find_hits(sampler, count, colliders(), scene.frame_start, scene.frame_end, int(v["substeps"]))
    store_hits(root, hits)
    n = len(hits)
    if n == 0:
        return
    for part, template, per, mat in (
            ("flashes", wx.disc_template(16, 0.5), 1,
             rt.flat_material(root.name + "_flash_mat", v["flash_color"], v["opacity"], owner=root)),
            ("rings", wx.disc_template(24, 0.5, 0.42), 1,
             rt.flat_material(root.name + "_ring_mat", v["ring_color"], v["opacity"], owner=root)),
            ("fire", wx.puff_template(), int(v["fire_count"]),
             wx.toon_material(root.name + "_fire_mat", v["fire_color"], v["fire_shade"], v["opacity"], root)),
            ("smoke", wx.puff_template(), int(v["smoke_count"]),
             wx.toon_material(root.name + "_smoke_mat", v["smoke_color"], v["smoke_shade"], v["opacity"], root)),
            ("sparks", wx.card_template(-1.0, 0.0), int(v["spark_count"]),
             rt.flat_material(root.name + "_spark_mat", v["spark_color"], v["opacity"], owner=root))):
        if per > 0:
            obj = wx.particle_object(root, "%s_%s" % (root.name, part), template, n * per, mat,
                                     smooth=part in ("fire", "smoke"))
            obj.data["dfx_per"] = per


def yup_hits(root):
    return [dict(h, pos=mo.yup_vector(h["pos"]), normal=mo.yup_vector(h["normal"])) for h in _bl.read_hits(root)]


def update(root, frame):
    v = rt.read_params(root, mx.stored_params())
    hits = yup_hits(root)
    objs = dict((p, wx.part(root, "_" + p)) for p in PARTS)
    for part, color in (("flashes", "flash_color"), ("rings", "ring_color"), ("sparks", "spark_color")):
        if objs[part] is not None:
            rt.set_material(wx.material_of(objs[part]), v[color], v["opacity"])
    for part in ("fire", "smoke"):
        if objs[part] is not None:
            wx.set_toon_material(wx.material_of(objs[part]), v[part + "_color"], v[part + "_shade"], v["opacity"])
    if not hits:
        return
    cp, cdir = wx.camera_yup(root)
    if objs["flashes"] is not None:
        out = []
        for e, hit in enumerate(hits):
            p, sz, vis, roll = mo.explosion_flash(e, frame, hit, v)
            out.append((p, mo.billboard(p, cp, cdir, roll), (sz,) * 3) if vis and sz > 1e-9 else wx.hidden(p))
        wx.write_particles(objs["flashes"], out)
    if objs["rings"] is not None:
        out = []
        for e, hit in enumerate(hits):
            p, axes, sz, vis = mo.explosion_ring(e, frame, hit, v)
            out.append((p, axes, (sz,) * 3) if vis and sz > 1e-9 else wx.hidden(p))
        wx.write_particles(objs["rings"], out)
    for part, fn in (("fire", mo.explosion_fire), ("smoke", mo.explosion_smoke)):
        obj = objs[part]
        if obj is None:
            continue
        per = obj.data["dfx_per"]
        out = []
        for e, hit in enumerate(hits):
            for j in range(per):
                p, sz, vis, rot = fn(e, j, frame, hit, v)
                out.append((p, wx.euler_axes(*rot), (sz,) * 3) if vis and sz > 1e-9 else wx.hidden(p))
        wx.write_particles(obj, out)
    obj = objs["sparks"]
    if obj is not None:
        per = obj.data["dfx_per"]
        out = []
        for e, hit in enumerate(hits):
            for j in range(per):
                p, vel, scale, vis = mo.explosion_spark(e, j, frame, hit, v)
                out.append((p, mo.streak(p, vel, cp, cdir), scale) if vis and scale[0] > 1e-9 else wx.hidden(p))
        wx.write_particles(obj, out)


def _on_hide_change(root, frame):
    update(root, frame)
    source = root.get(_bl.SOURCE_KEY)
    if source is not None and root.get("dfx_hide_seen") != bool(root.get("dfx_hide_bullet", True)):
        root["dfx_hide_seen"] = bool(root.get("dfx_hide_bullet", True))
        rt.update_root(source, frame)


bd.register_builder(mx.EFFECT_TYPE, mx.stored_params, mx.LIVE_PARAMS, build, _on_hide_change)
