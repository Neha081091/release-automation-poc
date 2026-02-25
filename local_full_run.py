#!/usr/bin/env python3
"""
Local full workflow using Socket Mode.

Runs all steps locally on Mac:
1) Export Jira tickets
2) Process with Claude (local API call)
3) Update Google Docs + Slack (socket mode)
"""

import sys
from datetime import datetime

from main import is_weekday, _today_date_str


def run_local_full() -> bool:
    print("=" * 60)
    print("  LOCAL FULL WORKFLOW (SOCKET MODE)")
    print("=" * 60)
    print(f"Started at: {datetime.now().isoformat()}")

    if not is_weekday():
        day_name = datetime.now().strftime('%A')
        print(f"[Workflow] Today is {day_name} — no releases on weekends. Skipping.")
        return True

    # Step 1: Export Jira tickets
    from hybrid_step1_export_jira import export_jira_tickets
    output_file = export_jira_tickets()
    if not output_file:
        print("[Workflow] No release found for today.")
        try:
            from slack_handler import SlackHandler
            # Force bot token to send from PMO Agent
            slack = SlackHandler(webhook_url="")
            if slack.test_connection():
                slack.send_no_release_notification(_today_date_str())
        except Exception as e:
            print(f"[Workflow] Could not send no-release Slack notification: {e}")
        return True

    # Step 2: Process with Claude (local)
    from hybrid_step2_process_claude import process_tickets_with_claude
    processed = process_tickets_with_claude()
    if not processed:
        print("[Workflow] ERROR: Claude processing failed.")
        return False

    # Step 3: Update Docs + Slack
    from hybrid_step3_update_docs import main as update_main
    update_main()
    return True


if __name__ == "__main__":
    success = run_local_full()
    sys.exit(0 if success else 1)
