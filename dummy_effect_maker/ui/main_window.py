# -*- coding: utf-8 -*-
"""ツールを並べたランチャーウインドウ。"""
from __future__ import absolute_import

import traceback

from ..core import ToolContext, ToolError, execute
from ..core import registry
from .attribute_editor import AttributeEditor
from .param_dialog import ParamDialog
from .qt import QtCore, QtWidgets
from .settings_page import SettingsDialog

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
        self.resize(640, 520)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)

        # 上部のタブ: ツールの tab ごと (ツール / カメラ ...)
        self.tabs = QtWidgets.QTabWidget()
        self.tool_areas = {}  # タブ名 → ボタンを並べるウィジェット
        self.menus = {}       # メニュー名 → QMenu (シーン / 開発)
        # 環境設定はシーン メニューから開く小さなウインドウ
        self.settings_dialog = SettingsDialog(log=self.log, parent=self)
        self.settings_page = self.settings_dialog.page

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.log_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 140])

        # 右側: 専用のアトリビュートエディタ (Maya の中だけ。選んだエフェクトのパラメータ)
        self.attribute_editor = AttributeEditor(self._attribute_backend(), run_update=self.run_update_for,
                                                parent=self)
        self.side_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.side_splitter.addWidget(splitter)
        self.side_splitter.addWidget(self.attribute_editor)
        self.side_splitter.setStretchFactor(0, 1)
        self.side_splitter.setStretchFactor(1, 1)
        self.side_splitter.setSizes([320, 320])
        self.setCentralWidget(self.side_splitter)
        if self.attribute_editor.backend is None:
            self.attribute_editor.show_empty(u"アトリビュートエディタは Maya の中でだけ使えます")

        self.tools = []
        self.populate()
        self.attribute_editor.start()

    # ------------------------------------------------------------------ UI
    def _build_menus(self):
        """上部のメニュー: menu を指定したツール (シーン: ベイク・全削除) + 環境設定 + 開発。"""
        for name, tools in registry.group_by_menu(self.tools):
            menu = self._menu(name)
            for category, cat_tools in registry.group_by_category(tools):
                if not menu.isEmpty():
                    menu.addSeparator()
                for tool in cat_tools:
                    self._add_tool_actions(menu, tool)
        scene = self._menu(u"シーン")
        if not scene.isEmpty():
            scene.addSeparator()
        scene.addAction(u"環境設定 (プリセットの保存先)...", self.open_settings)
        view = self._menu(u"表示")
        self.attribute_editor_action = view.addAction(u"アトリビュートエディタ")
        self.attribute_editor_action.setCheckable(True)
        self.attribute_editor_action.setChecked(True)
        self.attribute_editor_action.toggled.connect(self.attribute_editor.setVisible)
        dev_menu = self._menu(u"開発")
        dev_menu.addAction(u"ツールを再読み込み", self._on_reload)
        dev_menu.addAction(u"ログをクリア", lambda: self.log_view.clear())

    def _menu(self, name):
        if name not in self.menus:
            self.menus[name] = self.menuBar().addMenu(name)
        return self.menus[name]

    def _add_tool_actions(self, menu, tool):
        available = tool.is_available(self.host.name)
        action = menu.addAction(tool.label, lambda: self.run_tool(tool))
        actions = [action]
        if tool.params:
            actions.append(menu.addAction(u"%s (オプション)..." % tool.label,
                                          lambda: self.open_options(tool)))
        for a in actions:
            a.setToolTip(tool.tooltip)
            a.setStatusTip(tool.tooltip.split("\n")[0])
            a.setEnabled(available)
        menu.setToolTipsVisible(True)

    def open_settings(self):
        self.settings_dialog.open_page()

    def populate(self):
        self.tools = registry.discover(TOOLS_PACKAGE, on_error=self._on_load_error)
        self._build_menus()
        for tab, tab_tools in registry.group_by_tab(self.tools):
            area = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(area)
            for category, tools in registry.group_by_category(tab_tools):
                box = QtWidgets.QGroupBox(category)
                box_layout = QtWidgets.QVBoxLayout(box)
                for tool in tools:
                    box_layout.addLayout(self._make_tool_row(tool))
                layout.addWidget(box)
            layout.addStretch(1)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(area)
            self.tabs.addTab(scroll, tab)
            self.tool_areas[tab] = area
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

    def _attribute_backend(self):
        if self.host.name != "maya":
            return None
        from ..tools._attr_backend import MayaAttributeBackend
        return MayaAttributeBackend()

    def run_update_for(self, effect_type, root):
        """アトリビュートエディタの「更新」: その種類の「〜を更新」ツールを実行する (ルートは選択済み)。"""
        from ..tools._attr_backend import update_tool_name
        name = update_tool_name(effect_type)
        for tool in self.tools:
            if type(tool).__name__ == name and tool.is_available(self.host.name):
                self.run_tool(tool)
                return True
        self.log(u"  ✖ %s を更新するツールが見つかりません" % effect_type)
        return False

    def closeEvent(self, event):
        self.attribute_editor.shutdown()
        super(MainWindow, self).closeEvent(event)

    def _on_load_error(self, module_name, tb):
        self.log(u"ツール %s の読み込みに失敗しました\n%s" % (module_name, tb))

    def _on_reload(self):
        # パッケージごと読み直して新しいウインドウを開く (開発用)
        from .. import reload_and_show
        QtCore.QTimer.singleShot(0, reload_and_show)
