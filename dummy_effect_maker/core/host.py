# -*- coding: utf-8 -*-
"""実行ホスト (Maya / スタンドアロン) の差分を吸収する層。

ツールや UI は Maya を直接触らず、ホスト固有の処理 (アンドゥ、親ウインドウ
など) はここを経由する。スタンドアロン化の際はここに実装を足していく。
"""
from __future__ import absolute_import

import contextlib


class StandaloneHost(object):
    name = "standalone"

    def get_main_window(self):
        return None

    @contextlib.contextmanager
    def undo_chunk(self, name):
        yield


class MayaHost(object):
    name = "maya"

    def get_main_window(self):
        import maya.OpenMayaUI as omui
        from ..ui.qt import QtWidgets, wrapInstance

        ptr = omui.MQtUtil.mainWindow()
        if ptr is None:  # mayapy (バッチ) など UI が無い場合
            return None
        return wrapInstance(int(ptr), QtWidgets.QWidget)

    @contextlib.contextmanager
    def undo_chunk(self, name):
        from maya import cmds

        cmds.undoInfo(openChunk=True, chunkName=name)
        try:
            yield
        finally:
            cmds.undoInfo(closeChunk=True)


def detect():
    """現在のプロセスに合ったホストを返す。"""
    try:
        import maya.cmds  # noqa: F401
        import maya.OpenMayaUI  # noqa: F401
    except ImportError:
        return StandaloneHost()
    return MayaHost()
