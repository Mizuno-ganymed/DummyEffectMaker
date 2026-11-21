# -*- coding: utf-8 -*-
"""Maya なしで動かせる部分のテスト。  python -m pytest tests"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dummy_effect_maker.core import BaseTool, Param, ToolContext, execute  # noqa: E402
from dummy_effect_maker.core import host, registry  # noqa: E402
from dummy_effect_maker.tools import vulcan  # noqa: E402


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
    assert u"バルカン砲" in labels
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


def test_vulcan_fire_frames():
    assert vulcan.compute_fire_frames(1, 9, 2) == [1, 3, 5, 7, 9]
    assert vulcan.compute_fire_frames(1, 2, 0.5) == [1, 1.5, 2]
    assert vulcan.compute_fire_frames(10, 5, 1) == []
    with pytest.raises(ValueError):
        vulcan.compute_fire_frames(1, 10, 0)


def test_vulcan_bezier_passes_through_b():
    a, b, c = (0.0, 0.0, 0.0), (5.0, 3.0, 1.0), (10.0, 0.0, -2.0)
    p1 = vulcan.bezier_control_through(a, b, c)
    mid = tuple(0.25 * a[i] + 0.5 * p1[i] + 0.25 * c[i] for i in range(3))
    assert mid == pytest.approx(b)


def test_vulcan_params_are_consistent():
    tool = vulcan.VulcanTool()
    for p in tool.params:
        assert p.coerce(p.default) == p.default
    assert not tool.is_available("standalone")
