# Dummy Effect Maker

Autodesk Maya でダミーエフェクトを作るための小さなツール群と、それらを起動するランチャーウインドウ。
まずは Maya 上の Python スクリプトとして作り、最終的にはスタンドアロン動作を目指す。

対応: Maya 2022 以降 (Python 3 / PySide2・PySide6 両対応)

## 起動方法 (Maya)

1. このリポジトリを任意の場所に置く
2. `scripts/maya_launcher.py` の `REPO_PATH` を置いた場所に書き換える
3. その内容を Maya のスクリプトエディタ (Python タブ) で実行する
   (シェルフにドラッグしてボタン化しておくと便利)

ランチャーのメニュー「開発 → ツールを再読み込み」で、コードを編集した内容を Maya を再起動せずに反映できる。

## 使い方

- ツール名のボタン: 現在のオプション値ですぐに実行
- `□` ボタン: オプションダイアログを開く (適用 = 保存して実行 / 保存 = 値だけ保存)
- 1 回の実行は 1 回の Ctrl+Z で元に戻せる
- 下部のログ欄に実行結果やエラーが表示される

## ツール一覧

### 射撃 / バルカン砲

ロケータ A (発射位置) から B (経由点) を通って C (着弾点) まで、弾を指定フレームごとに撃ち続ける。

1. 「バルカン用ロケータ作成」で A/B/C のロケータを作る (既存のロケータでも OK)
2. A → B → C の順に選択して「バルカン砲」を実行
   (オプションでロケータ名を指定した場合は選択不要)

| オプション | 内容 |
| --- | --- |
| 軌道 | なめらか: B を通る曲線 / 折れ線: A-B-C を直線で結ぶ |
| 発射開始 / 終了フレーム | この範囲で撃ち続ける |
| 発射間隔 | 何フレームごとに 1 発撃つか (小数可。0.5 なら 1 フレームに 2 発) |
| 着弾までのフレーム数 | 1 発が A から C に到達するまでの時間 (等速) |
| 弾の形状 | 球体 / つぶれた球体 |
| つぶれ率・つぶれ方向 | つぶれた球体の時のみ。1 未満でつぶれ、1 より大きいと伸びる。方向は進行方向 / 上下 / 左右 |
| インスタンスで作成 | ON なら全弾が元メッシュ (`*_bulletSrc`) のインスタンス。元メッシュを編集すると全弾に反映 |

- 軌道カーブはロケータに**ライブ接続**されているので、作成後にロケータを動かすと弾道も追従する
- 弾は発射フレームに出現し、C に到達したフレームの次のフレームで消える (visibility のステップキー)
- 弾は進行方向を向く (モーションパスの follow。X 軸 = 進行方向)

### ユーティリティ / ダミーエフェクトを全削除

このツール群で作ったノード (グループ、マテリアル、補助ノード) をまとめて削除する。

## 構成

```
dummy_effect_maker/
  __init__.py          show() / reload_and_show()
  core/                Qt にも Maya にも依存しない層 (スタンドアロンでもそのまま使う)
    tool.py            BaseTool / Param / ToolContext / execute
    registry.py        tools/ からツールを自動収集
    host.py            Maya / スタンドアロンの差分 (アンドゥ、親ウインドウ)
  ui/                  Qt の UI (PySide2/6 両対応)
    main_window.py     ランチャーウインドウ
    param_dialog.py    Param からオプションダイアログを自動生成
  tools/               各ツール (ここにファイルを足すとボタンが増える)
    _maya_utils.py     Maya ツール共通処理 ("_" 始まりはツールとして読まれない)
    vulcan.py          バルカン砲
    cleanup.py         全削除
scripts/maya_launcher.py   Maya 起動用スニペット
run_standalone.py          Maya 外での起動 (UI 確認用)
tests/                     Maya なしで動くテスト
```

## ツールの追加方法

`dummy_effect_maker/tools/` に `.py` を作り、`BaseTool` を継承したクラスを書くだけでランチャーにボタンが追加される。

```python
from ..core import BaseTool, Param


class MyTool(BaseTool):
    label = u"マイツール"          # ボタン名 (必須)
    category = u"射撃"             # グループ名
    order = 30                     # 並び順
    params = [Param("count", u"個数", 10, minimum=1)]   # あれば □ ボタンが付く

    def run(self, ctx, **v):
        from maya import cmds      # Maya の import は run() の中で
        ctx.log(u"count = %d" % v["count"])
```

`Param` の型は既定値から推測される (bool / int / float / str)。選択肢は `kind="choice", choices=[...]`、色は `kind="color"` で `(r, g, b)`。
ユーザー向けのエラーは `ToolError("メッセージ")` を raise するとダイアログで表示される。

## 開発

```bash
python -m pytest tests          # Maya なしのテスト
python run_standalone.py        # Maya 外でランチャーを表示 (PySide6 が必要)
```
