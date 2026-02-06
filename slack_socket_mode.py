"""
Slack Socket Mode Handler for Interactive Buttons

This uses Slack's Socket Mode - no public URL or ngrok needed!
Slack connects directly to your app via WebSocket.

Features:
- Dynamic PL buttons from processed_notes.json
- Buttons disable after selection (Approve/Reject/Tomorrow)
- "X PL(s) pending review" counter
- Good to Announce only enabled when all PLs reviewed
- Tomorrow defers PL to next day's release

Setup:
1. Go to your Slack App settings: https://api.slack.com/apps
2. Click "Socket Mode" in the left sidebar
3. Toggle "Enable Socket Mode" ON
4. Generate an App-Level Token with "connections:write" scope
5. Add the token to your .env as SLACK_APP_TOKEN

Usage:
    python slack_socket_mode.py
"""

import os
import json
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

load_dotenv()

# Product Line order - grouped by category for consistent display
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
    """Sort product lines according to PRODUCT_LINE_ORDER."""
    ordered = []
    # First add PLs that are in the preferred order
    for pl in PRODUCT_LINE_ORDER:
        if pl in pl_list:
            ordered.append(pl)
    # Then add any PLs not in the preferred order (at the end)
    for pl in pl_list:
        if pl not in ordered:
            ordered.append(pl)
    return ordered


# Initialize the Bolt app with bot token
app = App(token=os.getenv("SLACK_BOT_TOKEN"))

# Slack client for API calls
client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# File paths for state persistence (shared between processes)
APPROVAL_STATES_FILE = 'approval_states.json'
MESSAGE_METADATA_FILE = 'message_metadata.json'
DEFERRED_PLS_FILE = 'deferred_pls.json'


def load_approval_states() -> dict:
    """Load approval states from file."""
    try:
        with open(APPROVAL_STATES_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[Socket Mode] Error loading approval states: {e}")
        return {}


def save_approval_states(states: dict):
    """Save approval states to file."""
    try:
        with open(APPROVAL_STATES_FILE, 'w') as f:
            json.dump(states, f, indent=2)
    except Exception as e:
        print(f"[Socket Mode] Error saving approval states: {e}")


def load_message_metadata() -> dict:
    """Load message metadata from file."""
    try:
        with open(MESSAGE_METADATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[Socket Mode] Error loading message metadata: {e}")
        return {}


def save_message_metadata(metadata: dict):
    """Save message metadata to file."""
    try:
        with open(MESSAGE_METADATA_FILE, 'w') as f:
            json.dump(metadata, f, indent=2)
    except Exception as e:
        print(f"[Socket Mode] Error saving message metadata: {e}")


def load_deferred_pls() -> dict:
    """Load deferred PLs from file."""
    try:
        with open(DEFERRED_PLS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[Socket Mode] Error loading deferred PLs: {e}")
        return {}


def save_deferred_pls(deferred: dict):
    """Save deferred PLs to file."""
    try:
        with open(DEFERRED_PLS_FILE, 'w') as f:
            json.dump(deferred, f, indent=2)
    except Exception as e:
        print(f"[Socket Mode] Error saving deferred PLs: {e}")


def get_pl_name_from_action(action_id: str) -> str:
    """Extract PL name from action_id like 'approve_Helix_PL2'."""
    # action_id format: action_PLName (with underscores replacing spaces)
    parts = action_id.split('_', 1)
    if len(parts) > 1:
        return parts[1].replace('_', ' ')
    return action_id


def clean_pl_name_for_action(pl_name: str) -> str:
    """Convert PL name to action_id safe format."""
    # Replace spaces and special chars with underscores
    return re.sub(r'[^a-zA-Z0-9]', '_', pl_name)


def count_pending_reviews(message_ts: str) -> int:
    """Count how many PLs are still pending review."""
    message_metadata = load_message_metadata()
    approval_states = load_approval_states()

    if message_ts not in message_metadata:
        return 0

    all_pls = message_metadata[message_ts].get('pls', [])
    reviewed = approval_states.get(message_ts, {})

    return len(all_pls) - len(reviewed)


def all_pls_reviewed(message_ts: str) -> bool:
    """Check if all PLs have been reviewed."""
    return count_pending_reviews(message_ts) == 0


def build_pl_blocks(pls: list, message_ts: str = None) -> list:
    """Build Slack blocks for PL review sections."""
    blocks = []
    approval_states = load_approval_states()

    for pl in pls:
        pl_action_id = clean_pl_name_for_action(pl)

        # Check if this PL has been reviewed
        reviewed_status = None
        reviewed_by = None
        if message_ts and message_ts in approval_states:
            pl_state = approval_states[message_ts].get(pl)
            if pl_state:
                reviewed_status = pl_state['status']
                reviewed_by = pl_state['user']

        if reviewed_status:
            # Show reviewed status with disabled buttons
            status_emoji = {
                'approved': '✅',
                'rejected': '❌',
                'tomorrow': '⏰'
            }
            status_text = {
                'approved': 'Approved',
                'rejected': 'Deferred',
                'tomorrow': 'Moved to Tomorrow'
            }

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{pl}*\n{status_emoji.get(reviewed_status, '❓')} {status_text.get(reviewed_status, reviewed_status)} by @{reviewed_by}"
                }
            })
        else:
            # Show pending with active buttons
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{pl}*"
                }
            })
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✓ Approve", "emoji": True},
                        "style": "primary",
                        "action_id": f"approve_{pl_action_id}"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✗ Defer", "emoji": True},
                        "style": "danger",
                        "action_id": f"reject_{pl_action_id}"
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "⏰ Tomorrow", "emoji": True},
                        "action_id": f"tomorrow_{pl_action_id}"
                    }
                ]
            })

    return blocks


