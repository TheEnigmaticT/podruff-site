#!/usr/bin/env bash
# install-video-poll-launchd.sh
# Installs the com.crowdtamers.video-poll launchd job on macOS.
# Run this script once to register the 15-minute video poll daemon.

set -e

PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/launchd/com.crowdtamers.video-poll.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.crowdtamers.video-poll.plist"
LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)/logs"
LABEL="com.crowdtamers.video-poll"

echo "==> Creating logs directory..."
mkdir -p "$LOG_DIR"

echo "==> Copying plist to LaunchAgents..."
cp "$PLIST_SRC" "$PLIST_DEST"

echo "==> Unloading any existing job (errors ignored)..."
launchctl unload "$PLIST_DEST" 2>/dev/null || true

echo "==> Loading launchd job..."
launchctl load -w "$PLIST_DEST"

echo ""
echo "==> Status:"
launchctl list | grep video-poll || echo "(job not yet visible — it will appear after the first run)"

echo ""
echo "Done. The video poll job will run every 15 minutes."
echo "Logs:"
echo "  stdout: $LOG_DIR/video-poll.out.log"
echo "  stderr: $LOG_DIR/video-poll.err.log"
