# -*- coding: utf-8 -*-
"""カメラタブ: スピードライン (トンネル)。消失点が画面の中にあるスピードライン。

カメラがトンネル (円柱) の中にいて、トンネルの内側の壁に並んだ線 (や星・ハート・菱形) が、
軸に沿って奥へ (または手前へ) 流れていく。

    dfx_tunnelN              ルート (パラメータ。カメラとつながる)
      dfx_tunnelN_vanish     消失点 (ロケータ。カメラに付いて動く (offsetParentMatrix)。移動はカメラの空間で、
                             X / Y に動かすと消失点が画面の中で動く)
      dfx_tunnelN_follow     カメラに張り付く (ペアレントコンストレイント)
        dfx_tunnelN_tunnel   トンネルの向き (消失点へのエイムコンストレイント。ローカル -Z が消失点の向き)
          dfx_tunnelN_items  線・形 (毎フレーム、トンネルの空間で置き直す)

線・形は「手前の端」と「完全に消える距離」の間を流れ、1 周するごとに壁の上の位置が変わる。
「消え始める距離」→「完全に消える距離」で小さくなって消え、カメラのすぐ前 (手前で消える距離) でも小さくなる。
計算は motion.tunnel_item と同じ式 (Blender 版も同じ関数を使う)。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param, ToolError
from . import _weather as wx
from . import focus_lines as fl
from .. import motion as mo

EFFECT_TYPE = "tunnel_lines"
VANISH_ATTR = "dfx_vanish"
SHAPE_LINE = u"線"
SHAPE_STAR = u"星"
SHAPE_HEART = u"ハート"
SHAPE_DIAMOND = u"菱形"
SHAPES = [SHAPE_LINE, SHAPE_STAR, SHAPE_HEART, SHAPE_DIAMOND]
REBUILD = ("shape", "count")
H = wx.mel_hash


# ---------------------------------------------------------------- 形 (Blender 版も使う。XY 面、Z を向く)
def shape_points(shape):
    """(頂点, 面)。線は幅 1・長さ 1 (Y = -1〜0。先頭が 0)、ほかは大きさ約 1 (中心が 0)。"""
    if shape == SHAPE_LINE:
        return [(-0.5, -1.0, 0.0), (0.5, -1.0, 0.0), (0.5, 0.0, 0.0), (-0.5, 0.0, 0.0)], [[0, 1, 2, 3]]
    if shape == SHAPE_STAR:
        rim = []
        for k in range(10):
            a = math.pi / 2 + math.pi * k / 5
            rad = 0.5 if k % 2 == 0 else 0.2
            rim.append((rad * math.cos(a), rad * math.sin(a), 0.0))
    elif shape == SHAPE_HEART:
        rim = []
        for k in range(24):
            t = 2 * math.pi * k / 24
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            rim.append((x / 34.0, (y + 2.5) / 34.0, 0.0))
    else:   # 菱形
        rim = [(0.0, 0.5, 0.0), (-0.32, 0.0, 0.0), (0.0, -0.5, 0.0), (0.32, 0.0, 0.0)]
    pts = [(0.0, 0.0, 0.0)] + rim
    n = len(rim)
    return pts, [[0, 1 + k, 1 + (k + 1) % n] for k in range(n)]


def _params():
    return [
        Param("shape", u"形", SHAPE_LINE, kind="choice", choices=SHAPES,
              tooltip=u"流れる物の形 (線は流れる向きに伸びる。ほかは壁に貼り付いて流れる)"),
        Param("count", u"数", 80, minimum=1, maximum=600),
        Param("direction", u"流れる向き", mo.TUNNEL_AWAY, kind="choice", choices=[mo.TUNNEL_AWAY, mo.TUNNEL_TOWARD],
              tooltip=u"奥へ: カメラの近くから消失点へ遠ざかる / 手前へ: 消失点の方から近づいてくる"),
        Param("speed", u"速さ", 0.8, minimum=0.0, step=0.1, tooltip=u"1 フレームに進む距離"),
        Param("radius", u"トンネルの半径", 3.0, minimum=0.01, step=0.5),
        Param("radius_random", u"半径のばらつき", 0.3, minimum=0.0, maximum=0.95, step=0.1,
              tooltip=u"壁からの浮き沈み (0 できれいな円柱の上)"),
        Param("near", u"手前の端", 0.5, minimum=0.0, step=0.5, tooltip=u"カメラからこの距離より手前には来ない"),
        Param("near_fade", u"手前で消える距離", 3.0, minimum=0.001, step=0.5,
              tooltip=u"手前の端からこの距離の間で小さくなって消える (カメラにぶつからないように)"),
        Param("fade_start", u"消え始める距離", 25.0, minimum=0.0, step=5.0,
              tooltip=u"カメラからこの距離 (トンネルの軸に沿って) から小さくなり始める"),
        Param("fade_end", u"完全に消える距離", 40.0, minimum=0.1, step=5.0,
              tooltip=u"この距離で完全に消える (トンネルの奥の端)"),
        Param("length", u"線の長さ", 3.0, minimum=0.0, step=0.5),
        Param("width", u"線の太さ", 0.05, minimum=0.0, step=0.01, decimals=3),
        Param("size", u"形の大きさ", 0.5, minimum=0.0, step=0.1),
        Param("size_random", u"大きさのばらつき", 0.4, minimum=0.0, maximum=0.95, step=0.1),
        Param("spin", u"形の回転 (度 / フレーム)", 0.0, step=1.0, decimals=1),
        Param("spin_random", u"形の向きのばらつき", 1.0, minimum=0.0, maximum=1.0, step=0.1,
              tooltip=u"1 で形ごとにばらばらの向き、0 でそろう"),
        Param("color", u"色", (1.0, 1.0, 1.0), kind="color"),
        Param("opacity", u"不透明度", 1.0, minimum=0.0, maximum=1.0, step=0.1),
        Param("seed", u"シード", 1, minimum=0, maximum=99999),
    ]


class TunnelLinesTool(BaseTool):
    label = u"スピードライン (トンネル)"
    tab = fl.TAB
    category = u"スピードライン"
    order = 636
    tooltip = (u"カメラがトンネルの中にいるような、消失点が画面の中にあるスピードラインを作ります。\n"
               u"カメラを選んで押してください (未選択ならシーンのカメラ)。\n"
               u"消失点のロケータ (_vanish) を X / Y に動かすと、消失点が画面の中で動きます。\n"
               u"線のほかに、星・ハート・菱形も流せます。")
    params = _params()

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        return wx.create_effect(cmds, mu, ctx, v, KIND)


class TunnelLinesUpdateTool(BaseTool):
    label = u"スピードライン (トンネル) を更新"
    tab = fl.TAB
    category = u"スピードライン"
    order = 637
    tooltip = (u"選択したスピードライン (トンネル) を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目 (形・数) を変えた時に使います。消失点のロケータはそのまま。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu
        roots = [r for r in mu.selected_effect_roots()
                 if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) == EFFECT_TYPE]
        if not roots:
            raise ToolError(u"更新するスピードライン (トンネル) (dfx_tunnelN、またはその中の物) を選択してください")
        return wx.rebuild_effects(cmds, mu, ctx, roots)


# ---------------------------------------------------------------- 構築 (Maya)
def _create_vanish(cmds, mu, root, v, camera=None, target=None):
    prefix = wx.short(root)
    loc = cmds.spaceLocator(name=prefix + "_vanish")[0]
    loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
    cmds.setAttr(loc + ".translate", 0.0, 0.0, -20.0, type="double3")
    shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)
    if camera:   # カメラに付いて動く (ロケータの移動はカメラの空間: -Z が前)
        cmds.connectAttr(camera + ".worldMatrix[0]", loc + ".offsetParentMatrix")
    mu.tag(loc)
    wx.link(cmds, root, VANISH_ATTR, loc)
    return loc


def _item_body(r, line):
    """線・形 $i の $p / $X $Y $Z / $S / $vis (トンネルの空間。motion.tunnel_item と同じ)。"""
    code = u"""float $nr = {r}.dfx_near;
