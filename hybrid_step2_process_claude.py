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
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import anthropic


def consolidate_with_claude(client, product: str, summaries: list) -> str:
    """Consolidate summaries into polished prose using Claude."""
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

Now consolidate for {product}:"""
        }]
    )

    result = message.content[0].text.strip()
    if result.lower().startswith(product.lower()):
        result = result[len(product):].lstrip(" -:")
    return result


def consolidate_body_with_claude(client, product: str, sections: list) -> str:
    """Consolidate body sections into polished prose using Claude."""
    sections_text = ""
    for section in sections:
        items_list = "\n".join([f"- {item}" for item in section.get("items", [])])
        status = section.get("status", "")
        status_text = f" ({status})" if status else ""
        sections_text += f"\n__{section['title']}__{status_text}\n{items_list}\n"

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""Consolidate these raw feature sections into polished, flowing prose bullet points.

Product: {product}

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

   Status Flag (if applicable)

8. Do NOT abbreviate or use technical jargon - explain clearly for stakeholders

Now consolidate for {product}:"""
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
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))

    for ticket in tickets:
        fix_version = ticket.get("fix_version", "")
        # Parse PL from fix version
        import re
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

    # Process body sections for each PL
    print("\n[Step 2] Processing body sections...")
    body_by_pl = {}
    for pl, epics in grouped.items():
        sections = []
        for epic_name, epic_tickets in epics.items():
            items = [t["summary"] for t in epic_tickets if t.get("summary")]
            status = ""
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
            try:
                body_by_pl[pl] = consolidate_body_with_claude(client, pl, sections)
                print(f"  ✅ {pl} - consolidated")
            except Exception as e:
                print(f"  ❌ {pl} - error: {e}")
                # Fallback
                body_text = ""
                for s in sections:
                    body_text += f"__{s['title']}__\n\nValue Add:\n"
                    for item in s['items']:
                        body_text += f"   * {item}\n"
                    if s['status']:
                        body_text += f"\n{s['status']}\n\n"
                body_by_pl[pl] = body_text

    # Export processed notes
    output_data = {
        "processed_at": datetime.now().isoformat(),
        "source_file": input_file,
        "release_summary": export_data.get("release_summary"),
        "ticket_count": len(tickets),
        "product_lines": list(grouped.keys()),
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
