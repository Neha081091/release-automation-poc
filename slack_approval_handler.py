"""
Slack Approval Workflow Handler

This module handles the complete Slack approval workflow for release notes:
- Creating interactive approval messages with buttons per PL
- Handling button clicks (Approve/Reject/Tomorrow)
- Managing approval state
- Moving rejected/deferred PLs to tomorrow's notes
- Posting final approved notes to release channel
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

load_dotenv()

# File paths for state management
APPROVAL_STATUS_FILE = "approval_status.json"
TODAY_NOTES_FILE = "processed_notes.json"
TOMORROW_NOTES_FILE = "tomorrow_notes.json"

# Google Docs URL
GOOGLE_DOC_URL = os.getenv("GOOGLE_DOC_URL", "https://docs.google.com/document/d/YOUR_DOC_ID/edit")


class SlackApprovalHandler:
    """Handler for Slack approval workflow with interactive buttons."""

    def __init__(self, bot_token: str = None, review_channel: str = None, announce_channel: str = None):
        """
        Initialize Slack approval handler.

        Args:
            bot_token: Slack Bot OAuth token (needs chat:write, reactions:write scopes)
            review_channel: Channel ID for PMO review (where approval message is posted)
            announce_channel: Channel ID for final announcements (#release-announcements)
        """
        self.bot_token = bot_token or os.getenv('SLACK_BOT_TOKEN')
        self.review_channel = review_channel or os.getenv('SLACK_REVIEW_CHANNEL')
        self.announce_channel = announce_channel or os.getenv('SLACK_ANNOUNCE_CHANNEL')

        if not self.bot_token:
            raise ValueError("SLACK_BOT_TOKEN is required for interactive messages")

        self.client = WebClient(token=self.bot_token)
        print(f"[SlackApproval] Initialized with review channel: {self.review_channel}")
        print(f"[SlackApproval] Announce channel: {self.announce_channel}")

    def load_approval_status(self) -> Dict:
        """Load approval status from JSON file."""
        if os.path.exists(APPROVAL_STATUS_FILE):
            with open(APPROVAL_STATUS_FILE, 'r') as f:
                return json.load(f)
        return {}

    def save_approval_status(self, status: Dict) -> None:
        """Save approval status to JSON file."""
        with open(APPROVAL_STATUS_FILE, 'w') as f:
            json.dump(status, f, indent=2)
        print(f"[SlackApproval] Saved approval status to {APPROVAL_STATUS_FILE}")

    def load_release_notes(self, file_path: str = TODAY_NOTES_FILE) -> Dict:
        """Load release notes from JSON file."""
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        return {}

    def save_release_notes(self, notes: Dict, file_path: str) -> None:
        """Save release notes to JSON file."""
        with open(file_path, 'w') as f:
            json.dump(notes, f, indent=2)
        print(f"[SlackApproval] Saved release notes to {file_path}")

    def create_approval_status(self, release_notes: Dict) -> Dict:
        """
        Create initial approval status structure from release notes.

        Args:
            release_notes: Processed release notes dictionary

        Returns:
            Approval status dictionary
        """
        release_versions = release_notes.get("release_versions", {})
        product_lines = release_notes.get("product_lines", [])

        approval_state = {}
        for pl in product_lines:
            approval_state[pl] = {
                "release_version": release_versions.get(pl, "Unknown"),
                "status": "pending",  # pending, approved, rejected, tomorrow
                "voted_by": None,
                "voted_at": None,
                "button_state": "enabled"  # enabled, disabled
            }

        status = {
            "message_timestamp": None,
            "review_channel": self.review_channel,
            "release_date": release_notes.get("release_summary", f"Release {datetime.now().strftime('%d %B %Y')}"),
            "created_at": datetime.now().isoformat(),
            "approval_state": approval_state,
            "global_state": {
                "all_decided": False,
                "all_approved": False,
                "good_to_announce_enabled": False,
                "rejected_pls": [],
                "tomorrow_pls": [],
                "approved_pls": []
            }
        }

        return status

    def build_approval_blocks(self, approval_status: Dict) -> List[Dict]:
        """
        Build Slack Block Kit blocks for the approval message.

        Args:
            approval_status: Current approval status dictionary

        Returns:
            List of Slack blocks
        """
        blocks = []

        # Header
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Release Notes Ready for Review",
                "emoji": True
            }
        })

        # Instructions
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Please review, refine, update the notes on *<{GOOGLE_DOC_URL}|Daily Consolidated Diplomacy Summary>*\n\nMark the options once QA verification is complete."
            }
        })

        blocks.append({"type": "divider"})

        # PL sections with buttons
        for pl_name, pl_data in approval_status["approval_state"].items():
            release_version = pl_data["release_version"]
            status = pl_data["status"]
            button_state = pl_data["button_state"]

            # Create status indicator
            status_emoji = {
                "pending": "",
                "approved": " :white_check_mark: *Approved*",
                "rejected": " :x: *Rejected - Moved to Tomorrow*",
                "tomorrow": " :arrow_right: *Moved to Tomorrow*"
            }.get(status, "")

            # Sanitize PL name for action_id (replace spaces and special chars)
            pl_id = pl_name.replace(" ", "_").replace("-", "_")

            # Build section with text
            section = {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{pl_name}:* {release_version}{status_emoji}"
                }
            }

            # Add buttons if not yet decided
            if button_state == "enabled":
                blocks.append(section)
                blocks.append({
                    "type": "actions",
                    "block_id": f"actions_{pl_id}",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                            "value": json.dumps({"pl_name": pl_name, "action": "approve"}),
                            "action_id": f"approve_{pl_id}",
                            "style": "primary"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                            "value": json.dumps({"pl_name": pl_name, "action": "reject"}),
                            "action_id": f"reject_{pl_id}",
                            "style": "danger"
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Tomorrow", "emoji": True},
                            "value": json.dumps({"pl_name": pl_name, "action": "tomorrow"}),
                            "action_id": f"tomorrow_{pl_id}"
                        }
                    ]
                })
            else:
                # Show status without buttons
                if pl_data.get("voted_by"):
                    voted_info = f"\n_Decided by <@{pl_data['voted_by']}> at {pl_data.get('voted_at', 'unknown')}_"
                    section["text"]["text"] += voted_info
                blocks.append(section)

        blocks.append({"type": "divider"})

        # Summary section
        global_state = approval_status["global_state"]
        approved_count = len(global_state.get("approved_pls", []))
        tomorrow_count = len(global_state.get("tomorrow_pls", []))
        rejected_count = len(global_state.get("rejected_pls", []))
        total_count = len(approval_status["approval_state"])
        pending_count = total_count - approved_count - tomorrow_count - rejected_count

        summary_text = f"*Status:* {approved_count} Approved | {tomorrow_count + rejected_count} Deferred | {pending_count} Pending"
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": summary_text}]
        })

        blocks.append({"type": "divider"})

        # "Good to Announce" button section
        if global_state.get("good_to_announce_enabled", False):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":tada: All PLs have been reviewed! Click below to post to release channel:"
                }
            })
            blocks.append({
                "type": "actions",
                "block_id": "good_to_announce_actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Good to Announce", "emoji": True},
                        "value": "good_to_announce",
                        "action_id": "good_to_announce",
                        "style": "primary"
                    }
                ]
            })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_Once all PL verification is done, the 'Good to Announce' button will appear._"
                }
            })

        return blocks

    def post_approval_message(self, release_notes: Dict = None) -> Optional[Dict]:
        """
        Post the initial approval message to the review channel.

        Args:
            release_notes: Release notes dictionary (loads from file if not provided)

        Returns:
            Response with message timestamp or None on failure
        """
        if release_notes is None:
            release_notes = self.load_release_notes()

        if not release_notes:
            print("[SlackApproval] ERROR: No release notes found")
            return None

        # Create approval status
        approval_status = self.create_approval_status(release_notes)

        # Build blocks
        blocks = self.build_approval_blocks(approval_status)

        # Post message
        try:
            response = self.client.chat_postMessage(
                channel=self.review_channel,
                text="Release Notes Ready for Review - Please approve or defer each PL.",
                blocks=blocks
            )

            if response["ok"]:
                # Save message timestamp for updates
                approval_status["message_timestamp"] = response["ts"]
                approval_status["review_channel"] = response["channel"]
                self.save_approval_status(approval_status)

                print(f"[SlackApproval] Posted approval message (ts: {response['ts']})")
                return {
                    "ok": True,
                    "ts": response["ts"],
                    "channel": response["channel"]
                }
            else:
                print(f"[SlackApproval] Failed to post message: {response.get('error')}")
                return None

        except SlackApiError as e:
            print(f"[SlackApproval] Slack API error: {e.response['error']}")
            return None

    def handle_button_click(self, payload: Dict) -> Tuple[bool, str]:
        """
        Handle a button click interaction from Slack.

        Args:
            payload: Slack interaction payload

        Returns:
            Tuple of (success: bool, message: str)
        """
        # Extract action info
        actions = payload.get("actions", [])
        if not actions:
            return False, "No action found in payload"

        action = actions[0]
        action_id = action.get("action_id", "")
        value = action.get("value", "")

        user_id = payload.get("user", {}).get("id", "unknown")
        user_name = payload.get("user", {}).get("username", "unknown")

        print(f"[SlackApproval] Button clicked: {action_id} by {user_name}")

        # Handle "Good to Announce" button
        if action_id == "good_to_announce":
            return self.handle_good_to_announce(payload, user_id)

        # Parse action value
        try:
            action_data = json.loads(value)
            pl_name = action_data.get("pl_name")
            action_type = action_data.get("action")
        except (json.JSONDecodeError, KeyError):
            return False, "Invalid action data"

        if not pl_name or not action_type:
            return False, "Missing PL name or action type"

        # Load current approval status
        approval_status = self.load_approval_status()
        if not approval_status:
            return False, "No approval status found"

        # Update the PL status
        if pl_name not in approval_status["approval_state"]:
            return False, f"Unknown PL: {pl_name}"

        pl_state = approval_status["approval_state"][pl_name]

        # Check if already decided
        if pl_state["status"] != "pending":
            return False, f"{pl_name} has already been decided"

        # Update status based on action
        pl_state["status"] = action_type if action_type != "reject" else "rejected"
        pl_state["voted_by"] = user_id
        pl_state["voted_at"] = datetime.now().strftime("%H:%M")
        pl_state["button_state"] = "disabled"

        # Update global state
        global_state = approval_status["global_state"]

        if action_type == "approve":
            global_state["approved_pls"].append(pl_name)
        elif action_type in ["reject", "tomorrow"]:
            if action_type == "reject":
                global_state["rejected_pls"].append(pl_name)
            global_state["tomorrow_pls"].append(pl_name)
            # Move to tomorrow's notes
            self.move_to_tomorrow(pl_name)

        # Check if all PLs have been decided
        all_statuses = [v["status"] for v in approval_status["approval_state"].values()]
        all_decided = all(s != "pending" for s in all_statuses)
        global_state["all_decided"] = all_decided

        # Check if we have any approved PLs (enable "Good to Announce" if all decided and at least one approved)
        if all_decided and len(global_state["approved_pls"]) > 0:
            global_state["good_to_announce_enabled"] = True
            global_state["all_approved"] = len(global_state["rejected_pls"]) == 0 and len(global_state["tomorrow_pls"]) == 0

        # Save updated status
        self.save_approval_status(approval_status)

        # Update the Slack message
        self.update_approval_message(approval_status)

        action_messages = {
            "approve": f"{pl_name} approved",
            "reject": f"{pl_name} rejected and moved to tomorrow",
            "tomorrow": f"{pl_name} deferred to tomorrow"
        }

        return True, action_messages.get(action_type, f"{pl_name} updated")

    def move_to_tomorrow(self, pl_name: str) -> bool:
        """
        Move a PL's release notes to tomorrow's file.

        Args:
            pl_name: Name of the product line to move

        Returns:
            True if successful
        """
        print(f"[SlackApproval] Moving {pl_name} to tomorrow...")

        # Load today's notes
        today_notes = self.load_release_notes(TODAY_NOTES_FILE)
        if not today_notes:
            print("[SlackApproval] ERROR: No today's notes found")
            return False

        # Load or create tomorrow's notes
        tomorrow_notes = self.load_release_notes(TOMORROW_NOTES_FILE)
        if not tomorrow_notes:
            tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%d %B %Y")
            tomorrow_notes = {
                "processed_at": datetime.now().isoformat(),
                "source_file": "deferred_from_today",
                "release_summary": f"Release {tomorrow_date}",
                "ticket_count": 0,
                "product_lines": [],
                "release_versions": {},
                "fix_version_urls": {},
                "epic_urls_by_pl": {},
                "tldr_by_pl": {},
                "body_by_pl": {},
                "grouped_data": {}
            }

        # Move PL data
        if pl_name in today_notes.get("product_lines", []):
            # Add to tomorrow
            tomorrow_notes["product_lines"].append(pl_name)

            # Move all associated data
            for key in ["release_versions", "fix_version_urls", "epic_urls_by_pl", "tldr_by_pl", "body_by_pl", "grouped_data"]:
                if key in today_notes and pl_name in today_notes[key]:
                    tomorrow_notes[key][pl_name] = today_notes[key][pl_name]

            # Update ticket count
            if pl_name in today_notes.get("grouped_data", {}):
                ticket_count = sum(len(tickets) for tickets in today_notes["grouped_data"][pl_name].values())
                tomorrow_notes["ticket_count"] += ticket_count

        # Save tomorrow's notes
        self.save_release_notes(tomorrow_notes, TOMORROW_NOTES_FILE)

        print(f"[SlackApproval] Moved {pl_name} to {TOMORROW_NOTES_FILE}")
        return True

    def update_approval_message(self, approval_status: Dict) -> bool:
        """
        Update the approval message in Slack with current status.

        Args:
            approval_status: Current approval status

        Returns:
            True if successful
        """
        message_ts = approval_status.get("message_timestamp")
        channel = approval_status.get("review_channel")

        if not message_ts or not channel:
            print("[SlackApproval] ERROR: No message timestamp or channel")
            return False

        # Build updated blocks
        blocks = self.build_approval_blocks(approval_status)

        try:
            response = self.client.chat_update(
                channel=channel,
                ts=message_ts,
                text="Release Notes Review - Status Updated",
                blocks=blocks
            )

            if response["ok"]:
                print(f"[SlackApproval] Updated approval message")
                return True
            else:
                print(f"[SlackApproval] Failed to update: {response.get('error')}")
                return False

        except SlackApiError as e:
            print(f"[SlackApproval] Slack API error: {e.response['error']}")
            return False

    def handle_good_to_announce(self, payload: Dict, user_id: str) -> Tuple[bool, str]:
        """
        Handle the "Good to Announce" button click.
        Posts final release notes to the announcement channel.

        Args:
            payload: Slack interaction payload
            user_id: ID of the user who clicked

        Returns:
            Tuple of (success: bool, message: str)
        """
        print(f"[SlackApproval] 'Good to Announce' clicked by {user_id}")

        # Load approval status
        approval_status = self.load_approval_status()
        if not approval_status:
            return False, "No approval status found"

        # Verify all PLs are decided
        global_state = approval_status["global_state"]
        if not global_state.get("good_to_announce_enabled", False):
            return False, "Not all PLs have been reviewed yet"

        approved_pls = global_state.get("approved_pls", [])
        if not approved_pls:
            return False, "No approved PLs to announce"

        # Load release notes
        release_notes = self.load_release_notes()
        if not release_notes:
            return False, "No release notes found"

        # Post final notes
        success = self.post_final_release_notes(release_notes, approved_pls, user_id)

        if success:
            # Update the approval message to show completion
            approval_status["global_state"]["announced"] = True
            approval_status["global_state"]["announced_by"] = user_id
            approval_status["global_state"]["announced_at"] = datetime.now().isoformat()
            self.save_approval_status(approval_status)

            # Update the original message to show completion
            self.mark_approval_complete(approval_status)

            return True, f"Release notes posted to {self.announce_channel}"
        else:
            return False, "Failed to post release notes"

    def post_final_release_notes(self, release_notes: Dict, approved_pls: List[str], approver_id: str) -> bool:
        """
        Post final approved release notes to the announcement channel.

        Args:
            release_notes: Full release notes dictionary
            approved_pls: List of approved PL names
            approver_id: User ID of the approver

        Returns:
            True if successful
        """
        print(f"[SlackApproval] Posting final release notes for: {approved_pls}")

        # Build the final message
        blocks = []

        # Header
        release_date = release_notes.get("release_summary", f"Release {datetime.now().strftime('%d %B %Y')}")
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"RELEASE DEPLOYED: {release_date}",
                "emoji": True
            }
        })

        blocks.append({"type": "divider"})

        # TL;DR section
        tldr_text = "*------------------TL;DR:------------------*\n\n"
        tldr_text += "*Key Deployments:*\n"

        tldr_by_pl = release_notes.get("tldr_by_pl", {})
        for pl in approved_pls:
            if pl in tldr_by_pl:
                tldr_text += f"*{pl}:* {tldr_by_pl[pl]}\n\n"

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": tldr_text[:3000]
            }
        })

        blocks.append({"type": "divider"})

        # Detailed notes per PL
        body_by_pl = release_notes.get("body_by_pl", {})
        release_versions = release_notes.get("release_versions", {})

        for pl in approved_pls:
            if pl in body_by_pl:
                version = release_versions.get(pl, "")
                pl_header = f"*{pl}: {version}*" if version else f"*{pl}*"

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": pl_header
                    }
                })

                # Split body into sections if too long
                body = body_by_pl[pl]
                if len(body) <= 3000:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": body
                        }
                    })
                else:
                    # Split into chunks
                    for i in range(0, len(body), 3000):
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": body[i:i+3000]
                            }
                        })

                blocks.append({"type": "divider"})

        # Footer with approval info
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Approved by <@{approver_id}> | <!date^{int(time.time())}^{{date_short}} {{time}}|{datetime.now().strftime('%Y-%m-%d %H:%M')}>"
                }
            ]
        })

        # Post to announcement channel
        try:
            response = self.client.chat_postMessage(
                channel=self.announce_channel,
                text=f"RELEASE DEPLOYED: {release_date}",
                blocks=blocks
            )

            if response["ok"]:
                print(f"[SlackApproval] Posted final notes to {self.announce_channel} (ts: {response['ts']})")
                return True
            else:
                print(f"[SlackApproval] Failed to post: {response.get('error')}")
                return False

        except SlackApiError as e:
            print(f"[SlackApproval] Slack API error: {e.response['error']}")
            return False

    def mark_approval_complete(self, approval_status: Dict) -> bool:
        """
        Update the approval message to show completion state.

        Args:
            approval_status: Current approval status

        Returns:
            True if successful
        """
        message_ts = approval_status.get("message_timestamp")
        channel = approval_status.get("review_channel")

        if not message_ts or not channel:
            return False

        global_state = approval_status["global_state"]
        approved_pls = global_state.get("approved_pls", [])
        tomorrow_pls = global_state.get("tomorrow_pls", [])

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Release Notes - COMPLETED",
                    "emoji": True
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":white_check_mark: *Release notes have been posted to <#{self.announce_channel}>*"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Approved PLs:* {', '.join(approved_pls) if approved_pls else 'None'}"
                }
            }
        ]

        if tomorrow_pls:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Deferred to Tomorrow:* {', '.join(tomorrow_pls)}"
                }
            })

        announced_by = global_state.get("announced_by", "Unknown")
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Announced by <@{announced_by}> at <!date^{int(time.time())}^{{date_short}} {{time}}|{datetime.now().strftime('%H:%M')}>"
                }
            ]
        })

        try:
            response = self.client.chat_update(
                channel=channel,
                ts=message_ts,
                text="Release Notes - COMPLETED",
                blocks=blocks
            )
            return response["ok"]
        except SlackApiError as e:
            print(f"[SlackApproval] Error marking complete: {e.response['error']}")
            return False


def main():
    """Test the Slack approval handler."""
    try:
        handler = SlackApprovalHandler()

        # Load and post approval message
        result = handler.post_approval_message()

        if result:
            print(f"\nApproval message posted!")
            print(f"Channel: {result['channel']}")
            print(f"Timestamp: {result['ts']}")
            print(f"\nApproval status saved to: {APPROVAL_STATUS_FILE}")
        else:
            print("Failed to post approval message")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
