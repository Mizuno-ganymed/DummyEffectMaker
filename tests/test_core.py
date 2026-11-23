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
from dummy_effect_maker.tools import (camera_shake, energy_ball, focus_lines,  # noqa: E402
                                      ghost_blur, hand_shake, homing, shockwave, speed_lines,
                                      vulcan, weather, weather_layers, wind)
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
    assert [tab for tab, _ in tabs] == [u"ツール", u"カメラ", u"エネルギー弾", u"衝撃波", u"お化けブラー",
                                        u"天候"]
    assert dict(tabs)[u"天候"] == [
        u"風", u"風を更新", u"雨 (線と跳ね)", u"雨 (遠景の筒)", u"雪 (粒)", u"雪 (遠景の筒)",
        u"土煙 (スプライト)", u"土煙 (もこもこ)", u"火花 (線)", u"火花 (尾を引く)",
        u"霧 (霧の板)", u"霧 (遠景の重ね板)", u"地霧 (重ね板)", u"地霧 (這う霧)",
        u"天候を更新", u"天候のパラメータを保存", u"天候のパラメータを読み込み"]
    weather_tools = [t for t in tools if t.tab == u"天候"]
    assert [c for c, _ in registry.group_by_category(weather_tools)] == [
        u"風", u"雨", u"雪", u"土煙", u"火花", u"霧", u"地霧", u"調整"]
    assert dict(tabs)[u"お化けブラー"] == [
        u"残像", u"スミア (引き伸ばし)", u"軌跡", u"お化けブラーを更新",
        u"お化けブラーのパラメータを保存", u"お化けブラーのパラメータを読み込み"]
    assert dict(tabs)[u"衝撃波"] == [u"衝撃波", u"衝撃波を更新", u"衝撃波のパラメータを保存",
                                   u"衝撃波のパラメータを読み込み"]
    assert dict(tabs)[u"エネルギー弾"] == [u"エネルギー弾", u"エネルギー弾を更新"]
    assert dict(tabs)[u"カメラ"] == [
        u"集中線", u"集中線を更新", u"集中線のパラメータを保存", u"集中線のパラメータを読み込み",
        u"スピードライン", u"スピードラインを更新", u"スピードラインのパラメータを保存",
        u"スピードラインのパラメータを読み込み",
        u"画ぶれ", u"画ぶれを更新", u"画ぶれのパラメータを保存", u"画ぶれのパラメータを読み込み",
        u"画ぶれを外す",
        u"手ブレ", u"手ブレを更新", u"手ブレのパラメータを保存", u"手ブレのパラメータを読み込み",
        u"手ブレを外す"]
    # ベイクと全削除はタブではなく上部の「シーン」メニュー
    menus = [(m, [t.label for t in ts]) for m, ts in registry.group_by_menu(tools)]
    assert menus == [(u"シーン", [u"ベイク", u"ダミーエフェクトを全削除"])]
    assert u"ベイク" not in dict(tabs)[u"ツール"]
    assert dict(tabs)[u"ツール"] == [
        u"追尾弾", u"追尾弾を更新", u"追尾弾のパラメータを保存", u"追尾弾のパラメータを読み込み",
        u"バルカン砲", u"バルカン砲を更新", u"バルカン砲のパラメータを保存", u"バルカン砲のパラメータを読み込み"]


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
    assert len(wx.KINDS) == 13
    for kind in wx.KINDS.values():
        names = set(p.name for p in kind.tool.params)
        stored = set(kind.live) | set(kind.rebuild)
        assert not set(kind.live) & set(kind.rebuild)
        assert stored <= names
        # 保存しないのは作成時だけの範囲の箱の設定
        assert names - stored <= set(("area_width", "area_height", "follow_camera"))
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


def test_file_param_and_weather_texture_option():
    p = Param("t", "t", "", kind="file")
    assert p.coerce(None) == "" and p.coerce("C:/a.png") == "C:/a.png"
    with_texture = [k.effect_type for k in wx.KINDS.values()
                    if "texture" in [p.name for p in k.tool.params]]
    assert sorted(with_texture) == sorted(["dust", "rain_far", "snow_far", "fog", "ground_fog",
                                           "ground_crawl"])
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
