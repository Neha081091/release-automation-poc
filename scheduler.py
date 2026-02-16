#!/usr/bin/env python3
"""
Daily Scheduler for Release Automation PoC

This script runs the release automation workflow automatically every day at 12:00 PM IST.
It handles:
- Scheduled execution using APScheduler
- Logging all operations
- Slack notifications for success/failure
- Retry logic for failed runs
- Metrics tracking

Usage:
    Local testing:     python scheduler.py
    Run immediately:   python scheduler.py --run-now
    Test mode:         python scheduler.py --test
    Background:        nohup python scheduler.py > scheduler.log 2>&1 &
    Stop:              pkill -f scheduler.py

Author: DeepIntent Release Automation Team
"""

import os
import sys
import json
import time
import logging
import subprocess
from datetime import datetime
from typing import Optional

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import configuration
from scheduler_config import SchedulerConfig as Config

# Ensure log directory exists
Config.ensure_log_directory()

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format=Config.LOG_FORMAT,
    handlers=[
        logging.FileHandler(Config.get_log_path()),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def send_slack_notification(message: str, is_error: bool = False) -> bool:
    """
    Send notification to Slack.

    Args:
        message: Message text to send
        is_error: If True, format as error message

    Returns:
        True if sent successfully, False otherwise
    """
    webhook_url = Config.SLACK_WEBHOOK_URL

    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL not configured, skipping notification")
        return False

    try:
        # Format message with emoji
        if is_error:
            formatted_message = f"❌ *Release Automation Failed*\n{message}"
        else:
            formatted_message = f"✅ *Release Automation Succeeded*\n{message}"

        payload = {
            "text": formatted_message,
            "mrkdwn": True
        }

        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            logger.info("Slack notification sent successfully")
            return True
        else:
            logger.error(f"Slack notification failed: {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")
        return False


def update_metrics(success: bool, duration: float, error: str = None) -> None:
    """
    Update metrics file with run information.

    Args:
        success: Whether the run was successful
        duration: Duration of the run in seconds
        error: Error message if failed
    """
    metrics_path = Config.get_metrics_path()

    try:
        # Load existing metrics or create new
        if os.path.exists(metrics_path):
            with open(metrics_path, 'r') as f:
                metrics = json.load(f)
        else:
            metrics = {
                "total_runs": 0,
                "successful_runs": 0,
                "failed_runs": 0,
                "runs": []
            }

        # Update metrics
        metrics["total_runs"] += 1
        if success:
            metrics["successful_runs"] += 1
        else:
            metrics["failed_runs"] += 1

        # Add run record (keep last 30 runs)
        run_record = {
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "duration_seconds": round(duration, 2),
            "error": error
        }
        metrics["runs"].append(run_record)
        metrics["runs"] = metrics["runs"][-30:]  # Keep last 30

        # Save metrics
        with open(metrics_path, 'w') as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"Metrics updated: {metrics['successful_runs']}/{metrics['total_runs']} successful")

    except Exception as e:
        logger.error(f"Error updating metrics: {str(e)}")


def run_release_automation() -> bool:
    """
    Execute the release automation script with retry logic.

    Returns:
        True if successful, False otherwise
    """
    logger.info("=" * 60)
    logger.info("Starting scheduled release automation")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    start_time = time.time()
    last_error = None

    for attempt in range(1, Config.MAX_RETRIES + 1):
        try:
            logger.info(f"Attempt {attempt}/{Config.MAX_RETRIES}")

            # Run the main.py script
            result = subprocess.run(
                Config.COMMAND,
                capture_output=True,
                text=True,
                timeout=Config.EXECUTION_TIMEOUT,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )

            duration = time.time() - start_time

            if result.returncode == 0:
                # Success
                logger.info("Release automation completed successfully")
                logger.info(f"Duration: {duration:.2f} seconds")

                # Log output summary
                output_lines = result.stdout.strip().split('\n')
                if output_lines:
                    logger.info("Output summary:")
                    for line in output_lines[-10:]:  # Last 10 lines
                        logger.info(f"  {line}")

                # Update metrics
                update_metrics(success=True, duration=duration)

                # Send success notification
                send_slack_notification(
                    f"Completed at {datetime.now().strftime('%I:%M %p')}\n"
                    f"Duration: {duration:.1f} seconds"
                )

                return True

            else:
                # Failed
                last_error = result.stderr or result.stdout or "Unknown error"
                logger.error(f"Attempt {attempt} failed: {last_error[:500]}")

                if attempt < Config.MAX_RETRIES:
                    logger.info(f"Retrying in {Config.RETRY_DELAY_SECONDS} seconds...")
                    time.sleep(Config.RETRY_DELAY_SECONDS)

        except subprocess.TimeoutExpired:
            last_error = f"Execution timed out after {Config.EXECUTION_TIMEOUT} seconds"
            logger.error(last_error)

            if attempt < Config.MAX_RETRIES:
                logger.info(f"Retrying in {Config.RETRY_DELAY_SECONDS} seconds...")
                time.sleep(Config.RETRY_DELAY_SECONDS)

        except Exception as e:
            last_error = str(e)
            logger.error(f"Unexpected error: {last_error}")

            if attempt < Config.MAX_RETRIES:
                logger.info(f"Retrying in {Config.RETRY_DELAY_SECONDS} seconds...")
                time.sleep(Config.RETRY_DELAY_SECONDS)

    # All retries exhausted
    duration = time.time() - start_time
    logger.error(f"All {Config.MAX_RETRIES} attempts failed")

    # Update metrics
    update_metrics(success=False, duration=duration, error=last_error[:200] if last_error else None)

    # Send failure notification
    send_slack_notification(
        f"Failed after {Config.MAX_RETRIES} attempts\n"
        f"Error: {last_error[:200] if last_error else 'Unknown'}",
        is_error=True
    )

    return False


