# -*- coding: utf-8 -*-
"""Dummy Effect Maker の Blender 版。

Maya 版と同じパラメータの定義 (Param)・計算・書き出しの形式を使い、組み立てだけを Blender で行う。
Blender の Python からだけ読み込む (bpy を使う)。

    import dummy_effect_maker.blender as dfx
    dfx.register()      # 3D ビューのサイドバー「Dummy Effect」タブが出る
    dfx.unregister()
"""
from __future__ import absolute_import

_REGISTERED = [False]


def register():
    from . import runtime, ui
    if _REGISTERED[0]:
        unregister()
    runtime.register()
    ui.register()
    _REGISTERED[0] = True


def unregister():
    from . import runtime, ui
    ui.unregister()
    runtime.unregister()
    _REGISTERED[0] = False


def reload_and_register():
    """パッケージを丸ごと読み直して登録し直す (開発用)。"""
    import importlib
    import sys
    try:
        unregister()
    except Exception:
        pass
    name = "dummy_effect_maker"
    for mod_name in list(sys.modules):
        if mod_name == name or mod_name.startswith(name + "."):
            del sys.modules[mod_name]
    importlib.import_module(name + ".blender").register()
