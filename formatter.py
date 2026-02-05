"""
Release Notes Formatter for Release Automation PoC

This module handles formatting of release notes:
- Grouping tickets by Product Line and Epic
- Generating TL;DR summary
- Creating formatted text for Google Docs
- Extracting value-add bullets from descriptions
- LLM-powered consolidation for polished prose
"""

from typing import Dict, List, Any
from collections import defaultdict
from datetime import datetime
import re
import os

# Try to import anthropic for LLM consolidation
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    print("[Formatter] Warning: anthropic package not installed. LLM consolidation disabled.")


# Product Line mapping based on components or labels
PRODUCT_LINE_MAPPING = {
    # DSP Product Lines
    "DSP": "DSP",
    "DSP PL1": "DSP PL1",
    "DSP PL2": "DSP PL2",
    "DSP PL3": "DSP PL3",
    # Audiences
    "Audiences": "Audiences",
    "Audiences PL1": "Audiences PL1",
    "Audience": "Audiences",
    # Media
    "Media": "Media",
    "Media PL1": "Media PL1",
    # Helix
    "Helix": "Helix",
    "Helix PL3": "Helix PL3",
    # Developer Experience
    "Developer Experience": "Developer Experience",
    "DevEx": "Developer Experience",
    "DX": "Developer Experience",
    # Data Governance
    "Data Governance": "Data Governance",
    "DG": "Data Governance",
    # Default
    "Other": "Other"
}

# Order for displaying product lines (dynamic - will be populated from fix versions)
# These are fallback/common names; actual names come from fix versions
# Grouped by category: Media -> Audiences -> DSP Core -> Developer Experience -> Data Ingress -> Helix -> Data Governance
PRODUCT_LINE_ORDER = [
    # Media PLs
    "Media PL1",
    "Media PL2",
    "Media",
    # Audiences PLs
    "Audiences PL1",
    "Audiences PL2",
    "Audiences",
    # DSP Core PLs
    "DSP Core PL1",
    "DSP Core PL2",
    "DSP Core PL3",
    "DSP Core PL5",
    "DSP PL1",
    "DSP PL2",
    "DSP PL3",
    "DSP",
    # Developer Experience
    "Developer Experience",
    "Developer Experience 2026",
    # Data Ingress
    "Data Ingress",
    "Data Ingress 2026",
    # Helix PLs
    "Helix PL3",
    "Helix",
    # Data Governance
    "Data Governance",
    "Other"
]


def parse_pl_from_fix_version(fix_version: str) -> str:
    """
    Extract product line name from fix version string.

    Examples:
        "DSP Core PL3 2026: Release 4.0" -> "DSP Core PL3"
        "DSP Core PL1: Release 3.0" -> "DSP Core PL1"
        "Developer Experience: Release 6.0" -> "Developer Experience"
        "Audiences PL2: Release 4.0" -> "Audiences PL2"

    Args:
        fix_version: Fix version string from Jira

    Returns:
        Product line name
    """
    if not fix_version:
        return "Other"

    # Try to match pattern with year: "DSP Core PL3 2026: Release 4.0"
    match = re.match(r'^(.+?)\s*\d{4}:\s*Release', fix_version)
    if match:
        return match.group(1).strip()

    # Try to match pattern without year: "Developer Experience: Release 6.0"
    match = re.match(r'^(.+?):\s*Release', fix_version)
    if match:
        return match.group(1).strip()

    # Fallback: return the fix version as-is (without release part)
    if ":" in fix_version:
        return fix_version.split(":")[0].strip()

    return fix_version


