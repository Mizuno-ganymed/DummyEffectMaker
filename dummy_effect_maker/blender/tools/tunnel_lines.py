# -*- coding: utf-8 -*-
"""スピードライン (トンネル) (Blender 版): 消失点が画面の中にあるスピードライン。

Maya 版 (tools/tunnel_lines.py) と同じパラメータ・同じ計算 (motion.tunnel_basis / tunnel_item)。
    dfx_tunnelN           ルート (パラメータ。dfx_camera = カメラ)
      dfx_tunnelN_vanish  消失点 (Empty。チャイルドオブでカメラに付く。位置はカメラの空間で、-Z が前)
      dfx_tunnelN_items   線・形 (全部で 1 つのメッシュ。毎フレーム頂点を置き直す)
カメラの空間 (X 右・Y 上・-Z 前) は Maya と同じなので、Y が上への直しはいらない。
"""
from __future__ import absolute_import

import bpy
from mathutils import Matrix, Vector

from ... import motion as mo
from ...core import BaseTool, ToolError
from ...tools import tunnel_lines as mt
from .. import runtime as rt
from . import _build as bd, _wx as wx


class TunnelLinesTool(BaseTool):
    label = mt.TunnelLinesTool.label
    tab = mt.TunnelLinesTool.tab
    category = mt.TunnelLinesTool.category
    order = 636
    hosts = ("blender",)
    tooltip = (u"カメラがトンネルの中にいるような、消失点が画面の中にあるスピードラインを作ります。\n"
               u"カメラを選んで押してください (未選択ならシーンのカメラ)。\n"
               u"消失点の Empty (_vanish) を X / Y に動かすと、消失点が画面の中で動きます。\n"
               u"「回転の速さ」を 0 以外にすると、トンネルが回って線が弧を描いて飛んできます。\n"
               u"線のほかに、星・ハート・菱形も流せます。形・数を変えたら「エフェクトを更新」。")
    params = mt.TunnelLinesTool.params

    def run(self, ctx, **v):
        kind = mt.KIND
        root, cam, _ = wx.create_root(bpy.context, ctx, kind.effect_type, "dfx_tunnel", kind.stored_params(),
                                      kind.live, v, need_camera=True, need_wind=False)
        if cam is None:
            bpy.data.objects.remove(root, do_unlink=True)
            raise ToolError(u"カメラがありません。カメラを選ぶか、シーンのカメラを決めてください")
        vanish = rt.add_control(root, "vanish", (0.0, 0.0, -20.0), display="SPHERE", size=0.5)
        con = vanish.constraints.new("CHILD_OF")   # カメラに付いて動く (位置はカメラの空間)
        con.target = cam
        con.inverse_matrix = Matrix.Identity(4)
        con.set_inverse_pending = False
        build(root, v)
        rt.update_root(root)
        wx.select_only(root)
        return root.name


def build(root, v):
    pts, faces = mt.shape_points(v["shape"])
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    count = int(v["count"])
    obj = wx.particle_object(root, root.name + "_items", (pts, [tuple(f) for f in faces], None), count, mat)
    obj.data["dfx_line"] = v["shape"] == mt.SHAPE_LINE


def camera_space(root):
    """(カメラの行列 (大きさなし), 消失点のカメラの空間の位置)。"""
    cam = root.get(wx.CAMERA_KEY)
    vanish = rt.control(root, "vanish")
    if cam is None or vanish is None:
        return None, None
    loc, rot, _ = cam.matrix_world.decompose()
    cm = Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
    return cm, tuple(cm.inverted_safe() @ vanish.matrix_world.translation)


def item_vertices(obj, v, frame, cm, vanish):
    """全部の線・形の頂点 (ワールド)。トンネルの空間 → カメラの空間 → ワールド。"""
    tmpl = list(obj.data["dfx_template"])
    n = len(tmpl) // 3
    bx, by, bz = mo.tunnel_basis(vanish)
    tun = Matrix(((bx[0], by[0], bz[0], 0.0), (bx[1], by[1], bz[1], 0.0), (bx[2], by[2], bz[2], 0.0),
                  (0.0, 0.0, 0.0, 1.0)))
    world = cm @ tun
    line = bool(obj.data["dfx_line"])
    pts = [tmpl[3 * k:3 * k + 3] for k in range(n)]
    if line:   # 線: X は左右の目印 (±1)。本当の X は ±太さ (入り Y = -1 → 終わり Y = 0)
        pts = [(t[0] * mo.tunnel_width_at(t[1] + 1.0, v), t[1], t[2]) for t in pts]
    out = []
    for i in range(obj.data["dfx_count"]):
        p, axes, s = mo.tunnel_item(i, frame, v, line)
        base = Vector(p)
        ax = [Vector(axes[k]) * s[k] for k in range(3)]
        for t in pts:
            out.append(world @ (base + ax[0] * t[0] + ax[1] * t[1] + ax[2] * t[2]))
    return out


def update(root, frame):
    v = rt.read_params(root, mt.KIND.stored_params())
    obj = wx.part(root, "_items")
    cm, vanish = camera_space(root)
    if obj is None or cm is None:
        return
    rt.set_material(wx.material_of(obj), v["color"], v["opacity"])
    co = [c for p in item_vertices(obj, v, frame, cm, vanish) for c in p]
    obj.data.vertices.foreach_set("co", co)
    obj.data.update()


bd.register_builder(mt.EFFECT_TYPE, mt.KIND.stored_params, mt.KIND.live, build, update)
