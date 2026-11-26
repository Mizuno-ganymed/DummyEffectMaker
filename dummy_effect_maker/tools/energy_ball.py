# -*- coding: utf-8 -*-
"""エネルギー弾: ロケータごとに 1 つの球が出現し、ガタガタぶれながら目標へ飛ぶ。

構成:
    dfx_energyN                      ルート (移動ロック。全体のパラメータをアトリビュートで持つ)
      dfx_energyN_ballSrc            球の元メッシュ (非表示。インスタンス元)
      dfx_energyN_start_01           発射位置のロケータ (動かしてよい)
        dfx_energyN_ball_01          球 (ロケータの子)
      dfx_energyN_target_01          目標のロケータ (動かしてよい)
      ...

発射位置のロケータには、その球のタイミングがアトリビュートで付いている:
    出現開始フレーム → (出現フレーム数かけてスケール 0 から大きくなる) → 発射フレーム → 着弾フレーム

球の動きは 1 球につき 1 つのエクスプレッションで決まるので、ロケータを動かしても、
タイミングやルートのパラメータを変えても、すぐ反映される。
    位置   = 目標 (発射位置ロケータから見た位置) × 進み具合 + ジッター
    大きさ = 半径 × 出現の進み具合 (ease-out)
    表示   = 出現開始以降。「着弾後: 消える」なら着弾フレームまで
"""
from __future__ import absolute_import

from ..core import BaseTool, Param, ToolError

EFFECT_TYPE = "energy_ball"
TAB = u"射撃"   # 追尾弾・バルカン砲と同じタブ
CATEGORY = u"エネルギー弾"

EASE_ACCEL = u"加速"
EASE_LINEAR = u"等速"
AFTER_VANISH = u"消える"
AFTER_STAY = u"目標に残る"

LIVE_PARAMS = ("radius", "color", "jitter", "jitter_speed", "appear_frames",
               "fly_ease", "after_hit")
# 個数は「更新」で反映。タイミング・配置は球を追加するときの初期値
REBUILD_PARAMS = ("count", "appear_start", "charge_frames", "fly_frames", "stagger",
                  "spacing", "target_distance")

# 発射位置ロケータに付ける、球ごとのタイミング
BALL_ATTRS = (("dfx_appear_start", u"出現開始フレーム"),
              ("dfx_fly_start", u"発射フレーム"),
              ("dfx_fly_end", u"着弾フレーム"))
TARGET_ATTR = "dfx_target"
INDEX_ATTR = "dfx_ball_index"
MAX_BALLS = 200


# ---------------------------------------------------------------- 純粋な計算
def default_timing(v, index):
    """球 index (0 始まり) の (出現開始, 発射, 着弾) の初期値。"""
    appear = v["appear_start"] + index * v["stagger"]
    fly_start = appear + v["appear_frames"] + v["charge_frames"]
    return appear, fly_start, fly_start + v["fly_frames"]


def default_positions(v, index, count):
    """球 index の (発射位置, 目標) の初期配置。X 方向に並べ、目標は +Z 方向。"""
    x = (index - (count - 1) / 2.0) * v["spacing"]
    return (x, 0.0, 0.0), (x, 0.0, v["target_distance"])


def flight_progress(frame, fly_start, fly_end, ease):
    """発射からの進み具合 0〜1 (エクスプレッションと同じ式。検証用)。"""
    if fly_end > fly_start:
        p = min(1.0, max(0.0, (frame - fly_start) / float(fly_end - fly_start)))
    else:
        p = 1.0 if frame >= fly_start else 0.0
    return p * p if ease == EASE_ACCEL else p


def appear_scale(frame, appear_start, appear_frames):
    """出現の大きさ 0〜1 (ease-out。エクスプレッションと同じ式)。"""
    a = min(1.0, max(0.0, (frame - appear_start) / max(appear_frames, 0.001)))
    return 1.0 - (1.0 - a) ** 3


