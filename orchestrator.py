"""
Release Automation Orchestrator

This script:
1. Runs the Socket Mode handler for Slack button clicks
2. Schedules the release notes pipeline to run at 12:00 PM daily

Usage:
    python orchestrator.py

The script will:
- Start listening for Slack button clicks immediately
- Run the release notes pipeline at 12:00 PM daily
- You can also trigger manually by pressing Enter
"""

import os
import signal
import sys
import threading
import time
from datetime import datetime
from dotenv import load_dotenv

# Ignore SIGPIPE so writes to closed stdout/sockets don't kill the process (e.g. nohup > log)
try:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
except (AttributeError, OSError):
    pass  # Windows or unsupported

load_dotenv()

# --- Install logging filter FIRST: block at Logger.callHandlers so NO handler ever sees the record ---
def _install_logging_filter():
    import logging
    def _should_suppress(record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = ""
        return "on_error invoked" in msg and "Broken pipe" in msg

    _orig_call_handlers = logging.Logger.callHandlers
    def _filtered_call_handlers(self, record):
        if _should_suppress(record):
            return
        _orig_call_handlers(self, record)
    logging.Logger.callHandlers = _filtered_call_handlers
_install_logging_filter()

# Replace stderr so any direct writes are also filtered
class _FilteredStream:
    """Drops Bolt's 'on_error invoked' BrokenPipeError lines; forwards everything else."""
    def __init__(self, stream):
        self._stream = stream
    def write(self, s):
        if s and "on_error invoked" in s:
            return
        try:
            self._stream.write(s)
        except BrokenPipeError:
            pass
    def flush(self):
        try:
            self._stream.flush()
        except BrokenPipeError:
            pass
    def __getattr__(self, name):
        return getattr(self._stream, name)


# Always install filter so Bolt's "on_error invoked" BrokenPipeError lines are dropped
sys.stdout = _FilteredStream(sys.stdout)
sys.stderr = _FilteredStream(sys.stderr)

# --- Suppress at source (SDK client) so the error handler never logs ---
def _patch_slack_sdk_on_error():
    import errno
    try:
        from slack_sdk.socket_mode.builtin import client as _sm_client

        def _is_broken_pipe(e):
            if e is None:
                return False
            if isinstance(e, BrokenPipeError):
                return True
            if isinstance(e, OSError) and getattr(e, "errno", None) == errno.EPIPE:
                return True
            return False

        _orig_on_error = _sm_client.SocketModeClient._on_error
        def _on_error_suppress_broken_pipe(self, error):
            if _is_broken_pipe(error):
                return
            _orig_on_error(self, error)
        _sm_client.SocketModeClient._on_error = _on_error_suppress_broken_pipe

        _orig_connect = _sm_client.SocketModeClient.connect
        def _connect_wrap_error_listener(self):
            _real_on_error = self._on_error
            def _wrapped_listener(e):
                if _is_broken_pipe(e):
                    return
                _real_on_error(e)
            self._on_error = _wrapped_listener
            try:
                return _orig_connect(self)
            finally:
                self._on_error = _real_on_error
        _sm_client.SocketModeClient.connect = _connect_wrap_error_listener
    except Exception:
        pass
_patch_slack_sdk_on_error()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Import the pipeline components
from slack_socket_mode import post_approval_message, app
from slack_bolt.adapter.socket_mode import SocketModeHandler
from scheduler_config import SchedulerConfig

# Import the hybrid step modules
import subprocess


def run_pipeline():
    """Run the complete release notes pipeline."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Release Notes Pipeline")
    print(f"{'='*60}\n")

    try:
        # Step 1: Fetch Jira tickets
        print("[Pipeline] Step 1: Fetching Jira tickets...")
        result = subprocess.run(
            ['python', 'hybrid_step1_export_jira.py'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[Pipeline] Step 1 failed: {result.stderr}")
            return False

        # Step 2: Process with Claude
        print("\n[Pipeline] Step 2: Processing with Claude API...")
        result = subprocess.run(
            ['python', 'hybrid_step2_process_claude.py'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[Pipeline] Step 2 failed: {result.stderr}")
            return False

        # Step 3: Update Google Doc & Post to Slack
        print("\n[Pipeline] Step 3: Updating Google Doc & posting to Slack...")
        result = subprocess.run(
            ['python', 'hybrid_step3_update_docs.py'],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[Pipeline] Step 3 failed: {result.stderr}")
            return False

        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Pipeline Complete!")
        print(f"{'='*60}\n")

        return True

    except Exception as e:
        print(f"[Pipeline] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def start_socket_mode():
    """Start the Socket Mode handler in a separate thread."""
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not app_token:
        print("[Socket Mode] ERROR: SLACK_APP_TOKEN not found in .env")
        return

    print("[Socket Mode] Starting Socket Mode handler...")
    handler = SocketModeHandler(app, app_token)
    handler.start()


def main(daemon_mode=False):
    """Main orchestrator function."""

    print("""
╔══════════════════════════════════════════════════════════════╗
║         Release Automation Orchestrator                      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  This script:                                                ║
║  • Listens for Slack button clicks (Socket Mode)             ║
║  • Runs release pipeline at 12:00 PM daily                   ║
║                                                              ║
║  Commands:                                                   ║
║  • Press Enter  - Run pipeline manually                      ║
║  • Type 'quit'  - Exit the orchestrator                      ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

    # Check required environment variables
    required_vars = ['SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'SLACK_REVIEW_CHANNEL']
    missing = [v for v in required_vars if not os.getenv(v)]

    if missing:
        print(f"[Error] Missing environment variables: {', '.join(missing)}")
        print("Please add them to your .env file")
        return

    # Start Socket Mode in a background thread
    socket_thread = threading.Thread(target=start_socket_mode, daemon=True)
    socket_thread.start()

    # Give Socket Mode time to connect
    time.sleep(2)

    # Set up the scheduler with explicit timezone (IST) so 12:00 fires at 12:00 PM IST
    # even when the machine or nohup env uses a different TZ (e.g. UTC)
    scheduler = BackgroundScheduler(timezone=SchedulerConfig.TIMEZONE)
    hour = SchedulerConfig.SCHEDULE_HOUR
    minute = SchedulerConfig.SCHEDULE_MINUTE

    scheduler.add_job(
        run_pipeline,
        CronTrigger(hour=hour, minute=minute, timezone=SchedulerConfig.TIMEZONE),
        id='daily_pipeline',
        name='Daily Release Notes Pipeline'
    )

    scheduler.start()

    job = scheduler.get_job('daily_pipeline')
    next_run = job.next_run_time if job else "N/A"
    print(f"[Scheduler] Pipeline scheduled daily at {hour:02d}:{minute:02d} {SchedulerConfig.TIMEZONE}")
    print(f"[Scheduler] Next run: {next_run}")
    print(f"\n[Ready] Listening for Slack button clicks...")

    # Interactive only if stdin and stdout are both a TTY (real terminal).
    # With nohup ... > file & stdout is not a TTY, so we skip input() and avoid suspend.
    if not daemon_mode and sys.stdin.isatty() and sys.stdout.isatty():
        print("[Ready] Press Enter to run pipeline manually, or type 'quit' to exit\n")
        try:
            while True:
                user_input = input()

                if user_input.lower() == 'quit':
                    print("\n[Shutdown] Stopping orchestrator...")
                    scheduler.shutdown()
                    break
                else:
                    # Run pipeline manually
                    print("\n[Manual] Running pipeline now...")
                    run_pipeline()
                    print("\n[Ready] Press Enter to run again, or type 'quit' to exit\n")

        except KeyboardInterrupt:
            print("\n[Shutdown] Stopping orchestrator...")
            scheduler.shutdown()
    else:
        # Running in background mode (nohup, etc.)
        print("[Background] Running in daemon mode. Check logs for updates.\n")
        try:
            while True:
                time.sleep(60)  # Keep alive, check every minute
        except KeyboardInterrupt:
            print("\n[Shutdown] Stopping orchestrator...")
            scheduler.shutdown()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true', help='Run in daemon mode (no input)')
    args = parser.parse_args()
    main(daemon_mode=args.daemon)
