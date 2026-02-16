#!/bin/bash
# Start the Slack Socket Mode handler for release automation

cd /home/user/release-automation-poc

# Activate virtual environment if exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Start the socket mode handler
exec python slack_socket_mode.py
