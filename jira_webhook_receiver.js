/**
 * Jira Webhook Receiver - Google Apps Script
 *
 * SETUP INSTRUCTIONS:
 * 1. Go to https://script.google.com
 * 2. Create a new project
 * 3. Paste this entire code
 * 4. Click Deploy → New deployment
 * 5. Select type: "Web app"
 * 6. Set "Execute as": Me
 * 7. Set "Who has access": Anyone
 * 8. Click Deploy
 * 9. Copy the URL - that's your Jira webhook URL!
 *
 * The URL will look like:
 * https://script.google.com/macros/s/AKfycbx...YOUR_UNIQUE_ID.../exec
 */

// Handle POST requests from Jira webhooks
function doPost(e) {
  try {
    // Parse the incoming Jira webhook payload
    var payload = JSON.parse(e.postData.contents);

    // Log the webhook event
    console.log("Received Jira webhook:", JSON.stringify(payload));

    // Extract version event details
    var webhookEvent = payload.webhookEvent || "unknown";
    var version = payload.version || {};
    var versionName = version.name || "unknown";
    var projectKey = version.projectId || payload.project?.key || "unknown";

    // Log to a spreadsheet (optional - for tracking)
    logToSheet(webhookEvent, versionName, projectKey, payload);

    // Handle specific version events
    if (webhookEvent === "jira:version_created") {
      handleVersionCreated(version, payload);
    } else if (webhookEvent === "jira:version_released") {
      handleVersionReleased(version, payload);
    }

    // Return success response
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "success",
        message: "Webhook received",
        event: webhookEvent
      }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    console.error("Error processing webhook:", error);
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "error",
        message: error.toString()
      }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Handle GET requests (for testing the URL)
function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({
      status: "ok",
      message: "Jira Webhook Receiver is running",
      timestamp: new Date().toISOString()
    }))
    .setMimeType(ContentService.MimeType.JSON);
}

// Handle version created event
function handleVersionCreated(version, payload) {
  console.log("New version created:", version.name);

  // TODO: Add your automation here
  // Examples:
  // - Send Slack notification
  // - Trigger GitHub Actions
  // - Update Google Sheet

  // Optional: Send to Slack
  // sendSlackNotification("New Jira version created: " + version.name);
}

// Handle version released event
function handleVersionReleased(version, payload) {
  console.log("Version released:", version.name);

  // TODO: Add your automation here
  // This is where you trigger your release notes workflow

  // Optional: Send to Slack
  // sendSlackNotification("Jira version released: " + version.name);
}

// Log webhook events to a Google Sheet (optional)
function logToSheet(event, versionName, projectKey, payload) {
  try {
    // Create or get the log spreadsheet
    var spreadsheetId = PropertiesService.getScriptProperties().getProperty('LOG_SPREADSHEET_ID');

    if (!spreadsheetId) {
      // Create a new spreadsheet if none exists
      var ss = SpreadsheetApp.create("Jira Webhook Logs");
      spreadsheetId = ss.getId();
      PropertiesService.getScriptProperties().setProperty('LOG_SPREADSHEET_ID', spreadsheetId);

      // Add headers
      var sheet = ss.getActiveSheet();
      sheet.appendRow(["Timestamp", "Event", "Version", "Project", "Raw Payload"]);
    }

    var ss = SpreadsheetApp.openById(spreadsheetId);
    var sheet = ss.getActiveSheet();

    // Append the log entry
    sheet.appendRow([
      new Date().toISOString(),
      event,
      versionName,
      projectKey,
      JSON.stringify(payload).substring(0, 50000) // Truncate if too long
    ]);

  } catch (error) {
    console.error("Error logging to sheet:", error);
  }
}

// Optional: Send notification to Slack
function sendSlackNotification(message) {
  var slackWebhookUrl = PropertiesService.getScriptProperties().getProperty('SLACK_WEBHOOK_URL');

  if (!slackWebhookUrl) {
    console.log("Slack webhook URL not configured");
    return;
  }

  var payload = {
    text: message
  };

  UrlFetchApp.fetch(slackWebhookUrl, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload)
  });
}
