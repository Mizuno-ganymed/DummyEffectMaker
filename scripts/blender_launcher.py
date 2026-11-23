# -*- coding: utf-8 -*-
# Blender のテキストエディタに貼り付けて「スクリプト実行」する起動スクリプト。
# 3D ビューの右のサイドバー (N キー) に「Dummy Effect」タブが出る。
# REPO_PATH をこのリポジトリを置いた場所に書き換えてください。
# (毎回使うなら、scripts/blender_addon.py をアドオンとして入れる方が便利)
import sys

REPO_PATH = r"C:/path/to/DummyEffectMaker"

if REPO_PATH not in sys.path:
    sys.path.insert(0, REPO_PATH)

# 毎回最新のコードを読み込み直して登録し直す (開発中向け)
for _name in list(sys.modules):
    if _name == "dummy_effect_maker" or _name.startswith("dummy_effect_maker."):
        del sys.modules[_name]

import dummy_effect_maker.blender as dfx  # noqa: E402

dfx.register()
