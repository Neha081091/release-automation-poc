"""
Slack Socket Mode Handler for Interactive Buttons

Features:
- Dynamic PL buttons from processed_notes.json
- Buttons disable after selection (Approve/Reject/Tomorrow)
- Refresh to check for new Jira versions
- Good to Announce workflow
"""

import os
import json
import re
import threading
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

# Product Line order - grouped by category for consistent display
PRODUCT_LINE_ORDER = [
    "Media PL1", "Media PL2", "Media",
    "Audiences PL1", "Audiences PL2", "Audiences",
    "DSP Core PL1", "DSP Core PL2", "DSP Core PL3", "DSP Core PL5",
    "DSP PL1", "DSP PL2", "DSP PL3", "DSP",
    "Developer Experience", "Developer Experience 2026",
    "Data Ingress", "Data Ingress 2026",
    "Helix PL3", "Helix",
    "Data Governance", "Other",
]

APPROVAL_STATES_FILE = 'approval_states.json'
MESSAGE_METADATA_FILE = 'message_metadata.json'
DEFERRED_PLS_FILE = 'deferred_pls.json'
LAST_ANNOUNCEMENT_FILE = 'last_announcement.json'

app = App(token=os.getenv("SLACK_BOT_TOKEN"))
client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))


def get_ordered_pls(pl_list: list) -> list:
    ordered = []
    for pl in PRODUCT_LINE_ORDER:
        if pl in pl_list:
            ordered.append(pl)
    for pl in pl_list:
        if pl not in ordered:
            ordered.append(pl)
    return ordered


def load_json(path: str, default):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def save_json(path: str, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def load_approval_states():
    return load_json(APPROVAL_STATES_FILE, {})


def save_approval_states(states: dict):
    save_json(APPROVAL_STATES_FILE, states)


def load_message_metadata():
    return load_json(MESSAGE_METADATA_FILE, {})


def save_message_metadata(metadata: dict):
    save_json(MESSAGE_METADATA_FILE, metadata)


def load_deferred_pls():
    return load_json(DEFERRED_PLS_FILE, {})


def save_deferred_pls(deferred: dict):
    save_json(DEFERRED_PLS_FILE, deferred)


def clean_pl_name_for_action(pl_name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]', '_', pl_name)


def get_pl_name_from_action(action_id: str) -> str:
    parts = action_id.split('_', 1)
    if len(parts) > 1:
        return parts[1].replace('_', ' ')
    return action_id


def _clean_pl_name_for_doc(pl_name: str) -> str:
    return re.sub(r'\s+20\d{2}$', '', pl_name).strip()


def build_refresh_blocks() -> list:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_Refresh to check for new Jira versions_"},
            "accessory": {"type": "button", "text": {"type": "plain_text", "text": "Refresh"}, "action_id": "refresh_versions"}
        }
    ]


def _resolve_pl_key(pl_name: str, notes_by_pl: dict) -> str:
    if pl_name in notes_by_pl:
        return pl_name
    pl_clean = _clean_pl_name_for_doc(pl_name)
    for key in notes_by_pl.keys():
        if _clean_pl_name_for_doc(key) == pl_clean:
            return key
    return pl_name


def _resolve_pl_key_from_processed(pl_name: str, processed_data: dict) -> str:
    pl_clean = _clean_pl_name_for_doc(pl_name)
    candidates = []
    candidates.extend(processed_data.get("product_lines", []) or [])
    candidates.extend(list((processed_data.get("tldr_by_pl", {}) or {}).keys()))
    candidates.extend(list((processed_data.get("body_by_pl", {}) or {}).keys()))
    candidates.extend(list((processed_data.get("release_versions", {}) or {}).keys()))
    for key in candidates:
        if _clean_pl_name_for_doc(key) == pl_clean:
            return key
    return pl_name


def _build_text_blocks(text: str, chunk_size: int = 3000):
    chunks = []
    remaining = text or ""
    while remaining:
        chunks.append(remaining[:chunk_size])
        remaining = remaining[chunk_size:]
    return [{"type": "section", "text": {"type": "mrkdwn", "text": chunk}} for chunk in chunks] or [
        {"type": "section", "text": {"type": "mrkdwn", "text": ""}}
    ]


def _extract_epics_from_body(body_text: str) -> list:
    epics = []
    current = None

    def _normalize_line(line: str) -> str:
        cleaned = line.strip()
        if cleaned.startswith("#### "):
            cleaned = cleaned[5:].strip()
        cleaned = cleaned.replace("**", "").strip()
        link_match = re.match(r'^\[([^\]]+)\]\([^)]+\)$', cleaned)
        if link_match:
            cleaned = link_match.group(1).strip()
        return cleaned

    def _is_epic_header(line: str) -> bool:
        stripped = _normalize_line(line)
        if not stripped:
            return False
        if stripped.startswith(("●", "•", "-", "*")):
            return False
        lowered = stripped.lower()
        if lowered.startswith("value add") or lowered.startswith("bug fix"):
            return False
        if lowered in ("general availability", "feature flag", "beta"):
            return False
        if lowered in ("uncategorized", "other"):
            return False
        if "bug fix" in lowered:
            return False
        return True

    for line in body_text.splitlines():
        if _is_epic_header(line):
            current = _normalize_line(line)
            if current not in epics:
                epics.append(current)
    return epics


def _split_body_by_epic(body_text: str) -> list:
    sections = []
    current_epic = None
    current_lines = []

    def _normalize_line(line: str) -> str:
        cleaned = line.strip()
        if cleaned.startswith("#### "):
            cleaned = cleaned[5:].strip()
        cleaned = cleaned.replace("**", "").strip()
        link_match = re.match(r'^\[([^\]]+)\]\([^)]+\)$', cleaned)
        if link_match:
            cleaned = link_match.group(1).strip()
        return cleaned

    def _is_epic_header(line: str) -> bool:
        stripped = _normalize_line(line)
        if not stripped:
            return False
        if stripped.startswith(("●", "•", "-", "*")):
            return False
        if re.fullmatch(r'[-–—]{3,}', stripped):
            return False
        lowered = stripped.lower()
        if lowered.startswith("value add") or lowered.startswith("bug fix"):
            return False
        if lowered in ("general availability", "feature flag", "beta"):
            return False
        if lowered in ("uncategorized", "other"):
            return False
        if "bug fix" in lowered:
            return False
        return True

    for line in body_text.splitlines():
        if _is_epic_header(line):
            if current_epic:
                sections.append((current_epic, current_lines))
            current_epic = _normalize_line(line)
            current_lines = [line]
        else:
            if current_epic:
                if not re.fullmatch(r'\s*[-–—]{3,}\s*', line.strip()):
                    current_lines.append(line)
            else:
                current_epic = "Other"
                current_lines = [line]

    if current_epic:
        sections.append((current_epic, current_lines))
    return sections


def _filter_body_by_deferred_epics(body_text: str, deferred_epics: list) -> str:
    if not deferred_epics:
        return body_text
    def _normalize_epic_name(name: str) -> str:
        cleaned = name.strip()
        if cleaned.startswith("#### "):
            cleaned = cleaned[5:].strip()
        cleaned = cleaned.replace("**", "").strip()
        link_match = re.match(r'^\[([^\]]+)\]\([^)]+\)$', cleaned)
        if link_match:
            cleaned = link_match.group(1).strip()
        return cleaned.lower().strip()

    deferred_set = {_normalize_epic_name(e) for e in deferred_epics}
    sections = _split_body_by_epic(body_text)
    kept = []
    for epic, lines in sections:
        if _normalize_epic_name(epic) in deferred_set:
            continue
        kept.extend(lines)
        kept.append("")
    return "\n".join(kept).strip() + ("\n" if kept else "")


