#!/usr/bin/env python3
"""
HYBRID STEP 1: Export Jira Tickets (Run on Mac)

This script fetches Jira tickets and exports them to a JSON file
that can be processed by Claude API on the server.

Usage:
    python hybrid_step1_export_jira.py                    # Auto-detect today's release
    python hybrid_step1_export_jira.py --date "5th Feb 2026"  # Specific date

Output:
    tickets_export.json
"""

import json
import os
import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from jira_handler import JiraHandler


def get_day_suffix(day: int) -> str:
    """Get the ordinal suffix for a day (1st, 2nd, 3rd, 4th, etc.)."""
    if 11 <= day <= 13:
        return 'th'
    suffix_map = {1: 'st', 2: 'nd', 3: 'rd'}
    return suffix_map.get(day % 10, 'th')


def format_release_date(date: datetime = None) -> str:
    """
    Format a date as 'Release {day}{suffix} {Month} {Year}'.

    Examples:
        - Release 13th Oct 2025
        - Release 2nd February 2026
        - Release 1st March 2026
    """
    if date is None:
        date = datetime.now()

    day = date.day
    suffix = get_day_suffix(day)
    month = date.strftime('%B')  # Full month name
    year = date.year

    return f"Release {day}{suffix} {month} {year}"


def export_jira_tickets(release_date_str: str = None):
    """Fetch Jira tickets and export to JSON."""
    print("=" * 60)
    print("  HYBRID STEP 1: Export Jira Tickets")
    print("=" * 60)

    # Auto-detect today's release if no date provided
    if release_date_str:
        release_summary = f"Release {release_date_str}"
    else:
        # Check environment variable first, then auto-detect
        env_summary = os.getenv('RELEASE_TICKET_SUMMARY', '')
        if env_summary:
            release_summary = env_summary
            print(f"\n[Step 1] Using release from env: {release_summary}")
        else:
            release_summary = format_release_date(datetime.now())
            print(f"\n[Step 1] Auto-detected today's release: {release_summary}")

    project_key = os.getenv('JIRA_PROJECT_KEY', 'DI')

    print(f"[Step 1] Connecting to Jira...")
    jira = JiraHandler()

    if not jira.test_connection():
        print("[Step 1] ERROR: Could not connect to Jira")
        return None

    print(f"[Step 1] Searching for release ticket: '{release_summary}'")
    release_ticket = jira.find_release_ticket(release_summary, project_key)

    if not release_ticket:
        print(f"[Step 1] ERROR: Release ticket not found: '{release_summary}'")
        print(f"[Step 1] TIP: Check if the date format matches Jira (e.g., '5th February 2026')")
        return None

    release_key = release_ticket['key']
    print(f"[Step 1] Found release ticket: {release_key}")

    print(f"[Step 1] Fetching linked tickets...")
    linked_tickets = jira.get_linked_tickets(release_key)

    if not linked_tickets:
        print("[Step 1] WARNING: No linked tickets found")
        return None

    print(f"[Step 1] Found {len(linked_tickets)} tickets")

    # Filter out Hotfix tickets
    original_count = len(linked_tickets)
    linked_tickets = [
        t for t in linked_tickets
        if "hotfix" not in t.get("fix_version", "").lower()
    ]
    hotfix_count = original_count - len(linked_tickets)
    if hotfix_count > 0:
        print(f"[Step 1] Filtered out {hotfix_count} Hotfix ticket(s)")
        print(f"[Step 1] Remaining: {len(linked_tickets)} tickets")

    # Export to JSON
    export_data = {
        "exported_at": datetime.now().isoformat(),
        "release_summary": release_summary,
        "release_key": release_key,
        "ticket_count": len(linked_tickets),
        "tickets": linked_tickets
    }

    output_file = "tickets_export.json"
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)

    print(f"\n[Step 1] EXPORTED to: {output_file}")
    print(f"[Step 1] Tickets: {len(linked_tickets)}")
    print("\n" + "=" * 60)
    print("  NEXT: Copy tickets_export.json to server and run:")
    print("  python hybrid_step2_process_claude.py")
    print("=" * 60)

    return output_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Export Jira tickets for release')
    parser.add_argument('--date', type=str, help='Release date (e.g., "5th Feb 2026"). Auto-detects today if not provided.')
    args = parser.parse_args()

    export_jira_tickets(args.date)
