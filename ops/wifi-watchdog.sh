#!/bin/bash
# WiFi 自動復旧 watchdog
# gateway に届かない状態が連続したら段階的に復旧を試みる
set -u

STATE_DIR=/var/lib/wifi-watchdog
COUNT_FILE=$STATE_DIR/fail_count
REBOOT_COUNT_FILE=$STATE_DIR/reboot_count
MAX_REBOOTS=3   # これ以上リブートしても直らない場合はリブートを諦める
mkdir -p "$STATE_DIR"
[ -f "$COUNT_FILE" ] || echo 0 > "$COUNT_FILE"
[ -f "$REBOOT_COUNT_FILE" ] || echo 0 > "$REBOOT_COUNT_FILE"

log() { logger -t wifi-watchdog -- "$*"; }

GATEWAY=$(ip route show default | awk "/default/ {print \$3; exit}")

check() {
  # 3回 ping, 各 2 秒タイムアウト
  [ -n "${GATEWAY:-}" ] && ping -c 3 -W 2 -q "$GATEWAY" >/dev/null 2>&1
}

if check; then
  prev=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
  prev_reboots=$(cat "$REBOOT_COUNT_FILE" 2>/dev/null || echo 0)
  if [ "$prev" -gt 0 ] || [ "$prev_reboots" -gt 0 ]; then
    log "OK gw=$GATEWAY (recovered from fail_count=$prev, reboot_count=$prev_reboots)"
  fi
  echo 0 > "$COUNT_FILE"
  echo 0 > "$REBOOT_COUNT_FILE"
  exit 0
fi

count=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$COUNT_FILE"
log "FAIL gw=${GATEWAY:-none} fail_count=$count"

case "$count" in
  1)
    # 一過性の可能性: 何もせず次回に賭ける
    ;;
  2)
    log "escalation L1: ip link bounce wlan0"
    ip link set wlan0 down
    sleep 3
    ip link set wlan0 up
    ;;
  3)
    log "escalation L2: restart NetworkManager"
    systemctl restart NetworkManager
    ;;
  4)
    log "escalation L3: reload brcmfmac driver"
    modprobe -r brcmfmac
    sleep 2
    modprobe brcmfmac
    ;;
  *)
    reboot_count=$(cat "$REBOOT_COUNT_FILE" 2>/dev/null || echo 0)
    if [ "$reboot_count" -ge "$MAX_REBOOTS" ]; then
      log "escalation L4 SKIPPED: reboot_count=$reboot_count reached MAX_REBOOTS=$MAX_REBOOTS. Giving up on auto-reboot; leaving system up for manual diagnosis."
      # ここで打ち切り。fail_count は増やし続けない (次回以降も L4 に来るが SKIP される)
      exit 0
    fi
    reboot_count=$((reboot_count + 1))
    echo "$reboot_count" > "$REBOOT_COUNT_FILE"
    # リブート前に fail_count をリセット: 起動後は L1 から順にエスカレートさせる
    # (これにより連続リブートの最短間隔が ~10 分 = 5 tick × 2 分 に自然に広がる)
    echo 0 > "$COUNT_FILE"
    log "escalation L4: rebooting (fail_count=$count, reboot_count=$reboot_count/$MAX_REBOOTS)"
    /sbin/reboot
    ;;
esac