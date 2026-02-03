#!/usr/bin/env python3
"""
AUTOMATED HYBRID WORKFLOW

This script runs on Mac and automates the entire hybrid workflow:
1. Fetch Jira tickets
2. Push to Git
3. Wait for server to process (via GitHub Actions or manual)
4. Pull processed notes
5. Update Google Docs & Slack

For fully automated processing, use with GitHub Actions.

Usage:
    python hybrid_automated.py --export     # Step 1: Export and push
    python hybrid_automated.py --process    # Step 2: Process with Claude (run on server)
    python hybrid_automated.py --update     # Step 3: Pull and update docs
    python hybrid_automated.py --full       # Full workflow (requires GitHub Actions)
"""

import json
import os
import sys
import subprocess
import time
import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def run_command(cmd, description=""):
    """Run a shell command and return output."""
    print(f"  → {description or cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    Error: {result.stderr}")
    return result.returncode == 0, result.stdout


def step1_export_jira():
    """Export Jira tickets and commit to git."""
    print("\n" + "=" * 60)
    print("  STEP 1: Export Jira Tickets")
    print("=" * 60)

    # Import and run export
    from hybrid_step1_export_jira import export_jira_tickets
    output_file = export_jira_tickets()

    if not output_file:
        print("[Step 1] FAILED: Could not export tickets")
        return False

    # Commit and push
    print("\n[Step 1] Committing to Git...")
    run_command("git add tickets_export.json", "Adding tickets_export.json")
    run_command(
        f'git commit -m "Auto-export Jira tickets {datetime.now().isoformat()}"',
        "Committing"
    )
    success, _ = run_command("git push", "Pushing to remote")

    if success:
        print("[Step 1] ✅ Tickets exported and pushed to Git")
    else:
        print("[Step 1] ⚠️ Push failed - may need to pull first")

    return True


def step2_process_claude():
    """Process tickets with Claude API."""
    print("\n" + "=" * 60)
    print("  STEP 2: Process with Claude API")
    print("=" * 60)

    # Pull latest
    print("[Step 2] Pulling latest from Git...")
    run_command("git pull", "Pulling")

    # Check if tickets file exists
    if not os.path.exists("tickets_export.json"):
        print("[Step 2] ERROR: tickets_export.json not found")
        print("[Step 2] Run --export first on Mac")
        return False

    # Import and run processing
    from hybrid_step2_process_claude import process_tickets_with_claude
    output_file = process_tickets_with_claude()

    if not output_file:
        print("[Step 2] FAILED: Could not process tickets")
        return False

    # Commit and push
    print("\n[Step 2] Committing to Git...")
    run_command("git add processed_notes.json", "Adding processed_notes.json")
    run_command(
        f'git commit -m "Auto-process with Claude {datetime.now().isoformat()}"',
        "Committing"
    )
    success, _ = run_command("git push", "Pushing to remote")

    if success:
        print("[Step 2] ✅ Processed notes pushed to Git")

    return True


def step3_update_docs():
    """Pull processed notes and update Google Docs & Slack."""
    print("\n" + "=" * 60)
    print("  STEP 3: Update Google Docs & Slack")
    print("=" * 60)

    # Pull latest
    print("[Step 3] Pulling latest from Git...")
    run_command("git pull", "Pulling")

    # Check if processed file exists
    if not os.path.exists("processed_notes.json"):
        print("[Step 3] ERROR: processed_notes.json not found")
        print("[Step 3] Run --process first on server")
        return False

    # Import and run update
    from hybrid_step3_update_docs import main as update_main
    update_main()

    return True


def full_workflow():
    """
    Full automated workflow.
    Requires GitHub Actions to process Step 2 automatically.
    """
    print("\n" + "=" * 60)
    print("  FULL AUTOMATED WORKFLOW")
    print("=" * 60)

    # Step 1: Export
    if not step1_export_jira():
        return False

    print("\n[Workflow] Waiting for server to process...")
    print("[Workflow] (GitHub Actions should trigger automatically)")
    print("[Workflow] Waiting 60 seconds...")

    # Wait for GitHub Actions to process
    time.sleep(60)

    # Step 3: Pull and update
    print("\n[Workflow] Pulling processed notes...")
    run_command("git pull", "Pulling")

    # Check if processed
    if not os.path.exists("processed_notes.json"):
        print("[Workflow] ⚠️ processed_notes.json not ready yet")
        print("[Workflow] Run manually: python hybrid_automated.py --update")
        return False

    return step3_update_docs()


def main():
    parser = argparse.ArgumentParser(description='Automated Hybrid Workflow')
    parser.add_argument('--export', action='store_true', help='Step 1: Export Jira tickets and push')
    parser.add_argument('--process', action='store_true', help='Step 2: Process with Claude API')
    parser.add_argument('--update', action='store_true', help='Step 3: Pull and update docs')
    parser.add_argument('--full', action='store_true', help='Full workflow (requires GitHub Actions)')

    args = parser.parse_args()

    if args.export:
        step1_export_jira()
    elif args.process:
        step2_process_claude()
    elif args.update:
        step3_update_docs()
    elif args.full:
        full_workflow()
    else:
        print("""
Usage:
    python hybrid_automated.py --export     # Mac: Export Jira tickets
    python hybrid_automated.py --process    # Server: Process with Claude API
    python hybrid_automated.py --update     # Mac: Update Google Docs & Slack

Workflow:
    1. Mac:    python hybrid_automated.py --export
    2. Server: python hybrid_automated.py --process
    3. Mac:    python hybrid_automated.py --update

Or with GitHub Actions for Step 2:
    Mac:       python hybrid_automated.py --full
        """)


if __name__ == "__main__":
    main()