def auto_format_text(text: str, processed_data: dict = None) -> str:
    if processed_data is None:
        try:
            with open('processed_notes.json', 'r') as f:
                processed_data = json.load(f)
        except Exception:
            processed_data = {}

    fix_version_urls = processed_data.get('fix_version_urls', {})
    epic_urls_by_pl = processed_data.get('epic_urls_by_pl', {})
    epic_urls_flat = processed_data.get('epic_urls', {})

    def _normalize_epic_key(text: str) -> str:
        text = re.sub(r'\s+', ' ', text.strip().lower())
        text = re.sub(r'\s*:\s*', ':', text)
        return text

    # Flatten epic URLs for easier lookup
    all_epic_urls = {}
    for _, epics in epic_urls_by_pl.items():
        if epics:
            for epic_name, epic_url in epics.items():
                all_epic_urls[_normalize_epic_key(epic_name)] = (epic_name, epic_url)
    if epic_urls_flat:
        for epic_name, epic_url in epic_urls_flat.items():
            all_epic_urls[_normalize_epic_key(epic_name)] = (epic_name, epic_url)

    lines = (text or "").split('\n')
    formatted_lines = []

    def strip_formatting(s: str) -> str:
        s = s.strip('*')
        s = re.sub(r'^\s*#{2,}\s*', '', s)
        link_match = re.match(r'^<[^|]+\|(.+)>$', s)
        if link_match:
            s = link_match.group(1).strip('*')
        return s.strip()

    def _parse_slack_link(s: str):
        match = re.match(r'^<([^|>]+)\|(.+)>$', s)
        if not match:
            return None
        return match.group(1), match.group(2)

    in_value_add = False
    in_bug_fixes = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted_lines.append('')
            in_value_add = False
            in_bug_fixes = False
            continue
        if re.fullmatch(r'[-–—]{3,}', stripped):
            continue

        # Normalize markdown bold (**text**) to Slack bold (*text*)
        stripped = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', stripped)
        stripped = stripped.replace("**", "")

        parsed_link = _parse_slack_link(stripped)
        if parsed_link:
            url, link_text = parsed_link
            link_clean = strip_formatting(link_text)
            if link_clean.startswith("Release "):
                formatted_lines.append(stripped)
            else:
                formatted_lines.append(f"<{url}|*{link_clean}*>")
            in_value_add = False
            in_bug_fixes = False
            continue

        # Strip markdown heading prefixes
        stripped = re.sub(r'^\s*#{2,}\s*', '', stripped)
        stripped = re.sub(r'^\*\s*Value Add\s*\*:\s*', 'Value Add: ', stripped, flags=re.IGNORECASE)
        stripped = re.sub(r'^\*\s*Bug Fixes\s*\*:\s*', 'Bug Fixes: ', stripped, flags=re.IGNORECASE)
        stripped = re.sub(r'^\*\s+', '• ', stripped)
        stripped = re.sub(r'^[\-]\s+', '• ', stripped)
        bullet_stripped = re.sub(r'^[●•\*\-]\s*', '', stripped)
        clean_text = strip_formatting(bullet_stripped)
        clean_lower = _normalize_epic_key(clean_text)
        if clean_lower in ("uncategorized", "other"):
            continue
        if "bug fix" in clean_lower and clean_lower not in ("bug fixes", "bug fixes:"):
            continue

        # Normalize "PL: <url|Release X>" to bold PL name
        release_link_match = re.match(r'^([^:]+):\s*<([^|>]+)\|(Release\s+\d+\.\d+)>$', stripped)
        if release_link_match:
            pl_name = release_link_match.group(1).strip()
            url = release_link_match.group(2).strip()
            release_ver = release_link_match.group(3).strip()
            formatted_lines.append(f"*{pl_name}*: <{url}|{release_ver}>")
            in_value_add = False
            in_bug_fixes = False
            continue

        if clean_lower in ('value add:', 'value add'):
            formatted_lines.append('*Value Add:*')
            in_value_add = True
            in_bug_fixes = False
            continue
        if clean_lower in ('bug fixes:', 'bug fixes'):
            formatted_lines.append('*Bug Fixes:*')
            in_bug_fixes = True
            in_value_add = False
            continue

        if clean_text in ('General Availability', 'Feature Flag', 'Beta'):
            formatted_lines.append(f'`{clean_text}`')
            in_value_add = False
            in_bug_fixes = False
            continue

        release_match = re.match(r'^\*?([^*:]+)\*?:\s*(Release\s+\d+\.\d+)$', stripped)
        if release_match and '<' not in stripped:
            pl_name = release_match.group(1).strip()
            release_ver = release_match.group(2)
            url = None
            pl_name_lower = pl_name.lower()
            for stored_pl, stored_url in fix_version_urls.items():
                stored_pl_lower = stored_pl.lower()
                stored_pl_clean = re.sub(r'\s+20\d{2}$', '', stored_pl_lower)
                pl_name_clean = re.sub(r'\s+20\d{2}$', '', pl_name_lower)
                if (pl_name_lower == stored_pl_lower or
                    pl_name_clean == stored_pl_clean or
                    pl_name_lower in stored_pl_lower or
                    stored_pl_lower in pl_name_lower):
                    url = stored_url
                    break
            if url:
                formatted_lines.append(f"*{pl_name}*: <{url}|{release_ver}>")
                in_value_add = False
                in_bug_fixes = False
                continue

        md_link = re.match(r'^\[([^\]]+)\]\(([^)]+)\)$', clean_text)
        if md_link:
            md_text = md_link.group(1).strip()
            md_url = md_link.group(2).strip()
            formatted_lines.append(f"<{md_url}|*{md_text}*>")
            continue

        clean_words = set(clean_lower.split())
        found_epic = False
        for epic_key, (epic_name, epic_url) in all_epic_urls.items():
            epic_words = set(epic_key.split())
            if not epic_words or not clean_words:
                continue
            common = clean_words & epic_words
            forward_ratio = len(common) / len(epic_words)
            reverse_ratio = len(common) / len(clean_words)
            if forward_ratio >= 0.7 or reverse_ratio >= 0.7:
                formatted_lines.append(f"<{epic_url}|*{clean_text}*>")
                found_epic = True
                break

        if found_epic:
            in_value_add = False
            in_bug_fixes = False
            continue

        # Skip bullets/prose from epic matching
        if '<' in stripped or stripped.startswith(('●', '•', '-', '*', '`')):
            formatted_lines.append(stripped)
            continue
        if in_value_add or in_bug_fixes:
            formatted_lines.append(f"• {stripped}")
        else:
            formatted_lines.append(stripped)

    return '\n'.join(formatted_lines)


def run_async(target, *args, **kwargs):
    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()


