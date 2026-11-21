# -*- coding: utf-8 -*-
"""Param 定義からオプションダイアログを自動生成する。"""
from __future__ import absolute_import

from .qt import QtCore, QtGui, QtWidgets


class ColorButton(QtWidgets.QPushButton):
    def __init__(self, rgb, parent=None):
        super(ColorButton, self).__init__(parent)
        self.setFixedWidth(60)
        self.clicked.connect(self._pick)
        self.set_rgb(rgb)

    def set_rgb(self, rgb):
        self._rgb = tuple(rgb)
        color = QtGui.QColor.fromRgbF(*self._rgb)
        self.setStyleSheet("background-color: %s;" % color.name())

    def rgb(self):
        return self._rgb

    def _pick(self):
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor.fromRgbF(*self._rgb), self)
        if color.isValid():
            self.set_rgb((color.redF(), color.greenF(), color.blueF()))


def _make_editor(param, value):
    """(widget, getter, setter) を返す。"""
    kind = param.kind
    if kind == "bool":
        w = QtWidgets.QCheckBox()
        return w, w.isChecked, w.setChecked
    if kind in ("int", "float"):
        if kind == "int":
            w = QtWidgets.QSpinBox()
            lo, hi = -10 ** 9, 10 ** 9
        else:
            w = QtWidgets.QDoubleSpinBox()
            w.setDecimals(param.decimals)
            lo, hi = -1e9, 1e9
        w.setRange(param.minimum if param.minimum is not None else lo,
                   param.maximum if param.maximum is not None else hi)
        if param.step is not None:
            w.setSingleStep(param.step)
        return w, w.value, w.setValue
    if kind == "str":
        w = QtWidgets.QLineEdit()
        return w, w.text, w.setText
    if kind == "choice":
        w = QtWidgets.QComboBox()
        w.addItems(param.choices)
        return (w, w.currentText,
                lambda v: w.setCurrentIndex(max(0, w.findText(v))))
    if kind == "color":
        w = ColorButton(value)
        return w, w.rgb, w.set_rgb
    raise ValueError(kind)


class ParamDialog(QtWidgets.QDialog):
    """ツールのオプション設定ダイアログ。

    「適用」でツールに値を保存して実行、「保存」で保存のみ。
    """

    APPLY = 2

    def __init__(self, tool, parent=None):
        super(ParamDialog, self).__init__(parent)
        self.tool = tool
        self.setWindowTitle(u"%s オプション" % tool.label)
        self._editors = {}

        form = QtWidgets.QFormLayout()
        for param in tool.params:
            widget, getter, setter = _make_editor(param, tool.values[param.name])
            setter(tool.values[param.name])
            if param.tooltip:
                widget.setToolTip(param.tooltip)
            self._editors[param.name] = (getter, setter)
            form.addRow(param.label, widget)

        if tool.tooltip:
            desc = QtWidgets.QLabel(tool.tooltip)
            desc.setWordWrap(True)
        else:
            desc = None

        apply_btn = QtWidgets.QPushButton(u"適用")
        save_btn = QtWidgets.QPushButton(u"保存")
        reset_btn = QtWidgets.QPushButton(u"リセット")
        close_btn = QtWidgets.QPushButton(u"閉じる")
        apply_btn.clicked.connect(self._on_apply)
        save_btn.clicked.connect(self._on_save)
        reset_btn.clicked.connect(self._on_reset)
        close_btn.clicked.connect(self.reject)

        buttons = QtWidgets.QHBoxLayout()
        for b in (apply_btn, save_btn, reset_btn, close_btn):
            buttons.addWidget(b)

        layout = QtWidgets.QVBoxLayout(self)
        if desc:
            layout.addWidget(desc)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.setMinimumWidth(360)

    def values(self):
        return dict((name, getter()) for name, (getter, _) in self._editors.items())

    def _store(self):
        self.tool.set_values(self.values())

    def _on_apply(self):
        self._store()
        self.done(self.APPLY)

    def _on_save(self):
        self._store()
        self.accept()

    def _on_reset(self):
        defaults = self.tool.default_values()
        for name, (_, setter) in self._editors.items():
            setter(defaults[name])
