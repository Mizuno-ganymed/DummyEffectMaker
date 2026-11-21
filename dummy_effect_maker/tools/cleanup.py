# -*- coding: utf-8 -*-
"""このツール群で作成したノードを一括削除する。"""
from __future__ import absolute_import

from ..core import BaseTool


class DeleteAllDummiesTool(BaseTool):
    label = u"ダミーエフェクトを全削除"
    category = u"ユーティリティ"
    order = 900
    tooltip = u"Dummy Effect Maker で作成したノード (グループ・マテリアル等) を全て削除します。"

    def run(self, ctx, **v):
        from maya import cmds
        from . import _maya_utils as mu

        nodes = mu.list_tagged()
        if not nodes:
            ctx.log(u"  削除対象はありません")
            return 0
        answer = cmds.confirmDialog(
            title=self.label, message=u"%d 個のノードを削除します。よろしいですか？" % len(nodes),
            button=[u"削除", u"キャンセル"], defaultButton=u"キャンセル",
            cancelButton=u"キャンセル", dismissString=u"キャンセル")
        if answer != u"削除":
            ctx.log(u"  キャンセルしました")
            return 0

        # 先に DAG を消すと子や接続ノードが連鎖で消えるので、残ったものだけ消す
        dag = [n for n in nodes if cmds.objectType(n, isAType="dagNode")]
        if dag:
            cmds.delete(dag)
        rest = [n for n in nodes if cmds.objExists(n)]
        if rest:
            cmds.delete(rest)
        ctx.log(u"  %d 個のノードを削除しました" % len(nodes))
        return len(nodes)
