"""
Slack Webhook Server for Interactive Button Handling

This Flask server receives Slack interaction payloads when users click buttons
and routes them to the appropriate handlers.

Setup:
1. Create a Slack App with Interactivity enabled
2. Set the Request URL to: https://your-server.com/slack/interactions
3. Add the SLACK_SIGNING_SECRET to your environment

For local development, use ngrok:
    ngrok http 5000
    Then update Slack App's Request URL with the ngrok URL

Usage:
    python slack_webhook_server.py

Environment Variables:
    SLACK_BOT_TOKEN - Bot OAuth token
    SLACK_SIGNING_SECRET - Signing secret for request verification
    SLACK_REVIEW_CHANNEL - Channel for PMO review
    SLACK_ANNOUNCE_CHANNEL - Channel for final announcements
"""

import os
import json
import hmac
import hashlib
import time
from datetime import datetime
from flask import Flask, request, jsonify, make_response
from dotenv import load_dotenv

from slack_approval_handler import SlackApprovalHandler

load_dotenv()

app = Flask(__name__)

# Slack signing secret for verifying requests
SLACK_SIGNING_SECRET = os.getenv('SLACK_SIGNING_SECRET', '')

# Initialize the approval handler
approval_handler = None


def get_approval_handler():
    """Get or create the approval handler instance."""
    global approval_handler
    if approval_handler is None:
        approval_handler = SlackApprovalHandler()
    return approval_handler


def verify_slack_signature(request_data: bytes, timestamp: str, signature: str) -> bool:
    """
    Verify that the request came from Slack using the signing secret.

    Args:
        request_data: Raw request body bytes
        timestamp: X-Slack-Request-Timestamp header
        signature: X-Slack-Signature header

    Returns:
        True if signature is valid
    """
    if not SLACK_SIGNING_SECRET:
        print("[Webhook] WARNING: No signing secret configured, skipping verification")
        return True

    # Check timestamp to prevent replay attacks (within 5 minutes)
    if abs(time.time() - float(timestamp)) > 60 * 5:
        print("[Webhook] Request timestamp too old")
        return False

    # Create the signature base string
    sig_basestring = f"v0:{timestamp}:{request_data.decode('utf-8')}"

    # Calculate the expected signature
    my_signature = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode('utf-8'),
        sig_basestring.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    # Compare signatures
    if hmac.compare_digest(my_signature, signature):
        return True

    print("[Webhook] Signature verification failed")
    return False


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

    Slack sends interaction payloads as form-encoded with a 'payload' field
    containing JSON.
    """
    # Get raw request data for signature verification
    request_data = request.get_data()
    timestamp = request.headers.get('X-Slack-Request-Timestamp', '')
    signature = request.headers.get('X-Slack-Signature', '')

    # Verify the request is from Slack
    if not verify_slack_signature(request_data, timestamp, signature):
        return make_response("Invalid signature", 403)

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
    elif interaction_type == 'view_submission':
        return handle_view_submission(payload)
    elif interaction_type == 'shortcut':
        return handle_shortcut(payload)
    else:
        print(f"[Webhook] Unknown interaction type: {interaction_type}")
        return make_response("", 200)


def handle_block_actions(payload: dict):
    """
    Handle button click actions.

    Args:
        payload: Slack interaction payload

    Returns:
        Response to Slack
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
        # Return empty 200 to acknowledge - message is updated via API
        return make_response("", 200)
    else:
        print(f"[Webhook] Action failed: {message}")
        # Return error response
        return jsonify({
            "response_type": "ephemeral",
            "text": f"Error: {message}"
        })


def handle_view_submission(payload: dict):
    """Handle modal/view submissions (for future use)."""
    print("[Webhook] View submission received")
    return make_response("", 200)


def handle_shortcut(payload: dict):
    """Handle shortcuts/commands (for future use)."""
    print("[Webhook] Shortcut received")
    return make_response("", 200)


@app.route('/slack/events', methods=['POST'])
def handle_slack_events():
    """
    Handle Slack Events API callbacks.
    Primarily used for URL verification challenge.
    """
    data = request.get_json()

    # Handle URL verification challenge
    if data and data.get('type') == 'url_verification':
        return jsonify({'challenge': data.get('challenge')})

    # Handle other events
    event = data.get('event', {})
    event_type = event.get('type', 'unknown')
    print(f"[Webhook] Received event: {event_type}")

    return make_response("", 200)


@app.route('/api/post-approval', methods=['POST'])
def api_post_approval():
    """
    API endpoint to programmatically post an approval message.
    This can be called from the hybrid workflow.

    Request body (JSON):
        {
            "release_notes_file": "processed_notes.json"  // optional, defaults to processed_notes.json
        }
    """
    try:
        data = request.get_json() or {}
        release_notes_file = data.get('release_notes_file', 'processed_notes.json')

        handler = get_approval_handler()

        # Load release notes
        if os.path.exists(release_notes_file):
            with open(release_notes_file, 'r') as f:
                release_notes = json.load(f)
        else:
            return jsonify({
                "success": False,
                "error": f"Release notes file not found: {release_notes_file}"
            }), 404

        # Post approval message
        result = handler.post_approval_message(release_notes)

        if result:
            return jsonify({
                "success": True,
                "message_ts": result.get('ts'),
                "channel": result.get('channel')
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to post approval message"
            }), 500

    except Exception as e:
        print(f"[Webhook] Error in api_post_approval: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/status', methods=['GET'])
def api_get_status():
    """Get current approval status."""
    try:
        handler = get_approval_handler()
        status = handler.load_approval_status()

        if status:
            return jsonify({
                "success": True,
                "status": status
            })
        else:
            return jsonify({
                "success": False,
                "error": "No approval status found"
            }), 404

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/simulate-click', methods=['POST'])
def api_simulate_click():
    """
    Simulate a button click for testing (development only).

    Request body (JSON):
        {
            "pl_name": "DSP Core PL1",
            "action": "approve"  // approve, reject, or tomorrow
        }
    """
    if os.getenv('FLASK_ENV') != 'development':
        return jsonify({"error": "Only available in development mode"}), 403

    try:
        data = request.get_json()
        pl_name = data.get('pl_name')
        action = data.get('action')

        if not pl_name or not action:
            return jsonify({"error": "Missing pl_name or action"}), 400

        # Create a simulated payload
        payload = {
            "type": "block_actions",
            "user": {
                "id": "U_SIMULATE",
                "username": "simulator"
            },
            "actions": [{
                "action_id": f"{action}_{pl_name.replace(' ', '_')}",
                "value": json.dumps({"pl_name": pl_name, "action": action})
            }]
        }

        handler = get_approval_handler()
        success, message = handler.handle_button_click(payload)

        return jsonify({
            "success": success,
            "message": message
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def run_server(host='0.0.0.0', port=5000, debug=False):
    """
    Run the Flask server.

    Args:
        host: Host to bind to
        port: Port to listen on
        debug: Enable debug mode
    """
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         Slack Approval Webhook Server                       ║
╠══════════════════════════════════════════════════════════════╣
║  Server starting on http://{host}:{port}
║                                                              ║
║  Endpoints:                                                  ║
║    GET  /                    - Health check                  ║
║    POST /slack/interactions  - Slack button callbacks        ║
║    POST /slack/events        - Slack events API              ║
║    POST /api/post-approval   - Trigger approval message      ║
║    GET  /api/status          - Get approval status           ║
║                                                              ║
║  For local development, use ngrok:                           ║
║    ngrok http {port}
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'
    run_server(port=port, debug=debug)
