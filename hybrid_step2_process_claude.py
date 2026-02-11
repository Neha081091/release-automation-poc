#!/usr/bin/env python3
"""
HYBRID STEP 2: Process with Claude API (Run on Server)

This script reads the exported Jira tickets JSON and processes them
with Claude API to create polished release notes.

Usage:
    python hybrid_step2_process_claude.py

Input:
    tickets_export.json

Output:
    processed_notes.json
"""

import json
import os
import re
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

import anthropic


# Product Line order - grouped by category: Media -> Audiences -> DSP Core -> Developer Experience -> Data Ingress -> Helix -> Data Governance
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


def consolidate_with_claude(client, product: str, summaries: list, statuses: list = None) -> str:
    """Consolidate summaries into technical TL;DR bullet point using Claude."""
    summaries_text = "\n".join([f"- {s}" for s in summaries])

    # Check if any items are feature flagged
    has_feature_flag = False
    if statuses:
        has_feature_flag = any("feature flag" in str(s).lower() for s in statuses if s)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Write a TECHNICAL TL;DR bullet point for this deployment.

Product: {product}
Raw Jira Items:
{summaries_text}

CRITICAL FORMAT RULES - Follow this EXACT style:
1. Write ONE technical bullet point (NOT prose sentences)
2. Use SPECIFIC technical terms: technology names, job names, service names, API names
3. List specific items in parentheses: (job1, job2, job3)
4. Mention FROM/TO when migrating: "from X to Y"
5. Include version numbers when relevant: "v2.11.0 to v3.1.6"
6. Use commas to separate distinct changes within the bullet
7. Keep technical details like specific values in quotes: "N/A", "Unknown"
8. Mention bug fixes directly: "bug fix for X, ensuring Y"
9. Use present tense for capabilities: "displays", "supports", "enables"
10. NO marketing fluff like "improved", "enhanced", "better"
11. Be CONCISE but SPECIFIC - include technical names and details
{"12. Add (Feature Flag) at the END of the bullet if items are feature flagged" if has_feature_flag else ""}

BAD EXAMPLES (too generic/marketing-speak - DO NOT DO THIS):
- "Migrated data pipelines from legacy systems for improved workflow management"
- "Updated the UI to display conversions with appropriate states"
- "Implemented session management with improved search functionality"

GOOD EXAMPLES (technical and specific - DO THIS):
- "Data pipeline migration from Java Spring Batch to Python Airflow for 5 deduplication jobs (bidder_engagement, bidder_impression, bidder_conversion, bidder_click, bq_schema_generation), inventory tier reporting displaying "N/A" instead of "Unknown", bug fix for package deal targeting ensuring deals target as packages"
- "Ad Group builder revamp with ACBA-related conversions widget displaying Campaign Group level selections with appropriate enabled/disabled states (Feature Flag)"
- "Redis checkpointer conversation history retrieval, async LLM invoke calls (ainvoke) to resolve sync client errors, backend session list integration with sidebar display, session title search for quick conversation discovery"
- "Account Manager revamp with tab navigation on organization details page, ticker migration to new UI, bulk action to add users to multiple advertisers, current user role tag display"
- "Apache Airflow upgrade from v2.11.0 to v3.1.6, Airflow code centralized in common-graphql across 9 services, Lambda environment variable updates for DATA_AIRFLOW_AUTH_HEADER, REST API compatibility changes across 6 repositories"

