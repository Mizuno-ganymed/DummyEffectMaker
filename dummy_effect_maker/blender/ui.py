# -*- coding: utf-8 -*-
"""Blender 版の画面。ツールの Param 定義から、オペレーターとパネルを自動で作る。

3D ビューの右のサイドバー (N キー) の「Dummy Effect」タブ:
    - Maya 版のランチャーと同じく、タブ → グループごとにボタンを並べる
    - ボタン: 今のオプションの値ですぐ実行。歯車: オプションのダイアログを開いて実行
    - 「選択したエフェクト」: 選んだエフェクトのパラメータ (すぐ反映 / 「(更新)」は更新で反映)
"""
from __future__ import absolute_import

import bpy

from ..core import ToolContext, ToolError, execute, registry
from ..core import host as host_mod
from . import runtime as rt

TOOLS_PACKAGE = "dummy_effect_maker.blender.tools"
CATEGORY = "Dummy Effect"

_TOOLS = []
_CLASSES = []
_OPERATORS = {}   # tool_id → bl_idname


def operator_idname(tool):
    module = type(tool).__module__.rsplit(".", 1)[-1]
    return ("dfx." + rt.safe_name(module + "_" + type(tool).__name__)).lower()[:60]


def _prop(p):
    """Param → bpy のプロパティ。"""
    kw = {"name": p.label, "description": p.tooltip or p.label}
    if p.kind == "bool":
        return bpy.props.BoolProperty(default=bool(p.default), **kw)
    if p.kind == "int":
        if p.minimum is not None:
            kw["min"] = int(p.minimum)
        if p.maximum is not None:
            kw["max"] = int(p.maximum)
        return bpy.props.IntProperty(default=int(p.default), **kw)
    if p.kind == "float":
        if p.minimum is not None:
            kw["min"] = float(p.minimum)
        if p.maximum is not None:
            kw["max"] = float(p.maximum)
        return bpy.props.FloatProperty(default=float(p.default), precision=min(p.decimals, 6), **kw)
    if p.kind == "choice":
        items = [(str(i), c, "") for i, c in enumerate(p.choices)]
        return bpy.props.EnumProperty(items=items, default=str(p.choices.index(p.default)), **kw)
    if p.kind == "color":
        return bpy.props.FloatVectorProperty(default=tuple(p.default), subtype="COLOR", size=3,
                                             min=0.0, max=1.0, **kw)
    if p.kind == "file":
        return bpy.props.StringProperty(default=p.default, subtype="FILE_PATH", **kw)
    return bpy.props.StringProperty(default=u"%s" % p.default, **kw)


def _from_prop(p, value):
    if p.kind == "choice":
        return p.choices[int(value)]
    if p.kind == "color":
        return tuple(value)
    return value


def _to_prop(p, value):
    if p.kind == "choice":
        return str(p.choices.index(value))
    return value


def run_tool(tool, report=None):
    """ツールを実行する (アンドゥはオペレーターがまとめる)。ログは report へ。"""
    lines = []
    ctx = ToolContext(host_mod.detect(), log=lines.append)
    try:
        result = execute(tool, ctx)
    finally:
        if report and lines:
            report({"INFO"}, u"\n".join(l.strip() for l in lines))
    return result


def _make_operator(tool):
    idname = operator_idname(tool)
    annotations = dict((p.name, _prop(p)) for p in tool.params)
    annotations["dfx_options"] = bpy.props.BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        if self.dfx_options and tool.params:
            for p in tool.params:
                setattr(self, p.name, _to_prop(p, tool.values[p.name]))
            return context.window_manager.invoke_props_dialog(self, width=420)
        return self.execute(context)

    def execute(self, context):
        if self.dfx_options:
            tool.set_values(dict((p.name, _from_prop(p, getattr(self, p.name))) for p in tool.params))
        try:
            run_tool(tool, self.report)
        except ToolError as e:
            self.report({"ERROR"}, u"%s" % e)
            return {"CANCELLED"}
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        if tool.tooltip:
            for line in tool.tooltip.split("\n"):
                layout.label(text=line)
        for p in tool.params:
            layout.prop(self, p.name)

    cls = type("DFX_OT_" + idname.split(".", 1)[1], (bpy.types.Operator,), {
        "bl_idname": idname, "bl_label": tool.label,
        "bl_description": tool.tooltip or tool.label,
        "bl_options": {"REGISTER", "UNDO"} if tool.undoable else {"REGISTER"},
        "__annotations__": annotations,
        "invoke": invoke, "execute": execute, "draw": draw})
    _OPERATORS[tool.tool_id()] = idname
    return cls