def build_footer_blocks(message_ts: str = None, pls: list = None) -> list:
    """Build footer with pending count and Good to Announce button."""
    blocks = []

    pending_count = count_pending_reviews(message_ts) if message_ts else len(pls or [])
    all_reviewed = pending_count == 0

    # Pending count section
    if pending_count > 0:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"⏳ *{pending_count} PL(s) pending review*"
                }
            ]
        })
    else:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "✅ *All PLs reviewed!*"
                }
            ]
        })

    # Good to Announce button
    if all_reviewed:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✓ Good to Announce", "emoji": True},
                    "style": "primary",
                    "action_id": "good_to_announce"
                }
            ]
        })
    else:
        # Disabled-looking button (Slack doesn't support disabled, so we use a different style)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_Good to Announce (review all PLs first)_"
            }
        })

    return blocks


def update_message_with_status(channel: str, message_ts: str):
    """Update the entire message to reflect current approval states."""
    message_metadata = load_message_metadata()

    if message_ts not in message_metadata:
        print(f"[Socket Mode] Warning: message_ts {message_ts} not found in metadata")
        return

    pls = message_metadata[message_ts].get('pls', [])
    doc_url = message_metadata[message_ts].get('doc_url', '')
    release_date = message_metadata[message_ts].get('release_date', '')

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Release Notes Review",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Daily Consolidated Deployment Summary" + (f" - {release_date}" if release_date else "")
            }
        }
    ]

    if doc_url:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<{doc_url}|📄 View Release Notes>"
            }
        })

    blocks.append({"type": "divider"})

    # Add PL blocks
    blocks.extend(build_pl_blocks(pls, message_ts))

    blocks.append({"type": "divider"})

    # Add footer
    blocks.extend(build_footer_blocks(message_ts, pls))

    try:
        client.chat_update(
            channel=channel,
            ts=message_ts,
            blocks=blocks,
            text="Release Notes Review"
        )
    except Exception as e:
        print(f"[Socket Mode] Error updating message: {e}")


# Generic action handler that matches any PL
@app.action(re.compile(r"^approve_.+$"))
def handle_approve(ack, body, action):
    """Handle Approve button clicks for any PL."""
    ack()

    pl_name = get_pl_name_from_action(action['action_id'])
    user = body['user']['username']
    message_ts = body['message']['ts']
    channel = body['channel']['id']

    print(f"[Socket Mode] {user} APPROVED {pl_name}")

    # Load, update, and save approval states
    approval_states = load_approval_states()
    if message_ts not in approval_states:
        approval_states[message_ts] = {}

    approval_states[message_ts][pl_name] = {
        'status': 'approved',
        'user': user,
        'timestamp': datetime.now().isoformat()
    }
    save_approval_states(approval_states)

    update_message_with_status(channel, message_ts)


@app.action(re.compile(r"^reject_.+$"))
def handle_reject(ack, body, action):
    """Handle Reject button clicks for any PL."""
    ack()

    pl_name = get_pl_name_from_action(action['action_id'])
    user = body['user']['username']
    message_ts = body['message']['ts']
    channel = body['channel']['id']

    print(f"[Socket Mode] {user} REJECTED {pl_name}")

    # Load, update, and save approval states
    approval_states = load_approval_states()
    if message_ts not in approval_states:
        approval_states[message_ts] = {}

    approval_states[message_ts][pl_name] = {
        'status': 'rejected',
        'user': user,
        'timestamp': datetime.now().isoformat()
    }
    save_approval_states(approval_states)

    update_message_with_status(channel, message_ts)


