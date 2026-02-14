"""
Slack API Handler for Release Automation PoC

This module handles all Slack operations:
- Sending notification messages via webhook or bot token
- Posting release notes
- Managing approval workflows (via reactions or buttons)
- DM and channel messaging
"""

import os
import json
import time
import requests
from typing import Dict, List, Optional, Any
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class SlackHandler:
    """Handler for Slack API operations."""

    def __init__(self, bot_token: str = None, default_channel: str = None, webhook_url: str = None):
        """
        Initialize Slack handler.
        Supports both webhook URLs (simpler) and bot tokens.

        Args:
            bot_token: Slack Bot OAuth token
            default_channel: Default channel ID for messages
            webhook_url: Slack webhook URL (preferred for simple posting)
        """
        self.webhook_url = webhook_url or os.getenv('SLACK_WEBHOOK_URL')
        self.bot_token = bot_token or os.getenv('SLACK_BOT_TOKEN')
        self.default_channel = default_channel or os.getenv('SLACK_DM_CHANNEL')

        # Use webhook if available, otherwise use bot token
        self.use_webhook = bool(self.webhook_url)

        if self.use_webhook:
            print(f"[Slack] Initialized with webhook URL")
            self.client = None
        elif self.bot_token:
            self.client = WebClient(token=self.bot_token)
            print(f"[Slack] Initialized with bot token, channel: {self.default_channel}")
        else:
            raise ValueError("Either SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN must be provided")

        self.approval_store = {}  # In-memory store for PoC

    def test_connection(self) -> bool:
        """
        Test the Slack connection.

        Returns:
            True if connection successful, False otherwise
        """
        print("[Slack] Testing connection...")

        if self.use_webhook:
            # Test webhook by sending a minimal test (won't actually send)
            print("[Slack] Using webhook - connection assumed OK")
            return True

        try:
            response = self.client.auth_test()
            if response["ok"]:
                print(f"[Slack] Connected as: {response.get('user', 'Unknown')}")
                print(f"[Slack] Team: {response.get('team', 'Unknown')}")
                return True
            else:
                print(f"[Slack] Auth test failed: {response.get('error', 'Unknown error')}")
                return False

        except SlackApiError as e:
            print(f"[Slack] Connection test failed: {e.response['error']}")
            return False

    def send_webhook_message(self, text: str, blocks: List[Dict] = None) -> Optional[Dict]:
        """
        Send a message via webhook.

        Args:
            text: Message text
            blocks: Optional Block Kit blocks

        Returns:
            Success dict or None on failure
        """
        if not self.webhook_url:
            print("[Slack] No webhook URL configured")
            return None

        payload = {"text": text}
        if blocks:
            payload["blocks"] = blocks

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )

            if response.status_code == 200 and response.text == "ok":
                print("[Slack] Webhook message sent successfully")
                return {"ok": True, "ts": str(time.time())}
            else:
                print(f"[Slack] Webhook error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"[Slack] Webhook request failed: {e}")
            return None

    def send_message(self, text: str, channel: str = None, blocks: List[Dict] = None,
                     thread_ts: str = None) -> Optional[Dict]:
        """
        Send a message to a channel or DM.
        Uses webhook if available, otherwise uses bot token.

        Args:
            text: Message text (also used as fallback for blocks)
            channel: Channel ID (uses default if not provided, ignored for webhook)
            blocks: Optional Block Kit blocks for rich formatting
            thread_ts: Optional thread timestamp for replies (ignored for webhook)

        Returns:
            API response or None on failure
        """
        # Use webhook if available
        if self.use_webhook:
            return self.send_webhook_message(text, blocks)

        channel = channel or self.default_channel

        if not channel:
            print("[Slack] No channel specified")
            return None

        print(f"[Slack] Sending message to {channel}")

        try:
            response = self.client.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts
            )

            if response["ok"]:
                print(f"[Slack] Message sent successfully (ts: {response['ts']})")
                return {
                    "ok": True,
                    "ts": response["ts"],
                    "channel": response["channel"]
                }
            else:
                print(f"[Slack] Message failed: {response.get('error', 'Unknown')}")
                return None

        except SlackApiError as e:
            print(f"[Slack] Error sending message: {e.response['error']}")
            return None

    def send_no_release_notification(self, today_date: str, channel: str = None) -> Optional[Dict]:
        """
        Send a 'No release planned for today' notification to Slack.

        Args:
            today_date: Today's date string (e.g., "13th February 2026")
            channel: Target channel (uses default if not provided)

        Returns:
            API response or None on failure
        """
        print("[Slack] Sending 'no release planned' notification...")

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"No release planned for today ({today_date})."
                }
            }
        ]

        return self.send_message(
            f"No release planned for today ({today_date}).",
            channel, blocks
        )

    def send_review_notification(self, release_date: str, doc_url: str,
                                 tldr_summary: str, channel: str = None) -> Optional[Dict]:
        """
        Send release notes review notification to PMOs.

        Args:
            release_date: Release date string
            doc_url: Google Doc URL
            tldr_summary: TL;DR summary text
            channel: Target channel (uses default if not provided)

        Returns:
            API response or None on failure
        """
        print("[Slack] Sending review notification...")

        # Create Block Kit message for rich formatting
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Release Notes Ready for Review",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Release Date:*\n{release_date}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Google Doc Link:*\n<{doc_url}|Click here to view release notes>"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*TL;DR Summary:*\n{tldr_summary}"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Please review and approve the release notes in the Google Doc above.\n*Awaiting your YES/NO vote.*"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Approve (YES)",
                            "emoji": True
                        },
                        "style": "primary",
                        "action_id": "approve_release",
                        "value": "approved"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Reject (NO)",
                            "emoji": True
                        },
                        "style": "danger",
                        "action_id": "reject_release",
                        "value": "rejected"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Release Tomorrow",
                            "emoji": True
                        },
                        "action_id": "defer_release",
                        "value": "deferred"
                    }
                ]
            }
        ]

        fallback_text = f"""Release Notes Ready for Review

Release Date: {release_date}

Google Doc Link: {doc_url}

TL;DR Summary:
{tldr_summary}

---

Please review and approve the release notes in the Google Doc above.
Awaiting your YES/NO vote."""

        return self.send_message(fallback_text, channel, blocks)

    def send_final_release_notes(self, release_date: str, release_notes: str,
                                 approver_name: str = None, channel: str = None) -> Optional[Dict]:
        """
        Post final release notes to the release channel.

        Args:
            release_date: Release date string
            release_notes: Formatted release notes text
            approver_name: Name of the final approver
            channel: Target channel (uses default if not provided)

        Returns:
            API response or None on failure
        """
        print("[Slack] Posting final release notes...")

        # Create rich formatted message
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"RELEASE DEPLOYED: {release_date}",
                    "emoji": True
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": release_notes[:3000]  # Slack limit
                }
            }
        ]

        # Add overflow content if needed
        if len(release_notes) > 3000:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": release_notes[3000:6000]
                }
            })

        # Add approval info
        blocks.append({"type": "divider"})

        approval_text = f"*Approved by:* {approver_name or 'System'}\n*Time:* <!date^{int(time.time())}^{{date_short}} {{time}}|{time.strftime('%Y-%m-%d %H:%M')}>"
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": approval_text
                }
            ]
        })

        fallback_text = f"""RELEASE DEPLOYED: {release_date}

{release_notes}

---
Approved by: {approver_name or 'System'}
Time: {time.strftime('%Y-%m-%d %H:%M')}"""

        return self.send_message(fallback_text, channel, blocks)

    def send_approval_reminder(self, release_date: str, pending_approvers: List[str],
                              channel: str = None) -> Optional[Dict]:
        """
        Send a reminder for pending approvals.

        Args:
            release_date: Release date string
            pending_approvers: List of approvers who haven't voted
            channel: Target channel

        Returns:
            API response or None on failure
        """
        print("[Slack] Sending approval reminder...")

        pending_list = ", ".join(pending_approvers) if pending_approvers else "Unknown"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Reminder: Approval Pending*\n\nRelease notes for *{release_date}* are still awaiting approval.\n\n*Pending approvers:* {pending_list}"
                }
            }
        ]

        return self.send_message(
            f"Reminder: Release notes for {release_date} awaiting approval from: {pending_list}",
            channel, blocks
        )

    def send_good_to_release_notification(self, release_date: str, doc_url: str,
                                          channel: str = None) -> Optional[Dict]:
        """
        Send notification that all approvals are complete and ready for final release.

        Args:
            release_date: Release date string
            doc_url: Google Doc URL
            channel: Target channel

        Returns:
            API response or None on failure
        """
        print("[Slack] Sending 'Good to Release' notification...")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "All Approvals Complete!",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"All PMOs have approved the release notes for *{release_date}*.\n\n<{doc_url}|View Release Notes>"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Good to Release",
                            "emoji": True
                        },
                        "style": "primary",
                        "action_id": "final_approve_release",
                        "value": "final_approved"
                    }
                ]
            }
        ]

        return self.send_message(
            f"All approvals complete for {release_date}. Click 'Good to Release' to publish.",
            channel, blocks
        )

    def track_approval(self, release_id: str, approver: str, vote: str) -> Dict:
        """
        Track an approval vote (in-memory for PoC).

        Args:
            release_id: Unique release identifier
            approver: Approver name or ID
            vote: Vote value (approved/rejected/deferred)

        Returns:
            Current approval status
        """
        if release_id not in self.approval_store:
            self.approval_store[release_id] = {
                "votes": {},
                "created_at": time.time()
            }

        self.approval_store[release_id]["votes"][approver] = {
            "vote": vote,
            "timestamp": time.time()
        }

        print(f"[Slack] Tracked vote: {approver} -> {vote} for release {release_id}")

        return self.get_approval_status(release_id)

    def get_approval_status(self, release_id: str) -> Dict:
        """
        Get the current approval status for a release.

        Args:
            release_id: Unique release identifier

        Returns:
            Approval status with counts and details
        """
        if release_id not in self.approval_store:
            return {"found": False}

        votes = self.approval_store[release_id]["votes"]

        approved = sum(1 for v in votes.values() if v["vote"] == "approved")
        rejected = sum(1 for v in votes.values() if v["vote"] == "rejected")
        deferred = sum(1 for v in votes.values() if v["vote"] == "deferred")

        return {
            "found": True,
            "release_id": release_id,
            "total_votes": len(votes),
            "approved": approved,
            "rejected": rejected,
            "deferred": deferred,
            "all_approved": rejected == 0 and deferred == 0 and approved > 0,
            "votes": votes
        }

    def update_message(self, channel: str, ts: str, text: str,
                       blocks: List[Dict] = None) -> Optional[Dict]:
        """
        Update an existing message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            text: New text
            blocks: New blocks

        Returns:
            API response or None on failure
        """
        try:
            response = self.client.chat_update(
                channel=channel,
                ts=ts,
                text=text,
                blocks=blocks
            )

            if response["ok"]:
                print(f"[Slack] Message updated successfully")
                return {"ok": True, "ts": response["ts"]}
            return None

        except SlackApiError as e:
            print(f"[Slack] Error updating message: {e.response['error']}")
            return None

    def add_reaction(self, channel: str, ts: str, emoji: str) -> bool:
        """
        Add a reaction to a message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            emoji: Emoji name (without colons)

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.reactions_add(
                channel=channel,
                timestamp=ts,
                name=emoji
            )
            return True

        except SlackApiError as e:
            print(f"[Slack] Error adding reaction: {e.response['error']}")
            return False

    def get_reactions(self, channel: str, ts: str) -> List[Dict]:
        """
        Get reactions on a message.

        Args:
            channel: Channel ID
            ts: Message timestamp

        Returns:
            List of reactions with counts and users
        """
        try:
            response = self.client.reactions_get(
                channel=channel,
                timestamp=ts
            )

            if response["ok"]:
                message = response.get("message", {})
                return message.get("reactions", [])
            return []

        except SlackApiError as e:
            print(f"[Slack] Error getting reactions: {e.response['error']}")
            return []


def main():
    """Test the Slack handler."""
    from dotenv import load_dotenv
    load_dotenv()

    try:
        handler = SlackHandler()

        if not handler.test_connection():
            print("Failed to connect to Slack")
            return

        # Test sending a simple message
        result = handler.send_message("Test message from Release Automation PoC")

        if result:
            print(f"Message sent! ts: {result['ts']}")

            # Test sending review notification
            review_result = handler.send_review_notification(
                release_date="2nd February 2026",
                doc_url="https://docs.google.com/document/d/test/edit",
                tldr_summary="*Deployments by:* DSP PL2, Audiences PL1\n*Major Feature:* New targeting options\n*Key Enhancement:* Performance improvements"
            )

            if review_result:
                print(f"Review notification sent! ts: {review_result['ts']}")

        else:
            print("Failed to send message")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
