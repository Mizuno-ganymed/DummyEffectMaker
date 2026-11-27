# -*- coding: utf-8 -*-
"""Maya なしで動かせる部分のテスト。  python -m pytest tests"""
import io
import json
import math
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dummy_effect_maker.core import BaseTool, Param, ToolContext, ToolError, execute  # noqa: E402
from dummy_effect_maker.core import host, presets, registry, settings  # noqa: E402
from dummy_effect_maker.tools import (camera_shake, energy_ball, explosion,  # noqa: E402
                                      focus_lines, ghost_blur, hand_shake, homing, shockwave,
                                      speed_lines, vulcan, weather, weather_layers, wind)
from dummy_effect_maker.tools import _weather as wx  # noqa: E402


def test_param_kind_inference():
    assert Param("a", "a", True).kind == "bool"
    assert Param("a", "a", 1).kind == "int"
    assert Param("a", "a", 1.0).kind == "float"
    assert Param("a", "a", "x").kind == "str"
    with pytest.raises(ValueError):
        Param("a", "a", "x", kind="choice")


def test_param_coerce_clamps_and_validates():
    p = Param("n", "n", 5, minimum=1, maximum=10)
    assert p.coerce(0) == 1
    assert p.coerce(99) == 10
    c = Param("c", "c", "x", kind="choice", choices=["x", "y"])
    with pytest.raises(ValueError):
        c.coerce("z")
    col = Param("col", "col", (1.0, 0.0, 0.0), kind="color")
    assert col.coerce((2, -1, 0.5)) == (1.0, 0.0, 0.5)


def test_discover_finds_tools_without_maya():
    errors = []
    tools = registry.discover("dummy_effect_maker.tools",
                              on_error=lambda m, tb: errors.append(m))
    assert errors == []
    labels = [t.label for t in tools]
    assert u"追尾弾" in labels
    # 補助モジュール (_maya_utils) はツールとして読み込まれない
    assert all(not type(t).__module__.endswith("_maya_utils") for t in tools)
    groups = registry.group_by_category(tools)
    assert sum(len(ts) for _, ts in groups) == len(tools)


def test_execute_passes_values_and_uses_undo_chunk():
    calls = []

    class Dummy(BaseTool):
        label = "dummy"
        hosts = None
        params = [Param("count", "count", 3)]

        def run(self, ctx, **v):
            calls.append(v)
            return v["count"] * 2

    tool = Dummy()
    tool.set_values({"count": 4, "unknown": 1})
    ctx = ToolContext(host.StandaloneHost(), log=lambda m: None)
    assert execute(tool, ctx) == 8
    assert calls == [{"count": 4}]
    assert tool.is_available("standalone")


def test_homing_fire_frames():
    assert homing.compute_fire_frames(1, 9, 2) == [1, 3, 5, 7, 9]
    assert homing.compute_fire_frames(1, 2, 0.5) == [1, 1.5, 2]
    assert homing.compute_fire_frames(10, 5, 1) == []
    with pytest.raises(ValueError):
        homing.compute_fire_frames(1, 10, 0)


def test_homing_bezier_passes_through_b():
    a, b, c = (0.0, 0.0, 0.0), (5.0, 3.0, 1.0), (10.0, 0.0, -2.0)
    p1 = homing.bezier_control_through(a, b, c)
    mid = tuple(0.25 * a[i] + 0.5 * p1[i] + 0.25 * c[i] for i in range(3))
    assert mid == pytest.approx(b)


def test_homing_params_are_consistent():
    tool = homing.HomingTool()
    for p in tool.params:
        assert p.coerce(p.default) == p.default
    assert not tool.is_available("standalone")


def test_homing_every_param_is_live_rebuild_or_create():
    # 作成後に調整できるよう、全パラメータがどれか 1 つに分類されている
    names = [p.name for p in homing.HomingTool.params]
    groups = homing.LIVE_PARAMS + homing.REBUILD_PARAMS + homing.CREATE_PARAMS
    assert sorted(names) == sorted(groups)
    assert [p.name for p in homing.stored_params()] == list(
        homing.LIVE_PARAMS + homing.REBUILD_PARAMS)
    assert not homing.HomingUpdateTool.params


def _numeric_speed(mode, m, t, h=1e-5):
    lo, hi = max(0.0, t - h), min(1.0, t + h)
    return (homing.travel_fraction(mode, m, hi) - homing.travel_fraction(mode, m, lo)) / (hi - lo)


def test_homing_speed_profiles():
    for mode in (homing.SPEED_LINEAR, homing.SPEED_QUADRATIC):
        for m in (0.0, 0.3, 1.0):
            # 始点 0・終点 1 で、単調に進む
            us = [homing.travel_fraction(mode, m, i / 100.0) for i in range(101)]
            assert us[0] == 0.0 and abs(us[-1] - 1.0) < 1e-12
            assert all(b >= a for a, b in zip(us, us[1:]))
            # 着弾時の速さ / 発射時の速さ = 最低速度
            ratio = _numeric_speed(mode, m, 1.0) / _numeric_speed(mode, m, 0.0)
            assert abs(ratio - m) < 1e-3
            # 速さは単調に下がる (加速しない)
            speeds = [homing.speed_at(mode, m, i / 10.0) for i in range(11)]
            assert all(b <= a + 1e-12 for a, b in zip(speeds, speeds[1:]))
    # 2乗は前半で大きく減速する: 中間の速さがリニアより遅い
    assert homing.speed_at(homing.SPEED_QUADRATIC, 0.3, 0.5) < homing.speed_at(homing.SPEED_LINEAR, 0.3, 0.5)
    # 等速は経過時間 = 進み具合
    assert homing.travel_fraction(homing.SPEED_CONSTANT, 0.3, 0.25) == 0.25


def test_homing_travel_keys():
    assert homing.travel_keys(homing.SPEED_CONSTANT, 0.3, 10, 8) == [(10, 0.0), (18, 1.0)]
    assert homing.travel_keys(homing.SPEED_LINEAR, 1.0, 10, 8) == [(10, 0.0), (18, 1.0)]
    keys = homing.travel_keys(homing.SPEED_LINEAR, 0.3, 10, 8)
    assert len(keys) == 8 * homing.SPEED_KEYS_PER_FRAME + 1
    assert keys[0] == (10, 0.0) and keys[-1][0] == 18 and abs(keys[-1][1] - 1.0) < 1e-12
    assert len(homing.travel_keys(homing.SPEED_QUADRATIC, 0.3, 0, 0.5)) == 9  # 短くても 8 区間


def test_tools_are_grouped_into_tabs():
    tools = registry.discover("dummy_effect_maker.tools")
    tabs = [(tab, [t.label for t in ts]) for tab, ts in registry.group_by_tab(tools)]
    assert [tab for tab, _ in tabs] == [u"射撃", u"カメラ", u"衝撃波", u"お化けブラー", u"天候", u"爆発", u"布"]
    assert dict(tabs)[u"布"] == [u"波打つ布", u"波打つ布を更新"]
    # 爆発タブは単体で起きる爆発だけ (弾が当たって起きる着弾爆発とコリジョンは射撃タブ)
    assert dict(tabs)[u"爆発"] == [u"爆発 (単体)", u"爆発 (単体) を更新", u"核爆発", u"核爆発を更新"]
    assert dict(tabs)[u"天候"] == [
        u"風", u"風を更新", u"雨 (線と跳ね)", u"雨 (遠景の筒)", u"雪 (粒)", u"雪 (遠景の筒)",
        u"落ち葉 (舞い散る)", u"落ち葉 (地面の渦)",
        u"土煙 (スプライト)", u"土煙 (もこもこ)",
        u"足元の砂煙 (スプライト)", u"足元の砂煙 (もこもこ)", u"細い煙 (たばこ・線香)", u"火花 (線)", u"火花 (尾を引く)",
        u"霧 (霧の板)", u"霧 (遠景の重ね板)", u"地霧 (重ね板)", u"地霧 (這う霧)",
        u"稲妻 (実写)", u"稲妻 (アニメ)", u"放電 (実写)", u"放電 (アニメ)", u"電撃 (実写)", u"電撃 (アニメ)", u"天候を更新"]
    weather_tools = [t for t in tools if t.tab == u"天候"]
    assert [c for c, _ in registry.group_by_category(weather_tools)] == [
        u"風", u"雨", u"雪", u"落ち葉", u"土煙", u"砂煙", u"煙", u"火花", u"霧", u"地霧", u"稲妻", u"調整"]
    assert dict(tabs)[u"お化けブラー"] == [
        u"残像", u"スミア (引き伸ばし)", u"軌跡", u"お化けブラーを更新"]
    assert dict(tabs)[u"衝撃波"] == [u"衝撃波", u"衝撃波を更新"]
    assert dict(tabs)[u"カメラ"] == [
        u"集中線", u"集中線を更新",
        u"スピードライン", u"スピードラインを更新", u"スピードライン (トンネル)", u"スピードライン (トンネル) を更新",
        u"画ぶれ", u"画ぶれを更新",
        u"画ぶれを外す",
        u"手ブレ", u"手ブレを更新",
        u"手ブレを外す"]
    # ベイクと全削除はタブではなく上部の「シーン」メニュー
    menus = [(m, [t.label for t in ts]) for m, ts in registry.group_by_menu(tools)]
    assert menus == [(u"シーン", [u"ベイク", u"書き出し (Unreal 用)", u"ダミーエフェクトを全削除"])]
    assert u"ベイク" not in dict(tabs)[u"射撃"]
    # パラメータの保存・読み込みは全部の種類で共通 (アトリビュートエディタのボタンから。タブ・メニューには出ない)
    hidden = [t for t in tools if t.hidden]
    assert sorted(type(t).__name__ for t in hidden) == ["PresetLoadTool", "PresetSaveTool"]
    assert not any(u"パラメータ" in label for _, labels in tabs + menus for label in labels)
    # 射撃タブ: 追尾弾・バルカン砲・エネルギー弾を項目 (グループ) に分ける
    assert dict(tabs)[u"射撃"] == [
        u"追尾弾", u"追尾弾を更新", u"バルカン砲", u"バルカン砲を更新", u"エネルギー弾", u"エネルギー弾を更新",
        u"コリジョンに指定", u"コリジョンを解除", u"着弾爆発", u"着弾爆発を更新", u"着弾爆発を外す"]
    shooting = [t for t in tools if t.tab == u"射撃" and not t.hidden]
    assert [c for c, _ in registry.group_by_category(shooting)] == [u"追尾弾", u"バルカン砲", u"エネルギー弾", u"着弾爆発"]


def test_menu_tools_are_not_in_tabs():
    class TabTool(BaseTool):
        label = u"a"
        order = 1

    class MenuTool(BaseTool):
        label = u"b"
        menu = u"シーン"
        order = 2

    tools = [TabTool(), MenuTool()]
    assert [t for t, _ in registry.group_by_tab(tools)] == [u"ツール"]
    assert [(m, [t.label for t in ts]) for m, ts in registry.group_by_menu(tools)] == [(u"シーン", [u"b"])]


def test_focus_lines_params_are_live_or_rebuild():
    names = sorted(p.name for p in focus_lines.FocusLinesTool.params)
    assert names == sorted(focus_lines.LIVE_PARAMS + focus_lines.REBUILD_PARAMS
                           + focus_lines.CREATE_PARAMS)
    assert "focus_radius" not in [p.name for p in focus_lines.stored_params()]
    for p in focus_lines.FocusLinesTool.params:
        assert p.coerce(p.default) == p.default


def _close(a, b, tol=1e-9):
    return abs(a - b) < tol


