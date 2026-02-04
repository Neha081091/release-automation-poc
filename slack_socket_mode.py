"""
Slack Socket Mode Handler for Interactive Buttons

This uses Slack's Socket Mode - no public URL or ngrok needed!
Slack connects directly to your app via WebSocket.

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
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

load_dotenv()

# Initialize the Bolt app with bot token
app = App(token=os.getenv("SLACK_BOT_TOKEN"))

# Slack client for API calls
client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# Store approval states
approval_states = {}


def get_pl_name_from_action(action_id: str) -> str:
    """Extract PL name from action_id like 'approve_Platform'."""
    parts = action_id.split('_', 1)
    if len(parts) > 1:
        return parts[1]
    return action_id


@app.action("approve_Platform")
@app.action("approve_Data")
@app.action("approve_Bidder")
@app.action("approve_AdOps")
def handle_approve(ack, body, action):
    """Handle Approve button clicks."""
    ack()

    pl_name = get_pl_name_from_action(action['action_id'])
    user = body['user']['username']

    print(f"[Socket Mode] {user} APPROVED {pl_name}")

    # Store the approval
    message_ts = body['message']['ts']
    channel = body['channel']['id']

    if message_ts not in approval_states:
        approval_states[message_ts] = {}
    approval_states[message_ts][pl_name] = {
        'status': 'approved',
        'user': user,
        'timestamp': datetime.now().isoformat()
    }

    # Update the message to show the approval
    update_approval_message(channel, message_ts, body['message'], pl_name, 'approved', user)


@app.action("reject_Platform")
@app.action("reject_Data")
@app.action("reject_Bidder")
@app.action("reject_AdOps")
def handle_reject(ack, body, action):
    """Handle Reject button clicks."""
    ack()

    pl_name = get_pl_name_from_action(action['action_id'])
    user = body['user']['username']

    print(f"[Socket Mode] {user} REJECTED {pl_name}")

    message_ts = body['message']['ts']
    channel = body['channel']['id']

    if message_ts not in approval_states:
        approval_states[message_ts] = {}
    approval_states[message_ts][pl_name] = {
        'status': 'rejected',
        'user': user,
        'timestamp': datetime.now().isoformat()
    }

    update_approval_message(channel, message_ts, body['message'], pl_name, 'rejected', user)


@app.action("tomorrow_Platform")
@app.action("tomorrow_Data")
@app.action("tomorrow_Bidder")
@app.action("tomorrow_AdOps")
def handle_tomorrow(ack, body, action):
    """Handle Tomorrow button clicks."""
    ack()

    pl_name = get_pl_name_from_action(action['action_id'])
    user = body['user']['username']

    print(f"[Socket Mode] {user} marked {pl_name} for TOMORROW")

    message_ts = body['message']['ts']
    channel = body['channel']['id']

    if message_ts not in approval_states:
        approval_states[message_ts] = {}
    approval_states[message_ts][pl_name] = {
        'status': 'tomorrow',
        'user': user,
        'timestamp': datetime.now().isoformat()
    }

    update_approval_message(channel, message_ts, body['message'], pl_name, 'tomorrow', user)


@app.action("good_to_announce")
def handle_good_to_announce(ack, body):
    """Handle Good to Announce button click."""
    ack()

    user = body['user']['username']
    channel = body['channel']['id']
    message_ts = body['message']['ts']

    print(f"[Socket Mode] {user} clicked GOOD TO ANNOUNCE")

    # Get the announce channel
    announce_channel = os.getenv('SLACK_ANNOUNCE_CHANNEL', channel)

    # Post the announcement
    try:
        client.chat_postMessage(
            channel=announce_channel,
            text=":mega: *Release Announcement*\n\nThe release has been approved and is ready for deployment!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":white_check_mark: *Release Approved and Announced*\n\nAll approvals received. The release is good to go!"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Announced by @{user} at {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        }
                    ]
                }
            ]
        )

        # Update the original message
        client.chat_update(
            channel=channel,
            ts=message_ts,
            text="Release has been announced!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":white_check_mark: *Release Announced Successfully*\n\nThis release has been approved and announced."
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Announced by @{user} at {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        }
                    ]
                }
            ]
        )

        print(f"[Socket Mode] Announcement posted to {announce_channel}")

    except Exception as e:
        print(f"[Socket Mode] Error posting announcement: {e}")


def update_approval_message(channel: str, message_ts: str, original_message: dict, pl_name: str, status: str, user: str):
    """Update the message to reflect the approval status."""

    status_emoji = {
        'approved': ':white_check_mark:',
        'rejected': ':x:',
        'tomorrow': ':arrow_right:'
    }

    status_text = {
        'approved': 'Approved',
        'rejected': 'Rejected',
        'tomorrow': 'Tomorrow'
    }

    # Get current blocks and update them
    blocks = original_message.get('blocks', [])

    # Find and update the section for this PL
    for i, block in enumerate(blocks):
        if block.get('type') == 'section':
            text = block.get('text', {}).get('text', '')
            if pl_name in text:
                # Update this section with status
                emoji = status_emoji.get(status, ':question:')
                new_text = f"{emoji} *{pl_name}*: {status_text.get(status, status)} by @{user}"
                blocks[i] = {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": new_text
                    }
                }
                # Remove the actions block for this PL (next block)
                if i + 1 < len(blocks) and blocks[i + 1].get('type') == 'actions':
                    action_block = blocks[i + 1]
                    # Check if this action block belongs to this PL
                    actions = action_block.get('elements', [])
                    if actions and pl_name in actions[0].get('action_id', ''):
                        blocks.pop(i + 1)
                break

    try:
        client.chat_update(
            channel=channel,
            ts=message_ts,
            blocks=blocks,
            text=f"Approval status updated for {pl_name}"
        )
    except Exception as e:
        print(f"[Socket Mode] Error updating message: {e}")


def post_approval_message(release_notes: str = None):
    """Post an approval message with buttons to the review channel."""

    channel = os.getenv('SLACK_REVIEW_CHANNEL')
    if not channel:
        print("Error: SLACK_REVIEW_CHANNEL not set")
        return

    today = datetime.now().strftime('%Y-%m-%d')

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":clipboard: Release Approval - {today}",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Please review and approve the release notes for your Product Line."
            }
        },
        {"type": "divider"}
    ]

    # Add approval sections for each PL
    pls = ["Platform", "Data", "Bidder", "AdOps"]

    for pl in pls:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":hourglass: *{pl}*: Pending approval"
            }
        })
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "style": "primary",
                    "action_id": f"approve_{pl}"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                    "style": "danger",
                    "action_id": f"reject_{pl}"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Tomorrow", "emoji": True},
                    "action_id": f"tomorrow_{pl}"
                }
            ]
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": ":mega: Good to Announce", "emoji": True},
                "style": "primary",
                "action_id": "good_to_announce"
            }
        ]
    })

    try:
        result = client.chat_postMessage(
            channel=channel,
            text=f"Release Approval Request - {today}",
            blocks=blocks
        )
        print(f"[Socket Mode] Approval message posted: {result['ts']}")
        return result['ts']
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
║  Listening for button clicks...                              ║
║  Press Ctrl+C to stop                                         ║
╚══════════════════════════════════════════════════════════════╝
""")

    # Start Socket Mode
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()
