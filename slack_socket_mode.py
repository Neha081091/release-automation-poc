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


def _extract_epics_from_body(body_text: str) -> list:
    epics = []
    current = None

    def _is_epic_header(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("●") or stripped.startswith("•") or stripped.startswith("-"):
            return False
        lowered = stripped.lower()
        if lowered.startswith("value add:") or lowered.startswith("bug fixes:"):
            return False
        if stripped in ("Value Add:", "Bug Fixes:", "General Availability", "Feature Flag", "Beta"):
            return False
        return True

    for line in body_text.splitlines():
        if _is_epic_header(line):
            current = line.strip()
            if current not in epics:
                epics.append(current)
    return epics


def _split_body_by_epic(body_text: str) -> list:
    sections = []
    current_epic = None
    current_lines = []

    def _is_epic_header(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("●") or stripped.startswith("•") or stripped.startswith("-"):
            return False
        if stripped in ("Value Add:", "Bug Fixes:", "General Availability", "Feature Flag", "Beta"):
            return False
        return True

    for line in body_text.splitlines():
        if _is_epic_header(line):
            if current_epic:
                sections.append((current_epic, current_lines))
            current_epic = line.strip()
            current_lines = [line]
        else:
            if current_epic:
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
    deferred_set = {e.lower().strip() for e in deferred_epics}
    sections = _split_body_by_epic(body_text)
    kept = []
    for epic, lines in sections:
        if epic.lower().strip() in deferred_set:
            continue
        kept.extend(lines)
        kept.append("")
    return "\n".join(kept).strip() + ("\n" if kept else "")


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
        from google_docs_formatter import GoogleDocsFormatter

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
        tldr_line = f"{pl_clean} - {tldr_summary}\n"

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

        formatter = GoogleDocsFormatter()
        formatter.reset()
        release_ver = deferred_pl_data.get('release_version', 'Release 1.0')
        fix_version_url = deferred_pl_data.get('fix_version_url', '')
        epic_urls = deferred_pl_data.get('epic_urls', {})
        body_text = deferred_pl_data.get('body', '')

        formatter._insert_text("\n")
        formatter._insert_text(f"{pl_clean}: ")
        ver_start = formatter.current_index
        formatter._insert_text(f"{release_ver}\n")
        ver_end = ver_start + len(release_ver)
        if fix_version_url:
            formatter._mark_link(ver_start, ver_end, fix_version_url)

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

        body_insert_index = _text_pos_to_doc_index(section_end)
        tldr_insert_index = _text_pos_to_doc_index(tldr_insert_text_pos) if tldr_insert_text_pos is not None else None

        jobs = []
        if tldr_insert_index is not None and tldr_line:
            tldr_requests = [{"insertText": {"location": {"index": tldr_insert_index}, "text": tldr_line}}]
            tldr_format = [
                {"updateTextStyle": {"range": {"startIndex": tldr_insert_index, "endIndex": tldr_insert_index + len(tldr_line)}, "textStyle": {"bold": False}, "fields": "bold"}},
                {"updateTextStyle": {"range": {"startIndex": tldr_insert_index, "endIndex": tldr_insert_index + len(pl_clean)}, "textStyle": {"bold": True}, "fields": "bold"}}
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
                pl_data['epic_urls'] = processed_data.get('epic_urls_by_pl', {}).get(original_pl, {})
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
    for pl in approved_pls:
        body = body_by_pl.get(pl, "") or body_by_pl.get(pl.replace(' 2026', ''), "")
        if pl in deferred_partial and body:
            body = _filter_body_by_deferred_epics(body, deferred_partial.get(pl, []))
        if body and body.strip():
            announced_pls.append(pl)
            body_for_pl[pl] = body

    announcement_text = f"*Daily Deployment Summary: {release_date}*\n\n"
    announcement_text += "------------------TL;DR:------------------\n\n"
    announcement_text += "*Key Deployments:*\n"
    for pl in announced_pls:
        tldr = tldr_by_pl.get(pl) or tldr_by_pl.get(pl.replace(' 2026', ''))
        if tldr:
            announcement_text += f"● *{pl}* - {tldr}\n"
    announcement_text += "\n"

    for pl in announced_pls:
        version = release_versions.get(pl, "") or release_versions.get(pl.replace(' 2026', ''), "")
        announcement_text += f"------------------{pl}------------------\n"
        if version:
            announcement_text += f"{pl}: {version}\n"
        body = body_for_pl.get(pl, "")
        if body:
            announcement_text += f"{body}\n\n"

    try:
        result = client.chat_postMessage(channel=announce_channel, text=f"Daily Deployment Summary: {release_date}", blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": announcement_text}
        }])
        save_json(LAST_ANNOUNCEMENT_FILE, {"channel": announce_channel, "message_ts": result.get('ts'), "text": announcement_text})
    except Exception:
        pass

    deferred_partial_pls = list(deferred_partial.keys())
    final_blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"✅ *Release Announced Successfully*\n\nAnnounced by @{user} at {datetime.now().strftime('%Y-%m-%d %H:%M')}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"• Approved: {', '.join(approved_pls) if approved_pls else 'None'}\n• Deferred (Full): {', '.join(deferred_full_pls) if deferred_full_pls else 'None'}\n• Deferred (Partial): {', '.join(deferred_partial_pls) if deferred_partial_pls else 'None'}\n• Tomorrow: {', '.join(tomorrow_pls) if tomorrow_pls else 'None'}"}}
    ]
    client.chat_update(channel=channel, ts=message_ts, blocks=final_blocks, text="Release has been announced!")


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