Output ONLY the technical bullet point (no prefix, no product name):"""
        }]
    )

    result = message.content[0].text.strip()
    # Remove quotes if present
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    if result.lower().startswith(product.lower()):
        result = result[len(product):].lstrip(" -:")
    # Remove leading bullet characters if present
    result = result.lstrip('*•-● ')
    # Capitalize the first letter of the summary
    if result:
        result = result[0].upper() + result[1:]
    return result


def consolidate_body_with_claude(client, product: str, sections: list, release_version: str) -> str:
    """Consolidate body sections into technical bullet-point release notes using Claude."""
    sections_text = ""
    for section in sections:
        items_list = "\n".join([f"- {item}" for item in section.get("items", [])])
        status = section.get("status", "General Availability")
        sections_text += f"\nEpic: {section['title']}\nStatus: {status}\nItems:\n{items_list}\n"

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""Transform these raw Jira sections into TECHNICAL BULLET-POINT release notes.

Product: {product}
Release Version: {release_version}

Raw Sections:
{sections_text}

CRITICAL FORMAT RULES - Follow this EXACT style:
1. NO markdown formatting (no **, no __, no backticks)
2. Use PLAIN TEXT only
3. Epic names as headers on their own line
4. For regular epics: include "Value Add:" header followed by BULLET POINTS using asterisks (*)
5. Each bullet point should be a SPECIFIC, TECHNICAL description
6. Include SPECIFIC names: job names, service names, repo names, field names
7. Use quotes for specific values: "N/A", "Unknown", "Authentic Brand Suitability"
8. Preserve technical details from the Jira items - don't generalize
9. End each epic section with status tag on its own line: General Availability OR Feature Flag
10. ONE blank line between epic sections

FOR BUG FIXES:
- Use "Bug Fix:" (singular, with colon) as inline prefix
- Write as: "Bug Fix: Fixed [specific issue], ensuring [specific outcome]."
- Put the status tag on the next line

TRANSFORMATION EXAMPLES:

Raw Input:
Epic: Migration of data pipelines from spring batch to airflow - Media PLs
Status: General Availability
Items:
- Migrate dedup_bidder_engagement job from spring batch to airflow
- Migrate dedup_bidder_impression job from spring batch to airflow
- Migrate dedup_bidder_conversion job from spring batch to airflow
- Migrate dedup_bidder_click job from spring batch to airflow
- Migrate bq_schema_generation job from spring batch to airflow

CORRECT OUTPUT:
Migration of data pipelines from spring batch to airflow - Media PLs
Value Add:
* Data pipeline jobs (dedup_bidder_engagement, dedup_bidder_impression, dedup_bidder_conversion, dedup_bidder_click, bq_schema_generation) migrated from Java Spring Batch to Python Airflow for improved management and execution efficiency.
General Availability

Raw Input:
Epic: Inventory Priority Tiers - Reporting
Status: General Availability
Items:
- Display Unknown values as N/A in InventoryTier dimension
- Treat tier 0 as tier 1 in reporting

CORRECT OUTPUT:
Inventory Priority Tiers - Reporting
Value Add:
* Improved data accuracy in reports by displaying "N/A" instead of "Unknown" for inventory tier dimension and treating tier 1,2,3 as tier 1,2,3 respectively.
General Availability

Raw Input:
Epic: Bug Fixes
Status: General Availability
Items:
- Fix package deal targeting where deals were targeted individually instead of as unified packages

CORRECT OUTPUT:
Bug Fix: Fixed package deal targeting issue, ensuring deals are targeted as part of the package rather than individually when new packages are created.
General Availability

Raw Input:
Epic: Core Chat UI Shell (Cora Tab Only)
Status: Feature Flag
Items:
- Implement Redis checkpointer for conversation history retrieval
- Convert LLM invoke to async ainvoke
- Add session list fetched from backend to sidebar
- Add session search by title functionality

CORRECT OUTPUT:
Core Chat UI Shell (Cora Tab Only)
Value Add:
* Users can now retrieve the full conversation history from the Redis checkpointer for any thread.
* LLM invoke calls converted to async (ainvoke) to resolve sync client unavailable errors in async environments.
* Session list now fetched from the backend and displayed in the sidebar with search functionality.
* Users can search sessions by title to quickly find and return to relevant conversations.
Feature Flag

Raw Input:
Epic: DSP PL5 - General Enhancements 1Q26
Status: General Availability
Items:
- Rename DoubleVerify product from Authentic Brand Safety to Authentic Brand Suitability

CORRECT OUTPUT:
DSP PL5 - General Enhancements 1Q26
Value Add:
* DoubleVerify's product renamed from "Authentic Brand Safety" to "Authentic Brand Suitability" for correct terminology
General Availability

KEY PRINCIPLES:
- PRESERVE specific technical names, values, and details from Jira
- Use asterisks (*) for bullet points, NOT prose paragraphs
- Each bullet should describe ONE specific change/capability
- Include service names, job names, field names when mentioned in Jira
- Use quotes for specific UI text or values
- Be TECHNICAL, not marketing/generic

Transform sections for {product}:"""
        }]
    )

    result = message.content[0].text.strip()

    # Post-process: Normalize bullet characters to asterisks
    lines = result.split('\n')
    processed_lines = []
    for line in lines:
        stripped = line.lstrip()
        # Convert various bullet characters to asterisk format
        if stripped.startswith('● '):
            line = line.replace('● ', '* ', 1)
        elif stripped.startswith('• '):
            line = line.replace('• ', '* ', 1)
        elif stripped.startswith('- ') and not stripped.lower().startswith('- media') and not stripped.lower().startswith('- dsp'):
            # Convert dash bullets to asterisks (but not if it's part of a PL name)
            if len(stripped) > 2 and stripped[2].isupper():
                line = line.replace('- ', '* ', 1)
        processed_lines.append(line)

    return '\n'.join(processed_lines)


