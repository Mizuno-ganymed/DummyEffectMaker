# ショットの受け渡し形式 (DummyEffectMakerShot, 形式 1)

Maya / Blender のプレビズで作ったダミーエフェクトを、Unreal Engine (Niagara) で仕上げるための
「設計図」のファイル。1 ショット 1 ファイルの JSON (UTF-8)。

- 書き出し: Maya の「シーン → 書き出し (Unreal 用)」(`tools/export_shot.py`)
- 読み書きの共通処理: `dummy_effect_maker/core/exchange.py` (Maya にも Blender にも Unreal にも依存しない)
- エフェクトの種類とパラメータの一覧: `dummy_effect_maker/catalog.py`

プレビズの見た目 (板や丸のメッシュ) は書かない。**タイミング・配置・パラメータ**を書き、
質感は Unreal 側で作る。タイミングと配置はプレビズが正本 (Unreal で直したものは戻さない)。

## 空間と単位

| 項目 | この形式の中 |
| --- | --- |
| 座標系 | 右手系・**Z が上** (Blender と同じ) |
| 長さ | **メートル** |
| 時間 | フレーム (`time.fps` で秒に直す) |
| 回転 | 四元数 `[x, y, z, w]` |

元のソフトからの変換 (書き出し時):

| 元 | 変換 |
| --- | --- |
| Maya (Y が上・センチ、既定) | 位置 `(x, y, z)` → `(x, -z, y) × 0.01` |
| Maya (Z が上) | 軸はそのまま、単位だけメートルに |
| Blender | そのまま (Z が上・メートル) |

Unreal への変換 (読み込み時。Unreal は左手系・Z が上・センチ):

| 値 | 変換 |
| --- | --- |
| 位置 | `(x, y, z)` → `(x, -y, z) × 100` |
| 四元数 | `(x, y, z, w)` → `(-x, y, -z, w)` |
| 向き (法線・速さ) | `(x, y, z)` → `(x, -y, z)` (速さは × 100) |

`exchange.to_unreal(position, quaternion)` が同じ計算をする (Unreal の Python から使ってよい)。

**オブジェクトのローカル軸の意味は元のまま**。回転は「オブジェクトのローカル軸が、共通の空間でどちらを向くか」を表す。

- カメラ: ローカル **-Z が前、+Y が上** (Maya も Blender も同じ)。Unreal のカメラは +X が前なので、読み込み時に向きを足す
- ロケータなど: 元のソフトのローカル軸 (Maya のロケータなら、作った時の X / Y / Z)

## 全体

```json
{
  "format": "DummyEffectMakerShot",
  "version": 1,
  "source": {"app": "maya", "app_version": "2027", "scene": "...", "tool_version": "0.1.0",
             "up_axis": "y", "linear_unit": "cm"},
  "space": {"up": "Z", "handedness": "right", "unit": "meter"},
  "time": {"fps": 24.0, "start": 1, "end": 120},
  "cameras": [ ... ],
  "effects": [ ... ]
}
```

`version` がツールより新しいファイルは読み込まない (`exchange.read_shot` がエラーにする)。
項目が増えても読めるよう、知らない項目は無視すること。

## トラック (毎フレームの値)

位置・向きなどの時間で変わる値。全フレームで同じなら 1 つにまとめる。

```json
{"static": true, "t": [x, y, z], "q": [x, y, z, w], "s": [sx, sy, sz]}
```

```json
{"static": false, "frames": [1, 2, 3, ...],
 "t": [[x, y, z], ...], "q": [[x, y, z, w], ...], "s": [[sx, sy, sz], ...]}
```

- `t` 位置 (メートル)、`q` 回転、`s` 拡大縮小 (負なら裏返し)
- 種類によって `visible` (表示)、`focal_length` (焦点距離 mm)、`velocity` / `drift` (風) などが付く
- 値は毎フレーム (`time.start` 〜 `time.end`)。間のフレームは直線補間 (`exchange.track_value`)

