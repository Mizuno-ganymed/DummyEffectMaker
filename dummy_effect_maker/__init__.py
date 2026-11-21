# -*- coding: utf-8 -*-
"""Dummy Effect Maker

Maya 上でダミーエフェクトを作る小ツール群と、それを起動するランチャー。

Maya のスクリプトエディタ (Python) から:

    import dummy_effect_maker
    dummy_effect_maker.show()
"""
from __future__ import absolute_import

import importlib
import sys

__version__ = "0.1.0"

_window = None


def show():
    """ランチャーウインドウを開く (既に開いていれば閉じて開き直す)。"""
    global _window
    from .core import host as host_mod
    from .ui.main_window import MainWindow, WINDOW_OBJECT_NAME
    from .ui.qt import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError(
            "QApplication がありません。スタンドアロンでは run_standalone.py を使ってください")

    _close_existing(WINDOW_OBJECT_NAME)
    host = host_mod.detect()
    _window = MainWindow(host, parent=host.get_main_window())
    _window.show()
    return _window


def _close_existing(object_name):
    from .ui.qt import QtWidgets

    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget.objectName() == object_name:
            widget.close()
            widget.deleteLater()


def reload_and_show():
    """パッケージを丸ごと読み直してウインドウを開き直す (開発用)。"""
    name = __name__
    for mod_name in list(sys.modules):
        if mod_name == name or mod_name.startswith(name + "."):
            del sys.modules[mod_name]
    return importlib.import_module(name).show()
