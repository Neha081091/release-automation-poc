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
            "content": f"""Consolidate these raw Jira ticket summaries into a concise TL;DR for executive review.

Product: {product}
Raw Summaries:
{summaries_text}

Rules:
1. Write 2-3 SHORT sentences maximum
2. Focus on major features and business impact
3. Use semicolons to separate different feature areas
4. NO bullet points or lists
5. Use present tense and action-oriented language
6. Be concise - executives skim this section
7. Output ONLY the consolidated prose (no product name prefix)

Example format:
"Order listing improvements with multi-select filtering and persistent preferences; forecasting enhancements with deal/exchange validation"

Now consolidate for {product}:"""
        }]
    )

    result = message.content[0].text.strip()
    if result.lower().startswith(product.lower()):
        result = result[len(product):].lstrip(" -:")
    return result


def consolidate_body_with_claude(client, product: str, sections: list, release_version: str) -> str:
    """Consolidate body sections into executive-style release notes using Claude."""
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
            "content": f"""Transform these raw Jira sections into executive-style release notes.

Product: {product}
Release Version: {release_version}

Raw Sections:
{sections_text}

STRICT FORMAT RULES:
1. NO markdown formatting (no **, no __, no backticks)
2. Use PLAIN TEXT only
3. Epic names as simple headers (no formatting)
4. Always include "Value Add:" header before bullets
5. Use bullet character "•" (not *, not -)
6. 2-4 action-oriented bullets per epic
7. Each bullet is ONE complete sentence with period
8. End each epic section with status tag on its own line: General Availability OR Feature Flag
9. NO parentheses around status tags
10. One blank line between epic sections

EXACT OUTPUT FORMAT:
Epic Name Here

Value Add:
• First benefit statement explaining user value and impact.
• Second benefit statement with clear business context.
• Third benefit statement if needed.

General Availability

Next Epic Name

Value Add:
• Benefit statement here.

Feature Flag

Transform the sections for {product} using this EXACT format:"""
        }]
    )

    return message.content[0].text.strip()


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

        epic_name = ticket.get("epic_name") or "Uncategorized"
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

    # Extract release versions per PL
    release_versions = {}
    for pl, epics in grouped.items():
        for epic_name, epic_tickets in epics.items():
            for t in epic_tickets:
                fix_version = t.get("fix_version", "")
                # Extract version number (e.g., "Release 3.0" from "DSP Core PL1 2026: Release 3.0")
                version_match = re.search(r'Release\s*([\d.]+)', fix_version)
                if version_match:
                    release_versions[pl] = f"Release {version_match.group(1)}"
                    break
            if pl in release_versions:
                break
        if pl not in release_versions:
            release_versions[pl] = "Release 1.0"

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
