#!/usr/bin/env python3
"""
Startup Check for Release Automation

This script runs on laptop boot (via @reboot cron) and checks if today's
scheduled release notes run was missed. If the laptop was off at the
scheduled time (12:00 PM IST), this will trigger the pipeline automatically.

How it works:
1. Waits for network connectivity (retries for up to 2 minutes)
2. Checks if today is a weekday (Mon-Fri)
3. Checks if the current time is past the scheduled run time
4. Reads the last-run marker file to see if today's run already happened
5. If the run was missed, triggers the release notes pipeline

Usage:
    Crontab entry:
        @reboot cd /path/to/release-automation-poc && python startup_check.py >> logs/startup_check.log 2>&1

    Manual test:
        python startup_check.py
        python startup_check.py --force    # Skip time/day checks, only check last-run marker
        python startup_check.py --dry-run  # Check without actually running the pipeline
"""

import os
import sys
import time
import socket
import logging
import subprocess
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LAST_RUN_FILE = os.path.join(LOG_DIR, "last_run_date.txt")
LOG_FILE = os.path.join(LOG_DIR, "startup_check.log")

SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "12"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "0"))

# Network check settings
NETWORK_CHECK_HOST = "atlassian.net"
NETWORK_CHECK_PORT = 443
NETWORK_RETRY_LIMIT = 12          # 12 retries
NETWORK_RETRY_INTERVAL_SECS = 10  # 10s apart => ~2 minutes total

# Startup delay to let the system fully boot
BOOT_SETTLE_SECS = int(os.getenv("BOOT_SETTLE_SECS", "30"))

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def is_weekday() -> bool:
    """Return True if today is Monday–Friday."""
    return datetime.now().weekday() < 5  # 0=Mon … 4=Fri


def is_past_scheduled_time() -> bool:
    """Return True if the current time is past the scheduled run time."""
    now = datetime.now()
    return (now.hour > SCHEDULE_HOUR) or (
        now.hour == SCHEDULE_HOUR and now.minute >= SCHEDULE_MINUTE
    )


def read_last_run_date() -> str | None:
    """Read the date string from the last-run marker file."""
    if not os.path.exists(LAST_RUN_FILE):
        return None
    try:
        with open(LAST_RUN_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def write_last_run_date() -> None:
    """Write today's date to the last-run marker file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LAST_RUN_FILE, "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d"))
    logger.info(f"Updated last-run marker: {LAST_RUN_FILE}")


def already_ran_today() -> bool:
    """Return True if the pipeline already ran successfully today."""
    last = read_last_run_date()
    today = datetime.now().strftime("%Y-%m-%d")
    return last == today


def wait_for_network() -> bool:
    """Wait until network is available (or timeout)."""
    for attempt in range(1, NETWORK_RETRY_LIMIT + 1):
        try:
            sock = socket.create_connection(
                (NETWORK_CHECK_HOST, NETWORK_CHECK_PORT), timeout=5
            )
            sock.close()
            logger.info(f"Network available (attempt {attempt})")
            return True
        except OSError:
            logger.info(
                f"Network not ready (attempt {attempt}/{NETWORK_RETRY_LIMIT}), "
                f"retrying in {NETWORK_RETRY_INTERVAL_SECS}s..."
            )
            time.sleep(NETWORK_RETRY_INTERVAL_SECS)

    logger.error("Network not available after all retries")
    return False


def run_pipeline() -> bool:
    """Run the release notes pipeline via orchestrator's run_pipeline."""
    logger.info("Starting release notes pipeline...")

    try:
        result = subprocess.run(
            [sys.executable, "orchestrator.py", "--run-once"],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
            cwd=SCRIPT_DIR,
        )

        if result.returncode == 0:
            logger.info("Pipeline completed successfully")
            if result.stdout:
                # Log last 15 lines of output
                for line in result.stdout.strip().splitlines()[-15:]:
                    logger.info(f"  {line}")
            write_last_run_date()
            return True
        else:
            logger.error(f"Pipeline failed (exit code {result.returncode})")
            if result.stderr:
                for line in result.stderr.strip().splitlines()[-10:]:
                    logger.error(f"  {line}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Pipeline timed out after 600 seconds")
        return False
    except Exception as e:
        logger.error(f"Error running pipeline: {e}")
        return False


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main():
    force = "--force" in sys.argv
    dry_run = "--dry-run" in sys.argv

    logger.info("=" * 60)
    logger.info("Startup Check - Release Automation")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Flags: force={force}, dry_run={dry_run}")
    logger.info("=" * 60)

    # 1. Check if already ran today
    if already_ran_today():
        logger.info("Pipeline already ran today — skipping")
        return

    # 2. Check weekday (unless --force)
    if not force and not is_weekday():
        logger.info("Today is a weekend — skipping")
        return

    # 3. Check if past scheduled time (unless --force)
    if not force and not is_past_scheduled_time():
        logger.info(
            f"Current time is before scheduled time "
            f"({SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}) — "
            f"the normal scheduler will handle it"
        )
        return

    logger.info("Missed run detected! Preparing to run pipeline...")

    if dry_run:
        logger.info("[DRY RUN] Would run the pipeline now — exiting")
        return

    # 4. Wait for system to settle after boot
    logger.info(f"Waiting {BOOT_SETTLE_SECS}s for system to settle...")
    time.sleep(BOOT_SETTLE_SECS)

    # 5. Wait for network
    if not wait_for_network():
        logger.error("Aborting: no network connectivity")
        return

    # 6. Run the pipeline
    success = run_pipeline()

    if success:
        logger.info("Startup check completed — missed run recovered successfully")
    else:
        logger.error("Startup check completed — pipeline failed, check logs")


if __name__ == "__main__":
    main()