# ---------------------------------------------------------------- ツール
class EnergyBallTool(BaseTool):
    label = u"エネルギー弾"
    tab = TAB
    category = CATEGORY
    order = 700
    tooltip = (u"発射位置のロケータと、その子の球、目標のロケータを指定した数だけ作ります。\n"
               u"球はスケール 0 から出現し、ガタガタぶれながら目標へ飛んでいきます。\n"
               u"タイミングは発射位置のロケータ、全体の見た目はルート (dfx_energyN) の\n"
               u"チャンネルボックスで調整できます。")
    params = [
        Param("count", u"個数", 3, minimum=1, maximum=MAX_BALLS),
        Param("radius", u"球の半径", 0.5, minimum=0.001, step=0.1),
        Param("color", u"色", (0.4, 0.85, 1.0), kind="color"),
        Param("jitter", u"ジッターの強さ", 0.15, minimum=0.0, maximum=10.0, step=0.05,
              tooltip=u"ぶれる幅。球の半径に対する割合"),
        Param("jitter_speed", u"ジッターの速さ", 2.7, minimum=0.0, maximum=100.0, step=0.5,
              tooltip=u"大きいほど細かくガタガタする。0 で止まる"),
        Param("appear_frames", u"出現フレーム数", 6.0, minimum=0.0, step=1.0, decimals=1,
              tooltip=u"スケール 0 から元の大きさになるまでのフレーム数"),
        Param("fly_ease", u"飛び方", EASE_ACCEL, kind="choice",
              choices=[EASE_ACCEL, EASE_LINEAR],
              tooltip=u"加速: だんだん速くなる / 等速: 同じ速さ"),
        Param("after_hit", u"着弾後", AFTER_VANISH, kind="choice",
              choices=[AFTER_VANISH, AFTER_STAY]),
        Param("appear_start", u"出現開始フレーム", 1.0, decimals=1,
              tooltip=u"1 個目の球が出現し始めるフレーム (作成・追加時の初期値)"),
        Param("charge_frames", u"出現後の待機フレーム数", 10.0, minimum=0.0, decimals=1,
              tooltip=u"出現してから発射するまでのフレーム数 (作成・追加時の初期値)"),
        Param("fly_frames", u"飛ぶフレーム数", 12.0, minimum=0.0, decimals=1,
              tooltip=u"発射から着弾までのフレーム数 (作成・追加時の初期値)"),
        Param("stagger", u"ずらし (フレーム)", 4.0, decimals=1,
              tooltip=u"1 個ごとにタイミングをずらすフレーム数 (作成・追加時の初期値)"),
        Param("spacing", u"ロケータの間隔", 2.0, step=0.5,
              tooltip=u"発射位置を X 方向に並べる間隔 (作成・追加時の初期値)"),
        Param("target_distance", u"目標までの距離", 10.0, step=1.0,
              tooltip=u"目標を +Z 方向にどれだけ離して置くか (作成・追加時の初期値)"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        root = cmds.group(empty=True, name="dfx_energy#", world=True)
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, EFFECT_TYPE)
        mu.add_param_attrs(root, stored_params(), v, LIVE_PARAMS)
        mu.lock_transform(root)

        pairs = [_create_locators(cmds, mu, root, v, i, v["count"])
                 for i in range(v["count"])]
        build_effect(cmds, mu, root, pairs)
        cmds.select(root)
        _log(ctx, root, pairs)
        return root


class EnergyBallUpdateTool(BaseTool):
    label = u"エネルギー弾を更新"
    tab = TAB
    category = CATEGORY
    order = 710
    tooltip = (u"選択したエネルギー弾を作り直します。「個数」を変えたあとに使います。\n"
               u"既存のロケータ (位置・タイミング) はそのまま。増えた分は初期値で追加、\n"
               u"減った分は後ろから削除します。")
    params = [
        Param("retime_all", u"タイミングを全部の球に適用", False,
              tooltip=u"ON: ルートの「出現開始・出現後の待機・飛ぶ・ずらし」で全部の球のタイミングを設定し直す"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = mu.selected_effect_roots(EFFECT_TYPE)
        if not roots:
            raise ToolError(u"更新するエネルギー弾 (dfx_energyN か、その中のロケータや球) を選択してください")
        return rebuild_effects(cmds, mu, ctx, roots, retime_all=v["retime_all"])


def rebuild_effects(cmds, mu, ctx, roots, overrides=None, retime_all=False):
    """roots を作り直す。overrides があれば先にその値を設定する (キーや接続で決まっている物は変えない)。

    既存のロケータ (位置・タイミング) はそのまま。増えた分は初期値で追加、減った分は後ろから削除する。
    """
    params = stored_params()
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % _short(root))
    for root in roots:
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, x) for k, x in overrides.items() if k not in driven)
        mu.delete_owned(root)
        mu.sync_param_attrs(root, params, values, LIVE_PARAMS)
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        pairs = existing_pairs(cmds, root)
        count = values["count"]
        for start, target in pairs[count:]:
            cmds.delete([start, target])
        pairs = pairs[:count]
        for i in range(len(pairs), count):
            pairs.append(_create_locators(cmds, mu, root, values, i, count))
        if retime_all:
            for i, (start, _) in enumerate(pairs):
                _set_timing(cmds, start, default_timing(values, i))
        build_effect(cmds, mu, root, pairs)
        _log(ctx, root, pairs)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s" % u", ".join(ignored))
    cmds.select(roots)
    return roots


