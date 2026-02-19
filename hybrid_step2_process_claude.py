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
from formatter import CLAUDE_MODEL, CLAUDE_TEMPERATURE, RELEASE_NOTES_SYSTEM_PROMPT, parse_pl_from_fix_version


# ---------------------------------------------------------------------------
# Markdown stripping & post-processing
# ---------------------------------------------------------------------------

def _strip_markdown_from_body(text: str) -> str:
    """
    Strip markdown formatting from body text.

    NOTE: This function is kept for backward-compatibility and emergency
    fallback use. In the current architecture, Claude is instructed to
    OUTPUT markdown (#### [Epic](url), **Value Add**:, [GA]/[FF:] tags),
    and the google_docs_formatter.py parser handles that structure.
    Do NOT call this function in the normal processing pipeline.

    Handles:
    - Double-nested links: [[text](url)](url) -> text
    - Markdown links: [text](url) -> text
    - Bold markers: **text** -> text
    - Heading markers: #### text -> text
    - Duplicate consecutive lines
    """
    if not text:
        return text

    # Strip markdown links in two passes to handle brackets in text
    # Pass 1: Standard links [text](url) where text has no brackets
    text = re.sub(r'\[([^\[\]]+)\]\([^)]*\)', r'\1', text)
    # Pass 2: Links with inner brackets [[text] rest](url)
    text = re.sub(r'\[(\[[^\]]*\][^\[\]]*)\]\([^)]*\)', r'\1', text)

    # Replace **bold** markers -> plain text
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)

    # Replace #### heading markers -> plain text
    text = re.sub(r'^####\s+', '', text, flags=re.MULTILINE)

    # Remove duplicate consecutive non-blank lines
    lines = text.split('\n')
    deduped = []
    prev_stripped = None
    for line in lines:
        stripped = line.strip()
        if stripped and stripped == prev_stripped:
            continue
        deduped.append(line)
        prev_stripped = stripped

    return '\n'.join(deduped)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_description(raw: str, max_len: int = 300) -> str:
    """
    Clean a Jira description for LLM consumption.

    Strips acceptance criteria, SQL queries, code blocks, and other noise
    that causes the LLM to over-extract technical details.
    """
    if not raw:
        return ""
    desc = " ".join(raw.split())  # Normalize whitespace

    # Remove common noise sections (Acceptance Criteria, SQL, code, etc.)
    # Cut at the first occurrence of these markers
    for marker in [
        "Acceptance Criteria", "AC:", "Test Cases", "Test Plan",
        "SELECT ", "FROM ", "WHERE ", "INSERT ", "UPDATE ",
        "```", "query {", "Requirements / Scope",
        "Steps to Reproduce", "Expected Result", "Actual Result",
    ]:
        idx = desc.find(marker)
        if idx > 0:
            desc = desc[:idx].rstrip(" .:;-")

    if len(desc) > max_len:
        desc = desc[:max_len] + "..."
    return desc.strip()


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
            desc_clean = _clean_description(description)
            if desc_clean:
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
        # "Bug Fixes" is a synthetic key for bugs that don't belong to any feature epic.
        # Render it as a standalone bug section so Claude knows NOT to add Value Add / GA / FF.
        is_standalone_bugs = (epic_name == "Bug Fixes")

        if is_standalone_bugs:
            sections.append("=== Standalone Bug Fixes (NOT a feature epic) ===")
            sections.append("Note: These bugs do not belong to any feature epic.")
            sections.append("Write ONLY a Bug Fixes section for these — no Value Add, no availability tag.")
            sections.append("")
            for t in epic_tickets:
                key = t.get("key", "")
                summary = t.get("summary", "")
                description = (t.get("description") or "").strip()
                sections.append(f"  [{key}] (Bug) {summary}")
                if description and description.lower() != summary.lower():
                    desc_clean = _clean_description(description)
                    if desc_clean:
                        sections.append(f"    Context: {desc_clean}")
            sections.append("")
            continue

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
                desc_clean = _clean_description(description)
                if desc_clean:
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
            "content": f"""Write a TL;DR summary for the "{product}" product line ({fix_version}).

This product line has {len(all_tickets)} tickets across these epics: {epic_list}

Here is the FULL ticket data — read the descriptions carefully to understand what \
actually shipped, not just the Jira summary titles:

{ticket_context}

EXAMPLES of good TL;DR entries (note the concise, direct style):
   * DSP Core PL3 - Implemented Cora Agent Dynamic Renderer Framework enabling dynamic, \
agent-driven UI rendering within the Cora Chat experience. Users can now view conversational \
outputs in multiple formats including text, charts, cards, and tables. Also implemented \
advertiser-scoped conversation isolation to ensure data privacy when users switch between \
different advertiser contexts.
   * Audiences PL1 - Channel chart scale updated to use 4MM HCP Universe for better \
visualization of reach differences between EHR and other channels.
   * Helix PL3 - Migrated STM impression and Outcomes tiered reporting data pipelines from \
Spring Batch to Python/Airflow for improved performance, maintainability, and better \
workflow visualization.

Guidelines:
- Write 2-4 sentences. Each sentence covers one major epic/theme.
- Draw from the ticket descriptions (Context field), not just the summaries — descriptions contain the real value.
- State WHAT shipped and WHY it matters to users. One sentence per theme, 15-30 words each.
- Use plain language a PMO can understand in 5 seconds.
- Do NOT chain multiple themes with semicolons into one long run-on sentence — use separate sentences.
- Do NOT include internal repo names or low-level implementation details.
- Do NOT include the product name prefix — output ONLY the description part after the dash.
- If there are bug fixes, mention them briefly at the end (e.g., "Fixed Peer39 Usage Report to correctly display category IDs.").

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
            "content": f"""Write the detailed body section for "{product}" ({fix_version}). \
