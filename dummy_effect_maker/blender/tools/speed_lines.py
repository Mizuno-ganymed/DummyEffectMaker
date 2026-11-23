# -*- coding: utf-8 -*-
"""スピードライン (Blender 版): カメラの前を、線が速いスピードで横切っていく。

Maya 版 (tools/speed_lines.py) と同じパラメータ・同じ計算 (plane_axes / plane_region /
line_state / line_seeds / width_at)。
    dfx_speedLinesN            ルート (パラメータ。dfx_camera = カメラ)
      dfx_speedLinesN_frame    カメラ前方。中は「画面の横幅の半分 = 1」の座標 (集中線と同じ)
        dfx_speedLinesN_band   中央の四角 (Empty。X / Y の移動・X / Y / Z の回転・X / Y の拡大縮小。キーも打てる)
        dfx_speedLinesN_lines  線 (全部で 1 つのメッシュ。毎フレーム頂点を置き直す)

線は四角のローカル X 方向 (回転 0 なら左 → 右) に流れ、四角の上辺より上と下辺より下だけに出る。
四角を Z 回転させると流れる向きが、X / Y 回転させると線が乗る面の奥行きの傾き (パース) が変わる
(回転の順番は XYZ = Maya 版の xyz と同じ)。Maya 版の _flow (四角の位置と回転に追従する面) は作らず、
面の上の位置 (u, v) から _frame の座標を直接計算して頂点に書く。

線の動きは Maya 版のエクスプレッションと同じく毎フレーム計算する (キーは打たない)。
線ごとの乱数 (ずれ・速さの倍率・太さの倍率) は作成・更新時に決めて線のオブジェクトに持たせる。
"""
from __future__ import absolute_import

import math

from ...core import BaseTool
from ...tools import speed_lines as msl
from .. import runtime as rt
from . import _build as bd, focus_lines as bfl

BAND_ROLE = "band"
# 線 1 本の頂点数: 上側 (入り → 終わり) と下側 (入り → 終わり)
LINE_VERTS = 2 * msl.WIDTH_SAMPLES


class SpeedLinesTool(BaseTool):
    label = msl.SpeedLinesTool.label
    tab = msl.TAB
    category = msl.CATEGORY
    order = msl.SpeedLinesTool.order
    hosts = ("blender",)
    tooltip = (u"カメラの前を線が速く横切るスピードラインを作成します。\n"
               u"カメラを選択して押してください (未選択ならシーンのカメラ)。\n"
               u"中央の四角 (dfx_speedLinesN_band の Empty) の上辺より上と下辺より下に線が流れます。\n"
               u"四角を Z 回転させると流れる向きが、X / Y 回転させると奥行きの傾き (パース) が変わります。\n"
               u"作成後はサイドバーの「選択したエフェクト」で調整できます。")
    params = msl.SpeedLinesTool.params

    def run(self, ctx, **v):
        cam = bfl.choose_camera(u"スピードライン")
        root = rt.new_root(msl.EFFECT_TYPE, "dfx_speedLines")
        rt.add_params(root, msl.stored_params(), v, msl.LIVE_PARAMS)
        root[msl.fl.CAMERA_ATTR] = cam
        add_band(root, (v["band_width"] / 2.0, v["band_height"] / 2.0))
        build(root, v)
        rt.update_root(root)
        bfl.select_only(root)
        ctx.log(u"  %s: %d 本 (カメラ: %s)" % (root.name, v["count"], cam.name))
        return root.name


def add_band(root, size):
    """中央の四角 (Z の移動と Z の拡大縮小だけロック。回転は X / Y / Z とも使える)。"""
    band = rt.add_control(root, BAND_ROLE, (0.0, 0.0, 0.0), display="CUBE", size=1.0)
    band.scale = (max(size[0], 1e-4), size[1], bfl.FLAT_Z)
    band.rotation_mode = "XYZ"
    band.lock_location = (False, False, True)
    band.lock_scale = (False, False, True)
    return band


