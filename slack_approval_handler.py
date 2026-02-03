"""
Slack Approval Workflow Handler (Emoji-Based)

This module handles the Slack approval workflow using emoji reactions
instead of interactive buttons - no Slack App setup required.

Workflow:
1. Post approval message with each PL on a separate line
2. PMOs react with emojis:
   - ✅ (white_check_mark) = Approve
   - ❌ (x) = Reject
   - ➡️ (arrow_right) = Tomorrow
3. Poll for reactions to track status
4. When all PLs decided, post final notes

Requirements:
- SLACK_BOT_TOKEN with reactions:read scope
- Or just SLACK_WEBHOOK_URL for posting (reactions checked manually)
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

load_dotenv()

# File paths
APPROVAL_STATUS_FILE = "approval_status.json"
TODAY_NOTES_FILE = "processed_notes.json"
TOMORROW_NOTES_FILE = "tomorrow_notes.json"

# Emoji mappings
EMOJI_APPROVE = "white_check_mark"  # ✅
EMOJI_REJECT = "x"                   # ❌
EMOJI_TOMORROW = "arrow_right"       # ➡️
EMOJI_ANNOUNCE = "tada"              # 🎉

# Google Docs URL
GOOGLE_DOC_URL = os.getenv("GOOGLE_DOC_URL", "")
GOOGLE_DOC_ID = os.getenv("GOOGLE_DOC_ID", "")
if GOOGLE_DOC_ID and not GOOGLE_DOC_URL:
    GOOGLE_DOC_URL = f"https://docs.google.com/document/d/{GOOGLE_DOC_ID}/edit"


class SlackApprovalHandler:
    """Handler for Slack approval workflow using emoji reactions."""

    def __init__(self, bot_token: str = None, channel: str = None, announce_channel: str = None):
        """
        Initialize Slack approval handler.

        Args:
            bot_token: Slack Bot OAuth token
            channel: Channel ID for posting approval message
            announce_channel: Channel ID for final announcements
        """
        self.bot_token = bot_token or os.getenv('SLACK_BOT_TOKEN')
        self.channel = channel or os.getenv('SLACK_REVIEW_CHANNEL') or os.getenv('SLACK_DM_CHANNEL')
        self.announce_channel = announce_channel or os.getenv('SLACK_ANNOUNCE_CHANNEL') or self.channel

        if not self.bot_token:
            raise ValueError("SLACK_BOT_TOKEN is required")

        self.client = WebClient(token=self.bot_token)
        print(f"[SlackApproval] Initialized for channel: {self.channel}")

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
        print(f"[SlackApproval] Saved status to {APPROVAL_STATUS_FILE}")

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

    def create_approval_status(self, release_notes: Dict) -> Dict:
        """Create initial approval status structure."""
        release_versions = release_notes.get("release_versions", {})
        product_lines = release_notes.get("product_lines", [])

        approval_state = {}
        for pl in product_lines:
            approval_state[pl] = {
                "release_version": release_versions.get(pl, "Unknown"),
                "status": "pending",
                "reacted_by": None,
                "reacted_at": None
            }

        return {
            "message_ts": None,
            "channel": self.channel,
            "release_date": release_notes.get("release_summary", ""),
            "created_at": datetime.now().isoformat(),
            "approval_state": approval_state,
            "pl_message_map": {},  # Maps PL name to message timestamp
            "global_state": {
                "all_decided": False,
                "approved_pls": [],
                "rejected_pls": [],
                "tomorrow_pls": []
            }
        }

    def post_approval_message(self, release_notes: Dict = None) -> Optional[Dict]:
        """
        Post the approval message to Slack.
        Posts a main message followed by individual messages per PL for reactions.

        Returns:
            Dict with message info or None on failure
        """
        if release_notes is None:
            release_notes = self.load_release_notes()

        if not release_notes:
            print("[SlackApproval] ERROR: No release notes found")
            return None

        product_lines = release_notes.get("product_lines", [])
        release_versions = release_notes.get("release_versions", {})

        # Create approval status
        approval_status = self.create_approval_status(release_notes)

        try:
            # Post header message
            header_text = (
                "*📋 Release Notes Ready for Review*\n\n"
                f"Please review the notes on <{GOOGLE_DOC_URL}|Daily Consolidated Diplomacy Summary>\n\n"
                "*React to each PL below:*\n"
                "• ✅ = Approve\n"
                "• ❌ = Reject (move to tomorrow)\n"
                "• ➡️ = Tomorrow (defer)\n\n"
                "_Once all PLs reviewed, react with 🎉 on final message to announce._\n"
                "─" * 20
            )

            header_response = self.client.chat_postMessage(
                channel=self.channel,
                text=header_text,
                mrkdwn=True
            )

            if not header_response["ok"]:
                print(f"[SlackApproval] Failed to post header")
                return None

            approval_status["message_ts"] = header_response["ts"]
            approval_status["channel"] = header_response["channel"]

            # Post individual message for each PL (for reactions)
            pl_message_map = {}
            for pl in product_lines:
                version = release_versions.get(pl, "Release")
                pl_text = f"*{pl}:* {version}"

                pl_response = self.client.chat_postMessage(
                    channel=self.channel,
                    text=pl_text,
                    mrkdwn=True
                )

                if pl_response["ok"]:
                    pl_message_map[pl] = pl_response["ts"]
                    # Add seed reactions to guide users
                    for emoji in [EMOJI_APPROVE, EMOJI_REJECT, EMOJI_TOMORROW]:
                        try:
                            self.client.reactions_add(
                                channel=self.channel,
                                timestamp=pl_response["ts"],
                                name=emoji
                            )
                        except SlackApiError:
                            pass  # Reaction may already exist

                time.sleep(0.3)  # Rate limiting

            approval_status["pl_message_map"] = pl_message_map

            # Post footer with "Good to Announce" trigger
            footer_text = (
                "─" * 20 + "\n"
                "*Once all PLs are reviewed, react with 🎉 here to announce:*"
            )

            footer_response = self.client.chat_postMessage(
                channel=self.channel,
                text=footer_text,
                mrkdwn=True
            )

            if footer_response["ok"]:
                approval_status["announce_message_ts"] = footer_response["ts"]
                # Add 🎉 reaction
                try:
                    self.client.reactions_add(
                        channel=self.channel,
                        timestamp=footer_response["ts"],
                        name=EMOJI_ANNOUNCE
                    )
                except SlackApiError:
                    pass

            # Save status
            self.save_approval_status(approval_status)

            print(f"[SlackApproval] Posted approval messages for {len(product_lines)} PLs")
            return {
                "ok": True,
                "ts": header_response["ts"],
                "channel": header_response["channel"],
                "pl_count": len(product_lines)
            }

        except SlackApiError as e:
            print(f"[SlackApproval] Slack API error: {e.response['error']}")
            return None

    def check_reactions(self) -> Dict:
        """
        Check reactions on PL messages to determine approval status.

        Returns:
            Updated approval status dict
        """
        approval_status = self.load_approval_status()
        if not approval_status:
            print("[SlackApproval] No approval status found")
            return {}

        channel = approval_status.get("channel")
        pl_message_map = approval_status.get("pl_message_map", {})

        if not pl_message_map:
            print("[SlackApproval] No PL messages to check")
            return approval_status

        print(f"[SlackApproval] Checking reactions for {len(pl_message_map)} PLs...")

        for pl_name, message_ts in pl_message_map.items():
            try:
                # Get reactions for this message
                response = self.client.reactions_get(
                    channel=channel,
                    timestamp=message_ts
                )

                if not response["ok"]:
                    continue

                message = response.get("message", {})
                reactions = message.get("reactions", [])

                # Check for user reactions (excluding bot's seed reactions)
                for reaction in reactions:
                    emoji = reaction.get("name")
                    users = reaction.get("users", [])
                    count = reaction.get("count", 0)

                    # Skip if only 1 reaction (just the bot's seed)
                    if count <= 1:
                        continue

                    # Determine status based on emoji
                    if emoji == EMOJI_APPROVE:
                        approval_status["approval_state"][pl_name]["status"] = "approved"
                        approval_status["approval_state"][pl_name]["reacted_at"] = datetime.now().isoformat()
                        if pl_name not in approval_status["global_state"]["approved_pls"]:
                            approval_status["global_state"]["approved_pls"].append(pl_name)
                        break
                    elif emoji == EMOJI_REJECT:
                        approval_status["approval_state"][pl_name]["status"] = "rejected"
                        approval_status["approval_state"][pl_name]["reacted_at"] = datetime.now().isoformat()
                        if pl_name not in approval_status["global_state"]["rejected_pls"]:
                            approval_status["global_state"]["rejected_pls"].append(pl_name)
                        self.move_to_tomorrow(pl_name)
                        break
                    elif emoji == EMOJI_TOMORROW:
                        approval_status["approval_state"][pl_name]["status"] = "tomorrow"
                        approval_status["approval_state"][pl_name]["reacted_at"] = datetime.now().isoformat()
                        if pl_name not in approval_status["global_state"]["tomorrow_pls"]:
                            approval_status["global_state"]["tomorrow_pls"].append(pl_name)
                        self.move_to_tomorrow(pl_name)
                        break

            except SlackApiError as e:
                print(f"[SlackApproval] Error checking {pl_name}: {e.response['error']}")

        # Check if all decided
        all_statuses = [v["status"] for v in approval_status["approval_state"].values()]
        approval_status["global_state"]["all_decided"] = all(s != "pending" for s in all_statuses)

        # Save updated status
        self.save_approval_status(approval_status)

        # Print summary
        approved = len(approval_status["global_state"]["approved_pls"])
        rejected = len(approval_status["global_state"]["rejected_pls"])
        tomorrow = len(approval_status["global_state"]["tomorrow_pls"])
        pending = len([s for s in all_statuses if s == "pending"])

        print(f"[SlackApproval] Status: {approved} approved, {rejected} rejected, {tomorrow} tomorrow, {pending} pending")

        return approval_status

    def check_announce_trigger(self) -> bool:
        """
        Check if someone reacted with 🎉 on the announce message.

        Returns:
            True if announce was triggered
        """
        approval_status = self.load_approval_status()
        if not approval_status:
            return False

        announce_ts = approval_status.get("announce_message_ts")
        channel = approval_status.get("channel")

        if not announce_ts:
            return False

        try:
            response = self.client.reactions_get(
                channel=channel,
                timestamp=announce_ts
            )

            if not response["ok"]:
                return False

            message = response.get("message", {})
            reactions = message.get("reactions", [])

            for reaction in reactions:
                if reaction.get("name") == EMOJI_ANNOUNCE and reaction.get("count", 0) > 1:
                    return True

            return False

        except SlackApiError as e:
            print(f"[SlackApproval] Error checking announce: {e.response['error']}")
            return False

    def move_to_tomorrow(self, pl_name: str) -> bool:
        """Move a PL's release notes to tomorrow's file."""
        print(f"[SlackApproval] Moving {pl_name} to tomorrow...")

        today_notes = self.load_release_notes(TODAY_NOTES_FILE)
        if not today_notes:
            return False

        tomorrow_notes = self.load_release_notes(TOMORROW_NOTES_FILE)
        if not tomorrow_notes:
            tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%d %B %Y")
            tomorrow_notes = {
                "processed_at": datetime.now().isoformat(),
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

        if pl_name in today_notes.get("product_lines", []):
            tomorrow_notes["product_lines"].append(pl_name)

            for key in ["release_versions", "fix_version_urls", "epic_urls_by_pl",
                        "tldr_by_pl", "body_by_pl", "grouped_data"]:
                if key in today_notes and pl_name in today_notes[key]:
                    tomorrow_notes[key][pl_name] = today_notes[key][pl_name]

        self.save_release_notes(tomorrow_notes, TOMORROW_NOTES_FILE)
        return True

    def post_final_announcement(self) -> bool:
        """
        Post final approved release notes to the announcement channel.

        Returns:
            True if successful
        """
        approval_status = self.load_approval_status()
        if not approval_status:
            print("[SlackApproval] No approval status found")
            return False

        approved_pls = approval_status["global_state"].get("approved_pls", [])
        if not approved_pls:
            print("[SlackApproval] No approved PLs to announce")
            return False

        release_notes = self.load_release_notes()
        if not release_notes:
            print("[SlackApproval] No release notes found")
            return False

        release_date = release_notes.get("release_summary", "Release")
        tldr_by_pl = release_notes.get("tldr_by_pl", {})
        body_by_pl = release_notes.get("body_by_pl", {})
        release_versions = release_notes.get("release_versions", {})

        # Build announcement message
        message_parts = [
            f"*🚀 RELEASE DEPLOYED: {release_date}*\n",
            "─" * 30 + "\n",
            "*------------------TL;DR:------------------*\n\n",
            "*Key Deployments:*\n"
        ]

        for pl in approved_pls:
            if pl in tldr_by_pl:
                message_parts.append(f"• *{pl}:* {tldr_by_pl[pl]}\n")

        message_parts.append("\n" + "─" * 30 + "\n")

        # Add detailed sections
        for pl in approved_pls:
            version = release_versions.get(pl, "")
            message_parts.append(f"\n*{pl}: {version}*\n")
            if pl in body_by_pl:
                body = body_by_pl[pl]
                # Truncate if too long
                if len(body) > 2500:
                    body = body[:2500] + "...\n_(truncated)_"
                message_parts.append(body + "\n")

        message_parts.append("\n" + "─" * 30)
        message_parts.append(f"\n_Posted at {datetime.now().strftime('%H:%M')} | Approved PLs: {len(approved_pls)}_")

        full_message = "".join(message_parts)

        try:
            response = self.client.chat_postMessage(
                channel=self.announce_channel,
                text=full_message,
                mrkdwn=True
            )

            if response["ok"]:
                print(f"[SlackApproval] ✅ Final announcement posted to {self.announce_channel}")

                # Update approval status
                approval_status["global_state"]["announced"] = True
                approval_status["global_state"]["announced_at"] = datetime.now().isoformat()
                self.save_approval_status(approval_status)

                # Post confirmation to review channel
                self.client.chat_postMessage(
                    channel=approval_status.get("channel", self.channel),
                    text=f"✅ *Release notes posted to <#{self.announce_channel}>*\n"
                         f"Approved: {', '.join(approved_pls)}"
                )

                return True
            else:
                print(f"[SlackApproval] Failed to post: {response.get('error')}")
                return False

        except SlackApiError as e:
            print(f"[SlackApproval] Error: {e.response['error']}")
            return False

    def run_poll_loop(self, interval: int = 30, max_duration: int = 3600):
        """
        Run a polling loop to check reactions and trigger announcement.

        Args:
            interval: Seconds between checks
            max_duration: Maximum seconds to run (default 1 hour)
        """
        print(f"[SlackApproval] Starting poll loop (interval: {interval}s, max: {max_duration}s)")
        start_time = time.time()

        while time.time() - start_time < max_duration:
            # Check reactions
            status = self.check_reactions()

            if not status:
                print("[SlackApproval] No status found, exiting")
                break

            # Check if all decided
            if status["global_state"].get("all_decided"):
                print("[SlackApproval] All PLs decided!")

                # Check for announce trigger
                if self.check_announce_trigger():
                    print("[SlackApproval] 🎉 Announce triggered!")
                    self.post_final_announcement()
                    break

            # Wait for next check
            print(f"[SlackApproval] Next check in {interval}s...")
            time.sleep(interval)

        print("[SlackApproval] Poll loop ended")


def main():
    """Post approval message or run poll loop."""
    import argparse

    parser = argparse.ArgumentParser(description='Slack Approval Workflow (Emoji-based)')
    parser.add_argument('--post', action='store_true', help='Post approval message')
    parser.add_argument('--check', action='store_true', help='Check reactions once')
    parser.add_argument('--poll', action='store_true', help='Run polling loop')
    parser.add_argument('--announce', action='store_true', help='Post final announcement')
    parser.add_argument('--interval', type=int, default=30, help='Poll interval in seconds')
    args = parser.parse_args()

    try:
        handler = SlackApprovalHandler()

        if args.post:
            result = handler.post_approval_message()
            if result:
                print(f"\n✅ Approval message posted!")
                print(f"   Channel: {result['channel']}")
                print(f"   PLs: {result['pl_count']}")
                print("\nPMOs can now react with:")
                print("   ✅ = Approve")
                print("   ❌ = Reject")
                print("   ➡️ = Tomorrow")
            else:
                print("❌ Failed to post approval message")

        elif args.check:
            status = handler.check_reactions()
            if status:
                gs = status["global_state"]
                print(f"\nApproval Status:")
                print(f"  Approved: {gs['approved_pls']}")
                print(f"  Rejected: {gs['rejected_pls']}")
                print(f"  Tomorrow: {gs['tomorrow_pls']}")
                print(f"  All decided: {gs['all_decided']}")

        elif args.poll:
            handler.run_poll_loop(interval=args.interval)

        elif args.announce:
            if handler.post_final_announcement():
                print("✅ Announcement posted!")
            else:
                print("❌ Failed to post announcement")

        else:
            parser.print_help()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
