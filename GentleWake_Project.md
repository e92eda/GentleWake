# GentleWake Project

## 概要

GentleWake は、ベッド在床検知を利用したインテリジェント起床支援システムである。

利用者がベッドにいる場合のみ起床通知を行い、利用者が不在の場合は通知を抑制することで、不必要なアラームを防ぐ。

また、同じベッドで就寝する配偶者を起こさないことを重要な設計目標とする。

---

# プロジェクト目標

## 必須要件

- 利用者がベッドにいる場合のみ起床通知を行う
- 利用者がベッドを離れている場合は通知しない
- Raspberry Pi上で自律動作する
- Amazon Echo Flexを活用する
- 在床状態を記録する
- 後から判定ロジックを変更できる

## 将来要件

- Home Assistant連携
- LLM連携
- 枕振動デバイスによる静音起床
- 睡眠パターン解析

---

# システム構成

## GentleWake v1

ロードセル → HX711 → Raspberry Pi 4 → Python → ログ保存

## GentleWake v2　(2026/7)

ロードセル → HX711 → Raspberry Pi 4 → 在床判定 → Echo Flex

## GentleWake v3　将来

ロードセル → HX711 → Raspberry Pi 4 → MQTT → Home Assistant → Echo Flex

---

# ハードウェア

## センサー

購入済み：

- ハーフブリッジひずみゲージロードセル　50kg × 4
- HX711モジュール

Amazon:
https://www.amazon.co.jp/dp/B089LS556S

## 開発用コンピュータ

### Raspberry Pi 4

## 運用機 

### Raspberry Pi Zero W 初代

---

# デバイスの準備

## mDNS エイリアス (`wake.local`)

hostname は変えずに `wake.local` でもアクセスできるようにする。avahi-publish を systemd で常駐させる方式。

```bash
sudo apt update && sudo apt install -y avahi-utils
sudo nano /etc/systemd/system/avahi-alias@.service
```

サービスファイル内容：

```ini
[Unit]
Description=Publish %I as alias for %H.local via mdns
After=network.target avahi-daemon.service
Requires=avahi-daemon.service

[Service]
Type=simple
ExecStart=/bin/bash -c "/usr/bin/avahi-publish -a -R %I $(avahi-resolve -4 -n $(hostname).local | cut -f 2)"
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

有効化：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now avahi-alias@wake.local.service
```

Mac から `ping wake.local` で確認。元の hostname も引き続き使える。

---

# Home Assistant連携　将来

## 導入目的

GentleWakeは単独でも動作するが、Home Assistantを導入することでセンサー管理、状態管理、通知制御を容易に行える。

Home Assistantは本プロジェクトの「頭脳」として機能する。

## 利点

### 状態管理

在床状態を一元管理できる。

### 自動化機能

GUIベースで条件判定を作成できる。

例：

06:00 AND bed_occupied == ON → 起床通知

### ログ・可視化

- 就寝時刻
- 起床時刻
- 在床時間
- 離床回数

を自動記録できる。

### 音声アシスタント連携

- Alexa
- Home Assistant Voice

との連携が容易になる。

### システム拡張性

将来的なセンサー追加が容易。

## 結論

GentleWake v1では必須ではないが、将来的な拡張性を考えると有力な選択肢である。

---

# LLM連携構想

## 導入目的

単純なアラームから状況判断型アシスタントへ発展させる。

## 利点

### 状況判断

以下を総合判断できる。

- 在床状態
- 曜日
- 予定
- 天気
- 過去の起床パターン

### 起床戦略の最適化

- 通常より早く起こす
- 少し寝かせる
- 通知レベルを調整する

### パーソナルアシスタント化

「おはよう」に対し、

- 時刻
- 天気
- 今日の予定
- 睡眠状況

などを自然な会話で提供する。

## Home Assistantとの関係

センサー → Home Assistant → LLM → 通知

## 結論

LLMは必須ではないが、GentleWakeを知能型起床支援システムへ発展させる中核技術となる。

---

# プロジェクトビジョン

GentleWakeは単なる目覚まし時計ではない。

「睡眠状態を理解し、最適なタイミングで利用者を起こすパーソナル起床アシスタント」を目指す。

---

# 実装

## HX711からロードセルデータを読み取る

### 概要

HX711はロードセル（ひずみゲージ）用の24bitアナログ-デジタルコンバータ（ADC）である。（結構タイミングが微妙！）
Raspberry PiのGPIOと接続し、シリアル通信でデータを取得する。

### 装置

wake

### 接続

ssh kunieda@wake.local

### 配線

| HX711ピン  | Raspberry Pi                                          |
|-----------|-------------------------------------------------------|
| VCC       | **物理 P1 (3.3V)**  ※5V は使わない (後述)              |
| GND       | 物理 P6 (GND)                                         |
| DT (DOUT) | 物理 P11 (GPIO17) ※GPIO2/3はI2Cプルアップ干渉あり      |
| SCK (CLK) | 物理 P15 (GPIO22)                                     |

