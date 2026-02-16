/**
 * Google Sheets Approval System for Release Notes
 *
 * SETUP:
 * 1. Open Google Sheet
 * 2. Extensions → Apps Script
 * 3. Paste this entire code
 * 4. Update SLACK_WEBHOOK_URL below
 * 5. Save and run onOpen() once to authorize
 *
 * USAGE:
 * - Click cells in Approve/Reject/Tomorrow columns to vote
 * - Use menu: Release Approval → Good to Announce
 */

// ============================================
// CONFIGURATION - UPDATE THESE VALUES
// ============================================
const SLACK_WEBHOOK_URL = "YOUR_SLACK_WEBHOOK_URL_HERE";
const SLACK_ANNOUNCE_CHANNEL = "#release-announcements";
const SLACK_NOTIFY_CHANNEL = "#pmo-releases";
const GOOGLE_DOC_URL = "YOUR_GOOGLE_DOC_URL_HERE";

// Column indices (1-based)
const COL_PL_NAME = 1;
const COL_VERSION = 2;
const COL_TLDR = 3;
const COL_STATUS = 4;
const COL_VOTED_BY = 5;
const COL_VOTED_AT = 6;
const COL_APPROVE = 7;
const COL_REJECT = 8;
const COL_TOMORROW = 9;

// ============================================
// MENU & INITIALIZATION
// ============================================

function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu('🚀 Release Approval')
    .addItem('📋 Show Status', 'showApprovalStatus')
    .addSeparator()
    .addItem('🎉 Good to Announce', 'handleGoodToAnnounce')
    .addSeparator()
    .addItem('🔄 Reset All Buttons', 'resetAllButtons')
    .addItem('📅 Create Tomorrow Section', 'createTomorrowSection')
    .addToUi();

  Logger.log("Release Approval menu added");
}

// ============================================
// EDIT HANDLER - Detects button clicks
// ============================================

function onEdit(e) {
  var sheet = e.source.getActiveSheet();
  var range = e.range;
  var row = range.getRow();
  var col = range.getColumn();

  // Skip header row
  if (row <= 1) return;

  // Skip if not a button column
  if (col < COL_APPROVE || col > COL_TOMORROW) return;

  // Get PL info
  var plName = sheet.getRange(row, COL_PL_NAME).getValue();
  var version = sheet.getRange(row, COL_VERSION).getValue();
  var currentStatus = sheet.getRange(row, COL_STATUS).getValue();

  // Skip separator rows or empty rows
  if (!plName || plName === "---" || plName.includes("Date:")) return;

  // Skip if already voted
  if (currentStatus && currentStatus !== "Pending" && currentStatus !== "⏳ Pending") {
    SpreadsheetApp.getUi().alert("⚠️ This PL has already been voted on!\n\nStatus: " + currentStatus);
    return;
  }

  // Handle button click based on column
  if (col === COL_APPROVE) {
    handleApprove(sheet, row, plName, version);
  } else if (col === COL_REJECT) {
    handleReject(sheet, row, plName, version);
  } else if (col === COL_TOMORROW) {
    handleTomorrow(sheet, row, plName, version);
  }
}

// ============================================
// BUTTON HANDLERS
// ============================================

function handleApprove(sheet, row, plName, version) {
  var user = Session.getEffectiveUser().getEmail();
  var timestamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "MMM d, yyyy h:mm a");

  // Update status
  sheet.getRange(row, COL_STATUS).setValue("✅ Approved");
  sheet.getRange(row, COL_VOTED_BY).setValue(user);
  sheet.getRange(row, COL_VOTED_AT).setValue(timestamp);

  // Style the row
  styleApprovedRow(sheet, row);

  // Disable buttons
  disableButtons(sheet, row);

  // Send Slack notification
  sendSlackNotification(`✅ *${plName}: ${version}* APPROVED by ${user}`);

  // Check if all approved
  checkAllApproved(sheet);

  SpreadsheetApp.getUi().alert("✅ " + plName + " APPROVED!");
}

