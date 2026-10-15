# Universal Forum Post Extractor (URL in, TXT out)

A local application that takes a forum URL, works out how the forum is built, walks every
discussion thread on every listing page, and saves **only the original post** of each thread to
rotating UTF-8 `.txt` files. You don't write a scraper per site. Detection is automatic, and when it
isn't confident you correct a few CSS selectors once in the preview screen and save them as a
profile for that forum.

```
paste URL → Analyze & preview → (optionally correct selectors) → Start → Forum_Pages_0002-1101.txt …
```

## Installation

**Quick start:** on Windows double-click `start_windows.bat`; on macOS/Linux run `./start_mac_linux.sh`.
The first start installs everything (a few minutes); later starts open the app directly.

Manual installation:

Requires Python 3.10+ (tested with 3.13).

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

If Chromium is already installed elsewhere, set `FORUM_EXTRACTOR_CHROMIUM=/path/to/chrome` instead
of running `playwright install`.

## Using the web interface

```bash
python -m forum_extractor serve            # http://127.0.0.1:8765
```

1. Paste the starting URL, for example
   `https://forum.nofap.com/index.php?forums/porn-addiction-recovery.2/page-2`.
2. Click **Analyze & preview**. The page opens in a headless browser and the app shows:
   * the page type (listing, thread or blocked), the detected forum software and confidence scores
   * the discussion threads found, plus the next-page link and how it was found
   * the extracted **original post** of the first thread, its confidence, the container HTML and
     every candidate that was evaluated, including the reason each rejected one was rejected
   * a **site profile**: all selectors, pre-filled
3. Click **Preview** next to any thread (or paste a thread URL) to test the extraction on one thread.
   This is the live single-thread test mode.
4. If something is wrong, edit the selectors, click **Re-test with these selectors**, then
   **Save profile**. Saved profiles are reused for every job on that domain.
5. Choose the output folder and settings, then click **Start**. Live counters show the current listing
   page, threads discovered, completed, skipped, failed and duplicate, the current output file,
   extraction confidence, recent errors and the tail of the output file.
6. Use **Pause**, **Resume** and **Stop** at any time. **Retry failed threads** re-runs only the
   failures and appends the successes to the correct page-range file. **Export processing log**
   (JSONL) and **Export thread list (CSV)** download the records. **Open output folder** opens the
   folder in your file manager.

If the app can't work out the pagination, the job pauses and lists the page links it found. You pick
the next page, paste one, or tell it this was the last page.

With **Resume previous session** ticked, pressing Start on a URL and output folder that already have
an unfinished job continues that job instead of starting over.

## Reddit

Use the **Reddit** tab, which is separate from the forum input. Type a subreddit (`r/socialanxiety` or its full
link), choose which list to read (Newest, Hot, Top – all time / year / month), how many posts you want,
and how many posts go into each text file.

Reddit is one endlessly scrolling list, so Reddit jobs are counted in **posts**, not pages:
* each record starts with `POST NUMBER: n` and holds the post title (`THREAD TITLE`) and the question
  (`ORIGINAL POST`); comments, image/link posts and ads are skipped; a title-only post is saved with
  `(this post has no body text - title only)`
* files are named by post number: `Reddit_Posts_00001-00500.txt`, `Reddit_Posts_00501-01000.txt`, …
* the job stops once **How many posts** have been saved

Behind the scenes the app reads Reddit's public data feed (the `.json` version of the subreddit list):
one request returns 100 posts with their full title and text, so 1,000 posts need only about 10 requests and no
post pages are opened.

Things to know:
* Reddit shows at most about 1,000 posts per list. To reach other posts, run another list (Hot, Top – all time…).
* Reddit's robots.txt asks automated tools not to crawl. With **Respect Reddit's robots.txt** ticked, the job
  stops, and the job card then offers **Ignore robots.txt & run**, which asks you to confirm. Only do this if
  your use is allowed by Reddit's terms.
* Keep the delay at 2 seconds or more. If Reddit shows a "blocked by network security" page, the job stops
  and the app does not try to get around it. Private and 18+ communities are not accessed.

## Command line

```bash
python -m forum_extractor analyze  "URL"                 # detection report + preview
python -m forum_extractor test-thread "THREAD_URL"       # single-thread test
python -m forum_extractor run "URL" --out out --max-pages 50 --pages-per-file 1100 --delay 1 --concurrency 2
python -m forum_extractor jobs                           # list jobs
python -m forum_extractor resume JOB_ID                  # continue after Ctrl+C / crash
python -m forum_extractor retry  JOB_ID                  # retry failed threads
```