# ---------------------------------------------------------------- 構築
def stored_params():
    by_name = dict((p.name, p) for p in EnergyBallTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def _short(node):
    return node.split("|")[-1]


def _log(ctx, root, pairs):
    ctx.log(u"  %s: %d 個" % (_short(root), len(pairs)))


def _set_timing(cmds, start, timing):
    for (attr, _), value in zip(BALL_ATTRS, timing):
        cmds.setAttr(start + "." + attr, value)


def _create_locators(cmds, mu, root, v, index, count):
    """発射位置と目標のロケータを 1 組作る。(start, target) を返す。"""
    prefix = _short(root)
    start_pos, target_pos = default_positions(v, index, count)
    made = []
    for kind, pos in (("start", start_pos), ("target", target_pos)):
        loc = cmds.spaceLocator(name="%s_%s_%02d" % (prefix, kind, index + 1))[0]
        loc = cmds.parent(loc, root, relative=True)[0]
        loc = cmds.ls(loc, long=True)[0]
        cmds.setAttr(loc + ".translate", *pos, type="double3")
        mu.tag(loc)
        made.append(loc)
    start, target = made
    cmds.setAttr(cmds.listRelatives(target, shapes=True, fullPath=True)[0] + ".overrideEnabled", 1)
    cmds.setAttr(cmds.listRelatives(target, shapes=True, fullPath=True)[0] + ".overrideColor", 13)  # 赤
    cmds.addAttr(start, longName=INDEX_ATTR, attributeType="long")
    cmds.setAttr(start + "." + INDEX_ATTR, index)
    for (attr, nice), value in zip(BALL_ATTRS, default_timing(v, index)):
        cmds.addAttr(start, longName=attr, niceName=nice, attributeType="double")
        cmds.setAttr(start + "." + attr, value)
        cmds.setAttr(start + "." + attr, keyable=True)
    cmds.addAttr(start, longName=TARGET_ATTR, attributeType="message")
    cmds.connectAttr(target + ".message", start + "." + TARGET_ATTR)
    return start, target


def existing_pairs(cmds, root):
    """root の下の (発射位置, 目標) を番号順に返す (目標が消されたものは除く)。"""
    starts = []
    for node in cmds.listRelatives(root, children=True, type="transform", fullPath=True) or []:
        if cmds.attributeQuery(INDEX_ATTR, node=node, exists=True):
            conns = cmds.listConnections(node + "." + TARGET_ATTR, source=True,
                                         destination=False) or []
            if conns:
                starts.append((cmds.getAttr(node + "." + INDEX_ATTR), node,
                               cmds.ls(conns[0], long=True)[0]))
    return [(s, t) for _, s, t in sorted(starts)]


def build_effect(cmds, mu, root, pairs):
    """発射位置ロケータごとに球とエクスプレッションを作る (作成時と更新時で共通)。"""
    prefix = _short(root)
    owned = []
    try:
        cmds.loadPlugin("matrixNodes", quiet=True)
    except RuntimeError:
        pass

    src = cmds.polySphere(radius=1.0, subdivisionsAxis=16, subdivisionsHeight=10,
                          constructionHistory=False, name=prefix + "_ballSrc")[0]
    src = cmds.parent(src, root, relative=True)[0]
    src = cmds.ls(src, long=True)[0]
    cmds.setAttr(src + ".visibility", 0)
    owned.append(src)

    shader, sg = mu.create_surface_shader(prefix + "_mat", (1.0, 1.0, 1.0))
    cmds.connectAttr(root + ".dfx_color", shader + ".outColor", force=True)
    cmds.sets(src, edit=True, forceElement=sg)
    owned.extend([shader, sg])

    balls = []
    for start, target in pairs:
        index = cmds.getAttr(start + "." + INDEX_ATTR)
        name = "%s_ball_%02d" % (prefix, index + 1)
        ball = cmds.instance(src, name=name)[0]
        ball = cmds.parent(ball, start, relative=True)[0]
        ball = cmds.ls(ball, long=True)[0]
        cmds.setAttr(ball + ".visibility", 1)
        balls.append(ball)
        owned.append(ball)

        # 目標を発射位置ロケータから見た座標にする
        mm = cmds.createNode("multMatrix", name="%s_toLocal_%02d" % (prefix, index + 1))
        cmds.connectAttr(target + ".worldMatrix[0]", mm + ".matrixIn[0]")
        cmds.connectAttr(start + ".worldInverseMatrix[0]", mm + ".matrixIn[1]")
        dm = cmds.createNode("decomposeMatrix", name="%s_targetLocal_%02d" % (prefix, index + 1))
        cmds.connectAttr(mm + ".matrixSum", dm + ".inputMatrix")
        expr = cmds.expression(name="%s_motion_%02d" % (prefix, index + 1),
                               string=_expression(root, start, ball, dm, index),
                               alwaysEvaluate=True, unitConversion="all")
        owned.extend([mm, dm, expr])
    cmds.sets(balls, edit=True, forceElement=sg)

    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)
    return balls