function handleReject(sheet, row, plName, version) {
  var user = Session.getEffectiveUser().getEmail();
  var timestamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "MMM d, yyyy h:mm a");

  // Update status
  sheet.getRange(row, COL_STATUS).setValue("❌ Rejected");
  sheet.getRange(row, COL_VOTED_BY).setValue(user);
  sheet.getRange(row, COL_VOTED_AT).setValue(timestamp);

  // Style the row
  styleRejectedRow(sheet, row);

  // Disable buttons
  disableButtons(sheet, row);

  // Copy to tomorrow section
  copyToTomorrow(sheet, row, plName, version, "Rejected");

  // Send Slack notification
  sendSlackNotification(`❌ *${plName}: ${version}* REJECTED by ${user}\n→ Moved to tomorrow's release`);

  SpreadsheetApp.getUi().alert("❌ " + plName + " REJECTED\n\n→ Moved to tomorrow's release");
}

function handleTomorrow(sheet, row, plName, version) {
  var user = Session.getEffectiveUser().getEmail();
  var timestamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "MMM d, yyyy h:mm a");

  // Update status
  sheet.getRange(row, COL_STATUS).setValue("➡️ Tomorrow");
  sheet.getRange(row, COL_VOTED_BY).setValue(user);
  sheet.getRange(row, COL_VOTED_AT).setValue(timestamp);

  // Style the row
  styleTomorrowRow(sheet, row);

  // Disable buttons
  disableButtons(sheet, row);

  // Copy to tomorrow section
  copyToTomorrow(sheet, row, plName, version, "Deferred");

  // Send Slack notification
  sendSlackNotification(`➡️ *${plName}: ${version}* deferred to TOMORROW by ${user}`);

  SpreadsheetApp.getUi().alert("➡️ " + plName + " deferred to TOMORROW");
}

// ============================================
// ROW STYLING
// ============================================

function styleApprovedRow(sheet, row) {
  var range = sheet.getRange(row, 1, 1, COL_TOMORROW);
  range.setBackground("#E8F5E9"); // Light green
  sheet.getRange(row, COL_STATUS).setFontColor("#2E7D32").setFontWeight("bold");
}

function styleRejectedRow(sheet, row) {
  var range = sheet.getRange(row, 1, 1, COL_TOMORROW);
  range.setBackground("#FFEBEE"); // Light red
  sheet.getRange(row, COL_STATUS).setFontColor("#C62828").setFontWeight("bold");
}

function styleTomorrowRow(sheet, row) {
  var range = sheet.getRange(row, 1, 1, COL_TOMORROW);
  range.setBackground("#FFF3E0"); // Light orange
  sheet.getRange(row, COL_STATUS).setFontColor("#EF6C00").setFontWeight("bold");
}

function disableButtons(sheet, row) {
  for (var col = COL_APPROVE; col <= COL_TOMORROW; col++) {
    var cell = sheet.getRange(row, col);
    cell.setBackground("#E0E0E0");
    cell.setFontColor("#9E9E9E");
  }
}

function resetAllButtons() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var lastRow = sheet.getLastRow();

  for (var row = 2; row <= lastRow; row++) {
    var plName = sheet.getRange(row, COL_PL_NAME).getValue();
    if (!plName || plName === "---" || plName.includes("Date:")) continue;

    // Reset status
    sheet.getRange(row, COL_STATUS).setValue("⏳ Pending");
    sheet.getRange(row, COL_VOTED_BY).setValue("-");
    sheet.getRange(row, COL_VOTED_AT).setValue("-");

    // Reset row style
    var range = sheet.getRange(row, 1, 1, COL_TOMORROW);
    range.setBackground("white");

    // Reset buttons
    sheet.getRange(row, COL_APPROVE).setBackground("#C8E6C9").setFontColor("#2E7D32").setValue("✓");
    sheet.getRange(row, COL_REJECT).setBackground("#FFCDD2").setFontColor("#C62828").setValue("✗");
    sheet.getRange(row, COL_TOMORROW).setBackground("#FFE0B2").setFontColor("#EF6C00").setValue("→");
  }

  SpreadsheetApp.getUi().alert("🔄 All buttons reset to Pending");
}

// ============================================
// TOMORROW SECTION
// ============================================