@app.action(re.compile(r"^tomorrow_.+$"))
def handle_tomorrow(ack, body, action):
    """Handle Tomorrow button clicks for any PL.

    This will:
    1. Mark the PL as 'tomorrow' in approval states
    2. Save complete PL data for tomorrow's release
    3. Remove the PL section from today's Google Doc
    """
    ack()

    pl_name = get_pl_name_from_action(action['action_id'])
    user = body['user']['username']
    message_ts = body['message']['ts']
    channel = body['channel']['id']

    print(f"[Socket Mode] {user} marked {pl_name} for TOMORROW")

    # Load, update, and save approval states
    approval_states = load_approval_states()
    if message_ts not in approval_states:
        approval_states[message_ts] = {}

    approval_states[message_ts][pl_name] = {
        'status': 'tomorrow',
        'user': user,
        'timestamp': datetime.now().isoformat()
    }
    save_approval_states(approval_states)

    # Store complete PL data for tomorrow's release
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    deferred_pls = load_deferred_pls()
    if tomorrow not in deferred_pls:
        deferred_pls[tomorrow] = []

    # Load processed_notes.json to get complete PL data
    message_metadata = load_message_metadata()
    pl_notes = message_metadata.get(message_ts, {}).get('notes_by_pl', {}).get(pl_name, '')

    # Try to load full processed data for more complete info
    pl_data = {
        'pl': pl_name,
        'notes': pl_notes,
        'deferred_by': user,
        'deferred_at': datetime.now().isoformat()
    }

    try:
        with open('processed_notes.json', 'r') as f:
            processed_data = json.load(f)

        # Find the original PL name (may have year suffix)
        original_pl = None
        for pl in processed_data.get('product_lines', []):
            if pl_name in pl or pl in pl_name or pl.replace(' 2026', '').replace(' 2025', '') == pl_name:
                original_pl = pl
                break

        if original_pl:
            # Save complete data for tomorrow
            pl_data['tldr'] = processed_data.get('tldr_by_pl', {}).get(original_pl, '')
            pl_data['body'] = processed_data.get('body_by_pl', {}).get(original_pl, '')
            pl_data['release_version'] = processed_data.get('release_versions', {}).get(original_pl, 'Release 1.0')
            pl_data['fix_version_url'] = processed_data.get('fix_version_urls', {}).get(original_pl, '')
            pl_data['epic_urls'] = processed_data.get('epic_urls_by_pl', {}).get(original_pl, {})
            print(f"[Socket Mode] Loaded complete data for {pl_name}")

    except FileNotFoundError:
        print("[Socket Mode] processed_notes.json not found, saving minimal data")
    except Exception as e:
        print(f"[Socket Mode] Error loading processed data: {e}")

    deferred_pls[tomorrow].append(pl_data)
    save_deferred_pls(deferred_pls)
    print(f"[Socket Mode] Saved deferred PL {pl_name} for {tomorrow}")

    # Remove PL section from today's Google Doc
    try:
        from google_docs_handler import GoogleDocsHandler

        print(f"[Socket Mode] Attempting to remove PL from Google Doc: '{pl_name}'")
        google_docs = GoogleDocsHandler()
        if google_docs.authenticate():
            print(f"[Socket Mode] Google Docs authenticated, calling remove_pl_section('{pl_name}')")
            if google_docs.remove_pl_section(pl_name):
                print(f"[Socket Mode] Successfully removed {pl_name} section from Google Doc")
            else:
                print(f"[Socket Mode] Could not remove {pl_name} from Google Doc (may need manual removal)")
        else:
            print("[Socket Mode] Could not authenticate with Google Docs")
    except Exception as e:
        print(f"[Socket Mode] Error removing from Google Doc: {e}")
        import traceback
        traceback.print_exc()

    update_message_with_status(channel, message_ts)


