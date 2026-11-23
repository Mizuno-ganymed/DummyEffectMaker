# -*- coding: utf-8 -*-
"""Blender 版の「シーン」: 更新・全削除・書き出し (Unreal 用)。

書き出しは Maya 版と同じ形式 (core/exchange.py)。Blender は Z が上なので、座標は単位だけ直す。
"""
from __future__ import absolute_import

from ...core import BaseTool, Param, ToolError
from ...core import exchange as xc
from .. import runtime as rt
from . import _build as bd

TAB = u"シーン"
TARGET_ALL = u"シーン内のすべてのエフェクト"
TARGET_SELECTED = u"選択したエフェクト"
RANGE_SCENE = u"シーンのフレーム範囲"
RANGE_CUSTOM = u"指定した範囲"


class UpdateEffectsTool(BaseTool):
    label = u"エフェクトを更新"
    tab = TAB
    category = u"シーン"
    order = 900
    hosts = ("blender",)
    tooltip = (u"選択したエフェクトを、パラメータの値で作り直します (Empty はそのまま)。\n"
               u"「(更新)」の付いた項目 (発射の間隔・輪の数など) を変えた時に使います。")

    def run(self, ctx, **v):
        import bpy
        roots = [r for r in rt.selected_roots(bpy.context) if r[rt.TYPE_KEY] in bd.BUILDERS]
        if not roots:
            raise ToolError(u"更新するエフェクト (ルートか、その中の物) を選択してください")
        for root in roots:
            bd.rebuild(root)
            ctx.log(u"  %s を作り直しました" % root.name)
        return [r.name for r in roots]


class DeleteAllTool(BaseTool):
    label = u"ダミーエフェクトを全削除"
    tab = TAB
    category = u"シーン"
    order = 910
    hosts = ("blender",)
    tooltip = u"Dummy Effect Maker で作った物 (オブジェクト・メッシュ・マテリアル) を全て削除します。"

    def run(self, ctx, **v):
        import bpy
        objects = [o for o in bpy.data.objects if o.get(rt.TAG_KEY)]
        if not objects:
            ctx.log(u"  削除対象はありません")
            return 0
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for coll in (bpy.data.meshes, bpy.data.materials):
            for idb in list(coll):
                if idb.get(rt.TAG_KEY) and idb.users == 0:
                    coll.remove(idb)
        dfx = bpy.data.collections.get(rt.COLLECTION)
        if dfx is not None and not dfx.objects and not dfx.children:
            bpy.data.collections.remove(dfx)
        ctx.log(u"  %d 個のオブジェクトを削除しました" % len(objects))
        return len(objects)


class ExportShotTool(BaseTool):
    label = u"書き出し (Unreal 用)"
    tab = TAB
    category = u"書き出し"
    order = 920
    hosts = ("blender",)
    undoable = False
    tooltip = (u"ダミーエフェクトを、Unreal Engine で仕上げるためのショットのファイル (JSON) に書き出します。\n"
               u"Maya 版と同じ形式です (docs/exchange_format.md)。")
    params = [
        Param("path", u"書き出すファイル", u"//dfx_shot.json", kind="file",
              tooltip=u"// で始めると .blend と同じフォルダから"),
        Param("target", u"対象", TARGET_ALL, kind="choice", choices=[TARGET_ALL, TARGET_SELECTED]),
        Param("range_mode", u"フレーム範囲", RANGE_SCENE, kind="choice",
              choices=[RANGE_SCENE, RANGE_CUSTOM]),
        Param("start_frame", u"開始フレーム", 1.0, decimals=2),
        Param("end_frame", u"終了フレーム", 120.0, decimals=2),
    ]

    def run(self, ctx, **v):
        import bpy
        scene = bpy.context.scene
        roots = rt.effect_roots() if v["target"] == TARGET_ALL else rt.selected_roots(bpy.context)
        if not roots:
            raise ToolError(u"書き出すダミーエフェクトがありません")
        if v["range_mode"] == RANGE_SCENE:
            start, end = scene.frame_start, scene.frame_end
        else:
            start, end = v["start_frame"], v["end_frame"]
        if end < start:
            raise ToolError(u"終了フレームが開始フレームより前になっています")
        path = bpy.path.abspath(v["path"])
        if not path.lower().endswith(".json"):
            path += ".json"
        shot = build_shot(scene, roots, start, end)
        xc.write_shot(path, shot)
        ctx.log(u"  %d 個のエフェクト、カメラ %d 台 (フレーム %s 〜 %s) を書き出しました: %s"
                % (len(shot["effects"]), len(shot["cameras"]), start, end, path))
        return path