function copyToTomorrow(sheet, row, plName, version, reason) {
  // Get TL;DR from original row
  var tldr = sheet.getRange(row, COL_TLDR).getValue();

  // Find or create tomorrow section
  var tomorrowRow = findTomorrowSection(sheet);
  if (tomorrowRow === -1) {
    tomorrowRow = createTomorrowSection();
  }

  // Find next empty row in tomorrow section
  var nextRow = findNextEmptyRow(sheet, tomorrowRow + 1);

  // Copy data
  sheet.getRange(nextRow, COL_PL_NAME).setValue(plName);
  sheet.getRange(nextRow, COL_VERSION).setValue(version);
  sheet.getRange(nextRow, COL_TLDR).setValue(tldr);
  sheet.getRange(nextRow, COL_STATUS).setValue("⏳ Pending");
  sheet.getRange(nextRow, COL_VOTED_BY).setValue("-");
  sheet.getRange(nextRow, COL_VOTED_AT).setValue("-");

  // Set up buttons
  sheet.getRange(nextRow, COL_APPROVE).setValue("✓").setBackground("#C8E6C9").setFontColor("#2E7D32");
  sheet.getRange(nextRow, COL_REJECT).setValue("✗").setBackground("#FFCDD2").setFontColor("#C62828");
  sheet.getRange(nextRow, COL_TOMORROW).setValue("→").setBackground("#FFE0B2").setFontColor("#EF6C00");
}

function findTomorrowSection(sheet) {
  var lastRow = sheet.getLastRow();
  var data = sheet.getRange(1, 1, lastRow, 1).getValues();

  for (var i = 0; i < data.length; i++) {
    if (data[i][0] && data[i][0].toString().includes("Tomorrow")) {
      return i + 1; // Convert to 1-based
    }
  }
  return -1;
}

function createTomorrowSection() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var lastRow = sheet.getLastRow();

  // Add separator
  var sepRow = lastRow + 2;
  sheet.getRange(sepRow, 1, 1, COL_TOMORROW).merge();
  sheet.getRange(sepRow, 1).setValue("─".repeat(50));
  sheet.getRange(sepRow, 1).setHorizontalAlignment("center").setFontColor("#9E9E9E");

  // Add tomorrow header
  var tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  var tomorrowStr = Utilities.formatDate(tomorrow, Session.getScriptTimeZone(), "d MMM yyyy");

  var headerRow = sepRow + 1;
  sheet.getRange(headerRow, 1, 1, COL_TOMORROW).merge();
  sheet.getRange(headerRow, 1).setValue("📅 Tomorrow's Release: " + tomorrowStr);
  sheet.getRange(headerRow, 1).setBackground("#E3F2FD").setFontWeight("bold").setFontSize(12);

  // Add column headers
  var colHeaderRow = headerRow + 1;
  sheet.getRange(colHeaderRow, COL_PL_NAME).setValue("PL Name");
  sheet.getRange(colHeaderRow, COL_VERSION).setValue("Version");
  sheet.getRange(colHeaderRow, COL_TLDR).setValue("TL;DR");
  sheet.getRange(colHeaderRow, COL_STATUS).setValue("Status");
  sheet.getRange(colHeaderRow, COL_VOTED_BY).setValue("Voted By");
  sheet.getRange(colHeaderRow, COL_VOTED_AT).setValue("Voted At");
  sheet.getRange(colHeaderRow, COL_APPROVE).setValue("Approve");
  sheet.getRange(colHeaderRow, COL_REJECT).setValue("Reject");
  sheet.getRange(colHeaderRow, COL_TOMORROW).setValue("Tomorrow");
  sheet.getRange(colHeaderRow, 1, 1, COL_TOMORROW).setFontWeight("bold").setBackground("#F5F5F5");

  return colHeaderRow;
}

function findNextEmptyRow(sheet, startRow) {
  var lastRow = sheet.getLastRow();
  for (var row = startRow; row <= lastRow + 1; row++) {
    var value = sheet.getRange(row, COL_PL_NAME).getValue();
    if (!value || value === "") {
      return row;
    }
  }
  return lastRow + 1;
}

// ============================================
// APPROVAL STATUS
// ============================================

function checkAllApproved(sheet) {
  var stats = getApprovalStats(sheet);

  if (stats.pending === 0 && stats.approved > 0) {
    SpreadsheetApp.getUi().alert(
      "🎉 All PLs have been reviewed!\n\n" +
      "✅ Approved: " + stats.approved + "\n" +
      "❌ Rejected/Tomorrow: " + stats.rejected + "\n\n" +
      "Click menu: Release Approval → Good to Announce"
    );
  }
}

