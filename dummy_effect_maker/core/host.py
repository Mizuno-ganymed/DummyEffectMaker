# -*- coding: utf-8 -*-
"""実行ホスト (Maya / Blender / スタンドアロン) の差分を吸収する層。

ツールや UI は Maya を直接触らず、ホスト固有の処理 (アンドゥ、親ウインドウ
など) はここを経由する。ほかのソフトに広げる時はここに実装を足していく。

ツールは BaseTool.hosts で動くホストを決める (既定は Maya だけ)。
Blender 版のツールは hosts = ("blender",) にする。
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


class BlenderHost(object):
    """Blender (bpy)。画面は Blender のパネルで作るので、親ウインドウは無い。"""
    name = "blender"

    def get_main_window(self):
        return None

    @contextlib.contextmanager
    def undo_chunk(self, name):
        # Blender は操作 (オペレーター) ごとにアンドゥが積まれる。
        # まとめて 1 回で戻せるよう、終わった所で 1 つの区切りを置く
        yield
        import bpy
        try:
            bpy.ops.ed.undo_push(message=name)
        except RuntimeError:
            pass  # バックグラウンド実行などで使えない時


def detect():
    """現在のプロセスに合ったホストを返す。"""
    try:
        import maya.cmds  # noqa: F401
        import maya.OpenMayaUI  # noqa: F401
        return MayaHost()
    except ImportError:
        pass
    try:
        import bpy  # noqa: F401
        return BlenderHost()
    except ImportError:
        return StandaloneHost()
