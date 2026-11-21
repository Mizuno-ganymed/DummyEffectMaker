# -*- coding: utf-8 -*-
"""PySide6 (Maya 2025 以降 / スタンドアロン) と PySide2 (Maya 2022-2024) の差を吸収する。"""
from __future__ import absolute_import

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    from shiboken6 import wrapInstance  # noqa: F401
    QT_BINDING = "PySide6"
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    from shiboken2 import wrapInstance  # noqa: F401
    QT_BINDING = "PySide2"
