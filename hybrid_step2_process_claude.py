#!/usr/bin/env python3
"""
HYBRID STEP 2: Process with Claude API (Run on Server)

This is the PRIMARY release notes processor. It uses Claude as an extensive
AI writer by feeding it full ticket context (summaries, descriptions, issue types,
priorities, components, labels, assignees) — the same level of detail you'd paste
into a direct Claude AI conversation.

Processing pipeline:
  1. Load & group tickets by Product Line / Epic
  2. Per-PL TL;DR generation (full ticket context)
  3. Per-PL body section generation (full ticket context)
  4. Release-wide executive overview generation
  5. Final quality review pass

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
from formatter import CLAUDE_MODEL, CLAUDE_TEMPERATURE, RELEASE_NOTES_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_full_ticket_context(tickets: list) -> str:
    """
    Build a rich, human-readable context block from full ticket data.

    This is the key difference from the old approach — instead of only passing
    bare summaries, we give Claude the same depth of information a human would
    paste into a Claude AI chat window.
    """
    lines = []
    for t in tickets:
        key = t.get("key", "")
        summary = t.get("summary", "")
        description = (t.get("description") or "").strip()
        issue_type = t.get("issue_type", "")
        priority = t.get("priority", "")
        status = t.get("status", "")
        release_type = t.get("release_type") or ""
        assignee = t.get("assignee") or "Unassigned"
        components = ", ".join(t.get("components", [])) or "—"
        labels = ", ".join(t.get("labels", [])) or "—"
        story_points = t.get("story_points") or "—"

        lines.append(f"[{key}] {summary}")
        lines.append(f"  Type: {issue_type} | Priority: {priority} | Status: {status}")
        lines.append(f"  Components: {components} | Labels: {labels} | Points: {story_points}")
        lines.append(f"  Assignee: {assignee}")
        if release_type:
            lines.append(f"  Release Type: {release_type}")
        if description and description.lower() != summary.lower():
            # Truncate very long descriptions but keep enough for context
            desc_clean = " ".join(description.split())
            if len(desc_clean) > 600:
                desc_clean = desc_clean[:600] + "..."
            lines.append(f"  Description: {desc_clean}")
        lines.append("")

    return "\n".join(lines)


def _build_epic_sections_context(epics: dict) -> str:
    """
    Build structured context grouped by Epic, with full ticket details.

    Includes epic URLs, fix version URLs, and labels so Claude can:
    - Create hyperlinks for epic names
    - Determine GA/FF status from story labels
    - Separate bugs from stories for the Bug Fixes section
    """
    sections = []
    for epic_name, epic_tickets in epics.items():
        # Get epic URL from first ticket that has it
        epic_url = ""
        for t in epic_tickets:
            if t.get("epic_url"):
                epic_url = t["epic_url"]
                break

        sections.append(f"=== Epic: {epic_name} ===")
        if epic_url:
            sections.append(f"Epic URL: {epic_url}")

        # Determine release status for this epic from labels and release_type
        # Only derive availability from stories/tasks — bugs should NOT carry GA/FF tags
        statuses = set()
        for t in epic_tickets:
            if t.get("issue_type", "").lower() == "bug":
                continue
            rt = t.get("release_type")
            if rt:
                statuses.add(rt)
            # For stories/tasks, check labels for GA/FF
            if t.get("issue_type", "").lower() in ("story", "task"):
                labels = t.get("labels", [])
                for label in labels:
                    label_lower = label.lower()
                    if "general" in label_lower and "availability" in label_lower:
                        statuses.add("General Availability")
                    elif "feature" in label_lower and "flag" in label_lower:
                        statuses.add("Feature Flag")
                    elif label_lower in ("ga", "general_availability"):
                        statuses.add("General Availability")
                    elif label_lower in ("ff", "feature_flag", "featureflag"):
                        statuses.add("Feature Flag")
        if statuses:
            sections.append(f"Release Status: {', '.join(statuses)}")

        sections.append(f"Ticket Count: {len(epic_tickets)}")
        sections.append("")

        for t in epic_tickets:
            key = t.get("key", "")
            summary = t.get("summary", "")
            description = (t.get("description") or "").strip()
            issue_type = t.get("issue_type", "")
            labels = ", ".join(t.get("labels", [])) or "—"

            sections.append(f"  [{key}] ({issue_type}) {summary}")
            sections.append(f"    Labels: {labels}")
            if description and description.lower() != summary.lower():
                desc_clean = " ".join(description.split())
                if len(desc_clean) > 500:
                    desc_clean = desc_clean[:500] + "..."
                sections.append(f"    Context: {desc_clean}")

        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Claude API calls
# ---------------------------------------------------------------------------

def generate_tldr_with_claude(client, product: str, fix_version: str,
                              epics: dict) -> str:
    """
    Generate a polished TL;DR for one Product Line using full ticket context.

    Output matches the "Key Deployments" sub-bullet format from the manual prompt:
      * DSP Core PL3 - Feature description with user impact; second theme with details

    Unlike the old approach that only passed summaries, this sends descriptions,
    issue types, priorities, and labels — giving Claude the same information
    you'd paste into a direct conversation.
    """
    # Flatten all tickets for this PL
    all_tickets = []
    for epic_tickets in epics.values():
        all_tickets.extend(epic_tickets)

    ticket_context = _build_full_ticket_context(all_tickets)
    epic_list = ", ".join(epics.keys())

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        temperature=CLAUDE_TEMPERATURE,
        system=RELEASE_NOTES_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""Release Notes Prompt (with TLDR)

Find today's release ticket with summary "Release [today's date]" (e.g., "Release 13th Oct 2025"). Create a Daily Deployment Summary with this EXACT formatting:

Daily Deployment Summary: [Date in format "13th Oct 2025"]
------------------TL;DR:------------------
Key Deployments: [List deployments, e.g., "DSP PL2 and DSP PL4"]
   * {product} - [brief description of what shipped, focusing on user/business value]

Generate the TL;DR entry for the "{product}" product line ({fix_version}) using the ticket data below.

This product line has {len(all_tickets)} tickets across these epics: {epic_list}

Here is the FULL ticket data — read the descriptions carefully to understand what \
actually shipped, not just the Jira summary titles:

{ticket_context}

EXAMPLE format (from a real deployment summary):
   * Audiences PL1 - Channel chart scale updated to use 4MM HCP Universe for better \
visualization of reach differences between EHR and other channels; improved scale \
readability when filters are applied
   * DSP Core PL2 - Unique Reach now auto-enabled as default primary goal for new ad \
groups and templates; Outcomes reports with 'REQUESTED' state ad groups now visible \
(~50 analyses); SmartBid template added to Reporting Listing V2

Guidelines:
- Read the descriptions to understand the real user impact — don't just rephrase the titles
- Consolidate related tickets into coherent themes
- Separate distinct themes with semicolons
- Focus on what users and stakeholders gain, not what developers built
- If there are security or vulnerability fixes, call them out explicitly
- Keep it concise but informative — this is a quick-scan summary for leadership
- Do NOT include the product name prefix — output ONLY the description part after the dash

Output ONLY the summary description (without the product name prefix) — nothing else."""
        }]
    )

    result = message.content[0].text.strip()
    # Clean up common LLM artifacts
    if result.lower().startswith(product.lower()):
        result = result[len(product):].lstrip(" -:")
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    # Remove leading dash/bullet if Claude added one
    result = result.lstrip("*- ").strip()
    return result


