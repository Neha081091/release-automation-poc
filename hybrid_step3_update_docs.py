#!/usr/bin/env python3
"""
HYBRID STEP 3: Update Google Docs & Slack (Run on Mac)

This script reads the processed notes from Claude API and updates
Google Docs and Slack with the polished release notes.

Usage:
    python hybrid_step3_update_docs.py              # Full update with approval workflow
    python hybrid_step3_update_docs.py --no-slack   # Update docs only, no Slack
    python hybrid_step3_update_docs.py --approval   # Send only approval message

Input:
    processed_notes.json

Output:
    - Google Docs updated
    - Slack approval message sent (with interactive buttons)
"""

import json
import os
import re
import argparse
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

from google_docs_handler import GoogleDocsHandler
from google_docs_formatter import format_for_google_docs, GoogleDocsFormatter
from slack_handler import SlackHandler
from slack_socket_mode import post_approval_message as socket_post_approval

# Color definitions (RGB values 0-1)
BLUE_COLOR = {"red": 0.06, "green": 0.36, "blue": 0.7}  # Link blue
GREEN_COLOR = {"red": 0.13, "green": 0.55, "blue": 0.13}  # Dark green


def get_pl_category(pl_name: str) -> str:
    """Determine the category header for a product line."""
    pl_lower = pl_name.lower()
    if 'audience' in pl_lower:
        return "Audiences"
    elif 'data governance' in pl_lower:
        return "Data Governance"
    elif 'data ingress' in pl_lower:
        return "Data Ingress"
    elif 'developer' in pl_lower:
        return "Developer Experience"
    elif 'dsp' in pl_lower:
        return "DSP"
    elif 'helix' in pl_lower:
        return "Helix"
    elif 'media' in pl_lower:
        return "Media"
    else:
        return pl_name


def clean_pl_name(pl_name: str) -> str:
    """
    Clean PL name by removing year suffixes.

    Examples:
        - "Developer Experience 2026" -> "Developer Experience"
        - "DSP Core PL1" -> "DSP Core PL1" (unchanged)
    """
    import re
    # Remove trailing year (4 digits at the end)
    cleaned = re.sub(r'\s+20\d{2}$', '', pl_name)
    return cleaned


def find_epic_url(line_text: str, epic_urls: dict) -> str:
    """Find matching epic URL using flexible matching."""
    line_lower = line_text.lower().strip()

    # Direct match first
    if line_text in epic_urls:
        return epic_urls[line_text]

    # Case-insensitive match
    for epic_name, url in epic_urls.items():
        if epic_name.lower() == line_lower:
            return url

    # Partial match - check if line contains epic name or vice versa
    for epic_name, url in epic_urls.items():
        epic_lower = epic_name.lower()
        # Check if most of the words match
        line_words = set(line_lower.split())
        epic_words = set(epic_lower.split())
        common_words = line_words & epic_words

        # If 70% of words match, consider it a match
        if len(epic_words) > 0:
            match_ratio = len(common_words) / len(epic_words)
            if match_ratio >= 0.7:
                return url

    return ""


