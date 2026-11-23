# -*- coding: utf-8 -*-
"""お化けブラー: 既に動いているオブジェクトに、速さを表す 3 種類のブラーを付ける。

    残像     少し前の位置に、オブジェクトのコピーを何体も並べる (後ろほど薄く・小さく)
    スミア   オブジェクト自体を、進行方向の後ろへ餅のように引き伸ばす
    軌跡     通った跡に、先が細くなる帯 (またはチューブ) を残す

どれも元のアニメーションは変えず、過去のフレームの位置を getAttr -time で読んで作る
(止まっている時は何も出ない)。1 つのオブジェクトに複数付けてもよい (スミアは 1 つまで)。

構成:
    dfx_afterimageN        ルート (原点に固定)。パラメータを持つ
      _ghost_01            残像 1 体分 (表示・マテリアル)
        _ghost_01_<部品>   オブジェクトの中のメッシュ 1 つ分のコピー (形は元のメッシュとつながる)
    dfx_smearN
      _lattice / _base     オブジェクトを囲むラティス (オブジェクトに追従。非表示)
    dfx_trailN
      _a / _b              帯の両端の点 (オブジェクトに追従するロケータ。動かして調整)
      _trail               帯のメッシュ (行ごとのジョイントにスキン)
      _joints              行ごとのジョイント (エクスプレッションが毎フレーム置く)

ベイクすると、残像はコピーの移動・回転・大きさ・表示・色、軌跡はジョイントの動き
(スキンは残す) のキーになり、FBX でもそのまま使える。スミアはラティスの点のキーになるが、
ラティスは FBX では書き出せない (Maya 内か Alembic で使う)。
"""
from __future__ import absolute_import

import math

from ..core import BaseTool, Param, ToolError

TAB = u"お化けブラー"
CATEGORY_CREATE = u"作成"
CATEGORY_EDIT = u"調整"

AFTERIMAGE = "afterimage"
SMEAR = "smear"
TRAIL = "trail"
GHOST_TYPES = (AFTERIMAGE, SMEAR, TRAIL)

TARGET_ATTR = "dfx_target"
TRAIL_POINT_ATTRS = ("dfx_point_a", "dfx_point_b")

SHAPE_RIBBON = u"帯"
SHAPE_TUBE = u"チューブ"

LATTICE_PADDING = 0.05   # スミアのラティスを、オブジェクトの大きさよりこの割合ずつ大きくする
MAX_GHOSTS = 30


# ---------------------------------------------------------------- 純粋な計算
def lerp(a, b, t):
    return a + (b - a) * t


def ghost_ratio(index, count):
    """index 番目 (0 始まり) の残像の、先頭 (0) 〜 最後 (1) の位置。"""
    return index / float(count - 1) if count > 1 else 0.0


def speed_factor(speed, min_speed, full_speed):
    """速さ → 残像の濃さの倍率 (出始める速さ以下 0、くっきり出る速さ以上 1)。"""
    if full_speed > min_speed:
        return min(1.0, max(0.0, (speed - min_speed) / (full_speed - min_speed)))
    return 1.0 if speed > min_speed else 0.0


def smear_weight(position, start, falloff):
    """進行方向の前 (0) 〜 後ろ (1) の位置 → 遅れの割合 (0〜1)。"""
    if start >= 0.999:
        return 0.0
    w = min(1.0, max(0.0, (position - start) / (1.0 - start)))
    return w ** max(falloff, 0.01)


def trail_taper(row, segments, width_start, width_end):
    return lerp(width_start, width_end, row / float(segments))


def lattice_index(i, j, k, s_div, t_div):
    """ラティスの点 pt[i][j][k] の controlPoints の番号。"""
    return i + j * s_div + k * s_div * t_div


def euler_xyz(x_axis, y_axis, z_axis):
    """正規直交の軸 (行ベクトル) → 回転順 xyz の角度 (度)。エクスプレッションと同じ式。"""
    h = math.hypot(x_axis[0], x_axis[1])
    ry = math.degrees(math.atan2(-x_axis[2], h))
    if h > 1e-6:
        rx = math.degrees(math.atan2(y_axis[2], z_axis[2]))
        rz = math.degrees(math.atan2(x_axis[1], x_axis[0]))
    else:
        rx = 0.0
        rz = math.degrees(math.atan2(-y_axis[0], y_axis[1]))
    return rx, ry, rz


def rotation_rows(rx, ry, rz):
    """回転順 xyz の角度 (度) → 行列の行 (X / Y / Z 軸)。euler_xyz の逆 (検証用)。"""
    x, y, z = (math.radians(a) for a in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = (math.cos(x), math.sin(x), math.cos(y), math.sin(y),
                              math.cos(z), math.sin(z))
    return ((cy * cz, cy * sz, -sy),
            (sx * sy * cz - cx * sz, sx * sy * sz + cx * cz, sx * cy),
            (cx * sy * cz + sx * sz, cx * sy * sz - sx * cz, cx * cy))


def _xf(p, m):
    """点 p (3) × 行列 m (16、行ベクトルの並び)。"""
    return (p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12],
            p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13],
            p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14])


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _mag(a):
    return math.sqrt(_dot(a, a))


