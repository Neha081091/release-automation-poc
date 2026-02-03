"""
Release Notes Formatter for Release Automation PoC

This module handles formatting of release notes:
- Grouping tickets by Product Line and Epic
- Generating TL;DR summary
- Creating formatted text for Google Docs
- Extracting value-add bullets from descriptions
"""

from typing import Dict, List, Any
from collections import defaultdict
from datetime import datetime
import re


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
PRODUCT_LINE_ORDER = [
    "DSP Core PL1",
    "DSP Core PL2",
    "DSP Core PL3",
    "DSP Core PL5",
    "DSP PL1",
    "DSP PL2",
    "DSP PL3",
    "DSP",
    "Audiences PL1",
    "Audiences PL2",
    "Audiences",
    "Media PL1",
    "Media",
    "Helix PL3",
    "Helix",
    "Developer Experience",
    "Developer Experience 2026",
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

    def generate_tldr(self) -> Dict[str, Any]:
        """
        Generate TL;DR summary for the release notes with Key Deployments per PL.

        Returns:
            Dictionary with TL;DR components including key deployments by PL
        """
        # Get list of deployed product lines with their key deployments
        key_deployments = []

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

            # Create deployment entry with ALL summaries
            if pl_summaries:
                # Join all summaries with "; " for comprehensive TL;DR
                deployment_text = "; ".join(pl_summaries)
                if fix_version:
                    key_deployments.append({
                        "pl": pl,
                        "version": fix_version,
                        "summary": deployment_text
                    })
                else:
                    key_deployments.append({
                        "pl": pl,
                        "version": "",
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

    def get_plain_text_notes(self) -> str:
        """
        Generate plain text version of release notes (for Slack).

        Returns:
            Plain text release notes
        """
        lines = []

        # Title
        lines.append(f"Daily Deployment Summary: {self.release_date}")
        lines.append("")

        # TL;DR
        tldr = self.generate_tldr()
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

        # Process each product line
        for pl in self._get_ordered_pls():
            epics = self.grouped_data[pl]
            lines.append(f"------------------{pl}------------------")
            lines.append("")

            # Get fix version
            first_ticket = list(epics.values())[0][0]["ticket"]
            fix_version = first_ticket.get("fix_version")
            if fix_version:
                lines.append(f"{pl}: {fix_version}")
                lines.append("")

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
