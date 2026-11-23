# -*- coding: utf-8 -*-
"""パラメータのプリセット (JSON ファイル) の保存と読み込み。

Qt にも Maya にも依存しない。値の検証は Param.coerce に任せる。

ファイル形式:
    {
      "format": "DummyEffectMaker preset",
      "version": 1,
      "type": "homing",
      "params": {"radius": 0.1, "color": [1.0, 0.75, 0.3], ...}
    }
"""
from __future__ import absolute_import

import io
import json

from .tool import ToolError

FORMAT_NAME = "DummyEffectMaker preset"
FORMAT_VERSION = 1
FILE_FILTER = u"プリセット (*.json)"


def save_preset(path, effect_type, params, values):
    """params の値 (values) を path に保存する。"""
    data = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "type": effect_type,
        "params": dict((p.name, _to_json(values[p.name])) for p in params),
    }
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    except (IOError, OSError) as e:
        raise ToolError(u"プリセットを保存できません: %s" % e)


def load_preset(path, effect_type, params):
    """path のプリセットを読み、{name: 値} を返す。

    ファイルに無いパラメータは含めない (呼び出し側で今の値のままにする)。
    知らないキーは無視する。種類が違う・値が不正ならユーザー向けエラー。
    """
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError) as e:
        raise ToolError(u"プリセットを読み込めません: %s" % e)
    if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
        raise ToolError(u"Dummy Effect Maker のプリセットファイルではありません")
    if data.get("type") != effect_type:
        raise ToolError(u"種類の違うプリセットです (ファイル: %s / 必要: %s)"
                        % (data.get("type"), effect_type))

    raw = data.get("params")
    if not isinstance(raw, dict):
        raise ToolError(u"プリセットにパラメータがありません")
    by_name = dict((p.name, p) for p in params)
    values = {}
    for name, value in raw.items():
        param = by_name.get(name)
        if param is None:
            continue
        try:
            values[name] = param.coerce(value)
        except (ValueError, TypeError):
            raise ToolError(u"プリセットの値が不正です: %s = %r" % (param.label, value))
    return values


def _to_json(value):
    return list(value) if isinstance(value, tuple) else value