def build_pl_blocks(pls: list, message_ts: str = None) -> list:
    blocks = []
    approval_states = load_approval_states()

    for pl in pls:
        pl_action_id = clean_pl_name_for_action(pl)
        reviewed_status = None
        reviewed_by = None
        pl_state = None
        if message_ts and message_ts in approval_states:
            pl_state = approval_states[message_ts].get(pl)
            if pl_state:
                reviewed_status = pl_state['status']
                reviewed_by = pl_state['user']

        if reviewed_status:
            status_text = {
                'approved': '✅ Approved',
                'rejected': '⏸️ Deferred (Full)',
                'deferred_full': '⏸️ Deferred (Full)',
                'deferred_partial': '⏸️ Deferred (Partial)',
                'tomorrow': '🗓️ Tomorrow'
            }
            status_line = status_text.get(reviewed_status, reviewed_status)
            if reviewed_status == "deferred_partial":
                deferred_epics = pl_state.get("deferred_epics", []) if pl_state else []
                if deferred_epics:
                    preview = ", ".join(deferred_epics[:3])
                    if len(deferred_epics) > 3:
                        preview += "..."
                    status_line = f"⏸️ Deferred (Partial: {preview})"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{pl}*\n{status_line} by @{reviewed_by}"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "↩️ Reset"},
                    "action_id": f"reset_{pl_action_id}"
                }
            })
        else:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{pl}*"},
                "accessory": {
                    "type": "static_select",
                    "action_id": f"actions_{pl_action_id}",
                    "placeholder": {"type": "plain_text", "text": "Choose action"},
                    "options": [
                        {"text": {"type": "plain_text", "text": "✅ Approved"}, "value": f"approve_{pl_action_id}"},
                        {"text": {"type": "plain_text", "text": "⏸️ Deferred"}, "value": f"defer_{pl_action_id}"},
                        {"text": {"type": "plain_text", "text": "🗓️ Tomorrow"}, "value": f"tomorrow_{pl_action_id}"},
                        {"text": {"type": "plain_text", "text": "↩️ Reset"}, "value": f"reset_{pl_action_id}"}
                    ]
                }
            })
    return blocks


def count_pending_reviews(message_ts: str) -> int:
    message_metadata = load_message_metadata()
    approval_states = load_approval_states()
    if message_ts not in message_metadata:
        return 0
    all_pls = message_metadata[message_ts].get('pls', [])
    reviewed = approval_states.get(message_ts, {})
    return len(all_pls) - len(reviewed)


def all_pls_reviewed(message_ts: str) -> bool:
    return count_pending_reviews(message_ts) == 0


def build_footer_blocks(message_ts: str = None, pls: list = None) -> list:
    blocks = []
    pending_count = count_pending_reviews(message_ts) if message_ts else len(pls or [])
    all_reviewed = pending_count == 0

    if pending_count > 0:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{pending_count} PL(s) pending review*"}]})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "*All PLs reviewed*"}]})

    if all_reviewed:
        blocks.append({
            "type": "actions",
            "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Good to Announce"}, "style": "primary", "action_id": "good_to_announce"}]
        })
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "_Good to Announce (review all PLs first)_"}})

    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "_© Powered by Release Announcement Agent_"}]})
    return blocks


def update_message_with_status(channel: str, message_ts: str, user_id: str = None):
    message_metadata = load_message_metadata()
    if message_ts not in message_metadata:
        return

    pls = message_metadata[message_ts].get('pls', [])
    doc_url = message_metadata[message_ts].get('doc_url', '')
    release_date = message_metadata[message_ts].get('release_date', '')

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Release Notes Review", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"Daily Consolidated Deployment Summary" + (f" - {release_date}" if release_date else "")}}
    ]
    if doc_url:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<{doc_url}|📄 View Release Notes>"}})

    blocks.extend(build_refresh_blocks())
    blocks.append({"type": "divider"})
    blocks.extend(build_pl_blocks(pls, message_ts))
    blocks.append({"type": "divider"})
    blocks.extend(build_footer_blocks(message_ts, pls))

    try:
        client.chat_update(channel=channel, ts=message_ts, blocks=blocks, text="Release Notes Review")
    except SlackApiError as e:
        if user_id:
            try:
                client.chat_postEphemeral(channel=channel, user=user_id, text=f"⚠️ Slack error: {e.response.get('error')}.")
            except Exception:
                pass


