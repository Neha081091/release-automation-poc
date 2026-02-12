#!/usr/bin/env python3
"""
HYBRID STEP 2: Process with Claude API (Run on Server)

This script reads the exported Jira tickets JSON and processes them
with Claude API to create polished release notes.

Uses the same model, system prompt, and quality settings as formatter.py
to ensure consistent output matching direct Claude AI conversation quality.

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
from formatter import CLAUDE_MODEL, CLAUDE_TEMPERATURE, RELEASE_NOTES_SYSTEM_PROMPT


def consolidate_with_claude(client, product: str, summaries: list) -> str:
    """Consolidate summaries into polished prose using Claude."""
    summaries_text = "\n".join([f"- {s}" for s in summaries])

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        temperature=CLAUDE_TEMPERATURE,
        system=RELEASE_NOTES_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""I need you to write a TL;DR summary for the "{product}" product line in our Daily Deployment Summary.

Here are the raw Jira ticket summaries that shipped in this release:

{summaries_text}

Write ONE polished prose sentence that captures all of the above. This sentence will appear in a \
"Key Deployments" section that PMOs read to get a quick overview of what's shipping.

Guidelines:
- Consolidate related tickets into coherent themes (e.g., if 10 tickets all say "Integrating X into Y repo", \
summarize as "X integration expanded across N repositories")
- Separate distinct themes with semicolons
- Use natural connectors like "with", "including", "alongside", "spanning"
- Focus on what users/stakeholders gain, not what developers did
- NO bullet points, NO category labels, NO product name prefix
- Should read like a polished executive summary when spoken aloud
- If there are security/vulnerability fixes, mention them clearly

Now write the TL;DR for {product}:"""
        }]
    )

    result = message.content[0].text.strip()
    if result.lower().startswith(product.lower()):
        result = result[len(product):].lstrip(" -:")
    # Remove wrapping quotes if present
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    return result


def consolidate_body_with_claude(client, product: str, sections: list) -> str:
    """Consolidate body sections into polished prose using Claude."""
    sections_text = ""
    for section in sections:
        items_list = "\n".join([f"- {item}" for item in section.get("items", [])])
        status = section.get("status", "")
        status_text = f" [Release Status: {status}]" if status else ""
        sections_text += f"\n__{section['title']}__{status_text}\n{items_list}\n"

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        temperature=CLAUDE_TEMPERATURE,
        system=RELEASE_NOTES_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""I need you to write the detailed body section for the "{product}" product line \
in our Daily Deployment Summary document.

Below are the raw Jira ticket summaries grouped by Epic. Transform them into polished, stakeholder-ready \
release notes.

Raw feature sections:
{sections_text}

Instructions:
- Keep each Epic as a separate section with its original title (use __Title__ format)
- Under each section, add a "Value Add:" header
- Write 1-3 polished bullet points (using * prefix) per section that explain the user/business value
- Each bullet should be a complete, well-written sentence (1-2 sentences max)
- If multiple tickets describe repetitive work (e.g., "Integrating X into repo-A", "Integrating X into repo-B", etc.), \
consolidate them into ONE meaningful bullet that captures the scope
- Translate developer-speak into stakeholder-friendly language
- After the bullets, include the release status on its own line if provided
- Do NOT invent features — only describe what the tickets actually cover
- Do NOT add extra sections or group epics together

Format each section exactly like this:
__Epic Name__

Value Add:

* Clear, stakeholder-friendly description of what shipped and why it matters.
* Another bullet if the epic has multiple distinct deliverables.

General Availability

Now write the body sections for {product}:"""
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
