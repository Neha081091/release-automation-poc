#!/usr/bin/env python3
"""
Refresh Handler for Release Automation

This module handles the "Refresh" functionality that allows teams to
check for new Jira versions and add them to existing release notes
without affecting already-edited content.

Features:
- Re-fetches Jira tickets for the current release date
- Identifies NEW PLs/fix versions not already processed
- Adds only new PL sections to Google Doc (preserves existing)
- Updates Slack approval message with new PL buttons
"""

import os
import json
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dotenv import load_dotenv

load_dotenv()

from jira_handler import JiraHandler
from google_docs_handler import GoogleDocsHandler
from google_docs_formatter import GoogleDocsFormatter, get_ordered_pls, PRODUCT_LINE_ORDER


def get_day_suffix(day: int) -> str:
    """Get the ordinal suffix for a day (1st, 2nd, 3rd, 4th, etc.)."""
    if 11 <= day <= 13:
        return 'th'
    suffix_map = {1: 'st', 2: 'nd', 3: 'rd'}
    return suffix_map.get(day % 10, 'th')


def format_release_date_for_jira(date: datetime = None) -> str:
    """Format date for Jira fix version search (e.g., '9th February 2026')."""
    if date is None:
        date = datetime.now()
    day = date.day
    suffix = get_day_suffix(day)
    month = date.strftime('%B')
    year = date.year
    return f"{day}{suffix} {month} {year}"


def clean_pl_name(pl_name: str) -> str:
    """Remove year suffix from PL name."""
    return re.sub(r'\s+20\d{2}$', '', pl_name)


def _get_pl_category(pl_name: str) -> str:
    """Determine the category header for a product line."""
    pl_lower = pl_name.lower()
    if 'media' in pl_lower:
        return "Media"
    if 'audience' in pl_lower:
        return "Audiences"
    if 'developer' in pl_lower:
        return "Developer Experience"
    if 'data ingress' in pl_lower:
        return "Data Ingress"
    if 'data governance' in pl_lower:
        return "Data Governance"
    if 'helix' in pl_lower:
        return "Helix"
    if 'dsp' in pl_lower:
        return "DSP"
    return pl_name


def _build_text_segments(doc: dict) -> list:
    """Build text segments with doc indices for position mapping."""
    segments = []
    for element in doc.get('body', {}).get('content', []):
        if 'paragraph' in element:
            for text_run in element['paragraph'].get('elements', []):
                if 'textRun' in text_run:
                    text = text_run['textRun'].get('content', '')
                    start = text_run.get('startIndex', 1)
                    end = text_run.get('endIndex', start + len(text))
                    segments.append((text, start, end))
    return segments


def _text_pos_to_doc_index(text_pos: int, segments: list) -> int:
    """Convert a text position to a document index."""
    current_pos = 0
    for text, doc_start, doc_end in segments:
        if current_pos + len(text) > text_pos:
            offset = text_pos - current_pos
            return doc_start + offset
        current_pos += len(text)
    return segments[-1][2] if segments else 1


def _pl_present_in_doc(content: str, pl_name: str) -> bool:
    """Check if a PL header exists in the Google Doc content."""
    pl_clean = clean_pl_name(pl_name)
    escaped = re.escape(pl_clean)
    # Match either "PL: Release X.X" or dashed PL header
    patterns = [
        rf'\b{escaped}\s*:\s*Release\s*\d+\.\d+',
        rf'------------------{escaped}------------------'
    ]
    for pattern in patterns:
        if re.search(pattern, content, re.IGNORECASE):
            return True
    return False


def _load_existing_ticket_keys() -> set:
    """Load existing ticket keys from tickets_export.json if available."""
    keys = set()
    try:
        if os.path.exists('tickets_export.json'):
            with open('tickets_export.json', 'r') as f:
                data = json.load(f)
            for ticket in data.get('tickets', []):
                key = ticket.get('key')
                if key:
                    keys.add(key)
    except Exception:
        pass
    return keys