def test_focus_lines_edge_distance():
    import math
    aspect = 16.0 / 9.0
    assert _close(focus_lines.edge_distance(0.0, aspect), 1.0)
    assert _close(focus_lines.edge_distance(math.pi / 2, aspect), 1.0 / aspect)
    assert _close(focus_lines.edge_distance(math.pi, aspect), 1.0)
    # 対角線方向は角に当たる
    corner = math.atan2(1.0 / aspect, 1.0)
    assert _close(focus_lines.edge_distance(corner, aspect), math.hypot(1.0, 1.0 / aspect))


def test_focus_lines_layout():
    import math
    lines = focus_lines.line_layout(40, 16.0 / 9.0, 7, 0.5, 0.25)
    assert len(lines) == 40
    assert lines == focus_lines.line_layout(40, 16.0 / 9.0, 7, 0.5, 0.25)  # 同じシードなら同じ
    assert lines != focus_lines.line_layout(40, 16.0 / 9.0, 8, 0.5, 0.25)
    for i, (angle, base, length, thickness) in enumerate(lines):
        # 全周を 40 等分した i 番目の区間に 1 本ずつ (偏らない)
        assert 2 * math.pi * i / 40 <= angle < 2 * math.pi * (i + 1) / 40
        # 画面の端までの距離
        assert _close(base, focus_lines.edge_distance(angle, 16.0 / 9.0))
        assert 0.5 <= length <= 1.0 and 0.75 <= thickness <= 1.0
    same = focus_lines.line_layout(10, 1.0, 1, 0.0, 0.0)
    assert all(l == 1.0 and t == 1.0 for _, _, l, t in same)  # ばらつき 0 なら全部同じ


def test_focus_lines_grow_schedule():
    import math
    aspect = 16.0 / 9.0
    starts = set()
    schedules = focus_lines.grow_schedule(3, 50, 1, 120, 6, aspect, 0.5)
    assert len(schedules) == 50
    assert schedules == focus_lines.grow_schedule(3, 50, 1, 120, 6, aspect, 0.5)
    assert schedules != focus_lines.grow_schedule(4, 50, 1, 120, 6, aspect, 0.5)
    for cycles in schedules:
        times = [c[0] for c in cycles]
        # 1 回目は 1 フレーム目の時点で伸びている途中 (または伸び始め)
        assert times[0] <= 1 < times[0] + 6
        # 6 フレームごとに次の場所へ。最後は 120 フレームまでを覆う
        assert all(b - a == 6 for a, b in zip(times, times[1:]))
        assert times[-1] <= 120 < times[-1] + 6
        for (_, a, edge, length), (_, b, _, _) in zip(cycles, cycles[1:]):
            # 続けて同じ場所から出ない: 直前から 30° 以上離れる
            assert focus_lines.angle_gap(a, b) >= math.radians(30) - 1e-12
        for _, angle, edge, length in cycles:
            assert 0 <= angle < 2 * math.pi
            assert _close(edge, focus_lines.edge_distance(angle, aspect))
            assert 0.5 <= length <= 1.0
        starts.add(times[0] % 6)
    # 伸び始めのずれが 0〜5 に均等に割り振られている
    assert starts == set(range(6))


def _active_angles(schedules, frame, grow_frames):
    return sorted(next(c[1] for c in cycles if c[0] <= frame < c[0] + grow_frames)
                  for cycles in schedules)


def test_focus_lines_grow_spreads_evenly():
    import math
    for count in (20, 40, 80):
        schedules = focus_lines.grow_schedule(1, count, 1, 120, 6, 16.0 / 9.0, 0.5)
        ideal = 2 * math.pi / count
        worst, total = 0.0, 0.0
        for f in range(1, 121):
            a = _active_angles(schedules, f, 6)
            biggest = max([y - x for x, y in zip(a, a[1:])] + [a[0] + 2 * math.pi - a[-1]])
            worst = max(worst, biggest / ideal)
            total += biggest / ideal
        # 独立にランダムだと 平均 3.7〜5.1 倍 / 最悪 7〜11 倍 の空きができていた
        assert total / 120 < 2.6, (count, total / 120)
        assert worst < 5.0, (count, worst)
        # 毎フレーム同じくらいの本数が移動する (差は 1 本以内)
        moves = {}
        for cycles in schedules:
            for c in cycles[1:]:
                moves[c[0]] = moves.get(c[0], 0) + 1
        assert max(moves.values()) - min(moves.values()) <= 1


def test_focus_lines_angle_gap():
    import math
    assert _close(focus_lines.angle_gap(0.1, 2 * math.pi - 0.1), 0.2)
    assert _close(focus_lines.angle_gap(0.0, math.pi), math.pi)


def test_focus_lines_flicker_is_reproducible():
    a = focus_lines.flicker_lengths(3, 5, 20, 0.6)
    assert a == focus_lines.flicker_lengths(3, 5, 20, 0.6)
    assert a != focus_lines.flicker_lengths(3, 6, 20, 0.6)
    assert all(0.4 <= f <= 1.0 for f in a) and len(set(a)) > 1


def test_energy_ball_params_are_live_or_rebuild():
    names = sorted(p.name for p in energy_ball.EnergyBallTool.params)
    assert names == sorted(energy_ball.LIVE_PARAMS + energy_ball.REBUILD_PARAMS)
    for p in energy_ball.EnergyBallTool.params + energy_ball.EnergyBallUpdateTool.params:
        assert p.coerce(p.default) == p.default


def test_energy_ball_default_timing_and_positions():
    v = energy_ball.EnergyBallTool.default_values()
    # 1 個目: 出現 1 → (出現 6 + 出現後の待機 10) → 発射 17 → (飛ぶ 12) → 着弾 29
    assert energy_ball.default_timing(v, 0) == (1.0, 17.0, 29.0)
    # 2 個目は 4 フレームずれる
    assert energy_ball.default_timing(v, 1) == (5.0, 21.0, 33.0)
    # 3 個を中央揃えで X に並べ、目標は +Z
    starts = [energy_ball.default_positions(v, i, 3) for i in range(3)]
    assert [s[0][0] for s in starts] == [-2.0, 0.0, 2.0]
    assert all(t == (s[0], 0.0, 10.0) for s, t in starts)


def test_energy_ball_curves():
    ac, ln = energy_ball.EASE_ACCEL, energy_ball.EASE_LINEAR
    assert energy_ball.flight_progress(16, 17, 29, ac) == 0.0
    assert energy_ball.flight_progress(29, 17, 29, ac) == 1.0
    assert energy_ball.flight_progress(23, 17, 29, ln) == 0.5
    assert energy_ball.flight_progress(23, 17, 29, ac) == 0.25   # 加速: 前半はゆっくり
    assert energy_ball.flight_progress(17, 17, 17, ln) == 1.0     # 0 フレームで飛ぶ = 瞬間移動
    assert energy_ball.appear_scale(1, 1, 6) == 0.0
    assert energy_ball.appear_scale(7, 1, 6) == 1.0
    assert energy_ball.appear_scale(4, 1, 6) > 0.5                # ease-out: 前半で大きくなる
    assert energy_ball.appear_scale(1, 1, 0) == 0.0 and energy_ball.appear_scale(2, 1, 0) == 1.0


class _TempDir(object):
    def __enter__(self):
        self.path = tempfile.mkdtemp()
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path)


def _write_json(path, data):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False))


def test_preset_roundtrip_keeps_every_value():
    params = homing.stored_params()
    values = dict((p.name, p.default) for p in params)
    values.update(radius=0.35, color=(0.0, 1.0, 0.5), shape=homing.SHAPE_SQUASHED,
                  path_type=homing.PATH_LINEAR, subdivisions=20, use_instance=False)
    with _TempDir() as d:
        path = os.path.join(d, u"テスト.json")
        presets.save_preset(path, homing.EFFECT_TYPE, params, values)
        with io.open(path, encoding="utf-8") as f:
            assert u"つぶれた球体" in f.read()  # 日本語はそのまま読める形で保存
        assert presets.load_preset(path, homing.EFFECT_TYPE, params) == values


def test_preset_load_partial_and_unknown_keys():
    params = homing.stored_params()
    with _TempDir() as d:
        path = os.path.join(d, "p.json")
        _write_json(path, {"format": presets.FORMAT_NAME, "version": 1, "type": "homing",
                           "params": {"radius": 0.5, "squash": 0.4}})
        # 無いものは含めず、知らないキー (旧バージョンの squash) は無視
        assert presets.load_preset(path, "homing", params) == {"radius": 0.5}


def test_preset_load_rejects_bad_files():
    params = homing.stored_params()
    base = {"format": presets.FORMAT_NAME, "version": 1, "type": "homing", "params": {}}
    cases = [
        "not json",
        dict(base, format="other"),
        dict(base, type="laser"),
        dict(base, params={"shape": u"四角"}),
        dict(base, params={"color": 3}),
        dict(base, params=None),
    ]
    with _TempDir() as d:
        for i, data in enumerate(cases):
            path = os.path.join(d, "%d.json" % i)
            if isinstance(data, str):
                with io.open(path, "w", encoding="utf-8") as f:
                    f.write(u"not json")
            else:
                _write_json(path, data)
            with pytest.raises(ToolError):
                presets.load_preset(path, "homing", params)
        with pytest.raises(ToolError):
            presets.load_preset(os.path.join(d, "missing.json"), "homing", params)


class _SettingsFile(object):
    """環境設定ファイルを一時ファイルに差し替える。"""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.prev = os.environ.get(settings.ENV_VAR)
        self.path = os.path.join(self.dir, "sub", "settings.json")
        os.environ[settings.ENV_VAR] = self.path
        return self.path

    def __exit__(self, *exc):
        if self.prev is None:
            os.environ.pop(settings.ENV_VAR, None)
        else:
            os.environ[settings.ENV_VAR] = self.prev
        shutil.rmtree(self.dir)


def test_settings_defaults_save_and_reload():
    with _SettingsFile() as path:
        assert settings.settings_path() == path
        assert settings.get("preset_dir") == ""          # ファイルが無ければ既定値
        settings.set("preset_dir", u"D:/共有/presets")    # フォルダも作って保存
        assert os.path.isfile(path)
        assert settings.get("preset_dir") == u"D:/共有/presets"
        with pytest.raises(KeyError):
            settings.set("unknown", 1)


def test_settings_ignores_broken_file_and_wrong_types():
    with _SettingsFile() as path:
        os.makedirs(os.path.dirname(path))
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(u"{ broken")
        assert settings.load() == settings.DEFAULTS
        _write_json(path, {"preset_dir": 123, "other": "x"})
        assert settings.get("preset_dir") == ""


def test_preset_values_are_clamped_like_the_dialog():
    params = homing.stored_params()
    with _TempDir() as d:
        path = os.path.join(d, "p.json")
        _write_json(path, {"format": presets.FORMAT_NAME, "version": 1, "type": "homing",
                           "params": {"subdivisions": 999, "radius": -1}})
        values = presets.load_preset(path, "homing", params)
        assert values == {"subdivisions": 64, "radius": 0.001}


# ---------------------------------------------------------------- バルカン砲
def test_vulcan_params_are_live_or_rebuild():
    names = sorted(p.name for p in vulcan.VulcanTool.params)
    assert names == sorted(vulcan.LIVE_PARAMS + vulcan.REBUILD_PARAMS + vulcan.CREATE_PARAMS)
    assert [p.name for p in vulcan.stored_params()] == list(vulcan.LIVE_PARAMS + vulcan.REBUILD_PARAMS)
    for p in vulcan.VulcanTool.params:
        assert p.coerce(p.default) == p.default
    # 追尾弾と同じ選択肢 (エクスプレッションは選択肢の番号で判定する)
    shared = dict((p.name, p) for p in homing.HomingTool.params)
    for p in vulcan.VulcanTool.params:
        if p.kind == "choice":
            assert p.choices == shared[p.name].choices
    assert vulcan.VulcanTool.params[0].name == "length"


