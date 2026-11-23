# -*- coding: utf-8 -*-
"""衝撃波: 中心のロケータから、端のロケータの距離まで、ドーナツ (輪) がブワッと広がる。

構成:
    dfx_shockwaveN               ルート (移動ロック。パラメータをアトリビュートで持つ)
      dfx_shockwaveN_center      中心のロケータ (動かす・回転させると輪の向きが変わる)
        dfx_shockwaveN_ring_01   輪 (トーラス。中心のロケータの XZ 面に広がる)
        ...
      dfx_shockwaveN_edge        端のロケータ (赤)。中心からの距離まで輪が広がる

広がり方 (輪ごとに、出てからのフレーム t):
    速さ   = 初速 × (1 - 減衰率)^t       (毎フレーム、減衰率の割合ずつ遅くなる)
    半径   = 速さを足し合わせた距離       (= 初速 / k × (1 - e^(-k t))、k = -ln(1 - 減衰率))
    端の距離に届いたら、そこで「消えるフレーム数」かけて細くなって消える。
    減衰が強すぎて届かない時は、最大の半径 (初速 / k) の 99% まで広がったところで消える。
    太さ   = ドーナツの太さ × (1 → 終わりの太さ) (広がるにつれて細くなる)

太さを半径と別に変えるため、輪は「太さ 0 のトーラス」に「太さ 1 のトーラス」を
ブレンドシェイプでつないで作る (ウェイト = 太さ / 半径)。半径は拡大縮小で付ける。
キーとブレンドシェイプだけで動くので、ベイクすれば FBX でもそのまま使える。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param, ToolError

EFFECT_TYPE = "shockwave"
TAB = u"衝撃波"
CATEGORY = u"衝撃波"

LOCATOR_ATTRS = ("dfx_center", "dfx_edge")
CREATE_PARAMS = ("distance",)
LIVE_PARAMS = ("start_frame", "interval", "speed", "decay", "thickness", "thickness_end",
               "flatten", "fade_frames", "color", "opacity")
REBUILD_PARAMS = ("count", "subdivisions")

BASE_TUBE = 0.001       # ブレンドシェイプの元 (太さ 0 の代わり)
MAX_RINGS = 50
UNREACHED_RATIO = 0.99  # 端まで届かない時、最大の半径の何割まで広がったら消えるか


# ---------------------------------------------------------------- 純粋な計算
def decay_rate(decay):
    """1 フレームあたりの減衰率 → 連続の減衰の速さ k。"""
    decay = min(max(decay, 0.0), 0.999)
    return -math.log(1.0 - decay)


def expand_radius(t, speed, decay):
    """出てから t フレームの半径 (端で止めない)。"""
    k = decay_rate(decay)
    if k < 1e-9:
        return speed * t
    return speed / k * (1.0 - math.exp(-k * t))


def reach(distance, speed, decay):
    """(広がりきる半径, かかるフレーム数, 端まで届くか)。"""
    speed = max(speed, 1e-6)
    k = decay_rate(decay)
    if k < 1e-9:
        return distance, distance / speed, True
    r_max = speed / k
    if distance < r_max * UNREACHED_RATIO:
        return distance, -math.log(1.0 - distance * k / speed) / k, True
    target = r_max * UNREACHED_RATIO
    return target, -math.log(1.0 - UNREACHED_RATIO) / k, False


def ring_state(t, v, distance):
    """出てから t フレームの輪の (半径, 太さ, 表示)。エクスプレッションと同じ式 (検証用)。"""
    if t < 0:
        return 0.0, 0.0, False
    target, t_reach, _ = reach(distance, v["speed"], v["decay"])
    radius = min(expand_radius(t, v["speed"], v["decay"]), target) if t < t_reach else target
    progress = radius / target if target > 1e-9 else 1.0
    thick = v["thickness"] * (1.0 + (v["thickness_end"] - 1.0) * progress)
    fade = max(v["fade_frames"], 1e-3)
    if t > t_reach:
        thick *= max(0.0, 1.0 - (t - t_reach) / fade)
    return radius, thick, t <= t_reach + fade


def actual_tube(radius, thick):
    """実際のチューブの太さ (ブレンドシェイプのウェイトは 0〜1 なので、半径より太くはならない)。"""
    s = max(radius, 1e-4)
    weight = min(1.0, max(0.0, (thick / s - BASE_TUBE) / (1.0 - BASE_TUBE)))
    return s * (BASE_TUBE + weight * (1.0 - BASE_TUBE))


def ring_start(v, index):
    return v["start_frame"] + index * v["interval"]


# ---------------------------------------------------------------- ツール
class ShockwaveTool(BaseTool):
    label = u"衝撃波"
    tab = TAB
    category = CATEGORY
    order = 800
    tooltip = (u"中心のロケータから端のロケータの距離まで、ドーナツがブワッと広がる衝撃波を作ります。\n"
               u"ロケータはルート (dfx_shockwaveN) の子にできるので、動かして位置と大きさを決めます\n"
               u"(中心のロケータを回転させると輪の向きが変わる)。")
    params = [
        Param("distance", u"端までの距離", 10.0, minimum=0.01,
              tooltip=u"作成時の端のロケータの位置 (中心から X 方向)。作成後はロケータを直接動かす"),
        Param("start_frame", u"開始フレーム", 1.0, decimals=2,
              tooltip=u"1 つ目の輪が出るフレーム"),
        Param("count", u"輪の数", 1, minimum=1, maximum=MAX_RINGS,
              tooltip=u"続けて出す輪の数"),
        Param("interval", u"輪の間隔 (フレーム)", 4.0, minimum=0.0, step=1.0, decimals=2,
              tooltip=u"輪を複数出す時、次の輪が出るまでのフレーム数"),
        Param("speed", u"初速", 2.0, minimum=0.001, step=0.5,
              tooltip=u"出た瞬間の広がる速さ (1 フレームに広がる距離)"),
        Param("decay", u"減衰率", 0.15, minimum=0.0, maximum=0.99, step=0.05,
              tooltip=u"毎フレーム、速さがこの割合ずつ遅くなる (0 で一定の速さ、大きいほど急に止まる)。\n"
                      u"強すぎて端まで届かない時は、届く所まで広がって消える (ログに表示)"),
        Param("thickness", u"ドーナツの太さ", 0.4, minimum=0.0, step=0.1,
              tooltip=u"輪の太さ (チューブの半径)"),
        Param("thickness_end", u"終わりの太さ (割合)", 0.3, minimum=0.0, maximum=2.0, step=0.1,
              tooltip=u"端まで広がった時の太さ (ドーナツの太さに対する割合)。広がるにつれてこの太さになる"),
        Param("flatten", u"平たさ", 1.0, minimum=0.0, maximum=2.0, step=0.1,
              tooltip=u"輪の縦 (輪の面に垂直な方向) の太さの倍率。1 で丸、小さいほど平たい帯になる"),
        Param("fade_frames", u"消えるフレーム数", 6.0, minimum=0.0, step=1.0, decimals=2,
              tooltip=u"端に届いてから、細くなって消えるまでのフレーム数"),
        Param("color", u"色", (0.85, 0.95, 1.0), kind="color"),
        Param("opacity", u"不透明度", 0.8, minimum=0.0, maximum=1.0, step=0.1),
        Param("subdivisions", u"分割数", 48, minimum=8, maximum=200,
              tooltip=u"輪の周りの分割数 (太さ方向はこの 1/4)"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        root = cmds.group(empty=True, name="dfx_shockwave#", world=True)
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, EFFECT_TYPE)
        mu.add_param_attrs(root, stored_params(), v, LIVE_PARAMS)
        center, edge = _create_locators(cmds, mu, root, v["distance"])
        mu.lock_transform(root)
        build_effect(cmds, mu, root, v, center, edge)
        cmds.select(root)
        _log(ctx, root, v, center, edge)
        return root


class ShockwaveUpdateTool(BaseTool):
    label = u"衝撃波を更新"
    tab = TAB
    category = CATEGORY
    order = 810
    tooltip = (u"選択した衝撃波を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (輪の数・分割数) を変えたあとに使います。ロケータはそのまま。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return rebuild_effects(cmds, mu, ctx, _selected_roots(mu, u"更新する"))


class ShockwaveSavePresetTool(BaseTool):
    label = u"衝撃波のパラメータを保存"
    tab = TAB
    category = CATEGORY
    order = 812
    undoable = False
    tooltip = u"選択した衝撃波のパラメータをプリセットファイル (.json) に保存します (ロケータの位置は含みません)。"

    def run(self, ctx, **v):
        from . import _maya_utils as mu

        return mu.save_preset_dialog(ctx, _selected_roots(mu, u"保存する"), EFFECT_TYPE,
                                     stored_params(), self.label, u"衝撃波")


class ShockwaveLoadPresetTool(BaseTool):
    label = u"衝撃波のパラメータを読み込み"
    tab = TAB
    category = CATEGORY
    order = 814
    tooltip = u"プリセットファイル (.json) のパラメータを選択した衝撃波に設定し、作り直します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = _selected_roots(mu, u"読み込み先の")
        values = mu.load_preset_dialog(ctx, EFFECT_TYPE, stored_params(), self.label)
        if values is None:
            return None
        return rebuild_effects(cmds, mu, ctx, roots, overrides=values)


# ---------------------------------------------------------------- 構築
def stored_params():
    by_name = dict((p.name, p) for p in ShockwaveTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def _short(node):
    return node.split("|")[-1]


def _selected_roots(mu, purpose):
    roots = mu.selected_effect_roots(EFFECT_TYPE)
    if not roots:
        raise ToolError(u"%s衝撃波 (dfx_shockwaveN か、その中のロケータや輪) を選択してください" % purpose)
    return roots


def _log(ctx, root, v, center, edge):
    from maya import cmds
    distance = _distance(cmds, center, edge)
    target, frames, ok = reach(distance, v["speed"], v["decay"])
    ctx.log(u"  %s: 輪 %d 個 / 端まで %.2f を %.1f フレームで広がる" % (_short(root), v["count"],
                                                              distance, frames))
    if not ok:
        ctx.log(u"  ※ 減衰が強くて端まで届きません (最大 %.2f まで)。初速を上げるか減衰率を下げてください"
                % target)


def _distance(cmds, center, edge):
    a = cmds.xform(center, query=True, worldSpace=True, translation=True)
    b = cmds.xform(edge, query=True, worldSpace=True, translation=True)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _create_locators(cmds, mu, root, distance):
    prefix = _short(root)
    made = []
    for suffix, pos, attr in (("center", (0.0, 0.0, 0.0), LOCATOR_ATTRS[0]),
                              ("edge", (distance, 0.0, 0.0), LOCATOR_ATTRS[1])):
        loc = cmds.spaceLocator(name="%s_%s" % (prefix, suffix))[0]
        loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
        cmds.setAttr(loc + ".translate", *pos, type="double3")
        mu.tag(loc)
        cmds.addAttr(root, longName=attr, attributeType="message")
        cmds.connectAttr(loc + ".message", root + "." + attr)
        made.append(loc)
    shape = cmds.listRelatives(made[1], shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 13)  # 赤
    return made


def _stored_locators(cmds, root):
    result = []
    for attr, label in zip(LOCATOR_ATTRS, (u"中心", u"端")):
        conns = cmds.listConnections(root + "." + attr, source=True, destination=False) or []
        if not conns:
            raise ToolError(u"%s の%sのロケータが見つかりません (削除された可能性があります)"
                            % (_short(root), label))
        result.append(cmds.ls(conns[0], long=True)[0])
    if result[0] == result[1]:
        raise ToolError(u"中心と端には別々のロケータを指定してください")
    return result


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    params = stored_params()
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % _short(root))
        jobs.append((root, _stored_locators(cmds, root)))
    for root, (center, edge) in jobs:
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        mu.delete_owned(root)
        mu.sync_param_attrs(root, params, values, LIVE_PARAMS, keep=LOCATOR_ATTRS)
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        build_effect(cmds, mu, root, values, center, edge)
        _log(ctx, root, values, center, edge)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s" % u", ".join(ignored))
    result = [job[0] for job in jobs]
    cmds.select(result)
    return result


def build_effect(cmds, mu, root, v, center, edge):
    """輪 (トーラス + ブレンドシェイプ) とエクスプレッションを作る (作成時と更新時で共通)。"""
    def attr(name):
        return "%s.%s%s" % (root, mu.PARAM_PREFIX, name)

    prefix = _short(root)
    owned = []
    try:
        cmds.loadPlugin("matrixNodes", quiet=True)
    except RuntimeError:
        pass

    # 端のロケータを、中心のロケータから見た位置にする (中心からの距離 = 広がりきる半径)
    mm = cmds.createNode("multMatrix", name=prefix + "_edgeLocal")
    cmds.connectAttr(edge + ".worldMatrix[0]", mm + ".matrixIn[0]")
    cmds.connectAttr(center + ".worldInverseMatrix[0]", mm + ".matrixIn[1]")
    dm = cmds.createNode("decomposeMatrix", name=prefix + "_edgeLocalPos")
    cmds.connectAttr(mm + ".matrixSum", dm + ".inputMatrix")
    owned.extend([mm, dm])

    shader, sg = mu.create_surface_shader(prefix + "_mat", v["color"], 1.0 - v["opacity"])
    cmds.connectAttr(attr("color"), shader + ".outColor", force=True)
    inv = cmds.createNode("reverse", name=prefix + "_transparency")
    for axis in "XYZ":
        cmds.connectAttr(attr("opacity"), inv + ".input" + axis)
    cmds.connectAttr(inv + ".output", shader + ".outTransparency", force=True)
    owned.extend([shader, sg, inv])

    sub_x = int(v["subdivisions"])
    sub_y = max(4, sub_x // 4)
    rings = []
    for i in range(int(v["count"])):
        name = "%s_ring_%02d" % (prefix, i + 1)
        ring = cmds.polyTorus(radius=1.0, sectionRadius=BASE_TUBE, subdivisionsX=sub_x,
                              subdivisionsY=sub_y, constructionHistory=False, name=name)[0]
        target = cmds.polyTorus(radius=1.0, sectionRadius=1.0, subdivisionsX=sub_x,
                                subdivisionsY=sub_y, constructionHistory=False,
                                name=name + "_thick")[0]
        bs = cmds.blendShape(target, ring, name=name + "_thickness")[0]
        cmds.delete(target)
        ring = cmds.ls(cmds.parent(ring, center, relative=True)[0], long=True)[0]
        shape = cmds.listRelatives(ring, shapes=True, fullPath=True)[0]
        for a in ("castsShadows", "receiveShadows"):
            cmds.setAttr(shape + "." + a, 0)
        cmds.sets(ring, edit=True, forceElement=sg)
        expr = cmds.expression(name=name + "_motion", alwaysEvaluate=True, unitConversion="all",
                               string=_expression(prefix, ring, bs, dm, i))
        owned.extend([ring, bs, expr])
        rings.append(ring)

    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)
    return rings


def _expression(r, ring, bs, dm, index):
    """輪 1 つのエクスプレッション (ring_state と同じ式)。"""
    g, b, d = _short(ring), _short(bs), _short(dm)
    return u"""