def consolidate_tldr_with_claude(raw_summaries_by_product: Dict[str, List[str]]) -> Dict[str, str]:
    """
    Consolidates raw Jira summaries into polished single-sentence TLDRs using Claude API.

    Args:
        raw_summaries_by_product: Dict like {
            "DSP Core PL1": ["Summary 1", "Summary 2", "Summary 3"],
            "DSP Core PL3": ["Summary A"]
        }

    Returns:
        Dict with consolidated TLDRs: {
            "DSP Core PL1": "Polished single-sentence prose...",
            "DSP Core PL3": "Polished single-sentence prose..."
        }
    """
    if not ANTHROPIC_AVAILABLE:
        print("[Formatter] Anthropic not available, returning raw summaries")
        # Capitalize first letter of joined summaries
        result = {}
        for product, summaries in raw_summaries_by_product.items():
            text = "; ".join(summaries)
            if text:
                text = text[0].upper() + text[1:]
            result[product] = text
        return result

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[Formatter] ANTHROPIC_API_KEY not set, returning raw summaries")
        # Capitalize first letter of joined summaries
        result = {}
        for product, summaries in raw_summaries_by_product.items():
            text = "; ".join(summaries)
            if text:
                text = text[0].upper() + text[1:]
            result[product] = text
        return result

    client = anthropic.Anthropic(api_key=api_key)
    consolidated = {}

    for product, summaries in raw_summaries_by_product.items():
        try:
            summaries_text = "\n".join([f"- {s}" for s in summaries])

            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": f"""Consolidate these raw Jira ticket summaries into ONE polished prose sentence for TLDR.

Product: {product}
Raw Summaries:
{summaries_text}

Rules:
1. Group related items conceptually (by feature area, not by change type)
2. Use flowing narrative with semicolons separating major sections
3. NO category labels like "usability improvements with", "data capabilities with"
4. NO bullet points or lists
5. Use natural connectors: "with", "including", "featuring", "spanning"
6. Should read smoothly when spoken aloud
7. Focus on feature areas and user impact
8. Keep to ONE sentence (no paragraph breaks)
9. Output ONLY the consolidated prose (no product name prefix)

Example input:
- Open Orders page with the last applied Status column filter
- Deselect Order Selections when user Archives Order
- Allow Multi-select Option for Status Column Filter on Order Listing
- Add Channel, Device, Inventory Filter Extraction Logic
- Fix di-creative-service critical vulnerability

Example output:
Order listing usability improvements with multi-select status filtering, persistent filter preferences across sessions, automatic selection clearing on archive; forecasting enhancements with Deal/Exchange-derived filter logic and validation; critical security vulnerability patched in di-creative-service

Now consolidate for {product}:"""
                }]
            )

            result = message.content[0].text.strip()
            # Remove product name prefix if Claude added it
            if result.lower().startswith(product.lower()):
                result = result[len(product):].lstrip(" -:")
            # Capitalize the first letter of the summary
            if result:
                result = result[0].upper() + result[1:]
            consolidated[product] = result
            print(f"[Formatter] Consolidated TLDR for {product}")

        except Exception as e:
            print(f"[Formatter] Error consolidating {product}: {str(e)}")
            # Fallback to joining with semicolons, capitalize first letter
            text = "; ".join(summaries)
            if text:
                text = text[0].upper() + text[1:]
            consolidated[product] = text

    return consolidated


def consolidate_body_sections_with_claude(product: str, release: str, sections: List[Dict]) -> str:
    """
    Consolidates raw feature sections into flowing prose bullet points.

    Args:
        product: Product name (e.g., "DSP Core PL1")
        release: Release version (e.g., "Release 3.0")
        sections: List of section dicts with structure:
            {
                "title": "Epic Name",
                "items": ["item 1", "item 2"],
                "status": "General Availability" or "Feature Flag"
            }

    Returns:
        Consolidated body text with flowing prose bullets
    """
    if not ANTHROPIC_AVAILABLE:
        print("[Formatter] Anthropic not available, returning raw sections")
        return _format_raw_sections_fallback(sections)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[Formatter] ANTHROPIC_API_KEY not set, returning raw sections")
        return _format_raw_sections_fallback(sections)

    client = anthropic.Anthropic(api_key=api_key)

    # Format sections for Claude
    sections_text = ""
    for section in sections:
        items_list = "\n".join([f"- {item}" for item in section.get("items", [])])
        status = section.get("status", "")
        status_text = f" ({status})" if status else ""
        sections_text += f"\n__{section['title']}__{status_text}\n{items_list}\n"

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"""Consolidate these raw feature sections into polished, flowing prose bullet points.