def _make_tab_panel(index, tab, tools):
    def draw(self, context):
        for category, cat_tools in registry.group_by_category(tools):
            box = self.layout.box()
            box.label(text=category)
            col = box.column(align=True)
            for tool in cat_tools:
                row = col.row(align=True)
                op = row.operator(_OPERATORS[tool.tool_id()], text=tool.label)
                op.dfx_options = False
                if tool.params:
                    op = row.operator(_OPERATORS[tool.tool_id()], text="", icon="PREFERENCES")
                    op.dfx_options = True

    return type("DFX_PT_tab_%d" % index, (bpy.types.Panel,), {
        "bl_label": tab, "bl_space_type": "VIEW_3D", "bl_region_type": "UI",
        "bl_category": CATEGORY, "bl_options": {"DEFAULT_CLOSED"} if index else set(),
        "draw": draw})


class DFX_OT_set_choice(bpy.types.Operator):
    """選択肢のパラメータを変える (カスタムプロパティは文字列なので、選択肢から選ぶ)。"""
    bl_idname = "dfx.set_choice"
    bl_label = u"選ぶ"
    bl_options = {"REGISTER", "UNDO"}
    root_name: bpy.props.StringProperty()
    param: bpy.props.StringProperty()
    value: bpy.props.StringProperty()

    def execute(self, context):
        root = bpy.data.objects.get(self.root_name)
        if root is None:
            return {"CANCELLED"}
        root[rt.PARAM_PREFIX + self.param] = self.value
        rt.update_root(root)
        return {"FINISHED"}


class DFX_PT_selected(bpy.types.Panel):
    bl_label = u"選択したエフェクト"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    def draw(self, context):
        from .. import catalog
        obj = context.active_object
        root = rt.root_of(obj) if obj else None
        if root is None:
            self.layout.label(text=u"エフェクトを選ぶと、ここにパラメータが出ます")
            return
        try:
            spec = catalog.spec(root[rt.TYPE_KEY])
        except KeyError:
            self.layout.label(text=root.name)
            return
        self.layout.label(text=u"%s  (%s)" % (root.name, spec.label))
        col = self.layout.column()
        for p in spec.params:
            key = rt.PARAM_PREFIX + p.name
            if key not in root:
                continue
            label = p.label if p.name in spec.live else p.label + rt.REBUILD_SUFFIX
            if p.kind == "choice":
                row = col.row(align=True)
                row.label(text=label)
                for c in p.choices:
                    op = row.operator("dfx.set_choice", text=c, depress=(root[key] == c))
                    op.root_name, op.param, op.value = root.name, p.name, c
            else:
                col.prop(root, '["%s"]' % key, text=label)


def register():
    tools = [t for t in registry.discover(TOOLS_PACKAGE) if t.is_available("blender")]
    _TOOLS[:] = tools
    classes = [_make_operator(t) for t in tools]
    classes.append(DFX_OT_set_choice)
    for i, (tab, tab_tools) in enumerate(registry.group_by_tab(tools)):
        classes.append(_make_tab_panel(i, tab, tab_tools))
    classes.append(DFX_PT_selected)
    for cls in classes:
        bpy.utils.register_class(cls)
    _CLASSES[:] = classes


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _CLASSES[:] = []
    _OPERATORS.clear()