`run` options: `--threshold`, `--manual` (saved profile only), `--no-skip-duplicates`,
`--keep-quotes`, `--ignore-robots`, `--prefix`, `--timeout`, `--headful`, `--ai ollama`.
All state is kept in `./data` (change it with `--data-dir` or `FORUM_EXTRACTOR_DATA`).

## Output

One file per *N* listing pages (default 1,100), named after the listing pages it actually contains:
`Forum_Pages_0002-1101.txt`, `Forum_Pages_1102-2201.txt`, … While a batch is still being filled it
carries its planned range. If the forum ends early, the file is renamed to the real range, for
example `Forum_Pages_2202-2350.txt`. Each record looks like this:

```
================================================================================
LISTING PAGE: 2
THREAD TITLE: Day 1: starting my journey
THREAD URL: https://forum.example.com/index.php?threads/day-1-starting-my-journey.2/
STATUS: Successfully extracted
ORIGINAL POST:
Today is day one. I’m nervous but “determined” to change 🙂

My plan is to exercise every morning and to journal every evening.
================================================================================
```

Usernames, dates, signatures, quoted replies, "Click to expand…", reaction bars and similar UI
text are removed. Paragraphs, line breaks, bulleted and numbered lists (including nested ones) and
code blocks are kept verbatim. Quoted text *inside* the original post is removed by default (it is
someone else's words). Untick "Remove quoted text" to keep it. Records are written incrementally in
listing order and flushed after each record, with an fsync after each listing page. A full batch is
never held in memory.

Threads that fail extraction are **not** written to the text files. They're recorded in the database
(with the reason) and shown in the UI and the CSV export, so you can fix the selectors and retry them.

See `samples/` for real output produced from the test fixtures.

## How detection works

| Module | Responsibility |
|---|---|
| `browser.py` | Playwright/Chromium, timeouts, retries with exponential backoff (429/5xx/network), browser-crash restart, global rate limiter, robots.txt |
| `detection.py` | platform fingerprinting (including Reddit), page-type classification (listing, thread, blocked), CAPTCHA/login detection |
| `platforms.py` | data-only signatures and selectors for XenForo 1/2, phpBB, vBulletin, Discourse, NodeBB, Flarum and Vanilla |
| `listing.py` | thread-link discovery: profile, then platform selectors, then generic grouping of repeated links by DOM position and URL shape |
| `pagination.py` | next page discovery (profile, platform, `rel=next`, "Next"-style controls in many languages, numbered pagination, `?page=` inference for infinite-scroll platforms), current-page and last-page detection, loop prevention |
| `extraction.py` | original-post detection hierarchy, validation and confidence scoring |
| `textutil.py` | HTML to structured plain text |
| `storage.py` | SQLite state (jobs, pages, threads, output batches, event log) |
| `writer.py` | incremental rotating `.txt` writer |
| `engine.py` | job loop: sequential listing pages, bounded-concurrency threads, ordered writing, pause/resume/stop, confirmation, retry |
| `web/` | FastAPI app and single-page HTML UI |

**Original-post hierarchy.** Every method produces a candidate, and every candidate is validated
before it can be used:

1. the saved **site profile** (user-confirmed, or learned automatically from earlier confident extractions)
2. **known selectors** for the detected platform
3. **semantic markup**: schema.org `DiscussionForumPosting`/`Comment`, `<article>`, `post-123` ids, post or message classes
4. **structural analysis**: repeated sibling elements that look like posts (text length, link density,
   author/date metadata), tolerant of OP-only classes like `topic-owner`
5. **visible order**: explicit numbering (`data-post-number=1`, `data-index=0`, `#post_1`), otherwise document order
6. optional **local AI classifier** (Ollama). It only *chooses* among candidates that already passed
   validation and must give a reason. The text always comes from the DOM.

Validation rejects a candidate if it is empty, is the whole page, sits inside navigation or a sidebar,
is a quotation, is a preview or search snippet, contains other posts or any reply text, is mostly
links or navigation labels, comes from page 2+ of a thread, or comes from a listing page. Agreement
between independent methods raises the confidence. If the best candidate is below the threshold
(default 0.6), the thread is recorded as **failed, configure selectors manually**. Nothing is
silently written.

**Scope and safety.** Only the starting host (and its subdomains, plus any explicitly allowed
domains) is followed. robots.txt is respected by default. CAPTCHAs, Cloudflare challenges, login
walls and access denials are detected and the job stops with a clear message. They are **never
bypassed**.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                       # all tests (~4 min, includes real-browser tests)
python -m pytest -m "not browser"      # fast, no browser
python tests/fixtures/build_fixtures.py  # regenerate the HTML fixtures
python tests/fixtures/server.py 8800     # serve the fixture forums to try the UI by hand
```

The fixtures (`tests/fixtures/sites/`) imitate the real markup of 8 forum engines plus 3 custom
layouts, and all share the same 10 threads. The threads include ordered and unordered lists, quotes
of other users, a reply that quotes the entire original post, signatures, code blocks, Unicode
(accents, CJK, Cyrillic, emoji), single-line breaks, a sticky thread on every page, a thread with no
replies, a deleted thread, a flaky page that returns 503 once, a CAPTCHA page, a robots.txt-disallowed
area, and a listing with ambiguous pagination.

## Test status (what has actually been verified)

All tests below pass in this environment against **local fixtures**, using a real Chromium driven by
Playwright for the end-to-end tests.

| Layout | Detection | Thread discovery | First-post text (exact match) | Pagination | Full job |
|---|---|---|---|---|---|
| XenForo 2 | ✔ | ✔ | ✔ 10/10 | ✔ numbered + Next | ✔ |
| XenForo 1 | ✔ | ✔ | ✔ 10/10 | ✔ | ✔ |
| phpBB 3 (prosilver) | ✔ | ✔ | ✔ 10/10 | ✔ `start=` offsets | ✔ |
| vBulletin 4 | ✔ | ✔ | ✔ 10/10 | ✔ | ✔ |
| Discourse (Ember DOM) | ✔ | ✔ | ✔ 10/10 | ✔ inferred `?page=` (0-based), stops on empty page | ✔ |
| NodeBB | ✔ | ✔ | ✔ 10/10 | ✔ | ✔ |
| Flarum (JS DOM + noscript) | ✔ | ✔ | ✔ 10/10 | ✔ | ✔ |
| Vanilla 2 | ✔ | ✔ | ✔ 10/10 | ✔ `/pN` | ✔ |
| Custom table forum (generic) | generic | ✔ | ✔ 10/10 | ✔ | ✔ |
| Custom utility-class forum (generic) | generic | ✔ | ✔ 9/10 (single-post thread needs the learned profile, which the job builds automatically) | ✔ | ✔ |
| JavaScript-rendered SPA (generic) | generic | ✔ | ✔ | ✔ | ✔ |

With platform detection switched off, the generic engine alone extracts the exact original post on
99 of 100 platform fixture threads. The one remaining case is reported as low-confidence, not
guessed.

Also covered by tests: duplicate detection (by URL identity and by content hash), low-confidence
handling and retry, output rotation at a small limit, final renaming, never appending to another
job's file, stop and resume, kill-mid-page and resume without duplicate records, retry of transient
503 errors, blocking/CAPTCHA detection, robots.txt, ambiguous pagination with user confirmation,
manual-selector mode, a single thread URL as input, UTF-8 output, and the web API.

### Not verified / experimental

* **No live forum was tested.** This build environment's network policy blocks outbound access to
  forum sites (including forum.nofap.com), so every result above comes from fixtures that imitate each
  platform's markup. Real forums vary by version and theme. Always run **Analyze & preview** and a few
  **Test extraction** checks before a large job.
* The fixtures cover one representative template per platform (for example vBulletin 4, not vBulletin
  3 or 5; Discourse's Ember DOM, not the crawler view). The other variants have selectors in
  `platforms.py` but are untested.
* Infinite-scroll listings (Discourse, NodeBB, Flarum) rely on the `?page=N` parameter those
  platforms accept. This is verified against fixtures only.
* The **Ollama AI classifier** is tested with a stand-in object only. No real model was run.
* **Browse…** (native folder picker via tkinter) and **Open output folder** depend on the desktop
  environment and could not be exercised headlessly.
* Logged-in-only content is not supported by design: the app doesn't log in and doesn't bypass
  access controls.
* Rare edge case: if the process is killed in the few milliseconds between writing a record and
  committing its status, that record can appear twice after resuming.
