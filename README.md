# jobwatch

Scans ~340 hardware companies for Summer/Fall 2027 mechanical engineering student
roles, ranks them by fit + location + career trajectory, and publishes a live
dashboard on GitHub Pages. Stdlib only, no installs, no server.

## The commands

```bash
python3 jobwatch/discover.py     # add companies found in internship feeds (CI: daily)
python3 jobwatch/resolve.py      # find board tokens for every company name (once)
python3 jobwatch/watch.py        # scan the resolved boards, write docs/data.json
python3 jobwatch/watch.py --dry  # same, without writing anything
```

## Why there are two steps

Every ATS keys its public feed to a company-specific token, and there is no
directory of them. Rather than hand-maintain 340 tokens, `resolve.py` generates
candidates from each company name and probes Greenhouse, Lever, Ashby,
SmartRecruiters, Workable, Pinpoint and Workday until one responds with postings.
It caches the answer in `resolved.json`, so each company is probed once.

Every hit is checked against the board's real owner before it is saved (see
**Verification** below), so a short slug that belongs to someone else
(`greenhouse/archer` is a vet clinic) is recorded under `"rejected"` instead.
Companies with no findable board are stored as `"board": null`; `--retry`
re-probes them.

**Workday discovery.** Every tenant serves
`https://<tenant>.<host>.myworkdayjobs.com/robots.txt`, listing its career sites,
and a wrong tenant/host answers 422. The resolver tries tenant guesses across the
known `wd*` hosts and reads the sites from robots.txt. Tenants that do not follow
from the name (RTX is `globalhr`, Northrop is `ngc`) go in `WD_ALIAS` in
`resolve.py`. Some tenants list no sites in robots.txt (Thermo Fisher, Xylem) and
a wrong site name returns 200 with 0 jobs, not an error, so those are added by hand.

## Supported platforms

| board | entry keys | feed |
|---|---|---|
| greenhouse | `slug` | `boards-api.greenhouse.io/v1/boards/<slug>/jobs` |
| lever | `slug` | `api.lever.co/v0/postings/<slug>` |
| ashby | `slug` | `api.ashbyhq.com/posting-api/job-board/<slug>` |
| smartrecruiters | `slug` | `api.smartrecruiters.com/v1/companies/<slug>/postings` |
| workable | `slug` | `apply.workable.com/api/v1/widget/accounts/<slug>` |
| pinpoint | `slug` | `<slug>.pinpointhq.com/postings.json` |
| workday | `tenant`, `host`, `site` (string or list) | CXS: `POST /wday/cxs/<tenant>/<site>/jobs` |
| eightfold | `host`, `domain` | `GET <host>/api/pcsx/search?domain=<domain>` |
| oracle | `host`, `site`, `careers` | Oracle Cloud HCM `recruitingCEJobRequisitions` |
| successfactors | `base` | SAP Career Site Builder HTML search, `<base>/search/?q=` |
| jibe | `host` | `<host>/api/jobs?keywords=` |
| rippling | `slug` | `api.rippling.com/platform/api/ats/v1/board/<slug>/jobs` |
| phenom | `base` (locale root, e.g. `https://careers.mitre.org/us/en`) | `POST <origin>/widgets` refineSearch |
| listings | none | SimplifyJobs feed, for companies with no scanned board |

Any entry can also carry `"match": "<regex>"`. The scan then keeps only postings
whose title, location or body match it. This covers companies listed on a
parent's board: Fluke and Tektronix (Fortive, Ralliant), Instron (ITW), Abiomed
(J&J), Capella (IonQ), Motiv (Rocket Lab), Astrobotic (Voyager), Pfeiffer (Busch).
Subsidiaries whose parent is already scanned in full use `"covered_by"` instead.

