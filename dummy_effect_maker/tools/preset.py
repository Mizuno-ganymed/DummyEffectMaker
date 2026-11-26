# -*- coding: utf-8 -*-
"""パラメータの保存と読み込み (プリセット)。全部の種類で共通。

アトリビュートエディタの「保存」「読み込み」ボタンから使う (タブ・メニューには出さない)。
種類はルートの dfxEffectType で決まり、パラメータの一覧は catalog.py、作り直しは種類ごとの
rebuild_effects (キーや接続で決まっている項目は変えない・古いルートに足りない項目は足す) に任せる。
"""
from __future__ import absolute_import

from ..core import BaseTool, ToolError


def selected_roots(cmds, mu):
    """選択中のエフェクトのルート (お化けブラー・画ぶれ・手ブレは、付けたオブジェクトを選んでもよい)。"""
    from . import camera_shake, ghost_blur
    roots = mu.selected_effect_roots()
    if roots:
        return roots
    roots = ghost_blur.roots_of_selected_targets(cmds, mu)
    roots += [r for r in camera_shake.roots_of_selected_targets(cmds, mu, camera_shake.SHAKE_TYPES)
              if r not in roots]
    return roots


def rebuilder(effect_type):
    """種類 → rebuild_effects(cmds, mu, ctx, roots, overrides=値)。"""
    from . import (_weather as wx, camera_shake, energy_ball, explosion, focus_lines, ghost_blur, homing,
                   shockwave, speed_lines, vulcan)
    from .. import catalog
    catalog.specs()   # 天候の仕組みの種類を登録させる
    table = {"homing": homing.rebuild_effects, "vulcan": vulcan.rebuild_effects,
             "energy_ball": energy_ball.rebuild_effects, "focus_lines": focus_lines.rebuild_effects,
             "speed_lines": speed_lines.rebuild_effects, "camera_shake": camera_shake.rebuild_effects,
             "hand_shake": camera_shake.rebuild_effects, "shockwave": shockwave.rebuild_effects,
             "explosion": explosion.rebuild_effects}
    if effect_type in table:
        return table[effect_type]
    if effect_type in ghost_blur.KINDS:
        return ghost_blur.rebuild_effects
    if effect_type in wx.KINDS:   # 天候・稲妻・電撃・核爆発・爆発 (単体)・布・トンネル・風
        return wx.rebuild_effects
    raise ToolError(u"この種類 (%s) のパラメータは読み込めません" % effect_type)


def _spec(effect_type):
    from .. import catalog
    try:
        return catalog.spec(effect_type)
    except KeyError:
        raise ToolError(u"この種類 (%s) のパラメータは保存・読み込みできません" % effect_type)


class PresetSaveTool(BaseTool):
    label = u"パラメータを保存"
    hidden = True
    undoable = False
    tooltip = u"選択したエフェクト 1 つのパラメータを、プリセットファイル (.json) に保存します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        roots = selected_roots(cmds, mu)
        if not roots:
            raise ToolError(u"保存するエフェクト (ルートか、その中の物) を選択してください")
        effect_type = cmds.getAttr(roots[0] + "." + mu.EFFECT_TYPE_ATTR)
        spec = _spec(effect_type)
        return mu.save_preset_dialog(ctx, roots, effect_type, spec.params, self.label, u"エフェクト")


class PresetLoadTool(BaseTool):
    label = u"パラメータを読み込み"
    hidden = True
    tooltip = (u"プリセットファイル (.json) のパラメータを、選択したエフェクトに設定して作り直します (複数選択可)。\n"
               u"同じ種類のプリセットだけ読み込めます。キーを打ってある項目は変えません。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        roots = selected_roots(cmds, mu)
        if not roots:
            raise ToolError(u"読み込み先のエフェクト (ルートか、その中の物) を選択してください")
        types = set(cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) for r in roots)
        if len(types) > 1:
            raise ToolError(u"読み込み先は同じ種類のエフェクトだけ選択してください")
        effect_type = types.pop()
        spec = _spec(effect_type)
        rebuild = rebuilder(effect_type)
        for root in roots:
            if mu.is_baked(root):
                raise ToolError(u"%s はベイク済みのため読み込めません (Ctrl+Z でベイク前に戻せます)"
                                % root.split("|")[-1])
        values = mu.load_preset_dialog(ctx, effect_type, spec.params, self.label)
        if values is None:
            return None
        return rebuild(cmds, mu, ctx, roots, overrides=values)