float $fr = max({r}.dfx_fade_end, $nr + 0.01);
float $span = $fr - $nr;
float $n = {r}.dfx_speed * frame / $span + {h71};
float $c = floor($n);
float $q = $n - $c;
int $away = ({r}.dfx_direction == 0);
float $depth = $fr - $q * $span;
if ($away) $depth = $nr + $q * $span;
float $th = 6.2831853 * {h72};
float $rr = {r}.dfx_radius * (1 + ({h73} - 0.5) * 2 * {r}.dfx_radius_random);
float $fade = clamp(0, 1, ($fr - $depth) / max($fr - {r}.dfx_fade_start, 1e-3))
            * clamp(0, 1, ($depth - $nr) / max({r}.dfx_near_fade, 1e-3));
float $ct = cos($th); float $st = sin($th);
vector $p = <<$rr * $ct, $rr * $st, -$depth>>;
vector $Z = <<-$ct, -$st, 0>>;
int $vis = ($fade > 0);
""".format(r=r, h71=H("$i", 0, 71), h72=H("$i", "$c", 72), h73=H("$i", "$c", 73))
    if line:
        code += u"""vector $Y = <<0, 0, 1>>;
if ($away) $Y = <<0, 0, -1>>;
vector $X = cross($Y, $Z);
vector $S = <<{r}.dfx_width * $fade, {r}.dfx_length * $fade, 1>>;
""".format(r=r)
    else:
        code += u"""vector $Y0 = <<0, 0, -1>>;