@app.action("good_to_announce")
def handle_good_to_announce(ack, body):
    """Handle Good to Announce button click."""
    ack()

    user = body['user']['username']
    channel = body['channel']['id']
    message_ts = body['message']['ts']

    print(f"[Socket Mode] {user} clicked GOOD TO ANNOUNCE")

    # Check if all PLs are reviewed
    if not all_pls_reviewed(message_ts):
        print("[Socket Mode] Not all PLs reviewed yet!")
        return

    # Load state from files
    approval_states = load_approval_states()
    message_metadata = load_message_metadata()

    # Get approved PLs
    approved_pls = []
    rejected_pls = []
    tomorrow_pls = []

    for pl, state in approval_states.get(message_ts, {}).items():
        if state['status'] == 'approved':
            approved_pls.append(pl)
        elif state['status'] == 'rejected':
            rejected_pls.append(pl)
        elif state['status'] == 'tomorrow':
            tomorrow_pls.append(pl)

    # Sort PLs according to PRODUCT_LINE_ORDER for consistent display
    approved_pls = get_ordered_pls(approved_pls)

    # Build announcement message
    announce_channel = os.getenv('SLACK_ANNOUNCE_CHANNEL', channel)
    doc_url = message_metadata.get(message_ts, {}).get('doc_url', '')
    release_date = message_metadata.get(message_ts, {}).get('release_date', datetime.now().strftime('%d %B %Y'))

    # Load processed notes for full content
    try:
        with open('processed_notes.json', 'r') as f:
            processed_data = json.load(f)
    except Exception as e:
        print(f"[Socket Mode] Error loading processed notes: {e}")
        processed_data = {}

    tldr_by_pl = processed_data.get('tldr_by_pl', {})
    body_by_pl = processed_data.get('body_by_pl', {})
    release_versions = processed_data.get('release_versions', {})
    fix_version_urls = processed_data.get('fix_version_urls', {})
    epic_urls_by_pl = processed_data.get('epic_urls_by_pl', {})

    # Debug logging for URL data
    print(f"[Socket Mode] Data loaded - PLs with release versions: {list(release_versions.keys())}")
    print(f"[Socket Mode] Data loaded - PLs with fix version URLs: {list(fix_version_urls.keys())}")
    print(f"[Socket Mode] Data loaded - PLs with epic URLs: {list(epic_urls_by_pl.keys())}")

    # Merge in deferred/carried-forward PLs from yesterday
    today = datetime.now().strftime('%Y-%m-%d')
    deferred_data = load_deferred_pls()
    if today in deferred_data:
        print(f"[Socket Mode] Found {len(deferred_data[today])} deferred PLs for today")
        for deferred in deferred_data[today]:
            pl_name = deferred.get('pl', '')
            if pl_name:
                # Add deferred PL data to the dictionaries
                if deferred.get('tldr') and pl_name not in tldr_by_pl:
                    tldr_by_pl[pl_name] = deferred['tldr']
                    print(f"[Socket Mode] Added deferred TLDR for {pl_name}")
                if deferred.get('body') and pl_name not in body_by_pl:
                    body_by_pl[pl_name] = deferred['body']
                    print(f"[Socket Mode] Added deferred body for {pl_name}")
                if deferred.get('release_version') and pl_name not in release_versions:
                    release_versions[pl_name] = deferred['release_version']
                if deferred.get('fix_version_url') and pl_name not in fix_version_urls:
                    fix_version_urls[pl_name] = deferred['fix_version_url']
                if deferred.get('epic_urls') and pl_name not in epic_urls_by_pl:
                    epic_urls_by_pl[pl_name] = deferred['epic_urls']

    # Flatten all epic URLs for better matching across PLs
    all_epic_urls = {}
    for pl, epics in epic_urls_by_pl.items():
        if epics:
            for epic_name, epic_url in epics.items():
                all_epic_urls[epic_name] = epic_url

    if all_epic_urls:
        print(f"[Socket Mode] Loaded {len(all_epic_urls)} epic URLs for formatting")
    else:
        print("[Socket Mode] Warning: No epic URLs found for hyperlinking")

    def format_body_for_slack(pl_body: str, epic_urls: dict) -> str:
        """Format body content with proper Slack markdown."""
        if not pl_body:
            return ""

        # Use both PL-specific and all epic URLs for matching
        combined_epic_urls = {**all_epic_urls, **epic_urls}

        def find_epic_url_flexible(text: str, epic_urls_dict: dict) -> tuple:
            """Find matching epic URL using flexible matching. Returns (url, matched).

            Uses bidirectional matching to detect epic names even when:
            - Body text has a shortened version of the epic name
            - Epic name has extra words compared to body text
            - Case differences exist
            """
            text_lower = text.lower().strip()

            # Direct match first
            if text in epic_urls_dict:
                return epic_urls_dict[text], True

            # Case-insensitive match
            for epic_name, url in epic_urls_dict.items():
                if epic_name.lower() == text_lower:
                    return url, True

            # Check if text contains epic name or vice versa (substring matching)
            for epic_name, url in epic_urls_dict.items():
                if epic_name.lower() in text_lower or text_lower in epic_name.lower():
                    return url, True

            # Bidirectional partial word match - check if most words match in EITHER direction
            # This catches cases where body text is a shortened version of the epic name
            for epic_name, url in epic_urls_dict.items():
                epic_lower = epic_name.lower()
                text_words = set(text_lower.split())
                epic_words = set(epic_lower.split())
                common_words = text_words & epic_words

                if len(epic_words) > 0 and len(text_words) > 0:
                    # Forward match: what % of epic words appear in text
                    forward_ratio = len(common_words) / len(epic_words)
                    # Reverse match: what % of text words appear in epic
                    reverse_ratio = len(common_words) / len(text_words)

                    # Match if either direction passes 70% threshold
                    # This handles shortened epic names in body text
                    if forward_ratio >= 0.7 or reverse_ratio >= 0.7:
                        return url, True

            return "", False

        def is_likely_epic_name(text: str) -> bool:
            """Check if text looks like an epic name (not a prose sentence)."""
            return (
                len(text) < 150 and
                not text.endswith('.') and
                not text.endswith(':') and
                not text.startswith('http') and
                not text.lower().startswith(('value add', 'bug fix', 'general availability', 'feature flag'))
            )

        lines = pl_body.split('\n')
        formatted_lines = []
        seen_headers = set()  # Track headers to avoid duplicates

        for line in lines:
            stripped = line.strip()

            # Skip empty lines but keep structure
            if not stripped:
                formatted_lines.append('')
                continue

            # Check for duplicate headers (Bug Fixes, Value Add)
            if stripped.lower() in ('bug fixes', 'bug fixes:'):
                if 'bug_fixes' in seen_headers:
                    continue  # Skip duplicate
                seen_headers.add('bug_fixes')
                formatted_lines.append('*Bug Fixes:*')
                continue

            if stripped.lower() in ('value add', 'value add:'):
                if 'value_add' in seen_headers:
                    continue  # Skip duplicate
                seen_headers.add('value_add')
                formatted_lines.append('*Value Add:*')
                continue

            # Format release type indicators as code
            if stripped in ('General Availability', 'Feature Flag', 'Beta'):
                formatted_lines.append(f'`{stripped}`')
                continue

            # Check if this might be an epic name (non-header, non-bullet line)
            if not stripped.startswith(('●', '•', '*', '-')):
                # Try to find matching epic URL using combined URLs
                epic_url, matched = find_epic_url_flexible(stripped, combined_epic_urls)

                if matched and epic_url:
                    # Bold and hyperlink the epic name
                    formatted_lines.append(f"<{epic_url}|*{stripped}*>")
                    seen_headers = set()  # Reset for new epic section
                elif is_likely_epic_name(stripped):
                    # Bold the epic name even without URL
                    formatted_lines.append(f"*{stripped}*")
                    seen_headers = set()  # Reset for new epic section
                else:
                    formatted_lines.append(stripped)
            else:
                formatted_lines.append(stripped)

        return '\n'.join(formatted_lines)

    # Build announcement in exact Google Doc format
    announcement_text = f"*Daily Deployment Summary: {release_date}*\n\n"
    announcement_text += "------------------TL;DR:------------------\n\n"
    announcement_text += "*Key Deployments:*\n"

    # TL;DR bullets for approved PLs only
    for pl in approved_pls:
        pl_tldr = None
        for orig_pl, tldr in tldr_by_pl.items():
            if pl in orig_pl or orig_pl in pl or orig_pl.replace(' 2026', '').replace(' 2025', '') == pl:
                pl_tldr = tldr
                break
        if pl_tldr:
            announcement_text += f"● *{pl}* - {pl_tldr}\n"

    announcement_text += "\n"

    def find_pl_data(pl_name: str, data_dict: dict) -> tuple:
        """Find data for a PL using flexible matching. Returns (value, matched_key)."""
        # Clean PL name for matching
        pl_clean = re.sub(r'\s+20\d{2}$', '', pl_name)
        pl_lower = pl_clean.lower()

        # Try exact match first
        if pl_name in data_dict:
            return data_dict[pl_name], pl_name

        # Try cleaned name
        if pl_clean in data_dict:
            return data_dict[pl_clean], pl_clean

        # Try case-insensitive and partial matching
        for key in data_dict.keys():
            key_clean = re.sub(r'\s+20\d{2}$', '', key)
            key_lower = key_clean.lower()

            if key_lower == pl_lower or pl_lower in key_lower or key_lower in pl_lower:
                return data_dict[key], key

        return None, None

    # Detailed sections for each approved PL
    print(f"[Socket Mode] Building announcement for approved PLs: {approved_pls}")
    for pl in approved_pls:
        pl_body = None
        pl_version = None
        pl_fix_url = None
        pl_epics = {}
        orig_pl_name = pl

        # Find body content
        pl_body, body_key = find_pl_data(pl, body_by_pl)
        if body_key:
            orig_pl_name = body_key

        # Find release version (might be under different key)
        pl_version, version_key = find_pl_data(pl, release_versions)

        # Find fix version URL (might be under different key)
        pl_fix_url, url_key = find_pl_data(pl, fix_version_urls)

        # Find epic URLs (might be under different key)
        pl_epics, epics_key = find_pl_data(pl, epic_urls_by_pl)
        if pl_epics is None:
            pl_epics = {}

        # Debug: Log what we found for this PL
        print(f"[Socket Mode] PL '{pl}': version={pl_version} (key={version_key}), fix_url={'Yes' if pl_fix_url else 'No'} (key={url_key}), epics={len(pl_epics) if pl_epics else 0} (key={epics_key})")

        # PL Section Header with dashes
        announcement_text += f"------------------{pl}------------------\n"

        # Release version line with hyperlink
        if pl_version and pl_fix_url:
            announcement_text += f"{orig_pl_name}: <{pl_fix_url}|{pl_version}>\n"
        elif pl_version:
            announcement_text += f"{orig_pl_name}: {pl_version}\n"
        else:
            announcement_text += f"{orig_pl_name}\n"

        # PL Body content with formatting
        if pl_body:
            formatted_body = format_body_for_slack(pl_body, pl_epics)
            announcement_text += f"{formatted_body}\n\n"

    # Split into chunks for Slack's 3000 char limit per block
    announcement_blocks = []
    chunks = []
    current_chunk = ""

    for line in announcement_text.split('\n'):
        if len(current_chunk) + len(line) + 1 > 2900:
            chunks.append(current_chunk)
            current_chunk = line + '\n'
        else:
            current_chunk += line + '\n'
    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        announcement_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": chunk
            }
        })

    # Note: Rejected PLs stay in Google Doc, just excluded from Slack announcement
    # Tomorrow PLs are already removed from Google Doc when the button was clicked

    try:
        # Post announcement
        result = client.chat_postMessage(
            channel=announce_channel,
            text=f"Daily Deployment Summary: {release_date}",
            blocks=announcement_blocks
        )

        # Save announcement for later edit/delete
        announcement_ts = result.get('ts')
        save_last_announcement(announce_channel, announcement_ts, announcement_text)
        print(f"[Socket Mode] Announcement saved (ts: {announcement_ts}) - use /delete-announcement or /edit-announcement to modify")

        # Update original message to show it's been announced
        final_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"✅ *Release Announced Successfully*\n\nAnnounced by @{user} at {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"• Approved: {', '.join(approved_pls) if approved_pls else 'None'}\n• Deferred: {', '.join(rejected_pls) if rejected_pls else 'None'}\n• Tomorrow: {', '.join(tomorrow_pls) if tomorrow_pls else 'None'}"
                }
            }
        ]

        client.chat_update(
            channel=channel,
            ts=message_ts,
            blocks=final_blocks,
            text="Release has been announced!"
        )

        print(f"[Socket Mode] Announcement posted to {announce_channel}")

    except Exception as e:
        print(f"[Socket Mode] Error posting announcement: {e}")


