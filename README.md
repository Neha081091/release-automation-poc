# Release Automation PoC - DeepIntent

Automated end-to-end release announcement workflow that runs daily at 12 PM with zero manual intervention.

## Overview

This PoC automates the complete release notes workflow:

1. **Step 1**: Fetch release tickets from Jira
2. **Step 2**: Generate formatted release notes and update Google Doc
3. **Step 3**: Send Slack notification to PMOs for review
4. **Step 4**: Track PMO approvals (YES/NO/RELEASE TOMORROW)
5. **Step 5**: Final approval button when all PMOs approve
6. **Step 6**: Auto-post final release notes to Slack release channel

## Project Structure

```
release-automation-poc/
├── main.py                 # Main orchestration script
├── jira_handler.py         # Jira API functions
├── google_docs_handler.py  # Google Docs API functions
├── slack_handler.py        # Slack API functions
├── formatter.py            # Release notes formatting
├── scheduler.py            # Daily trigger scheduling
├── requirements.txt        # Python dependencies
├── .env.example            # Environment configuration template
└── README.md               # This file
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy example config
cp .env.example .env

# Edit with your credentials
nano .env
```

Required credentials:
- **Jira**: Email and API token from https://id.atlassian.com/manage-profile/security/api-tokens
- **Google**: OAuth credentials from Google Cloud Console
- **Slack**: Bot token from Slack App settings

### 3. Set Up Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable Google Docs API
4. Create OAuth 2.0 credentials (Desktop application)
5. Download `credentials.json` to project root

### 4. Run the Automation

```bash
# Run full workflow (with approval)
python main.py

# Run full workflow (skip approval for testing)
python main.py --skip-approval

# Test API connections only
python main.py --test-connections

# Run specific steps
python main.py --step 1    # Jira only
python main.py --step 2    # Jira + Google Doc
python main.py --step 3    # Jira + Google Doc + Slack notification
```

## Configuration

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `JIRA_BASE_URL` | Jira instance URL | `https://deepintent.atlassian.net` |
| `JIRA_EMAIL` | Jira authentication email | `neha.singh@deepintent.com` |
| `JIRA_TOKEN` | Jira API token | `your-api-token` |
| `JIRA_PROJECT_KEY` | Jira project key | `DI` |
| `RELEASE_TICKET_SUMMARY` | Summary of release ticket to find | `Release 2nd February 2026` |
| `GOOGLE_DOC_ID` | Google Doc ID for release notes | `1D7mHR4_kjDLhvmYNlTgtQtBr1Kfen_T1RmLPDmHqBgs` |
| `SLACK_BOT_TOKEN` | Slack Bot OAuth token | `xoxb-...` |
| `SLACK_DM_CHANNEL` | Slack channel for notifications | `D0694CZAXAA` |
| `SCHEDULE_TIME` | Daily automation time | `12:00` |

## Workflow Details

### Step 1: Fetch Jira Tickets

- Connects to Jira using Basic Auth
- Searches for release ticket by summary
- Extracts all linked tickets with details:
  - Issue Key, Summary, Description
  - Issue Type, Epic Name, Labels
  - Fix Version, Priority, Status
  - Story Points

### Step 2: Create Release Notes

**Google Doc Format:**
```
Daily Deployment Summary: 2nd February 2026

------------------TL;DR:------------------

   * Deployments by: DSP PL2, Audiences PL1
   * Major Feature: [Main feature description]
   * Key Enhancement: [Enhancement description]

------------------DSP------------------

DSP PL2: Release 61.0

[Epic Name - BLUE HYPERLINK]

Value Add:
   * [Value-add bullet 1]
   * [Value-add bullet 2]

`General Availability` or `Feature Flag`
```

Features:
- Blue hyperlinks for epics and fix versions
- Bold "Value Add:" headers
- Grouped by Product Line and Epic
- GA/Feature Flag tags for stories

### Step 3: Slack Notification

Sends rich formatted message with:
- Release date
- Google Doc link (clickable)
- TL;DR summary
- Approval buttons (YES/NO/RELEASE TOMORROW)

### Step 4-5: Approval Workflow

- PMOs receive notification with action buttons
- System tracks votes
- When all approve, "Good to Release" button appears
- Final approver confirms release

### Step 6: Post to Release Channel

- Posts complete release notes
- Includes approval metadata
- Timestamps and approver name

## Scheduling

### Local Development

```bash
# Start scheduler (runs daily at configured time)
python scheduler.py --start

# Run immediately (manual trigger)
python scheduler.py --run-once

# Custom time
python scheduler.py --start --time 14:00
```

### Cloud Deployment

**Google Cloud Scheduler:**
```bash
# Generate deployment templates
python scheduler.py --generate-templates
```

**Cron Schedule:** `0 12 * * *` (12:00 PM daily)

## Testing

### Test Individual Components

```bash
# Test Jira handler
python jira_handler.py

# Test formatter
python formatter.py

# Test Google Docs handler
python google_docs_handler.py

# Test Slack handler
python slack_handler.py
```

### Test Full Workflow

```bash
# Test all connections
python main.py --test-connections

# Run with skip approval (full test)
python main.py --skip-approval
```

## Troubleshooting

### Jira Connection Failed
- Verify `JIRA_EMAIL` and `JIRA_TOKEN` in `.env`
- Check API token is valid at https://id.atlassian.com/manage-profile/security/api-tokens
- Ensure user has access to the project

### Google Auth Failed
- Ensure `credentials.json` exists in project root
- Delete `token.pickle` and re-authenticate
- Check Google Docs API is enabled in Cloud Console

### Slack Message Failed
- Verify `SLACK_BOT_TOKEN` is correct
- Ensure bot is invited to the target channel
- Check bot has required permissions (chat:write, chat:write.public)

### Release Ticket Not Found
- Verify `RELEASE_TICKET_SUMMARY` matches exactly
- Check release ticket exists in Jira
- Ensure user has access to view the ticket

## Success Criteria (Friday Presentation)

- [x] Steps 1-2 fully working (Jira -> Google Doc)
- [x] Step 3 working (Slack notification)
- [x] Step 4-5 basic approval flow (MVP)
- [x] Step 6 working (post to Slack)
- [x] Daily trigger configured
- [ ] Live demo showing end-to-end flow
- [x] Documentation for next phase

## Next Steps (Post-PoC)

1. **Webhook Server**: Implement Slack webhook handler for real-time button clicks
2. **Database**: Store approvals in persistent database
3. **Error Recovery**: Add retry logic and failure notifications
4. **Multi-Release**: Support multiple releases in parallel
5. **Analytics**: Track approval times and workflow metrics

## Support

For issues or questions:
- Check the troubleshooting section above
- Review logs for error messages
- Contact the Release Automation team

---

*DeepIntent Release Automation PoC - Built for the Friday Presentation*