def generate_body_with_claude(client, product: str, fix_version: str,
                              epics: dict) -> str:
    """
    Generate the detailed body section for one Product Line using full ticket context.

    Sends complete ticket data including descriptions so Claude can write
    stakeholder-quality prose — the same output you'd get in a direct Claude AI chat.

    Output format matches the exact structure used in the manual Claude AI prompt:
    - Epic names as markdown hyperlinks: #### [Epic Name](url)
    - **Value Add**: bold with colon
    - Flat bullet points (no sub-bullets)
    - Separate Bug Fixes section for bugs
    - GA/FF availability tags after value-adds
    """
    epic_context = _build_epic_sections_context(epics)

    # Count total tickets
    total = sum(len(tix) for tix in epics.values())

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        temperature=CLAUDE_TEMPERATURE,
        system=RELEASE_NOTES_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""Release Notes Prompt (with TLDR)

Find today's release ticket with summary "Release [today's date]" (e.g., "Release 13th Oct 2025"). Create a Daily Deployment Summary with this EXACT formatting:

Daily Deployment Summary: [Date in format "13th Oct 2025"]
------------------TL;DR:------------------
Key Deployments: [List deployments, e.g., "DSP PL2 and DSP PL4"]

------------------DSP------------------
For each Product Line:
{product}: {fix_version} (Make "{fix_version}" a blue hyperlink to the fix version)
#### [Epic Name](epic_url)
**Value Add**:
* List each value-add as a separate bullet point
* Extract from ticket summaries and descriptions
* Focus on business value and user benefits
* No sub-bullets, keep flat structure
After all value-adds for an epic, add availability tag: 'Feature Flag' or 'General Availability'

