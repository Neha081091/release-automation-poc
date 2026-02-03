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


def export_jira_tickets():
    """Fetch Jira tickets and export to JSON."""
    print("=" * 60)
    print("  HYBRID STEP 1: Export Jira Tickets")
    print("=" * 60)

    release_summary = os.getenv('RELEASE_TICKET_SUMMARY', 'Release 2nd February 2026')
    project_key = os.getenv('JIRA_PROJECT_KEY', 'DI')

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
    export_jira_tickets()
