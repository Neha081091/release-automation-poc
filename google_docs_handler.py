"""
Google Docs API Handler for Release Automation PoC

This module handles all Google Docs operations:
- OAuth authentication
- Clearing document content
- Inserting formatted release notes
- Managing hyperlinks and text styling
"""

import os
import json
import pickle
from typing import List, Dict, Optional, Tuple
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# OAuth scopes required for Google Docs
SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive.file'
]


class GoogleDocsHandler:
    """Handler for Google Docs API operations."""

    def __init__(self, document_id: str = None, credentials_path: str = None):
        """
        Initialize Google Docs handler.

        Args:
            document_id: Google Doc ID to work with
            credentials_path: Path to OAuth credentials file
        """
        self.document_id = document_id or os.getenv('GOOGLE_DOC_ID')
        self.credentials_path = credentials_path or os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
        self.service_account_path = os.getenv('GOOGLE_SERVICE_ACCOUNT_PATH', 'service_account.json')
        self.token_path = os.getenv('GOOGLE_TOKEN_PATH', 'token.pickle')
        self.service = None
        self.creds = None

        if not self.document_id:
            raise ValueError("GOOGLE_DOC_ID must be provided")

        print(f"[Google Docs] Initialized handler for document: {self.document_id}")

    def authenticate(self) -> bool:
        """
        Authenticate with Google.
        Supports service account, web credentials, and desktop credentials.

        Returns:
            True if authentication successful, False otherwise
        """
        print("[Google Docs] Authenticating...")

        try:
            # First, try service account (preferred for automation)
            if os.path.exists(self.service_account_path):
                print("[Google Docs] Using service account authentication...")
                self.creds = service_account.Credentials.from_service_account_file(
                    self.service_account_path, scopes=SCOPES)
                self.service = build('docs', 'v1', credentials=self.creds)
                print("[Google Docs] Service account authentication successful")
                return True

            # Check for existing OAuth token
            if os.path.exists(self.token_path):
                with open(self.token_path, 'rb') as token:
                    self.creds = pickle.load(token)
                print("[Google Docs] Loaded existing token")

            # If no valid credentials, authenticate with OAuth
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    print("[Google Docs] Refreshing expired token...")
                    self.creds.refresh(Request())
                else:
                    if not os.path.exists(self.credentials_path):
                        print(f"[Google Docs] Credentials file not found: {self.credentials_path}")
                        print("[Google Docs] Please download credentials.json from Google Cloud Console")
                        return False

                    # Detect credential type (web vs installed)
                    with open(self.credentials_path, 'r') as f:
                        creds_data = json.load(f)

                    if 'web' in creds_data:
                        print("[Google Docs] Detected 'web' credentials, using web flow...")
                        web_creds = creds_data['web']
                        installed_format = {
                            "installed": {
                                "client_id": web_creds["client_id"],
                                "client_secret": web_creds["client_secret"],
                                "auth_uri": web_creds["auth_uri"],
                                "token_uri": web_creds["token_uri"],
                                "redirect_uris": ["http://localhost"]
                            }
                        }
                        flow = InstalledAppFlow.from_client_config(installed_format, SCOPES)
                    else:
                        print("[Google Docs] Starting OAuth flow...")
                        flow = InstalledAppFlow.from_client_secrets_file(
                            self.credentials_path, SCOPES)

                    self.creds = flow.run_local_server(port=0)

                # Save the credentials for next run
                with open(self.token_path, 'wb') as token:
                    pickle.dump(self.creds, token)
                print("[Google Docs] Saved new token")

            # Build the service
            self.service = build('docs', 'v1', credentials=self.creds)
            print("[Google Docs] Authentication successful")
            return True

        except Exception as e:
            print(f"[Google Docs] Authentication failed: {e}")
            return False

    def clear_document(self) -> bool:
        """
        Clear all content from the document.

        Returns:
            True if successful, False otherwise
        """
        print(f"[Google Docs] Clearing document content...")

        try:
            # Get current document content
            doc = self.service.documents().get(documentId=self.document_id).execute()
            content = doc.get('body', {}).get('content', [])

            if len(content) <= 1:
                print("[Google Docs] Document is already empty")
                return True

            # Find the end index
            end_index = content[-1].get('endIndex', 1) - 1

            if end_index <= 1:
                print("[Google Docs] Document has no content to clear")
                return True

            # Delete all content
            requests = [{
                'deleteContentRange': {
                    'range': {
                        'startIndex': 1,
                        'endIndex': end_index
                    }
                }
            }]

            self.service.documents().batchUpdate(
                documentId=self.document_id,
                body={'requests': requests}
            ).execute()

            print("[Google Docs] Document cleared successfully")
            return True

        except HttpError as e:
            print(f"[Google Docs] Error clearing document: {e}")
            return False

    def update_document(self, requests: List[Dict]) -> bool:
        """
        Apply formatting requests to the document.

        Args:
            requests: List of Google Docs API requests

        Returns:
            True if successful, False otherwise
        """
        print(f"[Google Docs] Applying {len(requests)} updates...")

        if not requests:
            print("[Google Docs] No updates to apply")
            return True

        try:
            self.service.documents().batchUpdate(
                documentId=self.document_id,
                body={'requests': requests}
            ).execute()

            print("[Google Docs] Document updated successfully")
            return True

        except HttpError as e:
            print(f"[Google Docs] Error updating document: {e}")
            return False

    def insert_release_notes(self, formatter_requests: List[Dict]) -> bool:
        """
        Insert formatted release notes into the document.

        First clears the document, then applies the formatting.

        Args:
            formatter_requests: List of formatting requests from the formatter

        Returns:
            True if successful, False otherwise
        """
        print("[Google Docs] Inserting release notes...")

        # Clear existing content first
        if not self.clear_document():
            print("[Google Docs] Warning: Could not clear document, proceeding anyway")

        # Apply the formatting requests
        return self.update_document(formatter_requests)

    def insert_plain_text(self, text: str) -> bool:
        """
        Insert plain text into the document.

        Args:
            text: Plain text to insert

        Returns:
            True if successful, False otherwise
        """
        print("[Google Docs] Inserting plain text...")

        # Clear existing content first
        if not self.clear_document():
            print("[Google Docs] Warning: Could not clear document, proceeding anyway")

        requests = [{
            'insertText': {
                'location': {'index': 1},
                'text': text
            }
        }]

        return self.update_document(requests)

    def get_document_url(self) -> str:
        """
        Get the shareable URL for the document.

        Returns:
            Document URL
        """
        return f"https://docs.google.com/document/d/{self.document_id}/edit"

    def get_document_content(self) -> Optional[str]:
        """
        Get the current content of the document.

        Returns:
            Document content as text or None on error
        """
        try:
            doc = self.service.documents().get(documentId=self.document_id).execute()
            content = doc.get('body', {}).get('content', [])

            text_parts = []
            for element in content:
                if 'paragraph' in element:
                    for text_run in element['paragraph'].get('elements', []):
                        if 'textRun' in text_run:
                            text_parts.append(text_run['textRun'].get('content', ''))

            return ''.join(text_parts)

        except HttpError as e:
            print(f"[Google Docs] Error reading document: {e}")
            return None

    def test_connection(self) -> bool:
        """
        Test the Google Docs connection.

        Returns:
            True if connection is successful, False otherwise
        """
        print("[Google Docs] Testing connection...")

        try:
            doc = self.service.documents().get(documentId=self.document_id).execute()
            title = doc.get('title', 'Unknown')
            print(f"[Google Docs] Connected to document: {title}")
            return True

        except HttpError as e:
            print(f"[Google Docs] Connection test failed: {e}")
            return False