# ---------------------------------------------------------------- 構築
def build(root, v):
    cam = bfl.camera_of(root)
    if cam is None:
        cam = bfl.choose_camera(u"スピードライン")
        root[msl.fl.CAMERA_ATTR] = cam
    frame = bfl.build_frame(root, cam)
    band = rt.control(root, BAND_ROLE)
    if band is None:   # 消された時は作り直す
        band = add_band(root, (0.5, 0.2))
    band.rotation_mode = "XYZ"   # Maya 版の回転の順番 (xyz) と同じ
    bfl.attach_control(band, frame)

    count = int(v["count"])
    mat = rt.flat_material(root.name + "_mat", v["color"], v["opacity"], owner=root)
    n = msl.WIDTH_SAMPLES
    # 上側 j と下側 j (同じ x) を結ぶ四角を並べる
    faces = [(j, n + j, n + j + 1, j + 1) for j in range(n - 1)]
    lines = bfl.mesh_object(root, root.name + "_lines", frame, LINE_VERTS, faces, count, mat)
    seeds = msl.line_seeds(count, v["seed"], v["speed_random"], v["thickness_random"])
    lines["dfx_phase"] = [s[0] for s in seeds]
    lines["dfx_speedf"] = [s[1] for s in seeds]
    lines["dfx_thick"] = [s[2] for s in seeds]
    return lines


# ---------------------------------------------------------------- 毎フレーム
def band_state(root, frame):
    """四角の (移動 X, 移動 Y, 回転 X, 回転 Y, 回転 Z (度)) と、高さの半分 (scaleY の絶対値)。"""
    band = rt.control(root, BAND_ROLE)
    if band is None:
        return (0.0, 0.0, 0.0, 0.0, 0.0), 0.2
    loc, rot, scl = bfl.basis_at(band, frame)
    return (loc[0], loc[1], math.degrees(rot[0]), math.degrees(rot[1]), math.degrees(rot[2])), abs(scl[1])


def line_vertices(v, frame, band, half_height, region, seeds):
    """線の頂点 (線の数 × LINE_VERTS, 3)。_frame の座標。

    面の上の線 (左端 u, 中心 v, 長さ) は line_state、太さは width_at (Maya 版と同じ)。
    """
    import numpy as np
    count = len(seeds)
    if not count:
        return np.zeros((0, 3))
    ex, ey, _ = msl.plane_axes(band[2], band[3], band[4])
    ex, ey = np.array(ex), np.array(ey)
    origin = np.array((band[0], band[1], 0.0))
    xs = np.array([j / float(msl.WIDTH_SAMPLES - 1) for j in range(msl.WIDTH_SAMPLES)])
    w = np.array([msl.width_at(x, v["width_start"], v["width_mid"], v["width_end"]) for x in xs])
    states = np.array([msl.line_state(frame, i, ph, sp, th, v, region, half_height)
                       for i, (ph, sp, th) in enumerate(seeds)])
    thick = np.array([s[2] for s in seeds])
    u = states[:, 0:1] + xs[None, :] * states[:, 2:3]                    # (本数, 頂点)
    top = states[:, 1:2] + w[None, :] * thick[:, None]
    bottom = states[:, 1:2] - w[None, :] * thick[:, None]
    uu = np.concatenate([u, u], axis=1)
    vv = np.concatenate([top, bottom], axis=1)
    co = origin[None, None, :] + uu[:, :, None] * ex[None, None, :] + vv[:, :, None] * ey[None, None, :]
    return co.reshape(-1, 3)


def seeds_of(lines):
    return list(zip(lines["dfx_phase"], lines["dfx_speedf"], lines["dfx_thick"]))


def region_at(root, frame, band=None):
    """線が乗る面の上での、画面の範囲 (plane_region)。"""
    if band is None:
        band, _ = band_state(root, frame)
    cam = bfl.camera_of(root)
    # カメラが無い時は Blender の既定のカメラ (50mm・センサー 36mm) とみなす
    zc = bfl.camera_depth(cam, frame=frame) if cam is not None else msl.camera_depth(50.0, 36.0 / 25.4)
    reach = rt.read_params(root, [p for p in msl.SpeedLinesTool.params if p.name == "reach"])["reach"]
    return msl.plane_region(bfl.render_aspect(), zc, band, reach)


def update(root, frame):
    lines = bfl.part(root, bfl.LINES_PART)
    if lines is None:
        return
    v = rt.read_params(root, msl.stored_params())
    mats = lines.data.materials
    if len(mats) and mats[0] is not None:
        rt.set_material(mats[0], v["color"], v["opacity"])
    bfl.check_frame(root, bfl.camera_of(root), v["distance"])
    band, half_height = band_state(root, frame)
    region = region_at(root, frame, band)
    bfl.write_vertices(lines, line_vertices(v, frame, band, half_height, region, seeds_of(lines)))


bd.register_builder(msl.EFFECT_TYPE, msl.stored_params, msl.LIVE_PARAMS, build, update)