def post_approval_message(pls: list = None, doc_url: str = None, release_date: str = None, notes_by_pl: dict = None):
    """Post an approval message with buttons to the review channel."""

    channel = os.getenv('SLACK_REVIEW_CHANNEL')
    if not channel:
        print("Error: SLACK_REVIEW_CHANNEL not set")
        return None

    # Default PLs if not provided
    if not pls:
        # Try to load from processed_notes.json
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
        except Exception as e:
            print(f"[Socket Mode] Could not load processed_notes.json: {e}")
            pls = ["Platform", "Data", "Bidder", "AdOps"]  # Fallback

    # Clean PL names (remove year suffix)
    clean_pls = []
    for pl in pls:
        clean_name = re.sub(r'\s+20\d{2}$', '', pl)
        clean_pls.append(clean_name)

    # Check for deferred PLs from yesterday
    today = datetime.now().strftime('%Y-%m-%d')
    deferred_data = load_deferred_pls()
    if today in deferred_data:
        for deferred in deferred_data[today]:
            if deferred['pl'] not in clean_pls:
                clean_pls.append(deferred['pl'])
                print(f"[Socket Mode] Added deferred PL from yesterday: {deferred['pl']}")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Release Notes Review",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Daily Consolidated Deployment Summary" + (f" - {release_date}" if release_date else "")
            }
        }
    ]

    if doc_url:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<{doc_url}|📄 View Release Notes>"
            }
        })

    blocks.append({"type": "divider"})

    # Add PL blocks (no message_ts yet, so all will be pending)
    blocks.extend(build_pl_blocks(clean_pls))

    blocks.append({"type": "divider"})

    # Add footer
    blocks.extend(build_footer_blocks(pls=clean_pls))

    try:
        result = client.chat_postMessage(
            channel=channel,
            text=f"Release Notes Review - {release_date}",
            blocks=blocks
        )
        message_ts = result['ts']

        # Load existing metadata, add new entry, and save
        message_metadata = load_message_metadata()
        message_metadata[message_ts] = {
            'pls': clean_pls,
            'doc_url': doc_url,
            'release_date': release_date,
            'notes_by_pl': notes_by_pl or {},
            'channel': channel
        }
        save_message_metadata(message_metadata)

        print(f"[Socket Mode] Approval message posted: {message_ts}")
        print(f"[Socket Mode] PLs: {clean_pls}")
        print(f"[Socket Mode] Metadata saved to {MESSAGE_METADATA_FILE}")
        return message_ts

    except Exception as e:
        print(f"[Socket Mode] Error posting message: {e}")
        return None


