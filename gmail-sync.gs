/**
 * Gmail → Application Tracker bridge.
 *
 * Runs inside Google Apps Script (script.google.com), NOT in the tracker.
 * It reads recent Gmail messages, keeps only the ones that look like they came
 * from a recruiting pipeline, guesses which stage each one represents, and
 * serves the result as JSON for the tracker to fetch.
 *
 * It never sends, deletes, archives or modifies mail. Read-only by construction:
 * the only Gmail calls used are search + read.
 *
 * Setup: see GMAIL-SETUP.md.
 */

// ============================ CONFIG ============================

/** Shared secret. MUST be changed before deploying. The tracker sends this back. */
var TOKEN = "CHANGE-ME-TO-A-LONG-RANDOM-STRING";

/** How far back to look. Keep modest — this is re-scanned on every sync. */
var LOOKBACK_DAYS = 45;

/** Hard cap on threads examined per sync, to stay well inside execution limits. */
var MAX_THREADS = 150;

/** Extra Gmail search terms. e.g. "label:jobs" to restrict to a label you apply. */
var EXTRA_QUERY = "";

/** Characters of message body returned per email. Keep small — this crosses the wire. */
var SNIPPET_CHARS = 200;

// ====================== STAGE CLASSIFIER ========================
// Order matters: the first group that matches wins. Rejections are tested
// before interviews because rejection emails routinely say "thank you for
// interviewing with us, however...".

var RULES = [
  {
    stage: "Offer",
    strong: [
      "pleased to offer", "delighted to offer", "happy to offer", "we would like to offer",
      "offer of employment", "offer letter", "formal offer", "your offer"
    ],
    weak: ["congratulations", "welcome to the team"]
  },
  {
    stage: "Rejected/Withdrawn",
    strong: [
      "regret to inform", "not been successful", "unsuccessful on this occasion",
      "not be progressing", "not progressing your application", "will not be moving forward",
      "decided not to move forward", "decided not to proceed", "not selected",
      "unable to offer you", "other candidates", "no longer under consideration",
      "application was not successful"
    ],
    weak: ["unfortunately", "we are sorry"]
  },
  {
    stage: "Interview",
    strong: [
      "invite you to interview", "invitation to interview", "schedule your interview",
      "interview invitation", "assessment centre", "assessment center", "superday",
      "final round", "meet the team", "book a time to speak", "schedule a call with"
    ],
    weak: ["interview", "next round", "speak with our team"]
  },
  {
    stage: "OA/Assessment",
    strong: [
      "online assessment", "coding challenge", "coding test", "technical assessment",
      "hackerrank", "codility", "codesignal", "hirevue", "shl ", "cut-e", "cappfinity",
      "numerical reasoning", "situational judgement", "psychometric", "aptitude test",
      "video interview", "take-home", "take home task"
    ],
    weak: ["assessment", "complete the test"]
  },
  {
    stage: "Applied",
    strong: [
      "we have received your application", "application received", "thank you for applying",
      "thanks for applying", "your application has been submitted", "application confirmation"
    ],
    weak: ["thank you for your interest"]
  }
];

/** Applicant-tracking-system senders: the domain says nothing about the company. */
var ATS_DOMAINS = [
  "greenhouse.io", "myworkday.com", "workday.com", "lever.co", "smartrecruiters.com",
  "icims.com", "taleo.net", "successfactors.com", "avature.net", "ashbyhq.com",
  "jobvite.com", "workable.com", "teamtailor.com", "eightfold.ai", "brassring.com",
  "oraclecloud.com", "gr.hs-sites.com", "hire.lever.co", "us.greenhouse-mail.io"
];

/** A message must look recruiting-ish at all before we bother classifying it. */
var RELEVANCE = [
  "application", "applied", "candidate", "recruit", "interview", "assessment",
  "internship", "graduate programme", "graduate program", "role", "vacancy",
  "position", "hiring", "talent", "offer", "placement", "studentship"
];

// ========================== ENTRY POINT =========================

