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
from step1_jira_export import JiraExporter
from step2_claude_processor import ClaudeProcessor
from hybrid_step3_update_docs import GoogleDocsUpdater
from slack_socket_mode import post_approval_message, app

from slack_bolt.adapter.socket_mode import SocketModeHandler


def run_pipeline():
    """Run the complete release notes pipeline."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Release Notes Pipeline")
    print(f"{'='*60}\n")

    try:
        # Step 1: Fetch Jira tickets
        print("[Pipeline] Step 1: Fetching Jira tickets...")
        jira = JiraExporter()
        tickets = jira.fetch_release_tickets()

        if not tickets:
            print("[Pipeline] No tickets found for today. Skipping...")
            return False

        print(f"[Pipeline] Found {len(tickets)} tickets")

        # Step 2: Process with Claude
        print("\n[Pipeline] Step 2: Processing with Claude API...")
        claude = ClaudeProcessor()
        release_notes = claude.generate_release_notes(tickets)

        if not release_notes:
            print("[Pipeline] Failed to generate release notes")
            return False

        print("[Pipeline] Release notes generated successfully")

        # Step 3: Update Google Doc
        print("\n[Pipeline] Step 3: Updating Google Doc...")
        docs = GoogleDocsUpdater()
        doc_url = docs.update_document(release_notes)

        if doc_url:
            print(f"[Pipeline] Google Doc updated: {doc_url}")

        # Step 4: Post approval message to Slack
        print("\n[Pipeline] Step 4: Posting approval message to Slack...")
        message_ts = post_approval_message(release_notes)

        if message_ts:
            print(f"[Pipeline] Approval message posted: {message_ts}")

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


def main():
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


if __name__ == "__main__":
    main()
