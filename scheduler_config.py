"""
Scheduler Configuration for Release Automation PoC

This module contains all configuration settings for the daily scheduler.
All values can be overridden via environment variables.
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class SchedulerConfig:
    """Configuration settings for the scheduler."""

    # Timezone settings
    TIMEZONE = os.getenv('SCHEDULER_TIMEZONE', 'Asia/Kolkata')  # IST

    # Schedule time (24-hour format)
    SCHEDULE_HOUR = int(os.getenv('SCHEDULE_HOUR', '12'))
    SCHEDULE_MINUTE = int(os.getenv('SCHEDULE_MINUTE', '0'))

    # Logging configuration
    LOG_DIRECTORY = os.getenv('LOG_DIRECTORY', 'logs')
    LOG_FILE = os.getenv('LOG_FILE', 'release_automation.log')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'

    # Metrics file for tracking runs
    METRICS_FILE = os.getenv('METRICS_FILE', 'metrics.json')

    # Retry configuration
    MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))
    RETRY_DELAY_SECONDS = int(os.getenv('RETRY_DELAY_SECONDS', '60'))

    # Timeout for main.py execution (in seconds)
    EXECUTION_TIMEOUT = int(os.getenv('EXECUTION_TIMEOUT', '600'))  # 10 minutes

    # Slack configuration (for scheduler notifications)
    SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL', '')
    SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN', '')
    SLACK_CHANNEL_ID = os.getenv('SLACK_DM_CHANNEL', '')

    # Command to execute
    RUN_MODE = os.getenv('RUN_MODE', 'direct')  # direct | local_socket
    if RUN_MODE == 'local_socket':
        COMMAND = ['python', 'local_full_run.py']
    else:
        COMMAND = ['python', 'main.py', '--skip-approval']

    @classmethod
    def get_log_path(cls) -> str:
        """Get the full path to the log file."""
        return os.path.join(cls.LOG_DIRECTORY, cls.LOG_FILE)

    @classmethod
    def get_metrics_path(cls) -> str:
        """Get the full path to the metrics file."""
        return os.path.join(cls.LOG_DIRECTORY, cls.METRICS_FILE)

    @classmethod
    def ensure_log_directory(cls) -> None:
        """Create the log directory if it doesn't exist."""
        if not os.path.exists(cls.LOG_DIRECTORY):
            os.makedirs(cls.LOG_DIRECTORY)
            print(f"[Config] Created log directory: {cls.LOG_DIRECTORY}")

    @classmethod
    def print_config(cls) -> None:
        """Print current configuration for debugging."""
        print("\n" + "=" * 50)
        print("  SCHEDULER CONFIGURATION")
        print("=" * 50)
        print(f"  Timezone: {cls.TIMEZONE}")
        print(f"  Schedule: {cls.SCHEDULE_HOUR:02d}:{cls.SCHEDULE_MINUTE:02d}")
        print(f"  Log file: {cls.get_log_path()}")
        print(f"  Max retries: {cls.MAX_RETRIES}")
        print(f"  Timeout: {cls.EXECUTION_TIMEOUT}s")
        print(f"  Slack notifications: {'Enabled' if cls.SLACK_WEBHOOK_URL else 'Disabled'}")
        print("=" * 50 + "\n")


# Create a singleton instance
config = SchedulerConfig()