Product: {product}
Release: {release}

Raw Sections:
{sections_text}

Rules for consolidation:
1. Keep the original section structure (one section per heading)
2. Convert raw Jira summaries into flowing, descriptive prose bullets
3. Each bullet should be a complete, well-written sentence
4. Use natural language that reads smoothly and explains user value
5. Do NOT group sections together - keep them separate with their original titles
6. Include the status flag at the end of each section (General Availability, Feature Flag, etc.)
7. Format output as:
   __Section Title__

   Value Add:

   * Polished prose bullet point 1 explaining the feature and its impact
   * Polished prose bullet point 2 with more context and details
   * Polished prose bullet point 3 connecting to user value

   Status Flag (if applicable)

8. Do NOT abbreviate or use technical jargon - explain clearly for stakeholders
9. Each bullet should be 1-2 complete sentences

Example input:
__Forecasting in Ad Groups__
- Add Channel , Device , Inventory , Creative Unit Length Filter Extraction & Validation Logic based on Deals / Exchanges
- One time Pixel Audience Data in DCR Clickhouse

Example output:
__Forecasting in Ad Groups__

Value Add:

* Enhanced forecasting filter logic now accurately derives Channel, Device, Inventory Type, and Creative Unit Length from attached Deals and Exchanges.
* Validation errors now alert users when targeting configurations conflict (e.g., CTV deals attached to Banner ad groups or audio ad groups).
* Pixel audience data is now available in DCR Clickhouse to support complex audience size calculations spanning HCP and patient campaigns.

Feature Flag

