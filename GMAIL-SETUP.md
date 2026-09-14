# Connecting the tracker to Gmail

Roughly 10 minutes, done once.

The tracker itself never touches Gmail. A small Google Apps Script does the reading,
on Google's servers, and publishes a summary that the tracker fetches. This is the only
shape that works: the tracker runs from a `file://` page, and Gmail's API can't
authenticate one.

```
Gmail  →  Apps Script (hourly trigger)  →  JSON endpoint  →  tracker  →  review queue
```

---

## 1. Create the script

1. Go to <https://script.google.com> and click **New project**.
2. Delete the placeholder `myFunction` code.
3. Open `gmail-sync.gs` from this folder, copy all of it, and paste it in.
4. Rename the project (top left) to something like `Gmail → Tracker`.

## 2. Set your token

Near the top of the script:

```js
var TOKEN = "CHANGE-ME-TO-A-LONG-RANDOM-STRING";
```

Replace it with a long random string — 30+ characters, letters and digits. Generate one
however you like; a keyboard mash is fine. **Keep this handy, you'll paste it into the
tracker in step 5.** The script refuses to run until you change it.

Two other knobs you may want:

| Setting         | Default | What it does                                                         |
|-----------------|---------|----------------------------------------------------------------------|
| `LOOKBACK_DAYS` | `45`    | How far back each scan reaches.                                        |
| `EXTRA_QUERY`   | `""`    | Extra Gmail search terms. Set to `"label:jobs"` to scan only a label. |

If your inbox is busy, `EXTRA_QUERY` with a label you apply to recruiting mail will cut
noise dramatically.

## 3. Test before deploying

In the editor, pick **`testScan`** from the function dropdown and press **Run**.

Google will ask for authorization the first time. It will warn that the app isn't
verified — that's expected, it's your own script, unpublished. Click
**Advanced → Go to (project name)** and allow. It asks for permission to read Gmail
because that is exactly what it does.

Open **Execution log**. You should see lines like:

```
Found 14 candidate events
2026-08-04 OA/Assessment (high) Revolut Careers — Complete your online assessment
2026-08-01 Rejected/Withdrawn (high) Pirelli — Update on your application
```

If you see zero, widen `LOOKBACK_DAYS` or clear `EXTRA_QUERY`.

## 4. Deploy as a web app

1. **Deploy → New deployment**.
2. Gear icon → **Web app**.
3. Set:
   - **Execute as:** Me
   - **Who has access:** Anyone
4. **Deploy**, then copy the **Web app URL**. It ends in `/exec`.

> **"Anyone" is doing real work here.** It's what lets the tracker fetch without an OAuth
> flow. The endpoint is protected only by your token — see *Security* below.

## 5. Point the tracker at it

Open `index.html`, click **⚙** in the header, and fill in:

- **Web app URL** — the `/exec` URL from step 4
- **Shared token** — the string from step 2
- **Auto-apply** — leave off until you trust the classifications

Save, then click **Sync Gmail**. Matches land in the **Inbox** tab.

## 6. Keep it fresh (optional)

In the Apps Script editor, click the clock icon (**Triggers**) → **Add trigger**:

- Function: `collectEvents` — *not* `doGet`
- Event source: **Time-driven** → Hour timer → every hour

This is purely a warm-up; the tracker gets live results whenever it syncs regardless.
Skip it if you'd rather the script only run when you ask.

To sync every time you open the tracker, tick **Sync automatically on page load** in settings.

---

## How stage detection works

The script matches phrases in the subject and body, in priority order:

| Stage                 | Triggered by phrases like                                          |
|-----------------------|--------------------------------------------------------------------|
| Offer                 | "pleased to offer", "offer of employment", "offer letter"           |
| Rejected/Withdrawn    | "regret to inform", "not been successful", "other candidates"       |
| Interview             | "invitation to interview", "assessment centre", "final round"       |
| OA/Assessment         | "online assessment", "HackerRank", "HireVue", "numerical reasoning" |
| Applied               | "we have received your application", "thank you for applying"       |

Rejections are checked *before* interviews on purpose — rejection emails routinely open
with "thank you for interviewing with us".

A phrase from the main list marks the email **high confidence**; a weaker fallback word
(bare "unfortunately", bare "interview") marks it **low confidence** and it will never be
auto-applied.

Then the tracker decides which application the email belongs to, scoring:

- sender domain matching the company name (`revolut.com` → Revolut) — strongest
- company name in the sender's display name
- company name in the subject or body
- a distinctive word from the role title — small bonus

It needs a combined score of 3 to claim a match. Applicant tracking systems
(Greenhouse, Workday, Lever, and similar) are recognised, and their domains ignored,
since `myworkday.com` tells you nothing about who's hiring.

**It only ever moves an application forward** — Applied → Interview, never the reverse —
with one exception: a rejection can arrive at any point and always wins. Unmatched emails
are offered as new applications instead.

Every applied change appends an audit line to that application's notes:

```
[2026-08-04] → OA/Assessment · Complete your online assessment (Gmail sync)
```

## Tuning it

Everything lives in the `RULES` array in `gmail-sync.gs`. To stop a phrase misfiring,
delete it. To catch a company's odd wording, add it to the relevant `strong` list. Save
the script, then **Deploy → Manage deployments → edit → Version: New version**. A plain
save does *not* update the live URL.

## Security

Be clear-eyed about the trade you accepted:

- The endpoint is public. Anyone with **both** the URL and the token can read the
  parsed output: sender, subject line, detected stage, and the first 200 characters of
  each matching email. Full bodies, attachments, and everything not matching the
  recruiting filter are never exposed.
- The token travels as a URL query parameter, so it can appear in browser history. It's
  also in this browser's local storage.
- The script only ever reads. It cannot send, delete, archive, or label anything — the
  permissions it holds allow reading and searching, nothing more.

If a token leaks, change `TOKEN` in the script, redeploy a new version, and update settings.
**Forget settings** in the tracker clears the URL, token, and review history locally.

## When something breaks

| Symptom | Cause |
|---|---|
| `the script rejected the token` | Token mismatch, or still the placeholder. Check both ends. |
| `timed out` | Deployment isn't set to **Anyone**, or the URL is wrong. Open the URL in a browser tab — you should see JSON, not a login page. |
| `couldn't reach the script URL` | Typo in the URL, or it doesn't end in `/exec`. |
| Syncs fine, finds nothing | `EXTRA_QUERY` too narrow, or `LOOKBACK_DAYS` too short. Run `testScan` to see raw results. |
| Right email, wrong application | Company name in the tracker doesn't resemble the sender domain. Rename it to match, e.g. "Goldman Sachs" for `gs.com` won't match — add the domain word to the role or notes field. |
| Edited the script, nothing changed | You saved but didn't deploy a **new version**. |
