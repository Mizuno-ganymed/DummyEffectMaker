# -*- coding: utf-8 -*-
"""ランチャーの右側の、Dummy Effect Maker 専用のアトリビュートエディタ。

選んでいるエフェクト (ルートか、その中の物) のパラメータを、日本語の名前・種類に合った入力欄で並べる。
    - すぐ反映の物は、変えるとその場でアトリビュートが変わる (1 回のアンドゥで戻る)
    - 「(更新)」の物は、変えたら下の「更新」ボタンで作り直す
    - 右の ◆ でキーを打つ / 消す (色: 赤 = このフレームにキー、桃 = キーあり、黄 = エクスプレッションなどで決まる)
    - 動かす物 (範囲の箱・出る場所・爆心など) とつながった物 (カメラ・風) は、ボタンで選べる
    - 上の「保存」「読み込み」で、パラメータをプリセット (.json) に保存・読み込みする (全部の種類で共通)

Maya とのやり取りは backend (tools/_attr_backend.py の MayaAttributeBackend) に任せる。
"""
from __future__ import absolute_import

from .param_dialog import _make_editor
from .qt import QtCore, QtWidgets

KEY_COLORS = {"none": None, "key": "#cd2729", "animated": "#dd727a", "driven": "#e8e29a"}
KEY_TIPS = {"none": u"キーを打つ", "key": u"このフレームのキーを消す", "animated": u"キーを打つ (キーあり)",
            "driven": u"エクスプレッションなどで決まっているので変えられません"}
POLL_MS = 700        # チャンネルボックスなどで変えた値を拾う間隔
COMMIT_MS = 150      # 数値をドラッグ中に、書き込みをまとめる間隔
EMPTY_TEXT = u"エフェクト (ルートか、その中の物) を選ぶと、ここにパラメータが出ます"
PANEL_NAME = "dfxAttributePanel"
LOCK_YELLOW = "#f2c200"
LOCK_TEXT = u"🔓 ロック"
LOCKED_TEXT = u"🔒 ロック中"


