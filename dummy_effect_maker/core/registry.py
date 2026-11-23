# -*- coding: utf-8 -*-
"""tools パッケージからツールを自動収集する。

tools/ に .py を置き、BaseTool を継承したクラスを書くだけでウインドウに
ボタンが追加される。ファイル名が "_" で始まるモジュールは補助モジュール
として読み飛ばす。
"""
from __future__ import absolute_import

import importlib
import inspect
import pkgutil
import traceback

from .tool import BaseTool


def discover(package_name, on_error=None):
    """package_name 配下のツールクラスのインスタンス一覧を返す。

    読み込みに失敗したモジュールは on_error(module_name, traceback_text) に
    通知して読み飛ばす (1 つのツールの不具合でウインドウ全体を落とさない)。
    """
    package = importlib.import_module(package_name)
    tools = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        module_name = "%s.%s" % (package_name, info.name)
        try:
            module = importlib.import_module(module_name)
        except Exception:
            if on_error:
                on_error(module_name, traceback.format_exc())
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (issubclass(obj, BaseTool) and obj is not BaseTool
                    and obj.__module__ == module.__name__ and obj.label):
                tools.append(obj())
    # カテゴリの表示順は、そのカテゴリ内で最も小さい order で決まる
    tools.sort(key=lambda t: (t.order, t.label))
    return tools


def group_by_tab(tools):
    """[(tab, [tool, ...]), ...] をタブの登場順 (= 最も小さい order 順) で返す。"""
    groups = []
    index = {}
    for tool in tools:
        if tool.tab not in index:
            index[tool.tab] = len(groups)
            groups.append((tool.tab, []))
        groups[index[tool.tab]][1].append(tool)
    return groups


def group_by_category(tools):
    """[(category, [tool, ...]), ...] をカテゴリの登場順で返す。"""
    groups = []
    index = {}
    for tool in tools:
        if tool.category not in index:
            index[tool.category] = len(groups)
            groups.append((tool.category, []))
        groups[index[tool.category]][1].append(tool)
    return groups