function getApprovalStats(sheet) {
  var lastRow = sheet.getLastRow();
  var data = sheet.getRange(2, 1, lastRow - 1, COL_STATUS).getValues();

  var approved = 0, rejected = 0, pending = 0;

  for (var i = 0; i < data.length; i++) {
    var plName = data[i][0];
    var status = data[i][COL_STATUS - 1];

    // Skip empty or separator rows
    if (!plName || plName === "---" || plName.includes("Date:") || plName.includes("Tomorrow")) continue;

    if (status && status.includes("✅")) {
      approved++;
    } else if (status && (status.includes("❌") || status.includes("➡️"))) {
      rejected++;
    } else if (status && (status.includes("Pending") || status.includes("⏳"))) {
      pending++;
    }
  }

  return { approved: approved, rejected: rejected, pending: pending };
}

function showApprovalStatus() {
  var sheet = SpreadsheetApp.getActiveSheet();
  var stats = getApprovalStats(sheet);
  var total = stats.approved + stats.rejected + stats.pending;

  var message = "📊 APPROVAL STATUS\n\n" +
    "✅ Approved: " + stats.approved + "\n" +
    "⏳ Pending: " + stats.pending + "\n" +
    "❌ Rejected/Tomorrow: " + stats.rejected + "\n\n" +
    "Total: " + total;

  if (stats.pending === 0 && stats.approved > 0) {
    message += "\n\n🎉 Ready to announce!";
  }

  SpreadsheetApp.getUi().alert(message);
}

// ============================================
// GOOD TO ANNOUNCE
// ============================================

function handleGoodToAnnounce() {
  var ui = SpreadsheetApp.getUi();
  var sheet = SpreadsheetApp.getActiveSheet();
  var stats = getApprovalStats(sheet);

  // Confirm action
  var response = ui.alert(
    "🎉 Good to Announce",
    "This will post the release notes to Slack.\n\n" +
    "✅ Approved: " + stats.approved + "\n" +
    "❌ Rejected/Tomorrow: " + stats.rejected + "\n" +
    "⏳ Pending: " + stats.pending + "\n\n" +
    "Continue?",
    ui.ButtonSet.YES_NO
  );

  if (response !== ui.Button.YES) return;

  // Get approved PLs
  var approvedPLs = getApprovedPLs(sheet);

  if (approvedPLs.length === 0) {
    ui.alert("⚠️ No approved PLs to announce!");
    return;
  }

  // Build and post message
  var message = buildAnnouncementMessage(approvedPLs, stats);
  var success = postToSlack(message, SLACK_ANNOUNCE_CHANNEL);

  if (success) {
    // Mark sheet as announced
    markAsAnnounced(sheet);

    ui.alert("🎉 Release notes posted to Slack!\n\n" +
      "Approved PLs: " + approvedPLs.length + "\n" +
      "Posted to: " + SLACK_ANNOUNCE_CHANNEL);
  } else {
    ui.alert("⚠️ Error posting to Slack.\nCheck the webhook URL.");
  }
}

function getApprovedPLs(sheet) {
  var lastRow = sheet.getLastRow();
  var data = sheet.getRange(2, 1, lastRow - 1, COL_VOTED_AT).getValues();

  var approved = [];
  for (var i = 0; i < data.length; i++) {
    var plName = data[i][0];
    var status = data[i][COL_STATUS - 1];

    // Skip empty/separator rows
    if (!plName || plName === "---" || plName.includes("Date:") || plName.includes("Tomorrow")) continue;

    if (status && status.includes("✅")) {
      approved.push({
        name: data[i][COL_PL_NAME - 1],
        version: data[i][COL_VERSION - 1],
        tldr: data[i][COL_TLDR - 1],
        voter: data[i][COL_VOTED_BY - 1],
        time: data[i][COL_VOTED_AT - 1]
      });
    }
  }

  return approved;
}