def restore_pl_to_google_doc(pl_name: str, deferred_pl_data: dict, message_ts: str) -> bool:
    try:
        from google_docs_handler import GoogleDocsHandler
        from google_docs_formatter import GoogleDocsFormatter, get_ordered_pls

        message_metadata = load_message_metadata()
        release_date = message_metadata.get(message_ts, {}).get('release_date', '')
        google_docs = GoogleDocsHandler()
        if not google_docs.authenticate() or not google_docs.test_connection():
            return False

        full_text = google_docs.get_document_content()
        if not full_text:
            return False

        doc = google_docs.service.documents().get(documentId=google_docs.document_id).execute()
        segments = []
        for element in doc.get('body', {}).get('content', []):
            if 'paragraph' in element:
                for text_run in element['paragraph'].get('elements', []):
                    if 'textRun' in text_run:
                        text = text_run['textRun'].get('content', '')
                        start = text_run.get('startIndex', 1)
                        end = text_run.get('endIndex', start + len(text))
                        segments.append((text, start, end))

        def _text_pos_to_doc_index(text_pos: int) -> int:
            current_pos = 0
            for text, doc_start, doc_end in segments:
                if current_pos + len(text) > text_pos:
                    offset = text_pos - current_pos
                    return doc_start + offset
                current_pos += len(text)
            return segments[-1][2] if segments else 1

        release_header = f"Daily Deployment Summary: {release_date}" if release_date else "Daily Deployment Summary:"
        release_start = full_text.find(release_header)
        if release_start == -1:
            release_start = full_text.find("Daily Deployment Summary:")
        if release_start == -1:
            release_start = 0

        section_text = full_text[release_start:]
        separator_match = re.search(r'\n═{20,}\n', section_text)
        section_end = release_start + separator_match.start() if separator_match else len(full_text)

        tldr_summary = deferred_pl_data.get('tldr') or deferred_pl_data.get('notes') or "Updates added"
        pl_clean = _clean_pl_name_for_doc(pl_name)
        tldr_line = f"• {pl_clean} - {tldr_summary}\n"

        tldr_insert_text_pos = None
        tldr_header_match = re.search(r'-{10,}\s*TL;DR:?\s*-{10,}', section_text, re.IGNORECASE)
        if tldr_header_match:
            after_tldr_header = tldr_header_match.end()
            rest = section_text[after_tldr_header:]
            next_header = re.search(r'\n-{10,}[^-]+-{10,}', rest)
            if next_header:
                tldr_insert_text_pos = release_start + after_tldr_header + next_header.start()
            else:
                tldr_insert_text_pos = release_start + len(section_text)

        def _get_pl_category(name: str) -> str:
            lower = name.lower()
            if 'media' in lower:
                return "Media"
            if 'audience' in lower:
                return "Audiences"
            if 'developer' in lower:
                return "Developer Experience"
            if 'data ingress' in lower:
                return "Data Ingress"
            if 'data governance' in lower:
                return "Data Governance"
            if 'helix' in lower:
                return "Helix"
            if 'dsp' in lower:
                return "DSP"
            return name

        formatter = GoogleDocsFormatter()
        formatter.reset()
        release_ver = deferred_pl_data.get('release_version', 'Release 1.0')
        fix_version_url = deferred_pl_data.get('fix_version_url', '')
        epic_urls = deferred_pl_data.get('epic_urls', {})
        body_text = deferred_pl_data.get('body', '')

        category = _get_pl_category(pl_clean)
        header_text = f"------------------{category}------------------"

        # Find category section bounds
        category_start = section_text.find(header_text)
        category_insert_text_pos = None
        if category_start != -1:
            after_header = category_start + len(header_text)
            rest = section_text[after_header:]
            next_header = re.search(r'\n-{10,}[^-]+-{10,}', rest)
            category_end = after_header + (next_header.start() if next_header else len(rest))

            # Find existing PL headers within category section
            category_body = section_text[after_header:category_end]
            pl_header_matches = list(re.finditer(r'(^[^\n]+:\s*Release\s+\d+(?:\.\d+)?)', category_body, re.MULTILINE))
            existing_pls = []
            for m in pl_header_matches:
                header_line = m.group(1)
                pl_name_part = header_line.split(":")[0].strip()
                existing_pls.append((pl_name_part, m.start()))

            # Determine insertion order
            existing_names = [p for p, _ in existing_pls]
            desired_order = get_ordered_pls(existing_names + [pl_clean])

            insert_before = None
            for name in desired_order:
                if name == pl_clean:
                    # Insert before next existing PL in order
                    idx = desired_order.index(name)
                    for next_name in desired_order[idx + 1:]:
                        for existing_name, pos in existing_pls:
                            if existing_name == next_name:
                                insert_before = pos
                                break
                        if insert_before is not None:
                            break
                    break

            if insert_before is not None:
                category_insert_text_pos = release_start + after_header + insert_before
            else:
                category_insert_text_pos = release_start + category_end
        else:
            # Category header missing; insert at end of release section
            category_insert_text_pos = section_end

        if header_text not in section_text:
            formatter._insert_text(f"\n{header_text}\n\n")

        formatter._insert_text("\n")
        formatter._insert_text(f"{pl_clean}: ")
        ver_start = formatter.current_index
        formatter._insert_text(f"{release_ver}\n")
        ver_end = ver_start + len(release_ver)
        if fix_version_url:
            formatter._mark_link(ver_start, ver_end, fix_version_url)

        sections = formatter._parse_body_sections(body_text)
        if sections:
            aggregated_bug_fixes = []
            render_sections = []
            for section in sections:
                epic_title = section["epic"]
                has_value = bool(section["value_add"])
                has_bug = bool(section["bug_fixes"])
                if has_bug:
                    aggregated_bug_fixes.extend(section["bug_fixes"])
                if has_bug and not has_value:
                    continue
                render_sections.append(section)

            for section in render_sections:
                epic_title = section["epic"]
                epic_url = formatter._find_epic_url(epic_title, epic_urls) if epic_urls else ""
                epic_start = formatter.current_index
                formatter._insert_text(f"{epic_title}\n")
                epic_end = formatter.current_index
                formatter._mark_bold(epic_start, epic_end - 1)
                if epic_url:
                    formatter._mark_link(epic_start, epic_end - 1, epic_url)

                if section["value_add"]:
                    value_start = formatter.current_index
                    formatter._insert_text("Value Add:\n")
                    formatter._mark_bold(value_start, value_start + len("Value Add:"))
                    for item in section["value_add"]:
                        formatter._insert_text(f"• {item}\n")

                if section["availability"]:
                    avail_start = formatter.current_index
                    formatter._insert_text(f"{section['availability']}\n")
                    formatter._mark_green(avail_start, formatter.current_index - 1)

                formatter._insert_text("\n")

            if aggregated_bug_fixes:
                bug_start = formatter.current_index
                formatter._insert_text("Bug Fixes:\n")
                formatter._mark_bold(bug_start, bug_start + len("Bug Fixes:"))
                for item in aggregated_bug_fixes:
                    bug_text = formatter._normalize_bug_fix_bullet(item)
                    formatter._insert_text(f"• {bug_text}\n")
                formatter._insert_text("\n")
        else:
            elements = formatter._parse_body_content(body_text, epic_urls, pl_clean, release_ver)
            for element in elements:
                elem_start = formatter.current_index
                if element["type"] == "bullet":
                    formatter._insert_text(f"    ● {element['text']}")
                else:
                    formatter._insert_text(element["text"])
                elem_end = formatter.current_index
                if element["type"] == "epic":
                    if element.get("bold"):
                        formatter._mark_bold(elem_start, elem_end - 1)
                    if element.get("url"):
                        formatter._mark_link(elem_start, elem_end - 1, element["url"])
                elif element["type"] in ("value_add_header", "bug_fixes_header"):
                    bold_start, bold_end = element.get("bold_range", (0, 0))
                    if bold_end > bold_start:
                        formatter._mark_bold(elem_start + bold_start, elem_start + bold_end)
                elif element["type"] == "status":
                    if element.get("color") == "green":
                        formatter._mark_green(elem_start, elem_end - 1)

        formatter._insert_text("\n")
        formatter._build_format_requests()

        body_insert_index = _text_pos_to_doc_index(category_insert_text_pos)
        tldr_insert_index = _text_pos_to_doc_index(tldr_insert_text_pos) if tldr_insert_text_pos is not None else None

        jobs = []
        if tldr_insert_index is not None and tldr_line:
            tldr_requests = [{"insertText": {"location": {"index": tldr_insert_index}, "text": tldr_line}}]
            tldr_format = [
                {"updateTextStyle": {"range": {"startIndex": tldr_insert_index, "endIndex": tldr_insert_index + len(tldr_line)}, "textStyle": {"bold": False}, "fields": "bold"}},
                {"updateTextStyle": {"range": {"startIndex": tldr_insert_index + len("• "), "endIndex": tldr_insert_index + len("• ") + len(pl_clean)}, "textStyle": {"bold": True}, "fields": "bold"}}
            ]
            jobs.append((tldr_insert_index, tldr_requests, tldr_format))

        if formatter.insert_requests:
            offset = body_insert_index - 1
            body_insert_requests = []
            for req in formatter.insert_requests:
                new_req = json.loads(json.dumps(req))
                new_req["insertText"]["location"]["index"] += offset
                body_insert_requests.append(new_req)

            body_format_requests = []
            for req in formatter.format_requests:
                new_req = json.loads(json.dumps(req))
                if "updateTextStyle" in new_req:
                    new_req["updateTextStyle"]["range"]["startIndex"] += offset
                    new_req["updateTextStyle"]["range"]["endIndex"] += offset
                if "updateParagraphStyle" in new_req:
                    new_req["updateParagraphStyle"]["range"]["startIndex"] += offset
                    new_req["updateParagraphStyle"]["range"]["endIndex"] += offset
                body_format_requests.append(new_req)
            jobs.append((body_insert_index, body_insert_requests, body_format_requests))

        jobs.sort(key=lambda x: x[0], reverse=True)
        for _, insert_reqs, format_reqs in jobs:
            if insert_reqs:
                google_docs.update_document(insert_reqs)
            if format_reqs:
                google_docs.update_document(format_reqs)

        return True
    except Exception:
        return False


@app.action(re.compile(r"^approve_.+$"))
def handle_approve(ack, body, action):
    ack()
    def _work():
        pl_name = get_pl_name_from_action(action['action_id'])
        user = body['user']['username']
        user_id = body['user']['id']
        message_ts = body['message']['ts']
        channel = body['channel']['id']

        approval_states = load_approval_states()
        approval_states.setdefault(message_ts, {})
        approval_states[message_ts][pl_name] = {"status": "approved", "user": user, "timestamp": datetime.now().isoformat()}
        save_approval_states(approval_states)
        update_message_with_status(channel, message_ts, user_id)
    run_async(_work)


@app.action(re.compile(r"^reject_.+$"))
def handle_reject(ack, body, action):
    ack()
    def _work():
        pl_name = get_pl_name_from_action(action['action_id'])
        user = body['user']['username']
        user_id = body['user']['id']
        message_ts = body['message']['ts']
        channel = body['channel']['id']

        approval_states = load_approval_states()
        approval_states.setdefault(message_ts, {})
        approval_states[message_ts][pl_name] = {"status": "deferred_full", "user": user, "timestamp": datetime.now().isoformat()}
        save_approval_states(approval_states)
        update_message_with_status(channel, message_ts, user_id)
    run_async(_work)