def parse_fix_version(fix_version: str) -> Tuple[str, str]:
    """
    Parse fix version string to extract PL name and release version.

    Examples:
        "DSP Core PL3 2026: Release 4.0" -> ("DSP Core PL3", "Release 4.0")
        "Developer Experience: Release 6.0" -> ("Developer Experience", "Release 6.0")
        "Audiences PL2: Release 4.0" -> ("Audiences PL2", "Release 4.0")

    Args:
        fix_version: Fix version string from Jira

    Returns:
        Tuple of (pl_name, release_version)
    """
    import re

    # Try to match pattern with year: "DSP Core PL3 2026: Release 4.0"
    match = re.match(r'^(.+?)\s*\d{4}:\s*(Release\s+\d+\.\d+)$', fix_version)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    # Try to match pattern without year: "Developer Experience: Release 6.0"
    match = re.match(r'^(.+?):\s*(Release\s+\d+\.\d+)$', fix_version)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    # Fallback: return as-is
    return fix_version, ""


def create_formatted_requests(release_date: str, grouped_data: Dict,
                             tldr: Dict, extract_value_adds_func) -> List[Dict]:
    """
    Create Google Docs API requests for formatted release notes.

    This is a helper function that creates properly formatted requests
    for the Google Docs API with hyperlinks, bold text, and proper spacing.

    Args:
        release_date: Release date string
        grouped_data: Grouped ticket data by PL and Epic
        tldr: TL;DR dictionary
        extract_value_adds_func: Function to extract value adds from tickets

    Returns:
        List of Google Docs API requests
    """
    requests = []
    current_index = 1

    # Define product line order
    product_line_order = [
        "DSP", "DSP PL1", "DSP PL2", "DSP PL3",
        "Audiences", "Audiences PL1",
        "Media", "Media PL1",
        "Helix", "Helix PL3",
        "Developer Experience", "Data Governance", "Other"
    ]

    # Title
    title = f"Daily Deployment Summary: {release_date}\n\n"
    requests.append({
        "insertText": {
            "location": {"index": current_index},
            "text": title
        }
    })
    # Style title as heading
    requests.append({
        "updateParagraphStyle": {
            "range": {
                "startIndex": current_index,
                "endIndex": current_index + len(title) - 1
            },
            "paragraphStyle": {
                "namedStyleType": "HEADING_1"
            },
            "fields": "namedStyleType"
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

    # Key Deployments header
    key_deploy_header = "Key Deployments:\n"
    key_deploy_start = current_index
    requests.append({
        "insertText": {
            "location": {"index": current_index},
            "text": key_deploy_header
        }
    })
    # Bold "Key Deployments:"
    requests.append({
        "updateTextStyle": {
            "range": {
                "startIndex": key_deploy_start,
                "endIndex": key_deploy_start + len("Key Deployments:")
            },
            "textStyle": {"bold": True},
            "fields": "bold"
        }
    })
    current_index += len(key_deploy_header)

    # TL;DR content - Key Deployments per PL (with bold PL names)
    for deployment in tldr.get("key_deployments", []):
        fix_version = deployment.get("version", "")
        summary = deployment.get("summary", "")

        # Parse fix version to get proper PL name
        if fix_version:
            pl_display, release_ver = parse_fix_version(fix_version)
        else:
            pl_display = deployment["pl"]
            release_ver = ""

        # Format: "   • DSP Core PL1 - summary text"
        bullet_prefix = "   • "
        deploy_line = f"{bullet_prefix}{pl_display} - {summary}\n"

        deploy_start = current_index
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": deploy_line
            }
        })

        # Bold the PL name part
        pl_start = current_index + len(bullet_prefix)
        pl_end = pl_start + len(pl_display)
        requests.append({
            "updateTextStyle": {
                "range": {
                    "startIndex": pl_start,
                    "endIndex": pl_end
                },
                "textStyle": {"bold": True},
                "fields": "bold"
            }
        })

        current_index += len(deploy_line)

    # Add blank line after TL;DR
    requests.append({
        "insertText": {
            "location": {"index": current_index},
            "text": "\n"
        }
    })
    current_index += 1

    # Process each product line
    for pl in product_line_order:
        if pl not in grouped_data:
            continue

        epics = grouped_data[pl]

        # Product line header
        pl_header = f"------------------{pl}------------------\n\n"
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": pl_header
            }
        })
        current_index += len(pl_header)

        # Get fix version from first ticket
        first_ticket = list(epics.values())[0][0]["ticket"]
        fix_version = first_ticket.get("fix_version")
        fix_version_url = first_ticket.get("fix_version_url")

        if fix_version:
            # Parse fix version to get proper display format
            pl_display, release_ver = parse_fix_version(fix_version)

            if release_ver:
                # Format: "DSP Core PL3: Release 4.0" (PL bold, Release as hyperlink)
                version_line = f"{pl_display}: {release_ver}\n\n"
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": version_line
                    }
                })

                # Bold the PL name part
                pl_start = current_index
                pl_end = pl_start + len(pl_display) + 1  # Include the colon
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": pl_start,
                            "endIndex": pl_end
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold"
                    }
                })

                # Add hyperlink to release version
                if fix_version_url:
                    link_start = current_index + len(pl_display) + 2  # After "PL: "
                    link_end = link_start + len(release_ver)
                    requests.append({
                        "updateTextStyle": {
                            "range": {
                                "startIndex": link_start,
                                "endIndex": link_end
                            },
                            "textStyle": {
                                "link": {"url": fix_version_url},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"red": 0.0, "green": 0.0, "blue": 1.0}}
                                },
                                "underline": True
                            },
                            "fields": "link,foregroundColor,underline"
                        }
                    })

                current_index += len(version_line)
            else:
                # Fallback to original format
                version_line = f"{fix_version}\n\n"
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": version_line
                    }
                })
                if fix_version_url:
                    requests.append({
                        "updateTextStyle": {
                            "range": {
                                "startIndex": current_index,
                                "endIndex": current_index + len(fix_version)
                            },
                            "textStyle": {
                                "link": {"url": fix_version_url},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"red": 0.0, "green": 0.0, "blue": 1.0}}
                                }
                            },
                            "fields": "link,foregroundColor"
                        }
                    })
                current_index += len(version_line)

        # Add approval checkboxes for this PL
        approval_text = "☐ Yes   ☐ No   ☐ Release Tomorrow\n\n"
        approval_start = current_index
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": approval_text
            }
        })
        # Style the approval checkboxes with gray color
        requests.append({
            "updateTextStyle": {
                "range": {
                    "startIndex": approval_start,
                    "endIndex": approval_start + len(approval_text) - 2
                },
                "textStyle": {
                    "foregroundColor": {
                        "color": {"rgbColor": {"red": 0.4, "green": 0.4, "blue": 0.4}}
                    }
                },
                "fields": "foregroundColor"
            }
        })
        current_index += len(approval_text)

        # Process epics
        for epic_name, items in epics.items():
            epic_info = items[0]["epic_info"]

            # Epic name
            epic_text = f"{epic_name}\n\n"
            epic_start = current_index
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": epic_text
                }
            })

            # Make epic a hyperlink if URL exists
            if epic_info.get("url"):
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": epic_start,
                            "endIndex": epic_start + len(epic_name)
                        },
                        "textStyle": {
                            "link": {"url": epic_info["url"]},
                            "foregroundColor": {
                                "color": {"rgbColor": {"red": 0.0, "green": 0.0, "blue": 1.0}}
                            }
                        },
                        "fields": "link,foregroundColor"
                    }
                })

            current_index += len(epic_text)

            # Value Add header
            value_add_header = "Value Add:\n"
            value_add_start = current_index
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": value_add_header
                }
            })
            # Bold "Value Add:"
            requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": value_add_start,
                        "endIndex": value_add_start + len("Value Add:")
                    },
                    "textStyle": {"bold": True},
                    "fields": "bold"
                }
            })
            current_index += len(value_add_header)

            # Value add bullets
            for item in items:
                ticket = item["ticket"]
                value_adds = extract_value_adds_func(ticket)
                for value_add in value_adds:
                    bullet = f"   * {value_add}\n"
                    requests.append({
                        "insertText": {
                            "location": {"index": current_index},
                            "text": bullet
                        }
                    })
                    current_index += len(bullet)

            # Release type tag for stories with colors
            story_tickets = [i["ticket"] for i in items if i["ticket"].get("issue_type") == "Story"]
            if story_tickets:
                release_type = story_tickets[0].get("release_type")
                if release_type:
                    tag_text = f"\n{release_type}\n"
                    tag_start = current_index + 1  # After the first newline
                    requests.append({
                        "insertText": {
                            "location": {"index": current_index},
                            "text": tag_text
                        }
                    })

                    # Apply color based on release type
                    # Feature Flag = green, General Availability = green
                    if "feature flag" in release_type.lower():
                        requests.append({
                            "updateTextStyle": {
                                "range": {
                                    "startIndex": tag_start,
                                    "endIndex": tag_start + len(release_type)
                                },
                                "textStyle": {
                                    "foregroundColor": {
                                        "color": {"rgbColor": {"red": 0.13, "green": 0.55, "blue": 0.13}}
                                    }
                                },
                                "fields": "foregroundColor"
                            }
                        })
                    elif "general availability" in release_type.lower() or release_type.lower() == "ga":
                        requests.append({
                            "updateTextStyle": {
                                "range": {
                                    "startIndex": tag_start,
                                    "endIndex": tag_start + len(release_type)
                                },
                                "textStyle": {
                                    "foregroundColor": {
                                        "color": {"rgbColor": {"red": 0.13, "green": 0.55, "blue": 0.13}}
                                    }
                                },
                                "fields": "foregroundColor"
                            }
                        })

                    current_index += len(tag_text)

            # Spacing
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": "\n"
                }
            })
            current_index += 1

        # Spacing between product lines
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": "\n"
            }
        })
        current_index += 1

    return requests


def main():
    """Test the Google Docs handler."""
    from dotenv import load_dotenv
    load_dotenv()

    try:
        handler = GoogleDocsHandler()

        if not handler.authenticate():
            print("Failed to authenticate with Google")
            return

        if not handler.test_connection():
            print("Failed to connect to Google Doc")
            return

        # Test inserting some content
        test_content = """Daily Deployment Summary: 2nd February 2026

------------------TL;DR:------------------

   * Deployments by: DSP PL2, Audiences PL1
   * Major Feature: New targeting options for campaigns
   * Key Enhancement: Performance improvements

------------------DSP------------------

DSP PL2: Release 61.0

Campaign Targeting Enhancement

Value Add:
   * Add new targeting options for DSP campaigns
   * Geographic and demographic targeting capabilities

`General Availability`

"""
        if handler.insert_plain_text(test_content):
            print(f"\nDocument URL: {handler.get_document_url()}")
        else:
            print("Failed to update document")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
