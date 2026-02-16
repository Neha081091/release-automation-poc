"""
Slack & Jira Webhook Server for Interactive Button Handling

This Flask server receives:
- Slack interaction payloads when users click buttons
- Jira webhooks for version events (created, released)

Usage:
    python slack_webhook_server.py

Then set your Slack App's Request URL to:
    https://your-ngrok-url.ngrok.io/slack/interactions

And configure Jira webhooks to:
    https://your-ngrok-url.ngrok.io/jira/webhook
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


# ============================================================================
# Jira Webhook Endpoints
# ============================================================================

# Store recent Jira webhook events for debugging/monitoring
jira_webhook_log = []
MAX_JIRA_LOG_ENTRIES = 100


@app.route('/jira/webhook', methods=['GET', 'POST'])
def handle_jira_webhook():
    """
    Handle Jira webhook events.

    GET: Health check / test the endpoint
    POST: Receive Jira webhook payloads

    Supported events:
    - jira:version_created
    - jira:version_released
    - jira:version_updated
    - jira:version_deleted
    """
    if request.method == 'GET':
        return jsonify({
            "status": "ok",
            "message": "Jira Webhook Receiver is running",
            "timestamp": datetime.now().isoformat(),
            "recent_events": len(jira_webhook_log)
        })

    # POST - handle webhook payload
    try:
        payload = request.get_json()

        if not payload:
            print("[Jira Webhook] Empty payload received")
            return make_response("Empty payload", 400)

        # Extract event details
        webhook_event = payload.get('webhookEvent', 'unknown')
        version = payload.get('version', {})
        version_name = version.get('name', 'unknown')
        project_key = version.get('projectId') or payload.get('project', {}).get('key', 'unknown')

        print(f"[Jira Webhook] Received event: {webhook_event}")
        print(f"[Jira Webhook] Version: {version_name}, Project: {project_key}")

        # Log the event
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event": webhook_event,
            "version_name": version_name,
            "project_key": project_key,
            "payload_summary": {
                "version_id": version.get('id'),
                "version_description": version.get('description', '')[:100] if version.get('description') else None,
                "released": version.get('released', False),
                "release_date": version.get('releaseDate')
            }
        }

        # Add to log (keep only recent entries)
        jira_webhook_log.insert(0, log_entry)
        if len(jira_webhook_log) > MAX_JIRA_LOG_ENTRIES:
            jira_webhook_log.pop()

        # Handle specific version events
        if webhook_event == 'jira:version_created':
            handle_jira_version_created(version, payload)
        elif webhook_event == 'jira:version_released':
            handle_jira_version_released(version, payload)
        elif webhook_event == 'jira:version_updated':
            handle_jira_version_updated(version, payload)
        elif webhook_event == 'jira:version_deleted':
            handle_jira_version_deleted(version, payload)
        else:
            print(f"[Jira Webhook] Unhandled event type: {webhook_event}")

        return jsonify({
            "status": "success",
            "message": "Webhook received",
            "event": webhook_event
        })

    except json.JSONDecodeError as e:
        print(f"[Jira Webhook] JSON decode error: {e}")
        return make_response("Invalid JSON payload", 400)
    except Exception as e:
        print(f"[Jira Webhook] Error processing webhook: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


def handle_jira_version_created(version: dict, payload: dict):
    """
    Handle jira:version_created event.

    Args:
        version: Version data from the webhook
        payload: Full webhook payload
    """
    version_name = version.get('name', 'unknown')
    project_id = version.get('projectId', 'unknown')

    print(f"[Jira Webhook] New version created: {version_name} (Project: {project_id})")

    # TODO: Add your automation here
    # Examples:
    # - Send Slack notification about new version
    # - Create release ticket template
    # - Initialize release checklist


def handle_jira_version_released(version: dict, payload: dict):
    """
    Handle jira:version_released event.

    This is typically the trigger for release notes generation.

    Args:
        version: Version data from the webhook
        payload: Full webhook payload
    """
    version_name = version.get('name', 'unknown')
    project_id = version.get('projectId', 'unknown')
    release_date = version.get('releaseDate', 'unknown')

    print(f"[Jira Webhook] Version released: {version_name}")
    print(f"[Jira Webhook] Release date: {release_date}")

    # TODO: Add your automation here
    # Examples:
    # - Trigger release notes generation workflow
    # - Send Slack announcement about the release
    # - Update external dashboards
    # - Notify stakeholders


def handle_jira_version_updated(version: dict, payload: dict):
    """
    Handle jira:version_updated event.

    Args:
        version: Version data from the webhook
        payload: Full webhook payload
    """
    version_name = version.get('name', 'unknown')

    print(f"[Jira Webhook] Version updated: {version_name}")

    # TODO: Add your automation here
    # Examples:
    # - Update release tracking spreadsheet
    # - Sync version info to external systems


def handle_jira_version_deleted(version: dict, payload: dict):
    """
    Handle jira:version_deleted event.

    Args:
        version: Version data from the webhook
        payload: Full webhook payload
    """
    version_name = version.get('name', 'unknown')

    print(f"[Jira Webhook] Version deleted: {version_name}")

    # TODO: Add your automation here
    # Examples:
    # - Clean up related resources
    # - Notify team about version removal


@app.route('/jira/webhook/log', methods=['GET'])
def get_jira_webhook_log():
    """
    Get recent Jira webhook events (for debugging/monitoring).
    """
    limit = request.args.get('limit', 20, type=int)
    return jsonify({
        "status": "ok",
        "total_events": len(jira_webhook_log),
        "events": jira_webhook_log[:limit]
    })


def run_server(host='0.0.0.0', port=5000, debug=False):
    """Run the Flask server."""
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║       Slack & Jira Webhook Server                            ║
╠══════════════════════════════════════════════════════════════╣
║  Server starting on http://{host}:{port}
║                                                              ║
║  Slack Endpoints:                                            ║
║    GET  /                    - Health check                  ║
║    POST /slack/interactions  - Slack button callbacks        ║
║    POST /slack/events        - Slack events API              ║
║                                                              ║
║  Jira Endpoints:                                             ║
║    GET  /jira/webhook        - Test webhook endpoint         ║
║    POST /jira/webhook        - Receive Jira webhooks         ║
║    GET  /jira/webhook/log    - View recent webhook events    ║
║                                                              ║
║  Make sure ngrok is running:                                 ║
║    ngrok http {port}
║                                                              ║
║  Configure in Slack App:                                     ║
║    https://YOUR-NGROK-URL/slack/interactions                 ║
║                                                              ║
║  Configure in Jira Webhooks:                                 ║
║    https://YOUR-NGROK-URL/jira/webhook                       ║
║    Events: version_created, version_released,                ║
║            version_updated, version_deleted                  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    # Default to port 3000 to avoid macOS AirPlay conflict on port 5000
    port = int(os.getenv('PORT', 3000))
    debug = os.getenv('FLASK_ENV') == 'development'
    run_server(port=port, debug=debug)
