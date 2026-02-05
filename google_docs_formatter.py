#!/usr/bin/env python3
"""
Google Docs Formatter - Interpreter Layer for Claude Output

This module acts as an interpreter between Claude-generated content and
Google Docs API. It parses the structured text output and converts it
into properly formatted Google Docs API requests.

The formatter handles:
- Section headers (dashed lines) -> Styled headers
- Bullet points (●) -> Formatted bullets
- Bold text (Value Add:, Bug Fixes:, PL names, Epic names)
- Colored text (General Availability, Feature Flag -> green)
- Hyperlinks (Epic URLs, Release version URLs)

Usage:
    from google_docs_formatter import GoogleDocsFormatter

    formatter = GoogleDocsFormatter()
    requests = formatter.format_release_notes(
        release_date="5th February 2026",
        tldr_by_pl={"DSP Core PL1": "summary..."},
        body_by_pl={"DSP Core PL1": "Epic Name\\nValue Add:\\n..."},
        product_lines=["DSP Core PL1"],
        release_versions={"DSP Core PL1": "Release 5.0"},
        fix_version_urls={"DSP Core PL1": "https://..."},
        epic_urls_by_pl={"DSP Core PL1": {"Epic Name": "https://..."}}
    )
"""

import re
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


# Color definitions (RGB values 0-1 scale for Google Docs API)
BLUE_COLOR = {"red": 0.06, "green": 0.36, "blue": 0.7}  # Link blue
GREEN_COLOR = {"red": 0.13, "green": 0.55, "blue": 0.13}  # Dark green for status tags
GRAY_COLOR = {"red": 0.5, "green": 0.5, "blue": 0.5}  # Gray for section headers