Rules:
* Use blue hyperlinks for Release versions and Epic names
* "Value Add:" should be bold
* Keep bullet points simple and flat (no nested bullets)
* Add blank line between different PLs
* Exclude the release ticket itself from the summary
* For stories (not bugs), check labels field for GA/FF information

Generate professional, concise summaries emphasizing user benefits.

Now write the detailed body section for "{product}" ({fix_version}) using the ticket data below. There are {total} tickets across {len(epics)} epics.

Here is the FULL ticket data grouped by Epic. Read the descriptions carefully — they \
contain the user stories, acceptance criteria, and business rationale that you should \
reflect in the release notes:

{epic_context}

Write polished, stakeholder-ready release notes following this EXACT structure for EACH epic:

#### [Epic Name](epic_url)
**Value Add**:
* A clear, stakeholder-friendly sentence explaining what shipped and why it matters.
* Another bullet if the epic has multiple distinct deliverables.
General Availability

OR for multiple value-adds:

#### [Epic Name](epic_url)
**Value Add**:
* First value-add bullet explaining user benefit
* Second value-add bullet explaining user benefit
Feature Flag

If the epic has Bug tickets, add them in a separate section AFTER the value-adds:

**Bug Fixes:**
* Fixed issue where [description of what was broken and what was fixed]
* Fixed [another bug description]

EXAMPLE of a complete epic section:

#### [Campaigns List Page V3](https://deepintent.atlassian.net/browse/DI-12345)
**Value Add**:
* Improved campaign listing by allowing top bar metrics selection independently from listing columns
* Enhanced audit log for PG Ad Groups by removing inapplicable bid fields for improved clarity
General Availability

**Bug Fixes:**
* Fixed issue where Add frequency button disappears when directly deleting existing frequency on ad-group quickview
* Fixed null date display in tooltip when hovering on graph datapoints in Goal Widget

Critical rules:
- Use the Epic URL provided in the data to create markdown hyperlinks: [Epic Name](epic_url)
- "**Value Add**:" must be bold (use ** markdown) followed by a colon
- Keep bullet points simple and FLAT — no sub-bullets or nested lists
- Separate Bug tickets (issue_type=Bug) into a "**Bug Fixes:**" section after value-adds
- For story/task tickets (not bugs), check the Labels field for GA/FF information
- Add the availability tag (General Availability or Feature Flag) on its own line after value-add bullets ONLY for stories/tasks
- NEVER add availability tags (General Availability or Feature Flag) to the Bug Fixes section
- If multiple tickets in an epic describe the same work across different repos, consolidate into ONE bullet
- Keep each Epic as a SEPARATE section — do NOT merge epics together
- Draw from ticket descriptions to explain WHY the change matters, not just WHAT changed
- Translate developer jargon into language a PMO or executive would understand
- Do NOT invent features or benefits not supported by the ticket data
- Do NOT add an introduction or conclusion — jump straight into the first epic section
- Exclude the release ticket itself from the summary