Now consolidate for {product}:"""
            }]
        )

        result = message.content[0].text.strip()
        print(f"[Formatter] Consolidated body for {product}")
        return result

    except Exception as e:
        print(f"[Formatter] Error consolidating body for {product}: {str(e)}")
        return _format_raw_sections_fallback(sections)


def _format_raw_sections_fallback(sections: List[Dict]) -> str:
    """Fallback formatting when LLM is not available."""
    output = []
    for section in sections:
        output.append(f"{section['title']}\n")
        output.append("Value Add:")
        for item in section.get("items", []):
            output.append(f"   * {item}")
        status = section.get("status", "")
        if status:
            output.append(f"\n{status}\n")
        output.append("")
    return "\n".join(output)


class ReleaseNotesFormatter:
    """Formatter for creating release notes from Jira tickets."""

    def __init__(self, release_date: str = None):
        """
        Initialize the formatter.

        Args:
            release_date: Release date string (e.g., "2nd February 2026")
        """
        self.release_date = release_date or self._get_default_date()
        self.tickets = []
        self.grouped_data = defaultdict(lambda: defaultdict(list))

    def _get_default_date(self) -> str:
        """Get today's date in the required format."""
        today = datetime.now()
        day = today.day
        suffix = self._get_day_suffix(day)
        return today.strftime(f"{day}{suffix} %B %Y")

    def _get_day_suffix(self, day: int) -> str:
        """Get the ordinal suffix for a day number."""
        if 11 <= day <= 13:
            return "th"
        return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

    def process_tickets(self, tickets: List[Dict]) -> Dict:
        """
        Process and group tickets by Product Line and Epic.

        Args:
            tickets: List of ticket data from Jira

        Returns:
            Grouped data structure
        """
        print(f"[Formatter] Processing {len(tickets)} tickets")
        self.tickets = tickets

        for ticket in tickets:
            # Skip release tickets themselves
            if self._is_release_ticket(ticket):
                continue

            # Determine product line
            product_line = self._determine_product_line(ticket)

            # Get epic info
            epic_name = ticket.get("epic_name") or "Uncategorized"
            epic_key = ticket.get("epic_key")
            epic_url = ticket.get("epic_url")

            # Create epic info tuple
            epic_info = {
                "name": epic_name,
                "key": epic_key,
                "url": epic_url
            }

            # Group by product line and epic
            self.grouped_data[product_line][epic_name].append({
                "ticket": ticket,
                "epic_info": epic_info
            })

        print(f"[Formatter] Grouped into {len(self.grouped_data)} product lines")
        return self.grouped_data

    def _is_release_ticket(self, ticket: Dict) -> bool:
        """Check if a ticket is a release ticket (to be excluded)."""
        summary = ticket.get("summary", "").lower()
        return "release" in summary and any(
            keyword in summary for keyword in ["deployment", "release notes", "release "]
        )

    def _determine_product_line(self, ticket: Dict) -> str:
        """
        Determine the product line for a ticket.

        Uses fix version as the primary source since that's the most accurate
        indicator of which PL a ticket belongs to.

        Args:
            ticket: Ticket data

        Returns:
            Product line name
        """
        # Primary: Use fix version (most accurate)
        fix_version = ticket.get("fix_version", "")
        if fix_version:
            pl_name = parse_pl_from_fix_version(fix_version)
            if pl_name and pl_name != "Other":
                return pl_name

        # Fallback: Check components
        components = ticket.get("components", [])
        for component in components:
            for key, value in PRODUCT_LINE_MAPPING.items():
                if key.lower() in component.lower():
                    return value

        # Fallback: Check labels
        labels = ticket.get("labels", [])
        for label in labels:
            for key, value in PRODUCT_LINE_MAPPING.items():
                if key.lower() in label.lower():
                    return value

        return "Other"

    def extract_value_adds(self, ticket: Dict) -> List[str]:
        """
        Extract value-add bullets from ticket summary and description.

        Args:
            ticket: Ticket data

        Returns:
            List of value-add bullet points
        """
        value_adds = []

        # Start with the summary as the primary value-add
        summary = ticket.get("summary", "")
        if summary:
            # Clean up the summary
            summary = self._clean_text(summary)
            value_adds.append(summary)

        # Extract additional points from description
        description = ticket.get("description", "")
        if description:
            # Look for bullet points or key information
            bullets = self._extract_bullets_from_description(description)
            value_adds.extend(bullets)

        # Remove duplicates while preserving order
        seen = set()
        unique_value_adds = []
        for item in value_adds:
            if item.lower() not in seen:
                seen.add(item.lower())
                unique_value_adds.append(item)

        return unique_value_adds[:3]  # Limit to 3 bullets max

    def _clean_text(self, text: str) -> str:
        """Clean and format text for display."""
        # Remove extra whitespace
        text = " ".join(text.split())
        # Remove common prefixes
        prefixes_to_remove = ["[DSP]", "[API]", "[UI]", "[BUG]", "[FEATURE]"]
        for prefix in prefixes_to_remove:
            if text.upper().startswith(prefix.upper()):
                text = text[len(prefix):].strip()
        return text

    def _extract_bullets_from_description(self, description: str) -> List[str]:
        """Extract bullet points from description text."""
        bullets = []

        # Look for lines starting with -, *, or numbered items
        lines = description.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith(("-", "*", "+")):
                bullet = line[1:].strip()
                if len(bullet) > 10 and len(bullet) < 200:  # Reasonable length
                    bullets.append(self._clean_text(bullet))
            elif re.match(r"^\d+\.", line):
                bullet = re.sub(r"^\d+\.\s*", "", line)
                if len(bullet) > 10 and len(bullet) < 200:
                    bullets.append(self._clean_text(bullet))

        return bullets[:2]  # Limit additional bullets

    def _get_ordered_pls(self) -> List[str]:
        """Get product lines in preferred display order."""
        # Build actual order: preferred first, then any others
        ordered = []
        for pl in PRODUCT_LINE_ORDER:
            if pl in self.grouped_data:
                ordered.append(pl)
        # Add any PLs not in preferred order
        for pl in self.grouped_data.keys():
            if pl not in ordered:
                ordered.append(pl)
        return ordered

    def _format_summaries_as_prose(self, summaries: List[str]) -> str:
        """
        Format a list of summaries into flowing prose.

        Simply joins all summaries with semicolons for a clean, flowing narrative.
        No category prefixes - just the raw summaries connected naturally.
        """
        if not summaries:
            return ""

        # Clean summaries and join with semicolons
        cleaned = []
        for summary in summaries:
            # Remove trailing periods and clean up
            s = summary.rstrip('.').strip()
            if s:
                cleaned.append(s)

        # Join all summaries with semicolons
        result = '; '.join(cleaned)
        # Capitalize the first letter
        if result:
            result = result[0].upper() + result[1:]
        return result

    def generate_tldr(self, use_llm: bool = True) -> Dict[str, Any]:
        """
        Generate TL;DR summary for the release notes with Key Deployments per PL.

        Uses Claude API to consolidate raw summaries into polished flowing prose.

        Args:
            use_llm: Whether to use LLM consolidation (default True)

        Returns:
            Dictionary with TL;DR components including key deployments by PL
        """
        # Step 1: Collect all raw summaries by product
        raw_summaries_by_product = {}
        fix_versions_by_product = {}

        for pl in self._get_ordered_pls():
            if pl not in self.grouped_data:
                continue

            epics = self.grouped_data[pl]

            # Collect ALL summaries for this PL (cleaned)
            pl_summaries = []
            for epic_name, items in epics.items():
                for item in items:
                    ticket = item["ticket"]
                    summary = ticket.get("summary", "")
                    if summary:
                        # Clean the summary
                        cleaned = self._clean_text(summary)
                        if cleaned and cleaned not in pl_summaries:
                            pl_summaries.append(cleaned)

            # Get fix version for this PL
            first_ticket = list(epics.values())[0][0]["ticket"]
            fix_version = first_ticket.get("fix_version", "")

            if pl_summaries:
                raw_summaries_by_product[pl] = pl_summaries
                fix_versions_by_product[pl] = fix_version

        # Step 2: Use LLM to consolidate summaries into polished prose
        if use_llm and raw_summaries_by_product:
            print("[Formatter] Consolidating TL;DR with Claude API...")
            consolidated = consolidate_tldr_with_claude(raw_summaries_by_product)
        else:
            # Fallback: join with semicolons
            consolidated = {pl: self._format_summaries_as_prose(summaries)
                          for pl, summaries in raw_summaries_by_product.items()}

        # Step 3: Build key deployments list
        key_deployments = []
        for pl in self._get_ordered_pls():
            if pl not in consolidated:
                continue

            deployment_text = consolidated[pl]
            fix_version = fix_versions_by_product.get(pl, "")

            key_deployments.append({
                "pl": pl,
                "version": fix_version,
                "summary": deployment_text
            })

        return {
            "key_deployments": key_deployments,
            "total_pls": len(key_deployments)
        }

    def _find_major_feature(self) -> str:
        """Find the most significant feature in the release."""
        stories = [t for t in self.tickets if t.get("issue_type") == "Story"]

        if not stories:
            return None

        # Sort by story points (if available) or priority
        stories_with_points = [s for s in stories if s.get("story_points")]
        if stories_with_points:
            major = max(stories_with_points, key=lambda x: x.get("story_points", 0))
        else:
            # Sort by priority
            priority_order = {"Highest": 5, "High": 4, "Medium": 3, "Low": 2, "Lowest": 1}
            major = max(stories, key=lambda x: priority_order.get(x.get("priority", "Medium"), 3))

        return major.get("summary", "")[:150]  # Limit length

    def _find_key_enhancement(self) -> str:
        """Find a key enhancement from the release."""
        # Look for bug fixes or improvements
        enhancements = [t for t in self.tickets
                       if t.get("issue_type") in ["Bug", "Improvement", "Task"]]

        if enhancements:
            # Pick one with high priority
            priority_order = {"Highest": 5, "High": 4, "Medium": 3, "Low": 2, "Lowest": 1}
            enhancement = max(enhancements,
                            key=lambda x: priority_order.get(x.get("priority", "Medium"), 3))
            return enhancement.get("summary", "")[:150]

        return None

    def generate_consolidated_body_sections(self, use_llm: bool = True) -> Dict[str, str]:
        """
        Generate consolidated body sections for each PL using LLM.

        Args:
            use_llm: Whether to use LLM consolidation (default True)

        Returns:
            Dict mapping PL name to consolidated body text
        """
        consolidated_bodies = {}

        for pl in self._get_ordered_pls():
            if pl not in self.grouped_data:
                continue

            epics = self.grouped_data[pl]

            # Build sections list for this PL
            sections = []
            for epic_name, items in epics.items():
                # Collect all summaries for this epic
                epic_summaries = []
                for item in items:
                    ticket = item["ticket"]
                    summary = ticket.get("summary", "")
                    if summary:
                        cleaned = self._clean_text(summary)
                        if cleaned:
                            epic_summaries.append(cleaned)

                # Get release type/status from first story ticket
                story_tickets = [i["ticket"] for i in items if i["ticket"].get("issue_type") == "Story"]
                status = ""
                if story_tickets:
                    release_type = story_tickets[0].get("release_type", "")
                    if release_type:
                        status = release_type

                if epic_summaries:
                    sections.append({
                        "title": epic_name,
                        "items": epic_summaries,
                        "status": status
                    })

            # Get fix version for this PL
            first_ticket = list(epics.values())[0][0]["ticket"]
            fix_version = first_ticket.get("fix_version", "")

            # Use LLM to consolidate or fallback
            if use_llm and sections:
                print(f"[Formatter] Consolidating body sections for {pl}...")
                consolidated = consolidate_body_sections_with_claude(pl, fix_version, sections)
                consolidated_bodies[pl] = consolidated
            else:
                # Fallback: use raw sections
                consolidated_bodies[pl] = _format_raw_sections_fallback(sections)

        return consolidated_bodies

    def format_for_google_docs(self) -> List[Dict]:
        """
        Format release notes for Google Docs API.

        Returns:
            List of formatting instructions for Google Docs
        """
        requests = []

        # Current position in document (starts after clearing)
        current_index = 1

        # Title
        title = f"Daily Deployment Summary: {self.release_date}\n\n"
        requests.append({
            "insertText": {
                "location": {"index": current_index},
                "text": title
            }
        })
        current_index += len(title)

        # TL;DR Section
        tldr = self.generate_tldr()
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

        # TL;DR content - Key Deployments per PL
        for deployment in tldr.get("key_deployments", []):
            pl_name = deployment["pl"]
            version = deployment.get("version", "")
            summary = deployment.get("summary", "")

            if version:
                deploy_line = f"   * {pl_name} ({version}): {summary}\n"
            else:
                deploy_line = f"   * {pl_name}: {summary}\n"

            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": deploy_line
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

        # Process each product line in order
        for pl in self._get_ordered_pls():
            epics = self.grouped_data[pl]

            # Product line header with separator
            pl_header = f"------------------{pl}------------------\n\n"
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": pl_header
                }
            })
            current_index += len(pl_header)

            # Get fix version from first ticket in this PL
            first_ticket = list(epics.values())[0][0]["ticket"]
            fix_version = first_ticket.get("fix_version")
            fix_version_url = first_ticket.get("fix_version_url")

            if fix_version:
                version_text = f"{pl}: {fix_version}\n\n"
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": version_text
                    }
                })
                # Store position for hyperlink
                version_start = current_index + len(f"{pl}: ")
                version_end = version_start + len(fix_version)
                current_index += len(version_text)

                # Add hyperlink to fix version
                if fix_version_url:
                    requests.append({
                        "updateTextStyle": {
                            "range": {
                                "startIndex": version_start,
                                "endIndex": version_end
                            },
                            "textStyle": {
                                "link": {"url": fix_version_url},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"blue": 1.0}}
                                }
                            },
                            "fields": "link,foregroundColor"
                        }
                    })

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
                        "endIndex": approval_start + len(approval_text) - 2  # Exclude newlines
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

            # Process each epic
            for epic_name, items in epics.items():
                epic_info = items[0]["epic_info"]

                # Epic name (will be hyperlinked)
                epic_text = f"{epic_name}\n\n"
                epic_start = current_index
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": epic_text
                    }
                })
                epic_end = current_index + len(epic_name)
                current_index += len(epic_text)

                # Make epic name a blue hyperlink if URL exists
                if epic_info.get("url"):
                    requests.append({
                        "updateTextStyle": {
                            "range": {
                                "startIndex": epic_start,
                                "endIndex": epic_end
                            },
                            "textStyle": {
                                "link": {"url": epic_info["url"]},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"blue": 1.0}}
                                }
                            },
                            "fields": "link,foregroundColor"
                        }
                    })

                # Value Add section header
                value_add_header = "Value Add:\n"
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": value_add_header
                    }
                })
                # Bold the "Value Add:" text
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": current_index,
                            "endIndex": current_index + len("Value Add:")
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold"
                    }
                })
                current_index += len(value_add_header)

                # Process tickets under this epic
                for item in items:
                    ticket = item["ticket"]
                    value_adds = self.extract_value_adds(ticket)

                    for value_add in value_adds:
                        bullet = f"   * {value_add}\n"
                        requests.append({
                            "insertText": {
                                "location": {"index": current_index},
                                "text": bullet
                            }
                        })
                        current_index += len(bullet)

                # Add release type tag for stories with colors
                story_tickets = [i["ticket"] for i in items if i["ticket"].get("issue_type") == "Story"]
                if story_tickets:
                    release_type = story_tickets[0].get("release_type")
                    if release_type:
                        tag_text = f"\n{release_type}\n"
                        tag_start = current_index + 1  # After the newline
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
                                        },
                                        "bold": True
                                    },
                                    "fields": "foregroundColor,bold"
                                }
                            })
                        elif "general availability" in release_type.lower() or "ga" in release_type.lower():
                            requests.append({
                                "updateTextStyle": {
                                    "range": {
                                        "startIndex": tag_start,
                                        "endIndex": tag_start + len(release_type)
                                    },
                                    "textStyle": {
                                        "foregroundColor": {
                                            "color": {"rgbColor": {"red": 0.0, "green": 0.6, "blue": 0.0}}
                                        },
                                        "bold": True
                                    },
                                    "fields": "foregroundColor,bold"
                                }
                            })

                        current_index += len(tag_text)

                # Add spacing between epics
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": "\n"
                    }
                })
                current_index += 1

            # Add spacing between product lines
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": "\n"
                }
            })
            current_index += 1

        return requests

    def get_plain_text_notes(self, use_llm: bool = True) -> str:
        """
        Generate plain text version of release notes (for Slack).

        Args:
            use_llm: Whether to use LLM consolidation for body sections (default True)

        Returns:
            Plain text release notes
        """
        lines = []

        # Title
        lines.append(f"Daily Deployment Summary: {self.release_date}")
        lines.append("")

        # TL;DR (already uses LLM)
        tldr = self.generate_tldr(use_llm=use_llm)
        lines.append("------------------TL;DR:------------------")
        lines.append("")
        lines.append("*Key Deployments:*")

        for deployment in tldr.get("key_deployments", []):
            pl_name = deployment["pl"]
            version = deployment.get("version", "")
            summary = deployment.get("summary", "")
            if version:
                lines.append(f"   • {pl_name} ({version}): {summary}")
            else:
                lines.append(f"   • {pl_name}: {summary}")

        lines.append("")

        # Generate consolidated body sections (uses LLM if enabled)
        consolidated_bodies = self.generate_consolidated_body_sections(use_llm=use_llm)

        # Process each product line
        for pl in self._get_ordered_pls():
            if pl not in self.grouped_data:
                continue

            epics = self.grouped_data[pl]
            lines.append(f"------------------{pl}------------------")
            lines.append("")

            # Get fix version
            first_ticket = list(epics.values())[0][0]["ticket"]
            fix_version = first_ticket.get("fix_version")
            if fix_version:
                lines.append(f"{pl}: {fix_version}")
                lines.append("")

            # Use consolidated body if available
            if pl in consolidated_bodies:
                lines.append(consolidated_bodies[pl])
            else:
                # Fallback to raw format
                for epic_name, items in epics.items():
                    lines.append(f"{epic_name}")
                    lines.append("")
                    lines.append("**Value Add:**")

                    for item in items:
                        ticket = item["ticket"]
                        value_adds = self.extract_value_adds(ticket)
                        for value_add in value_adds:
                            lines.append(f"   * {value_add}")

                    # Add release type for stories
                    story_tickets = [i["ticket"] for i in items if i["ticket"].get("issue_type") == "Story"]
                    if story_tickets:
                        release_type = story_tickets[0].get("release_type")
                        if release_type:
                            lines.append(f"\n`{release_type}`")

                    lines.append("")

            lines.append("")

        return "\n".join(lines)

    def get_tldr_for_slack(self) -> str:
        """Get TL;DR formatted for Slack message."""
        tldr = self.generate_tldr()

        lines = ["*Key Deployments:*"]
        for deployment in tldr.get("key_deployments", []):
            pl_name = deployment["pl"]
            version = deployment.get("version", "")
            summary = deployment.get("summary", "")
            if version:
                lines.append(f"   • {pl_name} ({version}): {summary}")
            else:
                lines.append(f"   • {pl_name}: {summary}")

        return "\n".join(lines)


