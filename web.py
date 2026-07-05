from flask import Flask, render_template, request, jsonify
import json
import threading
import time
import datetime
import subprocess
import os

from sensor import SensorMonitor

app = Flask(__name__)
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
ALEXA_DEVICE = "Echo Flex"
ALEXA_SCRIPT = os.path.join(os.path.dirname(__file__), "alexa_remote_control.sh")

DAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]
DAY_MAP = {name: i for i, name in enumerate(DAY_NAMES)}

last_fired_date = None
last_skipped_date = None  # 離床でスキップした日 (二重ログ防止)

sensor = SensorMonitor(CONFIG_FILE)

# アラーム自動停止
ALARM_MAX_MINUTES = 30
ALARM_WATCHDOG_INTERVAL_S = 2
_alarm_lock = threading.Lock()
_alarm_active = False
_alarm_started_at = None
_alarm_was_in_bed = False


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


@app.route("/")
def index():
    config = load_config()
    state = sensor.get_state()
    return render_template("index.html", config=config, state=state, saved=False)


@app.route("/save", methods=["POST"])
def save():
    config = load_config()  # 既存値を保持 (tare_offset 等)
    config.update({
        "alarm_time": request.form.get("alarm_time", "07:00"),
        "days": request.form.getlist("days"),
        "skip_today": "skip_today" in request.form,
        "enabled": "enabled" in request.form,
        "in_bed_threshold": int(request.form.get("in_bed_threshold", 10000)),
        "consecutive_samples": int(request.form.get("consecutive_samples", 5)),
    })
    save_config(config)
    sensor.update_settings(
        threshold=config["in_bed_threshold"],
        consecutive=config["consecutive_samples"],
    )
    state = sensor.get_state()
    return render_template("index.html", config=config, state=state, saved=True)


@app.route("/state")
def get_state():
    return jsonify(sensor.get_state())


@app.route("/tare", methods=["POST"])
def tare():
    offset = sensor.tare_now()
    if offset is None:
        return jsonify({"ok": False, "error": "センサ値がまだ読めていません"}), 503
    return jsonify({"ok": True, "tare_offset": offset})


@app.route("/test-alarm", methods=["POST"])
def test_alarm():
    threading.Thread(target=trigger_alarm, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/stop-alarm", methods=["POST"])
def stop_alarm():
    threading.Thread(target=stop_music, daemon=True).start()
    return jsonify({"ok": True})


def _mark_alarm_started():
    global _alarm_active, _alarm_started_at, _alarm_was_in_bed
    with _alarm_lock:
        _alarm_active = True
        _alarm_started_at = time.time()
        _alarm_was_in_bed = sensor.is_in_bed()


def _mark_alarm_stopped():
    global _alarm_active, _alarm_started_at, _alarm_was_in_bed
    with _alarm_lock:
        _alarm_active = False
        _alarm_started_at = None
        _alarm_was_in_bed = False


def trigger_alarm():
    _mark_alarm_started()
    subprocess.run([
        ALEXA_SCRIPT,
        "-d", ALEXA_DEVICE,
        "-e", "textcommand:Play bolero by Maurice Ravel"
    ])


def stop_music():
    _mark_alarm_stopped()
    subprocess.run([
        ALEXA_SCRIPT,
        "-d", ALEXA_DEVICE,
        "-e", "textcommand:stop"
    ])


def alarm_watchdog_loop():
    """アラーム稼働中に離床 or 30分経過を検知して自動停止する。"""
    while True:
        try:
            with _alarm_lock:
                active = _alarm_active
                started_at = _alarm_started_at
                was_in_bed = _alarm_was_in_bed
            if active and started_at is not None:
                elapsed_min = (time.time() - started_at) / 60
                reason = None
                if elapsed_min >= ALARM_MAX_MINUTES:
                    reason = f"{ALARM_MAX_MINUTES}分経過"
                elif was_in_bed and not sensor.is_in_bed():
                    reason = "離床検知"
                if reason:
                    print(f"[watchdog] 自動停止 ({reason})")
                    stop_music()
        except Exception as e:
            print(f"watchdog エラー: {e}")
        time.sleep(ALARM_WATCHDOG_INTERVAL_S)


def alarm_loop():
    global last_fired_date, last_skipped_date
    while True:
        try:
            config = load_config()
            now = datetime.datetime.now()
            today = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")
            weekday = now.weekday()

            scheduled_days = [DAY_MAP[d] for d in config.get("days", []) if d in DAY_MAP]

            should_fire = (
                config.get("enabled")
                and not config.get("skip_today")
                and weekday in scheduled_days
                and current_time == config.get("alarm_time")
                and last_fired_date != today
            )

            if should_fire:
                if sensor.is_in_bed():
                    last_fired_date = today
                    print(f"[{current_time}] アラーム発動 (在床確認)")
                    trigger_alarm()
                elif last_skipped_date != today:
                    last_skipped_date = today
                    print(f"[{current_time}] 離床中のためアラームスキップ")

        except Exception as e:
            print(f"アラームエラー: {e}")

        time.sleep(30)


if __name__ == "__main__":
    sensor.start()
    threading.Thread(target=alarm_loop, daemon=True).start()
    threading.Thread(target=alarm_watchdog_loop, daemon=True).start()
    try:
        app.run(host="0.0.0.0", port=80, debug=False)
    finally:
        sensor.stop()