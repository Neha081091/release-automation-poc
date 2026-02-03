#!/usr/bin/env python3
"""
HYBRID STEP 3: Update Google Docs & Slack (Run on Mac)

This script reads the processed notes from Claude API and updates
Google Docs and Slack with the polished release notes.

Usage:
    python hybrid_step3_update_docs.py

Input:
    processed_notes.json

Output:
    - Google Docs updated
    - Slack notification sent
"""

import json
import os
import re
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

from google_docs_handler import GoogleDocsHandler
from slack_handler import SlackHandler

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


def update_google_docs(processed_data: dict) -> bool:
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

        # Key Deployments
        key_deploy_header = "Key Deployments:\n"
        insert_requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": key_deploy_header
            }
        })
        current_index += len(key_deploy_header)

        # TL;DR per PL
        for pl in product_lines:
            if pl in tldr_by_pl:
                summary = tldr_by_pl[pl]
                deploy_line = f"• {pl} - {summary}\n"
                insert_requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": deploy_line
                    }
                })
                current_index += len(deploy_line)

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
                # PL name (bold)
                pl_name_text = f"{pl}: "
                pl_start = current_index
                insert_requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": pl_name_text
                    }
                })
                formatting_positions["bold"].append((pl_start, current_index + len(pl_name_text)))
                current_index += len(pl_name_text)

                # Release version (blue link)
                release_ver = release_versions.get(pl, "Release 1.0")
                release_url = fix_version_urls.get(pl, "")
                ver_start = current_index
                ver_text = f"{release_ver}\n\n"
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

        # Clear document first
        google_docs.clear_document()

        # Insert all text
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
                        "underline": True
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
    """Send Slack notification with processed notes."""
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


def main():
    """Update Google Docs and Slack with processed notes."""
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

    # Update Google Docs
    docs_success = update_google_docs(processed_data)

    # Send Slack notification
    slack_success = send_slack_notification(processed_data)

    # Summary
    print("\n" + "=" * 60)
    print("  HYBRID WORKFLOW COMPLETE")
    print("=" * 60)
    print(f"  Google Docs: {'✅ SUCCESS' if docs_success else '❌ FAILED'}")
    print(f"  Slack:       {'✅ SUCCESS' if slack_success else '❌ FAILED'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
