#!/bin/sh
set -eu

CHROME_BIN="${CHROME_BIN:-chromium}"
CHROME_LOG="${CHROME_LOG:-/tmp/chromium.log}"
CHROME_RESTART_DELAY="${CHROME_RESTART_DELAY:-1}"
chrome_pid=

stop() {
    trap - TERM INT
    if [ -n "$chrome_pid" ]; then
        kill "$chrome_pid" 2>/dev/null || true
        wait "$chrome_pid" 2>/dev/null || true
    fi
    exit 0
}

trap stop TERM INT

while :; do
    "$CHROME_BIN" \
        --no-sandbox \
        --disable-dev-shm-usage \
        --remote-debugging-address=0.0.0.0 \
        --remote-debugging-port=9222 \
        --user-data-dir=/tmp/chrome \
        --lang=en-US \
        --accept-lang=en-US,en \
        --kiosk \
        about:blank >>"$CHROME_LOG" 2>&1 &
    chrome_pid=$!

    if wait "$chrome_pid"; then
        exit_code=0
    else
        exit_code=$?
    fi
    chrome_pid=

    echo "Chromium exited with status $exit_code; restarting in ${CHROME_RESTART_DELAY}s" >&2
    sleep "$CHROME_RESTART_DELAY"
done
