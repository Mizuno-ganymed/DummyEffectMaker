# -*- coding: utf-8 -*-
"""書き出し (Unreal 用): シーンのダミーエフェクトを、ショットのファイル (JSON) に書き出す。

Unreal Engine 側で Niagara を置き直して仕上げるための「設計図」を書く:
    - エフェクトの種類とパラメータ (と、その定義)
    - 動かして決めた物 (ロケータ・範囲の箱・出る場所・カメラ) の毎フレームの位置・向き
    - 弾の通り道、爆発の当たった所、風の流れ
座標は共通の空間 (右手系・Z が上・メートル) に直して書く。形式は docs/exchange_format.md。
"""
from __future__ import absolute_import

import os

from ..core import BaseTool, Param, ToolError
from ..core import exchange as xc

TARGET_SELECTED = u"選択したエフェクト"
TARGET_ALL = u"シーン内のすべてのエフェクト"
RANGE_TIMELINE = u"タイムスライダーの範囲"
RANGE_CUSTOM = u"指定した範囲"
FILE_FILTER = u"ショット (*.json)"


class ExportShotTool(BaseTool):
    label = u"書き出し (Unreal 用)"
    menu = u"シーン"
    category = u"書き出し"
    order = 520
    undoable = False
    tooltip = (u"ダミーエフェクトを、Unreal Engine で仕上げるためのショットのファイル (JSON) に書き出します。\n"
               u"エフェクトの種類とパラメータ、ロケータ・カメラの動き、弾の通り道、当たった所などを書きます。")
    params = [
        Param("target", u"対象", TARGET_ALL, kind="choice", choices=[TARGET_ALL, TARGET_SELECTED]),
        Param("range_mode", u"フレーム範囲", RANGE_TIMELINE, kind="choice",
              choices=[RANGE_TIMELINE, RANGE_CUSTOM]),
        Param("start_frame", u"開始フレーム", 1.0, decimals=2, tooltip=u"「指定した範囲」の時のみ"),
        Param("end_frame", u"終了フレーム", 120.0, decimals=2, tooltip=u"「指定した範囲」の時のみ"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = mu.all_effect_roots() if v["target"] == TARGET_ALL else mu.selected_effect_roots()
        if not roots:
            raise ToolError(u"書き出すダミーエフェクトがありません")
        if v["range_mode"] == RANGE_TIMELINE:
            start = cmds.playbackOptions(query=True, minTime=True)
            end = cmds.playbackOptions(query=True, maxTime=True)
        else:
            start, end = v["start_frame"], v["end_frame"]
        if end < start:
            raise ToolError(u"終了フレームが開始フレームより前になっています")
        path = mu.ask_file(True, default_folder(cmds), self.label, FILE_FILTER)
        if not path:
            ctx.log(u"  キャンセルしました")
            return None
        if not path.lower().endswith(".json"):
            path += ".json"
        shot = build_shot(cmds, mu, roots, start, end)
        xc.write_shot(path, shot)
        ctx.log(u"  %d 個のエフェクト、カメラ %d 台 (フレーム %s 〜 %s) を書き出しました: %s"
                % (len(shot["effects"]), len(shot["cameras"]), start, end, path))
        return path


def default_folder(cmds):
    root = cmds.workspace(query=True, rootDirectory=True) or os.path.expanduser("~")
    folder = os.path.join(root, "data", "dfx_shots")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return folder


# ---------------------------------------------------------------- 集める
class _Space(object):
    """Maya の空間 → 共通の空間。"""

    def __init__(self, cmds):
        self.up = cmds.upAxis(query=True, axis=True).lower()
        unit = cmds.currentUnit(query=True, linear=True)
        self.scale = xc.UNIT_TO_METER.get(unit, 0.01)

    def point(self, p):
        return xc.convert_point(p, self.up, self.scale)

    def vector(self, v, length=False):
        """向き。length=True なら長さも単位を直す (速さなど)。"""
        return xc.convert_point(v, self.up, self.scale if length else 1.0)

    def transform(self, m):
        t, q, s = xc.decompose(xc.convert_matrix(m, self.up, self.scale))
        return {"t": t, "q": q, "s": s}


def build_shot(cmds, mu, roots, start, end):
    from maya import mel
    from .. import __version__
    space = _Space(cmds)
    frames = frame_list(start, end)
    source = {"app": "maya", "app_version": cmds.about(version=True),
              "scene": cmds.file(query=True, sceneName=True) or "", "tool_version": __version__,
              "up_axis": space.up, "linear_unit": cmds.currentUnit(query=True, linear=True)}
    shot = xc.new_shot(source, mel.eval("currentTimeUnitToFPS()"), start, end)
    cameras = []
    for root in roots:
        effect = export_effect(cmds, mu, space, root, frames)
        shot["effects"].append(effect)
        cam = effect["links"].get("dfx_camera")
        if cam and cam not in cameras:
            cameras.append(cam)
    for node in cmds.ls(selection=True, long=True) or []:
        if cmds.listRelatives(node, shapes=True, type="camera") and node not in cameras:
            cameras.append(node)
    shot["cameras"] = [export_camera(cmds, space, c, frames) for c in cameras if cmds.objExists(c)]
    for effect in shot["effects"]:  # つながりは短い名前で書く
        effect["links"] = dict((k, v.split("|")[-1]) for k, v in effect["links"].items())
    return shot


def frame_list(start, end):
    first, last = int(round(start)), int(round(end))
    return list(range(first, last + 1))


def _links(cmds, mu, root):
    """ルートのメッセージアトリビュートにつながった物 {アトリビュート: ノード}。"""
    out = {}
    for attr in cmds.listAttr(root, userDefined=True) or []:
        if attr == mu.OWNED_ATTR or not cmds.attributeQuery(attr, node=root, exists=True):
            continue
        if cmds.getAttr(root + "." + attr, type=True) != "message":
            continue
        conns = cmds.listConnections(root + "." + attr, source=True, destination=False) or []
        if conns:
            out[attr] = cmds.ls(conns[0], long=True)[0]
    return out


def _controls(cmds, mu, root, links):
    """動かして決める物: ルートの直接の子のうち、ツールが作り直さない物 + つながった物 (カメラ・風以外)。"""
    owned = set(cmds.ls(cmds.listConnections(root + "." + mu.OWNED_ATTR, source=True,
                                             destination=False) or [], long=True) or [])
    nodes = []
    for child in cmds.listRelatives(root, children=True, type="transform", fullPath=True) or []:
        if child not in owned:
            nodes.append(("child", child))
    for attr, node in sorted(links.items()):
        if attr in ("dfx_camera", "dfx_wind", "dfx_source"):
            continue
        if cmds.objectType(node, isAType="transform") and node not in [n for _, n in nodes]:
            nodes.append((attr, node))
    return nodes


def _shape_type(cmds, node):
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    return cmds.nodeType(shapes[0]) if shapes else "transform"


def sample_transform(cmds, space, node, frames, extra=None):
    samples = []
    for f in frames:
        s = space.transform(cmds.getAttr(node + ".worldMatrix[0]", time=f))
        if extra:
            s.update(extra(f))
        samples.append(s)
    return xc.make_track(frames, samples)


def export_effect(cmds, mu, space, root, frames):
    from .. import catalog
    effect_type = cmds.getAttr(root + "." + mu.EFFECT_TYPE_ATTR)
    try:
        spec = catalog.spec(effect_type)
        params = mu.read_param_values(root, spec.params)
        out = {"type": effect_type, "label": spec.label, "group": spec.group,
               "params": xc.param_values(spec.params, params), "param_info": spec.describe()}
    except KeyError:
        out = {"type": effect_type, "label": effect_type, "group": "", "params": {},
               "param_info": {}}
    links = _links(cmds, mu, root)
    out.update(name=root.split("|")[-1], baked=bool(mu.is_baked(root)), links=links,
               controls=[{"name": node.split("|")[-1], "role": role,
                          "shape": _shape_type(cmds, node),
                          "track": sample_transform(cmds, space, node, frames)}
                         for role, node in _controls(cmds, mu, root, links)],
               data=export_data(cmds, mu, space, root, effect_type, frames))
    return out


def export_data(cmds, mu, space, root, effect_type, frames):
    """種類ごとの追加の情報 (弾の通り道・当たった所・風の流れ)。"""
    from . import explosion
    data = {}
    if effect_type in explosion.BULLET_TYPES:
        data["bullets"] = [_bullet(cmds, space, b, frames) for b in explosion.bullets_of(cmds, root)]
    elif effect_type == explosion.EFFECT_TYPE or effect_type.startswith("discharge_"):
        hits = explosion.read_hits(cmds, root) if cmds.attributeQuery(
            explosion.HITS_ATTR, node=root, exists=True) else []
        data["hits"] = [{"frame": h["frame"], "bullet": h["index"], "position": space.point(h["pos"]),
                         "normal": space.vector(h["normal"])} for h in hits]
    elif effect_type == "shockwave":
        from . import shockwave as sw
        center = [n for n in cmds.listRelatives(root, children=True, fullPath=True) or []
                  if n.endswith("_center")]
        if center:
            # 輪の面の向き (Maya では中心のロケータのローカル Y 軸)
            def normal(f):
                m = cmds.getAttr(center[0] + ".worldMatrix[0]", time=f)
                v = space.vector((m[4], m[5], m[6]))
                n = sum(x * x for x in v) ** 0.5 or 1.0
                return {"normal": tuple(x / n for x in v)}
            data["ring_normal"] = xc.make_track(frames, [normal(f) for f in frames])
    elif effect_type == "wind":
        from . import _weather as wx
        start = cmds.getAttr(root + "." + wx.DRIFT_START_ATTR)
        vec = [cmds.getAttr(root + "." + a) or [] for a in wx.WIND_VEC_ATTRS]
        drift = [cmds.getAttr(root + "." + a) or [] for a in wx.DRIFT_ATTRS]
        table_v = list(zip(*vec))
        table_d = list(zip(*drift))
        data["wind"] = xc.make_track(frames, [
            {"velocity": space.vector(wx.table_value(table_v, start, f), length=True),
             "drift": space.vector(wx.table_value(table_d, start, f), length=True)}
            for f in frames])
    return data


def _bullet(cmds, space, bullet, frames):
    def visible(f):
        return {"visible": bool(cmds.getAttr(bullet + ".visibility", time=f)
                                and cmds.getAttr(bullet + ".lodVisibility", time=f))}
    return {"name": bullet.split("|")[-1],
            "track": sample_transform(cmds, space, bullet, frames, extra=visible)}


def export_camera(cmds, space, camera, frames):
    shape = cmds.listRelatives(camera, shapes=True, type="camera", fullPath=True)[0]

    def lens(f):
        return {"focal_length": cmds.getAttr(shape + ".focalLength", time=f)}
    return {"name": camera.split("|")[-1],
            "film_aperture_mm": [cmds.getAttr(shape + ".horizontalFilmAperture") * 25.4,
                                 cmds.getAttr(shape + ".verticalFilmAperture") * 25.4],
            "clip": [cmds.getAttr(shape + ".nearClipPlane") * space.scale,
                     cmds.getAttr(shape + ".farClipPlane") * space.scale],
            "track": sample_transform(cmds, space, camera, frames, extra=lens)}
