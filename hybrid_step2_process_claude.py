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
        model="claude-opus-4-6",
        temperature=0,
        max_tokens=500,
        system="You are a professional technical writer at a healthcare ad-tech company. Write clear, concise deployment summaries for stakeholders.",
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
        model="claude-opus-4-6",
        temperature=0,
        max_tokens=2000,
        system="You are a professional technical writer at a healthcare ad-tech company. Write clear, concise deployment summaries for stakeholders.",
        messages=[{
            "role": "user",
            "content": f"""Transform these raw Jira sections into polished deployment value-add summaries.

Product: {product}
Release Version: {release_version}

Raw Sections:
{sections_text}

CRITICAL FORMAT RULES - Follow this EXACT style:
1. NO markdown formatting (no **, no __, no backticks)
2. Use PLAIN TEXT only
3. Epic names as headers on their own line
4. Include "Value Add:" header followed by the description
5. List each value-add as a SEPARATE bullet point using ● (filled circle)
6. Keep FLAT structure - no sub-bullets, no nesting
7. Focus on BUSINESS VALUE and USER BENEFITS in each bullet
8. Each bullet should start with action verb (Updated, Added, Improved, Included, Removed, Fixed, Enhanced)
9. Explain WHAT the change does and WHY it benefits users (e.g., "enabling...", "to ensure...", "for better...")
10. End each epic section with status tag on its own line: General Availability OR Feature Flag
11. ONE blank line between epic sections
12. Exclude the release ticket itself from the summary

CRITICAL BUG FIX FORMAT - THIS IS MANDATORY:
- Group all bug fixes under "Bug Fixes:" header (plural)
- EVERY bug fix bullet MUST start with the word "Fixed" - NO EXCEPTIONS
- Format: "Fixed [what was broken] issue ensuring [explanation of benefit]"
- WRONG: "● Package deals now target correctly as unified packages"
- CORRECT: "● Fixed package deal targeting issue ensuring deals are targeted as part of the package rather than individually"
- Use "ensuring", "enabling", "allowing" to explain the benefit of the fix
- Put the status tag after all bug fixes

TRANSFORMATION EXAMPLES:

Raw Input:
Epic: Home Dashboard V2
Status: General Availability
Items:
- Update copy for Goals Widget grammar

CORRECT OUTPUT:
Home Dashboard V2
Value Add: Updated copy for Goals Widget to ensure correct grammar and improved user experience.
General Availability

Raw Input:
Epic: DSP PL3 - General Enhancements
Status: General Availability
Items:
- Add click interactions for top bar metrics customization

CORRECT OUTPUT:
DSP PL3 - General Enhancements
Value Add: Added click interactions for top bar metrics customization, enabling easier metric selection alongside drag-drop functionality.
General Availability

Raw Input:
Epic: Campaigns List Page V3
Status: General Availability
Items:
- Allow top bar metrics selection independently from listing columns
- Enhanced audit log for PG Ad Groups by removing inapplicable bid fields

CORRECT OUTPUT:
Campaigns List Page V3
Value Add:
● Improved campaign listing by allowing top bar metrics selection independently from listing columns
● Enhanced audit log for PG Ad Groups by removing inapplicable bid fields for improved clarity
General Availability

Raw Input:
Epic: Bug Fixes
Status: General Availability
Items:
- Fix Add frequency button disappears when directly deleting existing frequency on ad-group quickview
- Fix null date display in tooltip when hovering on graph datapoints in Goal Widget
- Fix issue preventing users from changing ad group status from preview on campaign dashboard
- Package deals now target correctly as unified packages

CORRECT OUTPUT:
Bug Fixes:
● Fixed Add frequency button issue ensuring the button remains visible when directly deleting existing frequency on ad-group quickview
● Fixed null date display issue ensuring correct date formatting when hovering on graph datapoints in Goal Widget
● Fixed ad group status change issue enabling users to change status from preview on campaign dashboard
● Fixed package deal targeting issue ensuring deals are targeted as part of the package rather than individually when new packages are created
General Availability

Raw Input:
Epic: 2FA and Passwordless support for Platform
Status: Feature Flag
Items:
- Rename TOTP to Authenticator on Account Manager MFA slider
- Add ability to choose between EOTP and Authenticator Code on SSO UI

CORRECT OUTPUT:
2FA and Passwordless support for Platform
Value Add:
● Renamed TOTP to "Authenticator" on Account Manager MFA slider for improved clarity and user understanding
● Added ability to choose between EOTP and Authenticator Code on SSO UI to provide flexibility in authentication methods
Feature Flag

KEY PRINCIPLES:
- Generate professional, concise summaries emphasizing user benefits
- Keep bullet points simple and flat (no nested bullets)
- Be CONCISE and DIRECT - short sentences, no verbose marketing language
- KEEP TECHNICAL TERMS - preserve specific names like "ainvoke", "Redis checkpointer", "thread_id"
- State the ACTUAL PROBLEM SOLVED, not vague improvements
- COMBINE related functionality into single bullets - fewer bullets is better
- DROP low-value items that don't add meaningful information
- Use ● (filled circle) for bullet points
- Start with simple verbs: Added, Fixed, Migrated, Converted, Updated
- FOR BUG FIXES: ALWAYS start with "Fixed" - this is mandatory

Transform sections for {product}:"""
        }]
    )

    result = message.content[0].text.strip()

    # Post-process: Normalize bullet characters to ● (filled circle)
    lines = result.split('\n')
    processed_lines = []
    for line in lines:
        stripped = line.lstrip()
        # Convert various bullet characters to ● format
        if stripped.startswith('* '):
            line = line.replace('* ', '● ', 1)
        elif stripped.startswith('• '):
            line = line.replace('• ', '● ', 1)
        elif stripped.startswith('- ') and not stripped.lower().startswith('- media') and not stripped.lower().startswith('- dsp'):
            # Convert dash bullets to ● (but not if it's part of a PL name)
            if len(stripped) > 2 and stripped[2].isupper():
                line = line.replace('- ', '● ', 1)
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

    # Stale data guard: reject tickets_export.json if exported_at date doesn't match today
    exported_at = export_data.get("exported_at", "")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if exported_at and not exported_at.startswith(today_str):
        print(f"[Step 2] ERROR: Stale data detected!")
        print(f"[Step 2] tickets_export.json was exported on {exported_at[:10]}, but today is {today_str}")
        print(f"[Step 2] Re-run hybrid_step1_export_jira.py to get fresh data")
        return None

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
            # If no explicit release_type, check labels for GA/FF on stories (not bugs)
            if status == "General Availability":
                for t in epic_tickets:
                    if t.get("issue_type", "").lower() != "bug":
                        labels = t.get("labels", [])
                        for label in labels:
                            label_lower = label.lower()
                            if "feature flag" in label_lower or label_lower == "ff":
                                status = "Feature Flag"
                                break
                        if status == "Feature Flag":
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
                        body_text += f"● {item}\n"
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
