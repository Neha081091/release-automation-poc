"""
Scheduler for Release Automation PoC

This module handles scheduling of the daily release automation:
- Local development scheduling using the 'schedule' library
- Cloud deployment options (Google Cloud Scheduler, AWS CloudWatch)
- Manual trigger support
"""

import os
import sys
import time
import signal
from datetime import datetime
from typing import Callable, Optional

try:
    import schedule
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False
    print("[Scheduler] 'schedule' library not installed. Local scheduling unavailable.")


class ReleaseScheduler:
    """Scheduler for running release automation at specified times."""

    def __init__(self, job_function: Callable = None):
        """
        Initialize the scheduler.

        Args:
            job_function: The function to run on schedule
        """
        self.job_function = job_function
        self.running = False
        self.schedule_time = os.getenv('SCHEDULE_TIME', '12:00')  # Default 12 PM

        print(f"[Scheduler] Initialized with schedule time: {self.schedule_time}")

    def set_job(self, job_function: Callable):
        """
        Set or update the job function.

        Args:
            job_function: The function to run on schedule
        """
        self.job_function = job_function
        print("[Scheduler] Job function updated")

    def _job_wrapper(self):
        """Wrapper for the scheduled job with error handling."""
        print(f"\n{'='*60}")
        print(f"[Scheduler] Starting scheduled job at {datetime.now()}")
        print(f"{'='*60}\n")

        try:
            if self.job_function:
                self.job_function()
                print(f"\n[Scheduler] Job completed successfully at {datetime.now()}")
            else:
                print("[Scheduler] No job function configured")

        except Exception as e:
            print(f"\n[Scheduler] Job failed with error: {e}")
            import traceback
            traceback.print_exc()

        print(f"{'='*60}\n")

    def schedule_daily(self, time_str: str = None):
        """
        Schedule the job to run daily at a specific time.

        Args:
            time_str: Time string in HH:MM format (24-hour)
        """
        if not SCHEDULE_AVAILABLE:
            print("[Scheduler] Cannot schedule: 'schedule' library not available")
            return False

        time_str = time_str or self.schedule_time

        try:
            schedule.every().day.at(time_str).do(self._job_wrapper)
            print(f"[Scheduler] Scheduled daily job at {time_str}")
            return True

        except Exception as e:
            print(f"[Scheduler] Failed to schedule job: {e}")
            return False

    def run_once(self):
        """Run the job immediately (manual trigger)."""
        print("[Scheduler] Running job manually...")
        self._job_wrapper()

    def start(self):
        """
        Start the scheduler loop.

        This runs indefinitely until stopped.
        """
        if not SCHEDULE_AVAILABLE:
            print("[Scheduler] Cannot start: 'schedule' library not available")
            return

        self.running = True
        print(f"[Scheduler] Starting scheduler loop...")
        print(f"[Scheduler] Press Ctrl+C to stop")

        # Set up signal handler for graceful shutdown
        def signal_handler(sig, frame):
            print("\n[Scheduler] Received shutdown signal")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Show next run time
        next_run = schedule.next_run()
        if next_run:
            print(f"[Scheduler] Next scheduled run: {next_run}")

        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute

    def stop(self):
        """Stop the scheduler loop."""
        self.running = False
        print("[Scheduler] Scheduler stopped")

    @staticmethod
    def get_cloud_scheduler_config():
        """
        Get configuration for Google Cloud Scheduler.

        Returns:
            Dictionary with Cloud Scheduler configuration
        """
        return {
            "name": "release-automation-daily",
            "description": "Daily release automation trigger at 12 PM",
            "schedule": "0 12 * * *",  # Cron: 12:00 PM every day
            "time_zone": "America/New_York",  # Adjust as needed
            "http_target": {
                "uri": os.getenv('CLOUD_FUNCTION_URL', 'https://your-function-url'),
                "http_method": "POST",
                "headers": {
                    "Content-Type": "application/json"
                },
                "body": {
                    "action": "run_release_automation",
                    "date": "auto"  # Will use current date
                }
            }
        }

    @staticmethod
    def get_aws_cloudwatch_config():
        """
        Get configuration for AWS CloudWatch Events.

        Returns:
            Dictionary with CloudWatch Events configuration
        """
        return {
            "Name": "ReleaseAutomationDaily",
            "Description": "Daily release automation trigger at 12 PM",
            "ScheduleExpression": "cron(0 12 * * ? *)",  # 12:00 PM UTC every day
            "State": "ENABLED",
            "Targets": [
                {
                    "Id": "ReleaseAutomationLambda",
                    "Arn": os.getenv('LAMBDA_FUNCTION_ARN', 'arn:aws:lambda:region:account:function:name'),
                    "Input": '{"action": "run_release_automation", "date": "auto"}'
                }
            ]
        }

    @staticmethod
    def generate_cloud_function_template():
        """
        Generate a template for Google Cloud Function deployment.

        Returns:
            Python code string for Cloud Function
        """
        return '''
# main.py for Google Cloud Function
import functions_framework
from datetime import datetime
import os

# Set environment variables in Cloud Function configuration
# JIRA_EMAIL, JIRA_TOKEN, SLACK_BOT_TOKEN, GOOGLE_DOC_ID, etc.

@functions_framework.http
def release_automation_trigger(request):
    """HTTP Cloud Function for release automation.

    Args:
        request: The request object

    Returns:
        Response string
    """
    try:
        # Import the main module
        from main import run_release_automation

        # Get date from request or use today
        request_json = request.get_json(silent=True)
        if request_json and 'date' in request_json:
            release_date = request_json['date']
        else:
            release_date = None  # Will use today's date

        # Run the automation
        result = run_release_automation(release_date)

        return {
            "status": "success",
            "message": "Release automation completed",
            "result": result
        }, 200

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }, 500


# For scheduled triggers via Cloud Scheduler
@functions_framework.cloud_event
def release_automation_scheduled(cloud_event):
    """Cloud Event Function for scheduled release automation."""
    try:
        from main import run_release_automation
        result = run_release_automation()
        print(f"Scheduled automation completed: {result}")

    except Exception as e:
        print(f"Scheduled automation failed: {e}")
        raise
'''

    @staticmethod
    def generate_lambda_template():
        """
        Generate a template for AWS Lambda deployment.

        Returns:
            Python code string for Lambda function
        """
        return '''
# lambda_function.py for AWS Lambda
import json
import os
from datetime import datetime

# Set environment variables in Lambda configuration
# JIRA_EMAIL, JIRA_TOKEN, SLACK_BOT_TOKEN, GOOGLE_DOC_ID, etc.

def lambda_handler(event, context):
    """AWS Lambda handler for release automation.

    Args:
        event: Lambda event data
        context: Lambda context

    Returns:
        Response dictionary
    """
    try:
        # Import the main module
        from main import run_release_automation

        # Get date from event or use today
        release_date = event.get('date') if event.get('date') != 'auto' else None

        # Run the automation
        result = run_release_automation(release_date)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                "message": "Release automation completed",
                "result": result
            })
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "status": "error",
                "message": str(e)
            })
        }
'''


