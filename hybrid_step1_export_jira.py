#!/usr/bin/env python3
"""
HYBRID STEP 1: Export Jira Tickets (Run on Mac)

This script fetches Jira tickets and exports them to a JSON file
that can be processed by Claude API on the server.

Usage:
    python hybrid_step1_export_jira.py

Output:
    tickets_export.json
"""

import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from jira_handler import JiraHandler


def _ordinal(day: int) -> str:
    """Return day with ordinal suffix (1st, 2nd, 3rd, 4th, ...)."""
    if 11 <= day <= 13:
        return f"{day}th"
    return f"{day}{['th','st','nd','rd','th','th','th','th','th','th'][day % 10]}"


def _today_release_summary() -> str:
    """Build 'Release 14th February 2026' for today's date."""
    today = datetime.now()
    return f"Release {_ordinal(today.day)} {today.strftime('%B %Y')}"


def is_weekday() -> bool:
    """Return True if today is Monday-Friday."""
    return datetime.now().weekday() < 5  # 0=Mon … 4=Fri


def export_jira_tickets():
    """Fetch Jira tickets and export to JSON."""
    print("=" * 60)
    print("  HYBRID STEP 1: Export Jira Tickets")
    print("=" * 60)

    # Weekend guard
    if not is_weekday():
        day_name = datetime.now().strftime('%A')
        print(f"\n[Step 1] Today is {day_name} — no releases on weekends. Skipping.")
        return None

    # Auto-detect today's release summary; fall back to env override
    release_summary = os.getenv('RELEASE_TICKET_SUMMARY') or _today_release_summary()
    project_key = os.getenv('JIRA_PROJECT_KEY', 'DI')
    print(f"\n[Step 1] Auto-detected today's release: {release_summary}")

    print(f"\n[Step 1] Connecting to Jira...")
    jira = JiraHandler()

    if not jira.test_connection():
        print("[Step 1] ERROR: Could not connect to Jira")
        return None

    print(f"[Step 1] Searching for release ticket: '{release_summary}'")
    release_ticket = jira.find_release_ticket(release_summary, project_key)

    if not release_ticket:
        print(f"[Step 1] ERROR: Release ticket not found")
        return None

    release_key = release_ticket['key']
    print(f"[Step 1] Found release ticket: {release_key}")

    print(f"[Step 1] Fetching linked tickets...")
    linked_tickets = jira.get_linked_tickets(release_key)

    if not linked_tickets:
        print("[Step 1] WARNING: No linked tickets found")
        return None

    print(f"[Step 1] Found {len(linked_tickets)} tickets")

    # Collect unique fix versions for refresh capability (exclude Hotfix versions)
    fix_version_set = set()
    for t in linked_tickets:
        fv = t.get("fix_version")
        if fv and "hotfix" not in fv.lower():
            fix_version_set.add(fv)

    # Export to JSON
    export_data = {
        "exported_at": datetime.now().isoformat(),
        "release_summary": release_summary,
        "release_key": release_key,
        "fix_versions": sorted(fix_version_set),
        "ticket_count": len(linked_tickets),
        "tickets": linked_tickets
    }

    output_file = "tickets_export.json"
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)

    print(f"\n[Step 1] EXPORTED to: {output_file}")
    print(f"[Step 1] Tickets: {len(linked_tickets)}")
    print(f"[Step 1] Fix Versions: {sorted(fix_version_set)}")
    print("\n" + "=" * 60)
    print("  NEXT: Copy tickets_export.json to server and run:")
    print("  python hybrid_step2_process_claude.py")
    print("=" * 60)

    return output_file


def refresh_tickets():
    """
    Refresh tickets by re-querying Jira for the same fix versions
    from the existing export. Picks up any newly added stories/bugs.

    Returns:
        Path to updated export file, or None on failure.
    """
    print("=" * 60)
    print("  REFRESH: Re-fetching Tickets from Jira")
    print("=" * 60)

    input_file = "tickets_export.json"
    if not os.path.exists(input_file):
        print("[Refresh] ERROR: tickets_export.json not found. Run export first.")
        return None

    with open(input_file, 'r') as f:
        export_data = json.load(f)

    release_key = export_data.get("release_key")
    release_summary = export_data.get("release_summary", "")
    # Filter out any Hotfix versions that may have been stored from older exports
    old_fix_versions = [
        fv for fv in export_data.get("fix_versions", [])
        if "hotfix" not in fv.lower()
    ]
    old_ticket_keys = {t["key"] for t in export_data.get("tickets", [])}

    print(f"[Refresh] Release: {release_summary} ({release_key})")
    print(f"[Refresh] Previous ticket count: {len(old_ticket_keys)}")
    print(f"[Refresh] Fix Versions to re-query: {old_fix_versions}")

    if not old_fix_versions and not release_key:
        print("[Refresh] ERROR: No fix versions or release key in export to refresh from.")
        return None

    jira = JiraHandler()
    if not jira.test_connection():
        print("[Refresh] ERROR: Could not connect to Jira")
        return None

    # Re-check the release ticket for any newly added fix versions
    current_fix_versions = set(old_fix_versions)
    if release_key:
        latest_fix_versions = jira.get_fix_versions_for_ticket(release_key)
        new_fix_versions = set(latest_fix_versions) - set(old_fix_versions)
        if new_fix_versions:
            print(f"[Refresh] NEW fix versions detected on release ticket: {sorted(new_fix_versions)}")
            current_fix_versions.update(new_fix_versions)
        else:
            print("[Refresh] No new fix versions on release ticket.")

    # Re-fetch all tickets from the combined fix versions
    all_tickets = []
    if current_fix_versions:
        all_tickets = jira.get_tickets_by_fix_versions(
            sorted(current_fix_versions), exclude_key=release_key
        )
    else:
        # Fallback: re-fetch via linked tickets
        all_tickets = jira.get_linked_tickets(release_key)

    if not all_tickets:
        print("[Refresh] No tickets found. Nothing to update.")
        return None

    new_ticket_keys = {t["key"] for t in all_tickets}
    added = new_ticket_keys - old_ticket_keys
    removed = old_ticket_keys - new_ticket_keys

    print(f"[Refresh] Total tickets now: {len(all_tickets)}")
    if added:
        print(f"[Refresh] NEW tickets added: {sorted(added)}")
    if removed:
        print(f"[Refresh] Tickets removed: {sorted(removed)}")
    if not added and not removed:
        print("[Refresh] No changes detected — ticket list is up to date.")

    # Collect fix versions from refreshed set (exclude Hotfix versions)
    fix_version_set = set()
    for t in all_tickets:
        fv = t.get("fix_version")
        if fv and "hotfix" not in fv.lower():
            fix_version_set.add(fv)

    # Write updated export
    updated_export = {
        "exported_at": datetime.now().isoformat(),
        "release_summary": release_summary,
        "release_key": release_key,
        "fix_versions": sorted(fix_version_set),
        "ticket_count": len(all_tickets),
        "refreshed": True,
        "previous_count": len(old_ticket_keys),
        "new_tickets": sorted(added),
        "tickets": all_tickets
    }

    with open(input_file, 'w') as f:
        json.dump(updated_export, f, indent=2, default=str)

    print(f"\n[Refresh] UPDATED {input_file} with {len(all_tickets)} tickets")
    if added:
        print(f"[Refresh] {len(added)} new ticket(s) pulled in")

    return input_file


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh":
        refresh_tickets()
    else:
        export_jira_tickets()