def main():
    """Test the formatter."""
    # Create sample ticket data
    sample_tickets = [
        {
            "key": "DI-100",
            "summary": "Add new targeting options for DSP campaigns",
            "description": "Implement new targeting capabilities:\n- Geographic targeting\n- Demographic targeting",
            "issue_type": "Story",
            "status": "Done",
            "priority": "High",
            "fix_version": "Release 61.0",
            "labels": ["GA", "DSP"],
            "release_type": "General Availability",
            "epic_name": "Campaign Targeting Enhancement",
            "epic_key": "DI-50",
            "epic_url": "https://deepintent.atlassian.net/browse/DI-50",
            "components": ["DSP PL2"],
            "story_points": 8
        },
        {
            "key": "DI-101",
            "summary": "Fix audience segment loading issue",
            "description": "Resolved performance issue with audience segments",
            "issue_type": "Bug",
            "status": "Done",
            "priority": "High",
            "fix_version": "Release 61.0",
            "labels": ["Audiences"],
            "release_type": None,
            "epic_name": "Audience Management",
            "epic_key": "DI-51",
            "epic_url": "https://deepintent.atlassian.net/browse/DI-51",
            "components": ["Audiences PL1"],
            "story_points": 3
        }
    ]

    formatter = ReleaseNotesFormatter("2nd February 2026")
    formatter.process_tickets(sample_tickets)

    print("=== Plain Text Release Notes ===")
    print(formatter.get_plain_text_notes())

    print("\n=== TL;DR for Slack ===")
    print(formatter.get_tldr_for_slack())


if __name__ == "__main__":
    main()
