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
import json
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


# Color definitions (RGB values 0-1 scale for Google Docs API)
BLUE_COLOR = {"red": 0.06, "green": 0.36, "blue": 0.7}  # Link blue - also used for "General Availability" label
GREEN_COLOR = {"red": 0.13, "green": 0.55, "blue": 0.13}  # Dark green for "Feature Flag" and epic names
GRAY_COLOR = {"red": 0.5, "green": 0.5, "blue": 0.5}  # Gray for section headers

# Product Line order - alphabetical by category, then by PL number within category
PRODUCT_LINE_ORDER = [
    # Audiences PLs
    "Audiences PL1",
    "Audiences PL2",
    "Audiences",
    # Data Governance
    "Data Governance",
    # Data Ingress
    "Data Ingress",
    "Data Ingress 2026",
    # Developer Experience
    "Developer Experience",
    "Developer Experience 2026",
    # DSP Core PLs
    "DSP Core PL1",
    "DSP Core PL2",
    "DSP Core PL3",
    "DSP Core PL4",
    "DSP Core PL5",
    "DSP PL1",
    "DSP PL2",
    "DSP PL3",
    "DSP",
    # Helix PLs
    "Helix PL1",
    "Helix PL2",
    "Helix PL3",
    "Helix",
    # Media PLs
    "Media PL1",
    "Media PL2",
    "Media",
    "Other"
]


