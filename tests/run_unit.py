# -*- coding: utf-8 -*-
"""pytest なしで tests/test_core.py を動かす (pytest.raises / approx だけ代用)。Maya も Blender も要らない。

    python tests/run_unit.py            全部
    python tests/run_unit.py <リポジトリ> リポジトリの場所を指定する時 (既定はこのファイルの 1 つ上)
pytest があれば `python -m pytest tests/test_core.py` でもよい。
"""
import contextlib
import math
import os
import sys
import traceback
import types

repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo)
sys.path.insert(0, os.path.join(repo, "tests"))

fake = types.ModuleType("pytest")


@contextlib.contextmanager
def raises(exc):
    try:
        yield
    except exc:
        return
    raise AssertionError("did not raise %s" % exc)


class approx(object):
    def __init__(self, v):
        self.v = v

    def __eq__(self, other):
        return all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-12) for a, b in zip(self.v, other))


fake.raises = raises
fake.approx = approx
sys.modules["pytest"] = fake

import test_core as m
f = 0
for n, fn in sorted(vars(m).items()):
    if n.startswith("test_") and callable(fn):
        try:
            fn(); print("PASS", n)
        except Exception:
            f += 1; print("FAIL", n); traceback.print_exc()
print("failed:", f)
sys.exit(1 if f else 0)