## カメラ

```json
{"name": "shotCam1", "film_aperture_mm": [36.0, 24.0], "clip": [0.001, 100.0],
 "track": {"t": ..., "q": ..., "s": ..., "focal_length": ...}}
```

画ぶれ・手ブレを付けたカメラは、ブレ込みの動きになる。書き出すのは、エフェクトにつながったカメラと、
書き出す時に選択していたカメラ。

## エフェクト

```json
{
  "type": "vulcan", "label": "バルカン砲", "group": "bullets", "name": "dfx_vulcan1",
  "baked": false,
  "params": {"interval": 5.0, "color": [1.0, 0.85, 0.4], ...},
  "param_info": {"interval": {"label": "...", "kind": "float", "default": 1.0, "min": 0.01,
                              "live": false}, ...},
  "links": {"dfx_camera": "shotCam1", "dfx_wind": "dfx_wind1"},
  "controls": [{"name": "dfx_vulcan1_target", "role": "child", "shape": "locator",
                "track": {...}}],
  "data": {...}
}
```

| 項目 | 内容 |
| --- | --- |
| `type` | 種類 (下の表)。Unreal 側は種類ごとに Niagara のテンプレートを用意する |
| `group` | 大まかな分類 (`bullets` / `camera` / `burst` / `particles` / `layers` / `motion` / `control`) |
| `params` | ルートのパラメータの値。色は `[r, g, b]` (0〜1)、選択肢は選んだ項目の文字列 (日本語) |
| `param_info` | パラメータの定義 (表示名・種類・既定値・範囲・選択肢・すぐ反映か) |
| `links` | つながった物 (カメラ・風・付けたオブジェクト・弾のルートなど)。名前は短い名前 |
| `controls` | 動かして決める物 (ロケータ・範囲の箱・出る場所・中心の円など) の動き。`role` はルートの子なら `child`、つながりならアトリビュート名 |
| `data` | 種類ごとの追加の情報 (下) |

パラメータの単位は元のソフトのまま (例: 速さは「1 フレームに進む距離 (元の単位)」)。
距離のパラメータを使う時は `source.linear_unit` で直す。

### data

| 種類 | data |
| --- | --- |
| 弾 (`homing` / `vulcan` / `energy_ball`) | `bullets`: 弾ごとの `{"name", "track"}`。track に `visible` (爆発で消えた後は false) |
| 爆発 (`explosion`) | `hits`: `{"frame", "bullet" (弾の番号), "position", "normal" (面の外向き)}` |
| 風 (`wind`) | `wind`: トラック。`velocity` (その時の風、メートル / フレーム)、`drift` (流された距離、メートル) |
| 衝撃波 (`shockwave`) | `ring_normal`: トラック。`normal` (輪の面の向き。Maya は中心のロケータのローカル Y、Blender はローカル Z) |
| そのほか | 空 (パラメータと controls から作る) |

## 種類の一覧

`catalog.describe_all()` で、全部の種類のパラメータの定義を出せる。

| 分類 | 種類 |
| --- | --- |
| bullets | homing (追尾弾), vulcan (バルカン砲), energy_ball (エネルギー弾) |
| camera | focus_lines (集中線), speed_lines (スピードライン), camera_shake (画ぶれ), hand_shake (手ブレ) |
| burst | shockwave (衝撃波), explosion (爆発) |
| motion | afterimage (残像), smear (スミア), trail (軌跡) |
| control | wind (風) |
| particles | rain, snow, dust, dust_mesh, sparks, spark_trail |
| layers | rain_far, snow_far, fog, fog_depth, ground_fog, ground_crawl |

## 今後

- Blender 版も同じ形式で書き出す (Blender は Z が上・メートルなので、座標はそのまま)
- 形式を変える時は `FORMAT_VERSION` を上げ、古い形式も読めるようにする
