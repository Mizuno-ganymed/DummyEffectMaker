# -*- coding: utf-8 -*-
"""バルカン砲: 発射位置から目標へ、まっすぐ弾を一定間隔で撃ち続ける。

追尾弾との違い:
    追尾弾は軌道カーブがロケータにつながっているので、飛んでいる最中の弾も
    ロケータを動かすと軌道が変わる。
    バルカン砲の弾は「発射したフレームでのロケータの位置」を使って飛ぶので、
    発射後にロケータが動いても、その弾の軌道は変わらない
    (ロケータにキーを打って動かすと、発射のたびにその時の位置へ撃つ)。

構成:
    dfx_vulcanN                  ルート (移動ロック。パラメータをアトリビュートで持つ)
      dfx_vulcanN_muzzle         発射位置のロケータ
      dfx_vulcanN_target         目標のロケータ (赤)
      dfx_vulcanN_bulletSrc      弾の元メッシュ (非表示。インスタンス元)
      dfx_vulcanN_bullets
        dfx_vulcanN_bullet_001   弾 (1 発につき 1 つのエクスプレッションで動く)
        ...

弾のエクスプレッションは、自分の発射フレームでのロケータの位置を
getAttr -time で読み、その 2 点を結ぶ直線上を進む (向きも進行方向に合わせる)。
ロケータは UUID で探すので、名前を変えたり別の親の下に移したりしてもよい。
"""
from __future__ import absolute_import

from ..core import BaseTool, Param, ToolError
from . import homing

EFFECT_TYPE = "vulcan"
CATEGORY = u"射撃"

SHAPE_SPHERE = homing.SHAPE_SPHERE
SHAPE_SQUASHED = homing.SHAPE_SQUASHED
SPEED_CONSTANT = homing.SPEED_CONSTANT
SPEED_LINEAR = homing.SPEED_LINEAR
SPEED_QUADRATIC = homing.SPEED_QUADRATIC

LOCATOR_ATTRS = ("dfx_muzzle", "dfx_target")
LOCATOR_LABELS = (u"発射位置", u"目標")
CREATE_PARAMS = ("length",)
# エクスプレッションがルートの値を直接読むので、すぐ反映される
LIVE_PARAMS = ("color", "radius", "shape", "stretch", "subdivisions",
               "travel_frames", "speed_mode", "min_speed")
# 弾の数が変わるので「バルカン砲を更新」で反映
REBUILD_PARAMS = ("start_frame", "end_frame", "interval", "use_instance")


# ---------------------------------------------------------------- 純粋な計算
def bullet_position(muzzle, target, frame, fire, travel, mode=SPEED_CONSTANT,
                    min_ratio=1.0):
    """発射フレーム fire の弾の、frame での位置 (エクスプレッションと同じ式。検証用)。

    muzzle / target は発射フレームでのロケータの位置。
    """
    t = (frame - fire) / max(travel, 0.001)
    p = homing.travel_fraction(mode, min_ratio, t)
    return tuple(a + (b - a) * p for a, b in zip(muzzle, target))


def bullet_visible(frame, fire, travel):
    """発射フレームに出現し、着弾した次のフレームで消える (追尾弾と同じ)。"""
    return fire <= frame < fire + travel + 1


# ---------------------------------------------------------------- ツール
def _param(name, **overrides):
    """追尾弾と共通のパラメータ (既定値だけ変えたものを含む)。"""
    base = dict((p.name, p) for p in homing.HomingTool.params)[name]
    kw = dict(label=base.label, default=base.default, kind=base.kind,
              minimum=base.minimum, maximum=base.maximum, step=base.step,
              decimals=base.decimals, choices=base.choices, tooltip=base.tooltip)
    kw.update(overrides)
    return Param(name, **kw)