class AttributeEditor(QtWidgets.QWidget):
    """backend: MayaAttributeBackend など。

    run_update(effect_type, root): 「更新」ボタンで呼ぶ。
    run_preset(action): 「保存」(action = "save") /「読み込み」("load") で呼ぶ (対象のルートは選択済み)。
    """

    def __init__(self, backend, run_update=None, run_preset=None, parent=None):
        super(AttributeEditor, self).__init__(parent)
        self.backend = backend
        self.run_update = run_update
        self.run_preset = run_preset
        self.root = None
        self.effect_type = None
        self.spec = None
        self.rows = {}          # 名前 → (param, getter, setter, widget, key button)
        self._baked = False
        self._updating = False
        self._timers = {}
        self._pending_rebuild = False
        self.locked_root = None   # ロック中に表示し続けるルート

        self.lock_button = QtWidgets.QPushButton(LOCK_TEXT)
        self.lock_button.setCheckable(True)
        self.lock_button.setToolTip(u"ロックすると、選択を外したり変えたりしても、このエフェクトを表示し続けます")
        self.lock_button.toggled.connect(self.set_locked)
        self.save_button = QtWidgets.QPushButton(u"保存")
        self.save_button.setToolTip(u"このエフェクトのパラメータを、プリセットファイル (.json) に保存します")
        self.save_button.clicked.connect(self._on_save)
        self.load_button = QtWidgets.QPushButton(u"読み込み")
        self.load_button.setToolTip(
            u"プリセットファイル (.json) のパラメータを設定して作り直します (同じ種類のプリセットだけ)。\n"
            u"同じ種類のエフェクトを複数選んでいれば、全部に読み込みます (ロック中は表示中の 1 つだけ)。\n"
            u"キーを打ってある項目は変えません")
        self.load_button.clicked.connect(self._on_load)
        self.header = QtWidgets.QLabel()
        self.header.setWordWrap(True)
        font = self.header.font()
        font.setBold(True)
        self.header.setFont(font)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #d0a040;")

        self.form_host = QtWidgets.QWidget()
        self.form = QtWidgets.QFormLayout(self.form_host)
        self.form.setLabelAlignment(QtCore.Qt.AlignRight)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_host)

        self.links_box = QtWidgets.QGroupBox(u"動かす物・つながり (クリックで選択)")
        self.links_layout = QtWidgets.QVBoxLayout(self.links_box)

        self.update_button = QtWidgets.QPushButton(u"更新")
        self.update_button.setToolTip(u"「(更新)」の付いた項目を変えた時に、作り直して反映します")
        self.update_button.clicked.connect(self._on_update)
        self.root_button = QtWidgets.QPushButton(u"ルートを選択")
        self.root_button.clicked.connect(lambda: self.root and self.backend.select([self.root]))
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.root_button)
        buttons.addWidget(self.update_button, 1)

        # 中身を枠で囲む (ロック中は黄色い枠)
        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName(PANEL_NAME)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.header, 1)
        top.addWidget(self.save_button)
        top.addWidget(self.load_button)
        top.addWidget(self.lock_button)
        inner = QtWidgets.QVBoxLayout(self.panel)
        inner.setContentsMargins(6, 6, 6, 6)
        inner.addLayout(top)
        inner.addWidget(self.note)
        inner.addWidget(scroll, 1)
        inner.addWidget(self.links_box)
        inner.addLayout(buttons)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.panel)
        self._apply_lock_style()

        self.poll = QtCore.QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self.refresh)
        self.show_empty()

    # ------------------------------------------------------------ 開始・終了
    def start(self):
        if self.backend is not None:
            self.backend.watch(self._on_event)
        self.poll.start()
        self.refresh()

    def shutdown(self):
        self.poll.stop()
        if self.backend is not None:
            self.backend.unwatch()

    def _on_event(self, *args):
        QtCore.QTimer.singleShot(0, self.refresh)

    # ------------------------------------------------------------ ロック
    def set_locked(self, locked):
        """ロックする (今表示しているエフェクトを、選択に関係なく表示し続ける) / 外す。"""
        locked = bool(locked) and self.root is not None
        self.locked_root = self.root if locked else None
        if self.lock_button.isChecked() != locked:
            self.lock_button.blockSignals(True)
            self.lock_button.setChecked(locked)
            self.lock_button.blockSignals(False)
        self._apply_lock_style()
        if not locked:
            self.refresh()
        else:
            self._set_note(self._baked, 1)

    def is_locked(self):
        return self.locked_root is not None

    def _apply_lock_style(self):
        if self.is_locked():
            self.panel.setStyleSheet("QFrame#%s { border: 3px solid %s; border-radius: 4px; }" % (PANEL_NAME, LOCK_YELLOW))
            self.lock_button.setText(LOCKED_TEXT)
            self.lock_button.setStyleSheet(
                "QPushButton { background-color: %s; color: #000000; font-weight: bold; padding: 3px 10px;"
                " border: 1px solid #806000; border-radius: 3px; }" % LOCK_YELLOW)
        else:
            self.panel.setStyleSheet("QFrame#%s { border: 1px solid transparent; }" % PANEL_NAME)
            self.lock_button.setText(LOCK_TEXT)
            self.lock_button.setStyleSheet("")
        self.lock_button.setEnabled(self.root is not None or self.is_locked())
        self._apply_preset_buttons()

    def _apply_preset_buttons(self):
        can = self.root is not None and self.spec is not None and self.run_preset is not None
        self.save_button.setEnabled(can)
        self.load_button.setEnabled(can and not self._baked)

    # ------------------------------------------------------------ 表示
    def show_empty(self, text=EMPTY_TEXT):
        self.root, self.effect_type, self.spec = None, None, None
        self._clear_form()
        self.header.setText(u"")
        self.note.setText(text)
        self.links_box.setVisible(False)
        self.update_button.setEnabled(False)
        self.root_button.setEnabled(False)
        if hasattr(self, "panel"):
            self._apply_lock_style()

    def _clear_form(self):
        self.rows = {}
        while self.form.rowCount():
            self.form.removeRow(0)

    def refresh(self):
        """選択が変わったら作り直し、同じなら値とキーの色だけ直す。"""
        if self.backend is None:
            return
        if self.is_locked() and not self.backend.exists(self.locked_root):
            self.locked_root = None   # ロックしていたエフェクトが消された
            self.lock_button.blockSignals(True)
            self.lock_button.setChecked(False)
            self.lock_button.blockSignals(False)
            self.root = None
            self._apply_lock_style()
        try:
            roots = [self.locked_root] if self.is_locked() else self.backend.selected_roots()
        except Exception:
            return
        if not roots:
            if self.root is not None or not self.note.text():
                self.show_empty()
            return
        root = roots[0]
        effect_type, spec, baked = self.backend.describe(root)
        if root != self.root or effect_type != self.effect_type or baked != self._baked:
            self._build(root, effect_type, spec, baked, len(roots))
        else:
            self._sync_values()
        self._set_note(baked, len(roots))

    def _set_note(self, baked, count):
        notes = []
        if self.is_locked():
            notes.append(u"ロック中: 選択を変えても、このエフェクトを表示します")
        if count > 1:
            notes.append(u"%d 個選んでいます (最初の 1 つを表示)" % count)
        if baked:
            notes.append(u"ベイク済みのため、パラメータを変えても動きは変わりません")
        if self._pending_rebuild:
            notes.append(u"「(更新)」の項目を変えました。「更新」で反映します")
        self.note.setText(u"\n".join(notes))

    def _build(self, root, effect_type, spec, baked, count):
        self._clear_form()
        self.root, self.effect_type, self.spec = root, effect_type, spec
        self._pending_rebuild = False
        self._baked = baked
        label = spec.label if spec else effect_type
        self.header.setText(u"%s  —  %s" % (self.backend.short(root), label))
        self.root_button.setEnabled(True)
        self._apply_lock_style()
        if spec is None:
            self.note.setText(u"この種類のパラメータは表示できません")
            return
        values = self.backend.values(root, spec.params)
        self._updating = True
        try:
            for p in spec.params:
                if not self.backend.has(root, p):
                    continue
                self._add_row(p, values[p.name], p.name in spec.live, baked)
        finally:
            self._updating = False
        self.update_button.setEnabled(bool(spec.rebuild) and self.run_update is not None and not baked)
        self.update_button.setText(u"%s を更新" % label if spec.rebuild else u"更新 (すぐ反映の項目だけです)")
        self._build_links(root)
        self._sync_keys()

    def _add_row(self, p, value, live, baked):
        widget, getter, setter = _make_editor(p, value)
        setter(value)
        tip = p.tooltip or p.label
        if not live:
            tip += u"\n(「更新」で反映)"
        widget.setToolTip(tip)
        widget.setEnabled(not baked)
        key = None
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        row.addWidget(widget, 1)
        if live and p.kind not in ("str", "file"):
            key = QtWidgets.QToolButton()
            key.setText(u"◆")
            key.setFixedWidth(22)
            key.setEnabled(not baked)
            key.clicked.connect(lambda _=False, p=p: self._on_key(p))
            row.addWidget(key)
        else:
            spacer = QtWidgets.QWidget()
            spacer.setFixedWidth(22)
            row.addWidget(spacer)
        text = p.label if live else p.label + u" (更新)"
        label = QtWidgets.QLabel(text)
        label.setToolTip(tip)
        if not live:
            label.setStyleSheet("color: #8aa8d8;")
        self.form.addRow(label, row)
        self.rows[p.name] = (p, getter, setter, widget, key)
        self._connect(p, widget)

    def _connect(self, p, widget):
        commit = lambda *args: self._commit(p.name)  # noqa: E731
        if p.kind == "bool":
            widget.toggled.connect(commit)
        elif p.kind in ("int", "float"):
            widget.valueChanged.connect(lambda *args: self._commit_later(p.name))
        elif p.kind == "choice":
            widget.currentIndexChanged.connect(commit)
        elif p.kind == "color":
            widget.on_change = commit
        elif p.kind == "file":
            widget.line_edit.editingFinished.connect(commit)
        else:
            widget.editingFinished.connect(commit)

    def _build_links(self, root):
        while self.links_layout.count():
            item = self.links_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        links = self.backend.links(root)
        for text, node in links:
            b = QtWidgets.QPushButton(text)
            b.setToolTip(node)
            b.clicked.connect(lambda _=False, n=node: self.backend.select([n]))
            self.links_layout.addWidget(b)
        self.links_box.setVisible(bool(links))

    # ------------------------------------------------------------ 値の同期
    def _sync_values(self):
        if self.spec is None or not self.rows:
            return
        values = self.backend.values(self.root, [r[0] for r in self.rows.values()])
        focus = QtWidgets.QApplication.focusWidget()
        self._updating = True
        try:
            for name, (p, getter, setter, widget, key) in self.rows.items():
                if name in self._timers and self._timers[name].isActive():
                    continue   # 書き込み待ちの物は上書きしない
                if focus is not None and (focus is widget or widget.isAncestorOf(focus)):
                    continue
                if _differs(getter(), values[name]):
                    setter(values[name])
        finally:
            self._updating = False
        self._sync_keys()

    def _sync_keys(self):
        for name, (p, getter, setter, widget, key) in self.rows.items():
            if key is None:
                continue
            state = self.backend.key_state(self.root, p)
            color = KEY_COLORS.get(state)
            key.setStyleSheet("color: %s;" % color if color else "")
            key.setToolTip(KEY_TIPS.get(state, u""))
            key.setProperty("dfx_state", state)
            widget.setEnabled(state != "driven" and key.isEnabled())

    # ------------------------------------------------------------ 書き込み
    def _commit_later(self, name):
        if self._updating:
            return
        timer = self._timers.get(name)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._commit(name))
            self._timers[name] = timer
        timer.start(COMMIT_MS)

    def _commit(self, name):
        if self._updating or self.root is None or name not in self.rows:
            return
        p, getter, setter, widget, key = self.rows[name]
        value = p.coerce(getter())
        if not self.backend.write(self.root, p, value):
            return
        if self.spec is not None and name not in self.spec.live:
            self._pending_rebuild = True
            self.update_button.setStyleSheet("font-weight: bold;")
            self._set_note(False, 1)
        self._sync_keys()

    def flush(self):
        """書き込み待ちの数値をすぐ書く (テスト・更新の前に)。"""
        for name, timer in list(self._timers.items()):
            if timer.isActive():
                timer.stop()
                self._commit(name)

    def _on_key(self, p):
        if self.root is None:
            return
        self.flush()
        self.backend.toggle_key(self.root, p)
        self._sync_keys()

    # ------------------------------------------------------------ 保存・読み込み
    def preset_targets(self):
        """読み込み先: ロック中は表示中の 1 つ、そうでなければ選んでいる同じ種類のエフェクト全部。"""
        if self.root is None:
            return []
        if self.is_locked():
            return [self.root]
        roots = [r for r in self.backend.selected_roots() if self.backend.describe(r)[0] == self.effect_type]
        return roots if self.root in roots else [self.root]

    def _on_save(self):
        if self.root is None or self.run_preset is None:
            return
        self.flush()
        self.backend.select([self.root])
        self.run_preset("save")

    def _on_load(self):
        if self.root is None or self.run_preset is None:
            return
        self.flush()
        self.backend.select(self.preset_targets())
        self.run_preset("load")
        self._pending_rebuild = False
        self.update_button.setStyleSheet("")
        self.root = None   # 作り直した後は、もう一度読み直す
        self.refresh()

    def _on_update(self):
        if self.root is None or self.run_update is None:
            return
        self.flush()
        root = self.root
        self.backend.select([root])
        self.run_update(self.effect_type, root)
        self._pending_rebuild = False
        self.update_button.setStyleSheet("")
        self.root = None   # 作り直した後は、もう一度読み直す
        self.refresh()


def _differs(a, b):
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        return any(abs(x - y) > 1e-6 for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) > 1e-9
        except (TypeError, ValueError):
            return a != b
    return a != b

