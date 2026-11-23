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
        for root in rt.effect_roots():
            bd.release(root)
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves, bpy.data.lattices):
            for idb in list(coll):
                if idb.get(rt.TAG_KEY) and idb.users == 0:
                    coll.remove(idb)
        for ng in list(bpy.data.node_groups):   # お化けブラーの残像が使うノードグループ
            if ng.name.startswith("dfx_") and ng.users == 0:
                bpy.data.node_groups.remove(ng)
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


NOT_LINKS = (rt.OWNER_KEY, rt.CONTROL_KEY, "dfx_path")
NOT_CONTROLS = ("dfx_camera", "dfx_wind", "dfx_source")


def links_of(root):
    """ルートが指している物 {プロパティ: オブジェクト} (カメラ・風・弾のルート・注視点など)。"""
    import bpy
    out = {}
    for key in root.keys():
        if key.startswith("dfx_") and key not in NOT_LINKS:
            value = root.get(key)
            if isinstance(value, bpy.types.Object):
                out[key] = value
    return out


def bullets_of(root):
    """弾 (書き出しで通り道を書く物): 追尾弾・バルカン砲の弾、エネルギー弾の球。"""
    import bpy
    out = [o for o in bpy.data.objects if o.get(rt.OWNER_KEY) == root and ("dfx_fire" in o or o.get("dfx_ball"))]
    return sorted(out, key=lambda o: o.name)


def build_shot(scene, roots, start, end):
    import bpy
    from ... import __version__, catalog
    from ...tools import _weather as mwx
    from . import _bullets
    scale = scene.unit_settings.scale_length or 1.0
    frames = list(range(int(round(start)), int(round(end)) + 1))
    source = {"app": "blender", "app_version": bpy.app.version_string, "scene": bpy.data.filepath,
              "tool_version": __version__, "up_axis": "z", "linear_unit": "m"}
    shot = xc.new_shot(source, scene.render.fps / scene.render.fps_base, start, end)

    def transform(obj):
        t, q, s = xc.decompose(xc.convert_matrix(_rows(obj.matrix_world), "z", scale))
        return {"t": t, "q": q, "s": s}

    def point(p):
        return xc.convert_point(p, "z", scale)

    plans = []
    cameras = [scene.camera] if scene.camera is not None else []
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
        links = links_of(root)
        head.update(name=root.name, baked=False, links=dict((k, o.name) for k, o in links.items()))
        ctrls = [(c.get(rt.ROLE_KEY, "child"), c) for c in rt.controls(root)]
        ctrls += [(k, o) for k, o in sorted(links.items()) if k not in NOT_CONTROLS and o not in [c for _, c in ctrls]]
        cam = links.get("dfx_camera")
        if cam is not None and cam.type == "CAMERA" and cam not in cameras:
            cameras.append(cam)
        data = {}
        if effect_type == _bullets.EXPLOSION_TYPE or effect_type.startswith("discharge_"):
            data["hits"] = [{"frame": h["frame"], "bullet": h["index"], "position": point(h["pos"]),
                             "normal": xc.convert_vector(h["normal"], "z")} for h in _bullets.read_hits(root)]
        elif effect_type == "wind" and "dfxWindX" in root:
            from . import _wx
            vel = list(zip(*[list(root[k]) for k in _wx.TABLE_KEYS[3:]]))
            drift = list(zip(*[list(root[k]) for k in _wx.TABLE_KEYS[:3]]))
            t0 = root.get(_wx.DRIFT_START_KEY, 0.0)
            data["wind"] = xc.make_track(frames, [
                {"velocity": point(mwx.table_value(vel, t0, f)), "drift": point(mwx.table_value(drift, t0, f))}
                for f in frames])
        plans.append((head, ctrls, bullets_of(root), data,
                      {"controls": [[] for _ in ctrls], "bullets": None, "normal": []}))
    for plan in plans:
        plan[4]["bullets"] = [[] for _ in plan[2]]
    cam_samples = dict((c.name, []) for c in cameras)
    current = scene.frame_current
    try:
        for f in frames:
            scene.frame_set(f)
            for head, ctrls, bullets, data, samples in plans:
                for i, (_, c) in enumerate(ctrls):
                    samples["controls"][i].append(transform(c))
                for i, b in enumerate(bullets):
                    s = transform(b)
                    s["visible"] = max(abs(x) for x in b.scale) > 1e-9
                    samples["bullets"][i].append(s)
                if head["type"] == "shockwave":
                    center = [c for _, c in ctrls if c.get(rt.ROLE_KEY) == "center"]
                    if center:
                        n = center[0].matrix_world.to_3x3().col[2].normalized()
                        samples["normal"].append({"normal": tuple(n)})
            for c in cameras:
                s = transform(c)
                s["focal_length"] = c.data.lens
                cam_samples[c.name].append(s)
    finally:
        scene.frame_set(current)
    for head, ctrls, bullets, data, samples in plans:
        head["controls"] = [{"name": c.name, "role": role, "shape": "empty" if c.type == "EMPTY" else c.type.lower(),
                             "track": xc.make_track(frames, s)}
                            for (role, c), s in zip(ctrls, samples["controls"])]
        if bullets:
            data["bullets"] = [{"name": b.name, "track": xc.make_track(frames, s)}
                               for b, s in zip(bullets, samples["bullets"])]
        if samples["normal"]:
            data["ring_normal"] = xc.make_track(frames, samples["normal"])
        head["data"] = data
        shot["effects"].append(head)
    for c in cameras:
        cam = c.data
        shot["cameras"].append({"name": c.name,
                                "film_aperture_mm": [cam.sensor_width, cam.sensor_height],
                                "clip": [cam.clip_start * scale, cam.clip_end * scale],
                                "track": xc.make_track(frames, cam_samples[c.name])})
    return shot
