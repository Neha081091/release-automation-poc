/**
 * Google Docs Approval System - Apps Script
 *
 * SETUP:
 * 1. Open your Google Doc
 * 2. Extensions → Apps Script
 * 3. Paste this entire code
 * 4. Update SLACK_WEBHOOK_URL below
 * 5. Save and refresh the Doc
 * 6. Use menu: "🚀 Release Approval" → "Good to Announce"
 */

// ============================================
// CONFIGURATION - UPDATE THIS
// ============================================
const SLACK_WEBHOOK_URL = "YOUR_SLACK_WEBHOOK_URL_HERE";
const SLACK_CHANNEL = "#release-announcements";

// ============================================
// MENU SETUP
// ============================================

function onOpen() {
  DocumentApp.getUi()
    .createMenu('🚀 Release Approval')
    .addItem('📊 Check Status', 'checkApprovalStatus')
    .addSeparator()
    .addItem('🎉 Good to Announce', 'goodToAnnounce')
    .addSeparator()
    .addItem('🔄 Reset All Checkboxes', 'resetCheckboxes')
    .addToUi();
}

// ============================================
// CHECK APPROVAL STATUS
// ============================================

function checkApprovalStatus() {
  var doc = DocumentApp.getActiveDocument();
  var body = doc.getBody();
  var text = body.getText();

  // Count checkboxes by looking for patterns
  var approvedCount = 0;
  var rejectedCount = 0;
  var tomorrowCount = 0;
  var pendingCount = 0;

  // Find all PL rows and their status
  var lines = text.split('\n');
  var plLines = [];

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    // Look for lines with Release version pattern
    if (line.includes('Release') && line.includes('│')) {
      plLines.push(line);

      // Check status based on checkbox markers
      if (line.includes('✅') || line.includes('[x]') || line.includes('☑')) {
        if (line.indexOf('✅') < line.lastIndexOf('│')) {
          approvedCount++;
        }
      } else if (line.includes('❌')) {
        rejectedCount++;
      } else if (line.includes('➡️')) {
        tomorrowCount++;
      } else {
        pendingCount++;
      }
    }
  }

  var total = approvedCount + rejectedCount + tomorrowCount + pendingCount;

  var message = "📊 APPROVAL STATUS\n\n" +
    "✅ Approved: " + approvedCount + "\n" +
    "❌ Rejected: " + rejectedCount + "\n" +
    "➡️ Tomorrow: " + tomorrowCount + "\n" +
    "⏳ Pending: " + pendingCount + "\n\n" +
    "Total PLs: " + total;

  if (pendingCount === 0 && approvedCount > 0) {
    message += "\n\n🎉 Ready to announce!";
  }

  DocumentApp.getUi().alert(message);
}

// ============================================
// GOOD TO ANNOUNCE
// ============================================

function goodToAnnounce() {
  var ui = DocumentApp.getUi();

  // Confirm action
  var response = ui.alert(
    '🎉 Good to Announce',
    'This will post the release notes to Slack.\n\nContinue?',
    ui.ButtonSet.YES_NO
  );

  if (response !== ui.Button.YES) {
    return;
  }

  // Get document content
  var doc = DocumentApp.getActiveDocument();
  var body = doc.getBody();
  var text = body.getText();

  // Extract release info
  var releaseInfo = extractReleaseInfo(text);

  if (!releaseInfo.pls || releaseInfo.pls.length === 0) {
    ui.alert('⚠️ No PLs found in the document.');
    return;
  }

  // Build Slack message
  var message = buildSlackMessage(releaseInfo);

  // Post to Slack
  var success = postToSlack(message);

  if (success) {
    // Add "ANNOUNCED" marker to document
    markAsAnnounced(body);

    ui.alert('🎉 Success!\n\nRelease notes posted to Slack.\nChannel: ' + SLACK_CHANNEL);
  } else {
    ui.alert('⚠️ Error posting to Slack.\n\nCheck the webhook URL in Apps Script.');
  }
}

// ============================================
// EXTRACT RELEASE INFO
// ============================================

