# -*- coding: utf-8 -*-
"""Dummy Effect Maker の Blender アドオン (このファイル 1 つを入れる)。

入れ方:
    1. 編集 → プリファレンス → アドオン → 右上の ∨ → 「ディスクからインストール」で、このファイルを選ぶ
    2. アドオンの設定の「DummyEffectMaker フォルダ」に、このリポジトリを置いた場所を入れる
    3. 3D ビューの右のサイドバー (N キー) に「Dummy Effect」タブが出る

コードはリポジトリから読むので、リポジトリを更新すれば Blender を再起動するだけで新しくなる
(アドオンの設定の「読み込み直す」でも、再起動せずに読み直せる)。
"""
import os
import sys

import bpy

bl_info = {
    "name": "Dummy Effect Maker",
    "description": u"プレビズ用のダミーエフェクト (Maya 版と同じパラメータ・Unreal 用の書き出し)",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": u"3D ビュー > サイドバー > Dummy Effect",
    "category": "Animation",
}

ENV_VAR = "DUMMY_EFFECT_MAKER_PATH"


def _repo_path():
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        if prefs.repo_path:
            return bpy.path.abspath(prefs.repo_path)
    except (KeyError, AttributeError):
        pass
    if os.environ.get(ENV_VAR):
        return os.environ[ENV_VAR]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(here, "dummy_effect_maker")):
        return here  # リポジトリの scripts/ から直接使っている時
    return ""


def _load():
    path = _repo_path()
    if not path or not os.path.isdir(os.path.join(path, "dummy_effect_maker")):
        print(u"Dummy Effect Maker: アドオンの設定で DummyEffectMaker フォルダを指定してください")
        return False
    path = os.path.normpath(path)
    if path not in sys.path:
        sys.path.insert(0, path)
    import dummy_effect_maker.blender as dfx
    dfx.register()
    return True


def _reload(*args):
    try:
        import dummy_effect_maker.blender as dfx
        dfx.unregister()
    except Exception:
        pass
    for name in list(sys.modules):
        if name == "dummy_effect_maker" or name.startswith("dummy_effect_maker."):
            del sys.modules[name]
    _load()


class DFX_OT_reload_addon(bpy.types.Operator):
    bl_idname = "dfx.reload_addon"
    bl_label = u"読み込み直す"
    bl_description = u"リポジトリのコードを読み込み直す (開発用)"

    def execute(self, context):
        _reload()
        return {"FINISHED"}


class DFXAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    repo_path: bpy.props.StringProperty(
        name=u"DummyEffectMaker フォルダ", subtype="DIR_PATH",
        description=u"このリポジトリ (dummy_effect_maker フォルダがある場所)",
        update=lambda self, context: _reload())

    def draw(self, context):
        self.layout.prop(self, "repo_path")
        self.layout.operator("dfx.reload_addon")


def register():
    bpy.utils.register_class(DFX_OT_reload_addon)
    bpy.utils.register_class(DFXAddonPreferences)
    _load()


def unregister():
    try:
        import dummy_effect_maker.blender as dfx
        dfx.unregister()
    except Exception:
        pass
    bpy.utils.unregister_class(DFXAddonPreferences)
    bpy.utils.unregister_class(DFX_OT_reload_addon)
