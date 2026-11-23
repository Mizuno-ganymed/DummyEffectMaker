# -*- coding: utf-8 -*-
"""ツールの基底クラスとパラメータ定義。

ここは Qt にも Maya にも依存しない。スタンドアロン化の際もそのまま使える層。
"""
from __future__ import absolute_import


class Param(object):
    """ツールのパラメータ定義。オプションダイアログの自動生成に使う。

    kind は default から推測される (bool / int / float / str)。
    明示指定が必要なもの:
        kind="choice" : choices にリストを渡す。値は選択肢の文字列
        kind="color"  : default に (r, g, b) 0.0-1.0 のタプル
    """

    KINDS = ("bool", "int", "float", "str", "choice", "color")

    def __init__(self, name, label, default, kind=None, minimum=None,
                 maximum=None, step=None, decimals=3, choices=None,
                 tooltip=""):
        self.name = name
        self.label = label
        self.default = default
        self.kind = kind or self._infer_kind(default)
        if self.kind not in self.KINDS:
            raise ValueError("unknown param kind: %s" % self.kind)
        if self.kind == "choice" and not choices:
            raise ValueError("choice param '%s' needs choices" % name)
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.decimals = decimals
        self.choices = list(choices or [])
        self.tooltip = tooltip

    @staticmethod
    def _infer_kind(default):
        # bool は int のサブクラスなので先に判定する
        if isinstance(default, bool):
            return "bool"
        if isinstance(default, int):
            return "int"
        if isinstance(default, float):
            return "float"
        if isinstance(default, str):
            return "str"
        raise TypeError("cannot infer param kind from %r" % (default,))

    def coerce(self, value):
        """値を型に合わせて正規化する (範囲外はクランプ)。"""
        if self.kind == "bool":
            return bool(value)
        if self.kind in ("int", "float"):
            value = int(value) if self.kind == "int" else float(value)
            if self.minimum is not None:
                value = max(self.minimum, value)
            if self.maximum is not None:
                value = min(self.maximum, value)
            return value
        if self.kind == "str":
            return str(value)
        if self.kind == "choice":
            value = str(value)
            if value not in self.choices:
                raise ValueError("'%s' is not a valid choice for %s"
                                 % (value, self.name))
            return value
        if self.kind == "color":
            rgb = tuple(float(c) for c in value)
            if len(rgb) != 3:
                raise ValueError("color needs 3 components")
            return tuple(min(1.0, max(0.0, c)) for c in rgb)
        return value


class ToolError(Exception):
    """ユーザー向けのエラー (トレースバックを出さずにメッセージだけ表示する)。"""


class BaseTool(object):
    """小さなツール 1 つ分。tools パッケージ内でこれを継承すると自動登録される。

    サブクラスで設定する属性:
        label     : ボタンに表示する名前 (必須。空のクラスは登録されない)
        tab       : ランチャー上部のタブ名
        category  : タブ内のグループ名
        tooltip   : ボタンのツールチップ
        order     : カテゴリ内の並び順 (小さいほど上)
        hosts     : 動作するホスト ("maya", "standalone")。None なら全ホスト
        params    : Param のリスト。あればオプションボタン (□) が付く
        undoable  : True なら 1 回の実行が 1 アンドゥになる
    """

    label = ""
    tab = u"ツール"
    category = "その他"
    tooltip = ""
    order = 100
    hosts = ("maya",)
    params = ()
    undoable = True

    def __init__(self):
        self.values = self.default_values()

    @classmethod
    def tool_id(cls):
        return "%s.%s" % (cls.__module__, cls.__name__)

    @classmethod
    def default_values(cls):
        return dict((p.name, p.default) for p in cls.params)

    def is_available(self, host_name):
        return self.hosts is None or host_name in self.hosts

    def set_values(self, values):
        """values を検証して保持する。未知のキーは無視する。"""
        by_name = dict((p.name, p) for p in self.params)
        for key, value in values.items():
            if key in by_name:
                self.values[key] = by_name[key].coerce(value)

    def reset_values(self):
        self.values = self.default_values()

    def run(self, ctx, **values):
        """ツール本体。ctx は ToolContext、values はパラメータ値。"""
        raise NotImplementedError


class ToolContext(object):
    """実行時にツールへ渡される環境。"""

    def __init__(self, host, log=None, parent_widget=None):
        self.host = host
        self.parent_widget = parent_widget
        self._log = log or (lambda msg: print(msg))

    def log(self, message):
        self._log(message)


def execute(tool, ctx):
    """ツールを実行する。成功なら戻り値を返し、失敗時は例外をそのまま送出。"""
    values = dict(tool.values)
    if tool.undoable:
        with ctx.host.undo_chunk(tool.label):
            return tool.run(ctx, **values)
    return tool.run(ctx, **values)