class VulcanTool(BaseTool):
    label = u"バルカン砲"
    category = CATEGORY
    order = 20
    tooltip = (u"発射位置と目標のロケータと、まっすぐ飛ぶ弾をまとめて作成します。\n"
               u"弾は発射した時点のロケータの位置へ飛ぶので、発射後にロケータを\n"
               u"動かしても、飛んでいる弾の軌道は変わりません (追尾弾との違い)。\n"
               u"作成後はルート (dfx_vulcanN) のチャンネルボックスで調整できます。")
    params = [
        Param("length", u"発射位置〜目標の距離", 20.0, minimum=0.01,
              tooltip=u"作成時のロケータの配置 (発射位置は原点、目標は X 方向にこの距離)"),
        _param("start_frame"),
        _param("end_frame"),
        _param("interval", default=1.0),
        _param("travel_frames", default=6.0,
               tooltip=u"1 発が発射位置から目標に届くまでの時間"),
        _param("speed_mode"),
        _param("min_speed"),
        _param("shape", default=SHAPE_SQUASHED),
        _param("radius", default=0.08),
        _param("stretch", default=4.0),
        _param("subdivisions"),
        _param("color", default=(1.0, 0.85, 0.4)),
        _param("use_instance"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        frames = homing.check_fire_frames(v)
        root = cmds.group(empty=True, name="dfx_vulcan#")
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, EFFECT_TYPE)
        mu.add_param_attrs(root, stored_params(), v, LIVE_PARAMS)
        locators = _create_locators(cmds, mu, root, v["length"])
        mu.lock_transform(root)

        build_effect(cmds, mu, root, frames, v, locators)
        cmds.select(root)
        _log_effect(ctx, root, frames, v)
        return root


class VulcanUpdateTool(BaseTool):
    label = u"バルカン砲を更新"
    category = CATEGORY
    order = 25
    tooltip = (u"選択したバルカン砲を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (発射の開始・終了・間隔など) を変えたあとに使います。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return rebuild_effects(cmds, mu, ctx, _selected_roots(mu, u"更新する"))


class VulcanSavePresetTool(BaseTool):
    label = u"バルカン砲のパラメータを保存"
    category = CATEGORY
    order = 26
    undoable = False  # シーンは変えない
    tooltip = u"選択したバルカン砲のパラメータをプリセットファイル (.json) に保存します。"

    def run(self, ctx, **v):
        from . import _maya_utils as mu

        roots = _selected_roots(mu, u"保存する")
        return mu.save_preset_dialog(ctx, roots, EFFECT_TYPE, stored_params(),
                                     self.label, u"バルカン砲")


class VulcanLoadPresetTool(BaseTool):
    label = u"バルカン砲のパラメータを読み込み"
    category = CATEGORY
    order = 27
    tooltip = (u"プリセットファイル (.json) のパラメータを選択したバルカン砲に設定し、\n"
               u"作り直します (複数選択可。ロケータの位置はそのまま)。")

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
    by_name = dict((p.name, p) for p in VulcanTool.params)
    return [by_name[n] for n in LIVE_PARAMS + REBUILD_PARAMS]


def _short(node):
    return node.split("|")[-1]


def _selected_roots(mu, purpose):
    roots = mu.selected_effect_roots(EFFECT_TYPE)
    if not roots:
        raise ToolError(u"%sバルカン砲 (dfx_vulcanN か、その中のロケータや弾) を選択してください"
                        % purpose)
    return roots


def _create_locators(cmds, mu, root, length):
    """root の子に発射位置と目標のロケータを作り、root のアトリビュートに接続する。"""
    prefix = _short(root)
    locators = []
    for suffix, pos, attr in (("muzzle", (0.0, 0.0, 0.0), LOCATOR_ATTRS[0]),
                              ("target", (length, 0.0, 0.0), LOCATOR_ATTRS[1])):
        loc = cmds.spaceLocator(name="%s_%s" % (prefix, suffix))[0]
        loc = cmds.parent(loc, root, relative=True)[0]
        loc = cmds.ls(loc, long=True)[0]
        cmds.setAttr(loc + ".translate", *pos, type="double3")
        mu.tag(loc)
        cmds.addAttr(root, longName=attr, attributeType="message")
        cmds.connectAttr(loc + ".message", root + "." + attr)
        locators.append(loc)
    shape = cmds.listRelatives(locators[1], shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 13)  # 赤
    return locators


def _stored_locators(cmds, root):
    result = []
    for attr, label in zip(LOCATOR_ATTRS, LOCATOR_LABELS):
        conns = cmds.listConnections(root + "." + attr, source=True,
                                     destination=False) or []
        if not conns:
            raise ToolError(u"%s の%sロケータが見つかりません (削除された可能性があります)"
                            % (_short(root), label))
        result.append(cmds.ls(conns[0], long=True)[0])
    if result[0] == result[1]:
        raise ToolError(u"発射位置と目標には別々のロケータを指定してください")
    return result


def _log_effect(ctx, root, frames, v):
    ctx.log(u"  %s: %d 発 (フレーム %s 〜 %s, %s フレーム間隔)"
            % (_short(root), len(frames), frames[0], frames[-1], v["interval"]))


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    """roots をアトリビュートの値で作り直す (ロケータはそのまま)。"""
    params = stored_params()
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % _short(root))
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        jobs.append((root, values, homing.check_fire_frames(values),
                     _stored_locators(cmds, root), driven))

    for root, values, frames, locators, driven in jobs:
        mu.delete_owned(root)
        mu.sync_param_attrs(root, params, values, LIVE_PARAMS, keep=LOCATOR_ATTRS)
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        build_effect(cmds, mu, root, frames, values, locators)
        _log_effect(ctx, root, frames, values)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s"
                    % u", ".join(ignored))
    result = [job[0] for job in jobs]
    cmds.select(result)
    return result