def _open_defer_modal(trigger_id: str, pl_name: str, message_ts: str, channel: str):
    message_metadata = load_message_metadata()
    notes_by_pl = message_metadata.get(message_ts, {}).get("notes_by_pl", {})
    pl_key = _resolve_pl_key(pl_name, notes_by_pl)
    body_text = notes_by_pl.get(pl_key, "")
    epics = _extract_epics_from_body(body_text)

    epic_options = [
        {"text": {"type": "plain_text", "text": epic[:75]}, "value": epic[:75]}
        for epic in epics
    ]
    if not epic_options:
        epic_options = [{"text": {"type": "plain_text", "text": "No epics found"}, "value": "__none__"}]

    view = {
        "type": "modal",
        "callback_id": "defer_details",
        "title": {"type": "plain_text", "text": "Defer PL"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": json.dumps({
            "pl_name": pl_name,
            "message_ts": message_ts,
            "channel": channel
        }),
        "blocks": [
            {
                "type": "input",
                "block_id": "defer_scope_block",
                "label": {"type": "plain_text", "text": "Defer scope"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": "defer_scope",
                    "initial_option": {"text": {"type": "plain_text", "text": "Full"}, "value": "full"},
                    "options": [
                        {"text": {"type": "plain_text", "text": "Full"}, "value": "full"},
                        {"text": {"type": "plain_text", "text": "Partial"}, "value": "partial"}
                    ]
                }
            },
            {
                "type": "section",
                "block_id": "defer_epics_block",
                "text": {"type": "mrkdwn", "text": "*Select epics to defer (if deferring specific epics)*"},
                "accessory": {
                    "type": "multi_static_select",
                    "action_id": "defer_epics",
                    "placeholder": {"type": "plain_text", "text": "Choose epics"},
                    "options": epic_options
                }
            }
        ]
    }

    client.views_open(trigger_id=trigger_id, view=view)


@app.action(re.compile(r"^defer_.+$"))
def handle_defer(ack, body, action):
    ack()
    trigger_id = body.get("trigger_id")
    pl_name = get_pl_name_from_action(action['action_id'])
    message_ts = body.get("message", {}).get("ts") or body.get("container", {}).get("message_ts")
    channel = body.get("channel", {}).get("id") or body.get("container", {}).get("channel_id")
    user_id = body.get("user", {}).get("id")

    if not trigger_id:
        if user_id and channel:
            try:
                client.chat_postEphemeral(
                    channel=channel,
                    user=user_id,
                    text="⚠️ Could not open defer modal (missing trigger_id). Please try again."
                )
            except Exception:
                pass
        return
    try:
        _open_defer_modal(trigger_id, pl_name, message_ts, channel)
    except Exception as e:
        try:
            print(f"[Defer Modal] Error opening modal: {e}")
            response = getattr(e, "response", None)
            if response is not None:
                try:
                    print(f"[Defer Modal] Response: {response}")
                except Exception:
                    pass
        except Exception:
            pass
        if user_id and channel:
            try:
                response = getattr(e, "response", None)
                error_code = None
                if response is not None:
                    if isinstance(response, dict):
                        error_code = response.get("error")
                    else:
                        error_code = getattr(response, "data", {}).get("error")
                if error_code == "expired_trigger_id":
                    return
                error_text = "⚠️ Could not open defer modal. Please try again."
                if error_code:
                    error_text = f"⚠️ Could not open defer modal: {error_code}"
                client.chat_postEphemeral(channel=channel, user=user_id, text=error_text)
            except Exception:
                pass


@app.view("defer_details")
def handle_defer_view_submission(ack, body, view):
    values = view.get("state", {}).get("values", {})
    scope_block = values.get("defer_scope_block", {})
    scope_value = scope_block.get("defer_scope", {}).get("selected_option", {}).get("value")
    epics_block = values.get("defer_epics_block", {})
    epics_value = epics_block.get("defer_epics", {})
    selected_epics = epics_value.get("selected_options", [])
    deferred_epics = [
        opt.get("value")
        for opt in (selected_epics or [])
        if opt and opt.get("value") and opt.get("value") != "__none__"
    ]

    ack()

    try:
        meta = json.loads(view.get("private_metadata", "{}"))
        pl_name = meta.get("pl_name")
        message_ts = meta.get("message_ts")
        channel = meta.get("channel")
        user = body['user']['username']
        user_id = body['user']['id']

        approval_states = load_approval_states()
        approval_states.setdefault(message_ts, {})
        if scope_value == "partial":
            approval_states[message_ts][pl_name] = {
                "status": "deferred_partial",
                "user": user,
                "timestamp": datetime.now().isoformat(),
                "deferred_epics": deferred_epics
            }
        else:
            approval_states[message_ts][pl_name] = {
                "status": "deferred_full",
                "user": user,
                "timestamp": datetime.now().isoformat()
            }
        save_approval_states(approval_states)
        update_message_with_status(channel, message_ts, user_id)
    except Exception:
        pass


@app.action(re.compile(r"^tomorrow_.+$"))
def handle_tomorrow(ack, body, action):
    ack()
    def _work():
        pl_name = get_pl_name_from_action(action['action_id'])
        user = body['user']['username']
        user_id = body['user']['id']
        message_ts = body['message']['ts']
        channel = body['channel']['id']

        approval_states = load_approval_states()
        approval_states.setdefault(message_ts, {})
        approval_states[message_ts][pl_name] = {"status": "tomorrow", "user": user, "timestamp": datetime.now().isoformat()}
        save_approval_states(approval_states)

        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        deferred_pls = load_deferred_pls()
        deferred_pls.setdefault(tomorrow, [])

        pl_notes = load_message_metadata().get(message_ts, {}).get('notes_by_pl', {}).get(pl_name, '')
        pl_data = {'pl': pl_name, 'notes': pl_notes, 'deferred_by': user, 'deferred_at': datetime.now().isoformat()}

        try:
            with open('processed_notes.json', 'r') as f:
                processed_data = json.load(f)
            original_pl = None
            for pl in processed_data.get('product_lines', []):
                if pl_name in pl or pl in pl_name or pl.replace(' 2026', '').replace(' 2025', '') == pl_name:
                    original_pl = pl
                    break
            if original_pl:
                pl_data['tldr'] = processed_data.get('tldr_by_pl', {}).get(original_pl, '')
                pl_data['body'] = processed_data.get('body_by_pl', {}).get(original_pl, '')
                pl_data['release_version'] = processed_data.get('release_versions', {}).get(original_pl, 'Release 1.0')
                pl_data['fix_version_url'] = processed_data.get('fix_version_urls', {}).get(original_pl, '')
                epic_urls_by_pl = processed_data.get('epic_urls_by_pl', {}).get(original_pl, {})
                if not epic_urls_by_pl:
                    epic_urls_by_pl = processed_data.get('epic_urls', {}) or {}
                pl_data['epic_urls'] = epic_urls_by_pl
        except Exception:
            pass

        deferred_pls[tomorrow].append(pl_data)
        save_deferred_pls(deferred_pls)
        update_message_with_status(channel, message_ts, user_id)

        def _remove_from_doc():
            try:
                from google_docs_handler import GoogleDocsHandler
                google_docs = GoogleDocsHandler()
                if google_docs.authenticate():
                    google_docs.remove_pl_section(pl_name)
            except Exception:
                pass
        run_async(_remove_from_doc)
    run_async(_work)