def _expression(root, start, ball, dm, index):
    r, s, b = _short(root), _short(start), _short(ball)
    d = _short(dm)
    # ジッターは軸・球ごとに別の場所の noise を使う (同じ動きにならないように)
    seeds = [index * 31.7 + k * 113.3 for k in range(3)]
    return u"""
float $f = frame;
float $t0 = {s}.dfx_appear_start;
float $t1 = {s}.dfx_fly_start;
float $t2 = {s}.dfx_fly_end;
float $a = clamp(0, 1, ($f - $t0) / max({r}.dfx_appear_frames, 0.001));
float $grow = 1 - pow(1 - $a, 3);
float $p = 0;
if ($t2 > $t1) $p = clamp(0, 1, ($f - $t1) / ($t2 - $t1));
else if ($f >= $t1) $p = 1;
if ({r}.dfx_fly_ease == 0) $p = $p * $p;
float $rad = {r}.dfx_radius;
float $j = {r}.dfx_jitter * $rad;
float $k = $f * {r}.dfx_jitter_speed;
{b}.translateX = {d}.outputTranslateX * $p + $j * noise($k + {s0});
{b}.translateY = {d}.outputTranslateY * $p + $j * noise($k + {s1});
{b}.translateZ = {d}.outputTranslateZ * $p + $j * noise($k + {s2});
{b}.scaleX = $rad * $grow;
{b}.scaleY = $rad * $grow;
{b}.scaleZ = $rad * $grow;
{b}.visibility = ($f >= $t0) && ({r}.dfx_after_hit == 1 || $f <= $t2);
""".format(r=r, s=s, b=b, d=d, s0=seeds[0], s1=seeds[1], s2=seeds[2])