def process_tickets_with_claude():
    """Process exported tickets with Claude API."""
    print("=" * 60)
    print("  HYBRID STEP 2: Process with Claude API")
    print("=" * 60)

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[Step 2] ERROR: ANTHROPIC_API_KEY not set")
        return None

    # Load exported tickets
    input_file = "tickets_export.json"
    if not os.path.exists(input_file):
        print(f"[Step 2] ERROR: {input_file} not found")
        print("[Step 2] Run hybrid_step1_export_jira.py first on Mac")
        return None

    with open(input_file, 'r') as f:
        export_data = json.load(f)

    tickets = export_data.get("tickets", [])
    print(f"[Step 2] Loaded {len(tickets)} tickets from {input_file}")

    # Initialize Claude client
    client = anthropic.Anthropic(api_key=api_key)
    print("[Step 2] Claude API client initialized")

    # Group tickets by product line
    grouped = defaultdict(lambda: defaultdict(list))

    for ticket in tickets:
        fix_version = ticket.get("fix_version", "")
        # Parse PL from fix version - preserve year if present (e.g., "Media PL1 2026")
        match = re.match(r'^(.+?\s*\d{4}):\s*Release', fix_version)
        if match:
            pl = match.group(1).strip()
        else:
            # Try without year
            match = re.match(r'^(.+?):\s*Release', fix_version)
            pl = match.group(1).strip() if match else "Other"

        epic_name = ticket.get("epic_name")
        issue_type = ticket.get("issue_type", "").lower()

        # If no epic, categorize based on issue type
        if not epic_name:
            if issue_type == "bug":
                epic_name = "Bug Fixes"
            else:
                epic_name = "Uncategorized"

        grouped[pl][epic_name].append(ticket)

    print(f"[Step 2] Grouped into {len(grouped)} product lines")

    # Process TL;DR for each PL
    print("\n[Step 2] Processing TL;DR summaries...")
    tldr_by_pl = {}
    for pl, epics in grouped.items():
        summaries = []
        statuses = []
        for epic_name, epic_tickets in epics.items():
            for t in epic_tickets:
                if t.get("summary"):
                    summaries.append(t["summary"])
                if t.get("release_type"):
                    statuses.append(t["release_type"])

        if summaries:
            print(f"  Processing {pl}...")
            try:
                tldr_by_pl[pl] = consolidate_with_claude(client, pl, summaries, statuses)
                print(f"  ✅ {pl} - consolidated")
            except Exception as e:
                print(f"  ❌ {pl} - error: {e}")
                tldr_by_pl[pl] = "; ".join(summaries)

    # Extract release versions and URLs per PL
    release_versions = {}
    fix_version_urls = {}
    for pl, epics in grouped.items():
        for epic_name, epic_tickets in epics.items():
            for t in epic_tickets:
                fix_version = t.get("fix_version", "")
                # Extract version number (e.g., "Release 3.0" from "DSP Core PL1 2026: Release 3.0")
                version_match = re.search(r'Release\s*([\d.]+)', fix_version)
                if version_match:
                    release_versions[pl] = f"Release {version_match.group(1)}"
                    # Get fix version URL if available
                    if t.get("fix_version_url"):
                        fix_version_urls[pl] = t["fix_version_url"]
                    break
            if pl in release_versions:
                break
        if pl not in release_versions:
            release_versions[pl] = "Release 1.0"

    # Collect epic URLs for each PL
    epic_urls_by_pl = {}
    for pl, epics in grouped.items():
        epic_urls_by_pl[pl] = {}
        for epic_name, epic_tickets in epics.items():
            for t in epic_tickets:
                if t.get("epic_url"):
                    epic_urls_by_pl[pl][epic_name] = t["epic_url"]
                    break

    # Process body sections for each PL
    print("\n[Step 2] Processing body sections...")
    body_by_pl = {}
    for pl, epics in grouped.items():
        sections = []
        for epic_name, epic_tickets in epics.items():
            items = [t["summary"] for t in epic_tickets if t.get("summary")]
            status = "General Availability"
            for t in epic_tickets:
                if t.get("release_type"):
                    status = t["release_type"]
                    break
            if items:
                sections.append({
                    "title": epic_name,
                    "items": items,
                    "status": status
                })

        # Sort sections: Bug Fixes should come LAST
        sections.sort(key=lambda s: (1 if s["title"] == "Bug Fixes" else 0, s["title"]))

        if sections:
            print(f"  Processing {pl}...")
            release_ver = release_versions.get(pl, "Release 1.0")
            try:
                body_by_pl[pl] = consolidate_body_with_claude(client, pl, sections, release_ver)
                print(f"  ✅ {pl} - consolidated")
            except Exception as e:
                print(f"  ❌ {pl} - error: {e}")
                # Fallback with new format
                body_text = ""
                for s in sections:
                    body_text += f"{s['title']}\n\nValue Add:\n"
                    for item in s['items']:
                        body_text += f"• {item}\n"
                    status = s.get('status', 'General Availability')
                    body_text += f"\n{status}\n\n"
                body_by_pl[pl] = body_text

    # Export processed notes
    # Sort product lines according to PRODUCT_LINE_ORDER
    sorted_pls = get_ordered_pls(list(grouped.keys()))

    output_data = {
        "processed_at": datetime.now().isoformat(),
        "source_file": input_file,
        "release_summary": export_data.get("release_summary"),
        "ticket_count": len(tickets),
        "product_lines": sorted_pls,
        "release_versions": release_versions,
        "fix_version_urls": fix_version_urls,
        "epic_urls_by_pl": epic_urls_by_pl,
        "tldr_by_pl": tldr_by_pl,
        "body_by_pl": body_by_pl,
        "grouped_data": {pl: {epic: [t["key"] for t in tickets] for epic, tickets in epics.items()} for pl, epics in grouped.items()}
    }

    output_file = "processed_notes.json"
    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n[Step 2] EXPORTED to: {output_file}")
    print("\n" + "=" * 60)
    print("  NEXT: Copy processed_notes.json to Mac and run:")
    print("  python hybrid_step3_update_docs.py")
    print("=" * 60)

    return output_file


if __name__ == "__main__":
    process_tickets_with_claude()
