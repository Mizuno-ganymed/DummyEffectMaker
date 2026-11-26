# -*- coding: utf-8 -*-
"""爆発 (単体): コリジョンも弾も無しで、置いた所 (爆心のロケータ) で起きる爆発。

部品と動きは「着弾爆発」(弾がコリジョンに当たった所で起きる方。射撃タブ) と同じ:
    閃光・衝撃波の輪・火の玉・煙・火花
当たった所・面の向きの代わりに、爆心のロケータ (_center) の位置と上 (Y) の向きを使う。
起きるフレームは「開始フレーム」。ロケータを動かす・回す・キーを打つと、すぐ付いてくる。

    dfx_burstN          ルート (パラメータ。カメラとつながる)
      dfx_burstN_center 爆心 (動かしてよい。上 (Y) の向きに火の玉が広がり、輪はその面に沿う)
      _flashes / _rings / _fire / _smoke / _sparks  (爆発と同じ部品)

計算は motion.py の explosion_* と同じ式 (当たり = (開始フレーム, 爆心の位置, 上の向き))。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param, ToolError
from . import _weather as wx
from . import explosion as ex

EFFECT_TYPE = "explosion_single"
TAB = ex.TAB
CATEGORY = u"爆発"
COUNT_PARAMS = ("fire_count", "smoke_count", "spark_count")
_SKIP = ("hide_bullet", "substeps")   # 弾・当たり判定の項目は使わない


def _params():
    return ([Param("start_frame", u"開始フレーム", 1.0, decimals=2, tooltip=u"爆発が起きるフレーム")]
            + [p for p in ex._explosion_params() if p.name not in _SKIP])


def hit_of(v, p0, up):
    """motion.explosion_* に渡す当たり (Y が上の空間の位置・向き)。"""
    return {"frame": v["start_frame"], "pos": tuple(p0), "normal": tuple(up)}


class ExplosionSingleTool(BaseTool):
    label = u"爆発 (単体)"
    tab = TAB
    category = CATEGORY
    order = 905
    tooltip = (u"コリジョンも弾も無しで、爆心のロケータの所で起きる爆発を作ります (閃光・衝撃波の輪・火の玉・煙・火花)。\n"
               u"オブジェクトを選んでいればその位置に置きます。ロケータの上 (Y) の向きに広がります。\n"
               u"起きるフレームは「開始フレーム」。数を変えたら「爆発 (単体) を更新」を押します。")
    params = _params()

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, KIND)


class ExplosionSingleUpdateTool(BaseTool):
    label = u"爆発 (単体) を更新"
    tab = TAB
    category = CATEGORY
    order = 906
    tooltip = (u"選択した爆発 (単体) を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (火の玉・煙・火花の数) を変えた時に使います。爆心のロケータはそのまま。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        roots = selected(cmds, mu)
        if not roots:
            raise ToolError(u"更新する爆発 (単体) (dfx_burstN、またはその中の物) を選択してください")
        return wx.rebuild_effects(cmds, mu, ctx, roots)


def selected(cmds, mu):
    return [r for r in mu.selected_effect_roots()
            if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) == EFFECT_TYPE]


# ---------------------------------------------------------------- 構築 (Maya)
def _create_center(cmds, mu, root, v, camera=None, target=None):
    from . import nuke
    return nuke._create_center(cmds, mu, root, dict(v, size=max(v["size"], 0.5)), camera, target)


def _head(root):
    """爆心 ($P / 上 $N) と時刻 ($t0 / $age) と大きさ $S0。爆発の部品の式がそのまま使う。"""
    r = wx.short(root)
    dm = wx.track_name(root, wx.EMITTER_ATTR)
    return wx.mel_head(root, need_camera=True, need_wind=False) + wx.mel_rows(dm, "g") + u"""
vector $P = <<{dm}.outputTranslateX, {dm}.outputTranslateY, {dm}.outputTranslateZ>>;
vector $N = $gR1;
float $t0 = {r}.dfx_start_frame;
float $age = frame - $t0;
float $S0 = {r}.dfx_size;
""".format(r=r, dm=dm)


def build(cmds, mu, root, v):
    from . import weather
    prefix = wx.short(root)
    owned = []
    wx.track_links(cmds, mu, root, owned)
    src_grp = cmds.group(empty=True, name=prefix + "_src", parent=root)
    owned.append(src_grp)
    pts, faces = wx.disc_points(16, 0.5)
    flash_src = wx.make_mesh(cmds, prefix + "_flashSrc", src_grp, pts, faces)
    ring_pts, ring_faces = wx.disc_points(24, 0.5, 0.42)
    ring_src = wx.make_mesh(cmds, prefix + "_ringSrc", src_grp, ring_pts, ring_faces)
    cpts, cfaces, cuvs = wx.card_points(-1.0, 0.0)
    spark_src = wx.make_mesh(cmds, prefix + "_sparkSrc", src_grp, cpts, cfaces, cuvs)
    fire_src = weather._puff_mesh(cmds, prefix + "_fireSrc", src_grp)
    smoke_src = weather._puff_mesh(cmds, prefix + "_smokeSrc", src_grp)
    cmds.setAttr(src_grp + ".visibility", 0)
    for src, color in ((flash_src, "flash_color"), (ring_src, "ring_color"), (spark_src, "spark_color")):
        _, sg = wx.flat_shader(cmds, mu, root, "%s_%s_mat" % (prefix, color.split("_")[0]), owned,
                               color_attr=color, opacity_attr="opacity")
        cmds.sets(src, edit=True, forceElement=sg)
    for src, part in ((fire_src, "fire"), (smoke_src, "smoke")):
        sg = weather._toon_shader(cmds, mu, root, "%s_%s_mat" % (prefix, part), owned,
                                  color=part + "_color", shade=part + "_shade")
        cmds.sets(src, edit=True, forceElement=sg)
    head = _head(root)
    for name, src, count, body in (("flashes", flash_src, 1, ex._flash_body),
                                   ("rings", ring_src, 1, ex._ring_body),
                                   ("fire", fire_src, int(v["fire_count"]), ex._fire_body),
                                   ("smoke", smoke_src, int(v["smoke_count"]), ex._smoke_body),
                                   ("sparks", spark_src, int(v["spark_count"]), ex._spark_body)):
        if count <= 0:
            continue
        nodes = wx.make_instances(cmds, root, src, "%s_%s" % (prefix, name), count, owned)
        wx.chunked_expressions(
            cmds, root, "%s_%s" % (prefix, name), nodes, wx.TRANSFORM_OUTPUTS, head,
            (lambda fn: lambda a, b: u"int $e = 0;\nint $j = $i;\n" + fn(prefix, a))(body), owned)
    wx.finish(cmds, mu, root, owned)


def _live():
    return tuple(p.name for p in _params() if p.name not in COUNT_PARAMS)


KIND = wx.register(wx.Kind(EFFECT_TYPE, ExplosionSingleTool, "dfx_burst#", u"爆発 (単体)", _live(), COUNT_PARAMS,
                           build=build, create=_create_center, need_camera=True, need_wind=False))
