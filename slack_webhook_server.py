"""
Slack Webhook Server for Interactive Button Handling

This Flask server receives Slack interaction payloads when users click buttons.

Usage:
    python slack_webhook_server.py

Then set your Slack App's Request URL to:
    https://your-ngrok-url.ngrok.io/slack/interactions
"""

import os
import json
import hmac
import hashlib
import time
from datetime import datetime
from flask import Flask, request, jsonify, make_response
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Slack signing secret for verifying requests (optional but recommended)
SLACK_SIGNING_SECRET = os.getenv('SLACK_SIGNING_SECRET', '')

# Import the approval handler
from slack_approval_handler import SlackApprovalHandler

# Global handler instance
approval_handler = None


def get_approval_handler():
    """Get or create the approval handler instance."""
    global approval_handler
    if approval_handler is None:
        approval_handler = SlackApprovalHandler()
    return approval_handler


@app.route('/', methods=['GET'])
def home():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "service": "Slack Approval Webhook Server",
        "timestamp": datetime.now().isoformat()
    })


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy"})


@app.route('/slack/interactions', methods=['POST'])
def handle_slack_interaction():
    """
    Handle Slack interactive component payloads (button clicks).
    """
    # Parse the payload
    try:
        payload_str = request.form.get('payload', '{}')
        payload = json.loads(payload_str)
    except json.JSONDecodeError as e:
        print(f"[Webhook] JSON decode error: {e}")
        return make_response("Invalid payload", 400)

    # Log the interaction
    interaction_type = payload.get('type', 'unknown')
    user = payload.get('user', {})
    print(f"[Webhook] Received {interaction_type} from {user.get('username', 'unknown')}")

    # Handle different interaction types
    if interaction_type == 'block_actions':
        return handle_block_actions(payload)
    else:
        print(f"[Webhook] Unknown interaction type: {interaction_type}")
        return make_response("", 200)


def handle_block_actions(payload: dict):
    """
    Handle button click actions.
    """
    actions = payload.get('actions', [])
    if not actions:
        return make_response("", 200)

    action = actions[0]
    action_id = action.get('action_id', '')

    print(f"[Webhook] Handling action: {action_id}")

    # Get the handler
    handler = get_approval_handler()

    # Process the button click
    success, message = handler.handle_button_click(payload)

    if success:
        print(f"[Webhook] Action successful: {message}")
        return make_response("", 200)
    else:
        print(f"[Webhook] Action failed: {message}")
        return jsonify({
            "response_type": "ephemeral",
            "text": f"Error: {message}"
        })


@app.route('/slack/events', methods=['POST'])
def handle_slack_events():
    """
    Handle Slack Events API callbacks.
    """
    data = request.get_json()

    # Handle URL verification challenge
    if data and data.get('type') == 'url_verification':
        return jsonify({'challenge': data.get('challenge')})

    return make_response("", 200)


def run_server(host='0.0.0.0', port=5000, debug=False):
    """Run the Flask server."""
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         Slack Approval Webhook Server                        ║
╠══════════════════════════════════════════════════════════════╣
║  Server starting on http://{host}:{port}
║                                                              ║
║  Endpoints:                                                  ║
║    GET  /                    - Health check                  ║
║    POST /slack/interactions  - Slack button callbacks        ║
║    POST /slack/events        - Slack events API              ║
║                                                              ║
║  Make sure ngrok is running:                                 ║
║    ngrok http {port}
║                                                              ║
║  Set Request URL in Slack App to:                            ║
║    https://YOUR-NGROK-URL/slack/interactions                 ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'
    run_server(port=port, debug=debug)
