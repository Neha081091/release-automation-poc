#!/bin/bash
#
# Setup script for Release Automation Auto-Start on macOS
#
# This script installs a LaunchAgent that automatically starts the
# release automation scheduler when you log in to your Mac.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.deepintent.release-automation.plist"
PLIST_SOURCE="$SCRIPT_DIR/$PLIST_NAME"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "╔═══════════════════════════════════════════════════════════════════╗"
echo "║     Release Automation - Auto-Start Setup for macOS               ║"
echo "╚═══════════════════════════════════════════════════════════════════╝"
echo ""

# Check if plist source exists
if [ ! -f "$PLIST_SOURCE" ]; then
    echo "ERROR: $PLIST_SOURCE not found!"
    exit 1
fi

# Create LaunchAgents directory if it doesn't exist
if [ ! -d "$LAUNCH_AGENTS_DIR" ]; then
    echo "[1/4] Creating LaunchAgents directory..."
    mkdir -p "$LAUNCH_AGENTS_DIR"
else
    echo "[1/4] LaunchAgents directory exists"
fi

# Update the plist with the actual project path
echo "[2/4] Configuring LaunchAgent with your project path..."
ESCAPED_PATH=$(echo "$SCRIPT_DIR" | sed 's/\//\\\//g')

# Create a temporary plist with the correct path
sed "s|~/release-automation-poc|$SCRIPT_DIR|g" "$PLIST_SOURCE" > "/tmp/$PLIST_NAME"

# Copy to LaunchAgents
echo "[3/4] Installing LaunchAgent..."
cp "/tmp/$PLIST_NAME" "$PLIST_DEST"
rm "/tmp/$PLIST_NAME"

# Load the LaunchAgent
echo "[4/4] Loading LaunchAgent..."
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo ""
echo "============================================================"
echo "  SUCCESS! Auto-start is now configured."
echo "============================================================"
echo ""
echo "What happens now:"
echo "  - When you log in to your Mac, the scheduler starts automatically"
echo "  - If the 12:00 PM run was missed (laptop was off), it catches up"
echo "  - Logs: /tmp/release-automation.log"
echo ""
echo "Useful commands:"
echo "  Check status:    launchctl list | grep release-automation"
echo "  View logs:       tail -f /tmp/release-automation.log"
echo "  Stop:            launchctl unload $PLIST_DEST"
echo "  Start manually:  launchctl load $PLIST_DEST"
echo "  Uninstall:       rm $PLIST_DEST"
echo ""
echo "To test: Restart your laptop and check /tmp/release-automation.log"
echo ""