def _merge_tickets_export(delta_tickets: List[Dict], release_summary: str = None, release_key: str = None) -> None:
    """Append newly discovered tickets into tickets_export.json."""
    if not delta_tickets:
        return
    try:
        existing = {}
        if os.path.exists('tickets_export.json'):
            with open('tickets_export.json', 'r') as f:
                existing = json.load(f)
        existing.setdefault('tickets', [])
        existing_keys = {t.get('key') for t in existing.get('tickets', []) if t.get('key')}
        for ticket in delta_tickets:
            key = ticket.get('key')
            if key and key not in existing_keys:
                existing['tickets'].append(ticket)
                existing_keys.add(key)
        existing['ticket_count'] = len(existing.get('tickets', []))
        existing['exported_at'] = datetime.now().isoformat()
        if release_summary and not existing.get('release_summary'):
            existing['release_summary'] = release_summary
        if release_key and not existing.get('release_key'):
            existing['release_key'] = release_key
        with open('tickets_export.json', 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass


def _find_pl_section_end(content: str, pl_name: str) -> Optional[int]:
    """Find end position of a PL section in the document content."""
    pl_clean = clean_pl_name(pl_name)
    header_pattern = rf'^{re.escape(pl_clean)}\s*:\s*Release\s+\d+\.\d+.*$'
    match = re.search(header_pattern, content, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    start = match.end()
    rest = content[start:]
    next_patterns = [
        r'\n------------------[^-]+------------------',
        r'\n[^\\n]+:\\s*Release\\s+\\d+\\.\\d+',
        r'\n═{20,}'
    ]
    end_pos = len(rest)
    for pattern in next_patterns:
        next_match = re.search(pattern, rest)
        if next_match and next_match.start() < end_pos:
            end_pos = next_match.start()
    return start + end_pos


def _find_tldr_section_bounds(content: str) -> Optional[Tuple[int, int, int]]:
    """Find TL;DR section bounds and insertion start."""
    header_match = re.search(r'-{10,}\s*TL;DR:?\s*-{10,}', content, re.IGNORECASE)
    if not header_match:
        return None
    section_start = header_match.end()
    rest = content[section_start:]
    next_header = re.search(r'\n-{10,}[^-]+-{10,}', rest)
    section_end = section_start + (next_header.start() if next_header else len(rest))
    return header_match.start(), section_end, section_start


def _find_tldr_line_range(content: str, pl_name: str) -> Optional[Tuple[int, int]]:
    """Find the start/end positions of a TL;DR line for a PL."""
    bounds = _find_tldr_section_bounds(content)
    if not bounds:
        return None
    _, section_end, section_start = bounds
    section_text = content[section_start:section_end]
    pl_clean = clean_pl_name(pl_name)
    line_match = re.search(rf'^{re.escape(pl_clean)}\s*-\s*.+$', section_text, re.MULTILINE)
    if not line_match:
        return None
    line_start = section_start + line_match.start()
    line_end = section_start + line_match.end()
    # include trailing newline if present
    if line_end < len(content) and content[line_end:line_end + 1] == "\n":
        line_end += 1
    return line_start, line_end


def update_tldr_lines_for_existing_pls(tldr_updates: Dict[str, str], release_date: str) -> bool:
    """Update TL;DR lines for existing PLs in the Google Doc."""
    if not tldr_updates:
        return False
    try:
        google_docs = GoogleDocsHandler()
        if not google_docs.authenticate() or not google_docs.test_connection():
            print("[Refresh] ERROR: Could not connect to Google Doc")
            return False

        content = google_docs.get_document_content()
        if not content:
            print("[Refresh] ERROR: Could not read document content")
            return False

        release_header = f"Daily Deployment Summary: {release_date}"
        if release_header not in content:
            print(f"[Refresh] Release '{release_date}' not found in document")
            return False

        doc = google_docs.service.documents().get(documentId=google_docs.document_id).execute()
        segments = _build_text_segments(doc)

        jobs = []
        for pl, summary in tldr_updates.items():
            if not summary:
                continue
            pl_clean = clean_pl_name(pl)
            new_line = f"{pl_clean} - {summary}\n"
            line_range = _find_tldr_line_range(content, pl_clean)
            if line_range:
                start_pos, end_pos = line_range
                start_idx = _text_pos_to_doc_index(start_pos, segments)
                end_idx = _text_pos_to_doc_index(end_pos, segments)
                delete_req = {"deleteContentRange": {"range": {"startIndex": start_idx, "endIndex": end_idx}}}
                insert_req = {"insertText": {"location": {"index": start_idx}, "text": new_line}}
                format_reqs = [
                    {"updateTextStyle": {"range": {"startIndex": start_idx, "endIndex": start_idx + len(new_line)}, "textStyle": {"bold": False}, "fields": "bold"}},
                    {"updateTextStyle": {"range": {"startIndex": start_idx, "endIndex": start_idx + len(pl_clean)}, "textStyle": {"bold": True}, "fields": "bold"}}
                ]
                jobs.append((start_idx, [delete_req, insert_req], format_reqs))
            else:
                bounds = _find_tldr_section_bounds(content)
                if not bounds:
                    continue
                _, section_end, section_start = bounds
                insert_idx = _text_pos_to_doc_index(section_end, segments)
                insert_req = {"insertText": {"location": {"index": insert_idx}, "text": new_line}}
                format_reqs = [
                    {"updateTextStyle": {"range": {"startIndex": insert_idx, "endIndex": insert_idx + len(new_line)}, "textStyle": {"bold": False}, "fields": "bold"}},
                    {"updateTextStyle": {"range": {"startIndex": insert_idx, "endIndex": insert_idx + len(pl_clean)}, "textStyle": {"bold": True}, "fields": "bold"}}
                ]
                jobs.append((insert_idx, [insert_req], format_reqs))

        if not jobs:
            return False

        jobs.sort(key=lambda x: x[0], reverse=True)
        for _, insert_reqs, format_reqs in jobs:
            if insert_reqs:
                google_docs.update_document(insert_reqs)
            if format_reqs:
                google_docs.update_document(format_reqs)
        return True
    except Exception as e:
        print(f"[Refresh] ERROR updating TL;DR: {e}")
        return False

def fetch_tickets_for_pls_from_release(release_date: str, target_pls: List[str]) -> Dict[str, List]:
    """Fetch tickets for specific PLs from the release ticket."""
    jira = JiraHandler()
    if not jira.test_connection():
        return {}

    release_summary = f"Release {release_date}"
    release_ticket = jira.find_release_ticket(release_summary)
    if not release_ticket:
        return {}

    linked_tickets = jira.get_linked_tickets(release_ticket.get("key"))
    if not linked_tickets:
        return {}

    target_clean = {clean_pl_name(pl).lower(): pl for pl in target_pls}
    by_pl = {}
    for ticket in linked_tickets:
        fix_version = ticket.get('fix_version', '')
        if not fix_version:
            continue
        pl_name, _ = extract_pl_from_fix_version(fix_version)
        pl_clean = clean_pl_name(pl_name).lower()
        if pl_clean in target_clean:
            original_pl = target_clean[pl_clean]
            by_pl.setdefault(original_pl, []).append(ticket)
    return by_pl


def extract_pl_from_fix_version(fix_version: str) -> Tuple[str, str]:
    """
    Extract PL name and release version from fix version string.

    Examples:
        "DSP Core PL1 2026: Release 5.0" -> ("DSP Core PL1 2026", "Release 5.0")
        "Developer Experience: Release 8.0" -> ("Developer Experience", "Release 8.0")
    """
    match = re.match(r'^(.+?):\s*(Release\s+\d+\.\d+)$', fix_version)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return fix_version, ""


def fetch_new_versions(existing_pls: List[str], release_date: str = None) -> Dict:
    """
    Fetch Jira tickets and identify new PLs not already processed.

    Args:
        existing_pls: List of PL names already in the release
        release_date: Release date string (auto-detects if None)

    Returns:
        Dictionary with new tickets grouped by PL
    """
    print(f"[Refresh] Fetching new versions from Jira...")

    # Get release date
    if release_date is None:
        release_date = format_release_date_for_jira()

    print(f"[Refresh] Release date: {release_date}")
    print(f"[Refresh] Existing PLs: {existing_pls}")

    try:
        jira = JiraHandler()
        if not jira.test_connection():
            print("[Refresh] ERROR: Could not connect to Jira")
            return {"error": "Could not connect to Jira", "new_tickets": []}

        # Prefer release ticket fix versions (more reliable than date matching)
        all_tickets = []
        release_key = None
        release_summary = f"Release {release_date}"
        release_ticket = jira.find_release_ticket(release_summary)
        if release_ticket:
            release_key = release_ticket.get("key")
            print(f"[Refresh] Found release ticket: {release_key}")
            all_tickets = jira.get_linked_tickets(release_key)
        else:
            print("[Refresh] Release ticket not found; falling back to date-based search")
            all_tickets = jira.get_all_tickets_for_release_date(release_date)

        if not all_tickets:
            print(f"[Refresh] No tickets found for {release_date}")
            return {"new_tickets": [], "new_pls": []}

        print(f"[Refresh] Found {len(all_tickets)} total tickets")

        # Clean existing PL names for comparison
        existing_pls_clean = [clean_pl_name(pl).lower() for pl in existing_pls]

        # Group tickets by PL and find new ones
        new_tickets_by_pl = {}
        all_pls_found = set()

        for ticket in all_tickets:
            fix_version = ticket.get('fix_version', '')
            if not fix_version:
                continue

            pl_name, release_ver = extract_pl_from_fix_version(fix_version)
            all_pls_found.add(pl_name)

            # Check if this PL is new
            pl_clean = clean_pl_name(pl_name).lower()
            is_new = pl_clean not in existing_pls_clean

            if is_new:
                if pl_name not in new_tickets_by_pl:
                    new_tickets_by_pl[pl_name] = []
                new_tickets_by_pl[pl_name].append(ticket)

        new_pls = list(new_tickets_by_pl.keys())
        print(f"[Refresh] All PLs found in Jira: {list(all_pls_found)}")
        print(f"[Refresh] New PLs to add: {new_pls}")

        return {
            "new_tickets": new_tickets_by_pl,
            "new_pls": new_pls,
            "all_pls_found": list(all_pls_found),
            "release_date": release_date,
            "all_tickets": all_tickets,
            "release_summary": release_summary,
            "release_key": release_key
        }

    except Exception as e:
        print(f"[Refresh] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e), "new_tickets": [], "new_pls": []}


def process_new_tickets(new_tickets_by_pl: Dict[str, List]) -> Dict:
    """
    Process new tickets to generate release notes content.

    This creates the data structures needed for Google Doc formatting:
    - tldr_by_pl: TL;DR summaries
    - body_by_pl: Body content
    - release_versions: Version strings
    - fix_version_urls: URLs to fix versions
    - epic_urls_by_pl: Epic URLs

    Args:
        new_tickets_by_pl: Dictionary mapping PL names to ticket lists

    Returns:
        Processed data dictionary
    """
    print(f"[Refresh] Processing {len(new_tickets_by_pl)} new PLs...")

    processed = {
        "product_lines": [],
        "tldr_by_pl": {},
        "body_by_pl": {},
        "release_versions": {},
        "fix_version_urls": {},
        "epic_urls_by_pl": {}
    }

    def _normalize_bug_fix_bullet(text: str) -> str:
        trimmed = text.strip()
        if not trimmed:
            return text
        if re.match(r'(?i)^fixed\b', trimmed):
            return trimmed
        if re.match(r'(?i)^fix\b', trimmed):
            return re.sub(r'(?i)^fix\b', 'Fixed', trimmed, count=1)
        return f"Fixed {trimmed[0].lower() + trimmed[1:] if trimmed else trimmed}"

    for pl_name, tickets in new_tickets_by_pl.items():
        if not tickets:
            continue

        processed["product_lines"].append(pl_name)

        # Get release version from first ticket
        first_ticket = tickets[0]
        fix_version = first_ticket.get('fix_version', '')
        _, release_ver = extract_pl_from_fix_version(fix_version)
        processed["release_versions"][pl_name] = release_ver or "Release 1.0"

        if first_ticket.get('fix_version_url'):
            processed["fix_version_urls"][pl_name] = first_ticket['fix_version_url']

        # Group tickets by epic
        epics = {}
        for ticket in tickets:
            epic_name = ticket.get('epic_name', 'Other')
            if not epic_name:
                epic_name = 'Other'

            if epic_name not in epics:
                epics[epic_name] = {
                    "tickets": [],
                    "url": ticket.get('epic_url', '')
                }
            epics[epic_name]["tickets"].append(ticket)

        # Store epic URLs
        epic_urls = {}
        for epic_name, epic_data in epics.items():
            if epic_data.get('url'):
                epic_urls[epic_name] = epic_data['url']
        processed["epic_urls_by_pl"][pl_name] = epic_urls

        # Generate TL;DR from ticket summaries (not epic names)
        tldr_items = []
        for ticket in tickets:
            summary = ticket.get("summary", "")
            if summary:
                tldr_items.append(summary.strip())
            if len(tldr_items) >= 3:
                break
        if tldr_items:
            tldr = "; ".join(tldr_items)
            if len(tickets) > 3:
                tldr += f" (+{len(tickets) - 3} more)"
        else:
            tldr = f"{len(tickets)} ticket(s) in this release"
        processed["tldr_by_pl"][pl_name] = tldr

        # Generate body content
        body_lines = []
        for epic_name, epic_data in epics.items():
            if epic_name == 'Other':
                continue

            body_lines.append(epic_name)
            if epic_name == "Bug Fixes":
                body_lines.append("Bug Fixes:")
            else:
                body_lines.append("Value Add:")

            # Combine ticket summaries
            summaries = []
            for ticket in epic_data["tickets"][:5]:  # Max 5 tickets per epic
                summary = ticket.get('summary', '')
                if summary:
                    summaries.append(summary)

            if summaries:
                for summary in summaries:
                    if epic_name == "Bug Fixes":
                        summary = _normalize_bug_fix_bullet(summary)
                    body_lines.append(f"● {summary}")

            # Add release type if available
            release_type = epic_data["tickets"][0].get('release_type')
            if release_type:
                body_lines.append(release_type)
            else:
                body_lines.append("General Availability")

            body_lines.append("")  # Blank line between epics

        processed["body_by_pl"][pl_name] = "\n".join(body_lines)

    return processed


def find_insertion_point_for_category(google_docs: GoogleDocsHandler, category: str) -> Optional[int]:
    """
    Find the insertion point for a new PL within its category section.

    Args:
        google_docs: GoogleDocsHandler instance
        category: Category name (e.g., "DSP", "Media")

    Returns:
        Document index for insertion, or None if category not found
    """
    try:
        content = google_docs.get_document_content()
        if not content:
            return None

        # Find the category header
        category_pattern = rf'------------------{re.escape(category)}------------------'
        match = re.search(category_pattern, content, re.IGNORECASE)

        if not match:
            print(f"[Refresh] Category '{category}' not found in document")
            return None

        # Find the end of this category section (next category header or separator)
        rest = content[match.end():]
        next_patterns = [
            r'\n------------------[^-]+------------------',  # Next category
            r'\n═{20,}',  # Separator
        ]

        end_pos = len(rest)
        for pattern in next_patterns:
            next_match = re.search(pattern, rest)
            if next_match and next_match.start() < end_pos:
                end_pos = next_match.start()

        # The insertion point is at the end of the category content
        # We need to convert text position to document index
        # For simplicity, return the text position - caller will handle conversion
        return match.end() + end_pos

    except Exception as e:
        print(f"[Refresh] Error finding insertion point: {e}")
        return None


def add_new_pls_to_google_doc(processed_data: Dict, release_date: str) -> bool:
    """
    Add new PL sections to an existing Google Doc release.

    This function inserts new PLs WITHOUT touching existing content:
    1. Adds new TL;DR entries
    2. Adds new PL body sections in the appropriate category

    Args:
        processed_data: Processed data for new PLs
        release_date: Release date string

    Returns:
        True if successful, False otherwise
    """
    print(f"[Refresh] Adding {len(processed_data.get('product_lines', []))} new PLs to Google Doc...")

    try:
        google_docs = GoogleDocsHandler()

        if not google_docs.authenticate():
            print("[Refresh] ERROR: Could not authenticate with Google")
            return False

        if not google_docs.test_connection():
            print("[Refresh] ERROR: Could not connect to Google Doc")
            return False

        # Get current document content
        content = google_docs.get_document_content()
        if not content:
            print("[Refresh] ERROR: Could not read document content")
            return False

        # Check if this release date exists in the document
        release_header = f"Daily Deployment Summary: {release_date}"
        if release_header not in content:
            print(f"[Refresh] Release '{release_date}' not found in document")
            print("[Refresh] Cannot add new PLs - run full release first")
            return False

        # For now, use a simpler approach: append new content at a specific location
        # Find the separator line before the next release (or end of document)
        separator_pattern = r'\n═{20,}\n'
        separator_match = re.search(separator_pattern, content)

        if not separator_match:
            print("[Refresh] Could not find insertion point (separator)")
            return False

        # Read document and build segments for index mapping
        doc = google_docs.service.documents().get(documentId=google_docs.document_id).execute()
        segments = _build_text_segments(doc)

        product_lines = processed_data.get('product_lines', [])
        if not product_lines:
            print("[Refresh] No new PLs to insert")
            return False

        # Insert TL;DR entries under Key Deployments
        tldr_by_pl = processed_data.get('tldr_by_pl', {})
        tldr_lines = []
        tldr_bold_ranges = []
        tldr_offset = 0

        for pl in get_ordered_pls(product_lines):
            if pl.lower() == "other":
                continue
            summary = tldr_by_pl.get(pl, "")
            if not summary:
                continue
            pl_clean = clean_pl_name(pl)
            line = f"{pl_clean} - {summary}\n"
            tldr_lines.append(line)
            tldr_bold_ranges.append((tldr_offset, tldr_offset + len(pl_clean)))
            tldr_offset += len(line)

        tldr_insert_index = None
        if tldr_lines:
            tldr_text = "".join(tldr_lines)
            key_header = "Key Deployments:"
            key_pos = content.find(key_header)
            if key_pos != -1:
                after_key = key_pos + len(key_header)
                rest = content[after_key:]
                next_header = re.search(r'\n------------------[^-]+------------------', rest)
                tldr_text_pos = after_key + (next_header.start() if next_header else len(rest))
                tldr_insert_index = _text_pos_to_doc_index(tldr_text_pos, segments)

        jobs = []

        if tldr_insert_index is not None:
            insert_reqs = [{
                "insertText": {
                    "location": {"index": tldr_insert_index},
                    "text": "".join(tldr_lines)
                }
            }]
            format_reqs = []
            for start, end in tldr_bold_ranges:
                format_reqs.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": tldr_insert_index + start,
                            "endIndex": tldr_insert_index + end
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold"
                    }
                })
            jobs.append((tldr_insert_index, insert_reqs, format_reqs))

        # Insert PL bodies in their category sections
        for pl in get_ordered_pls(product_lines)[::-1]:
            pl_clean = clean_pl_name(pl)
            release_ver = processed_data.get('release_versions', {}).get(pl, "Release 1.0")
            fix_version_url = processed_data.get('fix_version_urls', {}).get(pl, "")
            epic_urls = processed_data.get('epic_urls_by_pl', {}).get(pl, {})
            body_text = processed_data.get('body_by_pl', {}).get(pl, "")

            if not body_text:
                continue

            category = _get_pl_category(pl_clean)
            insert_text_pos = find_insertion_point_for_category(google_docs, category)
            if insert_text_pos is None:
                print(f"[Refresh] Category '{category}' not found; skipping {pl_clean}")
                continue
            insert_index = _text_pos_to_doc_index(insert_text_pos, segments)

            formatter = GoogleDocsFormatter()
            formatter.reset()

            # Leading newline to separate from previous PL
            formatter._insert_text("\n")

            # PL header with release version (hyperlinked)
            formatter._insert_text(f"{pl_clean}: ")
            ver_start = formatter.current_index
            formatter._insert_text(f"{release_ver}\n")
            ver_end = ver_start + len(release_ver)
            if fix_version_url:
                formatter._mark_link(ver_start, ver_end, fix_version_url)

            elements = formatter._parse_body_content(body_text, epic_urls, pl_clean, release_ver)
            for element in elements:
                elem_start = formatter.current_index
                if element["type"] == "bullet":
                    formatter._insert_text(f"    ● {element['text']}")
                else:
                    formatter._insert_text(element["text"])
                elem_end = formatter.current_index

                if element["type"] == "epic":
                    if element.get("bold"):
                        formatter._mark_bold(elem_start, elem_end - 1)
                    if element.get("url"):
                        formatter._mark_link(elem_start, elem_end - 1, element["url"])
                elif element["type"] in ("value_add_header", "bug_fixes_header"):
                    bold_start, bold_end = element.get("bold_range", (0, 0))
                    if bold_end > bold_start:
                        formatter._mark_bold(elem_start + bold_start, elem_start + bold_end)
                elif element["type"] == "status":
                    if element.get("color") == "green":
                        formatter._mark_green(elem_start, elem_end - 1)

            formatter._insert_text("\n")
            formatter._build_format_requests()

            # Offset requests to the document index
            offset = insert_index - 1
            body_insert_requests = []
            for req in formatter.insert_requests:
                new_req = json.loads(json.dumps(req))
                new_req["insertText"]["location"]["index"] += offset
                body_insert_requests.append(new_req)

            body_format_requests = []
            for req in formatter.format_requests:
                new_req = json.loads(json.dumps(req))
                if "updateTextStyle" in new_req:
                    new_req["updateTextStyle"]["range"]["startIndex"] += offset
                    new_req["updateTextStyle"]["range"]["endIndex"] += offset
                if "updateParagraphStyle" in new_req:
                    new_req["updateParagraphStyle"]["range"]["startIndex"] += offset
                    new_req["updateParagraphStyle"]["range"]["endIndex"] += offset
                body_format_requests.append(new_req)

            jobs.append((insert_index, body_insert_requests, body_format_requests))

        if not jobs:
            print("[Refresh] No new content generated for new PLs")
            return False

        # Execute jobs from higher index to lower index
        jobs.sort(key=lambda x: x[0], reverse=True)
        for _, insert_reqs, format_reqs in jobs:
            if insert_reqs:
                google_docs.update_document(insert_reqs)
            if format_reqs:
                google_docs.update_document(format_reqs)

        print(f"[Refresh] ✅ Added {len(processed_data.get('product_lines', []))} new PLs to Google Doc")
        return True

    except Exception as e:
        print(f"[Refresh] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def add_new_tickets_to_existing_pls(processed_data: Dict, release_date: str) -> bool:
    """
    Append new tickets to existing PL sections without overwriting content.
    """
    print(f"[Refresh] Appending new tickets to {len(processed_data.get('product_lines', []))} existing PLs...")
    try:
        google_docs = GoogleDocsHandler()
        if not google_docs.authenticate() or not google_docs.test_connection():
            print("[Refresh] ERROR: Could not connect to Google Doc")
            return False

        content = google_docs.get_document_content()
        if not content:
            print("[Refresh] ERROR: Could not read document content")
            return False

        release_header = f"Daily Deployment Summary: {release_date}"
        if release_header not in content:
            print(f"[Refresh] Release '{release_date}' not found in document")
            return False

        doc = google_docs.service.documents().get(documentId=google_docs.document_id).execute()
        segments = _build_text_segments(doc)

        jobs = []
        for pl in processed_data.get('product_lines', []):
            body_text = processed_data.get('body_by_pl', {}).get(pl, "")
            if not body_text:
                continue
            insert_text_pos = _find_pl_section_end(content, pl)
            if insert_text_pos is None:
                print(f"[Refresh] Could not locate section for {pl}; skipping")
                continue
            insert_index = _text_pos_to_doc_index(insert_text_pos, segments)

            formatter = GoogleDocsFormatter()
            formatter.reset()
            formatter._insert_text("\n")
            epic_urls = processed_data.get('epic_urls_by_pl', {}).get(pl, {})
            release_ver = processed_data.get('release_versions', {}).get(pl, "Release 1.0")
            elements = formatter._parse_body_content(body_text, epic_urls, clean_pl_name(pl), release_ver)
            for element in elements:
                elem_start = formatter.current_index
                if element["type"] == "bullet":
                    formatter._insert_text(f"    ● {element['text']}")
                else:
                    formatter._insert_text(element["text"])
                elem_end = formatter.current_index
                if element["type"] == "epic":
                    if element.get("bold"):
                        formatter._mark_bold(elem_start, elem_end - 1)
                    if element.get("url"):
                        formatter._mark_link(elem_start, elem_end - 1, element["url"])
                elif element["type"] in ("value_add_header", "bug_fixes_header"):
                    bold_start, bold_end = element.get("bold_range", (0, 0))
                    if bold_end > bold_start:
                        formatter._mark_bold(elem_start + bold_start, elem_start + bold_end)
                elif element["type"] == "status":
                    if element.get("color") == "green":
                        formatter._mark_green(elem_start, elem_end - 1)

            formatter._insert_text("\n")
            formatter._build_format_requests()

            offset = insert_index - 1
            body_insert_requests = []
            for req in formatter.insert_requests:
                new_req = json.loads(json.dumps(req))
                new_req["insertText"]["location"]["index"] += offset
                body_insert_requests.append(new_req)

            body_format_requests = []
            for req in formatter.format_requests:
                new_req = json.loads(json.dumps(req))
                if "updateTextStyle" in new_req:
                    new_req["updateTextStyle"]["range"]["startIndex"] += offset
                    new_req["updateTextStyle"]["range"]["endIndex"] += offset
                if "updateParagraphStyle" in new_req:
                    new_req["updateParagraphStyle"]["range"]["startIndex"] += offset
                    new_req["updateParagraphStyle"]["range"]["endIndex"] += offset
                body_format_requests.append(new_req)

            jobs.append((insert_index, body_insert_requests, body_format_requests))

        if not jobs:
            print("[Refresh] No new content generated for existing PLs")
            return False

        jobs.sort(key=lambda x: x[0], reverse=True)
        for _, insert_reqs, format_reqs in jobs:
            if insert_reqs:
                google_docs.update_document(insert_reqs)
            if format_reqs:
                google_docs.update_document(format_reqs)

        return True
    except Exception as e:
        print(f"[Refresh] ERROR appending existing PLs: {e}")
        return False


def generate_incremental_content(processed_data: Dict, release_date: str) -> str:
    """
    Generate plain text content for new PLs (to be inserted into existing doc).

    This generates content in the same format as the existing document.

    Args:
        processed_data: Processed data for new PLs
        release_date: Release date string

    Returns:
        Formatted text content
    """
    lines = []

    product_lines = processed_data.get('product_lines', [])
    tldr_by_pl = processed_data.get('tldr_by_pl', {})
    body_by_pl = processed_data.get('body_by_pl', {})
    release_versions = processed_data.get('release_versions', {})

    # Sort PLs according to preferred order
    sorted_pls = get_ordered_pls(product_lines)

    for pl in sorted_pls:
        pl_clean = clean_pl_name(pl)

        # Add category header if needed (simplified - just PL header)
        lines.append(f"\n------------------{pl_clean}------------------\n")

        # PL name and release version
        release_ver = release_versions.get(pl, "Release 1.0")
        lines.append(f"{pl_clean}: {release_ver}\n")

        # Body content
        body = body_by_pl.get(pl, '')
        if body:
            lines.append(body)
            lines.append("\n")

    return "\n".join(lines)


def update_processed_notes(new_processed: Dict, existing_processed: Optional[Dict] = None) -> bool:
    """
    Merge new processed data into existing processed_notes.json.

    Args:
        new_processed: New processed data to merge

    Returns:
        True if successful
    """
    try:
        # Load existing processed notes
        if existing_processed is not None:
            existing = existing_processed
        else:
            existing = {}
            if os.path.exists('processed_notes.json'):
                with open('processed_notes.json', 'r') as f:
                    existing = json.load(f)

        # Merge new PLs
        existing_pls = existing.get('product_lines', [])
        new_pls = new_processed.get('product_lines', [])

        for pl in new_pls:
            if pl not in existing_pls:
                existing_pls.append(pl)
        existing['product_lines'] = existing_pls

        # Merge other dictionaries (append body and TL;DR for existing PLs)
        for key in ['tldr_by_pl', 'body_by_pl', 'release_versions', 'fix_version_urls', 'epic_urls_by_pl']:
            if key not in existing:
                existing[key] = {}
            if key in ('body_by_pl', 'tldr_by_pl'):
                for pl, value in new_processed.get(key, {}).items():
                    if not value:
                        continue
                    if pl in existing[key] and existing[key][pl]:
                        sep = "\n" if key == 'body_by_pl' else "; "
                        existing[key][pl] = existing[key][pl].rstrip() + sep + value.lstrip()
                    else:
                        existing[key][pl] = value
            else:
                existing[key].update(new_processed.get(key, {}))

        # Save updated file
        with open('processed_notes.json', 'w') as f:
            json.dump(existing, f, indent=2)

        print(f"[Refresh] Updated processed_notes.json with {len(new_pls)} new PLs")
        return True

    except Exception as e:
        print(f"[Refresh] ERROR updating processed_notes.json: {e}")
        return False


def refresh_release_versions(message_ts: str = None) -> Dict:
    """
    Main refresh function - checks for new versions and updates everything.

    This is the function called by the Slack button handler.

    Args:
        message_ts: Slack message timestamp (for updating the message)

    Returns:
        Dictionary with results:
        - success: bool
        - new_pls: list of new PL names
        - message: status message
    """
    print("\n" + "=" * 60)
    print("  REFRESH: Checking for new Jira versions")
    print("=" * 60)

    try:
        # Load existing processed notes to get current PLs
        existing_pls = []
        release_date = None

        if os.path.exists('processed_notes.json'):
            with open('processed_notes.json', 'r') as f:
                existing = json.load(f)
                existing_pls = existing.get('product_lines', [])
                release_date = existing.get('release_summary', '').replace('Release ', '')

        if not existing_pls:
            return {
                "success": False,
                "new_pls": [],
                "message": "No existing release found. Run the full release workflow first."
            }

        print(f"[Refresh] Current PLs in release: {existing_pls}")

        # Fetch new versions from Jira
        fetch_result = fetch_new_versions(existing_pls, release_date)

        if fetch_result.get('error'):
            return {
                "success": False,
                "new_pls": [],
                "message": f"Error fetching from Jira: {fetch_result['error']}"
            }

        new_pls = fetch_result.get('new_pls', [])
        all_tickets = fetch_result.get('all_tickets', [])
        release_summary = fetch_result.get('release_summary')
        release_key = fetch_result.get('release_key')

        existing_ticket_keys = _load_existing_ticket_keys()
        delta_tickets = [t for t in all_tickets if t.get('key') and t.get('key') not in existing_ticket_keys]

        # Option B: force-add PLs missing from the Google Doc
        missing_pls = []
        try:
            google_docs = GoogleDocsHandler()
            if google_docs.authenticate() and google_docs.test_connection():
                content = google_docs.get_document_content()
                if content:
                    for pl in existing_pls:
                        if not _pl_present_in_doc(content, pl):
                            missing_pls.append(pl)
        except Exception as e:
            print(f"[Refresh] Warning: Could not check missing PLs in doc: {e}")

        if not new_pls and not missing_pls and not delta_tickets:
            all_found = fetch_result.get('all_pls_found', [])
            return {
                "success": True,
                "new_pls": [],
                "updated_existing_pls": [],
                "message": f"No new PLs found. Current PLs in Jira: {', '.join(all_found) if all_found else 'none'}"
            }

        if new_pls:
            print(f"[Refresh] Found {len(new_pls)} new PLs: {new_pls}")
        if missing_pls:
            print(f"[Refresh] Found {len(missing_pls)} PLs missing in doc: {missing_pls}")

        # Process new tickets (new PLs + missing PLs)
        new_tickets = fetch_result.get('new_tickets', {})
        # New tickets within existing PLs
        delta_existing = {}
        if delta_tickets:
            existing_pls_clean = {clean_pl_name(pl).lower(): pl for pl in existing_pls}
            for ticket in delta_tickets:
                fix_version = ticket.get('fix_version', '')
                if not fix_version:
                    continue
                pl_name, _ = extract_pl_from_fix_version(fix_version)
                pl_clean = clean_pl_name(pl_name).lower()
                if pl_clean in existing_pls_clean:
                    original_pl = existing_pls_clean[pl_clean]
                    delta_existing.setdefault(original_pl, []).append(ticket)
        if missing_pls:
            missing_tickets = fetch_tickets_for_pls_from_release(release_date, missing_pls)
            for pl, tickets in missing_tickets.items():
                if pl in new_tickets:
                    new_tickets[pl].extend(tickets)
                else:
                    new_tickets[pl] = tickets
            new_pls = list(set(new_pls + missing_pls))
        processed_data = process_new_tickets(new_tickets) if new_tickets else {"product_lines": []}
        processed_existing = process_new_tickets(delta_existing) if delta_existing else {"product_lines": []}

        # Update Google Doc with new PLs (preserves existing content)
        doc_success = False
        if processed_data.get('product_lines'):
            doc_success = add_new_pls_to_google_doc(processed_data, release_date)
        # Append new tickets to existing PLs
        existing_doc_success = False
        if processed_existing.get('product_lines'):
            existing_doc_success = add_new_tickets_to_existing_pls(processed_existing, release_date)

        # Update processed_notes.json
        existing_processed = {}
        if os.path.exists('processed_notes.json'):
            with open('processed_notes.json', 'r') as f:
                existing_processed = json.load(f)
        if processed_data.get('product_lines'):
            update_processed_notes(processed_data, existing_processed)
        if processed_existing.get('product_lines'):
            update_processed_notes(processed_existing, existing_processed)

        # Update TL;DR lines for existing PLs with new tickets
        if processed_existing.get('tldr_by_pl'):
            merged_tldr = {}
            for pl, new_tldr in processed_existing.get('tldr_by_pl', {}).items():
                if not new_tldr:
                    continue
                old_tldr = existing_processed.get('tldr_by_pl', {}).get(pl, '')
                if old_tldr:
                    merged_tldr[pl] = old_tldr.rstrip() + "; " + new_tldr.lstrip()
                else:
                    merged_tldr[pl] = new_tldr
            update_tldr_lines_for_existing_pls(merged_tldr, release_date)
        _merge_tickets_export(delta_tickets, release_summary, release_key)

        return {
            "success": True,
            "new_pls": new_pls,
            "updated_existing_pls": list(processed_existing.get('product_lines', [])),
            "processed_data": processed_data,
            "processed_existing": processed_existing,
            "doc_updated": doc_success or existing_doc_success,
            "message": f"Added {len(new_pls)} new PL(s): {', '.join(new_pls)}"
        }

    except Exception as e:
        print(f"[Refresh] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "new_pls": [],
            "message": f"Error during refresh: {str(e)}"
        }


if __name__ == "__main__":
    # Test the refresh functionality
    result = refresh_release_versions()
    print("\n" + "=" * 60)
    print("  REFRESH RESULT")
    print("=" * 60)
    print(f"  Success: {result.get('success')}")
    print(f"  New PLs: {result.get('new_pls')}")
    print(f"  Message: {result.get('message')}")
    print("=" * 60)