function extractReleaseInfo(text) {
  var info = {
    date: "",
    pls: [],
    tldr: "",
    fullNotes: ""
  };

  var lines = text.split('\n');

  // Find release date
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].includes('Release:') || lines[i].includes('Deployment Summary:')) {
      var match = lines[i].match(/(\d+\w*\s+\w+\s+\d{4})/);
      if (match) {
        info.date = match[1];
        break;
      }
    }
  }

  // Find TL;DR section
  var inTldr = false;
  var tldrLines = [];

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];

    if (line.includes('TL;DR')) {
      inTldr = true;
      continue;
    }

    if (inTldr) {
      if (line.includes('──────') || line.includes('══════')) {
        inTldr = false;
        break;
      }
      if (line.trim()) {
        tldrLines.push(line);
      }
    }
  }

  info.tldr = tldrLines.join('\n');

  // Find PLs from approval table
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (line.includes('Release') && line.includes('│') && !line.includes('PL Name')) {
      var parts = line.split('│');
      if (parts.length >= 2) {
        var plName = parts[0].trim();
        var version = parts[1].trim();
        if (plName && version) {
          info.pls.push({
            name: plName,
            version: version
          });
        }
      }
    }
  }

  // Get everything after the separator as full notes
  var separatorIndex = text.indexOf('═══');
  if (separatorIndex > 0) {
    info.fullNotes = text.substring(separatorIndex + 80).trim();
  }

  return info;
}

// ============================================
// BUILD SLACK MESSAGE
// ============================================

function buildSlackMessage(info) {
  var message = "🚀 *RELEASE DEPLOYED: " + (info.date || new Date().toDateString()) + "*\n\n";
  message += "━".repeat(30) + "\n";

  if (info.tldr) {
    message += "*------------------TL;DR:------------------*\n\n";
    message += info.tldr + "\n\n";
  }

  message += "━".repeat(30) + "\n\n";

  // List approved PLs
  message += "*Deployed PLs:*\n";
  for (var i = 0; i < info.pls.length; i++) {
    message += "✅ " + info.pls[i].name + ": " + info.pls[i].version + "\n";
  }

  message += "\n━".repeat(30) + "\n";
  message += "_Posted at " + new Date().toLocaleTimeString() + "_";

  return message;
}

// ============================================
// POST TO SLACK
// ============================================

function postToSlack(message) {
  if (!SLACK_WEBHOOK_URL || SLACK_WEBHOOK_URL === "YOUR_SLACK_WEBHOOK_URL_HERE") {
    Logger.log("Slack webhook not configured");
    return false;
  }

  var payload = {
    text: message,
    channel: SLACK_CHANNEL,
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
// MARK AS ANNOUNCED
// ============================================

function markAsAnnounced(body) {
  // Add announcement marker at the top
  var timestamp = new Date().toLocaleString();
  var marker = "🎉 ANNOUNCED TO SLACK - " + timestamp + "\n\n";

  body.insertParagraph(0, marker)
    .setBackgroundColor('#C8E6C9')
    .setBold(true);
}

// ============================================
// RESET CHECKBOXES
// ============================================

function resetCheckboxes() {
  var ui = DocumentApp.getUi();
  var response = ui.alert(
    '🔄 Reset Checkboxes',
    'This will reset all approval checkboxes to unchecked.\n\nContinue?',
    ui.ButtonSet.YES_NO
  );

  if (response !== ui.Button.YES) {
    return;
  }

  var doc = DocumentApp.getActiveDocument();
  var body = doc.getBody();

  // Replace checked boxes with unchecked
  body.replaceText('☑', '☐');
  body.replaceText('✅', '☐');
  body.replaceText('❌', '☐');
  body.replaceText('➡️', '☐');
  body.replaceText('\\[x\\]', '☐');
  body.replaceText('\\[X\\]', '☐');

  ui.alert('🔄 All checkboxes reset to unchecked.');
}

// ============================================
// HELPER: String repeat polyfill for older Apps Script
// ============================================

if (!String.prototype.repeat) {
  String.prototype.repeat = function(count) {
    var str = '' + this;
    var result = '';
    for (var i = 0; i < count; i++) {
      result += str;
    }
    return result;
  };
}