vector $X0 = cross($Y0, $Z);
float $a = deg_to_rad({r}.dfx_spin * frame + 360.0 * {h75} * {r}.dfx_spin_random);
vector $X = $X0 * cos($a) + $Y0 * sin($a);
vector $Y = $Y0 * cos($a) - $X0 * sin($a);
float $sz = {r}.dfx_size * (1 + ({h74} - 0.5) * 2 * {r}.dfx_size_random) * $fade;
vector $S = <<$sz, $sz, $sz>>;
""".format(r=r, h74=H("$i", "$c", 74), h75=H("$i", "$c", 75))
    return code


def build(cmds, mu, root, v):
    prefix = wx.short(root)
    owned = []
    camera = wx.find_link(cmds, root, wx.CAMERA_ATTR)
    vanish = wx.find_link(cmds, root, VANISH_ATTR)
    if not camera or not vanish:
        raise ToolError(u"%s のカメラか消失点のロケータが見つかりません" % prefix)
    follow = cmds.ls(cmds.group(empty=True, name=prefix + "_follow", parent=root), long=True)[0]
    con = cmds.parentConstraint(camera, follow, maintainOffset=False, name=prefix + "_followCamera")[0]
    owned += [follow, cmds.ls(con, long=True)[0]]
    tunnel = cmds.ls(cmds.group(empty=True, name=prefix + "_tunnel", parent=follow), long=True)[0]
    aim = cmds.aimConstraint(vanish, tunnel, aimVector=(0, 0, -1), upVector=(0, 1, 0), worldUpType="objectrotation",
                             worldUpObject=follow, worldUpVector=(0, 1, 0), name=prefix + "_aimVanish")[0]
    owned += [tunnel, cmds.ls(aim, long=True)[0]]
    line = v["shape"] == SHAPE_LINE
    pts, faces = shape_points(v["shape"])
    src_grp = cmds.ls(cmds.group(empty=True, name=prefix + "_src", parent=root), long=True)[0]
    owned.append(src_grp)
    src = wx.make_mesh(cmds, prefix + "_itemSrc", src_grp, pts, faces)
    cmds.setAttr(src_grp + ".visibility", 0)
    _, sg = wx.flat_shader(cmds, mu, root, prefix + "_mat", owned, color_attr="color", opacity_attr="opacity")
    cmds.sets(src, edit=True, forceElement=sg)
    count = int(v["count"])
    grp = cmds.ls(cmds.group(empty=True, name=prefix + "_items", parent=tunnel), long=True)[0]
    owned.append(grp)
    nodes = [cmds.instance(src, name="%s_item_%03d" % (prefix, k + 1))[0] for k in range(count)]
    cmds.parent(nodes, grp, relative=True)
    nodes = cmds.listRelatives(grp, children=True, fullPath=True) or []
    head = u"float $sd = %s.dfx_seed;\n" % prefix
    body = _item_body(prefix, line)
    wx.chunked_expressions(cmds, root, prefix + "_item", nodes, wx.TRANSFORM_OUTPUTS, head,
                           lambda a, b: body + wx.mel_store_transform(a), owned)
    wx.finish(cmds, mu, root, owned)


def _live():
    return tuple(p.name for p in _params() if p.name not in REBUILD)


KIND = wx.register(wx.Kind(EFFECT_TYPE, TunnelLinesTool, "dfx_tunnel#", u"スピードライン (トンネル)", _live(), REBUILD,
                           build=build, create=_create_vanish, need_camera=True, need_wind=False,
                           keep=(VANISH_ATTR,)))
