# -*- coding: utf-8 -*-
# Maya のスクリプトエディタ (Python タブ) に貼り付けて実行する起動スクリプト。
# シェルフにドラッグしてボタンにしておくと便利。
# REPO_PATH をこのリポジトリを置いた場所に書き換えてください。
import sys

REPO_PATH = r"C:/path/to/DummyEffectMaker"

if REPO_PATH not in sys.path:
    sys.path.insert(0, REPO_PATH)

# 毎回最新のコードを読み込み直してから開く (開発中向け)
for _name in list(sys.modules):
    if _name == "dummy_effect_maker" or _name.startswith("dummy_effect_maker."):
        del sys.modules[_name]

import dummy_effect_maker  # noqa: E402

dummy_effect_maker.show()