def test_vulcan_bullet_moves_straight_from_fire_frame():
    a, c = (0.0, 0.0, 0.0), (12.0, 6.0, -3.0)
    assert vulcan.bullet_position(a, c, 10, 10, 6) == a
    assert vulcan.bullet_position(a, c, 13, 10, 6) == (6.0, 3.0, -1.5)
    assert vulcan.bullet_position(a, c, 16, 10, 6) == c
    assert vulcan.bullet_position(a, c, 30, 10, 6) == c     # 着弾後は目標で止まる
    p = vulcan.bullet_position(a, c, 13, 10, 6, vulcan.SPEED_LINEAR, 0.3)
    assert _close(p[0], 12.0 * homing.travel_fraction(homing.SPEED_LINEAR, 0.3, 0.5))
    assert p[0] > 6.0                                      # 減速するので半分より先


def test_vulcan_bullet_visibility_matches_homing():
    assert not vulcan.bullet_visible(9, 10, 6)
    assert vulcan.bullet_visible(10, 10, 6)
    assert vulcan.bullet_visible(16.5, 10, 6)
    assert not vulcan.bullet_visible(17, 10, 6)             # 着弾 (16) の次のフレームで消える


# ---------------------------------------------------------------- 集中線の中心の円
def test_edge_distance_from_off_center_point():
    aspect = 16.0 / 9.0
    assert _close(focus_lines.edge_distance(0.0, aspect, (0.5, 0.0)), 0.5)
    assert _close(focus_lines.edge_distance(math.pi, aspect, (0.5, 0.0)), 1.5)
    assert _close(focus_lines.edge_distance(math.pi / 2, aspect, (0.0, 0.2)), 9.0 / 16.0 - 0.2)
    # 中心なら従来どおり
    for a in (0.3, 1.2, 2.5, 4.0):
        assert _close(focus_lines.edge_distance(a, aspect, (0.0, 0.0)),
                      focus_lines.edge_distance(a, aspect))


def test_ellipse_radius():
    assert _close(focus_lines.ellipse_radius(0.0, 0.4, 0.1), 0.4)
    assert _close(focus_lines.ellipse_radius(math.pi / 2, 0.4, 0.1), 0.1)
    assert _close(focus_lines.ellipse_radius(1.0, 0.3, 0.3), 0.3)
    assert focus_lines.ellipse_radius(1.0, 0.0, 0.3) == 0.0


def test_line_stops_at_focus_circle():
    aspect = 2.0
    center, radius = (0.2, -0.1), (0.25, 0.25)
    edge, tip = focus_lines.line_geometry(0.0, aspect, center, radius, 1.0)
    assert _close(edge[0], 1.0) and _close(edge[1], -0.1)
    assert _close(tip[0], 0.45) and _close(tip[1], -0.1)        # 円の縁でちょうど止まる
    _, half = focus_lines.line_geometry(0.0, aspect, center, radius, 0.5)
    assert _close(half[0], 1.0 - 0.5 * 0.55)                   # 端〜円の縁の半分
    # 円が画面の端にかかる方向は長さ 0
    edge, tip = focus_lines.line_geometry(-math.pi / 2, aspect, (0.0, -0.4), (0.3, 0.3), 1.0)
    assert edge == tip


def test_round_tip_ends_where_the_sharp_tip_would():
    # とがる: 本体だけ
    assert focus_lines.tip_layout(0.5, 0.04, False, 0.5) == (0.5, 0.0)
    # 丸い: 先端の円 (半径 = 太さ × 先端の太さ) の分だけ本体を短くし、合計は同じ長さ
    body, radius = focus_lines.tip_layout(0.5, 0.04, True, 0.5)
    assert _close(radius, 0.02) and _close(body + radius, 0.5)
    # 線が短いときは円を線の長さまで縮める
    body, radius = focus_lines.tip_layout(0.01, 0.04, True, 0.5)
    assert _close(radius, 0.01) and _close(body, 0.0)
    # 先端の太さは 0.01〜1 に収める
    assert _close(focus_lines.tip_layout(1.0, 0.04, True, 5.0)[1], 0.04)


# ---------------------------------------------------------------- スピードライン
def test_speed_lines_params_are_live_or_rebuild():
    names = sorted(p.name for p in speed_lines.SpeedLinesTool.params)
    assert names == sorted(speed_lines.LIVE_PARAMS + speed_lines.REBUILD_PARAMS
                           + speed_lines.CREATE_PARAMS)
    for p in speed_lines.SpeedLinesTool.params:
        assert p.coerce(p.default) == p.default


def test_speed_line_width_passes_start_mid_end():
    w = speed_lines.width_at
    assert _close(w(0.0, 0.01, 0.03, 0.005), 0.01)
    assert _close(w(0.5, 0.01, 0.03, 0.005), 0.03)
    assert _close(w(1.0, 0.01, 0.03, 0.005), 0.005)
    assert w(0.25, 0.0, 0.02, 0.0) > 0.0                   # 間はなめらかにつながる
    assert w(0.9, 0.05, 0.0, 0.0) == 0.0                   # 0 未満にはならない


def _speed_values(**kw):
    v = speed_lines.SpeedLinesTool.default_values()
    v.update(kw)
    return v



ZC = 2.0  # 横の画角が約 53° のカメラ


def test_speed_line_flows_right_and_stays_outside_the_band():
    v = _speed_values(length_random=0.0)
    aspect, band = 16.0 / 9.0, 0.2
    region = speed_lines.plane_region(aspect, ZC, (0, 0, 0, 0, 0))
    assert all(_close(a, b) for a, b in zip(region, (-1, 1, -9.0 / 16, 9.0 / 16)))
    x1, y1, length = speed_lines.line_state(10, 3, 0.4, 1.0, 1.0, v, region, band)
    x2, y2, _ = speed_lines.line_state(11, 3, 0.4, 1.0, 1.0, v, region, band)
    if x2 > x1:                                            # 画面を通り過ぎた直後でなければ
        assert _close(x2 - x1, v["speed"]) and _close(y1, y2)
    assert _close(length, v["length"])
    for f in range(0, 300, 7):
        for i in range(10):
            x, y, _ = speed_lines.line_state(f, i, i / 10.0, 0.8, 1.0, v, region, band)
            assert abs(y) >= band                          # 四角の上辺と下辺の間には入らない
            assert -1 - v["length"] - 1e-9 <= x <= 1 + 1e-9  # 画面の外から入って外へ出る


def test_speed_line_respawns_at_a_new_height():
    v = _speed_values()
    region = speed_lines.plane_region(1.5, ZC, (0, 0, 0, 0, 0))
    ys = set(round(speed_lines.line_state(f, 0, 0.0, 1.0, 1.0, v, region, 0.2)[1], 6)
             for f in range(0, 400, 5))
    assert len(ys) >= 4 and min(ys) < 0 < max(ys)          # 上下どちらにも出る


def test_speed_line_region_follows_band_move_and_rotation():
    # 四角を Z 90° 回すと、面の上での画面の上下の幅は 1 (横幅の半分) になる
    umin, umax, vmin, vmax = speed_lines.plane_region(16.0 / 9.0, ZC, (0, 0, 0, 0, 90))
    assert _close(vmin, -1.0) and _close(vmax, 1.0) and _close(umax - umin, 9.0 / 8)
    # 四角を上に 0.3 動かすと、面の上での画面は下に 0.3 ずれる
    _, _, vmin, vmax = speed_lines.plane_region(2.0, ZC, (0, 0.3, 0, 0, 0))
    assert _close(vmin, -0.8) and _close(vmax, 0.2)


def test_speed_line_region_with_depth_tilt():
    # Y に傾けると、面の上での画面の範囲は奥側 (+X) に広がる (パース)
    umin, umax, vmin, vmax = speed_lines.plane_region(16.0 / 9.0, ZC, (0, 0, 0, 30, 0))
    assert umax > 1.0 and -1.0 < umin < 0
    ex, ey, ez = speed_lines.plane_axes(0, 30, 0)
    assert _close(ex[2], -0.5)                             # +X 側が奥 (カメラから遠い)
    # 地平線が見えるほど傾けても有限
    region = speed_lines.plane_region(16.0 / 9.0, ZC, (0, 0, 85, 0, 0))
    assert all(abs(x) < 1e4 for x in region)


def test_speed_line_does_not_jump_when_the_region_changes():
    v = _speed_values()
    a = speed_lines.plane_region(16.0 / 9.0, ZC, (0, 0, 0, 0, 0))
    b = speed_lines.plane_region(16.0 / 9.0, ZC, (0, 0, 0, 20, 10))
    # 同じフレームなら、範囲が変わっても 1 周の中の進み具合は同じ (範囲の中の同じ割合の位置)
    for f in (3, 17, 40):
        xa, _, _ = speed_lines.line_state(f, 2, 0.3, 1.0, 1.0, v, a, 0.2)
        xb, _, _ = speed_lines.line_state(f, 2, 0.3, 1.0, 1.0, v, b, 0.2)
        ta = (xa - (a[0] - v["length"])) / (a[1] - a[0] + v["length"])
        tb = (xb - (b[0] - v["length"])) / (b[1] - b[0] + v["length"])
        assert _close(ta, tb)


# ---------------------------------------------------------------- 画ぶれ
def test_camera_shake_params_are_live_or_rebuild():
    names = sorted(p.name for p in camera_shake.CameraShakeTool.params)
    assert names == sorted(camera_shake.LIVE_PARAMS + camera_shake.REBUILD_PARAMS)
    for p in camera_shake.CameraShakeTool.params:
        assert p.coerce(p.default) == p.default


def test_camera_shake_keys_follow_interval_and_strength_random():
    keys = camera_shake.shake_keys(3, 1, 60, lambda t: 4.0, 0.5, 0.25)
    times = [t for t, _ in keys]
    assert times[0] == 1 and times[-1] > 60 and times[-2] <= 60   # 範囲の最後まで
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(2.0 - 1e-9 <= g <= 6.0 + 1e-9 for g in gaps)      # 4 × (1 ± 0.5)
    assert max(gaps) - min(gaps) > 0.5                            # ばらつく
    for _, values in keys:
        assert all(0.75 - 1e-9 <= abs(x) <= 1.0 for x in values)  # 1 - 0.25 × 乱数
    flips = sum(1 for (_, a), (_, b) in zip(keys, keys[1:]) if a[0] * b[0] < 0)
    assert flips > len(keys) * 0.5                                # ほとんど毎回向きが変わる
    # ばらつき 0 なら一定間隔・一定の大きさ
    fixed = camera_shake.shake_keys(3, 0, 20, lambda t: 2.0, 0.0, 0.0)
    assert [t for t, _ in fixed] == [float(t) for t in range(0, 23, 2)]
    assert all(abs(x) == 1.0 for _, v in fixed for x in v)


def test_camera_shake_interval_can_change_over_time():
    keys = camera_shake.shake_keys(1, 0, 100, lambda t: 1.0 if t < 50 else 10.0, 0.0, 0.0)
    gaps = [b - a for (a, _), (b, _) in zip(keys, keys[1:])]
    assert gaps[0] == 1.0 and gaps[-1] == 10.0


def test_camera_shake_rotation_matches_offset_at_center_of_interest():
    rx, ry = camera_shake.rotation_offset(1.0, 0.0, 1.0)
    assert _close(rx, 0.0) and _close(ry, -45.0)
    rx, ry = camera_shake.rotation_offset(0.0, 0.5, 10.0)
    assert _close(rx, math.degrees(math.atan(0.05))) and _close(ry, 0.0)


