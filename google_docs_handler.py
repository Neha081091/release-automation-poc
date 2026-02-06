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
import ssl
import certifi
import httplib2
from typing import List, Dict, Optional, Tuple
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_httplib2 import AuthorizedHttp

# Disable SSL verification for corporate proxy environments
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['CURL_CA_BUNDLE'] = ''


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
                # Create http object with SSL verification disabled for corporate proxy
                http = httplib2.Http(disable_ssl_certificate_validation=True)
                authed_http = AuthorizedHttp(self.creds, http=http)
                self.service = build('docs', 'v1', http=authed_http)
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

            # Build the service with SSL verification disabled for corporate proxy
            http = httplib2.Http(disable_ssl_certificate_validation=True)
            authed_http = AuthorizedHttp(self.creds, http=http)
            self.service = build('docs', 'v1', http=authed_http)
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

    def find_release_section_range(self, release_date: str) -> Optional[Tuple[int, int]]:
        """
        Find the start and end indices of an entire daily release section.

        Looks for "Daily Deployment Summary: {release_date}" and finds the section
        until the next release header or separator.

        Args:
            release_date: Date string to find (e.g., "6th February 2026")

        Returns:
            Tuple of (start_index, end_index) or None if not found
        """
        try:
            doc = self.service.documents().get(documentId=self.document_id).execute()
            content = doc.get('body', {}).get('content', [])

            import re

            # Build text with document index tracking
            text_segments = []
            full_text = ""

            for element in content:
                if 'paragraph' in element:
                    for text_run in element['paragraph'].get('elements', []):
                        if 'textRun' in text_run:
                            text_content = text_run['textRun'].get('content', '')
                            run_start = text_run.get('startIndex', 0)
                            run_end = text_run.get('endIndex', run_start + len(text_content))
                            text_segments.append((text_content, run_start, run_end))
                            full_text += text_content

            # Find the release header
            header_pattern = rf'Daily Deployment Summary:\s*{re.escape(release_date)}'
            match = re.search(header_pattern, full_text, re.IGNORECASE)

            if not match:
                print(f"[Google Docs] Could not find release section for date: {release_date}")
                return None

            print(f"[Google Docs] Found release header: {match.group()}")

            # Convert text position to document index
            def text_pos_to_doc_index(text_pos: int) -> int:
                current_pos = 0
                for text, doc_start, doc_end in text_segments:
                    if current_pos + len(text) > text_pos:
                        offset = text_pos - current_pos
                        return doc_start + offset
                    current_pos += len(text)
                return text_segments[-1][2] if text_segments else 1

            # Section starts at the header
            section_text_start = match.start()

            # Look for end of section - next release header or document end
            rest_text = full_text[match.end():]
            next_patterns = [
                r'\nDaily Deployment Summary:',  # Next day's release
                r'\n═{20,}\n+Daily Deployment Summary:',  # Separator then next release
            ]

            section_text_end = None
            for pattern in next_patterns:
                next_match = re.search(pattern, rest_text)
                if next_match:
                    if section_text_end is None or next_match.start() < section_text_end - match.end():
                        section_text_end = match.end() + next_match.start()

            # If found separator line before next release, include it
            if section_text_end is None:
                # Check for just the separator at the end
                separator_match = re.search(r'\n═{20,}\n*$', rest_text)
                if separator_match:
                    section_text_end = match.end() + separator_match.end()
                else:
                    # This is the only release in the document
                    section_text_end = len(full_text)

            # Convert to document indices
            doc_start = text_pos_to_doc_index(section_text_start)
            doc_end = text_pos_to_doc_index(section_text_end)

            print(f"[Google Docs] Release section range: text={section_text_start}-{section_text_end}, doc={doc_start}-{doc_end}")
            return (doc_start, doc_end)

        except HttpError as e:
            print(f"[Google Docs] Error finding release section: {e}")
            return None

    def remove_release_section(self, release_date: str) -> bool:
        """
        Remove an entire daily release section from the document.

        Args:
            release_date: Date string of the release to remove

        Returns:
            True if successful, False otherwise
        """
        print(f"[Google Docs] Removing release section for date: {release_date}")

        section_range = self.find_release_section_range(release_date)
        if not section_range:
            print(f"[Google Docs] Could not find release section to remove")
            return False

        try:
            start_idx, end_idx = section_range
            requests = [{
                'deleteContentRange': {
                    'range': {
                        'startIndex': start_idx,
                        'endIndex': end_idx
                    }
                }
            }]

            self.service.documents().batchUpdate(
                documentId=self.document_id,
                body={'requests': requests}
            ).execute()

            print(f"[Google Docs] Successfully removed release section for: {release_date}")
            return True

        except HttpError as e:
            print(f"[Google Docs] Error removing release section: {e}")
            return False

    def find_pl_section_range(self, pl_name: str) -> Optional[Tuple[int, int]]:
        """
        Find the start and end indices of a PL section in the document.

        Looks for patterns like "PL Name: Release X.X" and finds the section
        until the next PL header or separator.

        Args:
            pl_name: Name of the product line to find

        Returns:
            Tuple of (start_index, end_index) or None if not found
        """
        try:
            doc = self.service.documents().get(documentId=self.document_id).execute()
            content = doc.get('body', {}).get('content', [])

            import re
            # Clean PL name for matching (remove year suffix)
            pl_clean = re.sub(r'\s+20\d{2}$', '', pl_name)

            # Build text with document index tracking
            # Each entry: (text, doc_start_index, doc_end_index)
            text_segments = []
            full_text = ""

            for element in content:
                elem_start = element.get('startIndex', 0)
                elem_end = element.get('endIndex', elem_start)

                if 'paragraph' in element:
                    for text_run in element['paragraph'].get('elements', []):
                        if 'textRun' in text_run:
                            text_content = text_run['textRun'].get('content', '')
                            run_start = text_run.get('startIndex', elem_start)
                            run_end = text_run.get('endIndex', run_start + len(text_content))
                            text_segments.append((text_content, run_start, run_end))
                            full_text += text_content

            print(f"[Google Docs] Document text length: {len(full_text)}, segments: {len(text_segments)}")

            # Pattern to find the PL header line
            pattern = rf'{re.escape(pl_clean)}(?:\s+20\d{{2}})?:\s*Release\s+\d+\.\d+'
            match = re.search(pattern, full_text, re.IGNORECASE)

            if not match:
                pattern2 = rf'{re.escape(pl_clean)}[^:\n]*:\s*Release'
                match = re.search(pattern2, full_text, re.IGNORECASE)

            if not match:
                print(f"[Google Docs] Could not find section for PL: {pl_name}")
                if pl_clean.lower() in full_text.lower():
                    idx = full_text.lower().find(pl_clean.lower())
                    print(f"[Google Docs] Found '{pl_clean}' at text index {idx}: '{full_text[idx:idx+100]}'")
                return None

            print(f"[Google Docs] Found main section: {match.group()}")

            # Convert text position to document index
            def text_pos_to_doc_index(text_pos: int) -> int:
                """Convert text position to Google Docs document index."""
                current_pos = 0
                for text, doc_start, doc_end in text_segments:
                    if current_pos + len(text) > text_pos:
                        offset = text_pos - current_pos
                        return doc_start + offset
                    current_pos += len(text)
                # If past all segments, return last index
                return text_segments[-1][2] if text_segments else 1

            # Find section boundaries
            section_text_start = match.start()
            section_text_end = match.end()

            # Look back for section header (dashes)
            prev_text = full_text[:section_text_start]
            header_pos = prev_text.rfind('------------------')
            if header_pos != -1:
                # Start from after the header line
                header_end = full_text.find('\n', header_pos)
                if header_end != -1 and header_end < section_text_start:
                    section_text_start = header_end + 1

            # Look forward for next section
            rest_text = full_text[match.end():]
            next_patterns = [
                r'\n[A-Za-z][\w\s]+:\s*Release\s+\d+\.\d+',  # Next PL
                r'\n------------------[^-]+------------------',  # Category header
                r'\n═{20,}',  # Release separator
                r'\nDaily Deployment Summary:',  # Next day's release header
            ]

            boundaries = []
            for pattern in next_patterns:
                next_match = re.search(pattern, rest_text)
                if next_match:
                    boundaries.append(next_match.start())

            if boundaries:
                section_text_end = match.end() + min(boundaries)
            else:
                # SAFETY: If no boundary found, only delete up to next double newline
                # or a maximum of 2000 characters to prevent deleting too much
                next_blank = rest_text.find('\n\n\n')  # Triple newline often separates sections
                if next_blank != -1 and next_blank < 2000:
                    section_text_end = match.end() + next_blank
                else:
                    # Find double newline as fallback
                    double_newline = rest_text.find('\n\n')
                    if double_newline != -1:
                        # Look for next paragraph after this blank
                        after_blank = rest_text[double_newline+2:]
                        next_content = after_blank.find('\n')
                        if next_content != -1:
                            section_text_end = match.end() + double_newline + 2 + next_content
                        else:
                            section_text_end = match.end() + double_newline + 2
                    else:
                        # Last resort: limit to 2000 chars to prevent disaster
                        section_text_end = min(match.end() + len(rest_text), match.end() + 2000)
                print(f"[Google Docs] Warning: No clear section boundary found, using safe fallback")

            # Convert to document indices
            doc_start = text_pos_to_doc_index(section_text_start)
            doc_end = text_pos_to_doc_index(section_text_end)

            print(f"[Google Docs] Section range: text={section_text_start}-{section_text_end}, doc={doc_start}-{doc_end}")
            return (doc_start, doc_end)

        except HttpError as e:
            print(f"[Google Docs] Error finding PL section: {e}")
            return None

    def find_tldr_line_range(self, pl_name: str) -> Optional[Tuple[int, int]]:
        """
        Find the TL;DR bullet line for a specific PL.

        Looks for patterns like "• Audiences PL1 - description..." in the TL;DR section.

        Args:
            pl_name: Name of the product line to find

        Returns:
            Tuple of (start_index, end_index) or None if not found
        """
        try:
            doc = self.service.documents().get(documentId=self.document_id).execute()
            content = doc.get('body', {}).get('content', [])

            import re
            # Clean PL name for matching (remove year suffix)
            pl_clean = re.sub(r'\s+20\d{2}$', '', pl_name)

            # Build text with document index tracking
            text_segments = []
            full_text = ""

            for element in content:
                if 'paragraph' in element:
                    for text_run in element['paragraph'].get('elements', []):
                        if 'textRun' in text_run:
                            text_content = text_run['textRun'].get('content', '')
                            run_start = text_run.get('startIndex', 0)
                            run_end = text_run.get('endIndex', run_start + len(text_content))
                            text_segments.append((text_content, run_start, run_end))
                            full_text += text_content

            # Find the TL;DR section boundaries
            tldr_start = full_text.find('TL;DR')
            if tldr_start == -1:
                print(f"[Google Docs] Could not find TL;DR section")
                return None

            # Find end of TL;DR header line
            tldr_header_end = full_text.find('\n', tldr_start)
            if tldr_header_end == -1:
                tldr_header_end = tldr_start + 50

            # Find end of TL;DR section (next major section with dashes)
            tldr_section_end = full_text.find('------------------', tldr_header_end)
            if tldr_section_end == -1:
                tldr_section_end = len(full_text)

            tldr_section = full_text[tldr_header_end:tldr_section_end]
            print(f"[Google Docs] TL;DR section preview: {tldr_section[:300]}...")

            # Patterns to find the TL;DR bullet line
            # Try multiple patterns from strict to flexible
            patterns = [
                rf'[•●]\s*{re.escape(pl_clean)}(?:\s+20\d{{2}})?\s*[-–—]\s*[^\n]+\n?',  # Unicode bullet
                rf'[•●\*\-]\s*\*?{re.escape(pl_clean)}(?:\s+20\d{{2}})?\*?\s*[-–—]\s*[^\n]+\n?',  # With bold
                rf'{re.escape(pl_clean)}(?:\s+20\d{{2}})?\s*[-–—]\s*[^\n]+\n?',  # No bullet
            ]

            match = None
            for i, pattern in enumerate(patterns):
                match = re.search(pattern, tldr_section, re.IGNORECASE)
                if match:
                    print(f"[Google Docs] Found TL;DR with pattern {i+1}: '{match.group()[:60]}...'")
                    break

            if not match:
                print(f"[Google Docs] Could not find TL;DR line for PL: {pl_name}")
                print(f"[Google Docs] PL clean name: '{pl_clean}'")
                return None

            # Find the full line (from line start to line end)
            line_start_in_section = tldr_section.rfind('\n', 0, match.start())
            if line_start_in_section == -1:
                line_start_in_section = 0
            else:
                line_start_in_section += 1  # Skip the newline

            line_end_in_section = match.end()

            # Convert to full text positions
            text_start = tldr_header_end + line_start_in_section
            text_end = tldr_header_end + line_end_in_section

            # Convert text position to document index
            def text_pos_to_doc_index(text_pos: int) -> int:
                current_pos = 0
                for text, doc_start, doc_end in text_segments:
                    if current_pos + len(text) > text_pos:
                        offset = text_pos - current_pos
                        return doc_start + offset
                    current_pos += len(text)
                return text_segments[-1][2] if text_segments else 1

            doc_start = text_pos_to_doc_index(text_start)
            doc_end = text_pos_to_doc_index(text_end)

            print(f"[Google Docs] TL;DR line range: text={text_start}-{text_end}, doc={doc_start}-{doc_end}")
            return (doc_start, doc_end)

        except HttpError as e:
            print(f"[Google Docs] Error finding TL;DR line: {e}")
            return None

    def remove_pl_section(self, pl_name: str) -> bool:
        """
        Remove a PL's section AND its TL;DR entry from the document.

        Args:
            pl_name: Name of the product line to remove

        Returns:
            True if successful, False otherwise
        """
        print(f"[Google Docs] Removing section for PL: {pl_name}")

        # Find the main section range
        section_range = self.find_pl_section_range(pl_name)

        # Find the TL;DR line range
        tldr_range = self.find_tldr_line_range(pl_name)

        if not section_range and not tldr_range:
            print(f"[Google Docs] Could not find any content to remove for PL: {pl_name}")
            return False

        try:
            requests = []

            # IMPORTANT: Delete in reverse order (higher indices first)
            # to avoid index shifting issues
            ranges_to_delete = []

            if section_range:
                ranges_to_delete.append(section_range)
                print(f"[Google Docs] Found main section at indices {section_range[0]} to {section_range[1]}")

            if tldr_range:
                ranges_to_delete.append(tldr_range)
                print(f"[Google Docs] Found TL;DR line at indices {tldr_range[0]} to {tldr_range[1]}")

            # Sort by start index in descending order (delete from end first)
            ranges_to_delete.sort(key=lambda x: x[0], reverse=True)

            for start_idx, end_idx in ranges_to_delete:
                requests.append({
                    'deleteContentRange': {
                        'range': {
                            'startIndex': start_idx,
                            'endIndex': end_idx
                        }
                    }
                })

            self.service.documents().batchUpdate(
                documentId=self.document_id,
                body={'requests': requests}
            ).execute()

            print(f"[Google Docs] Successfully removed {len(requests)} section(s) for PL: {pl_name}")
            return True

        except HttpError as e:
            print(f"[Google Docs] Error removing PL section: {e}")
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
                             tldr: Dict, extract_value_adds_func,
                             consolidated_bodies: Dict = None) -> List[Dict]:
    """
    Create Google Docs API requests for formatted release notes.

    This is a helper function that creates properly formatted requests
    for the Google Docs API with hyperlinks, bold text, and proper spacing.

    Args:
        release_date: Release date string
        grouped_data: Grouped ticket data by PL and Epic
        tldr: TL;DR dictionary
        extract_value_adds_func: Function to extract value adds from tickets
        consolidated_bodies: Optional dict mapping PL name to LLM-consolidated body text

    Returns:
        List of Google Docs API requests
    """
    requests = []
    current_index = 1

    # Define product line order (includes DSP Core PL variants)
    # PLs not in this list will be added at the end
    preferred_order = [
        "DSP Core PL1", "DSP Core PL2", "DSP Core PL3", "DSP Core PL5",
        "DSP PL1", "DSP PL2", "DSP PL3", "DSP",
        "Audiences PL1", "Audiences PL2", "Audiences",
        "Media PL1", "Media",
        "Helix PL3", "Helix",
        "Developer Experience", "Developer Experience 2026",
        "Data Governance", "Other"
    ]

    # Build actual order: preferred first, then any others
    product_line_order = []
    for pl in preferred_order:
        if pl in grouped_data:
            product_line_order.append(pl)
    # Add any PLs not in preferred order
    for pl in grouped_data.keys():
        if pl not in product_line_order:
            product_line_order.append(pl)

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

        # Check if we have LLM-consolidated body for this PL
        if consolidated_bodies and pl in consolidated_bodies:
            # Use LLM-consolidated body text (polished prose)
            consolidated_text = consolidated_bodies[pl]
            if consolidated_text:
                # Add the consolidated body text
                body_text = f"{consolidated_text}\n\n"
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": body_text
                    }
                })
                current_index += len(body_text)
        else:
            # Fallback: Process epics individually (raw Jira summaries)
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
