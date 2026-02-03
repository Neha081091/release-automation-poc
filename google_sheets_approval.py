"""
Google Sheets Approval System - Python Integration

This module populates a Google Sheet with release notes for approval workflow.
Works with the Google Apps Script (google_sheets_approval.js) for interactive buttons.

Usage:
    python google_sheets_approval.py --populate   # Add release notes to sheet
    python google_sheets_approval.py --status     # Check approval status
    python google_sheets_approval.py --announce   # Post approved notes to Slack

Requirements:
    - Google Sheet with Apps Script installed
    - GOOGLE_SHEET_ID in .env
    - Google service account credentials
"""

import os
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

# Google API imports
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Slack integration
import requests

# File paths
PROCESSED_NOTES_FILE = "processed_notes.json"
APPROVAL_STATUS_FILE = "approval_status.json"

# Column indices (0-based for API, matches Apps Script)
COL_PL_NAME = 0
COL_VERSION = 1
COL_TLDR = 2
COL_STATUS = 3
COL_VOTED_BY = 4
COL_VOTED_AT = 5
COL_APPROVE = 6
COL_REJECT = 7
COL_TOMORROW = 8


class GoogleSheetsApproval:
    """Handler for Google Sheets approval workflow."""

    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(self):
        """Initialize Google Sheets client."""
        self.sheet_id = os.getenv('GOOGLE_SHEET_ID')
        self.credentials_file = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'service_account.json')
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')

        if not self.sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is required in .env")

        self.service = self._authenticate()
        print(f"[Sheets] Initialized for sheet: {self.sheet_id}")

    def _authenticate(self):
        """Authenticate with Google Sheets API."""
        try:
            creds = Credentials.from_service_account_file(
                self.credentials_file,
                scopes=self.SCOPES
            )
            service = build('sheets', 'v4', credentials=creds)
            return service
        except Exception as e:
            print(f"[Sheets] Auth error: {e}")
            raise

    def get_sheet_url(self) -> str:
        """Get the Google Sheet URL."""
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit"

    def load_release_notes(self) -> Dict:
        """Load processed release notes."""
        if os.path.exists(PROCESSED_NOTES_FILE):
            with open(PROCESSED_NOTES_FILE, 'r') as f:
                return json.load(f)
        return {}

    def populate_sheet(self, release_notes: Dict = None) -> bool:
        """
        Populate Google Sheet with release notes for approval.

        Args:
            release_notes: Release notes dict (loads from file if not provided)

        Returns:
            True if successful
        """
        if release_notes is None:
            release_notes = self.load_release_notes()

        if not release_notes:
            print("[Sheets] ERROR: No release notes found")
            return False

        product_lines = release_notes.get("product_lines", [])
        release_versions = release_notes.get("release_versions", {})
        tldr_by_pl = release_notes.get("tldr_by_pl", {})
        release_date = release_notes.get("release_summary", "").replace("Release ", "")

        if not release_date:
            release_date = datetime.now().strftime("%d %B %Y")

        print(f"[Sheets] Populating sheet with {len(product_lines)} PLs...")

        try:
            # Get current sheet data to find insertion point
            sheet = self.service.spreadsheets()
            result = sheet.values().get(
                spreadsheetId=self.sheet_id,
                range='A:I'
            ).execute()

            existing_values = result.get('values', [])
            start_row = len(existing_values) + 1

            # If sheet is empty, add headers first
            if start_row == 1:
                headers = [
                    ["PL Name", "Version", "TL;DR", "Status", "Voted By", "Voted At", "✓", "✗", "→"]
                ]
                sheet.values().update(
                    spreadsheetId=self.sheet_id,
                    range='A1:I1',
                    valueInputOption='RAW',
                    body={'values': headers}
                ).execute()
                start_row = 2

                # Format header row
                self._format_header_row()

            else:
                # Add separator for new release section
                separator = [["━" * 50, "", "", "", "", "", "", "", ""]]
                sheet.values().update(
                    spreadsheetId=self.sheet_id,
                    range=f'A{start_row}:I{start_row}',
                    valueInputOption='RAW',
                    body={'values': separator}
                ).execute()
                start_row += 1

            # Add date header
            date_header = [[f"📅 Release: {release_date}", "", "", "", "", "", "", "", ""]]
            sheet.values().update(
                spreadsheetId=self.sheet_id,
                range=f'A{start_row}:I{start_row}',
                valueInputOption='RAW',
                body={'values': date_header}
            ).execute()
            self._format_date_header(start_row)
            start_row += 1

            # Add column headers for this section
            col_headers = [["PL Name", "Version", "TL;DR", "Status", "Voted By", "Voted At", "✓", "✗", "→"]]
            sheet.values().update(
                spreadsheetId=self.sheet_id,
                range=f'A{start_row}:I{start_row}',
                valueInputOption='RAW',
                body={'values': col_headers}
            ).execute()
            self._format_column_headers(start_row)
            start_row += 1

            # Add PL rows
            rows = []
            for pl in product_lines:
                version = release_versions.get(pl, "Release")
                tldr = tldr_by_pl.get(pl, "")

                row = [
                    pl,                    # PL Name
                    version,               # Version
                    tldr,                  # TL;DR
                    "⏳ Pending",          # Status
                    "-",                   # Voted By
                    "-",                   # Voted At
                    "✓",                   # Approve button
                    "✗",                   # Reject button
                    "→"                    # Tomorrow button
                ]
                rows.append(row)

            # Write all rows
            sheet.values().update(
                spreadsheetId=self.sheet_id,
                range=f'A{start_row}:I{start_row + len(rows) - 1}',
                valueInputOption='RAW',
                body={'values': rows}
            ).execute()

            # Format button columns
            self._format_button_columns(start_row, start_row + len(rows) - 1)

            print(f"[Sheets] ✅ Added {len(rows)} PLs to sheet")
            print(f"[Sheets] URL: {self.get_sheet_url()}")

            # Send Slack notification
            self._notify_slack_new_release(release_date, len(rows))

            return True

        except HttpError as e:
            print(f"[Sheets] API error: {e}")
            return False

    def _format_header_row(self):
        """Format the main header row."""
        requests = [{
            "repeatCell": {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                        "textFormat": {"bold": True}
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)"
            }
        }]
        self._batch_update(requests)

    def _format_date_header(self, row: int):
        """Format a date header row."""
        requests = [{
            "repeatCell": {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.89, "green": 0.95, "blue": 0.99},
                        "textFormat": {"bold": True, "fontSize": 12}
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)"
            }
        }, {
            "mergeCells": {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9
                },
                "mergeType": "MERGE_ALL"
            }
        }]
        self._batch_update(requests)

    def _format_column_headers(self, row: int):
        """Format column header row."""
        requests = [{
            "repeatCell": {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.96, "green": 0.96, "blue": 0.96},
                        "textFormat": {"bold": True}
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)"
            }
        }]
        self._batch_update(requests)

    def _format_button_columns(self, start_row: int, end_row: int):
        """Format the button columns with colors."""
        requests = [
            # Approve column - green
            {
                "repeatCell": {
                    "range": {
                        "sheetId": 0,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": COL_APPROVE,
                        "endColumnIndex": COL_APPROVE + 1
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.78, "green": 0.9, "blue": 0.79},
                            "textFormat": {"foregroundColor": {"red": 0.18, "green": 0.49, "blue": 0.2}},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
                }
            },
            # Reject column - red
            {
                "repeatCell": {
                    "range": {
                        "sheetId": 0,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": COL_REJECT,
                        "endColumnIndex": COL_REJECT + 1
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.82},
                            "textFormat": {"foregroundColor": {"red": 0.78, "green": 0.16, "blue": 0.16}},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
                }
            },
            # Tomorrow column - orange
            {
                "repeatCell": {
                    "range": {
                        "sheetId": 0,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": COL_TOMORROW,
                        "endColumnIndex": COL_TOMORROW + 1
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 1.0, "green": 0.88, "blue": 0.7},
                            "textFormat": {"foregroundColor": {"red": 0.94, "green": 0.42, "blue": 0.0}},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"
                }
            }
        ]
        self._batch_update(requests)

    def _batch_update(self, requests: List[Dict]):
        """Execute batch update requests."""
        try:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.sheet_id,
                body={"requests": requests}
            ).execute()
        except HttpError as e:
            print(f"[Sheets] Batch update error: {e}")

    def get_approval_status(self) -> Dict:
        """Get current approval status from sheet."""
        try:
            sheet = self.service.spreadsheets()
            result = sheet.values().get(
                spreadsheetId=self.sheet_id,
                range='A:F'
            ).execute()

            values = result.get('values', [])

            approved = []
            rejected = []
            pending = []

            for row in values[1:]:  # Skip header
                if len(row) < 4:
                    continue

                pl_name = row[0] if len(row) > 0 else ""
                status = row[3] if len(row) > 3 else ""

                # Skip separators/headers
                if not pl_name or pl_name.startswith("━") or pl_name.startswith("📅"):
                    continue

                if "✅" in status or "Approved" in status:
                    approved.append(pl_name)
                elif "❌" in status or "➡️" in status:
                    rejected.append(pl_name)
                elif "Pending" in status or "⏳" in status:
                    pending.append(pl_name)

            return {
                "approved": approved,
                "rejected": rejected,
                "pending": pending,
                "total": len(approved) + len(rejected) + len(pending)
            }

        except HttpError as e:
            print(f"[Sheets] Error getting status: {e}")
            return {}

    def _notify_slack_new_release(self, release_date: str, pl_count: int):
        """Notify Slack that new release notes are ready for review."""
        if not self.slack_webhook:
            print("[Sheets] Slack webhook not configured")
            return

        message = (
            f"📋 *Release Notes Ready for Review*\n\n"
            f"*Release:* {release_date}\n"
            f"*PLs to Review:* {pl_count}\n\n"
            f"👉 <{self.get_sheet_url()}|Open Google Sheet to Approve/Reject>\n\n"
            f"_Click the ✓/✗/→ cells in the sheet to vote_"
        )

        self._send_slack(message)

    def _send_slack(self, message: str) -> bool:
        """Send message to Slack webhook."""
        if not self.slack_webhook:
            return False

        try:
            response = requests.post(
                self.slack_webhook,
                json={"text": message},
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            print(f"[Sheets] Slack error: {e}")
            return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Google Sheets Approval System')
    parser.add_argument('--populate', action='store_true', help='Populate sheet with release notes')
    parser.add_argument('--status', action='store_true', help='Check approval status')
    parser.add_argument('--url', action='store_true', help='Print sheet URL')
    args = parser.parse_args()

    try:
        handler = GoogleSheetsApproval()

        if args.populate:
            success = handler.populate_sheet()
            if success:
                print("\n✅ Sheet populated successfully!")
                print(f"📊 URL: {handler.get_sheet_url()}")
            else:
                print("\n❌ Failed to populate sheet")

        elif args.status:
            status = handler.get_approval_status()
            if status:
                print("\n📊 APPROVAL STATUS")
                print(f"  ✅ Approved: {len(status['approved'])}")
                print(f"  ⏳ Pending: {len(status['pending'])}")
                print(f"  ❌ Rejected: {len(status['rejected'])}")
                print(f"  Total: {status['total']}")
            else:
                print("\n❌ Could not get status")

        elif args.url:
            print(handler.get_sheet_url())

        else:
            parser.print_help()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
