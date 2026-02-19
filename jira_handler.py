"""
Jira API Handler for Release Automation PoC

This module handles all Jira-related operations:
- Authenticating with Jira using Basic Auth
- Finding release tickets by summary
- Extracting linked tickets
- Fetching ticket details
"""

import os
import requests
from requests.auth import HTTPBasicAuth
from typing import Dict, List, Optional, Any
import time


class JiraHandler:
    """Handler for Jira API operations."""

    def __init__(self, base_url: str = None, email: str = None, token: str = None):
        """
        Initialize Jira handler with authentication credentials.

        Args:
            base_url: Jira instance URL (e.g., https://deepintent.atlassian.net)
            email: User email for authentication
            token: API token for authentication
        """
        self.base_url = base_url or os.getenv('JIRA_BASE_URL') or os.getenv('JIRA_URL', 'https://deepintent.atlassian.net')
        self.email = email or os.getenv('JIRA_EMAIL')
        self.token = token or os.getenv('JIRA_TOKEN') or os.getenv('JIRA_API_TOKEN')

        if not self.email or not self.token:
            raise ValueError("JIRA_EMAIL and JIRA_TOKEN must be provided")

        self.auth = HTTPBasicAuth(self.email, self.token)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        print(f"[Jira] Initialized handler for {self.base_url}")

    def _make_request(self, method: str, endpoint: str, params: dict = None,
                      json_data: dict = None, retries: int = 3) -> Optional[Dict]:
        """
        Make an API request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            json_data: JSON body for POST requests
            retries: Number of retry attempts

        Returns:
            JSON response or None on failure
        """
        url = f"{self.base_url}/rest/api/3/{endpoint}"

        for attempt in range(retries):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    auth=self.auth,
                    params=params,
                    json=json_data,
                    timeout=30
                )

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    print(f"[Jira] Resource not found: {endpoint}")
                    return None
                elif response.status_code == 401:
                    print("[Jira] Authentication failed. Check your credentials.")
                    return None
                else:
                    print(f"[Jira] Request failed with status {response.status_code}: {response.text}")

            except requests.exceptions.Timeout:
                print(f"[Jira] Request timeout (attempt {attempt + 1}/{retries})")
            except requests.exceptions.RequestException as e:
                print(f"[Jira] Request error (attempt {attempt + 1}/{retries}): {e}")

            if attempt < retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                print(f"[Jira] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)

        return None

    def find_release_ticket(self, summary: str, project: str = "DI") -> Optional[Dict]:
        """
        Find a release ticket by its summary.

        Args:
            summary: The summary/title of the release ticket
            project: Jira project key (default: DI)

        Returns:
            Ticket data or None if not found
        """
        print(f"[Jira] Searching for release ticket: '{summary}' in project {project}")

        # Use JQL to search for the ticket (using new search/jql endpoint)
        jql = f'project = {project} AND summary ~ "{summary}"'
        json_data = {
            "jql": jql,
            "fields": ["key", "summary", "description", "issuetype", "status", "priority", "fixVersions", "labels", "customfield_10014"],
            "maxResults": 10
        }

        result = self._make_request("POST", "search/jql", json_data=json_data)

        if result and result.get("issues"):
            issues = result["issues"]
            # Find exact or closest match
            for issue in issues:
                if issue["fields"]["summary"].strip() == summary.strip():
                    print(f"[Jira] Found release ticket: {issue['key']}")
                    return issue

            # Return first match if no exact match
            print(f"[Jira] Found closest match: {issues[0]['key']}")
            return issues[0]

        print(f"[Jira] No release ticket found with summary: '{summary}'")
        return None

    def get_linked_tickets(self, issue_key: str) -> List[Dict]:
        """
        Get all tickets linked to the specified issue.
        Falls back to searching by Fix Version if no direct links found.

        Args:
            issue_key: The Jira issue key (e.g., DI-12345)

        Returns:
            List of linked ticket data
        """
        print(f"[Jira] Fetching linked tickets for {issue_key}")

        # Get issue with links and fix versions
        endpoint = f"issue/{issue_key}"
        params = {
            "fields": "issuelinks,fixVersions",
            "expand": "names"
        }

        result = self._make_request("GET", endpoint, params=params)

        if not result:
            print(f"[Jira] Could not fetch issue {issue_key}")
            return []

        links = result.get("fields", {}).get("issuelinks", [])
        linked_keys = []

        for link in links:
            # Links can have inwardIssue or outwardIssue
            if "inwardIssue" in link:
                linked_keys.append(link["inwardIssue"]["key"])
            if "outwardIssue" in link:
                linked_keys.append(link["outwardIssue"]["key"])

        print(f"[Jira] Found {len(linked_keys)} directly linked tickets")

        # If no linked tickets, try searching by Fix Version(s)
        if not linked_keys:
            fix_versions = result.get("fields", {}).get("fixVersions", [])
            if fix_versions:
                # Get all fix version names, excluding Hotfix versions
                fix_version_names = [
                    fv.get("name") for fv in fix_versions
                    if fv.get("name") and "hotfix" not in fv.get("name", "").lower()
                ]
                excluded = [fv.get("name") for fv in fix_versions if fv.get("name") and "hotfix" in fv.get("name", "").lower()]
                if excluded:
                    print(f"[Jira] Excluding Hotfix versions: {excluded}")
                print(f"[Jira] No links found. Searching by {len(fix_version_names)} Fix Versions: {fix_version_names}")
                return self.get_tickets_by_fix_versions(fix_version_names, issue_key)
            else:
                print("[Jira] No Fix Version found on release ticket. Trying to search by date...")
                # Try to extract date from ticket summary and search
                return self.get_tickets_by_release_date(issue_key)

        # Fetch full details for each linked ticket
        linked_tickets = []
        for key in linked_keys:
            ticket = self.get_ticket_details(key)
            if ticket:
                linked_tickets.append(ticket)

        return linked_tickets

    def get_fix_versions_for_ticket(self, issue_key: str) -> List[str]:
        """
        Fetch the current fix version names from a Jira ticket.

        Used by refresh to detect newly added fix versions on the release ticket.

        Args:
            issue_key: The Jira issue key (e.g., DI-12345)

        Returns:
            List of fix version name strings (excluding Hotfix versions)
        """
        endpoint = f"issue/{issue_key}"
        params = {"fields": "fixVersions"}

        result = self._make_request("GET", endpoint, params=params)
        if not result:
            return []

        fix_versions = result.get("fields", {}).get("fixVersions", [])
        names = [
            fv.get("name") for fv in fix_versions
            if fv.get("name") and "hotfix" not in fv.get("name", "").lower()
        ]
        return names

    def get_tickets_by_fix_versions(self, fix_versions: List[str], exclude_key: str = None) -> List[Dict]:
        """
        Get all tickets with any of the specified Fix Versions.

        Args:
            fix_versions: List of Fix Version names
            exclude_key: Issue key to exclude (the release ticket itself)

        Returns:
            List of ticket data
        """
        if not fix_versions:
            return []

        print(f"[Jira] Searching for tickets with Fix Versions: {fix_versions}")

        # Build JQL with OR for multiple fix versions
        fix_version_conditions = [f'fixVersion = "{fv}"' for fv in fix_versions]
        jql = f'({" OR ".join(fix_version_conditions)})'
        if exclude_key:
            jql += f' AND key != {exclude_key}'

        json_data = {
            "jql": jql,
            "fields": ["key", "summary"],
            "maxResults": 200
        }

        result = self._make_request("POST", "search/jql", json_data=json_data)

        if not result or not result.get("issues"):
            print(f"[Jira] No tickets found with Fix Versions: {fix_versions}")
            return []

        issues = result.get("issues", [])
        print(f"[Jira] Found {len(issues)} tickets across all Fix Versions")

        # Fetch full details for each ticket
        tickets = []
        seen_keys = set()
        for issue in issues:
            key = issue["key"]
            if key not in seen_keys:
                seen_keys.add(key)
                ticket = self.get_ticket_details(key)
                if ticket:
                    tickets.append(ticket)

        print(f"[Jira] Fetched details for {len(tickets)} unique tickets")
        return tickets

    def get_tickets_by_fix_version(self, fix_version: str, exclude_key: str = None) -> List[Dict]:
        """
        Get all tickets with a specific Fix Version.

        Args:
            fix_version: The Fix Version name (e.g., "Release 61.0")
            exclude_key: Issue key to exclude (the release ticket itself)

        Returns:
            List of ticket data
        """
        return self.get_tickets_by_fix_versions([fix_version], exclude_key)

    def get_tickets_by_release_date(self, release_key: str) -> List[Dict]:
        """
        Get tickets that might be part of today's release by searching recent Done tickets.

        Args:
            release_key: The release ticket key to exclude

        Returns:
            List of ticket data
        """
        print(f"[Jira] Searching for recent completed tickets...")

        # Search for recently resolved tickets in the project
        jql = f'project = DI AND status = Done AND resolved >= -7d AND key != {release_key}'

        json_data = {
            "jql": jql,
            "fields": ["key", "summary"],
            "maxResults": 50
        }

        result = self._make_request("POST", "search/jql", json_data=json_data)

        if not result or not result.get("issues"):
            print("[Jira] No recent completed tickets found")
            return []

        issues = result.get("issues", [])
        print(f"[Jira] Found {len(issues)} recently completed tickets")

        # Fetch full details for each ticket
        tickets = []
        for issue in issues:
            ticket = self.get_ticket_details(issue["key"])
            if ticket:
                tickets.append(ticket)

        return tickets

    def get_ticket_details(self, issue_key: str) -> Optional[Dict]:
        """
        Get full details for a specific ticket.

        Args:
            issue_key: The Jira issue key (e.g., DI-100)

        Returns:
            Ticket data with all relevant fields
        """
        print(f"[Jira] Fetching details for {issue_key}")

        endpoint = f"issue/{issue_key}"
        params = {
            "fields": "key,summary,description,issuetype,status,priority,fixVersions,labels,"
                      "customfield_10014,customfield_10008,parent,components,assignee,reporter,"
                      "customfield_10016",  # Story points field (may vary by instance)
            "expand": "names"
        }

        result = self._make_request("GET", endpoint, params=params)

        if not result:
            return None

        # Parse the ticket data
        fields = result.get("fields", {})

        # Extract epic information
        epic_key = None
        epic_name = None
        epic_url = None

        # Try different ways to get epic info
        # Method 1: customfield_10014 (Epic Link in some Jira instances)
        if fields.get("customfield_10014"):
            epic_key = fields.get("customfield_10014")
        # Method 2: parent field (for next-gen/team-managed projects or sub-tasks)
        elif fields.get("parent"):
            parent = fields.get("parent", {})
            parent_type = (parent.get("fields", {}).get("issuetype", {}).get("name") or "").lower()
            if "epic" in parent_type:
                # Parent is an Epic — use it directly
                epic_key = parent.get("key")
                epic_name = parent.get("fields", {}).get("summary")
            elif parent_type not in ("sub-task", "subtask"):
                # Parent is a Story/Task/etc. — use parent summary as group name
                # and try to resolve the parent's own epic link
                epic_name = parent.get("fields", {}).get("summary")
                epic_key = parent.get("key")
        # Method 3: customfield_10008 (Epic Name in some instances)
        if fields.get("customfield_10008"):
            epic_name = fields.get("customfield_10008")

        # If we have an epic key but no name, fetch it
        if epic_key and not epic_name:
            epic_data = self._make_request("GET", f"issue/{epic_key}",
                                          params={"fields": "summary"})
            if epic_data:
                epic_name = epic_data.get("fields", {}).get("summary")

        if epic_key:
            epic_url = f"{self.base_url}/browse/{epic_key}"

        # Extract fix version
        fix_versions = fields.get("fixVersions", [])
        fix_version = fix_versions[0].get("name") if fix_versions else None
        fix_version_id = fix_versions[0].get("id") if fix_versions else None

        # Build fix version URL
        fix_version_url = None
        if fix_version_id:
            # Get project key from issue key
            project_key = issue_key.split("-")[0]
            fix_version_url = f"{self.base_url}/projects/{project_key}/versions/{fix_version_id}/tab/release-report-all-issues"

        # Extract labels for GA/Feature Flag detection
        labels = fields.get("labels", [])

        # Determine GA or Feature Flag
        release_type = None
        for label in labels:
            label_lower = label.lower()
            if "ga" in label_lower or "general" in label_lower or "availability" in label_lower:
                release_type = "General Availability"
            elif "feature" in label_lower and "flag" in label_lower:
                release_type = "Feature Flag"
            elif "featureflag" in label_lower:
                release_type = "Feature Flag"

        # Extract components (for Product Line grouping)
        components = [c.get("name") for c in fields.get("components", [])]

        # Extract story points
        story_points = fields.get("customfield_10016")  # Common field for story points

        ticket_data = {
            "key": result.get("key"),
            "url": f"{self.base_url}/browse/{result.get('key')}",
            "summary": fields.get("summary"),
            "description": self._parse_description(fields.get("description")),
            "issue_type": fields.get("issuetype", {}).get("name"),
            "status": fields.get("status", {}).get("name"),
            "priority": fields.get("priority", {}).get("name"),
            "fix_version": fix_version,
            "fix_version_url": fix_version_url,
            "labels": labels,
            "release_type": release_type,
            "epic_key": epic_key,
            "epic_name": epic_name,
            "epic_url": epic_url,
            "components": components,
            "story_points": story_points,
            "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
        }

        return ticket_data

    def _parse_description(self, description: Any) -> str:
        """
        Parse Jira description (may be Atlassian Document Format or plain text).

        Args:
            description: Raw description from Jira API

        Returns:
            Plain text description
        """
        if not description:
            return ""

        if isinstance(description, str):
            return description

        # Handle Atlassian Document Format (ADF)
        if isinstance(description, dict) and description.get("type") == "doc":
            return self._extract_text_from_adf(description)

        return str(description)

    def _extract_text_from_adf(self, node: Dict) -> str:
        """
        Recursively extract text from Atlassian Document Format.

        Args:
            node: ADF node

        Returns:
            Extracted plain text
        """
        text_parts = []

        if node.get("type") == "text":
            return node.get("text", "")

        for child in node.get("content", []):
            text_parts.append(self._extract_text_from_adf(child))

        return " ".join(text_parts)

    def get_epic_details(self, epic_key: str) -> Optional[Dict]:
        """
        Get details for an epic.

        Args:
            epic_key: The epic's Jira key

        Returns:
            Epic data
        """
        return self.get_ticket_details(epic_key)

    def test_connection(self) -> bool:
        """
        Test the Jira connection.

        Returns:
            True if connection is successful, False otherwise
        """
        print("[Jira] Testing connection...")
        result = self._make_request("GET", "myself")

        if result:
            print(f"[Jira] Connected as: {result.get('displayName', 'Unknown')}")
            return True

        print("[Jira] Connection test failed")
        return False


def main():
    """Test the Jira handler."""
    from dotenv import load_dotenv
    load_dotenv()

    try:
        handler = JiraHandler()

        # Test connection
        if not handler.test_connection():
            print("Failed to connect to Jira")
            return

        # Find release ticket
        release_summary = os.getenv('RELEASE_TICKET_SUMMARY', 'Release 2nd February 2026')
        release_ticket = handler.find_release_ticket(release_summary)

        if release_ticket:
            print(f"\nRelease Ticket: {release_ticket['key']}")
            print(f"Summary: {release_ticket['fields']['summary']}")

            # Get linked tickets
            linked_tickets = handler.get_linked_tickets(release_ticket['key'])

            print(f"\nFound {len(linked_tickets)} linked tickets:")
            for ticket in linked_tickets:
                print(f"  - {ticket['key']}: {ticket['summary']}")
                print(f"    Type: {ticket['issue_type']}, Epic: {ticket.get('epic_name', 'N/A')}")
        else:
            print("Release ticket not found")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