@app.action(re.compile(r"^actions_.+$"))
def handle_overflow_actions(ack, body, action):
    ack()
    selected_value = action.get("selected_option", {}).get("value")
    if not selected_value:
        return
    action["action_id"] = selected_value
    def _dispatch():
        if selected_value.startswith("approve_"):
            handle_approve(lambda: None, body, action)
        elif selected_value.startswith("reject_"):
            handle_reject(lambda: None, body, action)
        elif selected_value.startswith("defer_"):
            handle_defer(lambda: None, body, action)
        elif selected_value.startswith("tomorrow_"):
            handle_tomorrow(lambda: None, body, action)
        elif selected_value.startswith("reset_"):
            handle_reset(lambda: None, body, action)
    threading.Thread(target=_dispatch, daemon=True).start()


@app.action(re.compile(r"^reset_.+$"))
def handle_reset(ack, body, action):
    ack()
    def _work():
        pl_name = get_pl_name_from_action(action['action_id'])
        user_id = body['user']['id']
        message_ts = body['message']['ts']
        channel = body['channel']['id']

        approval_states = load_approval_states()
        previous_state = approval_states.get(message_ts, {}).get(pl_name, {})
        previous_status = previous_state.get('status')

        if message_ts in approval_states and pl_name in approval_states[message_ts]:
            del approval_states[message_ts][pl_name]
            save_approval_states(approval_states)

        if previous_status == 'tomorrow':
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            deferred_pls = load_deferred_pls()
            deferred_pl_data = None
            if tomorrow in deferred_pls:
                for d in deferred_pls[tomorrow]:
                    if d.get('pl') == pl_name:
                        deferred_pl_data = d
                        break
                deferred_pls[tomorrow] = [d for d in deferred_pls[tomorrow] if d.get('pl') != pl_name]
                save_deferred_pls(deferred_pls)
            if deferred_pl_data:
                run_async(restore_pl_to_google_doc, pl_name, deferred_pl_data, message_ts)

        update_message_with_status(channel, message_ts, user_id)
    run_async(_work)


@app.action("refresh_versions")
def handle_refresh_versions(ack, body):
    ack()
    user_id = body['user']['id']
    channel = body['channel']['id']
    message_ts = body['message']['ts']

    try:
        client.chat_postEphemeral(channel=channel, user=user_id, text="🔄 Checking for new Jira versions... This may take a moment.")
    except Exception:
        pass

    def _work():
        try:
            from refresh_handler import refresh_release_versions
            result = refresh_release_versions(message_ts)
            if not result.get('success'):
                try:
                    client.chat_postEphemeral(channel=channel, user=user_id, text=f"❌ Refresh failed: {result.get('message', 'Unknown error')}")
                except Exception:
                    pass
                return

            new_pls = result.get('new_pls', [])
            updated_existing = result.get('updated_existing_pls', [])
            if not new_pls and not updated_existing:
                try:
                    client.chat_postEphemeral(channel=channel, user=user_id, text=f"✅ {result.get('message', 'No new PLs found.')}")
                except Exception:
                    pass
                return

            # Update metadata with new PLs
            message_metadata = load_message_metadata()
            if message_ts in message_metadata:
                existing_pls = message_metadata[message_ts].get('pls', [])
                for pl in new_pls:
                    pl_clean = re.sub(r'\s+20\d{2}$', '', pl)
                    if pl_clean not in existing_pls:
                        existing_pls.append(pl_clean)
                message_metadata[message_ts]['pls'] = existing_pls

                existing_notes = message_metadata[message_ts].get('notes_by_pl', {})
                new_processed = result.get('processed_data', {})
                existing_processed = result.get('processed_existing', {})

                # Merge new PL bodies
                existing_notes.update(new_processed.get('body_by_pl', {}))

                # Append new content for existing PLs
                for pl, body in existing_processed.get('body_by_pl', {}).items():
                    if not body:
                        continue
                    if pl in existing_notes and existing_notes[pl]:
                        existing_notes[pl] = existing_notes[pl].rstrip() + "\n" + body.lstrip()
                    else:
                        existing_notes[pl] = body
                message_metadata[message_ts]['notes_by_pl'] = existing_notes
                save_message_metadata(message_metadata)

            update_message_with_status(channel, message_ts)
            try:
                details = []
                if new_pls:
                    details.append(f"Added {len(new_pls)} new PL(s): {', '.join(new_pls)}")
                if updated_existing:
                    details.append(f"Updated {len(updated_existing)} existing PL(s): {', '.join(updated_existing)}")
                client.chat_postEphemeral(channel=channel, user=user_id, text="✅ " + " | ".join(details))
            except Exception:
                pass
        except Exception as e:
            try:
                client.chat_postEphemeral(channel=channel, user=user_id, text=f"❌ Error during refresh: {str(e)}")
            except Exception:
                pass
    run_async(_work)


@app.action("good_to_announce")
def handle_good_to_announce(ack, body):
    ack()
    user = body['user']['username']
    channel = body['channel']['id']
    message_ts = body['message']['ts']
    user_id = body['user']['id']

    if not all_pls_reviewed(message_ts):
        return

    approval_states = load_approval_states()
    message_metadata = load_message_metadata()

    approved_pls = []
    deferred_full_pls = []
    deferred_partial = {}
    tomorrow_pls = []
    for pl, state in approval_states.get(message_ts, {}).items():
        status = state.get('status')
        if status == 'approved':
            approved_pls.append(pl)
        elif status in ('rejected', 'deferred_full'):
            deferred_full_pls.append(pl)
        elif status == 'deferred_partial':
            approved_pls.append(pl)
            deferred_partial[pl] = state.get('deferred_epics', [])
        elif status == 'tomorrow':
            tomorrow_pls.append(pl)

    approved_pls = get_ordered_pls(approved_pls)
    announce_channel = os.getenv('SLACK_ANNOUNCE_CHANNEL', channel)
    release_date = message_metadata.get(message_ts, {}).get('release_date', datetime.now().strftime('%d %B %Y'))

    try:
        with open('processed_notes.json', 'r') as f:
            processed_data = json.load(f)
    except Exception:
        processed_data = {}

    tldr_by_pl = processed_data.get('tldr_by_pl', {})
    body_by_pl = processed_data.get('body_by_pl', {})
    release_versions = processed_data.get('release_versions', {})

    announced_pls = []
    body_for_pl = {}
    resolved_by_pl = {}
    notes_by_pl = message_metadata.get(message_ts, {}).get('notes_by_pl', {}) if message_metadata else {}
    for pl in approved_pls:
        resolved_key = _resolve_pl_key_from_processed(pl, processed_data)
        resolved_by_pl[pl] = resolved_key
        body = body_by_pl.get(resolved_key, "") or body_by_pl.get(pl, "") or body_by_pl.get(pl.replace(' 2026', ''), "")
        if not body and notes_by_pl:
            notes_key = _resolve_pl_key(pl, notes_by_pl)
            body = notes_by_pl.get(notes_key, "")
        if pl in deferred_partial and body:
            body = _filter_body_by_deferred_epics(body, deferred_partial.get(pl, []))
        if body and body.strip():
            announced_pls.append(pl)
            body_for_pl[pl] = body

    announcement_text = f"*Daily Deployment Summary: {release_date}*\n\n"
    announcement_text += "------------------TL;DR:------------------\n\n"
    announcement_text += "*Key Deployments:*\n"
    for pl in announced_pls:
        resolved_key = resolved_by_pl.get(pl, pl)
        tldr = tldr_by_pl.get(resolved_key) or tldr_by_pl.get(pl) or tldr_by_pl.get(pl.replace(' 2026', ''))
        if tldr:
            announcement_text += f"● *{pl}* - {tldr}\n"
    announcement_text += "\n"

    for pl in announced_pls:
        resolved_key = resolved_by_pl.get(pl, pl)
        version = release_versions.get(resolved_key, "") or release_versions.get(pl, "") or release_versions.get(pl.replace(' 2026', ''), "")
        announcement_text += f"------------------{pl}------------------\n"
        if version:
            announcement_text += f"{pl}: {version}\n"
        body = body_for_pl.get(pl, "")
        if body:
            announcement_text += f"{body}\n\n"

    try:
        announcement_text = auto_format_text(announcement_text, processed_data)
        blocks = _build_text_blocks(announcement_text)
        result = client.chat_postMessage(
            channel=announce_channel,
            text=announcement_text[:40000],
            blocks=blocks
        )
        announcement_ts = result.get('ts')
        if announcement_ts:
            save_last_announcement(announce_channel, announcement_ts, announcement_text)
    except Exception as e:
        error_msg = str(e)
        try:
            if hasattr(e, "response"):
                error_msg = e.response.get("error", error_msg)
        except Exception:
            pass
        print(f"[Socket Mode] Error posting announcement: {error_msg}")
        try:
            client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=f"⚠️ Failed to post the final announcement: {error_msg}"
            )
        except Exception:
            pass

    deferred_partial_pls = list(deferred_partial.keys())
    final_blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"✅ *Release Announced Successfully*\n\nAnnounced by @{user} at {datetime.now().strftime('%Y-%m-%d %H:%M')}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"• Approved: {', '.join(approved_pls) if approved_pls else 'None'}\n• Deferred (Full): {', '.join(deferred_full_pls) if deferred_full_pls else 'None'}\n• Deferred (Partial): {', '.join(deferred_partial_pls) if deferred_partial_pls else 'None'}\n• Tomorrow: {', '.join(tomorrow_pls) if tomorrow_pls else 'None'}"}}
    ]
    client.chat_update(channel=channel, ts=message_ts, blocks=final_blocks, text="Release has been announced!")


