#!/usr/bin/env bash
# Run on campi (the Pi):  bash setup_campi_stream.sh [path/to/mjpeg_server_2.py]
#   QUALITY=HIGH|VERY_HIGH|MEDIUM  (default HIGH on Wi-Fi, VERY_HIGH wired)
#   SIZE=1920x1440                 (4:3; 1920 is the widest the Pi 4 hardware JPEG encoder supports)
# Makes the Picamera2 MJPEG server a systemd *user* service (no sudo; starts at boot via linger) and sets
# the stream size and MJPEG quality. Port 8000 drops for a few seconds while switching over.
set -euo pipefail

SCRIPT="${1:-$HOME/timelapse/mjpeg_server_2.py}"
[ -f "$SCRIPT" ] || { echo "not found: $SCRIPT"; exit 1; }
SIZE="${SIZE:-1920x1440}"
DEV=$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')
if [ -z "${QUALITY:-}" ]; then QUALITY=VERY_HIGH; [[ "$DEV" == wlan* ]] && QUALITY=HIGH; fi
echo "server: $SCRIPT  size: $SIZE  network: $DEV  quality: $QUALITY"

cp "$SCRIPT" "$SCRIPT.bak-$(date +%Y%m%d-%H%M%S)"
python3 - "$SCRIPT" "$QUALITY" "$SIZE" <<'EOF'
import re, sys
path, q, size = sys.argv[1], sys.argv[2], sys.argv[3]
w, h = size.split("x")
src = open(path).read()
src, n = re.subn(r'main=\{"size": \(\d+, \d+\)\}', f'main={{"size": ({w}, {h})}}', src)
if n != 1:
    sys.exit(f'expected one main={{"size": (...)}} config, found {n}; edit by hand')
calls = re.findall(r"start_recording\((.*)\)", src)
if len(calls) != 1:
    sys.exit(f"expected exactly one start_recording(...) call, found {len(calls)}; edit by hand")
args = re.sub(r",\s*quality=Quality\.\w+", "", calls[0])
src = src.replace(f"start_recording({calls[0]})", f"start_recording({args}, quality=Quality.{q})")
if not re.search(r"^from picamera2\.encoders import .*\bQuality\b", src, re.M):
    src = src.replace("from picamera2.encoders import MJPEGEncoder",
                      "from picamera2.encoders import MJPEGEncoder, Quality")
open(path, "w").write(src)
print("patched:", re.search(r"picam2\.configure\(.*\)", src).group(0))
print("patched:", f"start_recording({args}, quality=Quality.{q})")
EOF

mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/campi-stream.service <<EOF
[Unit]
Description=Campi Picamera2 MJPEG stream on :8000
After=network-online.target

[Service]
WorkingDirectory=$(dirname "$SCRIPT")
ExecStart=/usr/bin/python3 $SCRIPT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

# A second launcher would fight the service for the camera after a reboot.
if crontab -l 2>/dev/null | grep -q '^[^#].*mjpeg_server_2'; then
    crontab -l | sed '/^[^#].*mjpeg_server_2/s/^/# disabled by campi-stream.service: /' | crontab -
    echo "commented out the mjpeg_server_2 line in crontab"
fi

# Stop the manually started server (only one process can open the camera), then start the service.
pkill -f 'python3 .*mjpeg_server_2.py' || true
sleep 2
systemctl --user daemon-reload
systemctl --user enable --now campi-stream.service
sleep 5
systemctl --user --no-pager --lines=5 status campi-stream.service || true
[ "$(loginctl show-user "$USER" -p Linger --value)" = yes ] \
    || echo "WARNING: run 'sudo loginctl enable-linger $USER' so the service starts at boot without a login"
