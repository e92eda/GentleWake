#!/bin/bash
# Alexa接続テストスクリプト
# alexa_remote_control.sh の各ステップ（認証→cookie→コマンド）を順に確認する

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ALEXA_SCRIPT="${SCRIPT_DIR}/alexa_remote_control.sh"
ALEXA_DEVICE="Echo Flex"
COOKIE_FILE="/tmp/.alexa.cookie"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

echo "=========================================="
echo " Alexa 接続テスト"
echo "=========================================="

# 1. スクリプトの存在確認
echo ""
echo "--- Step 1: スクリプト存在確認 ---"
if [ -f "${ALEXA_SCRIPT}" ]; then
    pass "alexa_remote_control.sh が見つかりました: ${ALEXA_SCRIPT}"
else
    fail "alexa_remote_control.sh が見つかりません: ${ALEXA_SCRIPT}"
    exit 1
fi

# 2. 依存コマンド確認
echo ""
echo "--- Step 2: 依存コマンド確認 ---"
for cmd in curl jq; do
    if command -v "$cmd" &>/dev/null; then
        pass "$cmd: $(command -v $cmd)"
    else
        fail "$cmd が見つかりません (brew install $cmd で導入可能)"
    fi
done

# 3. REFRESH_TOKEN の存在確認
echo ""
echo "--- Step 3: REFRESH_TOKEN 確認 ---"
TOKEN=$(grep 'SET_REFRESH_TOKEN=' "${ALEXA_SCRIPT}" | head -1 | sed "s/.*='\(.*\)'/\1/")
if [ -n "${TOKEN}" ] && [ "${TOKEN}" != "''" ]; then
    PREVIEW="${TOKEN:0:20}..."
    pass "REFRESH_TOKEN が設定されています (先頭20文字: ${PREVIEW})"
else
    fail "REFRESH_TOKEN が空です。alexa-cookie-cli で再取得してください"
    exit 1
fi

# 4. Amazon への到達確認
echo ""
echo "--- Step 4: Amazon サーバー到達確認 ---"
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://www.amazon.com/")
if [ "${HTTP_STATUS}" = "200" ] || [ "${HTTP_STATUS}" = "302" ] || [ "${HTTP_STATUS}" = "301" ] || [ "${HTTP_STATUS}" = "403" ]; then
    pass "amazon.com に到達できました (HTTP ${HTTP_STATUS})"
else
    fail "amazon.com に到達できません (HTTP ${HTTP_STATUS})"
    info "ネットワーク接続またはファイアウォールを確認してください"
fi

# 5. ログイン / cookie 取得テスト
echo ""
echo "--- Step 5: ログイン (cookie 取得) テスト ---"
info "alexa_remote_control.sh -login を実行中..."
LOGIN_OUTPUT=$(bash "${ALEXA_SCRIPT}" -login 2>&1)
LOGIN_EXIT=$?

if echo "${LOGIN_OUTPUT}" | grep -qi "error\|failed\|fail\|ERROR"; then
    fail "ログインでエラーが発生しました"
    echo "${LOGIN_OUTPUT}"
elif [ -f "${COOKIE_FILE}" ] && grep -q "csrf" "${COOKIE_FILE}" 2>/dev/null; then
    pass "cookie ファイルが取得できました: ${COOKIE_FILE}"
else
    fail "cookie に csrf トークンがありません"
    info "REFRESH_TOKEN が期限切れの可能性があります"
    echo "${LOGIN_OUTPUT}"
fi

# 6. speak コマンドテスト
echo ""
echo "--- Step 6: speak コマンドテスト ---"
info "デバイス「${ALEXA_DEVICE}」に speak コマンドを送信中..."
SPEAK_OUTPUT=$(bash "${ALEXA_SCRIPT}" -d "${ALEXA_DEVICE}" -e "speak:Alexa connection test successful" 2>&1)
SPEAK_EXIT=$?

if echo "${SPEAK_OUTPUT}" | grep -qi "error\|failed\|ERROR"; then
    fail "speak コマンド失敗"
    echo "${SPEAK_OUTPUT}"
else
    pass "speak コマンドを送信しました (Alexaが喋ったか確認してください)"
fi

echo ""
echo "=========================================="
echo " テスト完了"
echo "=========================================="