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
                'rejected': 'Rejected (deferred)',
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
                        "text": {"type": "plain_text", "text": "✗ Reject", "emoji": True},
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

        google_docs = GoogleDocsHandler()
        if google_docs.authenticate():
            if google_docs.remove_pl_section(pl_name):
                print(f"[Socket Mode] Removed {pl_name} section from Google Doc")
            else:
                print(f"[Socket Mode] Could not remove {pl_name} from Google Doc (may need manual removal)")
        else:
            print("[Socket Mode] Could not authenticate with Google Docs")
    except Exception as e:
        print(f"[Socket Mode] Error removing from Google Doc: {e}")

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

    # Build announcement message
    announce_channel = os.getenv('SLACK_ANNOUNCE_CHANNEL', channel)
    doc_url = message_metadata.get(message_ts, {}).get('doc_url', '')
    release_date = message_metadata.get(message_ts, {}).get('release_date', datetime.now().strftime('%d %B %Y'))

    announcement_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📢 Daily Deployment Summary: {release_date}",
                "emoji": True
            }
        }
    ]

    if doc_url:
        announcement_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"<{doc_url}|📄 View Full Release Notes>"
            }
        })

    if approved_pls:
        announcement_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"✅ *Approved PLs:* {', '.join(approved_pls)}"
            }
        })

    if rejected_pls:
        announcement_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"❌ *Deferred PLs:* {', '.join(rejected_pls)}"
            }
        })

    if tomorrow_pls:
        announcement_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⏰ *Moved to Tomorrow:* {', '.join(tomorrow_pls)}"
            }
        })

    announcement_blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Announced by @{user} at {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            }
        ]
    })

    try:
        # Post announcement
        client.chat_postMessage(
            channel=announce_channel,
            text=f"Daily Deployment Summary: {release_date}",
            blocks=announcement_blocks
        )

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
                    "text": f"• Approved: {', '.join(approved_pls) if approved_pls else 'None'}\n• Rejected: {', '.join(rejected_pls) if rejected_pls else 'None'}\n• Tomorrow: {', '.join(tomorrow_pls) if tomorrow_pls else 'None'}"
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