# File to store last announcement info
LAST_ANNOUNCEMENT_FILE = 'last_announcement.json'


def save_last_announcement(channel: str, message_ts: str, text: str):
    """Save the last announcement details for edit/delete."""
    try:
        with open(LAST_ANNOUNCEMENT_FILE, 'w') as f:
            json.dump({
                'channel': channel,
                'message_ts': message_ts,
                'text': text,
                'posted_at': datetime.now().isoformat()
            }, f, indent=2)
    except Exception as e:
        print(f"[Socket Mode] Error saving last announcement: {e}")


def load_last_announcement() -> dict:
    """Load the last announcement details."""
    try:
        with open(LAST_ANNOUNCEMENT_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[Socket Mode] Error loading last announcement: {e}")
        return {}


@app.command("/delete-announcement")
def handle_delete_announcement(ack, command, respond):
    """Handle /delete-announcement slash command."""
    ack()

    user = command['user_name']
    text = command.get('text', '').strip()

    print(f"[Socket Mode] {user} triggered /delete-announcement")

    # Get message to delete
    if text:
        # User provided specific message_ts
        parts = text.split()
        if len(parts) >= 2:
            channel = parts[0]
            message_ts = parts[1]
        else:
            # Assume it's just the message_ts, use announce channel
            channel = os.getenv('SLACK_ANNOUNCE_CHANNEL', command['channel_id'])
            message_ts = parts[0]
    else:
        # Use last announcement
        last = load_last_announcement()
        if not last:
            respond("No announcement found to delete. Use `/delete-announcement <channel_id> <message_ts>` to specify.")
            return
        channel = last.get('channel')
        message_ts = last.get('message_ts')

    try:
        client.chat_delete(channel=channel, ts=message_ts)
        respond(f"✅ Announcement deleted successfully!")
        print(f"[Socket Mode] Deleted message {message_ts} from {channel}")

        # Clear the saved announcement
        if os.path.exists(LAST_ANNOUNCEMENT_FILE):
            os.remove(LAST_ANNOUNCEMENT_FILE)

    except Exception as e:
        respond(f"❌ Failed to delete announcement: {str(e)}")
        print(f"[Socket Mode] Error deleting announcement: {e}")


@app.command("/edit-announcement")
def handle_edit_announcement(ack, command, respond):
    """Handle /edit-announcement slash command - opens a modal for editing."""
    ack()

    user = command['user_name']
    trigger_id = command['trigger_id']

    print(f"[Socket Mode] {user} triggered /edit-announcement")

    # Load last announcement
    last = load_last_announcement()
    if not last:
        respond("No announcement found to edit. Post an announcement first using 'Good to Announce'.")
        return

    full_text = last.get('text', '')
    text_length = len(full_text)

    # Split content into chunks of 2900 chars (safely under Slack's 3000 limit per field)
    CHUNK_SIZE = 2900
    text_chunks = []
    for i in range(0, len(full_text), CHUNK_SIZE):
        text_chunks.append(full_text[i:i + CHUNK_SIZE])

    # Ensure at least one chunk
    if not text_chunks:
        text_chunks = ['']

    num_parts = len(text_chunks)
    is_multipart = num_parts > 1

    # Build modal blocks
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Formatting Tips:*\n• Bold: `*text*`\n• Link: `<url|text>`\n• Code: surround with backticks\n• Keep existing formatting syntax!"
            }
        }
    ]

    if is_multipart:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Note:* Content is {text_length} chars. Split into {num_parts} parts for editing (Slack limits each field to 3000 chars)."
            }
        })

    blocks.append({"type": "divider"})

    # Add a text input for each chunk
    for idx, chunk in enumerate(text_chunks):
        part_num = idx + 1
        label = "Announcement Text (use Slack markdown)" if num_parts == 1 else f"Part {part_num} of {num_parts}"
        blocks.append({
            "type": "input",
            "block_id": f"announcement_text_{part_num}",
            "element": {
                "type": "plain_text_input",
                "action_id": "text_input",
                "multiline": True,
                "initial_value": chunk
            },
            "label": {"type": "plain_text", "text": label}
        })

    # Open modal for editing
    try:
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
                    'message_ts': last.get('message_ts'),
                    'num_parts': num_parts
                }),
                "blocks": blocks
            }
        )
    except Exception as e:
        respond(f"❌ Failed to open edit modal: {str(e)}")
        print(f"[Socket Mode] Error opening edit modal: {e}")