Workday notes, all checked against live tenants: `limit` is capped at 20,
`total` is only filled on the first page (0 afterwards), and `searchText` is
fuzzy ("intern" matches nearly every req at Micron). So the adapter selects
student reqs with the tenant's own facets (worker type "Intern", job family
"Interns", and so on), falls back to `searchText` only when no such facet exists,
then fetches each matching job for its description, location and start date.

Eightfold, Oracle, SuccessFactors, Jibe, Phenom, Rippling and SmartRecruiters
cannot filter to student reqs server side either (or carry no description in the
list); each lists broadly, keeps intern/co-op titles, then reads detail only
for those. Eightfold pages are fixed at 10 and Microsoft's tenant returns 429 when
pushed, so its pages are fetched one at a time with backoff.

**Adding a company by hand.** Open the careers page, find the feed behind it, and
add the entry to `resolved.json` with `"verified": true`. The resolver never
overwrites an entry that already has a board, so hand edits are safe.

```
https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
        ^tenant ^host                     ^site
```
```json
"NVIDIA":        {"board":"workday","tenant":"nvidia","host":"wd5",
                  "site":"NVIDIAExternalCareerSite","trajectory":19,"verified":true},
"Lam Research":  {"board":"eightfold","host":"careers.lamresearch.com",
                  "domain":"lamresearch.com","trajectory":16,"verified":true},
"Texas Instruments": {"board":"oracle","host":"edbz.fa.us2.oraclecloud.com","site":"CX",
                  "careers":"careers.ti.com","trajectory":15,"verified":true},
"Teradyne":      {"board":"successfactors","base":"https://jobs.teradyne.com",
                  "trajectory":14,"verified":true},
"AMD":           {"board":"jibe","host":"careers.amd.com","trajectory":15,"verified":true}
```

## Verification

```bash
python3 jobwatch/verify.py          # report
python3 jobwatch/verify.py --fix    # null out SUSPECT, STALE and DUP entries
```

Each platform is asked who owns the board from a field that does not echo the
slug back. For Lever that is the hosted page's `<title>` and company-website link;
for Ashby it is the organisation name and `publicWebsite` from the hosted board's
GraphQL, falling back to a posting description when the board is embedded on the
company's own site. Greenhouse, SmartRecruiters and Workable report a company name,
Workday reports `hiringOrganization` on postings. Verdicts are OK, WEAK (one-word
name, look once and set `"verified": true`), SPARSE (1-2 postings, usually a stub),
STALE (newest posting over a year old), SUSPECT and DUP. Entries with
`"verified": true` are skipped.

Workday hiring organisations are legal entities ("1000 KLA Corporation",
"The Charles Stark Draper Laboratory, Inc."), which the strict one-word rule
flags as SUSPECT, so every Workday entry here was checked by hand and carries
`"verified": true`.

## Setup

1. Push this repo. Private is fine.
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write.** The
   scan commits `docs/data.json` back to the repo and fails without it.
4. **Actions → jobwatch → Run workflow.** First run seeds state silently.
5. Dashboard lives at `https://<user>.github.io/<repo>/`.

After that it scans every 20 minutes, around the clock, every day, because
postings go up at midnight and on weekends too. GitHub's scheduler alone cannot
deliver that: it dropped all but 2-4 of the 72 daily slots here. So each run ends
with a `next` job that waits until 20 minutes after the run started and then
dispatches the workflow again (`GITHUB_TOKEN` is allowed to trigger
`workflow_dispatch`). It does so even when the scan failed, and skips the
dispatch when another run is already queued or running, so there is only ever one
chain. The `8,28,48 * * * *` schedule stays as a backup that restarts the chain if
it ever stops. Runner minutes are free on a public repo.