def update_google_docs(processed_data: dict, force_update: bool = False) -> bool:
    """
    Update Google Docs with processed notes using the dedicated formatter.

    The formatter acts as an interpreter layer between Claude-generated content
    and Google Docs API, ensuring proper formatting is applied.
    """
    print("\n[Step 3a] Updating Google Docs...")

    try:
        google_docs = GoogleDocsHandler()

        if not google_docs.authenticate():
            print("[Step 3a] ERROR: Could not authenticate with Google")
            return False

        if not google_docs.test_connection():
            print("[Step 3a] ERROR: Could not connect to Google Doc")
            return False

        # Extract release date
        release_date = processed_data.get("release_summary", "").replace("Release ", "")
        if not release_date:
            release_date = datetime.now().strftime("%d %B %Y")

        # Check if this release already exists in the document
        existing_content = google_docs.get_document_content()
        release_header = f"Daily Deployment Summary: {release_date}"

        if existing_content and release_header in existing_content:
            if not force_update:
                print(f"[Step 3a] WARNING: Release '{release_date}' already exists in document")
                print("[Step 3a] Skipping to avoid duplicates. Use --force to override.")
                return True  # Return True since it's not an error
            else:
                print(f"[Step 3a] Force update: Adding '{release_date}' even though it exists")

        # Use the dedicated formatter to generate Google Docs API requests
        # This formatter acts as an interpreter between Claude output and Google Docs
        print("[Step 3a] Formatting content using Google Docs Formatter...")
        insert_requests, format_requests = format_for_google_docs(processed_data, release_date)

        print(f"[Step 3a] Generated {len(insert_requests)} insert requests")
        print(f"[Step 3a] Generated {len(format_requests)} format requests")

        # Insert all text at the beginning (index 1)
        # DO NOT clear document - prepend new content at top
        # This keeps older releases below the new one
        google_docs.update_document(insert_requests)

        # Now apply formatting
        print("[Step 3a] Applying formatting (links, colors, bold)...")

        # Apply formatting if there are any requests
        if format_requests:
            google_docs.update_document(format_requests)
            print(f"[Step 3a] Applied {len(format_requests)} formatting rules")

        print(f"[Step 3a] ✅ Google Docs updated: {google_docs.get_document_url()}")
        return True

    except Exception as e:
        print(f"[Step 3a] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def send_slack_notification(processed_data: dict) -> bool:
    """Send Slack notification with processed notes (legacy simple notification)."""
    print("\n[Step 3b] Sending Slack notification...")

    try:
        slack = SlackHandler()

        if not slack.test_connection():
            print("[Step 3b] ERROR: Could not connect to Slack")
            return False

        release_date = processed_data.get("release_summary", "").replace("Release ", "")
        tldr_by_pl = processed_data.get("tldr_by_pl", {})
        product_lines = processed_data.get("product_lines", [])

        # Build TL;DR summary (prose format, no bullets)
        tldr_lines = ["*Key Deployments:*"]
        for pl in product_lines:
            if pl in tldr_by_pl and pl.lower() != "other":
                # Clean PL name (remove year suffix)
                pl_clean = clean_pl_name(pl)
                tldr_lines.append(f"{pl_clean} - {tldr_by_pl[pl]}")

        tldr_summary = "\n".join(tldr_lines)

        # Get Google Doc URL
        doc_id = os.getenv('GOOGLE_DOC_ID', '')
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else ""

        # Send notification
        result = slack.send_review_notification(
            release_date=release_date,
            doc_url=doc_url,
            tldr_summary=tldr_summary
        )

        if result:
            print("[Step 3b] ✅ Slack notification sent")
            return True
        else:
            print("[Step 3b] ❌ Slack notification failed")
            return False

    except Exception as e:
        print(f"[Step 3b] ERROR: {e}")
        return False


def send_slack_approval_message(processed_data: dict) -> bool:
    """
    Send Slack approval message with interactive buttons per PL.

    This creates a message with Approve/Reject/Tomorrow buttons for each
    product line, allowing PMOs to review and approve releases interactively.

    Args:
        processed_data: Processed release notes dictionary

    Returns:
        True if message was sent successfully
    """
    print("\n[Step 3b] Sending Slack approval message with interactive buttons...")

    try:
        # Check if bot token is available (required for interactive messages)
        bot_token = os.getenv('SLACK_BOT_TOKEN')
        if not bot_token:
            print("[Step 3b] WARNING: SLACK_BOT_TOKEN not set, falling back to simple notification")
            return send_slack_notification(processed_data)

        # Get PLs and metadata from processed data
        pls = processed_data.get('product_lines', [])
        doc_id = os.getenv('GOOGLE_DOC_ID')
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else None
        release_date = processed_data.get('release_summary', '').replace('Release ', '')
        notes_by_pl = processed_data.get('body_by_pl', {})

        # Use socket mode post_approval_message
        result = socket_post_approval(
            pls=pls,
            doc_url=doc_url,
            release_date=release_date,
            notes_by_pl=notes_by_pl
        )

        if result:
            print(f"[Step 3b] ✅ Approval message sent (ts: {result})")
            print("[Step 3b] Interactive buttons are now active - waiting for PMO review")
            print("[Step 3b] Make sure slack_socket_mode.py is running to handle button clicks!")
            return True
        else:
            print("[Step 3b] ❌ Failed to send approval message")
            return False

    except Exception as e:
        print(f"[Step 3b] ERROR: {e}")
        import traceback
        traceback.print_exc()
        # Fall back to simple notification
        print("[Step 3b] Falling back to simple notification...")
        return send_slack_notification(processed_data)


def load_deferred_pls_for_today() -> list:
    """
    Load deferred PLs from yesterday that should be included in today's release.

    Returns:
        List of deferred PL data dictionaries
    """
    today = datetime.now().strftime('%Y-%m-%d')
    deferred_file = 'deferred_pls.json'

    try:
        with open(deferred_file, 'r') as f:
            deferred_data = json.load(f)

        if today in deferred_data:
            pls = deferred_data[today]
            print(f"[Step 3] Found {len(pls)} deferred PL(s) from yesterday")
            return pls
        return []

    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[Step 3] Error loading deferred PLs: {e}")
        return []


def merge_deferred_pls(processed_data: dict, deferred_pls: list) -> dict:
    """
    Merge deferred PLs into processed data.

    Args:
        processed_data: Current processed release notes
        deferred_pls: List of deferred PL data from yesterday

    Returns:
        Updated processed_data with deferred PLs included
    """
    if not deferred_pls:
        return processed_data

    for deferred in deferred_pls:
        pl_name = deferred.get('pl')
        if not pl_name:
            continue

        # Add to product_lines if not already present
        if pl_name not in processed_data.get('product_lines', []):
            processed_data.setdefault('product_lines', []).append(pl_name)
            print(f"[Step 3] Added deferred PL: {pl_name}")

        # Add TL;DR
        if deferred.get('tldr'):
            processed_data.setdefault('tldr_by_pl', {})[pl_name] = deferred['tldr']

        # Add body
        if deferred.get('body'):
            processed_data.setdefault('body_by_pl', {})[pl_name] = deferred['body']

        # Add release version
        if deferred.get('release_version'):
            processed_data.setdefault('release_versions', {})[pl_name] = deferred['release_version']

        # Add fix version URL
        if deferred.get('fix_version_url'):
            processed_data.setdefault('fix_version_urls', {})[pl_name] = deferred['fix_version_url']

        # Add epic URLs
        if deferred.get('epic_urls'):
            processed_data.setdefault('epic_urls_by_pl', {})[pl_name] = deferred['epic_urls']

    return processed_data


def main():
    """Update Google Docs and Slack with processed notes."""
    parser = argparse.ArgumentParser(description='Update Google Docs & Slack with release notes')
    parser.add_argument('--no-slack', action='store_true', help='Skip Slack notification')
    parser.add_argument('--approval', action='store_true', help='Send only Slack approval message')
    parser.add_argument('--simple-slack', action='store_true', help='Use simple Slack notification (no buttons)')
    parser.add_argument('--force', action='store_true', help='Force update even if release already exists')
    args = parser.parse_args()

    print("=" * 60)
    print("  HYBRID STEP 3: Update Google Docs & Slack")
    print("=" * 60)

    # Load processed notes
    input_file = "processed_notes.json"
    if not os.path.exists(input_file):
        print(f"[Step 3] ERROR: {input_file} not found")
        print("[Step 3] Run hybrid_step2_process_claude.py first on server")
        return

    with open(input_file, 'r') as f:
        processed_data = json.load(f)

    print(f"[Step 3] Loaded processed notes from {input_file}")
    print(f"[Step 3] Product lines: {len(processed_data.get('product_lines', []))}")

    # Load and merge deferred PLs from yesterday
    deferred_pls = load_deferred_pls_for_today()
    if deferred_pls:
        processed_data = merge_deferred_pls(processed_data, deferred_pls)
        print(f"[Step 3] After merge: {len(processed_data.get('product_lines', []))} PLs")

    docs_success = True
    slack_success = True

    # If only sending approval message
    if args.approval:
        slack_success = send_slack_approval_message(processed_data)
        print("\n" + "=" * 60)
        print("  APPROVAL MESSAGE SENT")
        print("=" * 60)
        print(f"  Slack Approval: {'✅ SUCCESS' if slack_success else '❌ FAILED'}")
        print("=" * 60)
        return

    # Update Google Docs
    docs_success = update_google_docs(processed_data, force_update=args.force)

    # Send Slack notification (unless skipped)
    if not args.no_slack:
        if args.simple_slack:
            # Use legacy simple notification
            slack_success = send_slack_notification(processed_data)
        else:
            # Use new approval workflow with interactive buttons
            slack_success = send_slack_approval_message(processed_data)
    else:
        print("\n[Step 3b] Skipping Slack notification (--no-slack)")

    # Summary
    print("\n" + "=" * 60)
    print("  HYBRID WORKFLOW COMPLETE")
    print("=" * 60)
    print(f"  Google Docs: {'✅ SUCCESS' if docs_success else '❌ FAILED'}")
    if not args.no_slack:
        print(f"  Slack:       {'✅ SUCCESS' if slack_success else '❌ FAILED'}")
        if slack_success and not args.simple_slack:
            print("\n  Approval workflow active!")
            print("  PMOs can now approve/reject/defer each PL via Slack buttons.")
            print("  Once all PLs reviewed, 'Good to Announce' will post to release channel.")
    print("=" * 60)


if __name__ == "__main__":
    main()
