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

# Files that should be cleaned up when no release is planned
STALE_FILES = ['tickets_export.json', 'processed_notes.json']


def is_weekday() -> bool:
    """Return True if today is Monday-Friday."""
    return datetime.now().weekday() < 5  # 0=Mon … 4=Fri


def cleanup_stale_exports():
    """Remove stale export files so previous day's notes don't leak through."""
    for filepath in STALE_FILES:
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"[Cleanup] Removed stale {filepath}")


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
        print(f"[Step 1] No release planned: '{release_summary}' not found in Jira")
        print(f"[Step 1] TIP: Check if the date format matches Jira (e.g., '5th February 2026')")
        cleanup_stale_exports()
        return None

    release_key = release_ticket['key']
    print(f"[Step 1] Found release ticket: {release_key}")

    print(f"[Step 1] Fetching linked tickets...")
    linked_tickets = jira.get_linked_tickets(release_key)

    if not linked_tickets:
        print("[Step 1] WARNING: No linked tickets found")
        cleanup_stale_exports()
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


def refresh_tickets():
    """Re-fetch tickets from Jira to pick up newly added fix versions or new tickets under existing fix versions.

    Compares against the previous tickets_export.json to detect new tickets.

    Returns:
        output file path if new tickets found, None otherwise
    """
    print("=" * 60)
    print("  REFRESH: Re-fetching Jira Tickets")
    print("=" * 60)

    # Load existing export to get release key and previous tickets
    try:
        with open("tickets_export.json", 'r') as f:
            previous_export = json.load(f)
    except FileNotFoundError:
        print("[Refresh] ERROR: tickets_export.json not found. Run export first.")
        return None

    release_key = previous_export.get("release_key")
    release_summary = previous_export.get("release_summary", "")
    if not release_key:
        print("[Refresh] ERROR: No release_key in tickets_export.json")
        return None

    previous_ticket_keys = {t.get("key") for t in previous_export.get("tickets", [])}
    print(f"[Refresh] Release ticket: {release_key}")
    print(f"[Refresh] Previously exported: {len(previous_ticket_keys)} tickets")

    # Connect to Jira and fetch current fix versions
    jira = JiraHandler()
    if not jira.test_connection():
        print("[Refresh] ERROR: Could not connect to Jira")
        return None

    current_fix_versions = jira.get_fix_versions_for_ticket(release_key)
    if not current_fix_versions:
        print("[Refresh] No fix versions found on release ticket")
        return None

    print(f"[Refresh] Current fix versions on {release_key}: {current_fix_versions}")

    # Fetch all tickets across all current fix versions
    all_tickets = jira.get_tickets_by_fix_versions(current_fix_versions, exclude_key=release_key)
    if not all_tickets:
        print("[Refresh] No tickets found across fix versions")
        return None

    # Filter out Hotfix tickets
    original_count = len(all_tickets)
    all_tickets = [
        t for t in all_tickets
        if "hotfix" not in t.get("fix_version", "").lower()
    ]
    hotfix_count = original_count - len(all_tickets)
    if hotfix_count > 0:
        print(f"[Refresh] Filtered out {hotfix_count} Hotfix ticket(s)")

    # Detect new tickets
    current_ticket_keys = {t.get("key") for t in all_tickets}
    new_ticket_keys = current_ticket_keys - previous_ticket_keys
    new_tickets = [t.get("key") for t in all_tickets if t.get("key") in new_ticket_keys]

    print(f"[Refresh] Total tickets now: {len(all_tickets)}")
    print(f"[Refresh] New tickets: {len(new_tickets)}")
    if new_tickets:
        print(f"[Refresh] New ticket keys: {new_tickets}")

    # Save updated export
    export_data = {
        "exported_at": datetime.now().isoformat(),
        "release_summary": release_summary,
        "release_key": release_key,
        "ticket_count": len(all_tickets),
        "tickets": all_tickets,
        "new_tickets": new_tickets,
        "refreshed_at": datetime.now().isoformat()
    }

    output_file = "tickets_export.json"
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)

    if new_tickets:
        print(f"\n[Refresh] UPDATED {output_file} with {len(new_tickets)} new ticket(s)")
        return output_file
    else:
        print(f"\n[Refresh] No new tickets found. Export is up to date.")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Export Jira tickets for release')
    parser.add_argument('--date', type=str, help='Release date (e.g., "5th Feb 2026"). Auto-detects today if not provided.')
    args = parser.parse_args()

    export_jira_tickets(args.date)