There are {total} tickets across {len(epics)} epics.

Here is the FULL ticket data grouped by Epic. Each section starts with "=== Epic: <name> ===" \
or "=== Standalone Bug Fixes ===". Read BOTH the ticket summary AND the Context (description) \
for every ticket — the Context field contains the real business value that must appear in the bullets.

{epic_context}

--- OUTPUT FORMAT ---

For each FEATURE epic (NOT the Standalone Bug Fixes section), output EXACTLY this structure:

#### [Epic Name](Epic URL)
**Value Add**:
- <Sentence drawn from ticket summary + description explaining what users gain> [GA]
- <Another bullet for each distinct user benefit covered by this epic> [GA]

Where:
- The Epic URL comes from the "Epic URL:" line in the context above (leave empty if none: #### [Epic Name]())
- [GA] = General Availability; [FF: flag_name] = Feature Flag (use the flag name from labels if available)
- Append [GA] or [FF: flag_name] at the END of EACH Value Add bullet (not on a separate line)
- Use 2-4 bullets per epic

If the feature epic also contains Bug tickets, add AFTER the last Value Add bullet:
**Bug Fixes**:
- Fixed [specific problem] — [what users can now do]

For the "=== Standalone Bug Fixes ===" section, output ONLY:
**Bug Fixes**:
- Fixed [specific problem] — [what users can now do]
(No epic title, no **Value Add**, no [GA]/[FF] for this section.)

--- EXAMPLE of the desired quality ---

#### [DSP | UI | DSP PL5 | Apollo V3 migration](https://deepintent.atlassian.net/browse/DI-1234)
**Value Add**:
- Improved application performance and data-fetching efficiency through Apollo V3 migration [GA]
- Modernised codebase with class-based components converted to functional components, enhancing maintainability and developer experience [GA]

#### [Account Manager Revamp - Bulk Actions](https://deepintent.atlassian.net/browse/DI-5678)
**Value Add**:
- Super Admin users can now view detailed seat assignments for other Super Admin users [FF: bulk-actions-v2]
- Enhanced user management with search functionality across seat assignment lists [FF: bulk-actions-v2]
- Enables Super Admin users to demote another Super Admin to a normal user role from the details page [FF: bulk-actions-v2]

**Bug Fixes**:
- Fixed Peer39 Usage Report to correctly display category IDs — users can now properly identify categories within the report

--- CRITICAL RULES ---
1. CONTENT: Each Value Add bullet MUST be drawn from the ticket's Context (description) field, not just the summary.
2. ISSUE TYPE: A ticket marked "(Bug)" must ONLY appear in a **Bug Fixes** section, never in **Value Add**.
   A ticket marked "(Story)" or "(Task)" must ONLY appear in **Value Add**, never in **Bug Fixes**.
3. EPIC ISOLATION: Keep each epic as its own separate section. NEVER mix content across epics.
4. AVAILABILITY: Append [GA] or [FF: flag_name] at the END of each Value Add bullet. NEVER on a separate line. NEVER after Bug Fixes.
5. SKIP garbage bugs: If a bug has no meaningful description and its summary is just a raw Jira tag \
(e.g. "DSP | UI | ticket-123"), skip that bug entirely.
6. FORMAT: Use exactly #### [Epic Name](url), **Value Add**:, - bullets, **Bug Fixes**: as shown above.
7. START immediately with the first #### epic heading — no introduction or conclusion.

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
                "content": f"""Review and polish these draft release notes for {product}. \
Your goal is QUALITY and CLARITY, not aggressive shortening.

{body_text}

Apply only these edits:

1. READABILITY: If a bullet is vague or generic (e.g., "Improved performance"), expand it using \
   context already implied by the epic name and other bullets — do NOT add invented facts. \
   If a bullet is clear and meaningful at 30+ words, leave it as-is.

2. GARBAGE REMOVAL: Delete ONLY bullet lines (starting with - or *) that contain ZERO user-facing \
   information — e.g., "- Fixed --", "- Fixed DSP | UI | DSP-123 |" with no further description. \
   NEVER delete or modify an epic section title (#### heading). Epic titles are always valid.

3. ISSUE TYPE ENFORCEMENT: If a Value Add bullet describes a bug fix (starts with "Fixed"), move it \
   to the **Bug Fixes** section of that epic. If a **Bug Fixes** bullet is actually a feature, move it to \
   **Value Add** with a [GA] tag. Do NOT invent a **Bug Fixes** section if none existed.

4. CONSOLIDATION: Merge Value Add bullets that describe the exact same user benefit from different \
   angles into ONE bullet. NEVER merge bullets from separate epics.

5. STRUCTURE: Preserve this EXACT markdown format — do NOT strip ####, ** bold, or [GA]/[FF:] tags:
   #### [Epic Name](url)
   **Value Add**:
   - Bullet explaining user benefit [GA]
   - Another distinct bullet [FF: flag_name]

   **Bug Fixes**:
   - Fixed [specific problem] — [what users can now do]

6. AVAILABILITY TAGS: Every Value Add bullet must end with [GA] or [FF: flag_name]. \
   NEVER add availability tags after **Bug Fixes** bullets. \
   If a bullet is missing its tag, infer it from the epic context (default to [GA] for stories/tasks).

7. Do NOT add introductions, conclusions, or any text outside the epic sections.

Output ONLY the final polished release notes."""
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
    epic_urls_by_pl = defaultdict(dict)

    for ticket in tickets:
        # Skip Deployment Tracker tickets — they are internal and should not appear in release notes
        issue_type = ticket.get("issue_type", "").lower()
        if "deployment" in issue_type and "tracker" in issue_type:
            continue

        fix_version = ticket.get("fix_version") or ""

        # Skip tickets from Hotfix fix versions
        if "hotfix" in fix_version.lower():
            continue
        # Parse PL from fix version using shared helper (handles "YYYY : Release" with space)
        pl = parse_pl_from_fix_version(fix_version)

        # Determine epic name.
        # RULE: Bugs always go to "Bug Fixes" — epic is ignored for bugs.
        # Non-bug tickets without an epic go to "General Enhancements".
        raw_epic = ticket.get("epic_name")
        if issue_type == "bug":
            epic_name = "Bug Fixes"
        elif raw_epic:
            epic_name = raw_epic
        else:
            epic_name = "General Enhancements"
        grouped[pl][epic_name].append(ticket)

        if pl not in fix_versions:
            fix_versions[pl] = fix_version
        if pl not in fix_version_urls and ticket.get("fix_version_url"):
            fix_version_urls[pl] = ticket["fix_version_url"]
        if epic_name not in epic_urls_by_pl.get(pl, {}) and ticket.get("epic_url"):
            epic_urls_by_pl[pl][epic_name] = ticket["epic_url"]

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
    # Parse short release version from fix_version (e.g., "DSP Core PL1 2026: Release 3.0" -> "Release 3.0")
    release_versions = {}
    for pl, fv in fix_versions.items():
        version_match = re.search(r'(Release\s*[\d.]+)', fv)
        if version_match:
            release_versions[pl] = version_match.group(1)

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
        "release_versions": release_versions,
        "fix_version_urls": fix_version_urls,
        "epic_urls_by_pl": dict(epic_urls_by_pl),
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
