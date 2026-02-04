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
from slack_handler import SlackHandler
from slack_approval_handler import SlackApprovalHandler

# Color definitions (RGB values 0-1)
BLUE_COLOR = {"red": 0.06, "green": 0.36, "blue": 0.7}  # Link blue
GREEN_COLOR = {"red": 0.13, "green": 0.55, "blue": 0.13}  # Dark green


def get_pl_category(pl_name: str) -> str:
    """Determine the category header for a product line."""
    pl_lower = pl_name.lower()
    if 'dsp' in pl_lower:
        return "DSP"
    elif 'audience' in pl_lower:
        return "Audiences"
    elif 'developer' in pl_lower:
        return "Developer Experience"
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
    """Update Google Docs with processed notes in executive style with formatting."""
    print("\n[Step 3a] Updating Google Docs...")

    try:
        google_docs = GoogleDocsHandler()

        if not google_docs.authenticate():
            print("[Step 3a] ERROR: Could not authenticate with Google")
            return False

        if not google_docs.test_connection():
            print("[Step 3a] ERROR: Could not connect to Google Doc")
            return False

        # Extract data
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

        tldr_by_pl = processed_data.get("tldr_by_pl", {})
        body_by_pl = processed_data.get("body_by_pl", {})
        product_lines = processed_data.get("product_lines", [])
        release_versions = processed_data.get("release_versions", {})
        fix_version_urls = processed_data.get("fix_version_urls", {})
        epic_urls_by_pl = processed_data.get("epic_urls_by_pl", {})

        # Build requests for Google Docs API
        insert_requests = []
        format_requests = []  # Will be added after text insertion
        current_index = 1

        # Track formatting positions
        formatting_positions = {
            "bold": [],      # [(start, end), ...]
            "links": [],     # [(start, end, url), ...]
            "green": [],     # [(start, end), ...]
        }

        # ========================================
        # RELEASE NOTES (No approval section - handled in Slack)
        # ========================================

        # Title
        title = f"Daily Deployment Summary: {release_date}\n\n"
        insert_requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": title
            }
        })
        current_index += len(title)

        # TL;DR Section
        tldr_header = "------------------TL;DR:------------------\n\n"
        insert_requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": tldr_header
            }
        })
        current_index += len(tldr_header)

        # Key Deployments (bold)
        key_deploy_header = "Key Deployments:\n"
        key_deploy_start = current_index
        insert_requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": key_deploy_header
            }
        })
        formatting_positions["bold"].append((key_deploy_start, current_index + len("Key Deployments:")))
        current_index += len(key_deploy_header)

        # TL;DR per PL (with bold PL names)
        for pl in product_lines:
            if pl in tldr_by_pl:
                summary = tldr_by_pl[pl]
                # Clean PL name (remove year suffix)
                pl_clean = clean_pl_name(pl)

                # Build the line: "• {PL name} - {summary}\n"
                bullet = "• "
                separator = " - "

                insert_requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": bullet
                    }
                })
                current_index += len(bullet)

                # PL name (bold)
                pl_start = current_index
                insert_requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": pl_clean
                    }
                })
                formatting_positions["bold"].append((pl_start, current_index + len(pl_clean)))
                current_index += len(pl_clean)

                # Separator and summary
                rest_of_line = f"{separator}{summary}\n"
                insert_requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": rest_of_line
                    }
                })
                current_index += len(rest_of_line)

        # Blank line
        insert_requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": "\n"
            }
        })
        current_index += 1

        # Group PLs by category
        pl_by_category = defaultdict(list)
        for pl in product_lines:
            category = get_pl_category(pl)
            pl_by_category[category].append(pl)

        # Body sections per category
        for category in pl_by_category:
            # Category Header
            category_header = f"------------------{category}------------------\n\n"
            insert_requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": category_header
                }
            })
            current_index += len(category_header)

            # Each PL in this category
            for pl in pl_by_category[category]:
                # PL name - clean name without year (NOT bold per user request)
                pl_clean = clean_pl_name(pl)
                pl_name_text = f"{pl_clean}: "
                insert_requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": pl_name_text
                    }
                })
                current_index += len(pl_name_text)

                # Release version (blue link)
                release_ver = release_versions.get(pl, "Release 1.0")
                release_url = fix_version_urls.get(pl, "")
                ver_start = current_index
                ver_text = f"{release_ver}\n"
                insert_requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": ver_text
                    }
                })
                ver_end = current_index + len(release_ver)
                if release_url:
                    formatting_positions["links"].append((ver_start, ver_end, release_url))
                current_index += len(ver_text)

                # Body content with formatting tracking
                if pl in body_by_pl:
                    body_text = body_by_pl[pl]
                    body_start = current_index

                    # Parse body to find epic names, Value Add, and status tags
                    lines = body_text.split('\n')

                    # Filter out duplicate PL header lines and leading empty lines
                    pl_clean_lower = pl_clean.lower()
                    release_ver_num = release_ver.replace("Release ", "").strip()
                    filtered_lines = []
                    found_content = False
                    for line in lines:
                        line_lower = line.strip().lower()
                        # Skip lines that look like "PL Name Release X.X" (duplicate header)
                        if pl_clean_lower in line_lower and 'release' in line_lower and release_ver_num in line:
                            continue
                        # Skip leading empty lines
                        if not found_content and not line.strip():
                            continue
                        found_content = True
                        filtered_lines.append(line)
                    lines = filtered_lines
                    full_body = ""

                    epic_urls = epic_urls_by_pl.get(pl, {})

                    for line in lines:
                        line_start = current_index + len(full_body)

                        # Check for epic names (lines that could be epic headers)
                        stripped = line.strip()

                        # Epic name detection: a line that's not "Value Add:", not a bullet, not status
                        if stripped and not stripped.startswith('•') and not stripped.startswith('Value Add') and stripped not in ['General Availability', 'Feature Flag'] and not stripped.startswith('-'):
                            # Check if this epic name has a URL (flexible matching)
                            epic_url = find_epic_url(stripped, epic_urls)
                            if epic_url:
                                formatting_positions["links"].append((line_start, line_start + len(stripped), epic_url))
                                # Also make epic names bold
                                formatting_positions["bold"].append((line_start, line_start + len(stripped)))

                        # Value Add: should be bold
                        if stripped.startswith('Value Add'):
                            formatting_positions["bold"].append((line_start, line_start + len("Value Add:")))

                        # General Availability - green
                        if stripped == 'General Availability':
                            formatting_positions["green"].append((line_start, line_start + len(stripped)))

                        # Feature Flag - green
                        if stripped == 'Feature Flag':
                            formatting_positions["green"].append((line_start, line_start + len(stripped)))

                        full_body += line + "\n"

                    insert_requests.append({
                        "insertText": {
                            "location": {"index": current_index},
                            "text": full_body + "\n"
                        }
                    })
                    current_index += len(full_body) + 1

        # Add separator line between this release and older releases
        separator = "\n" + "═" * 60 + "\n\n"
        insert_requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": separator
            }
        })
        current_index += len(separator)

        # DO NOT clear document - prepend new content at top
        # This keeps older releases below the new one

        # Insert all text at the beginning (index 1)
        google_docs.update_document(insert_requests)

        # Now apply formatting
        print("[Step 3a] Applying formatting (links, colors, bold)...")

        # Build formatting requests (must be applied in reverse order of index)
        for start, end in formatting_positions["bold"]:
            format_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"bold": True},
                    "fields": "bold"
                }
            })

        for start, end, url in formatting_positions["links"]:
            format_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {
                        "link": {"url": url},
                        "foregroundColor": {"color": {"rgbColor": BLUE_COLOR}},
                        "underline": False
                    },
                    "fields": "link,foregroundColor,underline"
                }
            })

        for start, end in formatting_positions["green"]:
            format_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {
                        "foregroundColor": {"color": {"rgbColor": GREEN_COLOR}}
                    },
                    "fields": "foregroundColor"
                }
            })

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

        # Build TL;DR summary
        tldr_lines = ["*Key Deployments:*"]
        for pl in product_lines:
            if pl in tldr_by_pl:
                tldr_lines.append(f"   • {pl}: {tldr_by_pl[pl]}")

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

        approval_handler = SlackApprovalHandler()

        # Post the approval message
        result = approval_handler.post_approval_message(processed_data)

        if result:
            print(f"[Step 3b] ✅ Approval message sent (ts: {result.get('ts')})")
            print(f"[Step 3b] Channel: {result.get('channel')}")
            print("[Step 3b] Interactive buttons are now active - waiting for PMO review")
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