Output the formatted sections now:"""
        }]
    )

    return message.content[0].text.strip()


def generate_release_overview_with_claude(client, release_summary: str,
                                          all_pls: dict,
                                          tldr_by_pl: dict) -> str:
    """
    Generate a release-wide executive overview by reviewing all PL summaries.

    This is a final synthesis pass — Claude reads all the per-PL TLDRs and
    produces a 2-3 sentence executive overview of the entire release.
    """
    # Build context: PL names, ticket counts, and TLDRs
    pl_context_lines = []
    for pl, epics in all_pls.items():
        count = sum(len(tix) for tix in epics.values())
        tldr = tldr_by_pl.get(pl, "")
        pl_context_lines.append(f"- {pl} ({count} tickets): {tldr}")
    pl_context = "\n".join(pl_context_lines)

    total_tickets = sum(
        sum(len(tix) for tix in epics.values())
        for epics in all_pls.values()
    )

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        temperature=CLAUDE_TEMPERATURE,
        system=RELEASE_NOTES_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"""I need a brief executive overview for our Daily Deployment Summary.

Release: {release_summary}
Total: {total_tickets} tickets across {len(all_pls)} product lines

Per-PL summaries:
{pl_context}

Write a 2-3 sentence executive overview that captures the most impactful themes across \
the ENTIRE release. This sits at the very top of the document before the TL;DR section. \
It should give a CTO or VP-level reader an instant understanding of what's shipping today.

Guidelines:
- Highlight the 2-3 most significant themes across all PLs
- If there's a common thread (e.g., multiple PLs doing security work), call it out
- Mention the breadth: "{len(all_pls)} product lines, {total_tickets} total changes"
- Keep it to 2-3 sentences maximum
- Professional, confident tone

Output ONLY the overview paragraph — nothing else."""
        }]
    )

    result = message.content[0].text.strip()
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    return result


def review_and_polish_with_claude(client, product: str, body_text: str) -> str:
    """
    Final quality review pass — Claude reviews its own body output for consistency,
    readability, and formatting correctness.

    Validates against the exact format used in the manual Claude AI prompt:
    - #### [Epic Name](url) headings
    - **Value Add**: bold format
    - Flat bullets only
    - Separate Bug Fixes section
    - GA/FF availability tags
    """
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        temperature=CLAUDE_TEMPERATURE,
        system=RELEASE_NOTES_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"""Here are the draft release notes I wrote for {product}:

{body_text}

Review and fix ONLY if there are actual issues. The format must match this exact structure:

#### [Epic Name](url)
**Value Add**:
* Flat bullet describing user value
* Another flat bullet if needed
General Availability

**Bug Fixes:**
* Fixed issue where [description]

Validation checklist:
1. Formatting: Every epic must use #### [Epic Name](url) as the heading
2. "**Value Add**:" must be bold (wrapped in **) followed by a colon
3. Bullet points must be flat — NO sub-bullets or nested lists
4. Bug tickets must be in a separate "**Bug Fixes:**" section (not mixed with value-adds)
5. Availability tags (General Availability / Feature Flag) must be on their own line after value-add bullets ONLY — NEVER after Bug Fixes
6. Clarity: Each bullet should be a complete sentence a PMO can understand
7. Accuracy: Bullets should not claim features or benefits not supported by the ticket data
8. Consolidation: Repetitive items (e.g., same integration across repos) should be one bullet
9. No extra sections, introductions, or conclusions

