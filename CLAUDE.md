# Dummy Effect Maker — 開発メモ (Claude Code 用)

プレビズ用のダミーエフェクトを作るツール群。Maya 版 (PySide のランチャー) と Blender 版 (アドオン) があり、
同じパラメータ・同じ計算・同じ書き出しの形式。使い方とエフェクトごとの説明は README.md、受け渡し形式は docs/exchange_format.md。

## やり取りの約束

- **日本語**でやり取りする (コード中のコメント・ツールの表示名も日本語)
- コミット・プッシュは**頼まれた時だけ**。コミットメッセージは英語で、最後に
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` を付ける
- 作業ブランチは `claude/gallant-clarke-bdykc4` (`main` はまだ無い)。リモートは
  `https://github.com/Mizuno-ganymed/DummyEffectMaker.git` (旧 URL から転送される)
- テストの成果物をユーザーの Maya プロジェクト・マイドキュメント・環境設定に書かない (一時フォルダを使う)
- 見た目が大事な変更は、Blender で描画した画像を見て確かめてから渡す (EEVEE、一時フォルダに出力)

## 作り

- `dummy_effect_maker/tools/` … Maya 版のツール (BaseTool を継承すると自動で登録。`tab` / `category` / `order` でボタンの場所)
- `dummy_effect_maker/blender/tools/` … Blender 版 (同じ Param 定義を使い、組み立てだけ Blender で行う)
- `dummy_effect_maker/motion.py` … **Maya と Blender で共通の計算** (Y が上の空間)。Blender 版はこれをそのまま呼ぶ
- Maya 版の動きは **MEL のエクスプレッション**で、motion.py と**同じ式を MEL で書き直したもの**。
  片方を変えたら必ずもう片方も変え、tests/maya_verify.py で一致を確かめる
  - 乱数は `wx.mel_hash` (MEL) と `motion.h` / `_weather.hash01` (Python) が同じ式
  - 粒は `wx.chunked_expressions` で 10 個ずつのエクスプレッション。出力は `wx.mel_store_transform`
    ($p / 正規直交の $X $Y $Z / $S / $vis)
- Blender は Z が上。motion.py に渡す前に `mo.yup_vector` / `_wx.yup(matrix)` で Y が上に直す
- `catalog.py` … 全部の種類の一覧 (書き出し・アトリビュートエディタ・プリセットが使う)
- パラメータはルートのカスタムアトリビュート `dfx_<名前>` (Blender はカスタムプロパティ)。種類は `dfxEffectType`
- パラメータの保存・読み込みは全部の種類で共通 (`tools/preset.py`。アトリビュートエディタの「保存」「読み込み」から)

### 新しいエフェクトを足す時に触る所

1. `motion.py` に計算 → `tools/<名前>.py` (Maya。MEL) → `blender/tools/<名前>.py`
2. `catalog.py` の `_WEATHER_GROUPS` (天候の仕組みの種類) と、読み込むモジュールの一覧
3. 更新ボタンが天候の「天候を更新」以外なら `tools/_attr_backend.py` の `UPDATE_TOOLS`
4. 読み込み (プリセット) の作り直しが特別なら `tools/preset.py` の `rebuilder`
5. テスト: `tests/test_core.py` (タブの並び・`wx.KINDS` の数・計算)、`tests/maya_verify.py` に節を 1 つ
   (末尾の `SECTIONS` にも足す)、`tests/blender_verify.py` に節を 1 つ (末尾の一覧にも足す)
6. README.md (タブの説明・エフェクトの節・ファイル一覧)

## テスト

| テスト | 動かし方 | 時間 |
| --- | --- | --- |
| 単体 (Maya・Blender 不要) | `python tests/run_unit.py` | 数秒 |
| Maya 版 (全部) | `PYTHONIOENCODING=utf-8 "C:/Program Files/Autodesk/Maya2027/bin/mayapy.exe" tests/maya_verify.py` | 約 10 分 |
| Maya 版 (一部) | 上の最後に節の名前の始まりを付ける (例: `t53 t45_`) | — |
| Blender 版 | `"C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" -b --factory-startup --python tests/blender_verify.py -- <リポジトリ>` | 約 1 分 |

- 長いテスト (Maya の全部) はバックグラウンドで動かす
- 変更したら、少なくとも単体と、関係する Maya の節・Blender を通してから渡す。最後に Maya の全部も通す

## つまずいた所 (繰り返さないように)

- **シェルのヒアドキュメントで Python を書き換えると `\n` が本当の改行になる** (文字列が壊れる)。
  ツールチップなど `\n` を含む文字列の書き換えは Edit ツールで行う
- **MEL の `{ }` の中で `vector $Y = ...` と宣言すると、外の `$Y` とは別の変数になる**。外で宣言した変数には代入だけする
  (`wx.mel_ortho` は宣言を含むので、ブロックの中で外の変数に使わない)
- **Maya 2027 では addDoubleLinear の種類名が `addDL`** (`cmds.nodeType` の結果)。種類名で探す所は両方入れる
- **motionPath の uValue にアニメーションカーブを直接つなぐと、キーごとに位置マーカーができる**。
  後から setAttr で隠すとリドゥで Maya が落ちる。間に中継ノードを挟む (追尾弾の `*_uRelay`)
- **インスタンスごとにマテリアルを変えると Arnold で表示されない**。色を変えるなら、色ごとに別のメッシュ (元) を作る
- 透明度は `outTransparency`。surfaceShader の色に不透明度を掛けない (`wx.flat_shader` の説明)
- transform の `boundingBoxMin/Max` は**親の空間** (シェイプのものはローカル)
- Blender のテストは `--factory-startup` の空のシーンで動かす。描画の確認は `tests/` 外の一時フォルダに出す