def test_camera_shake_axes_from_camera_to_aim():
    right, up = camera_shake.aim_axes((0, 0, 0), (0, 0, -5))   # -Z を見ている
    assert all(_close(a, b) for a, b in zip(right, (1, 0, 0)))
    assert all(_close(a, b) for a, b in zip(up, (0, 1, 0)))
    right, up = camera_shake.aim_axes((0, 0, 0), (3, 0, 0))    # +X を見ている
    assert all(_close(a, b) for a, b in zip(right, (0, 0, 1)))
    right, up = camera_shake.aim_axes((0, 0, 0), (0, -4, 0))   # 真下を見ている
    assert all(_close(a, b) for a, b in zip(right, (1, 0, 0)))


# ---------------------------------------------------------------- 手ブレ
def test_hand_shake_params_are_all_live():
    names = sorted(p.name for p in hand_shake.HandShakeTool.params)
    assert names == sorted(hand_shake.LIVE_PARAMS + hand_shake.REBUILD_PARAMS)
    assert hand_shake.REBUILD_PARAMS == ()                  # キーを使わないので全部すぐ反映
    for p in hand_shake.HandShakeTool.params:
        assert p.coerce(p.default) == p.default


def test_hand_shake_and_camera_shake_share_the_target_rules():
    assert set(camera_shake.SHAKE_TYPES) == {camera_shake.EFFECT_TYPE, hand_shake.EFFECT_TYPE}
    assert camera_shake.kind_of(hand_shake.EFFECT_TYPE) is hand_shake.KIND
    assert hand_shake.KIND.root_name == "dfx_handShake#"
    assert len(set(hand_shake.CHANNEL_OFFSETS)) == len(hand_shake.CHANNEL_OFFSETS) == 6
    # Z は後から追加: 前の 5 チャンネルの位相は変えない (前に作った手ブレの揺れ方が変わらない)
    assert hand_shake.CHANNEL_OFFSETS[:5] == (0.0, 101.3, 203.7, 307.1, 409.9)


def test_camera_shake_z_does_not_change_existing_xy():
    import random

    def old_keys(seed, start, end, interval, interval_random, strength_random):
        # Z を追加する前の作り方 (X / Y・回転 X / Y の 4 チャンネル)
        rng = random.Random("%s-shake" % seed)
        signs = [1.0, -1.0, 1.0, -1.0]
        keys, t = [], float(start)
        while True:
            values = []
            for c in range(4):
                if rng.random() < camera_shake.FLIP_CHANCE:
                    signs[c] = -signs[c]
                values.append(signs[c] * (1.0 - strength_random * rng.random()))
            keys.append((t, tuple(values)))
            if t > end:
                break
            t += max(camera_shake.MIN_STEP,
                     interval * (1.0 + interval_random * (2.0 * rng.random() - 1.0)))
        return keys

    new = camera_shake.shake_keys(5, 1, 80, lambda t: 2.5, 0.4, 0.3)
    old = old_keys(5, 1, 80, 2.5, 0.4, 0.3)
    assert [(t, v[:4]) for t, v in new] == old              # 前に作った画ぶれのぶれ方は同じ
    zs = [v[4] for _, v in new]
    assert all(0.7 - 1e-9 <= abs(z) <= 1.0 for z in zs) and min(zs) < 0 < max(zs)


# ---------------------------------------------------------------- 衝撃波
def test_shockwave_params_are_live_or_rebuild():
    names = sorted(p.name for p in shockwave.ShockwaveTool.params)
    assert names == sorted(shockwave.LIVE_PARAMS + shockwave.REBUILD_PARAMS
                           + shockwave.CREATE_PARAMS)
    for p in shockwave.ShockwaveTool.params:
        assert p.coerce(p.default) == p.default


def test_shockwave_expands_with_initial_speed_and_decay():
    # 減衰率 0 なら一定の速さ
    assert _close(shockwave.expand_radius(5, 2.0, 0.0), 10.0)
    # 毎フレーム 20% ずつ遅くなる: 1 フレーム目の進みは初速より少し少ない
    r1 = shockwave.expand_radius(1, 2.0, 0.2)
    r2 = shockwave.expand_radius(2, 2.0, 0.2)
    assert 1.6 < r1 < 2.0 and _close((r2 - r1) / r1, 0.8)    # 次のフレームは 0.8 倍しか進まない
    # 端まで届く時間
    target, frames, ok = shockwave.reach(10.0, 2.0, 0.15)
    assert ok and target == 10.0 and _close(shockwave.expand_radius(frames, 2.0, 0.15), 10.0)
    # 減衰が強すぎると届かない (最大 初速 / k の 99%)
    target, frames, ok = shockwave.reach(10.0, 1.0, 0.5)
    assert not ok and _close(target, 0.99 / math.log(2.0))


def test_shockwave_ring_thins_and_fades_after_reaching_the_edge():
    v = shockwave.ShockwaveTool.default_values()
    v.update(speed=2.0, decay=0.0, thickness=0.4, thickness_end=0.5, fade_frames=4.0)
    assert shockwave.ring_state(-1, v, 10.0) == (0.0, 0.0, False)       # まだ出ていない
    r, w, vis = shockwave.ring_state(2.5, v, 10.0)                     # 半分まで広がった
    assert _close(r, 5.0) and _close(w, 0.4 * 0.75) and vis
    r, w, vis = shockwave.ring_state(5.0, v, 10.0)                     # 端に届いた
    assert _close(r, 10.0) and _close(w, 0.2) and vis
    r, w, vis = shockwave.ring_state(7.0, v, 10.0)                     # 消えている途中
    assert _close(r, 10.0) and _close(w, 0.1) and vis
    assert not shockwave.ring_state(9.5, v, 10.0)[2]                   # 消えた
    # ブレンドシェイプのウェイトは 0〜1: チューブは半径より太くならない
    assert _close(shockwave.actual_tube(0.5, 2.0), 0.5)
    assert _close(shockwave.actual_tube(4.0, 1.0), 1.0)


def test_ghost_blur_params_are_live_or_rebuild():
    for kind in ghost_blur.KINDS.values():
        names = sorted(p.name for p in kind.tool.params)
        assert sorted(kind.live + kind.rebuild) == names
        assert not set(kind.live) & set(kind.rebuild)
        assert [p.name for p in kind.stored_params()] == list(kind.live + kind.rebuild)


def test_ghost_blur_euler_roundtrip():
    for angles in ((10, 20, 30), (-170, 45, 95), (0, 89.9, 10), (120, -60, -150)):
        rows = ghost_blur.rotation_rows(*angles)
        again = ghost_blur.rotation_rows(*ghost_blur.euler_xyz(*rows))
        assert all(_close(a, b, 1e-6) for r1, r2 in zip(rows, again) for a, b in zip(r1, r2))
    # 真上を向く (ジンバルロック) 時も同じ向きになる
    rows = ghost_blur.rotation_rows(30, 90, 0)
    again = ghost_blur.rotation_rows(*ghost_blur.euler_xyz(*rows))
    assert all(_close(a, b, 1e-6) for r1, r2 in zip(rows, again) for a, b in zip(r1, r2))


def test_ghost_blur_fades():
    assert ghost_blur.ghost_ratio(0, 5) == 0.0 and ghost_blur.ghost_ratio(4, 5) == 1.0
    assert ghost_blur.ghost_ratio(0, 1) == 0.0
    assert ghost_blur.speed_factor(0.05, 0.1, 0.5) == 0.0
    assert _close(ghost_blur.speed_factor(0.3, 0.1, 0.5), 0.5)
    assert ghost_blur.speed_factor(0.9, 0.1, 0.5) == 1.0
    assert ghost_blur.speed_factor(0.2, 0.1, 0.1) == 1.0     # しきい値が同じなら、超えたらくっきり
    assert ghost_blur.smear_weight(0.2, 0.3, 1.0) == 0.0     # 伸び始める位置より前は伸びない
    assert _close(ghost_blur.smear_weight(0.65, 0.3, 1.0), 0.5)
    assert _close(ghost_blur.smear_weight(0.65, 0.3, 2.0), 0.25)
    assert ghost_blur.smear_weight(1.0, 0.3, 3.0) == 1.0
    assert _close(ghost_blur.trail_taper(6, 24, 1.0, 0.0), 0.75)


def test_ghost_blur_trail_layout():
    pts, counts, conns, us, vs, uv_ids, per_row = ghost_blur.trail_layout(ghost_blur.SHAPE_RIBBON, 4, 8)
    assert per_row == 2 and len(pts) == 10 and len(counts) == 4 and len(us) == 10
    assert pts[2] == (1.0, 0.5, 0.0) and pts[3] == (1.0, -0.5, 0.0)
    pts, counts, conns, us, vs, uv_ids, per_row = ghost_blur.trail_layout(ghost_blur.SHAPE_TUBE, 4, 8)
    assert per_row == 8 and len(pts) == 40 and len(counts) == 32 and len(us) == 5 * 9
    assert all(_close(math.hypot(p[1], p[2]), 0.5) for p in pts)
    assert len(conns) == len(uv_ids) == 4 * 32 and max(uv_ids) == len(us) - 1
    assert ghost_blur.lattice_index(1, 2, 3, 2, 3) == 23