def get_ordered_pls(pl_list: list) -> list:
    """Sort product lines according to PRODUCT_LINE_ORDER.

    Handles year variants by matching base PL name (e.g., "Media PL1 2026" matches "Media PL1").
    """
    ordered = []

    def get_base_pl_name(pl_name: str) -> str:
        """Remove year suffix from PL name for matching."""
        return re.sub(r'\s+20\d{2}$', '', pl_name)

    # First add PLs that match the preferred order (considering year variants)
    for preferred_pl in PRODUCT_LINE_ORDER:
        for pl in pl_list:
            if pl in ordered:
                continue
            # Match exact or base name (without year)
            if pl == preferred_pl or get_base_pl_name(pl) == preferred_pl:
                ordered.append(pl)

    # Then add any PLs not matched (at the end)
    for pl in pl_list:
        if pl not in ordered:
            ordered.append(pl)
    return ordered


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
            "blue": [],      # [(start, end), ...] - for General Availability status
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
            "blue": [],
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
        """Mark text range as green (Feature Flag status and epic names)."""
        self.formatting_positions["green"].append((start, end))

    def _mark_blue(self, start: int, end: int):
        """Mark text range as blue (General Availability status)."""
        self.formatting_positions["blue"].append((start, end))

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

    def _join_pl_names(self, pl_names: List[str]) -> str:
        """Join PL names using commas and 'and'."""
        if not pl_names:
            return ""
        if len(pl_names) == 1:
            return pl_names[0]
        if len(pl_names) == 2:
            return f"{pl_names[0]} and {pl_names[1]}"
        return f"{', '.join(pl_names[:-1])} and {pl_names[-1]}"

    def _normalize_bug_fix_bullet(self, text: str) -> str:
        """Ensure bug fix bullets start with 'Fixed'."""
        trimmed = text.strip()
        if not trimmed:
            return text
        if re.match(r'(?i)^fixed\b', trimmed):
            return trimmed
        if re.match(r'(?i)^fix\b', trimmed):
            return re.sub(r'(?i)^fix\b', 'Fixed', trimmed, count=1)
        return f"Fixed {trimmed[0].lower() + trimmed[1:] if trimmed else trimmed}"

    def _extract_inline_tag(self, text: str) -> Tuple[str, str]:
        """
        Extract inline availability tag [GA] or [FF: flag_name] from the end of a bullet.

        Claude appends these tags directly to bullet lines, e.g.:
          "Users can now filter by date range [GA]"
          "Dark mode is behind a feature flag [FF: dark-mode-beta]"

        Returns:
            Tuple of (clean_text, tag) where:
              - clean_text is the bullet without the tag
              - tag is "[GA]", "[FF: flag_name]", or "" if none found
        """
        match = re.search(r'\s*(\[(?:GA|FF:[^\]]*)\])\s*$', text, re.IGNORECASE)
        if match:
            tag = match.group(1).strip()
            clean = text[:match.start()].strip()
            return clean, tag
        return text.strip(), ""

    def _parse_body_sections(self, body_text: str) -> List[Dict]:
        """
        Parse body text into epic sections with value-adds, bugs, and availability.

        Supports two input formats from Claude:

        Markdown format (new architecture):
            #### [Epic Name](url)
            **Value Add**:
            - Bullet text [GA]
            - Another bullet [FF: flag_name]
            **Bug Fixes**:
            - Fixed something specific

        Plain text format (legacy/fallback):
            Epic Name
            Value Add:
            * Bullet text
            General Availability

        Inline [GA] / [FF: name] tags on bullets are extracted and stored
        in value_add_tags (parallel to value_add) for green coloring.
        """
        sections: List[Dict] = []
        current = None
        mode = None  # "value" or "bug"

        def start_section(title: str, url: str = ""):
            nonlocal current, mode
            if current:
                sections.append(current)
            current = {
                "epic": title,
                "epic_url": url,
                "value_add": [],
                "value_add_tags": [],  # parallel to value_add; each entry is "[GA]", "[FF: name]", or ""
                "bug_fixes": [],
                "availability": ""
            }
            mode = None

        if not body_text:
            return sections

        for raw in body_text.split("\n"):
            stripped = raw.strip()
            if not stripped:
                continue

            # ── Markdown epic heading: #### [Epic Name](url) ──────────────────
            if stripped.startswith("#### "):
                inner = stripped[5:].strip()
                link_match = re.match(r'^\[([^\]]+)\]\(([^)]*)\)$', inner)
                if link_match:
                    epic_name = link_match.group(1).strip()
                    epic_url = link_match.group(2).strip()
                else:
                    # Heading without link — strip bold markers
                    epic_name = re.sub(r'\*\*([^*]+)\*\*', r'\1', inner).strip()
                    epic_url = ""
                start_section(epic_name, epic_url)
                continue

            # ── Strip bold markers for header detection (Value Add, Bug Fixes) ──
            line = re.sub(r'\*\*([^*]+)\*\*', r'\1', stripped)
            lower = line.lower()

            # ── Plain markdown link on its own line: [Epic](url) ──────────────
            # Only treat as epic heading when not inside a bullet section
            if mode is None:
                plain_link = re.match(r'^\[([^\]]+)\]\(([^)]*)\)$', line)
                if plain_link:
                    start_section(plain_link.group(1).strip(), plain_link.group(2).strip())
                    continue

            # ── Section mode transitions ───────────────────────────────────────
            if lower.startswith("value add"):
                mode = "value"
                continue
            if lower.startswith("bug fix"):
                mode = "bug"
                continue

            # Legacy standalone availability line (plain-text format)
            if line in ("General Availability", "Feature Flag"):
                if current:
                    current["availability"] = line
                mode = None
                continue

            # ── No active section: try to start a new epic ────────────────────
            if mode is None:
                # Skip separator lines (---, ===, ***) and known non-epic labels
                is_separator = bool(re.match(r'^[-=*_]{2,}$', stripped))
                is_skip_label = lower.strip() in {
                    "uncategorized", "general enhancements", "other",
                    "bug fixes", "bug fix"
                }
                if not is_separator and not is_skip_label:
                    # Strip any inline links from plain epic heading
                    clean_title = re.sub(r'\[([^\[\]]+)\]\([^)]*\)', r'\1', line)
                    clean_title = re.sub(r'\[(\[[^\]]*\][^\[\]]*)\]\([^)]*\)', r'\1', clean_title)
                    start_section(clean_title)
                continue

            # ── Bullet handling ───────────────────────────────────────────────
            clean = re.sub(r'^[●•\*\-]\s*', '', line).strip()
            # Strip markdown links from within bullet text (keep [GA]/[FF:] tags intact)
            clean = re.sub(r'\[([^\[\]]+)\]\([^)]*\)', r'\1', clean)
            if not clean:
                continue
            if current is None:
                start_section("Other")

            if mode == "value":
                clean_text, tag = self._extract_inline_tag(clean)
                current["value_add"].append(clean_text)
                current["value_add_tags"].append(tag)
            elif mode == "bug":
                current["bug_fixes"].append(clean)

        if current:
            sections.append(current)

        # ── Deduplicate entries within each section ───────────────────────────
        for section in sections:
            # Dedup value_add while keeping the first tag for each unique item
            seen_va: dict = {}
            for text, tag in zip(section["value_add"], section["value_add_tags"]):
                if text not in seen_va:
                    seen_va[text] = tag
            section["value_add"] = list(seen_va.keys())
            section["value_add_tags"] = list(seen_va.values())

            section["bug_fixes"] = list(dict.fromkeys(section["bug_fixes"]))

        # ── Merge duplicate epic sections ─────────────────────────────────────
        merged: dict = {}
        order: list = []
        for section in sections:
            epic = section["epic"]
            if epic in merged:
                merged[epic]["value_add"].extend(section["value_add"])
                merged[epic]["value_add_tags"].extend(section["value_add_tags"])
                merged[epic]["bug_fixes"].extend(section["bug_fixes"])
                if section["availability"] and not merged[epic]["availability"]:
                    merged[epic]["availability"] = section["availability"]
                if section["epic_url"] and not merged[epic]["epic_url"]:
                    merged[epic]["epic_url"] = section["epic_url"]
            else:
                merged[epic] = section
                order.append(epic)

        # Deduplicate again after merging
        result = []
        for epic in order:
            section = merged[epic]
            seen_va = {}
            for text, tag in zip(section["value_add"], section["value_add_tags"]):
                if text not in seen_va:
                    seen_va[text] = tag
            section["value_add"] = list(seen_va.keys())
            section["value_add_tags"] = list(seen_va.values())
            section["bug_fixes"] = list(dict.fromkeys(section["bug_fixes"]))
            result.append(section)

        return result

    def _extract_value_add_summaries(self, body_text: str) -> Dict[str, str]:
        """Extract first value-add sentence per epic from Claude body text."""
        summaries: Dict[str, str] = {}
        if not body_text:
            return summaries
        lines = body_text.split('\n')
        current_epic = None
        in_value = False
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith("#### "):
                line = line[5:].strip()
                lower = line.lower()
            if lower.startswith("value add"):
                in_value = True
                continue
            if lower.startswith("bug fix"):
                in_value = False
                continue
            if lower in ("general availability", "feature flag"):
                continue
            if not in_value:
                # treat as epic heading
                if not line.startswith(("●", "•", "-", "*")):
                    current_epic = line
                continue
            # Capture the first value-add line under this epic
            if current_epic and current_epic not in summaries:
                clean = re.sub(r'^[●•\*\-]\s*', '', line).strip()
                if clean:
                    summaries[current_epic] = clean
        return summaries

    def _availability_tag(self, ticket: Dict) -> str:
        """Return availability tag string for a ticket."""
        labels = ticket.get("labels") or []
        release_type = (ticket.get("release_type") or "").strip().lower()

        for label in labels:
            label_lower = label.lower()
            if label_lower.startswith(("ff:", "feature_flag:", "feature-flag:", "featureflag:")):
                name = label.split(":", 1)[1].strip()
                if name:
                    return f"[FF: {name}]"
            if label_lower.startswith(("ff-", "feature-flag-")):
                name = label.split("-", 1)[1].strip()
                if name:
                    return f"[FF: {name}]"

        if release_type in ("ga", "general availability"):
            return "[GA]"
        if "feature flag" in release_type or release_type == "ff":
            return "[FF]"

        for label in labels:
            label_lower = label.lower()
            if label_lower in ("ga", "general_availability") or "general availability" in label_lower:
                return "[GA]"
            if label_lower in ("ff", "feature_flag", "featureflag") or "feature flag" in label_lower:
                return "[FF]"

        issue_type = (ticket.get("issue_type") or "").lower()
        if issue_type in ("story", "task"):
            return "[GA]"

        return ""

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

        Uses bidirectional matching to auto-detect epic names even when:
        - Body text has a shortened version of the epic name
        - Epic name has extra words compared to body text
        - Case differences exist

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

        # Check if text contains epic name or vice versa (substring matching)
        for epic_name, url in epic_urls.items():
            if epic_name.lower() in line_lower or line_lower in epic_name.lower():
                return url

        # Bidirectional partial word match - check if most words match in EITHER direction
        # This catches cases where body text is a shortened version of the epic name
        for epic_name, url in epic_urls.items():
            epic_lower = epic_name.lower()
            line_words = set(line_lower.split())
            epic_words = set(epic_lower.split())
            common_words = line_words & epic_words

            if len(epic_words) > 0 and len(line_words) > 0:
                # Forward match: what % of epic words appear in text
                forward_ratio = len(common_words) / len(epic_words)
                # Reverse match: what % of text words appear in epic
                reverse_ratio = len(common_words) / len(line_words)

                # Match if either direction passes 70% threshold
                # This handles shortened epic names in body text
                if forward_ratio >= 0.7 or reverse_ratio >= 0.7:
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

        # Safety net: strip any markdown that Claude may have emitted
        # Pass 1: Standard links [text](url) where text has no brackets
        body_text = re.sub(r'\[([^\[\]]+)\]\([^)]*\)', r'\1', body_text)
        # Pass 2: Links with inner brackets [[text] rest](url)
        body_text = re.sub(r'\[(\[[^\]]*\][^\[\]]*)\]\([^)]*\)', r'\1', body_text)
        body_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', body_text)
        body_text = re.sub(r'^####\s+', '', body_text, flags=re.MULTILINE)

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
        last_was_blank = False  # Track consecutive blanks

        for line in filtered_lines:
            stripped = line.strip()

            if not stripped:
                # Skip consecutive blank lines - only allow one blank between sections
                if last_was_blank:
                    continue
                elements.append({"type": "blank", "text": "\n"})
                in_value_section = False
                last_was_blank = True
                continue

            last_was_blank = False

            # Check for Value Add header
            if stripped.lower().startswith('value add'):
                # Calculate the actual bold range based on the text
                # Find where the header part ends (at colon or end of "Value Add")
                header_text = stripped
                if ':' in stripped:
                    bold_end = stripped.index(':') + 1  # Include the colon
                else:
                    bold_end = len("Value Add")  # Just "Value Add" without colon
                elements.append({
                    "type": "value_add_header",
                    "text": header_text + "\n",
                    "bold_range": (0, bold_end)
                })
                in_value_section = True
                continue

            # Check for Bug Fixes header
            if stripped.lower().startswith('bug fix'):
                # Calculate the actual bold range based on the text
                # Find where the header part ends (at colon or end of header)
                header_text = stripped
                if ':' in stripped:
                    bold_end = stripped.index(':') + 1  # Include the colon
                else:
                    # Handle "Bug Fix" or "Bug Fixes" without colon
                    if stripped.lower().startswith('bug fixes'):
                        bold_end = len("Bug Fixes")
                    else:
                        bold_end = len("Bug Fix")
                elements.append({
                    "type": "bug_fixes_header",
                    "text": header_text + "\n",
                    "bold_range": (0, bold_end)
                })
                in_value_section = True
                continue

            # Check for status tags
            if stripped == 'General Availability':
                elements.append({
                    "type": "status",
                    "text": stripped + "\n",
                    "color": "green"  # General Availability uses green color
                })
                in_value_section = False
                continue

            if stripped == 'Feature Flag':
                elements.append({
                    "type": "status",
                    "text": stripped + "\n",
                    "color": "green"  # Feature Flag uses green color
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
        epic_urls_by_pl: Dict[str, Dict[str, str]],
        grouped_data: Optional[Dict[str, Dict[str, List[str]]]] = None,
        ticket_map: Optional[Dict[str, Dict]] = None
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

        # Key Deployments header (PL names appear in the bullets below, not on this line)
        sorted_product_lines = get_ordered_pls(product_lines)
        key_deploy_line = "Key Deployments:\n"
        key_deploy_start = self._insert_text(key_deploy_line)
        self._mark_bold(key_deploy_start, key_deploy_start + len("Key Deployments:"))

        # TL;DR items per PL (sub-bullets), deduplicated by cleaned PL name
        seen_pl_names = set()
        for pl in sorted_product_lines:
            if pl not in tldr_by_pl or pl.lower() == "other":
                continue
            pl_clean = self._clean_pl_name(pl)
            # Skip duplicate PL names (e.g., "Developer Experience" and "Developer Experience 2026")
            if pl_clean in seen_pl_names:
                continue
            seen_pl_names.add(pl_clean)
            summary = tldr_by_pl[pl]
            if summary:
                summary = summary[0].upper() + summary[1:] if len(summary) > 1 else summary.upper()
            bullet_text = f"• {pl_clean} - {summary}\n"
            bullet_start = self.current_index
            self._insert_text(bullet_text)
            pl_start = bullet_start + len("• ")
            pl_end = pl_start + len(pl_clean)
            self._mark_bold(pl_start, pl_end)

        # Blank line after TL;DR
        self._insert_text("\n")

        # === BODY SECTIONS BY CATEGORY ===
        # Group PLs by category, skip "Other" as it's for unclassified tickets
        pl_by_category = defaultdict(list)
        for pl in product_lines:
            if pl.lower() == "other":
                continue  # Skip "Other" category
            category = self._get_pl_category(pl)
            pl_by_category[category].append(pl)

        # Define category order - alphabetical
        category_order = [
            "Audiences", "Data Governance", "Data Ingress", "Developer Experience",
            "DSP", "Helix", "Media"
        ]

        # Process each category
        for category in category_order:
            if category not in pl_by_category:
                continue

            # Category header
            category_header = f"------------------{category}------------------\n\n"
            self._insert_text(category_header)

            # Sort PLs within this category according to PRODUCT_LINE_ORDER
            sorted_category_pls = get_ordered_pls(pl_by_category[category])

            # Process each PL in this category
            for pl in sorted_category_pls:
                # For body section headers, preserve the year (e.g., "Media PL1 2026")
                # Only clean for display in TL;DR
                pl_display = pl  # Keep the full PL name with year for body sections

                # PL name and release version line
                pl_name_text = f"{pl_display}: "
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

                # Parse and format body content from Claude output
                body_text = body_by_pl.get(pl, "")
                sections = self._parse_body_sections(body_text)

                if sections:
                    for section in sections:
                        epic_title = section["epic"]
                        has_value_add = bool(section["value_add"])
                        has_bugs = bool(section["bug_fixes"])

                        # Known labels that should never appear as epic section titles
                        _SUPPRESS_TITLES = {
                            "uncategorized", "general enhancements", "other",
                            "bug fixes", "bug fix", "---", "===",
                        }
                        # Render the epic title only when there are Value Add bullets
                        # AND the title is not a known non-epic label.
                        if has_value_add and epic_title.lower().strip() not in _SUPPRESS_TITLES:
                            # Use URL embedded in the parsed markdown (#### [Epic](url))
                            # if available; otherwise fall back to the epic_urls_by_pl dict.
                            epic_url = section.get("epic_url") or (
                                self._find_epic_url(epic_title, epic_urls) if epic_urls else ""
                            )
                            epic_start = self.current_index
                            self._insert_text(f"{epic_title}\n")
                            epic_end = self.current_index
                            self._mark_bold(epic_start, epic_end - 1)
                            if epic_url:
                                self._mark_link(epic_start, epic_end - 1, epic_url)

                            value_start = self.current_index
                            self._insert_text("Value Add:\n")
                            self._mark_bold(value_start, value_start + len("Value Add:"))

                            # Render value-add bullets, coloring any inline [GA]/[FF:] tags green
                            value_add_tags = section.get("value_add_tags", [])
                            for i, item in enumerate(section["value_add"]):
                                tag = value_add_tags[i] if i < len(value_add_tags) else ""
                                if tag:
                                    bullet_text = f"• {item} {tag}\n"
                                    bullet_start = self.current_index
                                    self._insert_text(bullet_text)
                                    # Mark the inline tag green
                                    tag_offset = len(f"• {item} ")
                                    self._mark_green(
                                        bullet_start + tag_offset,
                                        bullet_start + tag_offset + len(tag)
                                    )
                                else:
                                    self._insert_text(f"• {item}\n")

                            if section["availability"]:
                                avail_start = self.current_index
                                self._insert_text(f"{section['availability']}\n")
                                self._mark_green(avail_start, self.current_index - 1)

                        if has_bugs:
                            bug_start = self.current_index
                            self._insert_text("Bug Fixes:\n")
                            self._mark_bold(bug_start, bug_start + len("Bug Fixes:"))
                            for item in section["bug_fixes"]:
                                bug_text = self._normalize_bug_fix_bullet(item)
                                self._insert_text(f"• {bug_text}\n")

                        if has_value_add or has_bugs:
                            self._insert_text("\n")
                elif body_text:
                    # Fallback to existing Claude body parsing
                    elements = self._parse_body_content(
                        body_text, epic_urls, self._clean_pl_name(pl), release_ver
                    )
                    for element in elements:
                        elem_start = self.current_index
                        self._insert_text(element["text"])
                        elem_end = self.current_index
                        if element["type"] == "epic":
                            if element.get("bold"):
                                self._mark_bold(elem_start, elem_end - 1)
                            if element.get("url"):
                                self._mark_link(elem_start, elem_end - 1, element["url"])
                        elif element["type"] in ("value_add_header", "bug_fixes_header"):
                            bold_start, bold_end = element.get("bold_range", (0, 0))
                            if bold_end > bold_start:
                                self._mark_bold(elem_start + bold_start, elem_start + bold_end)
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

        # First, reset ALL text formatting to defaults (black color, NOT bold)
        # This is critical because Google Docs API causes inserted text to inherit
        # formatting from preceding characters. Without this reset, text inserted
        # after bold text would also be bold.
        if self.current_index > 1:
            self.format_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": 1, "endIndex": self.current_index},
                    "textStyle": {
                        "foregroundColor": {"color": {"rgbColor": {"red": 0.0, "green": 0.0, "blue": 0.0}}},
                        "bold": False
                    },
                    "fields": "foregroundColor,bold"
                }
            })

        # Create a set of link ranges for quick lookup
        link_ranges = {(start, end): url for start, end, url in self.formatting_positions["links"] if end > start and url}

        # Bold formatting - handle separately based on whether range also has a link
        for start, end in self.formatting_positions["bold"]:
            if end > start:
                if (start, end) in link_ranges:
                    # This range has both bold and link - apply together to ensure both work
                    url = link_ranges[(start, end)]
                    self.format_requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": start, "endIndex": end},
                            "textStyle": {
                                "bold": True,
                                "link": {"url": url},
                                "foregroundColor": {"color": {"rgbColor": BLUE_COLOR}},
                                "underline": False
                            },
                            "fields": "bold,link,foregroundColor,underline"
                        }
                    })
                    # Remove from link_ranges so we don't apply it again
                    del link_ranges[(start, end)]
                else:
                    # Bold only (no link)
                    self.format_requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": start, "endIndex": end},
                            "textStyle": {"bold": True},
                            "fields": "bold"
                        }
                    })

        # Link formatting for remaining links (those without bold)
        for (start, end), url in link_ranges.items():
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

        # Green text (Feature Flag status tags and epic names)
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

        # Blue text (General Availability status tags)
        for start, end in self.formatting_positions["blue"]:
            if end > start:
                self.format_requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {
                            "foregroundColor": {"color": {"rgbColor": BLUE_COLOR}}
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