ロードセル4本をホイートストンブリッジ接続し、HX711の E+/E-（励起）、A+/A-（信号）に接続する。

![img](https://img-proxy.blog-video.jp/images?url=http%3A%2F%2Fimg-cdn.jg.jugem.jp%2F85e%2F51887%2F20240106_3513335.png)

![img](https://sozorablog.com/wp-content/uploads/2021/09/gpio2.jpg)

### 重要: VCC は 3.3V

開発初期 5V で使っていたが、以下の症状で動かない/不安定だった:

- 元のHX711モジュールが ADC 出力 0x7FFFFF (正側飽和) に張り付く
- 新しいHX711でも信号応答が出ない or ノイズだらけ
- Pi Zero に移すと Pi 4 で動いていたものが動かない (Pi Zeroの5Vは特に汚い)

**VCC を 3.3V (物理P1) に変更したら全部解決**。理由:

- HX711 はブリッジ励起電圧 (E+) を VCC から内部レギュレータで生成 → ratiometric 信号なので VCC ノイズが直接出力に乗る
- Pi の 5V は USB入力直結に近くノイズ多 (特に Pi Zero / Pi Zero W)
- Pi の 3.3V は LDO 生成で綺麗
- 励起電圧は 5V時 4V → 3.3V時 約2.7V に下がるが、ノイズ低減の利得の方が大きい

### ライブラリ

**`lgpio` を使う** (Python の `RPi.GPIO` は HX711 には不適)。

```bash
~/GentleWake/.venv/bin/pip install lgpio
```

`RPi.GPIO` を使ったビットバンギングだと OS のスケジューラ割込みで SCK パルスが伸びてしまい:

- HX711 の SCK HIGH 上限 50μs を超えて **動作中に power-down に入る**
- 24bit シリアル読み取り中にビットが化ける → ±数千カウントの bimodal ノイズが出る

`lgpio` はカーネル空間 GPIO で 1op ~1-5μs と高速。さらに各24bit読みの所要時間を計測して、長すぎたら破棄する処理を入れることで、ビット化けを実質ゼロにできる。

### 自前ドライバ + 在床判定モジュール

実装は `sensor.py` に集約。HX711 ドライバと在床判定の両方を含む。

```python
from sensor import SensorMonitor

# config.json から tare_offset / 閾値 / 連続サンプル数を読む
monitor = SensorMonitor(config_path="config.json")
monitor.start()  # バックグラウンドスレッドで読み続ける

# どこからでも在床状態を取れる
if monitor.is_in_bed():
    print("在床中")

# 風袋引き (現在の raw を offset にする)
monitor.tare_now()  # config.json に永続化される

# 閾値や連続サンプル数の更新
monitor.update_settings(threshold=15000, consecutive=5)

monitor.stop()
```

`SensorMonitor` の挙動:

- 0.3秒ごとに HX711 から median(5) で1サンプル取得
- 設定: `NUM_PULSES = 25` (Channel A, Gain 128, ±13mV フルスケール @ 3.3V VCC)
- tared 値 (`raw - tare_offset`) が threshold を **N サンプル連続で上回ったら in_bed = True**
- 同様に **N サンプル連続で下回ったら in_bed = False** (両エッジでデバウンス → チャタリング防止)
- lgpio が無い環境 (Mac 開発時) は警告を出してモックモードで起動 (`available=False`)

### web.py との統合

`web.py` 起動時に `SensorMonitor` を起動。

- ブラウザ UI (`http://<host>:5000/`) で在床状態をリアルタイム表示 (1.5秒ポーリング)
- 「風袋引き」ボタンで `POST /tare` → 現在値をゼロ点に
- 閾値・連続サンプル数も UI から設定可能 → `config.json` に保存
- **アラーム発火は `is_in_bed() == True` のときだけ**。離床中はスキップ (案①)

### キャリブレーション

体重単位の換算は **現状不要** (在床/離床の閾値判定だけで十分)。
将来必要になった場合は、既知重量を載せて raw 値から `counts/kg` 係数を出せばよい。

### 注意事項

- 電源投入直後は値が安定しないため、`SensorMonitor` は起動時 2秒 + 読み捨て 5発で過渡を逃がす
- 起動初回や荷重を急変させた直後は機械クリープで数秒かけて settling する
- デバウンス N=5 + サンプル間隔 0.3秒 だと、状態遷移確定まで約 4秒のラグ

---

---

# Amazon Echo を Amazon.com アカウントへ移行する手順

Echo FlexはデフォルトでAmazon.co.jpに紐づいている場合がある。
GentleWakeではAmazon.comアカウント（e92eda@gmail.com）を使用するため、移行が必要。

## Step 1: Amazon.co.jp から登録解除

1. スマートフォンの Alexa アプリ（Amazon.co.jp用）を開く
2. **デバイス** → 対象のEcho Flex を選択
3. **デバイスの設定** → **デバイスの登録を解除** をタップ
4. または Amazon.co.jp のウェブサイト → **アカウント＆リスト** → **コンテンツと端末の管理** → **端末** → 対象デバイスを削除

## Step 2: Echo Flex をファクトリーリセット

Echo Flexのリセット方法：

1. Echo Flexの電源を入れたまま、底面または側面の小穴（リセットボタン）をペーパークリップなどで長押し
2. オレンジ色のリングが点灯したら離す
3. 青色ランプが点滅してセットアップモードに入るまで待つ（約30秒）

## Step 3: Amazon.com アカウントで再セットアップ

1. スマートフォンに **Amazon Alexa アプリ（米国版）** をインストール
   - App Store / Google Play で "Amazon Alexa" を検索（同じアプリだがアカウントで切り替え）
2. Amazon.com アカウント（e92eda@gmail.com）でログイン
3. **デバイス** → **「＋」** → **Echoスマートスピーカーを追加** を選択
4. 画面の指示に従いWi-Fiを設定してセットアップ完了

> **注意**: Alexaアプリのアカウントをco.jpからcomへ切り替える場合、アプリ内の **設定 → アカウントの切り替え** または一度サインアウトしてcom用アカウントでログインし直す。

## Step 4: GentleWake の設定を Amazon.com 用に更新

`alexa_remote_control.sh` を使う場合、Amazon.com エンドポイントを指定する：

```bash
# Amazon.com 用の環境変数
export AMAZON="amazon.com"
export ALEXA_TLD="com"
```

または `web.py` 等からスクリプトを呼び出す際に `-a amazon.com` オプションを付与する。

## Step 5: トークン確認

移行後は既存のトークンが引き続き使用可能（下記参照）。
デバイスシリアルは変わらないことが多いが、変わった場合は再取得が必要。

---

### Amazon.com トークン　e92eda@gmail.com用

\----------------------------------------------------------------------

 deviceSerial: 4fa2eb8357adcfb8ddde2eb0bcf0e9f2

=======================================================================

 refreshToken: Atnr|EwMDIGDKXi9cKSntjATYV12EIZCE0AK1VzCduAuIqryNjiDG-SEh99k3aXL_iSbO297OtoMVoHN8TstPA24Tb59oGAVyzm0fasvhdHdDGTrifpAJvr_nkMFckMiE3MfAnlZ4g1904FLYhxXl3kiOsqIrcQYkGwN7seygoN2GXUkwVg13oeZHbtChhL4F3Sb3pjyPXmPnHSoPNhYWaRdwfP4T14CYjBg5VwKrOQEoYEe0GTf50QA-umAcB2JTlXI-VSWFlFsJicFTZSItZXDRx_7N0YMq7asPu9R4yd6dRrerNvTF0AQVBz_kamgowNayiwsDRFQ



# 開発履歴

## 2026-06-20

- プロジェクト開始
- Echo Flex活用方針決定
- Raspberry Pi 4採用方針決定
- ロードセルキット選定
- Home Assistant統合方針策定

## 2026-06-26

- HX711 が出力 0x7FFFFF (正側飽和) に張り付き、当初「半田付けで焼いた」と判断
- 後日 (06-27〜28) これは誤診と判明 (5V VCC の症状で、3.3V にしたら同じモジュールが復活)

## 2026-06-27

- 新しい HX711 モジュールでテストするも、ノイズが ±6000 カウントと巨大
- 原因切り分け:
  - 旧テストは `RPi.GPIO` でビットバンギングしていた → OS割込みで SCK パルスが伸び、データビットが化けることを特定
  - `lgpio` (カーネル空間 GPIO) に置き換え、24bit 読みの所要時間を計測して長すぎは破棄する処理を追加 → ノイズが ±50カウント以下に激減
- Gain 64 (NUM_PULSES=26) では信号応答が出ない症状 → Gain 128 (NUM_PULSES=25) で正常動作確認
- 荷重 0.2mV ≈ 5万カウントの応答を確認

## 2026-06-28

- Pi Zero の電源汚さ問題を回避するため、HX711 VCC を 5V から **3.3V** に変更 → Pi 4 上で元の HX711 も含めて安定動作
- 6/26 に「壊れた」と思っていた HX711 が復活 (壊れていなかった)
- 在床検知付き v2 を実装:
  - `sensor.py` 新設: `HX711` ドライバ + `SensorMonitor` (バックグラウンドスレッド + N連続デバウンス)
  - `web.py` 改修: センサスレッド統合、`/state` と `/tare` ルート追加、アラームを在床時のみ発火に変更
  - `templates/index.html` 改修: 在床状態リアルタイム表示、風袋引きボタン、閾値・連続サンプル数の設定UI
  - `config.json` 拡張: `tare_offset`, `in_bed_threshold`, `consecutive_samples` を追加