def save_last_announcement(channel: str, message_ts: str, text: str):
    """Save the last announcement details for edit/delete."""
    save_json(LAST_ANNOUNCEMENT_FILE, {
        'channel': channel,
        'message_ts': message_ts,
        'text': text,
        'posted_at': datetime.now().isoformat()
    })


def load_last_announcement() -> dict:
    """Load the last announcement details."""
    return load_json(LAST_ANNOUNCEMENT_FILE, {})


@app.command("/delete-announcement")
def handle_delete_announcement(ack, command, respond):
    """Handle /delete-announcement slash command."""
    ack()

    user = command['user_name']
    text = command.get('text', '').strip()
    print(f"[Socket Mode] {user} triggered /delete-announcement")

    if text:
        parts = text.split()
        if len(parts) >= 2:
            channel = parts[0]
            message_ts = parts[1]
        else:
            channel = os.getenv('SLACK_ANNOUNCE_CHANNEL', command['channel_id'])
            message_ts = parts[0]
    else:
        last = load_last_announcement()
        if not last:
            respond("No announcement found to delete. Use `/delete-announcement <channel_id> <message_ts>` to specify.")
            return
        channel = last.get('channel')
        message_ts = last.get('message_ts')

    try:
        client.chat_delete(channel=channel, ts=message_ts)
        respond("✅ Announcement deleted successfully!")
        if os.path.exists(LAST_ANNOUNCEMENT_FILE):
            os.remove(LAST_ANNOUNCEMENT_FILE)
    except Exception as e:
        respond(f"❌ Failed to delete announcement: {str(e)}")


@app.command("/edit-announcement")
def handle_edit_announcement(ack, command, respond):
    """Handle /edit-announcement slash command - opens a modal for editing."""
    ack()

    user = command['user_name']
    trigger_id = command['trigger_id']
    print(f"[Socket Mode] {user} triggered /edit-announcement")

    last = load_last_announcement()
    if not last:
        respond("No announcement found to edit. Post an announcement first using 'Good to Announce'.")
        return

    def _split_text_chunks(text: str, size: int = 3000, parts: int = 3):
        chunks = []
        remaining = text or ""
        for _ in range(parts):
            chunks.append(remaining[:size])
            remaining = remaining[size:]
        return chunks

    try:
        part1, part2, part3 = _split_text_chunks(last.get('text', ''), 3000, 3)

        def _input_block(block_id, action_id, label, initial_value):
            element = {
                "type": "plain_text_input",
                "action_id": action_id,
                "multiline": True,
                "max_length": 3000
            }
            if initial_value:
                element["initial_value"] = initial_value
            else:
                element["placeholder"] = {"type": "plain_text", "text": "Add text..."}
            return {
                "type": "input",
                "block_id": block_id,
                "optional": True if block_id != "announcement_text_1" else False,
                "element": element,
                "label": {"type": "plain_text", "text": label}
            }
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "edit_announcement_modal",
                "title": {"type": "plain_text", "text": "Edit Announcement"},
                "submit": {"type": "plain_text", "text": "Update"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": json.dumps({
                    'channel': last.get('channel'),
                    'message_ts': last.get('message_ts')
                }),
                "blocks": [
                    _input_block("announcement_text_1", "text_input_1", "Announcement Text (Part 1)", part1),
                    _input_block("announcement_text_2", "text_input_2", "Announcement Text (Part 2)", part2),
                    _input_block("announcement_text_3", "text_input_3", "Announcement Text (Part 3)", part3)
                ]
            }
        )
    except Exception as e:
        respond(f"❌ Failed to open edit modal: {str(e)}")