def run_immediately() -> None:
    """Run the automation immediately (for testing)."""
    logger.info("Running automation immediately (manual trigger)")
    run_release_automation()


def main():
    """Main entry point for the scheduler."""
    print("""
╔═══════════════════════════════════════════════════════════════════╗
║           RELEASE AUTOMATION SCHEDULER - DeepIntent               ║
║                                                                   ║
║   Automated daily release notes at 12:00 PM IST                   ║
╚═══════════════════════════════════════════════════════════════════╝
    """)

    # Print configuration
    Config.print_config()

    # Check for command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == '--run-now':
            # Run immediately for testing
            run_immediately()
            return
        elif sys.argv[1] == '--test':
            # Test mode: run in 10 seconds
            logger.info("Test mode: Running in 10 seconds...")
            time.sleep(10)
            run_immediately()
            return
        elif sys.argv[1] == '--help':
            print("""
Usage:
    python scheduler.py              Start scheduler (runs at 12:00 PM IST daily)
    python scheduler.py --run-now    Run automation immediately
    python scheduler.py --test       Run automation after 10 second delay
    python scheduler.py --help       Show this help message

Background mode:
    nohup python scheduler.py > scheduler.log 2>&1 &

Check if running:
    ps aux | grep scheduler.py

Stop:
    pkill -f scheduler.py

View logs:
    tail -f logs/release_automation.log
            """)
            return

    # Create scheduler
    scheduler = BlockingScheduler()

    # Add the weekday-only job (Monday-Friday)
    trigger = CronTrigger(
        day_of_week='mon-fri',
        hour=Config.SCHEDULE_HOUR,
        minute=Config.SCHEDULE_MINUTE,
        timezone=Config.TIMEZONE
    )

    scheduler.add_job(
        run_release_automation,
        trigger=trigger,
        id='release_automation',
        name='Daily Release Automation',
        replace_existing=True
    )

    # Log startup
    logger.info("Scheduler started successfully")

    # Get next run time (handle different APScheduler versions)
    try:
        job = scheduler.get_job('release_automation')
        next_run = getattr(job, 'next_run_time', None) or "Check scheduler logs"
        logger.info(f"Next run: {next_run}")
        print(f"\n[INFO] Scheduler is running...")
        print(f"[INFO] Scheduled for: {Config.SCHEDULE_HOUR}:{Config.SCHEDULE_MINUTE:02d} {Config.TIMEZONE}")
        print(f"[INFO] Press Ctrl+C to stop\n")
    except Exception as e:
        logger.info(f"Scheduler configured for {Config.SCHEDULE_HOUR}:{Config.SCHEDULE_MINUTE:02d}")
        print(f"\n[INFO] Scheduler is running...")
        print(f"[INFO] Scheduled for: {Config.SCHEDULE_HOUR}:{Config.SCHEDULE_MINUTE:02d} {Config.TIMEZONE}")
        print(f"[INFO] Press Ctrl+C to stop\n")

    try:
        # Start the scheduler
        scheduler.start()

    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user (Ctrl+C)")
        print("\n[INFO] Scheduler stopped.")

    except Exception as e:
        logger.error(f"Scheduler error: {str(e)}")
        raise


if __name__ == "__main__":
    main()
