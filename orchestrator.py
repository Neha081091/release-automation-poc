"""
Release Automation Orchestrator

This script:
1. Runs the Socket Mode handler for Slack button clicks
2. Schedules the release notes pipeline to run at 12:00 PM daily

Usage:
    python orchestrator.py

The script will:
- Start listening for Slack button clicks immediately
- Run the release notes pipeline at 12:00 PM daily
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


def main(daemon_mode=False):
    """Main orchestrator function."""

    print("""
╔══════════════════════════════════════════════════════════════╗
║         Release Automation Orchestrator                      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  This script:                                                ║
║  • Listens for Slack button clicks (Socket Mode)             ║
║  • Runs release pipeline at 12:00 PM daily                   ║
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

    # Set up the scheduler
    scheduler = BackgroundScheduler()

    # Schedule pipeline to run at 12:00 PM daily
    scheduler.add_job(
        run_pipeline,
        CronTrigger(hour=12, minute=0),
        id='daily_pipeline',
        name='Daily Release Notes Pipeline'
    )

    scheduler.start()

    print(f"[Scheduler] Pipeline scheduled to run daily at 12:00 PM")
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
    args = parser.parse_args()
    main(daemon_mode=args.daemon)