def _translate(x, y, z):
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def test_ghost_blur_smear_points():
    v = ghost_blur.SmearTool.default_values()
    v.update(frames=3.0, amount=1.0, start=0.0, falloff=1.0, samples=3)
    divs = (2, 2, 2)
    rest = [c for idx in range(8)
            for c in (idx % 2 - 0.5, (idx // 2) % 2 - 0.5, idx // 4 - 0.5)]
    # 止まっていれば元の位置
    still = [_translate(5, 0, 0)] * 4
    out = ghost_blur.smear_points(still, _translate(-5, 0, 0), (0, 0, 0), (2, 2, 2), divs, v, 100.0)
    assert all(_close(a, b) for a, b in zip(out, rest))
    # X に 1 フレーム 1 動く: 後ろ (-X) の点は 3 フレーム前の位置 (3 後ろ) に残り、前はそのまま
    mats = [_translate(5 - s, 0, 0) for s in range(4)]
    out = ghost_blur.smear_points(mats, _translate(-5, 0, 0), (0, 0, 0), (2, 2, 2), divs, v, 100.0)
    for idx in range(8):
        x = out[idx * 3]
        assert _close(x, 0.5) if idx % 2 else _close(x, -0.5 - 3.0 / 2)   # 大きさ 2 で正規化
    # 上限 (cap = 1) で止まる
    out = ghost_blur.smear_points(mats, _translate(-5, 0, 0), (0, 0, 0), (2, 2, 2), divs, v, 1.0)
    assert _close(out[0], -0.5 - 1.0 / 2)


def test_weather_params_are_live_rebuild_or_create():
    assert len(wx.KINDS) == 28   # (+ スピードライン (トンネル)、電撃 2 つ、落ち葉 2 つ、足元の砂煙 2 つ)
    # 天候 18 (稲妻・放電 各 2 つ・細い煙を含む) + 核爆発・爆発 (単体)・波打つ布 (同じ仕組み)
    for kind in wx.KINDS.values():
        names = set(p.name for p in kind.tool.params)
        stored = set(kind.live) | set(kind.rebuild)
        assert not set(kind.live) & set(kind.rebuild)
        assert stored <= names
        # 保存しないのは作成時だけの範囲の箱の設定
        assert names - stored <= set(("area_width", "area_height", "follow_camera", "height"))
        if kind.need_wind:
            assert "wind_influence" in kind.live
        assert "seed" in names


def test_weather_hash_is_stable_and_uniform():
    a = wx.hash01(3, 5, 1, 1)
    assert a == wx.hash01(3, 5, 1, 1) and 0 <= a < 1
    values = [wx.hash01(i, 0, 1, 7) for i in range(2000)]
    assert 0.45 < sum(values) / len(values) < 0.55
    assert len(set(round(v, 6) for v in values)) > 1990
    assert wx.mel_hash("$i", 0, 3) == ("fmod(abs(sin(($i) * 12.9898 + (0) * 78.233 + 3.0 * 37.719"
                                        " + $sd * 4.1414) * 43758.5453), 1.0)")


def test_wind_series_and_integral():
    v = [wx.wind_vector((1.0, 0.0, 0.0), 0.2, 0.0, 1.5, 0.0, t, 1) for t in range(5)]
    assert all(_close(w[0], 0.2) and _close(w[2], 0.0) for w in v)
    drift = wx.integrate(v)
    assert _close(drift[4][0], 0.8) and drift[0] == (0.0, 0.0, 0.0)
    # 表の読み方 (直線補間、範囲外は端)
    assert _close(wx.table_value(drift, -2, 0.5)[0], 0.2 * 2.5)
    assert wx.table_value(drift, 0, -10) == drift[0] and wx.table_value(drift, 0, 99) == drift[-1]
    # 突風: interval ごとに 0〜(1 + ばらつき) の山
    g = [wx.gust_factor(t, 3, 40.0, 16.0, 0.0) for t in range(0, 400)]
    assert min(g) >= 0 and 0.9 < max(g) <= 1.0 + 1e-9
    assert sum(1 for a, b in zip(g, g[1:]) if a == 0 and b > 0) >= 8


def test_spark_cone_direction():
    for h1 in (0.0, 0.3, 1.0):
        for h2 in (0.0, 0.6):
            d = weather.cone_direction((0.0, 1.0, 0.0), h1, h2, 50.0)
            assert _close(math.sqrt(sum(x * x for x in d)), 1.0)
            assert math.degrees(math.acos(max(-1.0, min(1.0, d[1])))) <= 50.0 * h1 + 1e-6
    d = weather.cone_direction((1.0, 0.0, 0.0), 1.0, 0.25, 90.0)   # 上が X 軸でも直交する向き
    assert _close(d[0], 0.0)


def test_spark_bounces_on_the_ground():
    p, v = weather.spark_motion((0, 2, 0), (0.5, 0.5, 0), 0.05, 0.0, 0.5, 5.0)
    assert _close(p[0], 2.5) and _close(p[1], 2 + 2.5 - 0.5 * 0.05 * 25)        # まだ地面に着かない
    ys = [weather.spark_motion((0, 2, 0), (0.5, 0.5, 0), 0.05, 0.0, 0.5, a)[0][1] for a in range(0, 120)]
    assert min(ys) >= -1e-9                                                       # 地面より下に行かない
    # 跳ねるたびに低くなる
    peaks = [ys[i] for i in range(1, len(ys) - 1) if ys[i - 1] < ys[i] >= ys[i + 1]]
    assert len(peaks) >= 2 and peaks[1] < peaks[0]


def test_falling_cycle():
    c, age = weather.cycle(25.0, 10.0, 0.3)
    assert c == 2 and _close(age, 8.0)
    c2, age2 = weather.cycle(27.0, 10.0, 0.3)
    assert c2 == 3 and _close(age2, 0.0)


def test_weather_layer_helpers():
    around, rv = weather_layers.far_repeat(12.0, 40.0, 3.0, 2.0)
    assert around == 25 and _close(rv, 40.0 / 6.0)
    assert _close(1 - (1 - weather_layers.layer_opacity(0.8, 5)) ** 5, 0.8)
    assert weather_layers.ground_layer_opacity(0.6, 1.5, 0, 4) == 0.6
    assert weather_layers.ground_layer_opacity(0.6, 0.0, 3, 4) == 0.6
    assert weather_layers.ground_layer_opacity(0.6, 2.0, 2, 4) == 0.6 * 0.25
    pts, faces, uvs, uv_ids = weather_layers.cylinder_layout(8)
    assert len(pts) == 16 and len(faces) == 8 and len(uvs) == 18 and max(uv_ids) == 17


class _FakePanelCmds(object):
    """ビューポートの問い合わせだけを真似る cmds。"""

    def __init__(self, panels, batch=False):
        self.panels = panels   # {名前: (種類, 表示, テクスチャ表示)}
        self.batch = batch

    def about(self, batch=False):
        return self.batch

    def getPanel(self, visiblePanels=False, typeOf=None):
        if typeOf:
            return self.panels[typeOf][0]
        return list(self.panels)

    def modelEditor(self, panel, query=True, displayAppearance=False, displayTextures=False):
        kind, appearance, textures = self.panels[panel]
        return appearance if displayAppearance else textures


def test_warns_when_viewport_textures_are_off():
    logs = []
    ctx = ToolContext(None, log=logs.append)
    on = _FakePanelCmds({"modelPanel4": ("modelPanel", "smoothShaded", True),
                         "outlinerPanel1": ("outlinerPanel", None, None)})
    assert not wx.warn_if_textures_hidden(on, ctx) and not logs
    off = _FakePanelCmds({"modelPanel4": ("modelPanel", "smoothShaded", False)})
    assert wx.panels_without_textures(off) == ["modelPanel4"]
    assert wx.warn_if_textures_hidden(off, ctx) and u"6 キー" in logs[-1]
    wire = _FakePanelCmds({"modelPanel4": ("modelPanel", "wireframe", False)})
    assert wx.panels_without_textures(wire) == []          # ワイヤーフレーム表示なら関係ない
    batch = _FakePanelCmds({"modelPanel4": ("modelPanel", "smoothShaded", False)}, batch=True)
    assert wx.panels_without_textures(batch) == []
    assert set(wx.TEXTURED_TYPES) <= set(wx.KINDS)


class _FakeAlgoCmds(object):
    def __init__(self, value):
        self.value = value

    def getAttr(self, plug):
        if self.value is None:
            raise RuntimeError("no node")
        return self.value


def test_warns_when_transparency_is_alpha_cut():
    logs = []
    ctx = ToolContext(None, log=logs.append)
    assert not wx.warn_if_alpha_cut(_FakeAlgoCmds(1), ctx) and not logs     # オブジェクトのソート
    assert not wx.warn_if_alpha_cut(_FakeAlgoCmds(None), ctx)                # ノードが無い
    assert wx.warn_if_alpha_cut(_FakeAlgoCmds(5), ctx) and u"アルファ カット" in logs[-1]
    assert set(wx.TRANSLUCENT_TYPES) <= set(wx.KINDS)


def test_file_param_and_weather_texture_option():
    p = Param("t", "t", "", kind="file")
    assert p.coerce(None) == "" and p.coerce("C:/a.png") == "C:/a.png"
    with_texture = [k.effect_type for k in wx.KINDS.values()
                    if "texture" in [p.name for p in k.tool.params]]
    assert sorted(with_texture) == sorted(["dust", "foot_dust", "rain_far", "snow_far", "fog", "ground_fog",
                                           "ground_crawl", "wisp"])
    for name in with_texture:
        kind = wx.KINDS[name]
        assert "texture" in kind.rebuild and "texture" not in kind.live
        assert [p for p in kind.tool.params if p.name == "texture"][0].kind == "file"


def test_file_param_roundtrips_in_presets():
    with _TempDir() as d:
        params = [Param("texture", "t", "", kind="file"), Param("n", "n", 1)]
        path = os.path.join(d, "p.json")
        presets.save_preset(path, "dust", params, {"texture": u"D:/絵/煙.png", "n": 3})
        assert presets.load_preset(path, "dust", params) == {"texture": u"D:/絵/煙.png", "n": 3}


def test_explosion_params_are_live_or_rebuild():
    names = sorted(p.name for p in explosion.ExplosionTool.params)
    assert sorted(explosion.LIVE_PARAMS + explosion.REBUILD_PARAMS) == names
    assert not set(explosion.LIVE_PARAMS) & set(explosion.REBUILD_PARAMS)


def test_explosion_hit_helpers():
    assert explosion.sample_times(1, 3, 2) == [1.0, 1.5, 2.0, 2.5, 3.0]
    assert _close(explosion.segment_param((0, 0, 0), (10, 0, 0), (2.5, 1, 0)), 0.25)
    assert explosion.segment_param((0, 0, 0), (0, 0, 0), (1, 1, 1)) == 0.0
    assert explosion.BULLET_NAME.search("dfx_vulcan1_bullet_003")
    assert explosion.BULLET_NAME.search("dfx_energy1_ball_02")
    assert not explosion.BULLET_NAME.search("dfx_homing1_bulletSrc")


# ---------------------------------------------------------------- 土台 (カタログ・受け渡し形式・ホスト)
from dummy_effect_maker import catalog  # noqa: E402
from dummy_effect_maker.core import exchange as xc  # noqa: E402


def _registered_effect_types():
    types = set()
    for m in (homing, vulcan, energy_ball, focus_lines, speed_lines, camera_shake, hand_shake,
              shockwave, explosion):
        types.add(m.EFFECT_TYPE)
    types.update(ghost_blur.KINDS)
    types.update(wx.KINDS)
    return types


def test_catalog_covers_every_effect():
    specs = catalog.specs()
    assert set(s.effect_type for s in specs) == _registered_effect_types()
    for s in specs:
        names = [p.name for p in s.params]
        assert set(s.live) | set(s.rebuild) == set(names)
        assert s.group in (catalog.BULLETS, catalog.CAMERA, catalog.BURST, catalog.PARTICLES,
                           catalog.LAYERS, catalog.MOTION, catalog.CONTROL)
    text = json.dumps(catalog.describe_all(), ensure_ascii=False)
    assert u"バルカン砲" in text and catalog.spec("vulcan").describe()["color"]["kind"] == "color"
    with pytest.raises(KeyError):
        catalog.spec("nothing")


def test_host_detect_outside_maya_and_blender():
    assert host.detect().name == "standalone"


def _close3(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_exchange_converts_maya_space():
    # Maya (Y が上・センチ) → 共通 (Z が上・メートル): (x, y, z) → (x, -z, y) / 100
    assert _close3(xc.convert_point((100, 200, 300), "y", 0.01), (1, -3, 2))
    assert _close3(xc.convert_point((100, 200, 300), "z", 0.01), (1, 2, 3))
    assert _close3(xc.convert_vector((0, 1, 0), "y"), (0, 0, 1))      # Maya の上 → 共通の上
    # Y 軸まわり 90 度 (Maya) の回転: ローカル X 軸が Maya の -Z を向く → 共通の +Y を向く
    m = [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1]
    t, q, s = xc.decompose(xc.convert_matrix(m, "y", 0.01))
    rows = xc.rows_from_quaternion(q)
    assert _close3(rows[0], (0, 1, 0)) and _close3(rows[1], (0, 0, 1)) and _close3(s, (1, 1, 1))
    # 拡大縮小と負の拡大縮小
    t, q, s = xc.decompose([2, 0, 0, 0, 0, 3, 0, 0, 0, 0, -4, 0, 5, 6, 7, 1])
    assert _close3(t, (5, 6, 7)) and _close3(s, (2, 3, -4)) and _close3(q, (0, 0, 0, 1))


def test_exchange_quaternion_roundtrip():
    import math as _m
    for ax, ang in (((1, 0, 0), 30), ((0, 1, 0), 170), ((0.3, 0.4, 0.866), 95), ((0, 0, 1), 180)):
        n = _m.sqrt(sum(a * a for a in ax))
        ax = [a / n for a in ax]
        h = _m.radians(ang) / 2
        q = tuple([a * _m.sin(h) for a in ax] + [_m.cos(h)])
        back = xc.quaternion_from_rows(xc.rows_from_quaternion(q))
        if back[3] * q[3] < 0 or (abs(q[3]) < 1e-9 and sum(a * b for a, b in zip(q, back)) < 0):
            back = tuple(-x for x in back)
        assert _close3(back, q, 1e-9)


def test_exchange_to_unreal():
    p, q = xc.to_unreal((1.0, 2.0, 3.0), (0.1, 0.2, 0.3, 0.9))
    assert _close3(p, (100, -200, 300)) and _close3(q, (-0.1, 0.2, -0.3, 0.9))


def test_exchange_tracks():
    still = xc.make_track([1, 2, 3], [{"t": (1.0, 2.0, 3.0), "v": True}] * 3)
    assert still == {"static": True, "t": [1.0, 2.0, 3.0], "v": True}
    moving = xc.make_track([1, 2, 3], [{"t": (0.0, 0.0, float(i))} for i in range(3)])
    assert not moving["static"] and moving["frames"] == [1, 2, 3]
    assert xc.track_value(moving, "t", 2.5) == [0.0, 0.0, 1.5]
    assert xc.track_value(moving, "t", 99) == [0.0, 0.0, 2.0]
    assert xc.track_value(still, "t", 50) == [1.0, 2.0, 3.0]


def test_exchange_file_roundtrip_and_errors():
    with _TempDir() as d:
        path = os.path.join(d, "shot.json")
        shot = xc.new_shot({"app": "test"}, 24.0, 1, 10)
        shot["effects"].append({"type": "vulcan", "params": {"color": [1, 0, 0]}})
        xc.write_shot(path, shot)
        back = xc.read_shot(path)
        assert back["space"] == {"up": "Z", "handedness": "right", "unit": "meter"}
        assert back["effects"][0]["params"]["color"] == [1, 0, 0]
        _write_json(path, {"format": "other"})
        with pytest.raises(xc.ExchangeError):
            xc.read_shot(path)
        _write_json(path, {"format": xc.FORMAT_NAME, "version": xc.FORMAT_VERSION + 1})
        with pytest.raises(xc.ExchangeError):
            xc.read_shot(path)


def test_nuke_timeline_and_mushroom_shape():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import nuke
    v = nuke.NukeTool.default_values()
    v.update(start_frame=10.0)
    p0, axes = (0.0, 0.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    # 開始前は何も見えない。閃光は最初だけ
    assert not mo.nuke_flash(9, v, p0, axes)[2] and not mo.nuke_cap(0, 28, 9, v, p0, axes)[2]
    assert mo.nuke_flash(12, v, p0, axes)[2] and not mo.nuke_flash(10 + v["flash_frames"] + 1, v, p0, axes)[2]
    # 雲は上がっていく (かさの高さは単調に増え、最後は大きさ × 高さに近づく)
    hs = [mo.nuke_state(f, v)["H"] for f in (12, 40, 100, 250)]
    assert hs == sorted(hs) and abs(hs[-1] - v["size"] * v["cloud_height"]) < 0.5
    # 衝撃波の輪は地面 (爆心の X-Z 面) で広がって薄れる
    a = mo.nuke_shock(15, v, p0, axes)
    b = mo.nuke_shock(35, v, p0, axes)
    assert b[2] > a[2] and b[4] < a[4] and abs(a[0][1] - v["size"] * 0.02) < 1e-9
    # 雲の色: 最初は火の色、冷えると煙の色。最後の 1/4 で薄れる
    c0, _, al0 = mo.nuke_colors(10, v)
    c1, _, al1 = mo.nuke_colors(10 + v["cool_frames"] + 5, v)
    assert _close(c0[0], v["fire_color"][0]) and _close(c1[2], v["cloud_color"][2]) and al0 == v["opacity"]
    assert mo.nuke_colors(10 + v["life_frames"] * 0.9, v)[2] < 0.5
    # 爆心を傾けると、雲はその上の向きに上がる
    tilt = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    p, _, _, _ = mo.nuke_fireball(80, v, (5.0, 0.0, 0.0), tilt)
    assert p[2] > v["size"] and abs(p[1]) < 1e-9


def test_nuke_hemisphere_and_params():
    from dummy_effect_maker.tools import nuke
    pts, faces = nuke.hemisphere_points(12, 4)
    assert len(pts) == 1 + 12 * 4 and all(p[1] >= -1e-9 for p in pts)
    assert all(abs(sum(c * c for c in p) - 1) < 1e-9 for p in pts)
    assert set(nuke.KIND.rebuild) == set(("stem_count", "cap_count", "base_count"))
    assert not nuke.KIND.need_wind and nuke.KIND.need_camera


def test_lightning_strikes_and_shapes():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import lightning as lt
    v = lt.LightningRealTool.default_values()
    v.update(count=3, interval=20.0, interval_random=0.0, start_frame=5.0)
    # 落ちる回: 開始から間隔ごと、光るフレーム数だけ
    assert mo.lightning_strike(4, v) is None and mo.lightning_strike(5, v) == (0, 5.0)
    assert mo.lightning_strike(5 + v["duration"] + 1, v) is None and mo.lightning_strike(26, v)[0] == 1
    assert mo.lightning_strike(70, v) is None      # 回数を過ぎたら落ちない
    top, gnd, cp, cdir = (0.0, 40.0, 0.0), (0.0, 0.0, 0.0), (0.0, 10.0, 60.0), (0.0, 0.0, -1.0)
    n, nb, m = 30, 4, 8
    rows, alpha = mo.lightning_rows(mo.STYLE_REAL, 8, v, top, gnd, cp, cdir, n, nb, m)
    assert len(rows) == n + nb * m and alpha > 0
    t0, g0 = mo.lightning_ends(0, v, top, gnd)
    assert _close(rows[0][0][1], 40.0) and _close(rows[n - 1][0][1], 0.0)    # 雲から地面まで
    assert all(abs(t0[0] - 0) <= v["spread"] / 2 for _ in [0]) and abs(g0[0]) <= v["spread"]
    assert rows[0][2] > rows[n - 1][2] > 0                                    # 地面の方が細い
    # 伸び始め: 最初の点だけ太さがある。光っていない時は全部 0
    early, _ = mo.lightning_rows(mo.STYLE_REAL, 5, v, top, gnd, cp, cdir, n, nb, m)
    assert [w > 0 for _, _, w in early[:n]].count(True) == 1
    dark, a0 = mo.lightning_rows(mo.STYLE_REAL, 15, v, top, gnd, cp, cdir, n, nb, m)
    assert a0 == 0 and all(w == 0 for _, _, w in dark)
    # アニメ: 本筋の点は左右に交互にずれる (ジグザグ)
    va = lt.LightningAnimeTool.default_values()
    va.update(count=1, start_frame=1.0, spread=0.0)
    rows, _ = mo.lightning_rows(mo.STYLE_ANIME, 6, va, top, gnd, cp, cdir, 12, 2, 5)
    _, d, a, _ = mo.lightning_frame(top, gnd)
    side = [mo.dot(mo.sub(rows[k * mo.ANIME_SUB][0], mo.add(top, mo.mul(mo.sub(gnd, top), k / 11.0))), a) for k in range(1, 11)]
    assert all(x * y < 0 for x, y in zip(side[:-1], side[1:]))


def test_explosion_single_params_and_hit():
    from dummy_effect_maker.tools import explosion, explosion_single as es
    names = [p.name for p in es.ExplosionSingleTool.params]
    # 爆発と同じ見た目の項目 + 開始フレーム。弾・当たり判定の項目は無い
    assert names[0] == "start_frame" and "hide_bullet" not in names and "substeps" not in names
    assert set(names) - set(["start_frame"]) <= set(p.name for p in explosion.ExplosionTool.params)
    assert set(es.KIND.rebuild) == set(("fire_count", "smoke_count", "spark_count"))
    assert not es.KIND.need_wind and es.KIND.need_camera
    v = es.ExplosionSingleTool.default_values()
    v.update(start_frame=12.0)
    hit = es.hit_of(v, (1, 2, 3), (0, 1, 0))
    assert hit == {"frame": 12.0, "pos": (1, 2, 3), "normal": (0, 1, 0)}


def test_discharge_flows_between_two_points():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import lightning as lt
    v = lt.DischargeAnimeTool.default_values()
    v.update(start_frame=5.0, end_frame=20.0, change_frames=2.0, spread=0.0, flicker=0.0)
    # 開始〜終わりの間ずっと流れ、形の変わる回は 2 フレームごと
    assert mo.discharge_strike(4, v) is None and mo.discharge_strike(21, v) is None
    assert mo.discharge_strike(5, v) == (0, 5.0) and mo.discharge_strike(8.5, v) == (1, 7.0)
    a, b = (0.0, 8.0, 0.0), (6.0, 0.0, 0.0)
    n, nb, m = int(v["segments"]), int(v["branches"]), int(v["branch_segments"])
    rows, alpha = mo.discharge_rows(mo.STYLE_ANIME, 5, v, a, b, (0, 4, 30), (0, 0, -1), n, nb, m)
    rn = mo.strand_rows(mo.STYLE_ANIME, n)
    # 最初のフレームから発生と終端がつながる (伸びるのを待たない)
    assert alpha > 0 and _close(rows[0][0][1], 8.0) and all(abs(x - y) < 1e-9 for x, y in zip(rows[rn - 1][0], b))
    assert all(w > 0 for _, _, w in rows[:rn - 1])
    # 当たりを調べるフレームと線分 (真ん中から、終端の少し先まで)
    assert mo.discharge_samples(dict(v, burst_interval=4.0)) == [5.0, 9.0, 13.0, 17.0]
    p0, p1 = mo.discharge_probe(a, b, 1.0)
    assert _close(p0[0], 3.0) and _close(p0[1], 4.0) and _close(p1[0], 6.6) and _close(p1[1], -0.8)
    names = [p.name for p in lt.DischargeAnimeTool.params]
    assert "count" not in names and "end_frame" in names and "flash_size" in names and "sides" in names


def test_wisp_rises_straight_then_sways():
    near = _close3
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import wisp
    v = wisp.WispTool.default_values()
    v.update(start_frame=1.0, root_spread=0.0)
    origin = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2.0, 1.0, -3.0, 1]
    at = lambda t: origin   # noqa: E731
    pts = 40
    p0, w0, alive0 = mo.wisp_point(0, 0, 200, v, pts, at, mo.Wind())
    assert alive0 and near(p0, (2.0, 1.0, -3.0)) and _close(w0, v["width_start"])
    # まっすぐ上がる間は横にずれない。上ほど高く、太い
    j_lam = int(v["laminar"] / v["life"] * (pts - 1))
    p, w, _ = mo.wisp_point(0, j_lam, 200, v, pts, at, mo.Wind())
    assert abs(p[0] - 2.0) < 1e-9 and abs(p[2] + 3.0) < 1e-9 and p[1] > 1.0 and w > w0
    tip, wt, _ = mo.wisp_point(0, pts - 1, 200, v, pts, at, mo.Wind())
    assert tip[1] > p[1] and wt > w and (abs(tip[0] - 2.0) > 0.05 or abs(tip[2] + 3.0) > 0.05)
    # 出始め: まだ出ていない所は太さ 0 で、一番古い点に重なる
    rows = mo.wisp_rows(0, 5, v, pts, at, mo.Wind(), (0, 5, 40), (0, 0, -1))
    born = [r for r in rows if r[2] > 0]
    assert 0 < len(born) < pts and all(near(r[0], born[-1][0]) for r in rows[len(born):])
    # 筋ごとに揺れ方が違う
    a = mo.wisp_point(0, pts - 1, 200, v, pts, at, mo.Wind())[0]
    b = mo.wisp_point(1, pts - 1, 200, v, pts, at, mo.Wind())[0]
    assert not near(a, b)
    assert set(wisp.KIND.rebuild) == set(("count", "points", "style", "texture"))


def test_lightning_jag_tapers_toward_end():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import lightning as lt
    va = lt.LightningAnimeTool.default_values()
    assert va["jag_taper"] == 0.3 and lt.LightningRealTool.default_values()["jag_taper"] == 1.0
    assert "jag_taper" in lt.ANIME.live and "jag_taper" in lt.DISCHARGE_ANIME.live
    top, gnd = (0.0, 40.0, 0.0), (0.0, 0.0, 0.0)
    n = 12
    _, d, a, _ = mo.lightning_frame(top, gnd)

    def sideways(v):
        out = []
        for k in range(1, n - 1):
            p = mo.lightning_main_point(mo.STYLE_ANIME, k, n, 0, v, top, gnd)
            base = mo.add(top, mo.mul(mo.sub(gnd, top), k / float(n - 1)))
            out.append(abs(mo.dot(mo.sub(p, base), a)))
        return out

    flat = sideways(dict(va, jag_taper=1.0))
    taper = sideways(dict(va, jag_taper=0.3))
    # 雲の側はほとんど同じ、終端に近いほど小さく (終端の手前で 0.3 + 少し)
    assert abs(taper[0] / flat[0] - (1 - 0.7 * 1 / 11.0)) < 1e-9
    assert abs(taper[-1] / flat[-1] - (1 - 0.7 * 10 / 11.0)) < 1e-9
    assert mo.jag_scale({"jag_taper": 0.0}, 1.0) == 0.0 and mo.jag_scale({}, 0.7) == 1.0


def test_cloth_waves_layers_and_pins():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import cloth
    v = cloth.ClothTool.default_values()
    # 奥の布ほど奥 (-Z) で少し高い。色は手前 → 奥
    assert mo.cloth_layer_offset(2, v) == (0.0, 2 * v["height_step"], -2 * v["spacing"])
    assert _close3(mo.cloth_color(0, 4, v), v["color_front"]) and _close3(mo.cloth_color(3, 4, v), v["color_back"])
    # 波は長さの向きに流れる (周期でもとに戻る)。布ごとにずれる
    a = mo.cloth_wave(0, 0.1, 0.2, 10, v)
    assert abs(mo.cloth_wave(0, 0.1, 0.2, 10 + v["period"], dict(v, cross=0.0)) - mo.cloth_wave(0, 0.1, 0.2, 10, dict(v, cross=0.0))) < 1e-6
    assert abs(a) <= v["amplitude"] * (1 + v["cross"]) + 1e-9 and a != mo.cloth_wave(1, 0.1, 0.2, 10, v)
    # 両端を押さえると端は 0
    pinned = dict(v, pin_ends=True)
    assert abs(mo.cloth_wave(0, -0.5, 0.0, 7, pinned)) < 1e-12 and abs(mo.cloth_wave(0, 0.5, 0.3, 7, pinned)) < 1e-9
    grid = mo.cloth_lattice(0, 5, v, 6, cloth.ACROSS)
    assert len(grid) == 6 and all(len(r) == cloth.ACROSS for r in grid)
    assert set(cloth.KIND.rebuild) == set(("layers", "length", "width", "divisions"))
    assert cloth.ClothTool.tab == u"布" and not cloth.KIND.need_wind and not cloth.KIND.need_camera


def test_speed_lines_reach_clamps_tilted_region():
    from dummy_effect_maker.tools import speed_lines
    zc = speed_lines.camera_depth(35.0, 1.417)
    wide = speed_lines.plane_region(16 / 9.0, zc, (0, 0, 0, 65, 0))
    cut = speed_lines.plane_region(16 / 9.0, zc, (0, 0, 0, 65, 0), 4.0)
    assert wide[1] > 10 and cut[1] == 4.0 and cut[2] == -4.0 and cut[0] == wide[0]   # 手前側はそのまま
    flat = speed_lines.plane_region(16 / 9.0, zc, (0, 0, 0, 0, 0))
    assert speed_lines.plane_region(16 / 9.0, zc, (0, 0, 0, 0, 0), 4.0) == flat        # 傾けなければ変わらない
    assert "reach" in speed_lines.LIVE_PARAMS


def test_tunnel_lines_flow_toward_vanishing_point():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import tunnel_lines as tl
    v = tl.TunnelLinesTool.default_values()
    # トンネルの軸: -Z が消失点の向き、Y はなるべく上、直交
    x, y, z = mo.tunnel_basis((3.0, 1.5, -20.0))
    d = mo.unit((3.0, 1.5, -20.0))
    assert _close3(mo.mul(z, -1.0), d, 1e-9) and abs(mo.dot(x, y)) < 1e-9 and abs(x[1]) < 1e-9 and y[1] > 0.9
    # 壁の上 (半径のばらつき 0 なら軸からちょうど半径)、手前の端〜完全に消える距離の間
    flat = dict(v, radius_random=0.0)
    for i in range(20):
        p, axes, s3 = mo.tunnel_item(i, 13, flat, True)
        assert abs((p[0] ** 2 + p[1] ** 2) ** 0.5 - v["radius"]) < 1e-9
        assert v["near"] - 1e-9 <= -p[2] <= v["fade_end"] + 1e-9
        assert abs(mo.dot(axes[2], (p[0], p[1], 0.0))) > 0 and mo.dot(axes[2], (p[0], p[1], 0.0)) < 0   # 内側を向く
    # 奥へ / 手前へ
    a0 = -mo.tunnel_item(0, 10, v, True)[0][2]
    a1 = -mo.tunnel_item(0, 10.2, v, True)[0][2]
    t = dict(v, direction=mo.TUNNEL_TOWARD)
    b0 = -mo.tunnel_item(0, 10, t, True)[0][2]
    b1 = -mo.tunnel_item(0, 10.2, t, True)[0][2]
    assert a1 > a0 and b1 < b0
    # 完全に消える距離の近くは小さい (形)
    sizes = [(-mo.tunnel_item(i, 7, v, False)[0][2], mo.tunnel_item(i, 7, v, False)[2][0]) for i in range(60)]
    assert all(s3 == 0 or dd < v["fade_end"] for dd, s3 in sizes)
    for shape, faces in ((tl.SHAPE_LINE, tl.WIDTH_SAMPLES - 1), (tl.SHAPE_STAR, 10), (tl.SHAPE_HEART, 24),
                         (tl.SHAPE_DIAMOND, 4)):
        assert len(tl.shape_points(shape)[1]) == faces
    assert set(tl.KIND.rebuild) == set(("shape", "count", "trail", "ghost_count")) and "direction" in tl.KIND.live
    for name in ("roll", "roll_speed", "speed_random", "width_start", "width_mid", "width_end", "thickness_random", "length_random"):
        assert name in tl.KIND.live


def test_tunnel_lines_ghosts_and_tails():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import tunnel_lines as tl
    v = dict(tl.TunnelLinesTool.default_values(), shape=tl.SHAPE_STAR, ghost_count=4, ghost_step=2.5,
             ghost_fade=0.8, ghost_shrink=0.5, roll_speed=3.0)
    # 残像・尾を使うか (尾は形の時だけ)
    assert tl.ghost_levels(dict(v, trail=tl.TRAIL_NONE)) == 0 and not tl.has_tail(dict(v, trail=tl.TRAIL_NONE))
    assert tl.ghost_levels(dict(v, trail=tl.TRAIL_GHOST)) == 4 and not tl.has_tail(dict(v, trail=tl.TRAIL_GHOST))
    assert tl.ghost_levels(dict(v, trail=tl.TRAIL_TAIL)) == 0 and tl.has_tail(dict(v, trail=tl.TRAIL_TAIL))
    assert tl.ghost_levels(dict(v, trail=tl.TRAIL_BOTH)) == 4 and tl.has_tail(dict(v, trail=tl.TRAIL_BOTH))
    assert not tl.has_tail(dict(v, trail=tl.TRAIL_BOTH, shape=tl.SHAPE_LINE))
    assert tl.ghost_levels(dict(v, trail=tl.TRAIL_GHOST, shape=tl.SHAPE_LINE)) == 4
    # 残像 k 段目 = 間隔 × k フレーム前の形を小さくしたもの (回っていれば通り道に沿って並ぶ)
    for k in (1, 2, 4):
        p, axes, sc = mo.tunnel_ghost(7, 30, k, v, False)
        q, axes2, sc2 = mo.tunnel_item(7, 30 - 2.5 * k, v, False)
        m = 1 - 0.5 * k / 5.0
        assert _close3(p, q, 1e-12) and all(abs(sc[j] - sc2[j] * m) < 1e-12 for j in range(3))
        assert _close3(axes[0], axes2[0], 1e-12)
    # 後ろほど薄い・小さい。薄くなり方 0 なら全部同じ濃さ
    ops = [mo.tunnel_ghost_opacity(k, v) for k in range(1, 5)]
    assert all(a > b for a, b in zip(ops, ops[1:])) and ops[-1] > 0
    assert mo.tunnel_ghost_opacity(3, dict(v, ghost_fade=0.0)) == v["opacity"]
    assert all(mo.tunnel_ghost_scale(k, v) > mo.tunnel_ghost_scale(k + 1, v) for k in range(1, 4))


def test_tunnel_lines_roll_and_width():
    import math
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import tunnel_lines as tl, speed_lines as sl
    v = dict(tl.TunnelLinesTool.default_values(), radius_random=0.0, length_random=0.0, thickness_random=0.0,
             speed_random=0.0)
    # 回転 (度) はトンネル全体を回すだけ (深さ・大きさは同じ)
    p0, _, s0 = mo.tunnel_item(3, 12, v, True)
    p1, _, s1 = mo.tunnel_item(3, 12, dict(v, roll=90.0), True)
    assert abs(p0[2] - p1[2]) < 1e-9 and abs(s0[1] - s1[1]) < 1e-9
    assert abs(math.atan2(p1[1], p1[0]) - math.atan2(p0[1], p0[0]) - math.pi / 2) % (2 * math.pi) < 1e-6
    # 回り続けると、らせん: 1 フレームで軸の周りに回転の速さだけ進み、線は通り道に沿って伸びる
    r = dict(v, roll_speed=6.0)
    for direction in (mo.TUNNEL_AWAY, mo.TUNNEL_TOWARD):
        rd = dict(r, direction=direction)
        a, axes, sc = mo.tunnel_item(5, 20, rd, True)
        b = mo.tunnel_item(5, 20.01, rd, False)[0]
        da = math.atan2(b[1], b[0]) - math.atan2(a[1], a[0])
        assert abs(da - math.radians(6.0) * 0.01) < 1e-9
        vel = mo.unit(mo.sub(b, a))
        assert mo.dot(axes[1], vel) > 0.98   # 線の向き ≒ 進む向き (弦なので少しずれる)
        assert abs(mo.dot(axes[0], axes[1])) < 1e-9 and abs(mo.dot(axes[2], axes[1])) < 1e-9
        fade = sc[0]   # 太さのばらつき 0 なので 太さの倍率 = 消え方
        assert sc[1] <= v["length"] * fade + 1e-9 and sc[1] > 0.5 * v["length"] * fade - 1e-9
    # 太さはスピードラインと同じ 3 点の 2 次曲線
    w = dict(v, width_start=0.01, width_mid=0.05, width_end=0.002)
    for t in (0.0, 0.3, 0.5, 0.8, 1.0):
        assert abs(mo.tunnel_width_at(t, w) - sl.width_at(t, 0.01, 0.05, 0.002)) < 1e-12
    pts = tl.shape_points(tl.SHAPE_LINE)[0]
    assert len(pts) == 2 * tl.WIDTH_SAMPLES and {p[0] for p in pts} == {-1.0, 1.0}
    assert abs(pts[0][1] + 1.0) < 1e-12 and abs(pts[tl.WIDTH_SAMPLES - 1][1]) < 1e-12
    # 速さのばらつき: 線ごとに 1 フレームで進む距離が違う (0〜1 倍)。0 なら全部同じ
    def step(vv, i):
        return abs(mo.tunnel_item(i, 10.01, vv, True)[0][2] - mo.tunnel_item(i, 10, vv, True)[0][2]) / 0.01
    same = [step(dict(v, speed_random=0.0), i) for i in range(12)]
    rnd = [step(dict(v, speed_random=1.0), i) for i in range(12)]
    assert max(same) - min(same) < 1e-6 and abs(same[0] - v["speed"]) < 1e-6
    assert all(x <= v["speed"] + 1e-6 for x in rnd) and max(rnd) - min(rnd) > 0.2 * v["speed"]
    # 太さのばらつき: 0〜1 倍
    tr = dict(v, thickness_random=1.0)
    ks = [mo.tunnel_item(i, 4, tr, True)[2][0] for i in range(40)]
    assert all(0 <= k <= 1 for k in ks) and max(ks) - min(ks) > 0.2


def test_electric_cling_and_outward():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import electric as el
    v = el.ElectricRealTool.default_values()
    assert v["mode"] == mo.ELECTRIC_CLING and "mode" in el.REAL.live and "count" in el.REAL.rebuild
    assert "sides" in el.ANIME.rebuild and "sides" not in [p.name for p in el.ElectricRealTool.params]
    v.update(start_frame=5.0, end_frame=30.0, change_frames=2.0, flicker=0.0, length_random=0.0)
    # 開始〜終わりの間だけ出る。形の変わる時は線ごとにずれる
    assert mo.electric_cycle(0, 4, v) is None and mo.electric_cycle(0, 31, v) is None
    c, ts = mo.electric_cycle(0, 10, v)
    assert ts <= 10 < ts + 2.0 and mo.electric_cycle(0, ts + 2.0, v)[0] == c + 1
    assert len(set(round(mo.electric_cycle(b, 10, v)[1] % 2.0, 6) for b in range(6))) > 1
    center = (1.0, 2.0, 3.0)
    axes = ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))   # X に 2 倍の楕円体
    n, count = 24, 6
    rows = mo.electric_rows(mo.STYLE_REAL, 10, v, center, axes, (0, 2, 30), (0, 0, -1), count, n)
    assert len(rows) == n * count and all(w > 0 for _, _, w in rows)
    # まとわりつく: 両端は球 (半径 radius) の上、途中も球の外側だけにずれる。両端が細い
    for b in range(count):
        c, _ = mo.electric_cycle(b, 10, v)
        pts, wf = mo.electric_points(mo.STYLE_REAL, b, c, v, n)
        assert _close(mo.mag(pts[0]), v["radius"]) and _close(mo.mag(pts[-1]), v["radius"])
        assert all(mo.mag(p) >= v["radius"] - 0.2 * v["radius"] for p in pts)
        assert wf[0] < wf[n // 2] and _close(wf[0], v["taper"]) and _close(wf[-1], v["taper"])
        # 中心のロケータの空間 → ワールド (X は 2 倍)
        q = pts[0]
        p = rows[b * n][0]
        assert _close(p[0], 1.0 + 2.0 * q[0]) and _close(p[1], 2.0 + q[1]) and _close(p[2], 3.0 + q[2])
    # 外へ放電: 出る所の半径から、長さの分だけ外へ。先が細い
    vo = dict(v, mode=mo.ELECTRIC_OUT, inner=0.5, length=2.0)
    for b in range(count):
        c, _ = mo.electric_cycle(b, 10, vo)
        pts, wf = mo.electric_points(mo.STYLE_ANIME, b, c, vo, 8)
        d0 = mo.electric_dir(b, c, vo["seed"])
        assert _close(mo.mag(pts[0]), 0.5) and _close(mo.dot(pts[-1], d0), 2.5)
        assert _close(wf[0], 1.0) and _close(wf[-1], vo["taper"])
    # ちらつき 1: 回の最初のフレームだけ光る
    vf = dict(v, flicker=1.0)
    c, ts = mo.electric_cycle(0, 10, vf)
    assert mo.electric_on(0, c, ts, ts, vf) and not mo.electric_on(0, c, ts + 1.0, ts, vf)
    # 出ていない線は太さ 0 で中心に縮む
    off = mo.electric_rows(mo.STYLE_REAL, 40, v, center, axes, (0, 2, 30), (0, 0, -1), count, n)
    assert all(w == 0 and p == center for p, _, w in off)
    # オブジェクトの箱に合わせる (薄い軸は広げる)
    c, half = el.fit_box((-1.0, -0.0, -2.0), (1.0, 0.0, 2.0))
    assert c == (0.0, 0.0, 0.0) and half == (1.0, 0.5, 2.0)


def test_presets_cover_every_effect_type():
    from dummy_effect_maker import catalog
    from dummy_effect_maker.tools import preset
    # 共通の「パラメータを読み込み」は、全部の種類を作り直せる
    assert all(callable(preset.rebuilder(s.effect_type)) for s in catalog.specs())
    assert preset.PresetSaveTool.hidden and preset.PresetLoadTool.hidden and not preset.PresetSaveTool.undoable


def test_leaves_fall_and_karman_vortex():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import leaves as lv
    assert lv.color_counts(10) == [4, 3, 3] and sum(lv.color_counts(151)) == 151
    # 舞い散る: 箱の中を落ち、地面 (箱の底) に着いたら平たく寝て、最後に縮んで消える
    v = lv.LeafFallTool.default_values()
    v.update(speed=0.1, speed_random=0.0, ground_frames=40.0, amount=1.0)
    am = [10.0, 0, 0, 0, 0, 5.0, 0, 0, 0, 0, 10.0, 0, 0, 2.5, 0, 1]   # 幅 10・高さ 5 (底が 0)
    ai = mo.inverse(am)
    seen_air = seen_ground = False
    for f in range(0, 200, 3):
        p, axes, sz, vis = mo.leaf_fall(0, f, v, am, ai, mo.Wind())
        assert -5.0 - 1e-6 <= p[0] <= 5.0 + 1e-6 and -1e-6 <= p[1] <= 5.0 + 1e-6
        assert abs(mo.dot(axes[0], axes[1])) < 1e-9 and abs(mo.mag(axes[2]) - 1) < 1e-9
        if p[1] < 1e-6:
            seen_ground = True
            assert mo.dot(axes[1], (0, 1, 0)) > 1 - 1e-9   # 寝ている
        elif sz > 0:
            seen_air = True
    assert seen_air and seen_ground
    # 渦: 列ごとに左右交互・回る向きも交互、下流へ流れ、弱まると地面に落ちる
    vv = lv.LeafVortexTool.default_values()
    vv.update(start_frame=1.0, shed_interval=10.0, life=60.0, ground_frames=20.0, wander=0.0, size_random=0.0)
    lanes = mo.vortex_lanes(vv)
    assert lanes == 8
    assert mo.leaf_vortex(3, 1 + 3 * 10 - 1, vv)[3] is False          # 自分の渦が出る前は見えない
    a, b = mo.leaf_vortex(0, 31, vv), mo.leaf_vortex(1, 41, vv)       # 渦 0 と渦 1 (どちらも出て 30 フレーム)
    assert a[0][2] * b[0][2] < 0                                        # 左右の列
    assert abs(a[0][2] - vv["row_width"] / 2) < vv["radius"] * 1.2
    th = []
    for j in (0, 1):   # 回る向き: 渦の中心から見た角度の進み方の符号が逆
        def ang(f, j=j):
            p = mo.leaf_vortex(j, f, vv)[0]
            k = j % lanes
            ta = f - 1 - k * 10.0
            cz = (1 if k % 2 == 0 else -1) * vv["row_width"] / 2
            return math.atan2(p[2] - cz, p[0] - vv["speed"] * ta)
        d = ang(1 + j * 10 + 12) - ang(1 + j * 10 + 10)
        th.append(math.atan2(math.sin(d), math.cos(d)))
    assert th[0] * th[1] < 0
    early, late = mo.leaf_vortex(0, 1 + 20, vv), mo.leaf_vortex(0, 1 + 59, vv)
    assert late[0][0] > early[0][0] and early[0][1] > 0                 # 下流へ・巻き上げる
    rest = mo.leaf_vortex(0, 1 + 70, vv)
    assert rest[0][1] == 0 and rest[3] and mo.dot(rest[1][1], (0, 1, 0)) > 1 - 1e-9   # 地面で寝る
    assert not mo.leaf_vortex(0, 1 + 79.9, vv)[3] or mo.leaf_vortex(0, 1 + 79.9, vv)[2] < 0.05
    # ロケータの空間 → ワールド
    m = [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 5, 0, 0, 1]   # Y まわりに 90 度・(5, 0, 0)
    p, axes = mo.leaf_place(m, (1.0, 0.0, 0.0), mo.leaf_axes(0, 0, 0))
    assert _close3(p, (5, 0, -1)) and _close3(axes[0], (0, 0, -1)) and _close3(axes[1], (0, 1, 0))


def test_foot_dust_drifts_low_around_the_emitter():
    from dummy_effect_maker import motion as mo
    from dummy_effect_maker.tools import foot_dust as fd
    v = fd.FootDustTool.default_values()
    v.update(start_frame=10.0, life=40.0, life_random=0.0, follow=0.0, ground=0.5, wobble=0.0)

    def still(t):   # 出る場所は (3, 2, -1) に止まっている (高さ 2 でも砂煙は地面 0.5 に出る)
        return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 3.0, 2.0, -1.0, 1]
    assert not any(mo.foot_dust(i, 9.9, v, still, mo.Wind())[2] for i in range(40))   # 開始前は出ない
    seen = 0
    for f in range(10, 300, 7):
        for i in range(40):
            p, sz, vis, a, cyc = mo.foot_dust(i, f, v, still, mo.Wind())
            assert 0 <= a < 40.0 + 1e-9
            if vis:
                seen += 1
                r = math.hypot(p[0] - 3.0, p[2] + 1.0)
                assert r <= v["radius"] + v["spread"] * a + 1e-9          # 足元の円から広がるだけ
                assert 0.5 <= p[1] <= 0.5 + sz * 0.3 + v["rise"] + 1e-9   # 地面すれすれ
    assert seen > 200
    # 出直し続ける (同じ塊が次の回で別の所に出る)
    p0 = mo.foot_dust(0, 100, v, still, mo.Wind())
    p1 = mo.foot_dust(0, 140, v, still, mo.Wind())
    assert p0[4][0] + 1 == p1[4][0] and abs(p0[3] - p1[3]) < 1e-9 and not _close3(p0[0], p1[0], 1e-6)
    # 付いて動く割合: 0 は湧いた所に残り、1 は出る場所に付いて動く
    def moving(t):
        return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.1 * t, 0.0, 0.0, 1]
    v.update(radius=0.0, spread=0.0, rise=0.0)
    p, sz, vis, a, cyc = mo.foot_dust(3, 200, v, moving, mo.Wind())
    assert abs(p[0] - 0.1 * (200 - a)) < 1e-9
    v["follow"] = 1.0
    assert abs(mo.foot_dust(3, 200, v, moving, mo.Wind())[0][0] - 20.0) < 1e-9
    # 量 0 なら出ない
    v["amount"] = 0.0
    assert not any(mo.foot_dust(i, 150, v, still, mo.Wind())[2] for i in range(40))