function buildAnnouncementMessage(approvedPLs, stats) {
  var today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "d MMMM yyyy");

  var message = "🚀 *RELEASE DEPLOYED: " + today + "*\n\n";
  message += "━".repeat(30) + "\n";
  message += "*------------------TL;DR:------------------*\n\n";
  message += "*Key Deployments:*\n";

  approvedPLs.forEach(function(pl) {
    message += "• *" + pl.name + ":* " + pl.tldr + "\n";
  });

  message += "\n━".repeat(30) + "\n\n";

  // Detailed section
  approvedPLs.forEach(function(pl) {
    message += "*" + pl.name + ": " + pl.version + "*\n";
    message += "_Approved by " + pl.voter + "_\n\n";
  });

  message += "━".repeat(30) + "\n";
  message += "📄 Full notes: " + GOOGLE_DOC_URL + "\n";
  message += "_Posted at " + Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "h:mm a") + "_";

  return message;
}

function markAsAnnounced(sheet) {
  var lastRow = sheet.getLastRow();

  // Add announced banner
  var bannerRow = lastRow + 2;
  sheet.getRange(bannerRow, 1, 1, COL_TOMORROW).merge();
  sheet.getRange(bannerRow, 1).setValue("🎉 ANNOUNCED - " + new Date().toLocaleString());
  sheet.getRange(bannerRow, 1).setBackground("#4CAF50").setFontColor("white").setFontWeight("bold");
}

// ============================================
// SLACK INTEGRATION
// ============================================

function sendSlackNotification(message) {
  postToSlack(message, SLACK_NOTIFY_CHANNEL);
}

function postToSlack(message, channel) {
  if (!SLACK_WEBHOOK_URL || SLACK_WEBHOOK_URL === "YOUR_SLACK_WEBHOOK_URL_HERE") {
    Logger.log("Slack webhook not configured");
    return false;
  }

  var payload = {
    text: message,
    channel: channel,
    unfurl_links: false
  };

  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    var response = UrlFetchApp.fetch(SLACK_WEBHOOK_URL, options);
    var code = response.getResponseCode();
    Logger.log("Slack response: " + code);
    return code === 200;
  } catch (e) {
    Logger.log("Slack error: " + e);
    return false;
  }
}

// ============================================
// SHEET SETUP HELPER
// ============================================

function setupNewReleaseSection() {
  var ui = SpreadsheetApp.getUi();
  var response = ui.prompt(
    "📅 New Release Section",
    "Enter the release date (e.g., '3rd Feb 2026'):",
    ui.ButtonSet.OK_CANCEL
  );

  if (response.getSelectedButton() !== ui.Button.OK) return;

  var dateStr = response.getResponseText();
  var sheet = SpreadsheetApp.getActiveSheet();
  var lastRow = sheet.getLastRow();

  // Add separator
  var sepRow = lastRow + 2;
  sheet.getRange(sepRow, 1, 1, COL_TOMORROW).merge();
  sheet.getRange(sepRow, 1).setValue("━".repeat(50));
  sheet.getRange(sepRow, 1).setHorizontalAlignment("center").setFontColor("#9E9E9E");

  // Add date header
  var headerRow = sepRow + 1;
  sheet.getRange(headerRow, 1, 1, COL_TOMORROW).merge();
  sheet.getRange(headerRow, 1).setValue("📅 Release: " + dateStr);
  sheet.getRange(headerRow, 1).setBackground("#E3F2FD").setFontWeight("bold").setFontSize(12);

  // Add column headers
  var colHeaderRow = headerRow + 1;
  var headers = ["PL Name", "Version", "TL;DR", "Status", "Voted By", "Voted At", "✓", "✗", "→"];
  for (var i = 0; i < headers.length; i++) {
    sheet.getRange(colHeaderRow, i + 1).setValue(headers[i]);
  }
  sheet.getRange(colHeaderRow, 1, 1, COL_TOMORROW).setFontWeight("bold").setBackground("#F5F5F5");

  // Style button headers
  sheet.getRange(colHeaderRow, COL_APPROVE).setBackground("#C8E6C9").setFontColor("#2E7D32");
  sheet.getRange(colHeaderRow, COL_REJECT).setBackground("#FFCDD2").setFontColor("#C62828");
  sheet.getRange(colHeaderRow, COL_TOMORROW).setBackground("#FFE0B2").setFontColor("#EF6C00");

  ui.alert("✅ New release section created!\n\nAdd PLs starting from row " + (colHeaderRow + 1));
}