float $t = frame - ({r}.dfx_start_frame + {i} * {r}.dfx_interval);
float $R = sqrt(pow({d}.outputTranslateX, 2) + pow({d}.outputTranslateY, 2) + pow({d}.outputTranslateZ, 2));
float $v0 = max({r}.dfx_speed, 0.000001);
float $dec = clamp(0, 0.999, {r}.dfx_decay);
float $k = -log(1 - $dec);
float $target = $R;
float $treach = $R / $v0;
if ($k > 1e-9) {{
    float $rmax = $v0 / $k;
    if ($R < $rmax * {u}) {{
        $treach = -log(1 - $R * $k / $v0) / $k;
    }} else {{
        $target = $rmax * {u};
        $treach = -log(1 - {u}) / $k;
    }}
}}
float $rad = $target;
if ($t < $treach) {{
    $rad = $v0 * $t;
    if ($k > 1e-9) $rad = $v0 / $k * (1 - exp(-$k * $t));
    $rad = min($rad, $target);
}}
float $prog = 1;
if ($target > 1e-9) $prog = $rad / $target;
float $w = {r}.dfx_thickness * (1 + ({r}.dfx_thickness_end - 1) * $prog);
float $fade = max({r}.dfx_fade_frames, 0.001);
if ($t > $treach) $w = $w * max(0, 1 - ($t - $treach) / $fade);
float $s = max($rad, 0.0001);
{g}.scaleX = $s;
{g}.scaleY = $s * {r}.dfx_flatten;
{g}.scaleZ = $s;
{b}.weight[0] = clamp(0, 1, ($w / $s - {base}) / (1 - {base}));
{g}.visibility = ($t >= 0) && ($t <= $treach + $fade);
""".format(r=r, i=index, d=d, g=g, b=b, u=repr(UNREACHED_RATIO), base=repr(BASE_TUBE))