function doGet(e) {
  var params = (e && e.parameter) || {};
  var callback = params.callback;

  var payload;
  try {
    if (params.token !== TOKEN || TOKEN === "CHANGE-ME-TO-A-LONG-RANDOM-STRING") {
      payload = { ok: false, error: "unauthorized" };
    } else {
      var days = Math.min(180, Math.max(1, parseInt(params.days, 10) || LOOKBACK_DAYS));
      payload = { ok: true, generated: new Date().toISOString(), events: collectEvents(days) };
    }
  } catch (err) {
    payload = { ok: false, error: String(err && err.message || err) };
  }

  var json = JSON.stringify(payload);

  // JSONP: the tracker runs from a file:// page, whose origin is "null" and so
  // cannot rely on CORS. A <script> tag has no such restriction.
  if (callback && /^[A-Za-z_$][\w$]*$/.test(callback)) {
    return ContentService
      .createTextOutput(callback + "(" + json + ");")
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(json).setMimeType(ContentService.MimeType.JSON);
}

// ======================== CORE SCANNING =========================

function collectEvents(days) {
  var query = "newer_than:" + days + "d -in:chats -in:drafts " +
              "-category:promotions -category:social" +
              (EXTRA_QUERY ? " " + EXTRA_QUERY : "");

  var threads = GmailApp.search(query, 0, MAX_THREADS);
  var events = [];

  for (var t = 0; t < threads.length; t++) {
    var messages = threads[t].getMessages();
    for (var m = 0; m < messages.length; m++) {
      var msg = messages[m];

      // Skip anything you sent yourself — outbound mail isn't a status update.
      var from = msg.getFrom() || "";
      if (isSelf(from)) continue;

      var subject = msg.getSubject() || "";
      var body = plainBody(msg);
      var hay = (subject + " \n " + body).toLowerCase();

      if (!containsAny(hay, RELEVANCE)) continue;

      var verdict = classify(hay);
      if (!verdict) continue;

      var addr = extractAddress(from);
      var domain = addr.split("@")[1] || "";

      events.push({
        id: msg.getId(),
        date: toISODate(msg.getDate()),
        fromName: extractName(from),
        fromAddress: addr,
        domain: domain,
        isATS: isATS(domain),
        subject: subject,
        snippet: squash(body).slice(0, SNIPPET_CHARS),
        stage: verdict.stage,
        confidence: verdict.confidence,
        matchedOn: verdict.phrase
      });
    }
  }

  // Newest first, so the tracker's "latest wins" logic is trivial.
  events.sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; });
  return events;
}

function classify(hay) {
  for (var i = 0; i < RULES.length; i++) {
    var rule = RULES[i];
    var hit = firstMatch(hay, rule.strong);
    if (hit) return { stage: rule.stage, confidence: "high", phrase: hit };
  }
  for (var j = 0; j < RULES.length; j++) {
    var r = RULES[j];
    var weakHit = firstMatch(hay, r.weak);
    if (weakHit) return { stage: r.stage, confidence: "low", phrase: weakHit };
  }
  return null;
}

// =========================== HELPERS ============================

function firstMatch(hay, phrases) {
  if (!phrases) return null;
  for (var i = 0; i < phrases.length; i++) {
    if (hay.indexOf(phrases[i]) !== -1) return phrases[i];
  }
  return null;
}
function containsAny(hay, phrases) { return firstMatch(hay, phrases) !== null; }

function plainBody(msg) {
  try { return msg.getPlainBody() || ""; }
  catch (e) { return msg.getBody().replace(/<[^>]+>/g, " "); }
}

function squash(s) { return String(s).replace(/\s+/g, " ").trim(); }

function extractAddress(from) {
  var m = /<([^>]+)>/.exec(from);
  return (m ? m[1] : from).trim().toLowerCase();
}
function extractName(from) {
  var m = /^\s*"?([^"<]+?)"?\s*</.exec(from);
  return m ? m[1].trim() : "";
}
function isATS(domain) {
  for (var i = 0; i < ATS_DOMAINS.length; i++) {
    if (domain === ATS_DOMAINS[i] || domain.indexOf("." + ATS_DOMAINS[i]) !== -1) return true;
  }
  return false;
}
function isSelf(from) {
  var me = Session.getActiveUser().getEmail().toLowerCase();
  return me && extractAddress(from) === me;
}
function toISODate(d) {
  return Utilities.formatDate(d, Session.getScriptTimeZone(), "yyyy-MM-dd");
}

// ===================== MANUAL TEST HELPER =======================
// Run this from the Apps Script editor to see what the scan finds, without
// deploying anything. Output goes to the execution log.
function testScan() {
  var events = collectEvents(LOOKBACK_DAYS);
  Logger.log("Found " + events.length + " candidate events");
  for (var i = 0; i < Math.min(events.length, 25); i++) {
    var e = events[i];
    Logger.log([e.date, e.stage, "(" + e.confidence + ")", e.fromName || e.domain, "—", e.subject].join(" "));
  }
}
