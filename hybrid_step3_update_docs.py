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
from dotenv import load_dotenv

load_dotenv()

from google_docs_handler import GoogleDocsHandler
from slack_handler import SlackHandler


def _extract_release_number(fix_version: str) -> str:
    """Extract the release number from a fix version string.

    Examples:
        "DSP Core PL3 2025: Release 23.0" -> "Release 23.0"
        "Developer Experience 2026 : Release 6.0" -> "Release 6.0"
    """
    match = re.search(r'(Release\s+[\d.]+)', fix_version)
    return match.group(1) if match else fix_version


def update_google_docs(processed_data: dict) -> bool:
    """Update Google Docs with processed notes.

    Document format matches the manual Claude AI prompt output:
    - Title: Daily Deployment Summary: [Date]
    - TL;DR section with Key Deployments, Major Feature, Key Enhancement
    - Team sections with ------------------DSP------------------ separators
    - PL headers: DSP PL3: Release 23.0 (with hyperlink to fix version)
    - Body with #### [Epic Name](url), **Value Add**:, bullets, GA/FF tags
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

        # Build the document content
        release_date = processed_data.get("release_summary", "").replace("Release ", "")
        if not release_date:
            release_date = datetime.now().strftime("%d %B %Y")

        tldr_by_pl = processed_data.get("tldr_by_pl", {})
        body_by_pl = processed_data.get("body_by_pl", {})
        product_lines = processed_data.get("product_lines", [])
        fix_versions = processed_data.get("fix_versions", {})
        fix_version_urls = processed_data.get("fix_version_urls", {})

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

        # TL;DR Section Header
        tldr_header = "------------------TL;DR:------------------\n\n"
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": tldr_header
            }
        })
        current_index += len(tldr_header)

        # Key Deployments header with PL list
        deploying_pls = [pl for pl in product_lines if pl in tldr_by_pl]
        if len(deploying_pls) == 1:
            pl_list_str = deploying_pls[0]
        elif len(deploying_pls) == 2:
            pl_list_str = f"{deploying_pls[0]} and {deploying_pls[1]}"
        else:
            pl_list_str = ", ".join(deploying_pls[:-1]) + f", and {deploying_pls[-1]}"

        key_deploy_header = f"* Key Deployments: {pl_list_str}\n"
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": key_deploy_header
            }
        })
        current_index += len(key_deploy_header)

        # Key Deployments sub-bullets per PL
        for pl in product_lines:
            if pl in tldr_by_pl:
                summary = tldr_by_pl[pl]
                deploy_line = f"   * {pl} - {summary}\n"
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

        # Determine team groupings from PLs (e.g., "DSP" from "DSP Core PL3")
        team_pls = {}
        for pl in product_lines:
            # Extract team prefix (e.g., "DSP" from "DSP Core PL3", "Audiences" from "Audiences PL1")
            team = pl.split()[0] if pl.split() else pl
            if team not in team_pls:
                team_pls[team] = []
            team_pls[team].append(pl)

        # Body sections grouped by team
        for team, pls in team_pls.items():
            # Team separator
            team_header = f"------------------{team}------------------\n\n"
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": team_header
                }
            })
            current_index += len(team_header)

            for pl in pls:
                # PL Header with release version (e.g., "DSP PL3: Release 23.0")
                fv = fix_versions.get(pl, "")
                release_num = _extract_release_number(fv)
                fv_url = fix_version_urls.get(pl, "")

                if fv_url:
                    pl_title = f"{pl}: {release_num}\n"
                else:
                    pl_title = f"{pl}: {release_num}\n"

                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": pl_title
                    }
                })
                # Store position for hyperlink on the release number
                release_num_start = current_index + len(f"{pl}: ")
                release_num_end = release_num_start + len(release_num)
                current_index += len(pl_title)

                # Add hyperlink to the release number if URL available
                if fv_url:
                    requests.append({
                        "updateTextStyle": {
                            "range": {
                                "startIndex": release_num_start,
                                "endIndex": release_num_end
                            },
                            "textStyle": {
                                "link": {"url": fv_url},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"blue": 0.8, "red": 0.06, "green": 0.36}}
                                }
                            },
                            "fields": "link,foregroundColor"
                        }
                    })

                # Blank line after PL header
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": "\n"
                    }
                })
                current_index += 1

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

        # Build TL;DR summary — Version 1 format: Key Deployments with PL sub-bullets
        deploying_pls = [pl for pl in product_lines if pl in tldr_by_pl]
        if len(deploying_pls) == 1:
            pl_list_str = deploying_pls[0]
        elif len(deploying_pls) == 2:
            pl_list_str = f"{deploying_pls[0]} and {deploying_pls[1]}"
        else:
            pl_list_str = ", ".join(deploying_pls[:-1]) + f", and {deploying_pls[-1]}"

        tldr_lines = [f"*Key Deployments:* {pl_list_str}"]
        for pl in product_lines:
            if pl in tldr_by_pl:
                tldr_lines.append(f"   • {pl} - {tldr_by_pl[pl]}")

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

    # Only send Slack notification if Google Docs was actually updated.
    # If docs were skipped (e.g., duplicate detection), skip Slack too
    # to avoid re-sending approval requests for an old release.
    slack_success = False
    if docs_success:
        slack_success = send_slack_notification(processed_data)
    else:
        print("\n[Step 3b] Skipping Slack notification — Google Docs was not updated")

    # Summary
    print("\n" + "=" * 60)
    print("  HYBRID WORKFLOW COMPLETE")
    print("=" * 60)
    print(f"  Google Docs: {'✅ SUCCESS' if docs_success else '❌ FAILED / SKIPPED'}")
    print(f"  Slack:       {'✅ SUCCESS' if slack_success else '❌ SKIPPED (no doc update)'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