@app.view("edit_announcement_modal")
def handle_edit_modal_submission(ack, body, view):
    """Handle the edit announcement modal submission."""
    ack()

    user = body['user']['username']

    # Get channel, message_ts, and num_parts from private_metadata
    metadata = json.loads(view.get('private_metadata', '{}'))
    channel = metadata.get('channel')
    message_ts = metadata.get('message_ts')
    num_parts = metadata.get('num_parts', 1)

    if not channel or not message_ts:
        print("[Socket Mode] Missing channel or message_ts in edit modal")
        return

    # Reconstruct the full text from all parts
    text_parts = []
    values = view['state']['values']
    for part_num in range(1, num_parts + 1):
        block_id = f"announcement_text_{part_num}"
        if block_id in values:
            part_text = values[block_id]['text_input']['value'] or ''
            text_parts.append(part_text)

    new_text = ''.join(text_parts)
    print(f"[Socket Mode] Reconstructed {len(new_text)} chars from {num_parts} part(s)")

    # Load processed notes for URLs
    try:
        with open('processed_notes.json', 'r') as f:
            processed_data = json.load(f)
    except:
        processed_data = {}

    fix_version_urls = processed_data.get('fix_version_urls', {})
    epic_urls_by_pl = processed_data.get('epic_urls_by_pl', {})

    # Merge in deferred/carried-forward PLs from yesterday (same as Good to Announce)
    today = datetime.now().strftime('%Y-%m-%d')
    deferred_data = load_deferred_pls()
    if today in deferred_data:
        print(f"[Socket Mode Edit] Found {len(deferred_data[today])} deferred PLs for today")
        for deferred in deferred_data[today]:
            pl_name = deferred.get('pl', '')
            if pl_name:
                # Add deferred PL data to the dictionaries for URL matching
                if deferred.get('fix_version_url') and pl_name not in fix_version_urls:
                    fix_version_urls[pl_name] = deferred['fix_version_url']
                    print(f"[Socket Mode Edit] Added deferred fix_version_url for {pl_name}")
                if deferred.get('epic_urls') and pl_name not in epic_urls_by_pl:
                    epic_urls_by_pl[pl_name] = deferred['epic_urls']
                    print(f"[Socket Mode Edit] Added {len(deferred['epic_urls'])} deferred epic URLs for {pl_name}")

    # Flatten epic URLs for easier lookup
    all_epic_urls = {}
    for pl, epics in epic_urls_by_pl.items():
        if epics:
            for epic_name, epic_url in epics.items():
                all_epic_urls[epic_name.lower()] = (epic_name, epic_url)

    # Debug logging
    print(f"[Socket Mode Edit] Loaded {len(fix_version_urls)} fix version URLs")
    print(f"[Socket Mode Edit] Loaded {len(all_epic_urls)} epic URLs for matching")
    if all_epic_urls:
        print(f"[Socket Mode Edit] Epic names available: {list(all_epic_urls.keys())[:5]}...")

    # Auto-format the text (detect and apply Slack markdown)
    def auto_format_text(text: str) -> str:
        """Auto-detect and apply Slack formatting to plain text."""
        lines = text.split('\n')
        formatted_lines = []

        def strip_formatting(s: str) -> str:
            """Remove Slack formatting markers for matching purposes."""
            # Remove bold markers
            s = s.strip('*')
            # Remove link formatting <url|text> -> text
            link_match = re.match(r'^<[^|]+\|(.+)>$', s)
            if link_match:
                s = link_match.group(1).strip('*')
            return s.strip()

        def is_already_formatted(s: str) -> bool:
            """Check if line already has Slack formatting."""
            return s.startswith('<') and '|' in s and s.endswith('>')

        for line in lines:
            stripped = line.strip()

            # Skip empty lines
            if not stripped:
                formatted_lines.append('')
                continue

            # Skip already fully formatted lines (linked)
            if is_already_formatted(stripped):
                formatted_lines.append(stripped)
                continue

            # Get clean text for matching (without formatting markers)
            clean_text = strip_formatting(stripped)
            clean_lower = clean_text.lower()

            # Auto-format "Value Add:" and "Bug Fixes:" as bold (if not already)
            if clean_lower in ('value add:', 'value add') and not stripped.startswith('*'):
                formatted_lines.append('*Value Add:*')
                continue
            if clean_lower in ('bug fixes:', 'bug fixes') and not stripped.startswith('*'):
                formatted_lines.append('*Bug Fixes:*')
                continue

            # Auto-format release type indicators as code (if not already)
            if clean_text in ('General Availability', 'Feature Flag', 'Beta') and not stripped.startswith('`'):
                formatted_lines.append(f'`{clean_text}`')
                continue

            # Check for PL: Release pattern and add hyperlink
            # Pattern: "PL Name: Release X.X" or "PL Name 2026: Release X.X"
            # Also handle already-bold PL names like "*PL Name*: Release X.X"
            release_match = re.match(r'^\*?([^*:]+)\*?:\s*(Release\s+\d+\.\d+)$', stripped)
            if release_match and '<' not in stripped:  # Not already linked
                pl_name = release_match.group(1).strip()
                release_ver = release_match.group(2)
                # Find matching fix version URL using flexible matching
                url = None
                pl_name_lower = pl_name.lower()
                for stored_pl, stored_url in fix_version_urls.items():
                    stored_pl_lower = stored_pl.lower()
                    # Check exact, substring, or year-stripped match
                    stored_pl_clean = re.sub(r'\s+20\d{2}$', '', stored_pl_lower)
                    pl_name_clean = re.sub(r'\s+20\d{2}$', '', pl_name_lower)
                    if (pl_name_lower == stored_pl_lower or
                        pl_name_clean == stored_pl_clean or
                        pl_name_lower in stored_pl_lower or
                        stored_pl_lower in pl_name_lower):
                        url = stored_url
                        break
                if url:
                    formatted_lines.append(f"{pl_name}: <{url}|{release_ver}>")
                    continue

            # Check for epic names and add hyperlink + bold using flexible bidirectional matching
            found_epic = False
            clean_words = set(clean_lower.split())

            # Skip if already linked or if it's a bullet point / prose
            if '<' in stripped or stripped.startswith(('●', '•', '-', '*', '`')):
                formatted_lines.append(stripped)
                continue

            for epic_lower, (epic_name, epic_url) in all_epic_urls.items():
                # Exact match (case-insensitive)
                if clean_lower == epic_lower:
                    formatted_lines.append(f"<{epic_url}|*{clean_text}*>")
                    found_epic = True
                    break

                # Substring match (either direction)
                if epic_lower in clean_lower or clean_lower in epic_lower:
                    formatted_lines.append(f"<{epic_url}|*{clean_text}*>")
                    found_epic = True
                    break

                # Bidirectional partial word match (70% threshold in either direction)
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
                continue

            # Keep line as-is (preserves existing formatting)
            formatted_lines.append(stripped)

        return '\n'.join(formatted_lines)

    # Apply auto-formatting
    formatted_text = auto_format_text(new_text)

    try:
        # Split into chunks for Slack's 3000 char limit per block (same as original)
        chunks = []
        current_chunk = ""

        for line in formatted_text.split('\n'):
            if len(current_chunk) + len(line) + 1 > 2900:
                chunks.append(current_chunk)
                current_chunk = line + '\n'
            else:
                current_chunk += line + '\n'
        if current_chunk:
            chunks.append(current_chunk)

        # Build blocks with mrkdwn formatting
        blocks = []
        for chunk in chunks:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": chunk
                }
            })

        # Update the message
        client.chat_update(
            channel=channel,
            ts=message_ts,
            text=new_text[:100] + "...",
            blocks=blocks
        )

        # Update saved announcement (with formatting)
        save_last_announcement(channel, message_ts, formatted_text)

        print(f"[Socket Mode] {user} updated announcement {message_ts}")

        # Send confirmation DM to user
        try:
            client.chat_postMessage(
                channel=body['user']['id'],
                text="✅ Announcement updated successfully!"
            )
        except:
            pass  # DM might fail if not allowed

    except Exception as e:
        print(f"[Socket Mode] Error updating announcement: {e}")


