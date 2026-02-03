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
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from google_docs_handler import GoogleDocsHandler
from slack_handler import SlackHandler


def update_google_docs(processed_data: dict) -> bool:
    """Update Google Docs with processed notes."""
    print("\n[Step 3a] Updating Google Docs...")

    try:
        google_docs = GoogleDocsHandler()

        if not google_docs.authenticate():
            print("[Step 3a] ERROR: Could not authenticate with Google")
            return False

        if not google_docs.test_connection():
            print("[Step 3a] ERROR: Could not connect to Google Doc")
            return False

        # Build the document content
        release_date = processed_data.get("release_summary", "").replace("Release ", "")
        if not release_date:
            release_date = datetime.now().strftime("%d %B %Y")

        tldr_by_pl = processed_data.get("tldr_by_pl", {})
        body_by_pl = processed_data.get("body_by_pl", {})
        product_lines = processed_data.get("product_lines", [])

        # Build requests for Google Docs API
        requests = []
        current_index = 1

        # Title
        title = f"Daily Deployment Summary: {release_date}\n\n"
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": title
            }
        })
        current_index += len(title)

        # TL;DR Section
        tldr_header = "------------------TL;DR:------------------\n\n"
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": tldr_header
            }
        })
        current_index += len(tldr_header)

        # Key Deployments
        key_deploy_header = "Key Deployments:\n"
        requests.append({
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
                deploy_line = f"   • {pl} - {summary}\n"
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": deploy_line
                    }
                })
                current_index += len(deploy_line)

        # Blank line
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": "\n"
            }
        })
        current_index += 1

        # Body sections per PL
        for pl in product_lines:
            # PL Header
            pl_header = f"------------------{pl}------------------\n\n"
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": pl_header
                }
            })
            current_index += len(pl_header)

            # Approval checkboxes
            approval_text = "☐ Yes   ☐ No   ☐ Release Tomorrow\n\n"
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": approval_text
                }
            })
            current_index += len(approval_text)

            # Body content (polished from Claude)
            if pl in body_by_pl:
                body_text = body_by_pl[pl] + "\n\n"
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": body_text
                    }
                })
                current_index += len(body_text)

        # Clear and update document
        google_docs.clear_document()
        google_docs.update_document(requests)

        print(f"[Step 3a] ✅ Google Docs updated: {google_docs.get_document_url()}")
        return True

    except Exception as e:
        print(f"[Step 3a] ERROR: {e}")
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
