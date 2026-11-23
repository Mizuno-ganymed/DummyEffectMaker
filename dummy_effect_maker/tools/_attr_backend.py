# -*- coding: utf-8 -*-
"""専用のアトリビュートエディタ (ui/attribute_editor.py) の Maya 側。

画面は Maya を知らず、このクラスを通して、選んでいるエフェクトのパラメータを読み書きする。
"""
from __future__ import absolute_import

# 種類 → その種類を作り直す「〜を更新」ツールのクラス名 (ランチャーのツールから探す)
UPDATE_TOOLS = {
    "homing": "HomingUpdateTool", "vulcan": "VulcanUpdateTool", "energy_ball": "EnergyBallUpdateTool",
    "focus_lines": "FocusLinesUpdateTool", "speed_lines": "SpeedLinesUpdateTool",
    "camera_shake": "CameraShakeUpdateTool", "hand_shake": "HandShakeUpdateTool",
    "shockwave": "ShockwaveUpdateTool", "explosion": "ExplosionUpdateTool", "nuke": "NukeUpdateTool",
    "wind": "WindUpdateTool",
}
GHOST_UPDATE = "GhostBlurUpdateTool"
WEATHER_UPDATE = "WeatherUpdateTool"

KEY_NONE = "none"          # キーなし
KEY_ON_FRAME = "key"       # 今のフレームにキーがある
KEY_ANIMATED = "animated"  # キーがある (今のフレーム以外)
KEY_DRIVEN = "driven"      # キー以外 (エクスプレッション・接続) で決まっている

WATCH_EVENTS = ("SelectionChanged", "timeChanged", "Undo", "Redo", "SceneOpened", "NewSceneOpened")


def update_tool_name(effect_type):
    if effect_type in UPDATE_TOOLS:
        return UPDATE_TOOLS[effect_type]
    from . import ghost_blur
    if effect_type in ghost_blur.KINDS:
        return GHOST_UPDATE
    return WEATHER_UPDATE   # 天候・稲妻 (天候の仕組み)


class MayaAttributeBackend(object):
    host = "maya"

    def __init__(self):
        from maya import cmds
        from . import _maya_utils as mu
        self.cmds = cmds
        self.mu = mu
        self._jobs = []

    # ------------------------------------------------------------ 選択
    def selected_roots(self):
        return self.mu.selected_effect_roots()

    def describe(self, root):
        """(種類, EffectSpec か None, ベイク済みか)。"""
        from .. import catalog
        cmds, mu = self.cmds, self.mu
        if not cmds.objExists(root):
            return None, None, False
        effect_type = cmds.getAttr(root + "." + mu.EFFECT_TYPE_ATTR)
        try:
            spec = catalog.spec(effect_type)
        except KeyError:
            spec = None
        return effect_type, spec, bool(mu.is_baked(root))

    def exists(self, node):
        return bool(node) and self.cmds.objExists(node)

    def short(self, node):
        return node.split("|")[-1]

    # ------------------------------------------------------------ 値
    def has(self, root, param):
        return self.cmds.attributeQuery(self.mu.PARAM_PREFIX + param.name, node=root, exists=True)

    def values(self, root, params):
        return self.mu.read_param_values(root, params)

    def write(self, root, param, value):
        """値を書く (1 回のアンドゥ)。書けない (キー以外で決まっている) 時は False。"""
        if not self.has(root, param) or self.key_state(root, param) == KEY_DRIVEN:
            return False
        cmds = self.cmds
        cmds.undoInfo(openChunk=True, chunkName=u"dfx: %s" % param.label)
        try:
            self.mu.write_param_values(root, [param], {param.name: value})
        finally:
            cmds.undoInfo(closeChunk=True)
        return True

    def _plugs(self, root, param):
        plug = self.mu.param_plug(root, param)
        return [plug + c for c in "RGB"] if param.kind == "color" else [plug]

    def key_state(self, root, param):
        cmds = self.cmds
        if not self.has(root, param):
            return KEY_NONE
        plugs = self._plugs(root, param)
        t = cmds.currentTime(query=True)
        animated = on_frame = driven = False
        for plug in plugs:
            src = cmds.listConnections(plug, source=True, destination=False, skipConversionNodes=True) or []
            curves = [s for s in src if cmds.objectType(s, isAType="animCurve")]
            if src and not curves:
                driven = True
            if curves:
                animated = True
                if cmds.keyframe(plug, query=True, time=(t, t), keyframeCount=True):
                    on_frame = True
        if driven:
            return KEY_DRIVEN
        if on_frame:
            return KEY_ON_FRAME
        return KEY_ANIMATED if animated else KEY_NONE

    def toggle_key(self, root, param):
        """今のフレームにキーが無ければ打つ、あれば消す (1 回のアンドゥ)。"""
        cmds = self.cmds
        state = self.key_state(root, param)
        if state == KEY_DRIVEN or not self.has(root, param):
            return state
        t = cmds.currentTime(query=True)
        cmds.undoInfo(openChunk=True, chunkName=u"dfx: キー %s" % param.label)
        try:
            for plug in self._plugs(root, param):
                if state == KEY_ON_FRAME:
                    cmds.cutKey(plug, time=(t, t), clear=True)
                else:
                    cmds.setKeyframe(plug)
        finally:
            cmds.undoInfo(closeChunk=True)
        return self.key_state(root, param)

    # ------------------------------------------------------------ つながり
    def links(self, root):
        """動かして決める物とつながった物 [(見出し, ノード)]。"""
        from . import export_shot
        cmds, mu = self.cmds, self.mu
        links = export_shot._links(cmds, mu, root)
        out = []
        for role, node in export_shot._controls(cmds, mu, root, links):
            out.append((self.short(node), node))
        names = {"dfx_camera": u"カメラ", "dfx_wind": u"風", "dfx_source": u"弾"}
        for attr in ("dfx_camera", "dfx_wind", "dfx_source"):
            if attr in links:
                out.append((u"%s: %s" % (names[attr], self.short(links[attr])), links[attr]))
        return out

    def select(self, nodes):
        self.cmds.select(nodes, replace=True)

    # ------------------------------------------------------------ 変化の監視
    def watch(self, callback):
        self.unwatch()
        for event in WATCH_EVENTS:
            try:
                job = self.cmds.scriptJob(event=[event, callback], killWithScene=False)
            except (RuntimeError, TypeError):
                continue
            if isinstance(job, int):   # 画面の無い mayapy では番号が返らない
                self._jobs.append(job)

    def unwatch(self):
        for job in self._jobs:
            try:
                if self.cmds.scriptJob(exists=job):
                    self.cmds.scriptJob(kill=job, force=True)
            except (RuntimeError, TypeError):
                pass
        self._jobs = []