def create_deployment_files():
    """Create deployment configuration files for cloud platforms."""

    # Google Cloud Function requirements
    cloud_function_requirements = """
# requirements.txt for Google Cloud Function
functions-framework==3.*
requests>=2.28.0
google-auth>=2.16.0
google-auth-oauthlib>=1.0.0
google-auth-httplib2>=0.1.0
google-api-python-client>=2.80.0
slack-sdk>=3.19.0
python-dotenv>=1.0.0
python-dateutil>=2.8.2
"""

    # Dockerfile for containerized deployment
    dockerfile = """
# Dockerfile for Release Automation PoC
FROM python:3.9-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py .

# Set environment variables (override at runtime)
ENV SCHEDULE_TIME=12:00

# Run the scheduler
CMD ["python", "scheduler.py", "--start"]
"""

    # docker-compose for local development
    docker_compose = """
# docker-compose.yml for local development
version: '3.8'

services:
  release-automation:
    build: .
    environment:
      - JIRA_EMAIL=${JIRA_EMAIL}
      - JIRA_TOKEN=${JIRA_TOKEN}
      - SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}
      - SLACK_DM_CHANNEL=${SLACK_DM_CHANNEL}
      - GOOGLE_DOC_ID=${GOOGLE_DOC_ID}
      - SCHEDULE_TIME=${SCHEDULE_TIME:-12:00}
    volumes:
      - ./credentials.json:/app/credentials.json:ro
      - ./token.pickle:/app/token.pickle
    restart: unless-stopped
"""

    print("Cloud deployment templates generated")
    return {
        "cloud_function_requirements": cloud_function_requirements,
        "dockerfile": dockerfile,
        "docker_compose": docker_compose
    }


def main():
    """Test the scheduler or run in different modes."""
    import argparse

    parser = argparse.ArgumentParser(description='Release Automation Scheduler')
    parser.add_argument('--start', action='store_true', help='Start the scheduler loop')
    parser.add_argument('--run-once', action='store_true', help='Run the job once immediately')
    parser.add_argument('--time', type=str, default='12:00', help='Schedule time (HH:MM)')
    parser.add_argument('--generate-templates', action='store_true',
                       help='Generate cloud deployment templates')

    args = parser.parse_args()

    if args.generate_templates:
        templates = create_deployment_files()
        for name, content in templates.items():
            print(f"\n=== {name} ===")
            print(content)
        return

    # Import main function
    try:
        from main import run_release_automation
        job_function = run_release_automation
        print("[Scheduler] Loaded job function from main.py")
    except ImportError:
        def job_function():
            print("[Scheduler] Demo job running...")
            print("[Scheduler] (main.py not available, using demo function)")
        print("[Scheduler] Using demo job function")

    scheduler = ReleaseScheduler(job_function)

    if args.run_once:
        scheduler.run_once()
    elif args.start:
        scheduler.schedule_daily(args.time)
        scheduler.start()
    else:
        print("[Scheduler] Use --start to begin scheduling or --run-once for immediate execution")
        print(f"[Scheduler] Configured time: {args.time}")

        # Show cloud configs
        print("\n[Scheduler] Cloud Scheduler Config:")
        print(scheduler.get_cloud_scheduler_config())


if __name__ == "__main__":
    main()
