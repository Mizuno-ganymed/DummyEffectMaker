# -*- coding: utf-8 -*-
"""ツールを並べたランチャーウインドウ。"""
from __future__ import absolute_import

import traceback

from ..core import ToolContext, ToolError, execute
from ..core import registry
from .param_dialog import ParamDialog
from .qt import QtCore, QtWidgets

WINDOW_OBJECT_NAME = "DummyEffectMakerMainWindow"
WINDOW_TITLE = "Dummy Effect Maker"
TOOLS_PACKAGE = "dummy_effect_maker.tools"


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, host, parent=None):
        super(MainWindow, self).__init__(parent)
        self.host = host
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        # Maya のメインウインドウを親にしても独立したウインドウとして出す
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(320, 480)

        self._build_menu()

        self.tool_area = QtWidgets.QWidget()
        self.tool_layout = QtWidgets.QVBoxLayout(self.tool_area)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.tool_area)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(scroll)
        splitter.addWidget(self.log_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 140])
        self.setCentralWidget(splitter)

        self.tools = []
        self.populate()

    # ------------------------------------------------------------------ UI
    def _build_menu(self):
        dev_menu = self.menuBar().addMenu(u"開発")
        dev_menu.addAction(u"ツールを再読み込み", self._on_reload)
        dev_menu.addAction(u"ログをクリア", lambda: self.log_view.clear())

    def populate(self):
        self.tools = registry.discover(TOOLS_PACKAGE, on_error=self._on_load_error)
        for category, tools in registry.group_by_category(self.tools):
            box = QtWidgets.QGroupBox(category)
            box_layout = QtWidgets.QVBoxLayout(box)
            for tool in tools:
                box_layout.addLayout(self._make_tool_row(tool))
            self.tool_layout.addWidget(box)
        self.tool_layout.addStretch(1)
        self.log(u"%d 個のツールを読み込みました (host: %s)"
                 % (len(self.tools), self.host.name))

    def _make_tool_row(self, tool):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        button = QtWidgets.QPushButton(tool.label)
        button.setToolTip(tool.tooltip)
        button.clicked.connect(lambda: self.run_tool(tool))
        row.addWidget(button, 1)

        option = None
        if tool.params:
            option = QtWidgets.QPushButton(u"□")
            option.setFixedWidth(28)
            option.setToolTip(u"オプション")
            option.clicked.connect(lambda: self.open_options(tool))
            row.addWidget(option)

        if not tool.is_available(self.host.name):
            for w in (button, option):
                if w is not None:
                    w.setEnabled(False)
            button.setToolTip(u"%s\n(この環境では使用できません: %s 専用)"
                              % (tool.tooltip, "/".join(tool.hosts)))
        return row

    # ------------------------------------------------------------- actions
    def log(self, message):
        self.log_view.appendPlainText(message)
        self.log_view.ensureCursorVisible()

    def run_tool(self, tool):
        ctx = ToolContext(self.host, log=self.log, parent_widget=self)
        self.log(u"▶ %s" % tool.label)
        try:
            execute(tool, ctx)
        except ToolError as e:
            self.log(u"  ✖ %s" % e)
            QtWidgets.QMessageBox.warning(self, tool.label, str(e))
        except Exception:
            self.log(traceback.format_exc())
            self.log(u"  ✖ エラーが発生しました (詳細は上記)")

    def open_options(self, tool):
        dialog = ParamDialog(tool, self)
        # PySide6 では exec_ が非推奨のため exec を優先
        run_dialog = getattr(dialog, "exec", None) or dialog.exec_
        if run_dialog() == ParamDialog.APPLY:
            self.run_tool(tool)

    def _on_load_error(self, module_name, tb):
        self.log(u"ツール %s の読み込みに失敗しました\n%s" % (module_name, tb))

    def _on_reload(self):
        # パッケージごと読み直して新しいウインドウを開く (開発用)
        from .. import reload_and_show
        QtCore.QTimer.singleShot(0, reload_and_show)
