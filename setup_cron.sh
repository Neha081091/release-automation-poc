#!/bin/bash
# ============================================================
# Setup cron jobs for Release Automation
#
# This script installs two cron entries:
#   1. @reboot  - Starts Slack Socket Mode + checks for missed runs
#   2. Daily    - Runs release notes at 12:00 PM (Mon-Fri)
#
# Usage:
#   chmod +x setup_cron.sh
#   ./setup_cron.sh          # Install cron jobs
#   ./setup_cron.sh --remove # Remove cron jobs
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3 || which python)"
MARKER="# release-automation-poc"

# Cron entries
REBOOT_CRON="@reboot cd ${SCRIPT_DIR} && ${PYTHON} startup_check.py >> logs/startup_check.log 2>&1 ${MARKER}"
SOCKET_CRON="@reboot cd ${SCRIPT_DIR} && ${PYTHON} slack_socket_mode.py >> logs/socket_mode.log 2>&1 ${MARKER}"
DAILY_CRON="0 12 * * 1-5 cd ${SCRIPT_DIR} && ${PYTHON} orchestrator.py --run-once >> logs/daily_run.log 2>&1 ${MARKER}"

remove_jobs() {
    echo "Removing release-automation-poc cron jobs..."
    crontab -l 2>/dev/null | grep -v "${MARKER}" | crontab -
    echo "Done. Current crontab:"
    crontab -l 2>/dev/null || echo "  (empty)"
}

install_jobs() {
    # Remove old entries first to avoid duplicates
    existing=$(crontab -l 2>/dev/null | grep -v "${MARKER}")

    echo "Installing release-automation-poc cron jobs..."

    # Write new crontab
    {
        echo "${existing}"
        echo ""
        echo "# --- Release Automation PoC ---"
        echo "${REBOOT_CRON}"
        echo "${SOCKET_CRON}"
        echo "${DAILY_CRON}"
    } | crontab -

    echo ""
    echo "Installed cron jobs:"
    echo "  1. @reboot  -> startup_check.py  (recover missed runs on boot)"
    echo "  2. @reboot  -> slack_socket_mode.py  (Slack button listener)"
    echo "  3. Daily    -> orchestrator.py --run-once at 12:00 PM Mon-Fri"
    echo ""
    echo "Current crontab:"
    crontab -l
}

# Create logs directory
mkdir -p "${SCRIPT_DIR}/logs"

case "${1}" in
    --remove)
        remove_jobs
        ;;
    *)
        install_jobs
        ;;
esac
