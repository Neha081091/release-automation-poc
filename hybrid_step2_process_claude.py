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
    """Consolidate summaries into flowing prose TL;DR summary using Claude."""
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
            "content": f"""Write a flowing prose TL;DR summary for this deployment.

Product: {product}
Raw Jira Items:
{summaries_text}

CRITICAL FORMAT RULES - Follow this EXACT style:
1. Write ONE flowing prose sentence (NOT bullet points or technical jargon dumps)
2. Start with the main feature/improvement name, then use "with" to connect specific details
3. Use natural connectors: "with", "including", "enabling", "along with"
4. Use commas to list related items, semicolons only for truly separate feature areas
5. Should read smoothly when spoken aloud - like a natural summary
6. Keep technical names but explain them in context
7. Be SPECIFIC with details (file sizes, button names, field names, etc.)
8. Explain the PURPOSE/BENEFIT after changes using "enabling...", "for better...", "to ensure..."
9. For bug fixes, start with "Critical bug fixes including" then list all fixes with their benefits
10. Use active voice and present tense: "enables", "supports", "provides"
{"11. Mention that features are available via feature flag at the end of the summary" if has_feature_flag else ""}

BAD EXAMPLES (too terse/technical - DO NOT DO THIS):
- "Data pipeline migration (job1, job2, job3) from Spring Batch to Airflow, tier display N/A vs Unknown, package deal targeting fix"
- "Redis checkpointer, async ainvoke, session list, title search"
- "Restart button, placeholder update, welcome message"

GOOD EXAMPLES (flowing prose - DO THIS):
- "Sales Planning Copilot UX improvements with restart chat button, updated placeholders ("How can I help?"), and enhanced welcome messaging for better user guidance on uploading HCP or DTC RFP briefs"
- "Removed prebaked query requirement for HCP + DTC audience exports, enabling custom query-based audience creation with automatic type detection, validation, and permission-based export controls"
- "Critical bug fixes including HCP Planner target list upload support for files >3.5 MB, corrected patient age calculation removing hardcoded year values, fixed audience token mapping for multiple token types, and enhanced data ingestion pipeline reliability for IQVIA and SYMPHONY"
- "Account Manager experience improved with tab-based navigation on organization pages, modernized ticker components, bulk user assignment across advertisers, and current user role visibility"

Output ONLY the flowing prose summary (no prefix, no product name):"""
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
    """Consolidate body sections into flowing prose release notes using Claude."""
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
            "content": f"""Transform these raw Jira sections into polished, flowing prose release notes.

Product: {product}
Release Version: {release_version}

Raw Sections:
{sections_text}

CRITICAL FORMAT RULES - Follow this EXACT style:
1. NO markdown formatting (no **, no __, no backticks)
2. Use PLAIN TEXT only
3. Epic names as headers on their own line
4. For regular epics: include "Value Add:" header followed by BULLET POINTS using asterisks (*)
5. Each bullet point should be a COMPLETE, READABLE SENTENCE that explains user value
6. Write in flowing prose - each bullet should read naturally when spoken aloud
7. Explain WHAT the change does and WHY it matters to users
8. Keep technical context but make it accessible (e.g., "migrated to Airflow for better job management")
9. End each epic section with status tag on its own line: General Availability OR Feature Flag
10. ONE blank line between epic sections

FOR BUG FIXES:
- Use "Bug Fix:" (singular, with colon) as inline prefix
- Write as a complete sentence: "Bug Fix: Resolved [issue description], ensuring [user benefit]."
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
* Data pipeline jobs have been migrated from Java Spring Batch to Python Airflow, enabling better job scheduling, monitoring, and execution management across the deduplication workflow.
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
* Reporting now displays clearer inventory tier information, showing "N/A" instead of "Unknown" for better data clarity and consistent tier handling.
General Availability

Raw Input:
Epic: Bug Fixes
Status: General Availability
Items:
- Fix package deal targeting where deals were targeted individually instead of as unified packages

CORRECT OUTPUT:
Bug Fix: Resolved an issue with package deal targeting where deals were being targeted individually instead of as unified packages, ensuring proper package-level targeting for new deals.
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
* Users can now retrieve their full conversation history, with messages persisted via Redis checkpointer for reliable access across sessions.
* Chat performance improved by converting LLM calls to async operations, resolving sync client errors in async environments.
* The sidebar now displays a session list fetched from the backend, making it easy to navigate between conversations.
* Added session search by title, allowing users to quickly find and return to relevant conversations.
Feature Flag

Raw Input:
Epic: DSP PL5 - General Enhancements 1Q26
Status: General Availability
Items:
- Rename DoubleVerify product from Authentic Brand Safety to Authentic Brand Suitability

CORRECT OUTPUT:
DSP PL5 - General Enhancements 1Q26
Value Add:
* Updated DoubleVerify product naming from "Authentic Brand Safety" to "Authentic Brand Suitability" to reflect the correct industry terminology.
General Availability

KEY PRINCIPLES:
- Write COMPLETE, READABLE SENTENCES that explain value to users
- Each bullet should answer: What changed? Why does it matter?
- Use natural language that reads smoothly when spoken aloud
- Keep technical context but explain it accessibly
- Focus on user impact and benefits, not just technical changes
- Use asterisks (*) for bullet points

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
