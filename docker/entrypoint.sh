#!/bin/sh
set -eu
Xvfb :99 -screen 0 1440x900x24 &
fluxbox >/tmp/fluxbox.log 2>&1 &
/usr/local/bin/chromium-supervisor.sh &
CHROMIUM_SUPERVISOR_PID=$!
x11vnc -display :99 -forever -shared -rfbport 5900 -nopw >/tmp/x11vnc.log 2>&1 &
# Serve the VNC desktop through the minimal Rove canvas and keep /vnc.html as a debug fallback.
websockify --web=/usr/share/novnc 6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &

cleanup() {
  trap - TERM INT EXIT
  kill "$CHROMIUM_SUPERVISOR_PID" 2>/dev/null || true
  wait "$CHROMIUM_SUPERVISOR_PID" 2>/dev/null || true
}

trap cleanup TERM INT EXIT
python -m app.server
