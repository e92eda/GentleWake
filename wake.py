"""GentleWake - 曜日ごとに時刻を設定できる新版エントリーポイント。

既存の web.py はそのまま残し、systemd の ExecStart をこちらに差し替えれば切替可能。
テンプレートは templates/wake.html を使う。
"""
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

last_fired_date = None
last_skipped_date = None

sensor = SensorMonitor(CONFIG_FILE)

ALARM_MAX_MINUTES = 30
ALARM_WATCHDOG_INTERVAL_S = 2
_alarm_lock = threading.Lock()
_alarm_active = False
_alarm_started_at = None
_alarm_was_in_bed = False


def _migrate_schedule(cfg: dict) -> dict:
    """旧 alarm_time + days から新 schedule 形式へ移行、欠けている曜日を埋める。

    旧: {"alarm_time": "07:00", "days": ["月","火",...]}
    新: {"schedule": {"月": {"enabled": true, "time": "07:00"}, ...}}
    """
    if "schedule" not in cfg or not isinstance(cfg.get("schedule"), dict):
        old_time = cfg.get("alarm_time", "07:00")
        old_days = set(cfg.get("days", []))
        cfg["schedule"] = {
            d: {"enabled": d in old_days, "time": old_time}
            for d in DAY_NAMES
        }
    else:
        for d in DAY_NAMES:
            entry = cfg["schedule"].get(d)
            if not isinstance(entry, dict):
                cfg["schedule"][d] = {"enabled": False, "time": "07:00"}
            else:
                entry.setdefault("enabled", False)
                entry.setdefault("time", "07:00")
    return cfg


def load_config():
    with open(CONFIG_FILE) as f:
        return _migrate_schedule(json.load(f))


def save_config(config):
    config.pop("alarm_time", None)
    config.pop("days", None)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def compute_next_alarm(config, now=None):
    """次にアラームが鳴る予定の datetime を返す。無ければ None。"""
    if not config.get("enabled"):
        return None
    if now is None:
        now = datetime.datetime.now()
    schedule = config.get("schedule", {})
    for offset in range(0, 8):
        candidate_date = now.date() + datetime.timedelta(days=offset)
        day_name = DAY_NAMES[candidate_date.weekday()]
        entry = schedule.get(day_name, {})
        if not entry.get("enabled"):
            continue
        try:
            h, m = map(int, entry.get("time", "").split(":"))
        except (ValueError, AttributeError):
            continue
        candidate_dt = datetime.datetime.combine(candidate_date, datetime.time(h, m))
        if offset == 0:
            if candidate_dt <= now:
                continue
            if config.get("skip_today"):
                continue
        return candidate_dt
    return None


def _render_index(config, saved):
    state = sensor.get_state()
    next_alarm = compute_next_alarm(config)
    next_alarm_label = None
    if next_alarm:
        day_name = DAY_NAMES[next_alarm.weekday()]
        today = datetime.date.today()
        if next_alarm.date() == today:
            next_alarm_label = f"今日 ({day_name})"
        elif next_alarm.date() == today + datetime.timedelta(days=1):
            next_alarm_label = f"明日 ({day_name})"
        else:
            next_alarm_label = f"{next_alarm.month}/{next_alarm.day} ({day_name})"
    return render_template(
        "wake.html",
        config=config,
        state=state,
        saved=saved,
        next_alarm=next_alarm,
        next_alarm_label=next_alarm_label,
        day_names=DAY_NAMES,
    )


def _render_settings(config, saved):
    return render_template(
        "wake_settings.html",
        config=config,
        state=sensor.get_state(),
        saved=saved,
    )


@app.route("/")
def index():
    return _render_index(load_config(), saved=False)


@app.route("/settings")
def settings():
    return _render_settings(load_config(), saved=False)


@app.route("/save", methods=["POST"])
def save():
    """メイン画面フォーム: schedule / enabled / skip_today を保存する。"""
    config = load_config()
    schedule = {}
    for day in DAY_NAMES:
        schedule[day] = {
            "enabled": f"day_{day}_enabled" in request.form,
            "time": request.form.get(f"day_{day}_time", "07:00"),
        }
    config["schedule"] = schedule
    config["skip_today"] = "skip_today" in request.form
    config["enabled"] = "enabled" in request.form
    save_config(config)
    speak_async(_summarize_config(config))
    return _render_index(config, saved=True)


@app.route("/save-sensor", methods=["POST"])
def save_sensor():
    """詳細設定画面フォーム: 閾値 / デバウンスを保存する。"""
    config = load_config()
    config["in_bed_threshold"] = int(request.form.get("in_bed_threshold", 10000))
    config["consecutive_samples"] = int(request.form.get("consecutive_samples", 5))
    save_config(config)
    sensor.update_settings(
        threshold=config["in_bed_threshold"],
        consecutive=config["consecutive_samples"],
    )
    return _render_settings(config, saved=True)


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


def speak_async(text: str):
    def run():
        try:
            subprocess.run(
                [ALEXA_SCRIPT, "-d", ALEXA_DEVICE, "-e", f"speak:{text}"],
                timeout=30,
            )
        except Exception as e:
            print(f"speak エラー: {e}")
    threading.Thread(target=run, daemon=True).start()


DAY_EN = {"月": "Mon", "火": "Tue", "水": "Wed", "木": "Thu",
          "金": "Fri", "土": "Sat", "日": "Sun"}


def _summarize_config(config: dict) -> str:
    if not config.get("enabled"):
        return "Alarm disabled"
    active = [(d, e["time"]) for d, e in config.get("schedule", {}).items() if e.get("enabled")]
    if not active:
        return "No days enabled"
    parts = []
    times = {t for _, t in active}
    if len(times) == 1:
        time_str = next(iter(times))
        hour, _, minute = time_str.partition(":")
        parts.append(f"Alarm set to {int(hour)}:{minute}")
        day_names = [DAY_EN[d] for d, _ in active if d in DAY_EN]
        parts.append("on " + ", ".join(day_names))
    else:
        parts.append("Per day alarm schedule updated")
    if config.get("skip_today"):
        parts.append("skipping today")
    return ", ".join(parts)


def alarm_watchdog_loop():
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
            day_name = DAY_NAMES[now.weekday()]
            entry = config.get("schedule", {}).get(day_name, {})

            should_fire = (
                config.get("enabled")
                and not config.get("skip_today")
                and entry.get("enabled")
                and current_time == entry.get("time")
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

    def _delayed_startup_speak():
        time.sleep(30)
        speak_async("GentleWake started")
    threading.Thread(target=_delayed_startup_speak, daemon=True).start()

    port = int(os.environ.get("PORT", "80"))
    try:
        app.run(host="0.0.0.0", port=port, debug=False)
    finally:
        sensor.stop()
