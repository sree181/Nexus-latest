#!/bin/sh
# Stage 1A developer installer. Enterprise code signing, notarization, MDM, and
# keychain-backed queue keys are Stage 1B release responsibilities.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
BIN_DIR=${MESHAGENT_BIN_DIR:-"$HOME/.local/bin"}
VERSION=${MESHAGENT_RECORDER_VERSION:-"$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf dev)"}
TMP=$(mktemp -d)
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT HUP INT TERM

command -v go >/dev/null 2>&1 || { echo "Go 1.25.13+ is required" >&2; exit 1; }
if [ -d "$HOME/.meshagent/queues" ] && find "$HOME/.meshagent/queues" -type f \( -name '*.jsonl' -o -name '*.jsonl.inflight' \) -size +0c -print -quit | grep -q .; then
  echo "Undelivered Python recorder batches exist. Stop Cursor, run 'meshagent replay --json', and retry only after retained is 0." >&2
  exit 1
fi
mkdir -p "$BIN_DIR" "$HOME/.meshagent/logs"
chmod 700 "$HOME/.meshagent" "$HOME/.meshagent/logs"
(
  cd "$ROOT/recorder"
  go test ./...
  go build -trimpath -ldflags "-s -w -X main.version=$VERSION" -o "$TMP/meshagent-recorder" ./cmd/meshagent-recorder
  go build -trimpath -ldflags "-s -w -X main.version=$VERSION" -o "$TMP/meshagent-hook" ./cmd/meshagent-hook
)
install -m 700 "$TMP/meshagent-recorder" "$BIN_DIR/meshagent-recorder"
install -m 700 "$TMP/meshagent-hook" "$BIN_DIR/meshagent-hook"

case "$(uname -s)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.hypermesh.meshagent-recorder.plist"
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.hypermesh.meshagent-recorder</string>
  <key>ProgramArguments</key><array><string>$BIN_DIR/meshagent-recorder</string><string>serve</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HOME/.meshagent/logs/recorder.log</string>
  <key>StandardErrorPath</key><string>$HOME/.meshagent/logs/recorder.log</string>
</dict></plist>
EOF
    chmod 600 "$PLIST"
    launchctl bootout "gui/$(id -u)/com.hypermesh.meshagent-recorder" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    launchctl kickstart -k "gui/$(id -u)/com.hypermesh.meshagent-recorder"
    ;;
  Linux)
    UNIT_DIR="$HOME/.config/systemd/user"
    UNIT="$UNIT_DIR/meshagent-recorder.service"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT" <<EOF
[Unit]
Description=meshAgent native recorder
After=network-online.target

[Service]
Type=simple
ExecStart=$BIN_DIR/meshagent-recorder serve
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.meshagent

[Install]
WantedBy=default.target
EOF
    chmod 600 "$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable --now meshagent-recorder.service
    ;;
  *) echo "Unsupported operating system" >&2; exit 1 ;;
esac

"$BIN_DIR/meshagent-recorder" status
printf '\nInstalled Stage 1A development binaries in %s.\n' "$BIN_DIR"
printf 'The queue key is file-protected for development; enterprise keychain binding and signing are not claimed by this installer.\n'