If the draft is already good, return it unchanged. Output ONLY the final release notes."""
            }
        ]
    )

    return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------

def process_tickets_with_claude():
    """
    Primary Claude-powered release notes processor.

    Pipeline:
      1. Load & group tickets by PL / Epic
      2. Generate TL;DR per PL (with full ticket context)
      3. Generate body sections per PL (with full ticket context)
      4. Review & polish each body section
      5. Generate release-wide executive overview
      6. Export everything to processed_notes.json
    """
    print("=" * 60)
    print("  HYBRID STEP 2: Process with Claude API (Primary Processor)")
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

    # Validate that export data is from today (prevent stale data processing)
    exported_at = export_data.get("exported_at", "")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if exported_at and not exported_at.startswith(today_str):
        print(f"[Step 2] ERROR: tickets_export.json is stale (exported {exported_at[:10]}, today is {today_str})")
        print("[Step 2] Re-run hybrid_step1_export_jira.py to get fresh data")
        return None

    tickets = export_data.get("tickets", [])
    release_summary = export_data.get("release_summary", "")
    print(f"[Step 2] Loaded {len(tickets)} tickets from {input_file}")
    print(f"[Step 2] Release: {release_summary}")
    print(f"[Step 2] Model: {CLAUDE_MODEL} | Temperature: {CLAUDE_TEMPERATURE}")

    # Initialize Claude client
    client = anthropic.Anthropic(api_key=api_key)
    print("[Step 2] Claude API client initialized")

    # -----------------------------------------------------------------------
    # Step 1: Group tickets by Product Line → Epic
    # -----------------------------------------------------------------------
    grouped = defaultdict(lambda: defaultdict(list))
    fix_versions = {}
    fix_version_urls = {}
    epic_urls = {}

    for ticket in tickets:
        # Skip Deployment Tracker tickets — they are internal and should not appear in release notes
        issue_type = ticket.get("issue_type", "").lower()
        if "deployment" in issue_type and "tracker" in issue_type:
            continue

        fix_version = ticket.get("fix_version", "")

        # Skip tickets from Hotfix fix versions
        if "hotfix" in fix_version.lower():
            continue
        # Parse PL from fix version
        match = re.match(r'^(.+?)\s*\d{4}:\s*Release', fix_version)
        if match:
            pl = match.group(1).strip()
        else:
            match = re.match(r'^(.+?):\s*Release', fix_version)
            pl = match.group(1).strip() if match else "Other"

        epic_name = ticket.get("epic_name") or "Uncategorized"
        grouped[pl][epic_name].append(ticket)

        if pl not in fix_versions:
            fix_versions[pl] = fix_version
        if pl not in fix_version_urls and ticket.get("fix_version_url"):
            fix_version_urls[pl] = ticket["fix_version_url"]
        if epic_name not in epic_urls and ticket.get("epic_url"):
            epic_urls[epic_name] = ticket["epic_url"]

    print(f"[Step 2] Grouped into {len(grouped)} product lines:")
    for pl, epics in grouped.items():
        ticket_count = sum(len(tix) for tix in epics.values())
        print(f"  - {pl}: {len(epics)} epics, {ticket_count} tickets")

    # -----------------------------------------------------------------------
    # Step 2: Generate TL;DR for each PL (full ticket context)
    # -----------------------------------------------------------------------
    print("\n[Step 2] Generating TL;DR summaries (with full ticket context)...")
    tldr_by_pl = {}
    for pl, epics in grouped.items():
        fv = fix_versions.get(pl, "")
        print(f"  Processing {pl}...")
        try:
            tldr_by_pl[pl] = generate_tldr_with_claude(client, pl, fv, epics)
            print(f"  -> {pl}: {tldr_by_pl[pl][:80]}...")
        except Exception as e:
            print(f"  ERROR {pl}: {e}")
            # Fallback: join summaries
            all_summaries = []
            for epic_tickets in epics.values():
                for t in epic_tickets:
                    if t.get("summary"):
                        all_summaries.append(t["summary"])
            tldr_by_pl[pl] = "; ".join(all_summaries)

    # -----------------------------------------------------------------------
    # Step 3: Generate body sections for each PL (full ticket context)
    # -----------------------------------------------------------------------
    print("\n[Step 2] Generating body sections (with full ticket context)...")
    body_by_pl = {}
    for pl, epics in grouped.items():
        fv = fix_versions.get(pl, "")
        print(f"  Processing {pl}...")
        try:
            body_by_pl[pl] = generate_body_with_claude(client, pl, fv, epics)
            print(f"  -> {pl}: generated ({len(body_by_pl[pl])} chars)")
        except Exception as e:
            print(f"  ERROR {pl}: {e}")
            # Fallback to raw formatting matching new format
            body_text = ""
            for epic_name, epic_tickets in epics.items():
                epic_url = ""
                for t in epic_tickets:
                    if t.get("epic_url"):
                        epic_url = t["epic_url"]
                        break
                body_text += f"#### [{epic_name}]({epic_url})\n"
                body_text += "**Value Add**:\n"
                bug_fixes = []
                for t in epic_tickets:
                    if t.get("summary"):
                        if t.get("issue_type", "").lower() == "bug":
                            bug_fixes.append(t["summary"])
                        else:
                            body_text += f"* {t['summary']}\n"
                # Check for release type (only from stories/tasks, not bugs)
                for t in epic_tickets:
                    if t.get("issue_type", "").lower() != "bug" and t.get("release_type"):
                        body_text += f"{t['release_type']}\n"
                        break
                if bug_fixes:
                    body_text += "\n**Bug Fixes:**\n"
                    for fix in bug_fixes:
                        body_text += f"* {fix}\n"
                body_text += "\n"
            body_by_pl[pl] = body_text

    # -----------------------------------------------------------------------
    # Step 4: Review & polish each body section
    # -----------------------------------------------------------------------
    print("\n[Step 2] Running quality review pass...")
    for pl in body_by_pl:
        print(f"  Reviewing {pl}...")
        try:
            body_by_pl[pl] = review_and_polish_with_claude(client, pl, body_by_pl[pl])
            print(f"  -> {pl}: reviewed ({len(body_by_pl[pl])} chars)")
        except Exception as e:
            print(f"  Review skipped for {pl}: {e}")

    # -----------------------------------------------------------------------
    # Step 5: Generate release-wide executive overview
    # -----------------------------------------------------------------------
    print("\n[Step 2] Generating release-wide executive overview...")
    release_overview = ""
    try:
        release_overview = generate_release_overview_with_claude(
            client, release_summary, grouped, tldr_by_pl
        )
        print(f"  -> Overview: {release_overview[:100]}...")
    except Exception as e:
        print(f"  Overview skipped: {e}")

    # -----------------------------------------------------------------------
    # Step 6: Export
    # -----------------------------------------------------------------------
    output_data = {
        "processed_at": datetime.now().isoformat(),
        "source_file": input_file,
        "release_summary": release_summary,
        "model": CLAUDE_MODEL,
        "temperature": CLAUDE_TEMPERATURE,
        "ticket_count": len(tickets),
        "product_lines": list(grouped.keys()),
        "release_overview": release_overview,
        "tldr_by_pl": tldr_by_pl,
        "body_by_pl": body_by_pl,
        "fix_versions": fix_versions,
        "fix_version_urls": fix_version_urls,
        "epic_urls": epic_urls,
        "grouped_data": {
            pl: {
                epic: [t["key"] for t in tickets]
                for epic, tickets in epics.items()
            }
            for pl, epics in grouped.items()
        }
    }

    output_file = "processed_notes.json"
    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n[Step 2] EXPORTED to: {output_file}")
    print(f"[Step 2] Claude API calls made: {len(grouped) * 3 + 1}")
    print(f"  - {len(grouped)} TL;DR calls")
    print(f"  - {len(grouped)} body section calls")
    print(f"  - {len(grouped)} review/polish calls")
    print(f"  - 1 executive overview call")
    print("\n" + "=" * 60)
    print("  NEXT: Copy processed_notes.json to Mac and run:")
    print("  python hybrid_step3_update_docs.py")
    print("=" * 60)

    return output_file


if __name__ == "__main__":
    process_tickets_with_claude()
