# -*- coding: utf-8 -*-
"""Blender 版のツールの共通処理 (メッシュを作る・作り直しの登録)。"""
from __future__ import absolute_import

import math

import bmesh
import bpy

from .. import runtime as rt

BUILDERS = {}   # effect_type → (stored_params(), live, build(root, values))
RELEASERS = {}  # effect_type → release(root): 消す前に、ほかの物に加えた変更を元に戻す (画ぶれなど)


def register_builder(effect_type, stored_params, live, build, update):
    BUILDERS[effect_type] = (stored_params, live, build)
    rt.register_updater(effect_type, update)


def register_releaser(effect_type, fn):
    RELEASERS[effect_type] = fn


def release(root):
    fn = RELEASERS.get(root.get(rt.TYPE_KEY))
    if fn is not None:
        fn(root)


def rebuild(root, overrides=None):
    """ルートの値で作り直す (コントロールはそのまま)。"""
    stored_params, live, build = BUILDERS[root[rt.TYPE_KEY]]
    params = stored_params()
    values = rt.read_params(root, params)
    if overrides:
        values.update(overrides)
        rt.write_params(root, params, values)
    rt.delete_owned(root)
    build(root, values)
    rt.update_root(root)
    return root


def new_object(root, name, mesh, parent=None):
    obj = bpy.data.objects.new(name, mesh)
    rt.collection().objects.link(obj)
    obj.parent = parent if parent is not None else root
    rt.own(root, obj)
    return obj


def sphere_mesh(name, subdivisions, owner):
    mesh = bpy.data.meshes.new(name)
    fill_sphere(mesh, subdivisions)
    rt.own(owner, mesh)
    return mesh


def fill_sphere(mesh, subdivisions):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=max(3, int(subdivisions)),
                              v_segments=max(2, int(subdivisions) // 2), radius=1.0)
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = True


def torus_points(sub_x, sub_y, tube):
    """XY 面のトーラス (半径 1、チューブの半径 tube) の頂点と面。"""
    pts, faces = [], []
    for i in range(sub_x):
        u = 2 * math.pi * i / sub_x
        for j in range(sub_y):
            w = 2 * math.pi * j / sub_y
            r = 1.0 + tube * math.cos(w)
            pts.append((r * math.cos(u), r * math.sin(u), tube * math.sin(w)))
    for i in range(sub_x):
        for j in range(sub_y):
            a = i * sub_y + j
            b = ((i + 1) % sub_x) * sub_y + j
            c = ((i + 1) % sub_x) * sub_y + (j + 1) % sub_y
            d = i * sub_y + (j + 1) % sub_y
            faces.append((a, b, c, d))
    return pts, faces
