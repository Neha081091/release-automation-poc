#!/usr/bin/env python3
"""
Release Automation PoC - Main Orchestration Script

This script orchestrates the complete release automation workflow:
1. Fetch release tickets from Jira
2. Generate formatted release notes
3. Update Google Doc with release notes
4. Send Slack notification for approval
5. Handle approval workflow
6. Post final release notes to Slack

Author: DeepIntent Release Automation Team
"""

import os
import sys
import argparse
from datetime import datetime
from typing import Dict, Optional, Tuple

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Import handlers
from jira_handler import JiraHandler
from formatter import ReleaseNotesFormatter
from google_docs_handler import GoogleDocsHandler, create_formatted_requests
from slack_handler import SlackHandler


def _ordinal(day: int) -> str:
    """Return day with ordinal suffix (1st, 2nd, 3rd, 4th, ...)."""
    if 11 <= day <= 13:
        return f"{day}th"
    return f"{day}{['th','st','nd','rd','th','th','th','th','th','th'][day % 10]}"


def _today_date_str() -> str:
    """Return today's date formatted like '14th February 2026'."""
    today = datetime.now()
    return f"{_ordinal(today.day)} {today.strftime('%B %Y')}"


def is_weekday() -> bool:
    """Return True if today is Monday-Friday."""
    return datetime.now().weekday() < 5  # 0=Mon … 4=Fri


def print_banner():
    """Print application banner."""
    print("""
╔═══════════════════════════════════════════════════════════════════╗
║           RELEASE AUTOMATION POC - DeepIntent                     ║
║                                                                   ║
║   Automated end-to-end release announcement workflow              ║
╚═══════════════════════════════════════════════════════════════════╝
    """)


def print_step(step_num: int, title: str):
    """Print a step header."""
    print(f"\n{'='*60}")
    print(f"  STEP {step_num}: {title}")
    print(f"{'='*60}\n")


