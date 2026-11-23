# -*- coding: utf-8 -*-
"""ツール全体の環境設定 (JSON ファイルに保存)。

Qt にも Maya にも依存しない。保存先:
    Windows: %APPDATA%/DummyEffectMaker/settings.json
    その他 : ~/.config/DummyEffectMaker/settings.json
環境変数 DUMMY_EFFECT_MAKER_SETTINGS でファイルを差し替えられる (テスト用)。
"""
from __future__ import absolute_import

import io
import json
import os

ENV_VAR = "DUMMY_EFFECT_MAKER_SETTINGS"

# キー: (既定値, 説明)
DEFAULTS = {
    # 空ならツールごとの既定フォルダ (マイドキュメント/maya/DummyEffectMaker/presets/<種類>)
    "preset_dir": "",
}


def settings_path():
    override = os.environ.get(ENV_VAR)
    if override:
        return override
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "DummyEffectMaker", "settings.json")


def load():
    """全設定を返す (ファイルが無い・壊れている場合は既定値)。"""
    values = dict(DEFAULTS)
    try:
        with io.open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        return values
    if isinstance(data, dict):
        for key in DEFAULTS:
            if key in data and isinstance(data[key], type(DEFAULTS[key])):
                values[key] = data[key]
    return values


def get(key):
    return load()[key]


def set(key, value):  # noqa: A001  (settings.set として使う)
    if key not in DEFAULTS:
        raise KeyError(key)
    values = load()
    values[key] = value
    path = settings_path()
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True))