def main():
    """Start the Socket Mode handler."""

    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token:
        print("""
ERROR: SLACK_APP_TOKEN not found!

To set up Socket Mode:
1. Go to https://api.slack.com/apps and select your app
2. Click "Socket Mode" in the left sidebar
3. Toggle "Enable Socket Mode" ON
4. Click "Generate Token" under "App-Level Tokens"
5. Name it (e.g., "socket-token") and add scope: connections:write
6. Copy the token (starts with xapp-)
7. Add to your .env file:
   SLACK_APP_TOKEN=xapp-your-token-here
""")
        return

    print("""
╔══════════════════════════════════════════════════════════════╗
║         Slack Socket Mode Handler                            ║
╠══════════════════════════════════════════════════════════════╣
║  No ngrok or public URL needed!                              ║
║  Slack connects directly via WebSocket.                      ║
║                                                              ║
║  Features:                                                   ║
║  • Dynamic PLs from processed_notes.json                     ║
║  • Buttons disable after selection                           ║
║  • /delete-announcement - Delete last announcement           ║
║  • /edit-announcement - Edit last announcement               ║
║  • "X PL(s) pending" counter                                 ║
║  • Good to Announce enabled after all reviewed               ║
║  • Tomorrow defers PL to next day                            ║
║                                                              ║
║  Listening for button clicks...                              ║
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
""")

    # Start Socket Mode
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
