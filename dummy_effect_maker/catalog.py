# -*- coding: utf-8 -*-
"""エフェクトの一覧 (Maya / Blender / Unreal で共通の「設計図」)。

エフェクトの種類ごとに、名前・パラメータの定義・すぐ反映か作り直しか・分類をまとめる。
Maya にも Qt にも依存しない (各ツールのモジュールは Maya を run() の中でしか import しない)。

使う所:
    - 書き出し (Unreal 用 JSON): パラメータの値と定義 (型・範囲・選択肢) を書く
    - Blender 版: 同じパラメータの定義から画面と作成処理を作る
    - Unreal 側: 種類ごとに Niagara のテンプレートを用意する時の一覧

新しいエフェクトを足したら、ここに 1 行足す (テストで漏れを確かめる)。
"""
from __future__ import absolute_import

import importlib

# 分類 (Unreal 側でどう作るかの目安)
BULLETS = "bullets"        # 弾 (位置の軌跡を書き出す)
CAMERA = "camera"          # カメラの前に出す画面効果・カメラの揺れ
BURST = "burst"            # その場で広がる (衝撃波・爆発)
PARTICLES = "particles"    # 粒で作る天候
LAYERS = "layers"          # 大きな板で作る天候・霧
MOTION = "motion"          # オブジェクトの動きに付けるもの (お化けブラー)
CONTROL = "control"        # ほかのエフェクトを動かすもの (風)


class EffectSpec(object):
    """エフェクト 1 種類分の設計図。"""

    def __init__(self, effect_type, label, group, params, live, rebuild):
        self.effect_type = effect_type
        self.label = label
        self.group = group
        self.params = list(params)          # ルートに保存するパラメータ (Param)
        self.live = tuple(live)
        self.rebuild = tuple(rebuild)

    def param(self, name):
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)

    def describe(self):
        """パラメータの定義 (書き出し用の辞書)。"""
        out = {}
        for p in self.params:
            d = {"label": p.label, "kind": p.kind, "default": _jsonable(p.default),
                 "live": p.name in self.live}
            if p.minimum is not None:
                d["min"] = p.minimum
            if p.maximum is not None:
                d["max"] = p.maximum
            if p.choices:
                d["choices"] = list(p.choices)
            out[p.name] = d
        return out


def _jsonable(value):
    if isinstance(value, tuple):
        return list(value)
    return value


# (モジュール, ツールのクラス名, 分類) — モジュールの EFFECT_TYPE / LIVE_PARAMS /
# REBUILD_PARAMS / stored_params() を使う
_MODULE_SPECS = (
    ("homing", "HomingTool", BULLETS),
    ("vulcan", "VulcanTool", BULLETS),
    ("energy_ball", "EnergyBallTool", BULLETS),
    ("focus_lines", "FocusLinesTool", CAMERA),
    ("speed_lines", "SpeedLinesTool", CAMERA),
    ("camera_shake", "CameraShakeTool", CAMERA),
    ("hand_shake", "HandShakeTool", CAMERA),
    ("shockwave", "ShockwaveTool", BURST),
    ("explosion", "ExplosionTool", BURST),
)
# 種類の表 (Kind) を持つモジュール: 天候・お化けブラー・核爆発 (天候と同じ仕組み)
_WEATHER_GROUPS = {
    "wind": CONTROL, "rain": PARTICLES, "snow": PARTICLES, "dust": PARTICLES,
    "dust_mesh": PARTICLES, "sparks": PARTICLES, "spark_trail": PARTICLES,
    "rain_far": LAYERS, "snow_far": LAYERS, "fog": LAYERS, "fog_depth": LAYERS,
    "ground_fog": LAYERS, "ground_crawl": LAYERS, "nuke": BURST, "explosion_single": BURST,
    "lightning_real": PARTICLES, "lightning_anime": PARTICLES,
    "discharge_real": PARTICLES, "discharge_anime": PARTICLES,
    "electric_real": PARTICLES, "electric_anime": PARTICLES,
    "leaves_fall": PARTICLES, "leaves_vortex": PARTICLES,
    "foot_dust": PARTICLES, "foot_dust_mesh": PARTICLES, "wisp": PARTICLES, "cloth_waves": LAYERS, "tunnel_lines": CAMERA,
}

_CACHE = []


def _tools(name):
    return importlib.import_module("dummy_effect_maker.tools." + name)


def specs():
    """全部のエフェクトの EffectSpec (タブの並び順)。"""
    if _CACHE:
        return list(_CACHE)
    out = []
    for module_name, tool_name, group in _MODULE_SPECS:
        m = _tools(module_name)
        tool = getattr(m, tool_name)
        out.append(EffectSpec(m.EFFECT_TYPE, tool.label, group, m.stored_params(),
                              m.LIVE_PARAMS, m.REBUILD_PARAMS))
    gb = _tools("ghost_blur")
    for kind in gb.KINDS.values():
        out.append(EffectSpec(kind.effect_type, kind.noun, MOTION, kind.stored_params(),
                              kind.live, kind.rebuild))
    for module_name in ("wind", "weather", "weather_layers", "wisp", "lightning", "electric", "leaves", "foot_dust", "explosion_single", "nuke", "cloth",
                        "tunnel_lines"):
        _tools(module_name)  # 種類を登録させる
    wx = _tools("_weather")
    for effect_type in sorted(wx.KINDS, key=lambda t: wx.KINDS[t].tool.order):
        kind = wx.KINDS[effect_type]
        out.append(EffectSpec(effect_type, kind.noun, _WEATHER_GROUPS[effect_type],
                              kind.stored_params(), kind.live, kind.rebuild))
    _CACHE[:] = out
    return list(out)


def spec(effect_type):
    for s in specs():
        if s.effect_type == effect_type:
            return s
    raise KeyError(effect_type)


def describe_all():
    """全部の種類の定義 (Unreal 側でテンプレートを作る時の一覧)。"""
    return dict((s.effect_type, {"label": s.label, "group": s.group, "params": s.describe()})
                for s in specs())