def build_effect(cmds, mu, root, frames, v, locators):
    """root の下に弾とエクスプレッションを作る (作成時と更新時で共通)。"""
    def attr(name):
        return "%s.%s%s" % (root, mu.PARAM_PREFIX, name)

    prefix = _short(root)
    owned = []
    src, sphere, sg, scale_plug = homing.build_bullet_source(
        cmds, mu, root, v, attr, prefix, owned)

    # ルートは原点に固定 (移動ロック) なので、弾の translate = ワールド座標
    bullets_grp = cmds.group(empty=True, name=prefix + "_bullets", parent=root)
    owned.append(bullets_grp)
    bullets = []
    for i, fire in enumerate(frames):
        bullet = homing.make_bullet(cmds, src, sphere, "%s_bullet_%03d" % (prefix, i + 1),
                                    bullets_grp, v["use_instance"])
        bullet = cmds.ls(bullet, long=True)[0]
        # 回転は Z (上下の傾き) → Y (左右の向き) の順に効かせる
        cmds.setAttr(bullet + ".rotateOrder", 5)  # zyx
        bullets.append(bullet)
        cmds.connectAttr(scale_plug, bullet + ".scale")
        expr = cmds.expression(name="%s_motion_%03d" % (prefix, i + 1),
                               string=_expression(mu, root, locators, bullet, fire),
                               alwaysEvaluate=True, unitConversion="all")
        owned.append(expr)

    cmds.sets(bullets, edit=True, forceElement=sg)
    cmds.setAttr(src + ".visibility", 0)
    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)
    return bullets


def _expression(mu, root, locators, bullet, fire):
    """1 発分のエクスプレッション。

    発射フレームでのロケータのワールド位置を getAttr -time で読むので、
    その後ロケータが動いても軌道は変わらない。ロケータの translate を読むのは
    依存関係を作るため (ロケータを動かしたら再計算させる)。
    """
    r, b = _short(root), _short(bullet)
    m, t = [_short(n) for n in locators]
    find = (mu.mel_find_node("$mn", locators[0], "%s.%s" % (root, LOCATOR_ATTRS[0]))
            + mu.mel_find_node("$tn", locators[1], "%s.%s" % (root, LOCATOR_ATTRS[1])))
    return find + u"""
float $dep = {m}.translateX + {m}.translateY + {m}.translateZ
           + {t}.translateX + {t}.translateY + {t}.translateZ;
float $f = frame;
float $t0 = {fire!r};
float $tr = max({r}.dfx_travel_frames, 0.001);
float $a[] = {{0, 0, 0}};
float $c[] = {{0, 0, 0}};
if (`objExists $mn` && `objExists $tn`) {{
    float $ma[] = `getAttr -time $t0 ($mn + ".worldMatrix[0]")`;
    float $mc[] = `getAttr -time $t0 ($tn + ".worldMatrix[0]")`;
    $a = {{$ma[12], $ma[13], $ma[14]}};
    $c = {{$mc[12], $mc[13], $mc[14]}};
}}
float $u = clamp(0, 1, ($f - $t0) / $tr);
float $m = clamp(0, 1, {r}.dfx_min_speed / 100.0);
float $p = $u;
if ({r}.dfx_speed_mode == 1)
    $p = ($u - (1 - $m) * $u * $u / 2) / ((1 + $m) / 2);
else if ({r}.dfx_speed_mode == 2)
    $p = ($m * $u + (1 - $m) * (1 - pow(1 - $u, 3)) / 3) / ($m + (1 - $m) / 3);
float $dx = $c[0] - $a[0];
float $dy = $c[1] - $a[1];
float $dz = $c[2] - $a[2];
{b}.translateX = $a[0] + $dx * $p;
{b}.translateY = $a[1] + $dy * $p;
{b}.translateZ = $a[2] + $dz * $p;
{b}.rotateX = 0;
{b}.rotateY = atan2d(-$dz, $dx);
{b}.rotateZ = atan2d($dy, hypot($dx, $dz));
{b}.visibility = ($f >= $t0) && ($f < $t0 + $tr + 1);
""".format(r=r, b=b, m=m, t=t, fire=float(fire))
