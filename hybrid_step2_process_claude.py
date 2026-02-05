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


def consolidate_with_claude(client, product: str, summaries: list) -> str:
    """Consolidate summaries into polished TL;DR prose using Claude."""
    summaries_text = "\n".join([f"- {s}" for s in summaries])

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Write a 1-2 sentence TL;DR summary for executive review.

Product: {product}
Raw Items:
{summaries_text}

STRICT FORMAT RULES:
1. Write 1-2 flowing SENTENCES (not a list of items separated by semicolons)
2. Describe the IMPORTANT deployments and their PURPOSE/BENEFIT
3. Use past tense verbs: "updated", "added", "implemented", "integrated", "fixed"
4. Name SPECIFIC components, services, or features that were changed
5. NO bullet points, NO semicolons as separators
6. If multiple items, weave them into cohesive sentences
7. Keep it concise but descriptive - max 50 words
8. NEVER just list ticket titles - transform them into meaningful descriptions

BAD EXAMPLES (semicolon-separated list - DO NOT DO THIS):
- "InventoryTier dimension in Reporting; OA Enablement Flag for Deal IDs; Add Negotiated Bid Floor Value"
- "Integrating saarthi into di-agentic-service repo; Integrating saarthi into common-graphql repo"

GOOD EXAMPLES (prose sentences - DO THIS):
- "InventoryTier dimension now visible in Reporting for seats with enabled priority tiers; Open Auction enablement flag added for Deal IDs with new Negotiated Bid Floor field for internal auction dynamics"
- "Saarthi AI code reviewer integrated into di-agentic-service and common-graphql repos; Airflow upgrade compatibility with logical_date parameter support across 5 services"
- "Channel chart scale updated to use 4MM HCP Universe for better visualization of reach differences between EHR and other channels; improved scale readability when filters are applied"
- "Household frequency and recency capping support at Seat, Order, Campaign, and AdGroup levels with updated scheduling chart visualization; GraphQL schema and API updates for householding"

Output ONLY the 1-2 sentence summary:"""
        }]
    )

    result = message.content[0].text.strip()
    # Remove quotes if present
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    if result.lower().startswith(product.lower()):
        result = result[len(product):].lstrip(" -:")
    return result


def consolidate_body_with_claude(client, product: str, sections: list, release_version: str) -> str:
    """Consolidate body sections into executive-style prose release notes using Claude."""
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
            "content": f"""Transform these raw Jira sections into PROSE-STYLE executive release notes.

Product: {product}
Release Version: {release_version}

Raw Sections:
{sections_text}

STRICT FORMAT RULES:
1. NO markdown formatting (no **, no __, no backticks)
2. Use PLAIN TEXT only
3. Epic names as simple headers on their own line
4. For regular epics: include "Value Add:" header followed by a SINGLE PROSE SENTENCE
5. For "Bug Fixes" sections: use "Bug Fixes:" header followed by prose description
6. NO bullet points - write flowing prose sentences instead
7. Consolidate all items under an epic into ONE descriptive sentence
8. The sentence should explain WHAT was done and WHERE (name specific components/repos)
9. End each epic section with status tag on its own line: General Availability OR Feature Flag
10. One blank line between epic sections

TRANSFORMATION EXAMPLE:
Raw Input:
Epic: Saarthi Code Reviewer integration across major repositories
Items:
- Integrating saarthi into di-agentic-service repo
- Integrating saarthi into common-graphql repo

WRONG OUTPUT (bullet format - DO NOT DO THIS):
Saarthi Code Reviewer integration across major repositories
Value Add:
● Integrating saarthi into di-agentic-service repo
● Integrating saarthi into common-graphql repo
General Availability

CORRECT OUTPUT (prose format - DO THIS):
Saarthi Code Reviewer integration across major repositories
Value Add:
Saarthi AI code reviewer is now integrated into di-agentic-service and common-graphql repositories, enabling automated code review across more codebases.
General Availability

MORE EXAMPLES OF CORRECT PROSE FORMAT:

Epic: Migration of data pipelines from spring batch to airflow
Value Add:
REST API changes have been evaluated and prepared for Airflow upgrade across planner-service, patient-planner-service, event-consumer-service, di-match-service, and account-manager-service.
General Availability

Epic: Inventory Priority Tiers - Reporting
Value Add:
InventoryTier dimension is now available in Reporting for seats that have enabled priority tiers, providing visibility into inventory tier performance.
General Availability

IMPORTANT FOR BUG FIXES SECTIONS:
- When the epic name is "Bug Fixes", do NOT repeat "Bug Fixes" as a header
- Just use "Bug Fixes:" directly (no epic name above it)

Epic: Bug Fixes
WRONG OUTPUT (duplicated header - DO NOT DO THIS):
Bug Fixes
Bug Fixes:
Fixed alignment issues...

CORRECT OUTPUT (no duplication):
Bug Fixes:
Fixed alignment issues with save draft button in HCP planner and resolved query returning empty results when This Month date range was selected.
General Availability

Transform sections for {product}:"""
        }]
    )

    result = message.content[0].text.strip()

    # Post-process: Remove any bullet characters that Claude might have added
    lines = result.split('\n')
    processed_lines = []
    for line in lines:
        stripped = line.lstrip()
        # Remove bullet characters at start of line (convert to prose)
        if stripped.startswith('● '):
            line = line.replace('● ', '', 1)
        elif stripped.startswith('• '):
            line = line.replace('• ', '', 1)
        elif stripped.startswith('* '):
            line = line.replace('* ', '', 1)
        elif stripped.startswith('- '):
            line = line.replace('- ', '', 1)
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
        # Parse PL from fix version
        match = re.match(r'^(.+?)\s*\d{4}:\s*Release', fix_version)
        if match:
            pl = match.group(1).strip()
        else:
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
        for epic_name, epic_tickets in epics.items():
            for t in epic_tickets:
                if t.get("summary"):
                    summaries.append(t["summary"])

        if summaries:
            print(f"  Processing {pl}...")
            try:
                tldr_by_pl[pl] = consolidate_with_claude(client, pl, summaries)
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
    output_data = {
        "processed_at": datetime.now().isoformat(),
        "source_file": input_file,
        "release_summary": export_data.get("release_summary"),
        "ticket_count": len(tickets),
        "product_lines": list(grouped.keys()),
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
