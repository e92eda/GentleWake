# GentleWake

在床センサー付きの目覚まし時計。ベッドに寝ているときだけ Echo Flex にアラームを鳴らさせる Raspberry Pi プロジェクト。

- Pi + HX711 + ロードセル 4枚でベッドの荷重を計測
- 設定時刻に「在床中」なら Alexa 経由で Echo Flex から音楽再生
- 「離床中」ならアラームを鳴らさない (配偶者を起こさない)
- 30 分経過または離床検知で自動停止

詳細な設計思想・配線・履歴は [Docs/GentleWake_Project.md](Docs/GentleWake_Project.md) を参照。

---

## クイックスタート (Pi 側で)

1. リポジトリを `/home/kunieda/GentleWake/` に配置し `.venv` に依存を入れる
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/pip install lgpio  # Pi 側のみ (GPIO 直接叩く)
   ```
2. systemd で常駐起動 (詳しくは [gentlewake.service](gentlewake.service))
   ```bash
   sudo systemctl enable --now gentlewake.service
   ```
3. ブラウザから `http://zero.local/` (または Pi の IP) を開いて設定

---

## 使い方 (Web UI)

### メイン画面 (`/`)

- **上の大きい時刻表示** = 次に鳴る予定のアラーム時刻 (通常は読み取り専用)
- **7 個の曜日チップ**:
  - **短タップ** = その曜日を on/off (青くなる/白くなる)
  - **長押し (0.5 秒)** = その曜日の時刻を編集するモードに入る
    - 上の時刻フィールドが編集可能になり、チップに黄色いハイライト
    - タイムピッカーで新しい時刻を選び、「完了」ボタンで戻る
- **アラーム有効** = false のときはこの日以降アラーム発動しない
- **今日はスキップ** = 一度だけ今日のアラームを飛ばす (翌 0 時に自動解除)
- **設定を保存** = 保存 (Echo が英語で "Alarm set to..." と要約読み上げ)

### 詳細設定画面 (`/settings`)

- **在床判定閾値** (`raw - tare`) = tared 値がこの値を超えたら「載っている」と判定
- **デバウンス連続サンプル数** = 状態変化と判定するまでの連続一致回数 (デフォルト 5、サンプル 0.3 秒間隔なので 5 なら約 1.5 秒)
- **センサ状態**: raw/tared/offset/threshold のリアルタイム表示
- **⚖️ 風袋引き**: いま載っていない状態のセンサ値をゼロ点に補正
- **▶ 今すぐ鳴らす**: 在床判定を無視してテスト発火

---

## ファイル構成

### アプリ本体

| ファイル | 説明 |
|---|---|
| `wake.py` | **現行エントリーポイント** (Flask + Sensor + アラームループ)。曜日ごとに時刻を持つ新スキーマに対応 |
| `web.py` | 旧エントリーポイント (単一 `alarm_time` + `days[]` の旧仕様)。並存していて systemd の切替で戻せる |
| `sensor.py` | HX711 ドライバ + `SensorMonitor` (バックグラウンドスレッドで在床判定) |
| `alexa_remote_control.sh` | Echo に "speak" / "textcommand" を送るシェルスクリプト (外部由来) |
| `config.json` | 設定の永続化。schedule (曜日ごとの `{enabled,time}`), skip_today, enabled, tare_offset, in_bed_threshold, consecutive_samples |

### テンプレート

| ファイル | 説明 |
|---|---|
| `templates/wake.html` | 新版メイン画面。曜日チップの短タップ/長押しで on/off + 時刻編集を切替 |
| `templates/wake_settings.html` | 新版詳細設定 (閾値・デバウンス・センサ・テスト) |
| `templates/index.html` | 旧 `web.py` 用テンプレート |

### 運用系

| ファイル | 説明 |
|---|---|
| `gentlewake.service` | systemd サービス定義 (`ExecStart` を `wake.py` / `web.py` で差し替える) |
| `watchdog.sh` | 5 分毎の自己監視 (CPU/温度/svc状態/センサ値を journal 記録) |
| `requirements.txt` | Python 依存 |

### ドキュメント

| ファイル | 説明 |
|---|---|
| `Docs/GentleWake_Project.md` | プロジェクトの設計思想、配線、履歴 |
| `Docs/imageCircit.png` | 回路図 |
| `Docs/load_history.csv` | 荷重ログ (研究用) |

### Pi 側 (リポジトリ外) の関連ファイル

- `/etc/NetworkManager/conf.d/wifi-powersave-off.conf` — WiFi 省電力オフ (brcmfmac ハング対策)
- `/usr/local/sbin/wifi-watchdog.sh` + `/etc/systemd/system/wifi-watchdog.{service,timer}` — 2 分毎に gateway 疎通確認、失敗時は段階的復旧 (link bounce → NM restart → brcmfmac reload → reboot)
- `/usr/local/sbin/wifi-selector.sh` + `/etc/systemd/system/wifi-selector.{service,timer}` — 5 分毎に NM 登録済み SSID をスキャン、最強電波の AP に自動切替

---

## 動作モード切替 (wake.py ↔ web.py)

新版を試す・戻す:

```bash
# 新版 (wake.py) に切替
sudo systemctl stop gentlewake
sudo sed -i 's|web\.py|wake.py|' /etc/systemd/system/gentlewake.service
sudo systemctl daemon-reload && sudo systemctl start gentlewake

# 旧版 (web.py) に戻す
sudo sed -i 's|wake\.py|web.py|' /etc/systemd/system/gentlewake.service
sudo systemctl daemon-reload && sudo systemctl restart gentlewake
```

対話実行して確かめたい時は 80 番を避ける:

```bash
PORT=8080 /home/kunieda/GentleWake/.venv/bin/python /home/kunieda/GentleWake/wake.py
# ブラウザで http://zero.local:8080/
```

---

## トラブルシューティング

| 症状 | 主な原因 | 見るべきログ |
|---|---|---|
| アラームが鳴らなかった | Alexa cookie 期限切れ or WiFi 断 | `journalctl -u gentlewake --since '今朝'` で `HTTP/000` を検索 |
| ブラウザから見えない・SSH できない | brcmfmac ハング | `journalctl -t wifi-watchdog` / `journalctl -k | grep brcmf` |
| センサ値が突然半分に | ロードセル抜け or 半田クラック | `journalctl -t gentlewake-watchdog | grep raw=` |
| 起動時にモックモード | venv に lgpio が入ってない or `gpio` グループ未加入 | `sudo systemctl status gentlewake` の起動ログ |
| 一定間隔でサイレントリブート | 基板の機械的接触不良 | `journalctl --list-boots` (2026-07-05 発生時の例) |