def smear_points(mats, inv, center, size, divs, v, cap):
    """スミアのラティスの点 (正規化した位置 -0.5〜0.5、3 個ずつ)。エクスプレッションと同じ計算 (検証用)。

    mats: 今・1 サンプル前…の、オブジェクトのワールド行列 (16 個ずつ、サンプル数 + 1 個)。
    inv: 今のワールド逆行列。center / size: ラティスの中心と大きさ (オブジェクトの空間)。
    """
    s_div, t_div, u_div = divs
    n = s_div * t_div * u_div
    k_count = len(mats) - 1
    rest, local = [], []
    for idx in range(n):
        i, j, k = idx % s_div, (idx // s_div) % t_div, idx // (s_div * t_div)
        p = (i / float(s_div - 1) - 0.5, j / float(t_div - 1) - 0.5, k / float(u_div - 1) - 0.5)
        rest.append(p)
        local.append(tuple(c + q * s for c, q, s in zip(center, p, size)))
    length = max(0.0, v["frames"]) * max(0.0, v["amount"])
    if length <= 1e-6:
        return [x for p in rest for x in p]
    c0, ck = _xf(center, mats[0]), _xf(center, mats[k_count])
    move = _sub(c0, ck)
    if _mag(move) <= 1e-6:
        return [x for p in rest for x in p]
    back = tuple(-x / _mag(move) for x in move)
    p0 = [_xf(l, mats[0]) for l in local]
    proj = [_dot(_sub(p, c0), back) for p in p0]
    extent = max([1e-9] + [abs(x) for x in proj])
    out = []
    for idx in range(n):
        w = smear_weight((proj[idx] / extent + 1.0) / 2.0, v["start"], v["falloff"])
        sf = w * k_count
        s0 = min(int(math.floor(sf)), k_count - 1)
        fr = sf - s0
        qa, qb = _xf(local[idx], mats[s0]), _xf(local[idx], mats[s0 + 1])
        q = tuple(a + (b - a) * fr for a, b in zip(qa, qb))
        d = _sub(q, p0[idx])
        dm = _mag(d)
        if dm > cap:
            d = tuple(x * cap / dm for x in d)
        q = tuple(a + b for a, b in zip(p0[idx], d))
        lq = _xf(q, inv)
        out.extend((a - c) / s for a, c, s in zip(lq, center, size))
    return out


def trail_layout(shape, segments, sides):
    """軌跡のメッシュ: (頂点, 面の頂点数, 面の頂点番号, U, V, 面の UV 番号, 行ごとの頂点数)。

    行 j の頂点は x = j に置く (ジョイント j の位置)。帯は y = ±0.5、チューブは YZ 面の半径 0.5 の輪。
    U は根元 0 → 先 1 (ランプで色・不透明度を付ける)。
    """
    rows = segments + 1
    points, counts, connects, us, vs, uv_ids = [], [], [], [], [], []
    if shape == SHAPE_RIBBON:
        per_row = 2
        for j in range(rows):
            points += [(float(j), 0.5, 0.0), (float(j), -0.5, 0.0)]
            us += [j / float(segments)] * 2
            vs += [1.0, 0.0]
        for j in range(segments):
            a, b = j * 2, (j + 1) * 2
            face = [a, a + 1, b + 1, b]
            counts.append(4)
            connects += face
            uv_ids += face
        return points, counts, connects, us, vs, uv_ids, per_row
    per_row = sides
    for j in range(rows):
        for k in range(sides):
            ang = 2.0 * math.pi * k / sides
            points.append((float(j), 0.5 * math.cos(ang), 0.5 * math.sin(ang)))
        for k in range(sides + 1):  # UV は継ぎ目のために 1 列多い
            us.append(j / float(segments))
            vs.append(k / float(sides))
    for j in range(segments):
        for k in range(sides):
            k1 = (k + 1) % sides
            counts.append(4)
            connects += [j * sides + k, j * sides + k1, (j + 1) * sides + k1, (j + 1) * sides + k]
            uv_ids += [j * (sides + 1) + k, j * (sides + 1) + k + 1,
                       (j + 1) * (sides + 1) + k + 1, (j + 1) * (sides + 1) + k]
    return points, counts, connects, us, vs, uv_ids, per_row


# ---------------------------------------------------------------- ツール
class AfterimageTool(BaseTool):
    label = u"残像"
    tab = TAB
    category = CATEGORY_CREATE
    order = 850
    tooltip = (u"選択した (動いている) オブジェクトの少し前の位置に、コピーを何体も並べます。\n"
               u"後ろほど薄く (小さく) なり、速く動いた時だけ出ます。\n"
               u"オブジェクトの中のメッシュ全部をコピーします (スキンで変形するメッシュは今の形のまま)。")
    params = [
        Param("count", u"残像の数", 5, minimum=1, maximum=MAX_GHOSTS),
        Param("interval", u"間隔 (フレーム)", 1.0, minimum=0.05, step=0.5, decimals=2,
              tooltip=u"残像 1 体ごとに何フレーム前の位置にするか (小数も可)"),
        Param("opacity_start", u"先頭の不透明度", 0.6, minimum=0.0, maximum=1.0, step=0.1),
        Param("opacity_end", u"最後の不透明度", 0.1, minimum=0.0, maximum=1.0, step=0.1),
        Param("color_start", u"先頭の色", (0.55, 0.85, 1.0), kind="color"),
        Param("color_end", u"最後の色", (0.25, 0.35, 1.0), kind="color"),
        Param("scale_end", u"最後の大きさ (割合)", 1.0, minimum=0.0, maximum=2.0, step=0.1,
              tooltip=u"最後の残像の大きさ (先頭は 1)。オブジェクトのピボットを中心に縮む"),
        Param("min_speed", u"出始める速さ", 0.05, minimum=0.0, step=0.05,
              tooltip=u"1 フレームに動く距離がこれ以下の時は残像を出さない"),
        Param("full_speed", u"くっきり出る速さ", 0.3, minimum=0.0, step=0.1,
              tooltip=u"この速さ以上で、不透明度どおりにくっきり出る (間は徐々に濃くなる)"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return create_effects(cmds, mu, ctx, v, KINDS[AFTERIMAGE])


class SmearTool(BaseTool):
    label = u"スミア (引き伸ばし)"
    tab = TAB
    category = CATEGORY_CREATE
    order = 852
    tooltip = (u"選択した (動いている) オブジェクト自体を、進行方向の後ろへ引き伸ばします。\n"
               u"後ろ側ほど、少し前のフレームの位置に残ります (曲がって動けば曲がって伸びる)。\n"
               u"オブジェクトを囲むラティスで変形します。")
    params = [
        Param("frames", u"伸びるフレーム数", 3.0, minimum=0.0, step=0.5, decimals=2,
              tooltip=u"一番後ろが、何フレーム前の位置まで残るか"),
        Param("amount", u"強さ", 1.0, minimum=0.0, step=0.1,
              tooltip=u"伸びるフレーム数に掛ける倍率。キーを打てば一瞬だけ伸ばすこともできる"),
        Param("start", u"伸び始める位置", 0.3, minimum=0.0, maximum=0.99, step=0.1,
              tooltip=u"進行方向の前 (0) 〜 後ろ (1) のどこから伸び始めるか。前の部分は形が変わらない"),
        Param("falloff", u"カーブ", 1.0, minimum=0.1, maximum=5.0, step=0.1,
              tooltip=u"伸び方の偏り。1 で一様、大きいほど後ろの端だけが伸びる"),
        Param("max_stretch", u"最大の伸び (大きさの倍率)", 4.0, minimum=0.0, step=0.5,
              tooltip=u"伸びる距離の上限 (オブジェクトの大きさに対する倍率)。瞬間移動した時に伸びすぎないように"),
        Param("samples", u"なめらかさ", 6, minimum=1, maximum=30,
              tooltip=u"伸びるフレーム数の間を何回に分けて位置を読むか。曲がって動く時は多めに"),
        Param("divisions", u"ラティスの分割数", 6, minimum=2, maximum=12,
              tooltip=u"多いほど細かく曲がる (重くなる)"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return create_effects(cmds, mu, ctx, v, KINDS[SMEAR])


class TrailTool(BaseTool):
    label = u"軌跡"
    tab = TAB
    category = CATEGORY_CREATE
    order = 854
    tooltip = (u"選択した (動いている) オブジェクトの通った跡に、先が細くなる帯を残します。\n"
               u"帯の両端はルートの下のロケータ (_a / _b) で、オブジェクトに付いて動きます\n"
               u"(剣なら根元と先に動かす)。チューブは 2 点の間の距離が太さになります。")
    params = [
        Param("shape", u"形", SHAPE_RIBBON, kind="choice", choices=[SHAPE_RIBBON, SHAPE_TUBE]),
        Param("length", u"長さ (フレーム)", 8.0, minimum=0.0, step=1.0, decimals=2,
              tooltip=u"何フレーム前の位置までの跡を残すか"),
        Param("segments", u"分割数", 24, minimum=2, maximum=200,
              tooltip=u"長さ方向の分割数。曲がって動く時は多めに"),
        Param("sides", u"チューブの周りの分割数", 12, minimum=3, maximum=64),
        Param("width_start", u"根元の太さ (割合)", 1.0, minimum=0.0, maximum=2.0, step=0.1),
        Param("width_end", u"先の太さ (割合)", 0.0, minimum=0.0, maximum=2.0, step=0.1),
        Param("opacity_start", u"根元の不透明度", 0.8, minimum=0.0, maximum=1.0, step=0.1),
        Param("opacity_end", u"先の不透明度", 0.0, minimum=0.0, maximum=1.0, step=0.1),
        Param("color_start", u"根元の色", (1.0, 1.0, 1.0), kind="color"),
        Param("color_end", u"先の色", (0.4, 0.7, 1.0), kind="color"),
    ]

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return create_effects(cmds, mu, ctx, v, KINDS[TRAIL])


class GhostBlurUpdateTool(BaseTool):
    label = u"お化けブラーを更新"
    tab = TAB
    category = CATEGORY_EDIT
    order = 860
    tooltip = (u"選択したお化けブラー (ルートか、付けたオブジェクト) を、ルートのアトリビュートの値で作り直します。\n"
               u"「(更新)」の付いた項目を変えた時や、オブジェクトの形・大きさを変えた時に使います。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        return rebuild_effects(cmds, mu, ctx, selected_roots(cmds, mu, u"更新する"))


class GhostBlurSavePresetTool(BaseTool):
    label = u"お化けブラーのパラメータを保存"
    tab = TAB
    category = CATEGORY_EDIT
    order = 862
    undoable = False
    tooltip = u"選択したお化けブラー 1 つのパラメータをプリセットファイル (.json) に保存します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = selected_roots(cmds, mu, u"保存する")
        if len(roots) > 1:
            raise ToolError(u"保存するお化けブラーを 1 つだけ選択してください (現在 %d 個)" % len(roots))
        kind = kind_of(cmds, mu, roots[0])
        return mu.save_preset_dialog(ctx, roots, kind.effect_type, kind.stored_params(),
                                     self.label, kind.noun)


class GhostBlurLoadPresetTool(BaseTool):
    label = u"お化けブラーのパラメータを読み込み"
    tab = TAB
    category = CATEGORY_EDIT
    order = 864
    tooltip = (u"プリセットファイル (.json) のパラメータを選択したお化けブラーに設定し、作り直します。\n"
               u"同じ種類 (残像・スミア・軌跡) のプリセットだけ読み込めます。")

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        roots = selected_roots(cmds, mu, u"読み込み先の")
        kinds = set(kind_of(cmds, mu, r).effect_type for r in roots)
        if len(kinds) > 1:
            raise ToolError(u"読み込み先は同じ種類 (残像・スミア・軌跡のどれか) だけ選択してください")
        kind = KINDS[kinds.pop()]
        values = mu.load_preset_dialog(ctx, kind.effect_type, kind.stored_params(), self.label)
        if values is None:
            return None
        return rebuild_effects(cmds, mu, ctx, roots, overrides=values)


# ---------------------------------------------------------------- 種類
class Kind(object):
    def __init__(self, effect_type, tool, root_name, noun, live, rebuild, build,
                 need_mesh, one_per_target=False):
        self.effect_type = effect_type
        self.tool = tool
        self.root_name = root_name
        self.noun = noun
        self.live = live
        self.rebuild = rebuild
        self.build = build
        self.need_mesh = need_mesh
        self.one_per_target = one_per_target

    def stored_params(self):
        by_name = dict((p.name, p) for p in self.tool.params)
        return [by_name[n] for n in self.live + self.rebuild]


def kind_of(cmds, mu, root):
    return KINDS[cmds.getAttr(root + "." + mu.EFFECT_TYPE_ATTR)]


# ---------------------------------------------------------------- 共通の処理
def _short(node):
    return node.split("|")[-1]


def _safe(name):
    return _short(name).replace(":", "_")


def ghost_roots(cmds, mu):
    return [r for r in mu.all_effect_roots()
            if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in GHOST_TYPES]


def stored_target(cmds, root):
    if not cmds.attributeQuery(TARGET_ATTR, node=root, exists=True):
        return None
    conns = cmds.listConnections(root + "." + TARGET_ATTR, source=True, destination=False) or []
    return cmds.ls(conns[0], long=True)[0] if conns else None


def _in_ghost_root(cmds, mu, node):
    parts = node.split("|")
    for i in range(len(parts), 1, -1):
        candidate = "|".join(parts[:i])
        if (cmds.attributeQuery(mu.EFFECT_TYPE_ATTR, node=candidate, exists=True)
                and cmds.getAttr(candidate + "." + mu.EFFECT_TYPE_ATTR) in GHOST_TYPES):
            return True
    return False


def mesh_shapes(cmds, target):
    """target (と子孫) の、表示されるメッシュのシェイプ。"""
    shapes = cmds.listRelatives(target, allDescendents=True, type="mesh", fullPath=True) or []
    return [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]


def resolve_targets(cmds, mu, kind):
    """選択中の transform (シェイプを選んだらその親)。お化けブラー自身は除く。"""
    targets = []
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if not cmds.objectType(node, isAType="transform"):
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            node = parents[0] if parents else None
        if not node or node in targets or _in_ghost_root(cmds, mu, node):
            continue
        targets.append(node)
    if not targets:
        raise ToolError(u"%sを付ける (動いている) オブジェクトを選択してください" % kind.noun)
    for target in targets:
        if kind.need_mesh and not mesh_shapes(cmds, target):
            raise ToolError(u"%s にはメッシュがありません (%sはメッシュにだけ付けられます)"
                            % (_short(target), kind.noun))
        if kind.one_per_target:
            for other in ghost_roots(cmds, mu):
                if (cmds.getAttr(other + "." + mu.EFFECT_TYPE_ATTR) == kind.effect_type
                        and stored_target(cmds, other) == target):
                    raise ToolError(u"%s には既に%s (%s) が付いています"
                                    % (_short(target), kind.noun, _short(other)))
    return targets


def roots_of_selected_targets(cmds, mu, effect_types=GHOST_TYPES):
    """選択したオブジェクト (シェイプならその親) に付いているお化けブラー。"""
    selected = set()
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        selected.add(node)
        selected.update(cmds.listRelatives(node, parent=True, fullPath=True) or [])
    return [r for r in ghost_roots(cmds, mu)
            if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in effect_types
            and stored_target(cmds, r) in selected]


def selected_roots(cmds, mu, purpose):
    roots = [r for r in mu.selected_effect_roots()
             if cmds.getAttr(r + "." + mu.EFFECT_TYPE_ATTR) in GHOST_TYPES]
    roots += [r for r in roots_of_selected_targets(cmds, mu) if r not in roots]
    if not roots:
        raise ToolError(u"%sお化けブラー (dfx_afterimageN / dfx_smearN / dfx_trailN、"
                        u"または付けたオブジェクト) を選択してください" % purpose)
    return roots


def create_effects(cmds, mu, ctx, v, kind):
    """選択したオブジェクト 1 つにつき 1 つ作る。"""
    targets = resolve_targets(cmds, mu, kind)
    roots = []
    for target in targets:
        root = cmds.group(empty=True, name=kind.root_name, world=True)
        root = cmds.ls(root, long=True)[0]
        mu.tag(root)
        mu.mark_effect_root(root, kind.effect_type)
        mu.add_param_attrs(root, kind.stored_params(), v, kind.live)
        cmds.addAttr(root, longName=TARGET_ATTR, attributeType="message")
        cmds.connectAttr(target + ".message", root + "." + TARGET_ATTR)
        mu.lock_transform(root)
        if kind.effect_type == TRAIL:
            _create_trail_points(cmds, mu, root, target)
        kind.build(cmds, mu, root, v, target)
        _log(ctx, root, target, kind, v)
        roots.append(root)
    cmds.select(roots)
    return roots[0] if len(roots) == 1 else roots


def _log(ctx, root, target, kind, v):
    if kind.effect_type == AFTERIMAGE:
        detail = u"%d 体 (%s フレームおき)" % (v["count"], v["interval"])
    elif kind.effect_type == SMEAR:
        detail = u"%s フレーム分伸びる" % v["frames"]
    else:
        detail = u"%s / %s フレーム分" % (v["shape"], v["length"])
    ctx.log(u"  %s: %s に%s %s" % (_short(root), _short(target), kind.noun, detail))


def rebuild_effects(cmds, mu, ctx, roots, overrides=None):
    jobs = []
    for root in roots:
        if mu.is_baked(root):
            raise ToolError(u"%s はベイク済みのため作り直せません (Ctrl+Z でベイク前に戻せます)"
                            % _short(root))
        target = stored_target(cmds, root)
        if not target:
            raise ToolError(u"%s を付けたオブジェクトが見つかりません (削除された可能性があります)"
                            % _short(root))
        kind = kind_of(cmds, mu, root)
        if kind.need_mesh and not mesh_shapes(cmds, target):
            raise ToolError(u"%s にはメッシュがありません" % _short(target))
        jobs.append((root, target, kind))
    for root, target, kind in jobs:
        params = kind.stored_params()
        values = mu.read_param_values(root, params)
        driven = mu.driven_params(root, params) if overrides else []
        if overrides:
            values.update((k, v) for k, v in overrides.items() if k not in driven)
        mu.delete_owned(root)
        mu.sync_param_attrs(root, params, values, kind.live,
                            keep=(TARGET_ATTR,) + TRAIL_POINT_ATTRS)
        if overrides:
            mu.write_param_values(root, params, values, skip=driven)
        kind.build(cmds, mu, root, values, target)
        _log(ctx, root, target, kind, values)
        ignored = [p.label for p in params if p.name in driven and p.name in overrides]
        if ignored:
            ctx.log(u"  ※ キーが打たれているため変更しませんでした: %s" % u", ".join(ignored))
    result = [job[0] for job in jobs]
    cmds.select(result)
    return result


def _finish(cmds, mu, root, owned):
    owned = [n for n in owned if cmds.objExists(n)]
    mu.tag(*owned)
    mu.own(root, *owned)


def local_bbox(cmds, target):
    """target のメッシュ全部を囲む箱 (target の空間)。(中心, 大きさ)。メッシュが無ければ None。"""
    from maya.api import OpenMaya as om
    inv = om.MMatrix(cmds.getAttr(target + ".worldInverseMatrix[0]"))
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for shape in mesh_shapes(cmds, target):
        sel = om.MSelectionList()
        sel.add(shape)
        for p in om.MFnMesh(sel.getDagPath(0)).getPoints(om.MSpace.kWorld):
            q = p * inv
            for a in range(3):
                lo[a] = min(lo[a], q[a])
                hi[a] = max(hi[a], q[a])
    if lo[0] == float("inf"):
        return None
    return (tuple((a + b) / 2.0 for a, b in zip(lo, hi)),
            tuple(b - a for a, b in zip(lo, hi)))


def _follow(cmds, node, target):
    """node (ルートの子) を target に付いて動かす (offsetParentMatrix)。"""
    cmds.connectAttr(target + ".worldMatrix[0]", node + ".offsetParentMatrix", force=True)


# MEL の断片 ------------------------------------------------------------
def _mel_xf(px, py, pz, m, o="0"):
    """点 × 行列 (MEL の float 配列 m、先頭 o) の MEL の vector 式。"""
    def e(i):
        return "%s[%s + %d]" % (m, o, i)
    return ("<<{px} * {m0} + {py} * {m4} + {pz} * {m8} + {m12}, "
            "{px} * {m1} + {py} * {m5} + {pz} * {m9} + {m13}, "
            "{px} * {m2} + {py} * {m6} + {pz} * {m10} + {m14}>>").format(
                px=px, py=py, pz=pz, **dict(("m%d" % i, e(i)) for i in range(16)))


def _mel_euler(x, y, z):
    """正規直交の軸 x / y / z (MEL の vector 変数) → $rx $ry $rz (euler_xyz と同じ式)。"""
    return u"""
    float $h = sqrt({x}.x * {x}.x + {x}.y * {x}.y);
    $ry = atan2d(-{x}.z, $h);
    if ($h > 1e-6) {{ $rx = atan2d({y}.z, {z}.z); $rz = atan2d({x}.y, {x}.x); }}
    else {{ $rx = 0.0; $rz = atan2d(-{y}.x, {y}.y); }}""".format(x=x, y=y, z=z)


def _mel_decompose(m):
    """行列 m (float[16]) → $tr / $rx $ry $rz / $sx $sy $sz (euler_xyz と同じ式)。"""
    return u"""
    vector $tr = <<{m}[12], {m}[13], {m}[14]>>;
    vector $X = <<{m}[0], {m}[1], {m}[2]>>;
    vector $Y = <<{m}[4], {m}[5], {m}[6]>>;
    vector $Z = <<{m}[8], {m}[9], {m}[10]>>;
    float $sx = mag($X); float $sy = mag($Y); float $sz = mag($Z);
    if ($sx > 1e-12) $X = $X / $sx;
    if ($sy > 1e-12) $Y = $Y / $sy;
    if ($sz > 1e-12) $Z = $Z / $sz;
    if (dot(cross($X, $Y), $Z) < 0) {{ $sz = -$sz; $Z = $Z * -1.0; }}
    float $rx; float $ry; float $rz;""".format(m=m) + _mel_euler("$X", "$Y", "$Z")


# ---------------------------------------------------------------- 残像
def build_afterimage(cmds, mu, root, v, target):
    prefix = _short(root)
    owned = []
    parts = []
    for shape in mesh_shapes(cmds, target):
        parent = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
        parts.append((parent, shape))
    count = int(v["count"])
    for i in range(count):
        grp = cmds.group(empty=True, name="%s_ghost_%02d" % (prefix, i + 1), parent=root)
        grp = cmds.ls(grp, long=True)[0]
        shader, sg = mu.create_surface_shader("%s_ghost_%02d_mat" % (prefix, i + 1), (1, 1, 1))
        owned.extend([grp, shader, sg])
        made = []
        for part, shape in parts:
            name = "%s_ghost_%02d_%s" % (prefix, i + 1, _safe(part))
            node = cmds.createNode("transform", name=name, parent=grp)
            mesh = cmds.createNode("mesh", name=name + "Shape", parent=node)
            cmds.connectAttr(shape + ".outMesh", mesh + ".inMesh")
            for a in ("castsShadows", "receiveShadows"):
                cmds.setAttr(mesh + "." + a, 0)
            node = cmds.ls(node, long=True)[0]
            made.append((node, part))
        cmds.sets([n for n, _ in made], edit=True, forceElement=sg)
        expr = cmds.expression(name="%s_ghost_%02d_motion" % (prefix, i + 1), alwaysEvaluate=True,
                               unitConversion="all",
                               string=_afterimage_expression(mu, root, grp, shader, made,
                                                             target, i, count))
        owned.append(expr)
    _finish(cmds, mu, root, owned)


def _find_target(mu, root, target):
    """エクスプレッションで、付けたオブジェクトの名前を $tg に入れるコード。"""
    return mu.mel_find_node("$tg", target, "%s.%s" % (root, TARGET_ATTR))


def _afterimage_expression(mu, root, grp, shader, parts, target, index, count):
    r, g, sh = _short(root), _short(grp), _short(shader)
    code = [_find_target(mu, root, target), u"""
float $dt = max({r}.dfx_interval, 0.001) * {k};
float $t = frame - $dt;
float $now[] = `getAttr ($tg + ".worldMatrix[0]")`;
float $was[] = `getAttr -time $t ($tg + ".worldMatrix[0]")`;
float $spd = mag(<<$now[12] - $was[12], $now[13] - $was[13], $now[14] - $was[14]>>) / $dt;
float $lo = {r}.dfx_min_speed;
float $hi = {r}.dfx_full_speed;
float $f = 0.0;
if ($hi > $lo) $f = clamp(0.0, 1.0, ($spd - $lo) / ($hi - $lo));
else if ($spd > $lo) $f = 1.0;
float $a = {ratio};
float $op = ({r}.dfx_opacity_start + ({r}.dfx_opacity_end - {r}.dfx_opacity_start) * $a) * $f;
float $sc = 1.0 + ({r}.dfx_scale_end - 1.0) * $a;
{sh}.outColorR = ({r}.dfx_color_startR + ({r}.dfx_color_endR - {r}.dfx_color_startR) * $a) * $op;
{sh}.outColorG = ({r}.dfx_color_startG + ({r}.dfx_color_endG - {r}.dfx_color_startG) * $a) * $op;
{sh}.outColorB = ({r}.dfx_color_startB + ({r}.dfx_color_endB - {r}.dfx_color_startB) * $a) * $op;
{sh}.outTransparencyR = 1.0 - $op;
{sh}.outTransparencyG = 1.0 - $op;
{sh}.outTransparencyB = 1.0 - $op;
{g}.visibility = $op > 0.001;
""".format(r=r, k=float(index + 1), ratio=repr(ghost_ratio(index, count)), g=g, sh=sh)]
    for node, part in parts:
        n = _short(node)
        code.append(u"""{{
    string $p = "{path}";
    if (!`objExists $p`) $p = $tg + "{suffix}";
    float $m[] = `getAttr -time $t ($p + ".worldMatrix[0]")`;{dec}
    {n}.translateX = $tr.x; {n}.translateY = $tr.y; {n}.translateZ = $tr.z;
    {n}.rotateX = $rx; {n}.rotateY = $ry; {n}.rotateZ = $rz;
    {n}.scaleX = $sx * $sc; {n}.scaleY = $sy * $sc; {n}.scaleZ = $sz * $sc;
}}
""".format(path=part, suffix=part[len(target):], n=n, dec=_mel_decompose("$m")))
    return u"".join(code)


# ---------------------------------------------------------------- スミア
def build_smear(cmds, mu, root, v, target):
    prefix = _short(root)
    center, size = local_bbox(cmds, target)
    obj_size = max(size)
    size = tuple(max(s, obj_size * 0.01, 1e-4) * (1.0 + 2.0 * LATTICE_PADDING) for s in size)
    n = int(v["divisions"])
    shapes = mesh_shapes(cmds, target)
    ffd, lattice, base = cmds.lattice(shapes, divisions=(n, n, n), objectCentered=True,
                                      name=prefix + "_ffd")
    lattice = cmds.rename(lattice, prefix + "_lattice")
    base = cmds.rename(base, prefix + "_base")
    cmds.setAttr(ffd + ".local", 0)
    nodes = []
    for node in (lattice, base):
        node = cmds.ls(cmds.parent(node, root)[0], long=True)[0]
        _follow(cmds, node, target)
        cmds.setAttr(node + ".translate", *center, type="double3")
        cmds.setAttr(node + ".rotate", 0, 0, 0, type="double3")
        cmds.setAttr(node + ".scale", *size, type="double3")
        cmds.setAttr(node + ".visibility", 0)
        nodes.append(node)
    lattice_shape = cmds.listRelatives(nodes[0], shapes=True, fullPath=True)[0]
    expr = cmds.expression(name=prefix + "_smear", alwaysEvaluate=True, unitConversion="all",
                           string=_find_target(mu, root, target)
                           + _smear_expression(prefix, _short(lattice_shape), center, size,
                                               (n, n, n), obj_size))
    _finish(cmds, mu, root, [ffd] + nodes + [expr])


def _smear_expression(r, lat, center, size, divs, obj_size):
    s_div, t_div, u_div = divs
    count = s_div * t_div * u_div
    head = u"""
int $K = max(1, {r}.dfx_samples);
float $len = max(0.0, {r}.dfx_frames) * max(0.0, {r}.dfx_amount);
float $dt = $len / $K;
float $cx = {cx}; float $cy = {cy}; float $cz = {cz};
float $wx = {wx}; float $wy = {wy}; float $wz = {wz};
float $cap = max(0.0, {r}.dfx_max_stretch) * {obj};
float $out[];
float $L[];
int $i;
for ($i = 0; $i < {n}; $i++) {{
    int $a = $i % {S};
    int $b = ($i / {S}) % {T};
    int $c = $i / {ST};
    $out[$i * 3] = $a / {S1} - 0.5;
    $out[$i * 3 + 1] = $b / {T1} - 0.5;
    $out[$i * 3 + 2] = $c / {U1} - 0.5;
    $L[$i * 3] = $cx + $out[$i * 3] * $wx;
    $L[$i * 3 + 1] = $cy + $out[$i * 3 + 1] * $wy;
    $L[$i * 3 + 2] = $cz + $out[$i * 3 + 2] * $wz;
}}
if ($len > 1e-6) {{
    float $M[];
    int $s;
    for ($s = 0; $s <= $K; $s++) {{
        float $m[] = `getAttr -time (frame - $s * $dt) ($tg + ".worldMatrix[0]")`;
        int $e;
        for ($e = 0; $e < 16; $e++) $M[$s * 16 + $e] = $m[$e];
    }}
    float $inv[] = `getAttr ($tg + ".worldInverseMatrix[0]")`;
    int $oK = $K * 16;
    vector $C0 = {c0};
    vector $CK = {ck};
    vector $mv = $C0 - $CK;
    if (mag($mv) > 1e-6) {{
        vector $back = unit($CK - $C0);
        vector $P0[];
        float $proj[];
        float $ext = 1e-9;
        for ($i = 0; $i < {n}; $i++) {{
            float $px = $L[$i * 3]; float $py = $L[$i * 3 + 1]; float $pz = $L[$i * 3 + 2];
            $P0[$i] = {p0};
            $proj[$i] = dot($P0[$i] - $C0, $back);
            $ext = max($ext, abs($proj[$i]));
        }}
        float $st = {r}.dfx_start;
        float $fo = max({r}.dfx_falloff, 0.01);
        for ($i = 0; $i < {n}; $i++) {{
            float $px = $L[$i * 3]; float $py = $L[$i * 3 + 1]; float $pz = $L[$i * 3 + 2];
            float $pos = ($proj[$i] / $ext + 1.0) / 2.0;
            float $w = 0.0;
            if ($st < 0.999) $w = pow(clamp(0.0, 1.0, ($pos - $st) / (1.0 - $st)), $fo);
            float $sf = $w * $K;
            int $s0 = floor($sf);
            if ($s0 > $K - 1) $s0 = $K - 1;
            float $fr = $sf - $s0;
            int $oa = $s0 * 16;
            int $ob = $oa + 16;
            vector $qa = {qa};
            vector $qb = {qb};
            vector $d = $qa + ($qb - $qa) * $fr - $P0[$i];
            float $dm = mag($d);
            if ($dm > $cap) {{
                if ($dm > 1e-12) $d = $d * ($cap / $dm);
            }}
            vector $q = $P0[$i] + $d;
            float $qx = $q.x; float $qy = $q.y; float $qz = $q.z;
            vector $lq = {lq};
            $out[$i * 3] = ($lq.x - $cx) / $wx;
            $out[$i * 3 + 1] = ($lq.y - $cy) / $wy;
            $out[$i * 3 + 2] = ($lq.z - $cz) / $wz;
        }}
    }}
}}
""".format(r=r, n=count, S=s_div, T=t_div, ST=s_div * t_div,
           S1=repr(float(s_div - 1)), T1=repr(float(t_div - 1)), U1=repr(float(u_div - 1)),
           cx=repr(center[0]), cy=repr(center[1]), cz=repr(center[2]),
           wx=repr(size[0]), wy=repr(size[1]), wz=repr(size[2]), obj=repr(float(obj_size)),
           c0=_mel_xf("$cx", "$cy", "$cz", "$M"), ck=_mel_xf("$cx", "$cy", "$cz", "$M", "$oK"),
           p0=_mel_xf("$px", "$py", "$pz", "$M"),
           qa=_mel_xf("$px", "$py", "$pz", "$M", "$oa"),
           qb=_mel_xf("$px", "$py", "$pz", "$M", "$ob"),
           lq=_mel_xf("$qx", "$qy", "$qz", "$inv"))
    writes = [u"{lat}.controlPoints[{i}].xValue = $out[{a}];\n"
              u"{lat}.controlPoints[{i}].yValue = $out[{b}];\n"
              u"{lat}.controlPoints[{i}].zValue = $out[{c}];\n".format(
                  lat=lat, i=i, a=i * 3, b=i * 3 + 1, c=i * 3 + 2) for i in range(count)]
    return head + u"".join(writes)


def smear_cap(v, obj_size):
    return max(0.0, v["max_stretch"]) * obj_size


# ---------------------------------------------------------------- 軌跡
def _create_trail_points(cmds, mu, root, target):
    """帯の両端のロケータ (作成時だけ。更新しても位置はそのまま)。"""
    box = local_bbox(cmds, target)
    center, size = box if box else ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    half = (size[1] or max(size) or 1.0) / 2.0
    prefix = _short(root)
    for suffix, attr, dy in (("a", TRAIL_POINT_ATTRS[0], half), ("b", TRAIL_POINT_ATTRS[1], -half)):
        loc = cmds.spaceLocator(name="%s_%s" % (prefix, suffix))[0]
        loc = cmds.ls(cmds.parent(loc, root, relative=True)[0], long=True)[0]
        _follow(cmds, loc, target)
        cmds.setAttr(loc + ".translate", center[0], center[1] + dy, center[2], type="double3")
        shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]
        cmds.setAttr(shape + ".overrideEnabled", 1)
        cmds.setAttr(shape + ".overrideColor", 17)  # 黄
        for a in "XYZ":
            cmds.setAttr(shape + ".localScale" + a, max(half * 0.3, 0.05))
        mu.tag(loc)
        cmds.addAttr(root, longName=attr, attributeType="message")
        cmds.connectAttr(loc + ".message", root + "." + attr)


def trail_points(cmds, root):
    result = []
    for attr, label in zip(TRAIL_POINT_ATTRS, ("_a", "_b")):
        conns = cmds.listConnections(root + "." + attr, source=True, destination=False) or []
        if not conns:
            raise ToolError(u"%s の帯の端のロケータ (%s) が見つかりません" % (_short(root), label))
        result.append(cmds.ls(conns[0], long=True)[0])
    return result


def build_trail(cmds, mu, root, v, target):
    from maya.api import OpenMaya as om

    def attr(name):
        return "%s.%s%s" % (root, mu.PARAM_PREFIX, name)

    prefix = _short(root)
    owned = []
    point_a, point_b = trail_points(cmds, root)
    segments = int(v["segments"])
    sides = int(v["sides"])
    shape = v["shape"]

    # メッシュ (行 j の頂点は x = j)
    points, counts, connects, us, vs, uv_ids, per_row = trail_layout(shape, segments, sides)
    xform = cmds.createNode("transform", name=prefix + "_trail", parent=root)
    xform = cmds.ls(xform, long=True)[0]
    sel = om.MSelectionList()
    sel.add(xform)
    fn = om.MFnMesh()
    fn.create([om.MPoint(*p) for p in points], counts, connects, us, vs, sel.getDependNode(0))
    fn.assignUVs(counts, uv_ids)
    mesh = cmds.listRelatives(xform, shapes=True, fullPath=True)[0]
    mesh = cmds.rename(mesh, prefix + "_trailShape")
    mesh = cmds.listRelatives(xform, shapes=True, fullPath=True)[0]
    for a in ("castsShadows", "receiveShadows"):
        cmds.setAttr(mesh + "." + a, 0)
    owned.append(xform)

    # 行ごとのジョイント
    jgrp = cmds.group(empty=True, name=prefix + "_joints", parent=root)
    jgrp = cmds.ls(jgrp, long=True)[0]
    owned.append(jgrp)
    joints = []
    for j in range(segments + 1):
        jnt = cmds.createNode("joint", name="%s_jnt_%03d" % (prefix, j), parent=jgrp)
        cmds.setAttr(jnt + ".translate", float(j), 0, 0, type="double3")
        cmds.setAttr(jnt + ".drawStyle", 2)  # 表示しない
        joints.append(cmds.ls(jnt, long=True)[0])
    skin = cmds.skinCluster(joints, xform, toSelectedBones=True, bindMethod=0, maximumInfluences=1,
                            normalizeWeights=1, name=prefix + "_skin")[0]
    for j, jnt in enumerate(joints):
        first = j * per_row
        cmds.skinPercent(skin, "%s.vtx[%d:%d]" % (xform, first, first + per_row - 1),
                         transformValue=[(jnt, 1.0)])
    owned.append(skin)

    # マテリアル (根元 → 先の色・不透明度は U 方向のランプ)
    shader, sg = mu.create_surface_shader(prefix + "_mat", (1, 1, 1))
    place = cmds.shadingNode("place2dTexture", asUtility=True, name=prefix + "_place")
    ramps = []
    for kind in ("color", "transparency"):
        ramp = cmds.shadingNode("ramp", asTexture=True, name="%s_%sRamp" % (prefix, kind))
        cmds.setAttr(ramp + ".type", 1)  # U ランプ
        cmds.setAttr(ramp + ".interpolation", 1)  # 直線
        cmds.connectAttr(place + ".outUV", ramp + ".uvCoord")
        cmds.connectAttr(place + ".outUvFilterSize", ramp + ".uvFilterSize")
        for idx, pos in ((0, 0.0), (1, 1.0)):
            cmds.setAttr("%s.colorEntryList[%d].position" % (ramp, idx), pos)
        ramps.append(ramp)
    owned.extend([shader, sg, place] + ramps)
    for idx, end in ((0, "start"), (1, "end")):
        # 色は 色 × 不透明度 (乗算済み)
        cmds.connectAttr(mu.premultiply(attr("color_" + end), attr("opacity_" + end),
                                        "%s_premult_%s" % (prefix, end), owned),
                         "%s.colorEntryList[%d].color" % (ramps[0], idx))
        inv = cmds.createNode("reverse", name="%s_transparency_%s" % (prefix, end))
        for axis in "XYZ":
            cmds.connectAttr(attr("opacity_" + end), inv + ".input" + axis)
        cmds.connectAttr(inv + ".output", "%s.colorEntryList[%d].color" % (ramps[1], idx))
        owned.append(inv)
    cmds.connectAttr(ramps[0] + ".outColor", shader + ".outColor", force=True)
    cmds.connectAttr(ramps[1] + ".outColor", shader + ".outTransparency", force=True)
    cmds.sets(xform, edit=True, forceElement=sg)

    expr = cmds.expression(name=prefix + "_trailMotion", alwaysEvaluate=True,
                           unitConversion="all",
                           string=mu.mel_find_node("$ga", point_a,
                                                   "%s.%s" % (root, TRAIL_POINT_ATTRS[0]))
                           + mu.mel_find_node("$gb", point_b,
                                              "%s.%s" % (root, TRAIL_POINT_ATTRS[1]))
                           + _trail_expression(prefix, _short(xform),
                                                    [_short(j) for j in joints],
                                                    shape == SHAPE_TUBE))
    owned.append(expr)
    _finish(cmds, mu, root, owned)


def _mel_ortho(dst, primary, cand):
    """dst = cand から primary の成分を除いて正規化 (平行なら適当な直交する向き)。"""
    return u"""
        vector {d} = {c} - dot({c}, {p}) * {p};
        if (mag({d}) < 1e-6) {d} = cross({p}, <<0, 0, 1>>);
        if (mag({d}) < 1e-6) {d} = cross({p}, <<1, 0, 0>>);
        {d} = unit({d});""".format(d=dst, p=primary, c=cand)


def _trail_expression(r, mesh, joints, tube):
    segments = len(joints) - 1
    if tube:
        # 輪の回り方は前の行から引き継ぐ (オブジェクトが回っても輪がねじれない)。根元だけ 2 点の向き
        frame_code = u"""
        vector $X = $prevX;
        if (mag($T) > 1e-9) $X = unit($T);
        vector $up = $prevY;
        if ($j == 0) $up = $Y;""" + _mel_ortho("$Yn", "$X", "$up")
    else:
        frame_code = u"""
        vector $Yn = <<0, 1, 0>>;
        if ($wid > 1e-9) $Yn = unit($Y);
        vector $cand = $T;
        if (mag($T) <= 1e-9) $cand = $prevX;""" + _mel_ortho("$X", "$Yn", "$cand")
    head = u"""
float $len = max(0.0, {r}.dfx_length);
float $dt = $len / {S};
vector $A[]; vector $B[]; vector $Mid[];
int $j;
for ($j = 0; $j <= {S}; $j++) {{
    float $t = frame - $j * $dt;
    float $pa[] = `getAttr -time $t ($ga + ".worldMatrix[0]")`;
    float $pb[] = `getAttr -time $t ($gb + ".worldMatrix[0]")`;
    $A[$j] = <<$pa[12], $pa[13], $pa[14]>>;
    $B[$j] = <<$pb[12], $pb[13], $pb[14]>>;
    $Mid[$j] = ($A[$j] + $B[$j]) * 0.5;
}}
float $path = 0.0;
for ($j = 0; $j < {S}; $j++) $path += mag($Mid[$j] - $Mid[$j + 1]);
float $o[];
vector $prevX = <<1, 0, 0>>;
vector $prevY = <<0, 1, 0>>;
for ($j = 0; $j <= {S}; $j++) {{
    int $j0 = $j - 1;
    if ($j0 < 0) $j0 = 0;
    int $j1 = $j + 1;
    if ($j1 > {S}) $j1 = {S};
    vector $T = $Mid[$j0] - $Mid[$j1];
    vector $Y = $B[$j] - $A[$j];
    float $wid = mag($Y);
    {{{frame}
        vector $Z = cross($X, $Yn);
        $prevX = $X;
        $prevY = $Yn;
        float $rx; float $ry; float $rz;{euler}
        float $sc = $wid * ({r}.dfx_width_start + ({r}.dfx_width_end - {r}.dfx_width_start) * $j / {Sf});
        vector $mj = $Mid[$j];
        $o[$j * 7] = $mj.x;
        $o[$j * 7 + 1] = $mj.y;
        $o[$j * 7 + 2] = $mj.z;
        $o[$j * 7 + 3] = $rx;
        $o[$j * 7 + 4] = $ry;
        $o[$j * 7 + 5] = $rz;
        $o[$j * 7 + 6] = $sc;
    }}
}}
{mesh}.visibility = $path > 1e-4;
""".format(r=r, S=segments, Sf=repr(float(segments)),
           frame=frame_code, euler=_mel_euler("$X", "$Yn", "$Z"), mesh=mesh)
    writes = []
    for j, jnt in enumerate(joints):
        o = j * 7
        writes.append(
            u"{n}.translateX = $o[{a}]; {n}.translateY = $o[{b}]; {n}.translateZ = $o[{c}];\n"
            u"{n}.rotateX = $o[{d}]; {n}.rotateY = $o[{e}]; {n}.rotateZ = $o[{f}];\n"
            u"{n}.scaleX = $o[{g}]; {n}.scaleY = $o[{g}]; {n}.scaleZ = $o[{g}];\n".format(
                n=jnt, a=o, b=o + 1, c=o + 2, d=o + 3, e=o + 4, f=o + 5, g=o + 6))
    return head + u"".join(writes)


# ---------------------------------------------------------------- 種類の一覧
KINDS = {
    AFTERIMAGE: Kind(AFTERIMAGE, AfterimageTool, "dfx_afterimage#", u"残像",
                     live=("interval", "opacity_start", "opacity_end", "color_start", "color_end",
                           "scale_end", "min_speed", "full_speed"),
                     rebuild=("count",), build=build_afterimage, need_mesh=True),
    SMEAR: Kind(SMEAR, SmearTool, "dfx_smear#", u"スミア",
                live=("frames", "amount", "start", "falloff", "max_stretch", "samples"),
                rebuild=("divisions",), build=build_smear, need_mesh=True, one_per_target=True),
    TRAIL: Kind(TRAIL, TrailTool, "dfx_trail#", u"軌跡",
                live=("length", "width_start", "width_end", "opacity_start", "opacity_end",
                      "color_start", "color_end"),
                rebuild=("shape", "segments", "sides"), build=build_trail, need_mesh=False),
}