@app.view("edit_announcement_modal")
def handle_edit_modal_submission(ack, body, view):
    """Handle the edit announcement modal submission."""
    ack()

    values = view.get('state', {}).get('values', {})
    part1 = values.get('announcement_text_1', {}).get('text_input_1', {}).get('value', '') or ''
    part2 = values.get('announcement_text_2', {}).get('text_input_2', {}).get('value', '') or ''
    part3 = values.get('announcement_text_3', {}).get('text_input_3', {}).get('value', '') or ''
    new_text = "\n".join([p for p in [part1, part2, part3] if p.strip()]) or part1
    metadata = json.loads(view.get('private_metadata', '{}'))
    channel = metadata.get('channel')
    message_ts = metadata.get('message_ts')

    if not channel or not message_ts:
        print("[Socket Mode] Missing channel or message_ts in edit modal")
        return

    # Load fix version and epic URLs for auto-formatting
    try:
        with open('processed_notes.json', 'r') as f:
            processed_data = json.load(f)
    except Exception:
        processed_data = {}

    fix_version_urls = processed_data.get('fix_version_urls', {})
    epic_urls_by_pl = processed_data.get('epic_urls_by_pl', {})
    epic_urls_flat = processed_data.get('epic_urls', {})

    def _normalize_epic_key(text: str) -> str:
        text = re.sub(r'\s+', ' ', text.strip().lower())
        text = re.sub(r'\s*:\s*', ':', text)
        return text

    # Flatten epic URLs for easier lookup
    all_epic_urls = {}
    for _, epics in epic_urls_by_pl.items():
        if epics:
            for epic_name, epic_url in epics.items():
                all_epic_urls[_normalize_epic_key(epic_name)] = (epic_name, epic_url)
    if epic_urls_flat:
        for epic_name, epic_url in epic_urls_flat.items():
            all_epic_urls[_normalize_epic_key(epic_name)] = (epic_name, epic_url)

    def auto_format_text(text: str) -> str:
        lines = text.split('\n')
        formatted_lines = []

        def strip_formatting(s: str) -> str:
            s = s.strip('*')
            s = re.sub(r'^\s*#{2,}\s*', '', s)
            link_match = re.match(r'^<[^|]+\|(.+)>$', s)
            if link_match:
                s = link_match.group(1).strip('*')
            return s.strip()

        def _parse_slack_link(s: str):
            match = re.match(r'^<([^|>]+)\|(.+)>$', s)
            if not match:
                return None
            return match.group(1), match.group(2)

        in_value_add = False
        in_bug_fixes = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                formatted_lines.append('')
                in_value_add = False
                in_bug_fixes = False
                continue

            parsed_link = _parse_slack_link(stripped)
            if parsed_link:
                url, link_text = parsed_link
                link_clean = strip_formatting(link_text)
                if link_clean.startswith("Release "):
                    formatted_lines.append(stripped)
                else:
                    formatted_lines.append(f"<{url}|*{link_clean}*>")
                in_value_add = False
                in_bug_fixes = False
                continue

            stripped = re.sub(r'^\s*#{2,}\s*', '', stripped)
            bullet_stripped = re.sub(r'^[●•\*\-]\s*', '', stripped)
            clean_text = strip_formatting(bullet_stripped)
            clean_lower = _normalize_epic_key(clean_text)

            if clean_lower in ('value add:', 'value add') and not stripped.startswith('*'):
                formatted_lines.append('*Value Add:*')
                in_value_add = True
                in_bug_fixes = False
                continue
            if clean_lower in ('bug fixes:', 'bug fixes') and not stripped.startswith('*'):
                formatted_lines.append('*Bug Fixes:*')
                in_bug_fixes = True
                in_value_add = False
                continue

            if clean_text in ('General Availability', 'Feature Flag', 'Beta') and not stripped.startswith('`'):
                formatted_lines.append(f'`{clean_text}`')
                in_value_add = False
                in_bug_fixes = False
                continue

            release_match = re.match(r'^\*?([^*:]+)\*?:\s*(Release\s+\d+\.\d+)$', stripped)
            if release_match and '<' not in stripped:
                pl_name = release_match.group(1).strip()
                release_ver = release_match.group(2)
                url = None
                pl_name_lower = pl_name.lower()
                for stored_pl, stored_url in fix_version_urls.items():
                    stored_pl_lower = stored_pl.lower()
                    stored_pl_clean = re.sub(r'\s+20\d{2}$', '', stored_pl_lower)
                    pl_name_clean = re.sub(r'\s+20\d{2}$', '', pl_name_lower)
                    if (pl_name_lower == stored_pl_lower or
                        pl_name_clean == stored_pl_clean or
                        pl_name_lower in stored_pl_lower or
                        stored_pl_lower in pl_name_lower):
                        url = stored_url
                        break
                if url:
                    formatted_lines.append(f"*{pl_name}*: <{url}|{release_ver}>")
                    in_value_add = False
                    in_bug_fixes = False
                    continue

            md_link = re.match(r'^\[([^\]]+)\]\(([^)]+)\)$', clean_text)
            if md_link:
                md_text = md_link.group(1).strip()
                md_url = md_link.group(2).strip()
                formatted_lines.append(f"<{md_url}|*{md_text}*>")
                continue

            clean_words = set(clean_lower.split())
            found_epic = False
            for epic_lower, (epic_name, epic_url) in all_epic_urls.items():
                if clean_lower == epic_lower:
                    formatted_lines.append(f"<{epic_url}|*{clean_text}*>")
                    found_epic = True
                    break
                if epic_lower in clean_lower or clean_lower in epic_lower:
                    formatted_lines.append(f"<{epic_url}|*{clean_text}*>")
                    found_epic = True
                    break
                epic_words = set(epic_lower.split())
                common_words = clean_words & epic_words
                if len(epic_words) > 0 and len(clean_words) > 0:
                    forward_ratio = len(common_words) / len(epic_words)
                    reverse_ratio = len(common_words) / len(clean_words)
                    if forward_ratio >= 0.7 or reverse_ratio >= 0.7:
                        formatted_lines.append(f"<{epic_url}|*{clean_text}*>")
                        found_epic = True
                        break

            if found_epic:
                in_value_add = False
                in_bug_fixes = False
                continue

            # Skip bullets/prose from epic matching
            if '<' in stripped or stripped.startswith(('●', '•', '-', '*', '`')):
                formatted_lines.append(stripped)
                continue
            if in_value_add or in_bug_fixes:
                formatted_lines.append(f"• {stripped}")
            else:
                formatted_lines.append(stripped)

        return '\n'.join(formatted_lines)

    formatted_text = auto_format_text(new_text)

    def _build_text_blocks(text: str, chunk_size: int = 3000):
        chunks = []
        remaining = text or ""
        while remaining:
            chunks.append(remaining[:chunk_size])
            remaining = remaining[chunk_size:]
        return [{"type": "section", "text": {"type": "mrkdwn", "text": chunk}} for chunk in chunks] or [
            {"type": "section", "text": {"type": "mrkdwn", "text": ""}}
        ]

    try:
        client.chat_update(
            channel=channel,
            ts=message_ts,
            text=formatted_text[:40000],
            blocks=_build_text_blocks(formatted_text)
        )
        save_last_announcement(channel, message_ts, formatted_text)
        try:
            client.chat_postMessage(channel=body['user']['id'], text="✅ Announcement updated successfully!")
        except Exception:
            pass
    except Exception as e:
        print(f"[Socket Mode] Error updating announcement: {e}")


def post_approval_message(pls: list = None, doc_url: str = None, release_date: str = None, notes_by_pl: dict = None):
    channel = os.getenv('SLACK_REVIEW_CHANNEL')
    if not channel:
        return None

    if not pls:
        try:
            with open('processed_notes.json', 'r') as f:
                data = json.load(f)
                pls = data.get('product_lines', [])
                if not doc_url:
                    doc_id = os.getenv('GOOGLE_DOC_ID')
                    if doc_id:
                        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
                if not release_date:
                    release_date = data.get('release_summary', '').replace('Release ', '')
                if not notes_by_pl:
                    notes_by_pl = data.get('body_by_pl', {})
        except Exception:
            pls = []

    clean_pls = [re.sub(r'\s+20\d{2}$', '', pl) for pl in pls]

    today = datetime.now().strftime('%Y-%m-%d')
    deferred_data = load_deferred_pls()
    if today in deferred_data:
        for deferred in deferred_data[today]:
            if deferred['pl'] not in clean_pls:
                clean_pls.append(deferred['pl'])

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Release Notes Review", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"Daily Consolidated Deployment Summary" + (f" - {release_date}" if release_date else "")}}
    ]
    if doc_url:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"<{doc_url}|📄 View Release Notes>"}})
    blocks.extend(build_refresh_blocks())
    blocks.append({"type": "divider"})
    blocks.extend(build_pl_blocks(clean_pls))
    blocks.append({"type": "divider"})
    blocks.extend(build_footer_blocks(pls=clean_pls))

    result = client.chat_postMessage(channel=channel, text=f"Release Notes Review - {release_date}", blocks=blocks)
    message_ts = result['ts']

    message_metadata = load_message_metadata()
    message_metadata[message_ts] = {
        'pls': clean_pls,
        'doc_url': doc_url,
        'release_date': release_date,
        'notes_by_pl': notes_by_pl or {},
        'channel': channel
    }
    save_message_metadata(message_metadata)
    return message_ts


def main():
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token:
        print("ERROR: SLACK_APP_TOKEN not found!")
        return
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
