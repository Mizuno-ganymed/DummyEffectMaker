# -*- coding: utf-8 -*-
"""エネルギー弾 (Blender 版): 発射位置ごとに 1 つの球が出現し、ガタガタぶれながら目標へ飛ぶ。

Maya 版 (tools/energy_ball.py) と同じパラメータ・同じ計算 (flight_progress / appear_scale)。
    dfx_energyN               ルート (全体のパラメータ)
      dfx_energyN_start_01    発射位置 (動かしてよい。球ごとのタイミングをカスタムプロパティで持つ)
        dfx_energyN_ball_01   球 (発射位置の子)
      dfx_energyN_target_01   目標 (動かしてよい)

球ごとのタイミング (発射位置の dfx_appear_start / dfx_fly_start / dfx_fly_end) にはキーを打ってよい。
ぶれ (ジッター) は Blender のノイズを使う (Maya の noise とは細かな動きが違う)。
"""
from __future__ import absolute_import

import bpy
from mathutils import Vector, noise

from ... import motion as mo
from ...core import BaseTool
from ...tools import energy_ball as me
from .. import runtime as rt
from . import _build as bd, _bullets as _bl

TIMING_KEYS = [k for k, _ in me.BALL_ATTRS]


class EnergyBallTool(BaseTool):
    label = me.EnergyBallTool.label
    tab = me.TAB
    category = me.CATEGORY
    order = 700
    hosts = ("blender",)
    tooltip = (u"発射位置の Empty と、その子の球、目標の Empty を指定した数だけ作ります。\n"
               u"球は大きさ 0 から出現し、ガタガタぶれながら目標へ飛んでいきます。\n"
               u"球ごとのタイミングは発射位置の Empty のカスタムプロパティで変えられます。\n"
               u"個数を変えたら「エフェクトを更新」を押します。")
    params = me.EnergyBallTool.params

    def run(self, ctx, **v):
        root = rt.new_root(me.EFFECT_TYPE, "dfx_energy")
        rt.add_params(root, me.stored_params(), v, me.LIVE_PARAMS)
        build(root, v)
        rt.update_root(root)
        from . import _wx
        _wx.select_only(root)
        ctx.log(u"  %s: %d 個" % (root.name, len(pairs(root))))
        return root.name


def pairs(root):
    """(発射位置, 目標) を番号順に (目標が消されたものは除く)。"""
    starts = [c for c in rt.controls(root) if me.INDEX_ATTR in c and c.get(me.TARGET_ATTR) is not None]
    return [(s, s[me.TARGET_ATTR]) for s in sorted(starts, key=lambda s: s[me.INDEX_ATTR])]


def _add_pair(root, v, index, count):
    start_pos, target_pos = me.default_positions(v, index, count)
    start = rt.add_control(root, "start_%02d" % (index + 1), mo.zup_vector(start_pos), display="SPHERE",
                           size=0.3)
    target = rt.add_control(root, "target_%02d" % (index + 1), mo.zup_vector(target_pos),
                            display="CONE", size=0.3)
    start[me.INDEX_ATTR] = index
    for key, value in zip(TIMING_KEYS, me.default_timing(v, index)):
        start[key] = float(value)
    start[me.TARGET_ATTR] = target
    return start, target


def build(root, v):
    count = int(v["count"])
    existing = pairs(root)
    for start, target in existing[count:]:          # 減らした分
        for obj in (start, target):
            bpy.data.objects.remove(obj, do_unlink=True)
    existing = existing[:count]
    next_index = max([s[me.INDEX_ATTR] for s, _ in existing] + [-1]) + 1
    while len(existing) < count:                    # 増やした分
        existing.append(_add_pair(root, v, next_index, count))
        next_index += 1
    mesh = bd.sphere_mesh(root.name + "_ballMesh", 16, root)
    mat = rt.flat_material(root.name + "_mat", v["color"], 1.0, owner=root)
    mesh.materials.append(mat)
    root["dfx_material"] = mat
    for start, _ in existing:
        ball = bd.new_object(root, "%s_ball_%02d" % (root.name, start[me.INDEX_ATTR] + 1), mesh, parent=start)
        ball["dfx_ball"] = True


def balls(root):
    out = [o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and o.get("dfx_ball")]
    return sorted(out, key=lambda o: o.name)


def _timing(start, t):
    return [rt.prop_at(start, k, t) for k in TIMING_KEYS]


def _local(v, start, target, index, t, m_start=None, m_target=None):
    """球の、発射位置から見た (位置, 大きさ, 表示)。Maya 版のエクスプレッションと同じ式。"""
    t0, t1, t2 = _timing(start, t)
    grow = me.appear_scale(t, t0, v["appear_frames"])
    p = me.flight_progress(t, t1, t2, v["fly_ease"])
    m_start = m_start if m_start is not None else rt.matrix_world_at(start, t)
    m_target = m_target if m_target is not None else rt.matrix_world_at(target, t)
    local = m_start.inverted_safe() @ m_target.translation
    rad = v["radius"]
    j = v["jitter"] * rad
    k = t * v["jitter_speed"]
    seeds = [index * 31.7 + n * 113.3 for n in range(3)]
    jit = Vector([j * noise.noise(Vector((k + s, 0.5, 0.5))) for s in seeds])
    vis = t >= t0 and (v["after_hit"] == me.AFTER_STAY or t <= t2)
    return local * p + jit, rad * grow, vis


def sampler(root):
    v = rt.read_params(root, me.stored_params())
    pr = pairs(root)

    def at(t):
        out = []
        for start, target in pr:
            m = rt.matrix_world_at(start, t)
            local, _, vis = _local(v, start, target, start[me.INDEX_ATTR], t, m_start=m)
            out.append((m @ local, vis))
        return out
    return at


def update(root, frame):
    v = rt.read_params(root, me.stored_params())
    mat = root.get("dfx_material")
    if mat is not None:
        rt.set_material(mat, v["color"])
    hidden = _bl.hide_frames(root)
    by_start = dict((b.parent, b) for b in balls(root))
    for i, (start, target) in enumerate(pairs(root)):
        ball = by_start.get(start)
        if ball is None:
            continue
        local, s, vis = _local(v, start, target, start[me.INDEX_ATTR], frame,
                               m_start=start.matrix_world, m_target=target.matrix_world)
        if not vis or _bl.is_hit(hidden, i, frame):
            ball.scale = (0.0, 0.0, 0.0)
            continue
        ball.location = local
        ball.scale = (s, s, s)


bd.register_builder(me.EFFECT_TYPE, me.stored_params, me.LIVE_PARAMS, build, update)
_bl.register_sampler(me.EFFECT_TYPE, sampler)