# ---------------------------------------------------------------- 書き出し
def _rows(m):
    """Blender の行列 (列ベクトル) → この形式の 16 個 (行ベクトル)。"""
    return [m[0][0], m[1][0], m[2][0], 0.0, m[0][1], m[1][1], m[2][1], 0.0,
            m[0][2], m[1][2], m[2][2], 0.0, m[0][3], m[1][3], m[2][3], 1.0]


def build_shot(scene, roots, start, end):
    import bpy
    from ... import __version__, catalog
    scale = scene.unit_settings.scale_length or 1.0
    frames = list(range(int(round(start)), int(round(end)) + 1))
    source = {"app": "blender", "app_version": bpy.app.version_string, "scene": bpy.data.filepath,
              "tool_version": __version__, "up_axis": "z", "linear_unit": "m"}
    shot = xc.new_shot(source, scene.render.fps / scene.render.fps_base, start, end)

    def transform(obj):
        t, q, s = xc.decompose(xc.convert_matrix(_rows(obj.matrix_world), "z", scale))
        return {"t": t, "q": q, "s": s}

    plans = []
    for root in roots:
        effect_type = root[rt.TYPE_KEY]
        try:
            spec = catalog.spec(effect_type)
            values = rt.read_params(root, spec.params)
            head = {"type": effect_type, "label": spec.label, "group": spec.group,
                    "params": xc.param_values(spec.params, values), "param_info": spec.describe()}
        except KeyError:
            head = {"type": effect_type, "label": effect_type, "group": "", "params": {},
                    "param_info": {}}
        head.update(name=root.name, baked=False, links={})
        ctrls = rt.controls(root)
        bullets = [o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and "dfx_fire" in o]
        bullets.sort(key=lambda o: o.name)
        plans.append((head, ctrls, bullets, {"controls": [[] for _ in ctrls],
                                             "bullets": [[] for _ in bullets], "normal": []}))
    camera = scene.camera
    cam_samples = []
    current = scene.frame_current
    try:
        for f in frames:
            scene.frame_set(f)
            for head, ctrls, bullets, samples in plans:
                for i, c in enumerate(ctrls):
                    samples["controls"][i].append(transform(c))
                for i, b in enumerate(bullets):
                    s = transform(b)
                    s["visible"] = max(abs(x) for x in b.scale) > 1e-9
                    samples["bullets"][i].append(s)
                if head["type"] == "shockwave":
                    center = [c for c in ctrls if c.get(rt.ROLE_KEY) == "center"]
                    if center:
                        n = center[0].matrix_world.to_3x3().col[2].normalized()
                        samples["normal"].append({"normal": tuple(n)})
            if camera is not None:
                s = transform(camera)
                s["focal_length"] = camera.data.lens
                cam_samples.append(s)
    finally:
        scene.frame_set(current)
    for head, ctrls, bullets, samples in plans:
        head["controls"] = [{"name": c.name, "role": c.get(rt.ROLE_KEY, "child"), "shape": "empty",
                             "track": xc.make_track(frames, s)}
                            for c, s in zip(ctrls, samples["controls"])]
        data = {}
        if bullets:
            data["bullets"] = [{"name": b.name, "track": xc.make_track(frames, s)}
                               for b, s in zip(bullets, samples["bullets"])]
        if samples["normal"]:
            data["ring_normal"] = xc.make_track(frames, samples["normal"])
        head["data"] = data
        shot["effects"].append(head)
    if camera is not None:
        cam = camera.data
        shot["cameras"].append({"name": camera.name,
                                "film_aperture_mm": [cam.sensor_width, cam.sensor_height],
                                "clip": [cam.clip_start * scale, cam.clip_end * scale],
                                "track": xc.make_track(frames, cam_samples)})
        for head, _, _, _ in plans:
            head["links"]["dfx_camera"] = camera.name
    return shot
