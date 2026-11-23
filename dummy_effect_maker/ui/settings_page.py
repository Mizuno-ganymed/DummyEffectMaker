# -*- coding: utf-8 -*-
"""ランチャーの「環境設定」タブ。値は core.settings に即保存する。"""
from __future__ import absolute_import

import os

from ..core import settings
from .qt import QtWidgets

DEFAULT_PRESET_HINT = u"空欄: マイドキュメント/maya/DummyEffectMaker/presets/<種類> (既定)"


class SettingsPage(QtWidgets.QWidget):
    def __init__(self, log=None, parent=None):
        super(SettingsPage, self).__init__(parent)
        self._log = log or (lambda msg: None)

        self.preset_dir_edit = QtWidgets.QLineEdit()
        self.preset_dir_edit.setPlaceholderText(DEFAULT_PRESET_HINT)
        self.preset_dir_edit.setToolTip(
            u"「パラメータを保存 / 読み込み」のダイアログで最初に開くフォルダ")
        self.preset_dir_edit.editingFinished.connect(self._on_edited)
        browse_btn = QtWidgets.QPushButton(u"参照...")
        browse_btn.clicked.connect(self._on_browse)
        reset_btn = QtWidgets.QPushButton(u"既定に戻す")
        reset_btn.clicked.connect(self._on_reset)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.preset_dir_edit, 1)
        row.addWidget(browse_btn)
        row.addWidget(reset_btn)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)

        box = QtWidgets.QGroupBox(u"プリセット (パラメータの保存 / 読み込み)")
        form = QtWidgets.QFormLayout(box)
        form.addRow(u"保存先フォルダ", row)
        form.addRow("", self.status_label)

        file_label = QtWidgets.QLabel(u"設定ファイル: %s" % settings.settings_path())
        file_label.setWordWrap(True)
        file_label.setEnabled(False)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(box)
        layout.addStretch(1)
        layout.addWidget(file_label)

        self.refresh()

    def refresh(self):
        self.preset_dir_edit.setText(settings.get("preset_dir"))
        self._update_status()

    # ------------------------------------------------------------------
    def _store(self, value):
        value = value.strip()
        if value == settings.get("preset_dir"):
            self._update_status()
            return
        settings.set("preset_dir", value)
        self.preset_dir_edit.setText(value)
        self._update_status()
        self._log(u"環境設定: プリセットの保存先 = %s" % (value or u"(既定)"))

    def _update_status(self):
        value = settings.get("preset_dir")
        if not value:
            self.status_label.setText(u"既定のフォルダを使います")
        elif os.path.isdir(value):
            self.status_label.setText(u"このフォルダを使います")
        else:
            self.status_label.setText(u"フォルダはまだありません (最初の保存時に作成します)")

    def _on_edited(self):
        self._store(self.preset_dir_edit.text())

    def _on_browse(self):
        start = self.preset_dir_edit.text() or os.path.expanduser("~")
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, u"プリセットの保存先", start)
        if path:
            self._store(os.path.normpath(path))

    def _on_reset(self):
        self._store("")