def step1_fetch_jira_tickets(release_summary: str = None) -> Tuple[Optional[Dict], list]:
    """
    STEP 1: Read all tickets from Release Version.

    Connects to Jira, finds the release ticket, and extracts all linked tickets.

    Args:
        release_summary: Summary of the release ticket to find

    Returns:
        Tuple of (release_ticket, linked_tickets)
    """
    print_step(1, "FETCH JIRA TICKETS")

    release_summary = release_summary or os.getenv('RELEASE_TICKET_SUMMARY', 'Release 2nd February 2026')
    project_key = os.getenv('JIRA_PROJECT_KEY', 'DI')

    try:
        # Initialize Jira handler
        jira = JiraHandler()

        # Test connection
        print("[Step 1] Testing Jira connection...")
        if not jira.test_connection():
            print("[Step 1] ERROR: Could not connect to Jira")
            return None, []

        # Find release ticket
        print(f"[Step 1] Searching for release ticket: '{release_summary}'")
        release_ticket = jira.find_release_ticket(release_summary, project_key)

        if not release_ticket:
            print(f"[Step 1] ERROR: Release ticket not found: '{release_summary}'")
            return None, []

        release_key = release_ticket['key']
        print(f"[Step 1] Found release ticket: {release_key}")
        print(f"[Step 1] Summary: {release_ticket['fields']['summary']}")

        # Get linked tickets
        print(f"\n[Step 1] Fetching linked tickets for {release_key}...")
        linked_tickets = jira.get_linked_tickets(release_key)

        if not linked_tickets:
            print("[Step 1] WARNING: No linked tickets found")
            print("[Step 1] This may indicate the release ticket has no linked items")
            return release_ticket, []

        print(f"\n[Step 1] Successfully fetched {len(linked_tickets)} linked tickets:")
        for ticket in linked_tickets:
            issue_type = ticket.get('issue_type', 'Unknown')
            epic = ticket.get('epic_name', 'No Epic')
            print(f"  - {ticket['key']}: {ticket['summary'][:50]}...")
            print(f"    Type: {issue_type} | Epic: {epic}")

        print(f"\n[Step 1] COMPLETE: Found {len(linked_tickets)} tickets for release")
        return release_ticket, linked_tickets

    except Exception as e:
        print(f"[Step 1] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None, []


def step2_create_release_notes(tickets: list, release_date: str = None) -> Tuple[Optional[ReleaseNotesFormatter], str]:
    """
    STEP 2: Create Release Notes.

    Formats the tickets into structured release notes.

    Args:
        tickets: List of ticket data from Jira
        release_date: Release date string

    Returns:
        Tuple of (formatter, plain_text_notes)
    """
    print_step(2, "CREATE RELEASE NOTES")

    if not tickets:
        print("[Step 2] ERROR: No tickets provided")
        return None, ""

    # Parse release date from environment or use default
    if not release_date:
        release_summary = os.getenv('RELEASE_TICKET_SUMMARY', 'Release 2nd February 2026')
        # Extract date from "Release 2nd February 2026"
        if 'Release ' in release_summary:
            release_date = release_summary.replace('Release ', '')
        else:
            release_date = datetime.now().strftime("%d %B %Y")

    print(f"[Step 2] Release date: {release_date}")
    print(f"[Step 2] Processing {len(tickets)} tickets...")

    try:
        # Initialize formatter
        formatter = ReleaseNotesFormatter(release_date)

        # Process and group tickets
        grouped_data = formatter.process_tickets(tickets)

        # Generate TL;DR
        tldr = formatter.generate_tldr()
        print(f"\n[Step 2] TL;DR Generated:")
        print(f"  - Key Deployments: {tldr['total_pls']} product lines")
        for deployment in tldr.get('key_deployments', [])[:3]:
            print(f"    • {deployment['pl']}: {deployment['summary'][:50]}...")

        # Generate plain text notes
        plain_text = formatter.get_plain_text_notes()

        print(f"\n[Step 2] Grouped into {len(grouped_data)} product lines:")
        for pl, epics in grouped_data.items():
            ticket_count = sum(len(items) for items in epics.values())
            print(f"  - {pl}: {len(epics)} epics, {ticket_count} tickets")

        print(f"\n[Step 2] COMPLETE: Release notes generated ({len(plain_text)} characters)")
        return formatter, plain_text

    except Exception as e:
        print(f"[Step 2] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None, ""


def step2_update_google_doc(formatter: ReleaseNotesFormatter) -> Tuple[bool, str]:
    """
    STEP 2 (continued): Update Google Doc with formatted release notes.

    Args:
        formatter: The ReleaseNotesFormatter with processed data

    Returns:
        Tuple of (success, doc_url)
    """
    print("\n[Step 2b] Updating Google Doc...")

    try:
        # Initialize Google Docs handler
        google_docs = GoogleDocsHandler()

        # Authenticate
        print("[Step 2b] Authenticating with Google...")
        if not google_docs.authenticate():
            print("[Step 2b] ERROR: Could not authenticate with Google")
            return False, ""

        # Test connection
        if not google_docs.test_connection():
            print("[Step 2b] ERROR: Could not connect to Google Doc")
            return False, ""

        # Generate formatted requests with LLM-consolidated body sections
        print("[Step 2b] Generating formatted content...")
        print("[Step 2b] Using LLM consolidation for polished prose...")

        # Generate LLM-consolidated body sections for each PL
        consolidated_bodies = formatter.generate_consolidated_body_sections(use_llm=True)

        formatted_requests = create_formatted_requests(
            release_date=formatter.release_date,
            grouped_data=formatter.grouped_data,
            tldr=formatter.generate_tldr(),
            extract_value_adds_func=formatter.extract_value_adds,
            consolidated_bodies=consolidated_bodies
        )

        # Insert release notes
        print(f"[Step 2b] Applying {len(formatted_requests)} formatting operations...")
        if not google_docs.insert_release_notes(formatted_requests):
            # Fallback to plain text if formatting fails
            print("[Step 2b] Formatted insert failed, trying plain text...")
            plain_text = formatter.get_plain_text_notes()
            if not google_docs.insert_plain_text(plain_text):
                print("[Step 2b] ERROR: Could not update Google Doc")
                return False, ""

        doc_url = google_docs.get_document_url()
        print(f"\n[Step 2b] COMPLETE: Google Doc updated successfully")
        print(f"[Step 2b] URL: {doc_url}")
        return True, doc_url

    except Exception as e:
        print(f"[Step 2b] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False, ""


def step3_send_slack_notification(release_date: str, doc_url: str,
                                  tldr_summary: str,
                                  pl_names: list = None) -> Tuple[bool, Optional[Dict]]:
    """
    STEP 3: Send Slack Notification to PMOs.

    Args:
        release_date: Release date string
        doc_url: Google Doc URL
        tldr_summary: TL;DR summary text
        pl_names: List of product line names for per-PL dropdowns

    Returns:
        Tuple of (success, message_info)
    """
    print_step(3, "SEND SLACK NOTIFICATION")

    try:
        # Initialize Slack handler
        slack = SlackHandler()

        # Test connection
        print("[Step 3] Testing Slack connection...")
        if not slack.test_connection():
            print("[Step 3] ERROR: Could not connect to Slack")
            return False, None

        # Send review notification
        print("[Step 3] Sending review notification...")
        result = slack.send_review_notification(
            release_date=release_date,
            doc_url=doc_url,
            tldr_summary=tldr_summary,
            pl_names=pl_names or []
        )

        if result:
            print(f"\n[Step 3] COMPLETE: Notification sent successfully")
            print(f"[Step 3] Message timestamp: {result.get('ts', 'N/A')}")
            if result.get('channel'):
                print(f"[Step 3] Channel: {result['channel']}")
            return True, result
        else:
            print("[Step 3] ERROR: Failed to send notification")
            return False, None

    except Exception as e:
        print(f"[Step 3] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def step4_track_approvals(release_id: str, slack: SlackHandler) -> Dict:
    """
    STEP 4: Track PMO Approvals.

    This is a placeholder for the approval tracking workflow.
    In production, this would be handled by a webhook server or polling.

    Args:
        release_id: Unique release identifier
        slack: Slack handler instance

    Returns:
        Approval status
    """
    print_step(4, "TRACK APPROVALS (PoC)")

    print("[Step 4] Approval tracking workflow:")
    print("  1. PMOs receive notification with approval buttons")
    print("  2. Each PMO clicks YES, NO, or RELEASE TOMORROW")
    print("  3. System tracks votes in real-time")
    print("  4. When all approvals received, proceed to Step 5")

    # For PoC, we'll simulate the approval tracking
    print("\n[Step 4] In a full implementation:")
    print("  - A webhook server would handle button clicks")
    print("  - Or a polling mechanism would check message reactions")
    print("  - Approvals would be stored in a database")

    # Check current approval status
    status = slack.get_approval_status(release_id)
    print(f"\n[Step 4] Current approval status: {status}")

    print("\n[Step 4] PoC NOTE: For demonstration, you can:")
    print("  - Use message reactions as approval indicators")
    print("  - Or manually trigger Step 5/6 with --skip-approval flag")

    return status


def step5_final_approval(release_date: str, doc_url: str, slack: SlackHandler) -> bool:
    """
    STEP 5: Final Approval Button.

    Sends the "Good to Release" notification when all approvals are complete.

    Args:
        release_date: Release date string
        doc_url: Google Doc URL
        slack: Slack handler instance

    Returns:
        True if notification sent successfully
    """
    print_step(5, "FINAL APPROVAL")

    print("[Step 5] All PMO approvals received!")
    print("[Step 5] Sending 'Good to Release' notification...")

    result = slack.send_good_to_release_notification(
        release_date=release_date,
        doc_url=doc_url
    )

    if result:
        print(f"\n[Step 5] COMPLETE: Final approval notification sent")
        return True
    else:
        print("[Step 5] ERROR: Failed to send final approval notification")
        return False


def step6_post_to_release_channel(release_date: str, release_notes: str,
                                   approver: str = None) -> bool:
    """
    STEP 6: Auto-Post to Slack Release Channel.

    Posts the final release notes to the official release channel.

    Args:
        release_date: Release date string
        release_notes: Formatted release notes
        approver: Name of the final approver

    Returns:
        True if posted successfully
    """
    print_step(6, "POST TO RELEASE CHANNEL")

    try:
        # Initialize Slack handler
        slack = SlackHandler()

        # Get release channel (for PoC, using default DM channel)
        release_channel = os.getenv('SLACK_RELEASE_CHANNEL', os.getenv('SLACK_DM_CHANNEL'))

        print(f"[Step 6] Posting to channel: {release_channel}")

        result = slack.send_final_release_notes(
            release_date=release_date,
            release_notes=release_notes,
            approver_name=approver or "System",
            channel=release_channel
        )

        if result:
            print(f"\n[Step 6] COMPLETE: Release notes posted successfully")
            print(f"[Step 6] Message timestamp: {result['ts']}")
            return True
        else:
            print("[Step 6] ERROR: Failed to post release notes")
            return False

    except Exception as e:
        print(f"[Step 6] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_release_automation(release_date: str = None, skip_approval: bool = False) -> Dict:
    """
    Run the complete release automation workflow.

    Args:
        release_date: Optional release date override
        skip_approval: If True, skip approval workflow (for testing)

    Returns:
        Dictionary with workflow results
    """
    print_banner()
    print(f"Starting release automation at {datetime.now()}")
    print(f"Release date: {release_date or 'auto-detect'}")
    print(f"Skip approval: {skip_approval}")

    results = {
        "started_at": datetime.now().isoformat(),
        "steps": {},
        "success": False
    }

    # Weekend guard
    if not is_weekday():
        day_name = datetime.now().strftime('%A')
        print(f"\n[WORKFLOW] Today is {day_name} — no releases on weekends. Exiting.")
        results["error"] = "Weekend — no releases"
        return results

    # STEP 1: Fetch Jira tickets
    release_ticket, linked_tickets = step1_fetch_jira_tickets()
    results["steps"]["1_jira"] = {
        "success": release_ticket is not None,
        "ticket_count": len(linked_tickets)
    }

    if not linked_tickets:
        print("\n[WORKFLOW] No release found for today. Skipping Slack notification.")
        results["success"] = True
        results["error"] = "No tickets found"
        return results

    # STEP 2: Create release notes
    formatter, plain_text = step2_create_release_notes(linked_tickets, release_date)
    results["steps"]["2_format"] = {
        "success": formatter is not None,
        "notes_length": len(plain_text)
    }

    if not formatter:
        print("\n[WORKFLOW] Cannot continue without release notes. Exiting.")
        results["error"] = "Failed to format release notes"
        return results

    # STEP 2b: Update Google Doc
    doc_success, doc_url = step2_update_google_doc(formatter)
    results["steps"]["2b_google_doc"] = {
        "success": doc_success,
        "url": doc_url
    }

    if not doc_success:
        print("\n[WORKFLOW] Warning: Google Doc update failed. Continuing with Slack...")
        doc_url = os.getenv('GOOGLE_DOC_ID', '')
        if doc_url:
            doc_url = f"https://docs.google.com/document/d/{doc_url}/edit"

    # STEP 3: Send Slack notification
    pl_names = list(formatter.grouped_data.keys()) if formatter.grouped_data else []
    slack_success, message_info = step3_send_slack_notification(
        release_date=formatter.release_date,
        doc_url=doc_url,
        tldr_summary="",
        pl_names=pl_names
    )
    results["steps"]["3_slack_notification"] = {
        "success": slack_success,
        "message_ts": message_info['ts'] if message_info else None
    }

    if not slack_success:
        print("\n[WORKFLOW] Warning: Slack notification failed.")

    # STEP 4-5: Approval workflow (PoC mode)
    if skip_approval:
        print("\n[WORKFLOW] Skipping approval workflow (--skip-approval flag)")
        results["steps"]["4_approval"] = {"skipped": True}
        results["steps"]["5_final_approval"] = {"skipped": True}
    else:
        slack = SlackHandler()
        release_id = f"release_{datetime.now().strftime('%Y%m%d')}"
        approval_status = step4_track_approvals(release_id, slack)
        results["steps"]["4_approval"] = approval_status

        # For PoC, check if we should send final approval
        if approval_status.get("all_approved"):
            step5_final_approval(formatter.release_date, doc_url, slack)
            results["steps"]["5_final_approval"] = {"success": True}

    # STEP 6: Post to release channel (if skip_approval or all approved)
    if skip_approval:
        post_success = step6_post_to_release_channel(
            release_date=formatter.release_date,
            release_notes=plain_text,
            approver="System (Auto)"
        )
        results["steps"]["6_post_release"] = {"success": post_success}

    # Summary
    results["completed_at"] = datetime.now().isoformat()
    results["success"] = all(
        step.get("success", True) for step in results["steps"].values()
        if not step.get("skipped")
    )

    print("\n" + "="*60)
    print("  WORKFLOW SUMMARY")
    print("="*60)
    for step_name, step_result in results["steps"].items():
        status = "SKIPPED" if step_result.get("skipped") else (
            "SUCCESS" if step_result.get("success", True) else "FAILED"
        )
        print(f"  {step_name}: {status}")
    print("="*60)
    print(f"  Overall: {'SUCCESS' if results['success'] else 'FAILED'}")
    print("="*60 + "\n")

    return results


def main():
    """Main entry point for the release automation."""
    parser = argparse.ArgumentParser(
        description='Release Automation PoC - DeepIntent',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Run full workflow
  python main.py --skip-approval          # Skip approval for testing
  python main.py --step 1                 # Run only Step 1 (Jira)
  python main.py --step 2                 # Run Steps 1-2 (Jira + Format)
  python main.py --test-connections       # Test all API connections
  python main.py --slack-only             # Send only the Slack approval message
        """
    )

    parser.add_argument('--release-date', type=str, help='Release date (e.g., "2nd February 2026")')
    parser.add_argument('--skip-approval', action='store_true', help='Skip approval workflow')
    parser.add_argument('--step', type=int, choices=[1, 2, 3, 4, 5, 6],
                       help='Run up to specific step only')
    parser.add_argument('--test-connections', action='store_true',
                       help='Test all API connections without running workflow')
    parser.add_argument('--refresh', action='store_true',
                       help='Refresh tickets from Jira (picks up newly added stories/bugs under existing fix versions)')
    parser.add_argument('--slack-only', action='store_true',
                       help='Send only the Slack approval message (uses existing tickets_export.json)')

    args = parser.parse_args()

    # Test connections mode
    if args.test_connections:
        print_banner()
        print("Testing API connections...\n")

        print("1. Testing Jira connection...")
        try:
            jira = JiraHandler()
            jira_ok = jira.test_connection()
            print(f"   Jira: {'OK' if jira_ok else 'FAILED'}\n")
        except Exception as e:
            print(f"   Jira: FAILED - {e}\n")

        print("2. Testing Google Docs connection...")
        try:
            google = GoogleDocsHandler()
            google_ok = google.authenticate() and google.test_connection()
            print(f"   Google Docs: {'OK' if google_ok else 'FAILED'}\n")
        except Exception as e:
            print(f"   Google Docs: FAILED - {e}\n")

        print("3. Testing Slack connection...")
        try:
            slack = SlackHandler()
            slack_ok = slack.test_connection()
            print(f"   Slack: {'OK' if slack_ok else 'FAILED'}\n")
        except Exception as e:
            print(f"   Slack: FAILED - {e}\n")

        return

    # Slack-only mode: send just the Slack approval message
    if args.slack_only:
        print_banner()
        print("Sending Slack approval message only...\n")

        import json

        # Load tickets from existing export
        try:
            with open("tickets_export.json", 'r') as f:
                export_data = json.load(f)
            tickets = export_data.get("tickets", [])
        except FileNotFoundError:
            print("[Slack-only] ERROR: tickets_export.json not found. Run the full workflow first.")
            sys.exit(1)

        if not tickets:
            print("[Slack-only] ERROR: No tickets in tickets_export.json")
            sys.exit(1)

        # Build formatter to get TL;DR
        release_summary = os.getenv('RELEASE_TICKET_SUMMARY', 'Release 2nd February 2026')
        release_date = args.release_date
        if not release_date:
            if 'Release ' in release_summary:
                release_date = release_summary.replace('Release ', '')
            else:
                release_date = datetime.now().strftime("%d %B %Y")

        formatter = ReleaseNotesFormatter(release_date)
        formatter.process_tickets(tickets)

        # Extract PL names from grouped data
        pl_names = list(formatter.grouped_data.keys()) if formatter.grouped_data else []

        doc_id = os.getenv('GOOGLE_DOC_ID', '')
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else ""

        # Send the Slack notification with per-PL dropdowns
        success, result = step3_send_slack_notification(
            release_date, doc_url, "", pl_names=pl_names
        )
        sys.exit(0 if success else 1)

    # Refresh mode: re-fetch tickets from Jira under existing fix versions
    if args.refresh:
        print_banner()
        print("Refreshing tickets from Jira...\n")
        from hybrid_step1_export_jira import refresh_tickets
        result = refresh_tickets()
        if result:
            print("\n[Refresh] Tickets refreshed. Re-running release notes generation...")
            # Re-read refreshed tickets
            import json
            with open("tickets_export.json", 'r') as f:
                export_data = json.load(f)
            refreshed_tickets = export_data.get("tickets", [])
            new_tickets = export_data.get("new_tickets", [])
            if not new_tickets:
                print("[Refresh] No new tickets found. Release notes are up to date.")
                return
            print(f"[Refresh] {len(new_tickets)} new ticket(s) found: {new_tickets}")
            print("[Refresh] Re-generating release notes with updated tickets...")

            # Re-run steps 2 and 2b with refreshed tickets
            formatter, plain_text = step2_create_release_notes(refreshed_tickets, args.release_date)
            if formatter:
                step2_update_google_doc(formatter)
                print("\n[Refresh] Release notes and Google Doc updated with new tickets.")
            else:
                print("[Refresh] ERROR: Failed to regenerate release notes.")
        else:
            print("[Refresh] No changes or refresh failed.")
        return

    # Step-limited mode
    if args.step:
        print_banner()
        print(f"Running up to Step {args.step}...\n")

        if args.step >= 1:
            release_ticket, linked_tickets = step1_fetch_jira_tickets()
            if args.step == 1 or not linked_tickets:
                return

        if args.step >= 2:
            formatter, plain_text = step2_create_release_notes(linked_tickets, args.release_date)
            if formatter:
                step2_update_google_doc(formatter)
            if args.step == 2:
                return

        if args.step >= 3 and formatter:
            pl_names = list(formatter.grouped_data.keys()) if formatter.grouped_data else []
            doc_id = os.getenv('GOOGLE_DOC_ID', '')
            doc_url = f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else ""
            step3_send_slack_notification(formatter.release_date, doc_url, "", pl_names=pl_names)
            if args.step == 3:
                return

        # Steps 4-6 require full workflow
        if args.step >= 4:
            print("\nSteps 4-6 require full workflow. Use --skip-approval for testing.")

        return

    # Full workflow
    results = run_release_automation(
        release_date=args.release_date,
        skip_approval=args.skip_approval
    )

    # Exit with appropriate code
    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()
