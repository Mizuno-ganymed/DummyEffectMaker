# -*- coding: utf-8 -*-
"""Blender 版の弾 (追尾弾・バルカン砲・エネルギー弾) の共通処理。

SAMPLERS[種類](root) は「時刻 t → 弾ごとの (ワールドの位置, 表示)」を返す関数を返す。
爆発の当たり判定は、これでフレームを動かさずに弾の通り道をたどる。

爆発が弾を隠す: 爆発のルート (dfx_source = 弾のルート) の当たりの表を、弾の更新の時に読む。
"""
from __future__ import absolute_import

import bpy

from .. import runtime as rt

EXPLOSION_TYPE = "explosion"
SOURCE_KEY = "dfx_source"
HITS_KEY = "dfxHits"
HIT_STRIDE = 8
SAMPLERS = {}   # effect_type → sampler(root) → at(t) → [(Vector, bool)]


def register_sampler(effect_type, fn):
    SAMPLERS[effect_type] = fn


def read_hits(root):
    data = list(root.get(HITS_KEY, []))
    return [{"frame": data[i], "index": int(data[i + 1]), "pos": tuple(data[i + 2:i + 5]),
             "normal": tuple(data[i + 5:i + 8])} for i in range(0, len(data), HIT_STRIDE)]


def explosions_of(source):
    return [r for r in rt.effect_roots()
            if r.get(rt.TYPE_KEY) == EXPLOSION_TYPE and r.get(SOURCE_KEY) == source]


def hide_frames(source):
    """弾の番号 → 隠し始めるフレーム (当たったフレーム)。爆発が無ければ空。"""
    out = {}
    for ex in explosions_of(source):
        if not ex.get("dfx_hide_bullet", True):
            continue
        for h in read_hits(ex):
            if h["index"] not in out or h["frame"] < out[h["index"]]:
                out[h["index"]] = h["frame"]
    return out


def is_hit(hidden, index, frame):
    return index in hidden and frame >= hidden[index]


def sphere_material(root, v, mesh_name, color, subdivisions):
    """弾の球 (共有メッシュ) とマテリアル。root に覚えておく (分割数・色はすぐ反映)。"""
    from . import _build as bd
    mesh = bd.sphere_mesh(root.name + mesh_name, subdivisions, root)
    mat = rt.flat_material(root.name + "_mat", color, 1.0, owner=root)
    mesh.materials.append(mat)
    root["dfx_mesh"] = mesh
    root["dfx_material"] = mat
    root["dfx_mesh_sub"] = int(subdivisions)
    return mesh, mat


def refresh_sphere(root, subdivisions, color):
    from . import _build as bd
    mesh, mat = root.get("dfx_mesh"), root.get("dfx_material")
    if mesh is not None and root.get("dfx_mesh_sub") != int(subdivisions):
        bd.fill_sphere(mesh, subdivisions)
        root["dfx_mesh_sub"] = int(subdivisions)
    if mat is not None:
        rt.set_material(mat, color)


def bullet_objects(root, key="dfx_fire"):
    return sorted((o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and key in o),
                  key=lambda o: o.name)