class GoogleDocsFormatter:
    """
    Formatter that converts Claude-generated release notes into
    Google Docs API requests with proper styling.
    """

    def __init__(self):
        """Initialize the formatter."""
        self.current_index = 1  # Google Docs starts at index 1
        self.insert_requests = []
        self.format_requests = []

        # Track formatting positions for batch application
        self.formatting_positions = {
            "bold": [],      # [(start, end), ...]
            "links": [],     # [(start, end, url), ...]
            "green": [],     # [(start, end), ...]
            "gray": [],      # [(start, end), ...]
            "heading": [],   # [(start, end, level), ...]
        }

    def reset(self):
        """Reset formatter state for a new document."""
        self.current_index = 1
        self.insert_requests = []
        self.format_requests = []
        self.formatting_positions = {
            "bold": [],
            "links": [],
            "green": [],
            "gray": [],
            "heading": [],
        }

    def _insert_text(self, text: str) -> int:
        """
        Add text insertion request and update index.

        Args:
            text: Text to insert

        Returns:
            Start index of inserted text
        """
        start_index = self.current_index
        self.insert_requests.append({
            "insertText": {
                "location": {"index": self.current_index},
                "text": text
            }
        })
        self.current_index += len(text)
        return start_index

    def _mark_bold(self, start: int, end: int):
        """Mark text range as bold."""
        self.formatting_positions["bold"].append((start, end))

    def _mark_link(self, start: int, end: int, url: str):
        """Mark text range as a hyperlink."""
        self.formatting_positions["links"].append((start, end, url))

    def _mark_green(self, start: int, end: int):
        """Mark text range as green (status tags)."""
        self.formatting_positions["green"].append((start, end))

    def _mark_gray(self, start: int, end: int):
        """Mark text range as gray."""
        self.formatting_positions["gray"].append((start, end))

    def _clean_pl_name(self, pl_name: str) -> str:
        """
        Clean PL name by removing year suffixes.

        Examples:
            "Developer Experience 2026" -> "Developer Experience"
            "DSP Core PL1" -> "DSP Core PL1"
        """
        return re.sub(r'\s+20\d{2}$', '', pl_name)

    def _get_pl_category(self, pl_name: str) -> str:
        """Determine the category header for a product line."""
        pl_lower = pl_name.lower()

        if 'media' in pl_lower:
            return "Media"
        elif 'audience' in pl_lower:
            return "Audiences"
        elif 'developer' in pl_lower:
            return "Developer Experience"
        elif 'data ingress' in pl_lower:
            return "Data Ingress"
        elif 'data governance' in pl_lower:
            return "Data Governance"
        elif 'helix' in pl_lower:
            return "Helix"
        elif 'dsp' in pl_lower:
            return "DSP"
        else:
            return pl_name

    def _find_epic_url(self, line_text: str, epic_urls: Dict[str, str]) -> str:
        """
        Find matching epic URL using flexible matching.

        Args:
            line_text: Text to match
            epic_urls: Dictionary of epic names to URLs

        Returns:
            URL if found, empty string otherwise
        """
        line_lower = line_text.lower().strip()

        # Direct match first
        if line_text in epic_urls:
            return epic_urls[line_text]

        # Case-insensitive match
        for epic_name, url in epic_urls.items():
            if epic_name.lower() == line_lower:
                return url

        # Partial match - check if most words match
        for epic_name, url in epic_urls.items():
            epic_lower = epic_name.lower()
            line_words = set(line_lower.split())
            epic_words = set(epic_lower.split())
            common_words = line_words & epic_words

            if len(epic_words) > 0:
                match_ratio = len(common_words) / len(epic_words)
                if match_ratio >= 0.7:
                    return url

        return ""

    def _parse_body_content(self, body_text: str, epic_urls: Dict[str, str],
                           pl_clean: str, release_ver: str) -> List[Dict]:
        """
        Parse Claude-generated body content and identify formatting elements.

        This is the core interpreter logic that understands the structure
        of Claude's output and identifies what needs formatting.

        The expected format is prose-style (not bullet points):

        Epic Name Here
        Value Add:
        Single descriptive prose sentence about what was accomplished.
        General Availability

        Args:
            body_text: Raw body text from Claude
            epic_urls: Dictionary of epic names to URLs
            pl_clean: Cleaned PL name
            release_ver: Release version string

        Returns:
            List of parsed elements with text and formatting info
        """
        elements = []
        lines = body_text.split('\n')

        # Filter out duplicate PL header lines
        pl_clean_lower = pl_clean.lower()
        release_ver_num = release_ver.replace("Release ", "").strip() if release_ver else ""

        filtered_lines = []
        found_content = False
        for line in lines:
            line_lower = line.strip().lower()
            # Skip lines that look like duplicate PL headers
            if pl_clean_lower in line_lower and 'release' in line_lower and release_ver_num and release_ver_num in line:
                continue
            # Skip leading empty lines
            if not found_content and not line.strip():
                continue
            found_content = True
            filtered_lines.append(line)

        # Track context - are we after a "Value Add:" or "Bug Fixes:" header?
        in_value_section = False

        for line in filtered_lines:
            stripped = line.strip()

            if not stripped:
                elements.append({"type": "blank", "text": "\n"})
                in_value_section = False
                continue

            # Check for Value Add header
            if stripped.lower().startswith('value add'):
                elements.append({
                    "type": "value_add_header",
                    "text": stripped + "\n",
                    "bold_range": (0, len("Value Add:"))
                })
                in_value_section = True
                continue

            # Check for Bug Fixes header
            if stripped.lower().startswith('bug fix'):
                elements.append({
                    "type": "bug_fixes_header",
                    "text": stripped + "\n",
                    "bold_range": (0, len("Bug Fixes:"))
                })
                in_value_section = True
                continue

            # Check for status tags
            if stripped == 'General Availability':
                elements.append({
                    "type": "status",
                    "text": stripped + "\n",
                    "color": "green"
                })
                in_value_section = False
                continue

            if stripped == 'Feature Flag':
                elements.append({
                    "type": "status",
                    "text": stripped + "\n",
                    "color": "green"
                })
                in_value_section = False
                continue

            # If we're in a value section, this is prose content
            if in_value_section:
                # This is the prose description - just regular text
                # Remove any accidental bullet characters
                clean_text = re.sub(r'^[●•\*\-]\s*', '', stripped)
                elements.append({
                    "type": "prose",
                    "text": clean_text + "\n"
                })
                continue

            # Otherwise, check if it's an epic name
            epic_url = self._find_epic_url(stripped, epic_urls)
            if epic_url:
                elements.append({
                    "type": "epic",
                    "text": stripped + "\n",
                    "url": epic_url,
                    "bold": True
                })
            else:
                # Could be an epic without URL, or other text
                # Check if it looks like an epic name (not too long, not ending with period)
                is_likely_epic = (
                    len(stripped) < 100 and
                    not stripped.endswith('.') and
                    not stripped.endswith(':') and
                    not stripped.startswith('http')
                )
                if is_likely_epic:
                    elements.append({
                        "type": "epic",
                        "text": stripped + "\n",
                        "bold": True
                    })
                else:
                    # Regular prose text
                    elements.append({
                        "type": "prose",
                        "text": stripped + "\n"
                    })

        return elements

    def format_release_notes(
        self,
        release_date: str,
        tldr_by_pl: Dict[str, str],
        body_by_pl: Dict[str, str],
        product_lines: List[str],
        release_versions: Dict[str, str],
        fix_version_urls: Dict[str, str],
        epic_urls_by_pl: Dict[str, Dict[str, str]]
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Format release notes into Google Docs API requests.

        This is the main entry point that converts all the Claude-generated
        content into properly formatted Google Docs API requests.

        Args:
            release_date: Date string (e.g., "5th February 2026")
            tldr_by_pl: TL;DR summaries per PL
            body_by_pl: Body content per PL
            product_lines: List of product line names
            release_versions: Release version per PL
            fix_version_urls: Release version URLs per PL
            epic_urls_by_pl: Epic URLs per PL

        Returns:
            Tuple of (insert_requests, format_requests)
        """
        self.reset()

        # === TITLE ===
        title = f"Daily Deployment Summary: {release_date}\n\n"
        title_start = self._insert_text(title)
        self._mark_bold(title_start, title_start + len(title.strip()))

        # === TL;DR SECTION ===
        tldr_header = "------------------TL;DR:------------------\n\n"
        tldr_header_start = self._insert_text(tldr_header)

        # Key Deployments header (bold)
        key_deploy_header = "Key Deployments:\n"
        key_deploy_start = self._insert_text(key_deploy_header)
        self._mark_bold(key_deploy_start, key_deploy_start + len("Key Deployments:"))

        # TL;DR items per PL (prose format, no bullets)
        for pl in product_lines:
            if pl not in tldr_by_pl:
                continue

            summary = tldr_by_pl[pl]
            pl_clean = self._clean_pl_name(pl)

            # Insert PL name (bold, with link if available) - NO bullet
            pl_start = self.current_index
            self._insert_text(pl_clean)
            pl_end = self.current_index
            self._mark_bold(pl_start, pl_end)

            # Check if there's a release version URL
            if pl in fix_version_urls:
                self._mark_link(pl_start, pl_end, fix_version_urls[pl])

            # Insert separator and summary
            rest_of_line = f" - {summary}\n"
            self._insert_text(rest_of_line)

        # Blank line after TL;DR
        self._insert_text("\n")

        # === BODY SECTIONS BY CATEGORY ===
        # Group PLs by category
        pl_by_category = defaultdict(list)
        for pl in product_lines:
            category = self._get_pl_category(pl)
            pl_by_category[category].append(pl)

        # Define category order
        category_order = [
            "Media", "Developer Experience", "Audiences", "Data Ingress",
            "Data Governance", "Helix", "DSP", "Other"
        ]

        # Process each category
        for category in category_order:
            if category not in pl_by_category:
                continue

            # Category header
            category_header = f"------------------{category}------------------\n\n"
            self._insert_text(category_header)

            # Process each PL in this category
            for pl in pl_by_category[category]:
                pl_clean = self._clean_pl_name(pl)

                # PL name and release version line
                pl_name_text = f"{pl_clean}: "
                self._insert_text(pl_name_text)

                # Release version (with link)
                release_ver = release_versions.get(pl, "Release 1.0")
                release_url = fix_version_urls.get(pl, "")
                ver_start = self.current_index
                ver_text = f"{release_ver}\n"
                self._insert_text(ver_text)
                ver_end = ver_start + len(release_ver)
                if release_url:
                    self._mark_link(ver_start, ver_end, release_url)

                # Get epic URLs for this PL
                epic_urls = epic_urls_by_pl.get(pl, {})

                # Parse and format body content
                if pl in body_by_pl:
                    body_text = body_by_pl[pl]
                    elements = self._parse_body_content(
                        body_text, epic_urls, pl_clean, release_ver
                    )

                    # Insert each element with appropriate formatting
                    for element in elements:
                        elem_start = self.current_index
                        self._insert_text(element["text"])
                        elem_end = self.current_index

                        if element["type"] == "epic":
                            if element.get("bold"):
                                # Bold the epic name (excluding newline)
                                self._mark_bold(elem_start, elem_end - 1)
                            if element.get("url"):
                                # Add hyperlink
                                self._mark_link(elem_start, elem_end - 1, element["url"])

                        elif element["type"] in ("value_add_header", "bug_fixes_header"):
                            # Bold the header portion
                            bold_start, bold_end = element.get("bold_range", (0, 0))
                            if bold_end > bold_start:
                                self._mark_bold(elem_start + bold_start, elem_start + bold_end)

                        elif element["type"] == "status":
                            # Green color for status tags
                            if element.get("color") == "green":
                                self._mark_green(elem_start, elem_end - 1)

                        # "prose" type has no special formatting - just regular text

                    # Add spacing after body
                    self._insert_text("\n")

                # Spacing between PLs
                self._insert_text("\n")

        # === SEPARATOR LINE ===
        separator = "\n" + "═" * 60 + "\n\n"
        self._insert_text(separator)

        # === BUILD FORMAT REQUESTS ===
        self._build_format_requests()

        return self.insert_requests, self.format_requests

    def _build_format_requests(self):
        """Build formatting requests from tracked positions."""

        # Bold formatting
        for start, end in self.formatting_positions["bold"]:
            if end > start:
                self.format_requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {"bold": True},
                        "fields": "bold"
                    }
                })

        # Link formatting (blue with no underline)
        for start, end, url in self.formatting_positions["links"]:
            if end > start and url:
                self.format_requests.append({
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

        # Green text (status tags)
        for start, end in self.formatting_positions["green"]:
            if end > start:
                self.format_requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {
                            "foregroundColor": {"color": {"rgbColor": GREEN_COLOR}}
                        },
                        "fields": "foregroundColor"
                    }
                })

        # Gray text (section headers)
        for start, end in self.formatting_positions["gray"]:
            if end > start:
                self.format_requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {
                            "foregroundColor": {"color": {"rgbColor": GRAY_COLOR}}
                        },
                        "fields": "foregroundColor"
                    }
                })


def format_for_google_docs(
    processed_data: Dict,
    release_date: str = None
) -> Tuple[List[Dict], List[Dict]]:
    """
    Convenience function to format processed notes for Google Docs.

    This is the main entry point to use from hybrid_step3_update_docs.py.

    Args:
        processed_data: Dictionary containing processed release notes
        release_date: Optional override for release date

    Returns:
        Tuple of (insert_requests, format_requests)
    """
    formatter = GoogleDocsFormatter()

    # Extract release date
    if release_date is None:
        release_date = processed_data.get("release_summary", "").replace("Release ", "")
        if not release_date:
            from datetime import datetime
            release_date = datetime.now().strftime("%d %B %Y")

    return formatter.format_release_notes(
        release_date=release_date,
        tldr_by_pl=processed_data.get("tldr_by_pl", {}),
        body_by_pl=processed_data.get("body_by_pl", {}),
        product_lines=processed_data.get("product_lines", []),
        release_versions=processed_data.get("release_versions", {}),
        fix_version_urls=processed_data.get("fix_version_urls", {}),
        epic_urls_by_pl=processed_data.get("epic_urls_by_pl", {})
    )


# Test the formatter
if __name__ == "__main__":
    # Sample test data
    test_data = {
        "release_summary": "5th February 2026",
        "product_lines": ["Media PL1", "Developer Experience", "DSP Core PL1"],
        "tldr_by_pl": {
            "Media PL1": "InventoryTier dimension now visible in Reporting for seats with enabled priority tiers; Open Auction enablement flag added for Deal IDs with new Negotiated Bid Floor field for internal auction dynamics",
            "Developer Experience": "Saarthi AI code reviewer integrated into di-agentic-service and common-graphql repos; Airflow upgrade compatibility with logical_date parameter support across 5 services",
            "DSP Core PL1": "Forecasting improvements with frequency cap calculation logic moved to API side, returning minimum FCAP per day with timeframe for consistent results"
        },
        "body_by_pl": {
            "Media PL1": """Inventory Priority Tiers - Reporting
Value Add:
InventoryTier dimension is now available in Reporting for seats that have enabled priority tiers, providing visibility into inventory tier performance.
General Availability

Ops UI Enhancements
Value Add:
OA Enablement Flag has been added for Deal IDs along with the Negotiated Bid Floor Value, enhancing deal management capabilities in Ops UI.
General Availability""",
            "Developer Experience": """Migration of data pipelines from spring batch to airflow
Value Add:
REST API changes have been evaluated and prepared for Airflow upgrade across planner-service, patient-planner-service, event-consumer-service, di-match-service, and account-manager-service.
General Availability

Saarthi Code Reviewer integration across major repositories
Value Add:
Saarthi AI code reviewer is now integrated into di-agentic-service and common-graphql repositories, enabling automated code review across more codebases.
General Availability""",
            "DSP Core PL1": """Forecasting
Value Add:
FCAP calculation logic has been moved to the API side, improving forecasting performance and maintainability.
General Availability"""
        },
        "release_versions": {
            "Media PL1": "Release 5.0",
            "Developer Experience": "Release 8.0",
            "DSP Core PL1": "Release 4.0"
        },
        "fix_version_urls": {
            "Media PL1": "https://jira.example.com/media-pl1-release-5",
            "Developer Experience": "https://jira.example.com/dev-exp-release-8",
            "DSP Core PL1": "https://jira.example.com/dsp-core-pl1-release-4"
        },
        "epic_urls_by_pl": {
            "Media PL1": {
                "Inventory Priority Tiers - Reporting": "https://jira.example.com/epic/inventory",
                "Ops UI Enhancements": "https://jira.example.com/epic/ops-ui"
            },
            "Developer Experience": {
                "Migration of data pipelines from spring batch to airflow": "https://jira.example.com/epic/airflow",
                "Saarthi Code Reviewer integration across major repositories": "https://jira.example.com/epic/saarthi"
            },
            "DSP Core PL1": {
                "Forecasting": "https://jira.example.com/epic/forecasting"
            }
        }
    }

    insert_reqs, format_reqs = format_for_google_docs(test_data)

    print(f"Generated {len(insert_reqs)} insert requests")
    print(f"Generated {len(format_reqs)} format requests")

    # Preview the text content
    print("\n=== Preview of generated text ===")
    full_text = ""
    for req in insert_reqs:
        if "insertText" in req:
            full_text += req["insertText"]["text"]
    print(full_text)

    print("\n=== Format request summary ===")
    bold_count = sum(1 for r in format_reqs if r.get("updateTextStyle", {}).get("textStyle", {}).get("bold"))
    link_count = sum(1 for r in format_reqs if r.get("updateTextStyle", {}).get("textStyle", {}).get("link"))
    color_count = sum(1 for r in format_reqs if r.get("updateTextStyle", {}).get("textStyle", {}).get("foregroundColor") and not r.get("updateTextStyle", {}).get("textStyle", {}).get("link"))

    print(f"  Bold: {bold_count}")
    print(f"  Links: {link_count}")
    print(f"  Colors: {color_count}")
