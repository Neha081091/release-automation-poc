"""
Release Automation Orchestrator

This script:
1. Runs the Socket Mode handler for Slack button clicks
2. Schedules the release notes pipeline to run at 12:00 PM daily
3. On startup, checks if today's scheduled run was missed (e.g. laptop was off)
   and triggers the pipeline automatically

Usage:
    python orchestrator.py                # Interactive mode
    python orchestrator.py --daemon       # Background daemon mode
    python orchestrator.py --run-once     # Run pipeline once and exit (used by startup_check.py)

The script will:
- Start listening for Slack button clicks immediately
- Run the release notes pipeline at 12:00 PM daily
- On startup, recover any missed run if the laptop was off at scheduled time
- You can also trigger manually by pressing Enter
"""

import os
import sys
import threading
import time
from datetime import datetime
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

load_dotenv()

# Import the pipeline components
from slack_socket_mode import post_approval_message, app
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Import the hybrid step modules
import subprocess

# -------------------------------------------------------------------
# Last-run marker helpers
# -------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LAST_RUN_FILE = os.path.join(LOG_DIR, "last_run_date.txt")

SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "12"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", "0"))


def _read_last_run_date() -> str | None:
    """Read the date from the last-run marker file."""
    if not os.path.exists(LAST_RUN_FILE):
        return None
    try:
        with open(LAST_RUN_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _write_last_run_date() -> None:
    """Write today's date to the last-run marker file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LAST_RUN_FILE, "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d"))
    print(f"[Marker] Updated last-run marker: {LAST_RUN_FILE}")


def _already_ran_today() -> bool:
    """Return True if the pipeline already ran successfully today."""
    return _read_last_run_date() == datetime.now().strftime("%Y-%m-%d")


def _is_missed_run() -> bool:
    """Return True if today's scheduled run was missed and should be recovered."""
    now = datetime.now()
    # Only on weekdays
    if now.weekday() >= 5:
        return False
    # Only after the scheduled time
    if now.hour < SCHEDULE_HOUR or (now.hour == SCHEDULE_HOUR and now.minute < SCHEDULE_MINUTE):
        return False
    # Only if not already run today
    return not _already_ran_today()


def run_pipeline():
    """Run the complete release notes pipeline."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Release Notes Pipeline")
    print(f"{'='*60}\n")

    try:
        # Step 1: Fetch Jira tickets
        print("[Pipeline] Step 1: Fetching Jira tickets...")
        result = subprocess.run(
            ['python', 'hybrid_step1_export_jira.py'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[Pipeline] Step 1 failed: {result.stderr}")
            return False

        # Step 2: Process with Claude
        print("\n[Pipeline] Step 2: Processing with Claude API...")
        result = subprocess.run(
            ['python', 'hybrid_step2_process_claude.py'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[Pipeline] Step 2 failed: {result.stderr}")
            return False

        # Step 3: Update Google Doc & Post to Slack
        print("\n[Pipeline] Step 3: Updating Google Doc & posting to Slack...")
        result = subprocess.run(
            ['python', 'hybrid_step3_update_docs.py'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[Pipeline] Step 3 failed: {result.stderr}")
            return False

        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pipeline Complete!")
        print(f"{'='*60}\n")

        # Mark today's run as complete so it won't re-trigger on reboot
        _write_last_run_date()

        return True

    except Exception as e:
        print(f"[Pipeline] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def start_socket_mode():
    """Start the Socket Mode handler in a separate thread."""
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token:
        print("[Socket Mode] ERROR: SLACK_APP_TOKEN not found in .env")
        return

    print("[Socket Mode] Starting Socket Mode handler...")
    handler = SocketModeHandler(app, app_token)
    handler.start()


def check_and_recover_missed_run() -> bool:
    """Check if today's scheduled run was missed and run it now if so.

    Returns True if a missed run was recovered, False otherwise.
    """
    if not _is_missed_run():
        return False

    print(f"\n[Startup] Missed run detected!")
    print(f"[Startup] Last run date: {_read_last_run_date() or 'never'}")
    print(f"[Startup] Scheduled time was {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}, "
          f"current time is {datetime.now().strftime('%H:%M')}")
    print(f"[Startup] Triggering pipeline now...\n")

    success = run_pipeline()
    if success:
        print("[Startup] Missed run recovered successfully!")
    else:
        print("[Startup] Missed run recovery FAILED — check logs")
    return success


def main(daemon_mode=False, run_once=False):
    """Main orchestrator function."""

    # --run-once: just run the pipeline and exit (used by startup_check.py)
    if run_once:
        print(f"[Run Once] Running pipeline at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        success = run_pipeline()
        sys.exit(0 if success else 1)

    print("""
╔══════════════════════════════════════════════════════════════╗
║         Release Automation Orchestrator                      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  This script:                                                ║
║  • Listens for Slack button clicks (Socket Mode)             ║
║  • Runs release pipeline at 12:00 PM daily                   ║
║  • On startup, recovers missed runs if laptop was off        ║
║                                                              ║
║  Commands:                                                   ║
║  • Press Enter  - Run pipeline manually                      ║
║  • Type 'quit'  - Exit the orchestrator                      ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

    # Check required environment variables
    required_vars = ['SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'SLACK_REVIEW_CHANNEL']
    missing = [v for v in required_vars if not os.getenv(v)]

    if missing:
        print(f"[Error] Missing environment variables: {', '.join(missing)}")
        print("Please add them to your .env file")
        return

    # Start Socket Mode in a background thread
    socket_thread = threading.Thread(target=start_socket_mode, daemon=True)
    socket_thread.start()

    # Give Socket Mode time to connect
    time.sleep(2)

    # --- Startup: check for missed run ---
    check_and_recover_missed_run()

    # Set up the scheduler
    scheduler = BackgroundScheduler()

    # Schedule pipeline to run at 12:00 PM daily
    scheduler.add_job(
        run_pipeline,
        CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id='daily_pipeline',
        name='Daily Release Notes Pipeline'
    )

    scheduler.start()

    print(f"[Scheduler] Pipeline scheduled to run daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")
    print(f"[Scheduler] Next run: {scheduler.get_job('daily_pipeline').next_run_time}")
    print(f"\n[Ready] Listening for Slack button clicks...")

    # Check if running in interactive mode (terminal) or background
    if not daemon_mode and sys.stdin.isatty():
        print("[Ready] Press Enter to run pipeline manually, or type 'quit' to exit\n")
        try:
            while True:
                user_input = input()

                if user_input.lower() == 'quit':
                    print("\n[Shutdown] Stopping orchestrator...")
                    scheduler.shutdown()
                    break
                else:
                    # Run pipeline manually
                    print("\n[Manual] Running pipeline now...")
                    run_pipeline()
                    print("\n[Ready] Press Enter to run again, or type 'quit' to exit\n")

        except KeyboardInterrupt:
            print("\n[Shutdown] Stopping orchestrator...")
            scheduler.shutdown()
    else:
        # Running in background mode (nohup, etc.)
        print("[Background] Running in daemon mode. Check logs for updates.\n")
        try:
            while True:
                time.sleep(60)  # Keep alive, check every minute
        except KeyboardInterrupt:
            print("\n[Shutdown] Stopping orchestrator...")
            scheduler.shutdown()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true', help='Run in daemon mode (no input)')
    parser.add_argument('--run-once', action='store_true', help='Run pipeline once and exit')
    args = parser.parse_args()
    main(daemon_mode=args.daemon, run_once=args.run_once)