The dashboard reads `data.json` straight from the repo
(`raw.githubusercontent.com/<user>/<repo>/main/docs/data.json`, cached about 5
minutes), so it is fresh after every scan without republishing Pages. Pages
refuses deploys when asked every 20 minutes ("Deployment failed, try again
later"), so it is republished only when `docs/index.html` changed in the last 2
hours, when a person started the run, or once every 6 hours; a refused deploy is
retried once and never fails the run. The dashboard banner flags data older than
2 hours.

To pause the loop, add a repository variable `JOBWATCH_LOOP` = `off`
(Settings, Secrets and variables, Actions, Variables), or disable the workflow in
the Actions tab. Delete the variable and run the workflow once to restart it.

The first run of each day (any run more than 20 hours after the last discovery)
also runs discovery and probes up to 20 new company names before scanning.
Schedules only run from the default branch.

**Keep the workflow file's last edit under your own account.** GitHub ties a
schedule to whoever last changed it; when that commit's email is not linked to a
GitHub account the runs show actor `ghost` and the schedule never fires. Edit
`.github/workflows/jobwatch.yml` in the GitHub web editor, or from a machine
whose git email is on your account.

**When something breaks you hear about it.** `watch.py --ci` writes every board
error to the job summary and as an Actions warning, and exits non-zero when more
than a quarter of boards fail or nothing was scanned, which turns the run red and
triggers GitHub's failure email. The dashboard shows board errors and a stale-data
warning (dashboard data over 2 hours old; scans run every 20 minutes) in a banner at the top of the page.

## Growing the company list

`discover.py` reads the community-maintained
[SimplifyJobs Summer 2027 internships](https://github.com/SimplifyJobs/Summer2027-Internships)
feed (active, hardware category, Summer or Fall 2027) and works out each
posting's board from its apply URL: Greenhouse, Lever, Ashby, SmartRecruiters,
Workable, Pinpoint, Workday, Eightfold, Oracle and SuccessFactors URLs carry
everything an adapter needs. A board that is already scanned under any name is
skipped; a new one is probed once and saved to `resolved.json` with
`"source": "simplify"`, and the company is appended to `companies.txt` at
trajectory 11. Companies on sites with no adapter (iCIMS, Taleo, their own
domain) are appended to `companies.txt` too, so `resolve.py` tries to find a
board for them by name.

Until a company has a scanned board, its feed postings still reach the
dashboard through the **Internship feed (unscanned companies)** pseudo-board:
title, term, location and link, no description, so they rank on title,
location and company trajectory. Apple, Google and Amazon postings arrive this
way.

To add a company by hand, append to `companies.txt`:

```
Company Name|14
```

The number is trajectory, 1 to 20 — comp, scale, and how much a term there moves
your career. Run `resolve.py` again and it probes only the new names.

## Ranking

Four gates run before anything is scored:

- **Student req.** The title says intern, internship, co-op, student or
  undergraduate.
- **Summer or Fall 2027.** Accepted however it is written: "Summer 2027",
  "2027 Summer Intern", "Summer '27", "Summer/Fall 2027", "FY27 Intern",
  "(May - Aug 2027)", "(June - Dec 2027)", "2027 Internship - ...". Spring and
  winter terms, January-to-summer ranges, and 2025/2026/2028 terms are dropped.
  A posting that names no term or year at all is kept if it went up after
  2026-07-01, marked **term not stated** on the dashboard and docked 3 points.
- **Mechanical discipline.** Titles naming another discipline (software,
  electrical, RF, ASIC/FPGA/VLSI, silicon, design verification, finance, civil,
  quant trading, ...) are dropped unless the title also says mechanical or
  mechatronics, so "Hardware Engineering Intern (Mechanical & Electrical)" stays.
  The posting also needs a mechanical word in its title or body.
- **Open to undergraduates.** Dropped when the title names only a graduate degree
  (MS, PhD, Graduate, master's, MBA) with no BS/undergrad alongside it
  ("Bachelor's/Master's" stays), or when a requirement sentence asks for graduate
  enrollment ("Pursuit of an advanced degree (MS or PhD)", "Current enrollment in
  a full-time Master's or PhD program", Microsoft's "fellow doctoral candidates")
  and the posting never says undergraduates are eligible. "Masters and PhD
  students are also eligible" and pay lines by degree do not count.
- **Not a software / data / AI role.** Titles in software, firmware, embedded,
  data, AI/ML, computer vision, autonomy, algorithms, research science, web/UI,
  IT and similar are dropped unless the title also says mechanical
  ("Mechanical Engineer, Autonomy Hardware" stays). "AI&T" (assembly,
  integration and test) is not read as AI.
- **In the US.** Dropped when every listed location is abroad, Canada included,
  since a work permit would not come through in time. Each location in a
  multi-location posting is judged on its own, so "Boston, MA; Toronto, ON" stays,
  and a location that names no country ("Waltham Office") counts as possibly US.
  Workday locations carry the country from the posting ("Manching, Germany").

Then the weighted components:

| Component | Cap | What it measures |
|---|---|---|
| fit | 60 | title and body keywords from the resume and portfolio: test stands and fixtures, mechanisms and actuators, GD&T and tolerance stacks, P&IDs and leak testing, DFM/DFA, machine vision and hardware-software integration. Product design, mechanical design and hardware engineering titles weigh most because they lead to the best-paid full-time offers; quality and sustaining titles still count, at lower weight. Return or full-time offer language +10. Technician, inspector, operator and assembler titles are penalised, as are unpaid or for-credit roles |
| pay | +8 / -4 | the undergrad pay the posting states, as USD per hour (annual / 2080, monthly / 173, weekly / 40, CAD/EUR/GBP converted): +8 at $55/hr and up, +6 at $48, +4 at $42, +2 at $37, +1 at $33, 0 at $29, -2 at $25, -4 below. Pay orders comparable roles; it is kept small so a high-paying weak fit cannot jump a strong one. Read from "$35 - $44 per hour", "MIN $ 32.03 - MAX $ 51.43", "Pay Range (USD) 33 - 66, $ per hour", "weekly pay range: $1,300 — $1,650", and "rates begin at $24/hour" (taken as $24-36). Ranges up to 3x wide are used; company-wide scales wider than that ("$17 - $98") count as unstated. **No pay in the posting** borrows the median of the same company's stated ranges from the same scan, and failing that a typical rate for the company's trajectory ($52/hr at 20 down to $28/hr), whose points are capped at +2. Estimates show as "~$40/hr est." on the card |
| location | 8 | Boston area and the Bay Area / Seattle tech hubs highest, NYC next, LA, San Diego and Austin after, remote -6; postings only abroad are filtered out (see the gates). The best listed location wins, so "Boston; Bangalore" scores as Boston |
| trajectory | 20 | per company, from `companies.txt`: expected pay (intern rate and the full-time offer it leads to) first, then scale and career path. Big tech and semiconductor equipment at the top, industrial fluid-component makers at 9 |
| freshness | +12 / -8 | age of the posting: +12 within 2 days, +9 within 5, +6 within 10, +3 within 3 weeks, 0 to 5 weeks, -4 to 60 days, -8 after |

Penalties that misfire on boilerplate are matched narrowly: "unpaid intern",
"academic credit only", and "volunteer" only in the title (equal-opportunity text
mentions protected volunteer status). Postings that reach the dashboard only
through the internship feed have no description, so they get 14 fit points in
place of body keywords (the typical body score is about 19) and are marked
"description not fetched". A company scanned under two names ("KLA" and "KLA
Corporation", "Shield AI (ashby)") uses the main entry's trajectory, and the same
posting found twice is shown once.

Tier A at 50 and a fit of at least 30 (company weight and pay alone cannot make
an A), tier B at 32, everything below is tier C. A title that names a core
mechanical role (mechanical, manufacturing / design / test / quality / process
engineering, product design, reliability, structures, propulsion, thermal, fluid,
hardware or systems engineering, a plain "Engineering Intern", ...) is never left in
tier C: it is lifted to B, so a relevant role at a good company with a generic
description still shows by default; its score still sets the order. Tier C is kept,
not dropped: the dashboard's **C** chip (off by default) shows the rest. Titles that
are not engineering at all (purchasing, outreach, analytics, UX, customer success,
admin, technician, SkillBridge, ...) are filtered out. Tiers use fit + pay + location + trajectory, so an
older strong fit is never hidden. Freshness is added on top for ranking, so a
good fit posted this week sorts above an equally good one that has been up for
two months and is likely saturated. The steps live under `freshness` in
`scoring.json`.

Age is measured from when the posting went live, as the board reports it
(Greenhouse `first_published`, Lever `createdAt`, Workday `startDate`, and so
on, normalised to a date). Boards that give no date (Pinpoint) fall back to
when jobwatch first picked the posting up.

`companies.txt` is the only place to edit trajectory; the scan reads it on every
run. Every card shows the stated pay (or "pay not stated"), and the dashboard can
sort by highest stated pay.

The dashboard shows both dates on every card: **Posted** (from the board) and
**first picked up by jobwatch** (from `seen.json`). A newly added board's
postings all show as picked up today, which is why the posted date drives the
ranking. Filter by newness (last 24 hours, 3 days, 7 days, 2 weeks, 30 days,
or older than 30 days) and sort by fit + freshness, best fit ignoring age,
newest posted, newest found, or company. The sort and newness choices are
remembered per browser.

## Tracking what you do with each posting

Every row has three buttons and a note field:

| Mark | What it does |
|---|---|
| **Applied** | moves it to the Applied tab, green left border, stamped with the date |
| **Save for later** | Saved tab — interested, not acted on yet |
| **Not relevant** | Not relevant tab, dimmed. Nothing is deleted, it just leaves your open list |

Clicking the same button again clears the mark. The **Open** tab shows only what
you have not touched, so it stays a genuine to-do list instead of growing forever.
Tab headers carry live counts.

The note field saves as you type — use it for "waiting on Taylor", "needs
cover letter", "asked about relocation".

**Syncing marks across devices.** Marks and notes are saved in the browser, and
with sync turned on they are also kept in a secret GitHub gist that every device
reads and writes. The repo is public, so they are deliberately not stored in it.
Set it up once per device:

1. GitHub → Settings → Developer settings → Fine-grained tokens → Generate new
   token. Under **Account permissions** set **Gists** to **Read and write**.
   Leave everything else, including repository access, at none.
2. On the dashboard, press **Sync across devices** and paste the token. The first
   device creates a secret gist named "jobwatch status (private)"; the others
   find it and merge into it.

Each posting keeps whichever change is most recent, a cleared mark included, so
un-marking on the laptop also clears it on the phone. The dashboard syncs on
load, a moment after each change, and whenever the tab comes back into view; the
time of the last sync, or the error, shows next to the button. The token is
stored only in that browser. **Export status** / **Import status** still work as
a manual backup.

## Nothing is silently lost

Keyword gates miss oddly-titled roles. Anything that was a real student req in
the right season, and got rejected only on discipline or score, lands in the
dashboard's **review queue** with the reason attached. It is filtered to postings
whose body shows some mechanical signal, so it stays short enough to scan.

If the same kind of posting keeps appearing there, add a keyword to
`scoring.json` rather than loosening the gates.

## Tailored drafts

```bash
python3 jobwatch/tailor.py --top 5        # drafts for the 5 highest-scoring jobs
python3 jobwatch/tailor.py --min-score 65
python3 jobwatch/tailor.py --id gh:spacex:1234
```

Writes `drafts/<company>-<role>.md`, one per posting. Each file has four parts:

1. **What this posting asks for** — every requirement it recognised, with the
   words that triggered it. This alone is worth the run; it tells you what to
   lead with before you write anything.
2. **Things to check before claiming** — NX, FEA, CFD, machine tools, cryo,
   combustion, clearance. Flagged, not apologised for. Do not volunteer a gap.
3. **Blocks used** — which of your paragraphs got selected and why.
4. **The draft** — your letterhead, the role line, and the assembled body.

### Fully automatic mode

Set an API key and every new high-scoring posting gets a written letter without you
asking:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 jobwatch/tailor.py --llm --new-only --min-score 55
```

In CI, add `ANTHROPIC_API_KEY` as a repo secret. The workflow then runs this after
every scan, so a posting that appears at 2pm has a draft in `drafts/` by 2:20pm,
committed to the repo. Without the secret the step is skipped and nothing breaks.

The model is held on a short leash:

- **Closed source material.** It may only use `blocks.json` and `facts.md`. It is told
  explicitly to invent no numbers, no projects, no coursework, no tools.
- **Voice rules in the system prompt.** No em dashes. No "excited to apply". No
  "passionate", "leverage", "thrilled". No not-X-but-Y. Short declaratives.
- **Never volunteer a gap.** If the posting wants NX or combustion experience, the
  letter stays silent rather than pre-apologising.
- **Automatic lint.** Generated text is scanned for banned words and dashes. Anything
  it finds is printed at the top of the draft as **Voice check flagged**.
- **Graceful fallback.** No key, rate limit, or API error, and it assembles from blocks
  instead and says so in the file.

`facts.md` is the ground truth the model is allowed to draw on. It also lists what must
never appear in writing: Parker flow rates, pressures, leak rates, tolerances, part
numbers.

**Still read it before sending.** The model can be fluent and wrong in the same
sentence, which is the exact failure you have caught before. Thirty seconds of reading
is the price of not sending something that misstates your own work.

### Block mode: it assembles, it does not write

`blocks.json` holds paragraphs you have already approved, in your voice: the
endurance tester build, the check valve diagnosis, the leak fixture trade, the
hypochlorite program, the fixtures, the Braille cell and its failure analysis,
the pan/tilt tracker, Melagen. Each block has trigger words and a weight. The
tool picks the five that best match the posting and orders them by relevance.

Nothing is generated, so nothing can be invented and nothing can drift into
sounding like a machine wrote it. The cost is that the draft is generic where the
posting is generic — the closing paragraph in particular is a placeholder you
should replace with something specific to that company.

**Treat it as a 15-minute head start, not a send-ready letter.** Cut any block
that does not earn its place; three strong paragraphs beat five adequate ones.

### Adding a block

Edit `blocks.json`:

```json
"new_thing": {
  "triggers": ["keyword", "another"],
  "weight": 12,
  "text": "One paragraph, written by you, in your voice."
}
```

Weight is how strongly it competes when triggered. Write it the way you would
say it out loud.

## Honest limits

- About 50 companies have no feed this scanner reads. Each `"board": null` entry
  in `resolved.json` says why under `rejected`: no public feed (Apple, Google,
  Meta, Tesla, Parker, Safran) or an ATS without an adapter yet (iCIMS: Waters,
  Joby, GDMS, Bruker, ITT; Amazon's own JSON: Amazon Robotics, Lab126;
  BrassRing: General Atomics; Taleo: Bell; ADP, Paycor, Cornerstone and the new
  SuccessFactors JSON UI: Atlas Copco, Edwards, Grundfos). Check those by hand.
- Workable rate-limits by IP (429). The runner's IP is fresh each run, so this
  mostly bites when probing a lot from one machine.
- Microsoft's Eightfold tenant rate-limits. A scan that hits it too hard gets 429s,
  which are retried with backoff but can still surface as a board error.
- GitHub Actions cron drifts. Twenty minutes is a request, not a promise.
- Season filtering depends on the posting naming the season. A req that says
  "Summer '27" is dropped. Run `--explain` against a quiet board before assuming
  nothing was posted.
- Scores rank, they do not judge. A tier B at a company you want beats a tier A
  at one you do not.
