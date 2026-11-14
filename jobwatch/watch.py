#!/usr/bin/env python3
"""
jobwatch — scan every resolved company board, keep Summer/Fall 2027 mechanical
student roles, rank by fit + location + trajectory, publish docs/data.json.
"""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import ADAPTERS

ROOT     = os.path.dirname(os.path.abspath(__file__))
REPO     = os.path.dirname(ROOT)
RESOLVED = os.path.join(ROOT, "resolved.json")
SCORING  = os.path.join(ROOT, "scoring.json")
STATE    = os.path.join(ROOT, "seen.json")
OUT      = os.path.join(REPO, "docs", "data.json")
WORKERS  = 8
FAIL_FRACTION = 0.25   # --ci exits 1 when more than this share of boards error

# Seasons. Postings say it every way: "Summer 2027", "2027 Summer Intern", "Summer '27",
# "Summer/Fall 2027", "FY27 Intern" (Qualcomm), "(May - Aug 2027)", "(June - Dec 2027)",
# "2027 Internship - ...", or nothing at all.
_Y   = r"(?:20)?27"
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
SEASON_OK = re.compile(
    rf"\b(?:summer|fall|autumn)\b[\s/&,+-]*(?:and\s+|or\s+)?(?:(?:summer|fall|autumn)\b[\s/&,+-]*)?['’]?{_Y}\b"
    rf"|\b2027\b[\s/&,:–-]*(?:summer|fall|autumn)\b"
    rf"|\bFY\s?-?27\b"
    rf"|\b(?:may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(?:{_Y}\s*)?(?:-|–|to|through)\s*{_MON}\s*,?\s*2027", re.I)
# ranges that start in Jan-Mar are spring co-ops even when they run into summer
SEASON_SPRING_RANGE = re.compile(rf"\b(?:jan|feb|mar)[a-z]*\.?\s*(?:-|–|to|through)\s*{_MON}\s*\)?\s*,?\s*2027", re.I)
SEASON_BAD = re.compile(
    r"\b(?:spring|winter)\b[\s/&,+-]*['’]?(?:20)?27\b|\b2027\b[\s/&,:–-]*(?:spring|winter)\b"
    r"|\b(?:summer|fall|autumn|spring|winter)\b[\s/&,+-]*['’]?(?:20)?(?:25|26|28)\b"
    r"|\b20(?:25|26|28)\b[\s/&,:–-]*(?:summer|fall|autumn|spring|winter|intern|co-?op)", re.I)
SEASON_ANY_YEAR = re.compile(r"\b20(?:2[5-9])\b|\bFY\s?-?2\d\b|['’]2[5-9]\b")
# undated postings are assumed to be for 2027 only if they went up in this recruiting cycle
SEASON_CYCLE_START = "2026-07-01"
STUDENT = re.compile(r"\b(intern|interns|internship|co[-\s]?op|student|undergrad|undergraduate)\b", re.I)
ME_WORD = re.compile(r"mechanic|mechatron", re.I)
# grad-only reqs: a grad degree in the title with no BS/undergrad alongside it, or a body that
# requires grad enrollment and never mentions a bachelor's or undergraduates
GRAD_TITLE = re.compile(r"(?<!under)\bgraduate\b|\bmasters?\b|master['’]s|\bm\.s\.|\bms\b|\bph\.?\s?d\b|doctoral|\bmba\b", re.I)
UG_TITLE   = re.compile(r"undergrad|bachelor|\bb\.?s\.?\b|\bbs\b", re.I)
# a requirement sentence that asks for grad enrollment ("Pursuit of an advanced degree (MS or
# PhD)", "Current enrollment in a full-time Master's or PhD program"), as opposed to "Masters and
# PhD students are also eligible" or a pay line "based on pursuit of a Ph.D"
GRAD_REQ   = re.compile(r"(?:must be|required|requirements?|qualifications?|current(?:ly)? enrolled|current enrollment|"
                        r"pursuit of|pursuing|enrolled in|candidate for)[^.;•\n]{0,60}?"
                        r"(?:master['’]?s?\b|\bm\.?s\.?\b|ph\.?\s?d|doctoral|advanced degree|graduate (?:degree|program|student))", re.I)
GRAD_HINT  = re.compile(r"fellow doctoral candidates|we are looking for graduate students|"
                        r"open (?:only )?to (?:master['’]?s|ph\.?d)|for (?:master['’]?s|ph\.?d) students", re.I)
GRAD_OK    = re.compile(r"\balso\b|eligible|considered|additional|bachelor|undergrad|\bb\.?s\.?\b|\bbs\b|"
                        r"per hour|hourly|\$|\bpay\b|compensation|preferred|a plus", re.I)
UG_ANY     = re.compile(r"(?:pursuing|enrolled in|working toward|candidate for|current)[^.;\n]{0,50}"
                        r"(?:bachelor|undergraduate|b\.s\.|\bbs\b)|undergraduate students|sophomores?|juniors?\b|rising senior", re.I)
# titles in software, data, AI/ML, autonomy, research science and IT; a mechanical word in the
# title keeps it ("Mechanical Engineer, Autonomy Hardware"); "AI&T" (assembly, integration
# and test) is not AI
SOFT_TITLE = re.compile(r"software|firmware|embedded|\bdata\b|\bai\b(?!\s*&)|machine learning|deep learning|"
                        r"computer vision|\bcv\b|autonomy|algorithm|front.?end|back.?end|full.?stack|developer|devops|"
                        r"\bcloud\b|quantum|reinforcement|\bagents?\b|digitali[sz]ation|digital product|test automation|"
                        r"\bml\b|\bllm|perception|research intern|research scientist|applied scientist|\bit\b|"
                        r"information technology|cyber|aiops|\bweb\b|\bui\b|\bux\b|app(?:lication)? development", re.I)
# a US location in any of the usual shapes: "Boston, MA", "US-CA-Santa Clara", "USA",
# "United States", or a state name
US_LOC = re.compile(
    r"\bU\.?S\.?A?\b|\bUS-|"
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
    r"OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")
US_LOC_NAMES = re.compile(
    r"united states|\b(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|hawaii|idaho|"
    r"illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|"
    r"oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|"
    r"washington|wisconsin|wyoming)\b", re.I)
# "Toronto, ON, CA": a Canadian province code makes the trailing CA the country, not California
CA_PROVINCE = re.compile(r",\s*(?:ON|QC|BC|AB|MB|NS|NB|SK|NL|PE|PEI)\b")


def us_location(loc):
    return not CA_PROVINCE.search(loc) and bool(US_LOC.search(loc) or US_LOC_NAMES.search(loc))


def abroad_only(loc, r):
    """True when no listed location is in the US and at least one is abroad. Any US
    part keeps the posting ("Boston, MA; Toronto, ON")."""
    parts = [p_.strip() for p_ in re.split(r";|\||\bor\b|\n| / ", loc) if p_.strip()]
    if any(us_location(p_) for p_ in parts):
        return False
    # "Bolton, United Kingdom; Stevenage": a part naming no country is in the same country
    # as the parts that do; a string naming no country at all ("Waltham Office") stays
    return any(_non_us(p_, r) or CA_PROVINCE.search(p_) for p_ in parts)


def season(title, body):
    """"ok" when the posting states a Summer/Fall 2027 term, "bad" when it states some
    other term, "unstated" when it names no term or year at all."""
    blob = f"{title} {body}"
    for text in (title, blob):
        if SEASON_SPRING_RANGE.search(text) and not re.search(r"summer|fall|autumn", text, re.I):
            return "bad"
        if SEASON_OK.search(text):
            return "ok"
        if text is title:
            if SEASON_BAD.search(title) or re.search(r"\b(?:jan|feb|mar)[a-z]*\.?\s+2027\b", title, re.I):
                return "bad"                                 # "January 2027 Co-op" is spring
            if re.search(r"\b2027\b", title) and not re.search(r"spring|winter", title, re.I):
                return "ok"                                  # "2027 Internship - Hardware"
    if re.search(r"\b2027\b", body):
        return "ok"
    if SEASON_BAD.search(body) or SEASON_ANY_YEAR.search(blob):
        return "bad"
    return "unstated"


def _sentences_with(rx, text):
    """The sentence around each match of rx (split on . ; • and newlines)."""
    out = []
    for m in rx.finditer(text or ""):
        a = max(text.rfind(c, 0, m.start()) for c in ".;•\n") + 1
        ends = [i for i in (text.find(c, m.end()) for c in ".;•\n") if i >= 0]
        out.append(text[a:min(ends) if ends else len(text)])
    return out


_NON_US_RX = {}


def _non_us(loc, r):
    """Whole-word match against non_us_locations ("india" must not hit Indianapolis)."""
    key = id(r)
    if key not in _NON_US_RX:
        words = sorted(r.get("non_us_locations", []), key=len, reverse=True)
        _NON_US_RX[key] = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(w.strip()) for w in words) + r")(?![a-z])", re.I) \
            if words else None
    rx = _NON_US_RX[key]
    return bool(rx and rx.search(loc))


def gate(j, r):
    t, b = j["title"], j["body"]; tl, blob = t.lower(), f"{t} {b}"
    if not STUDENT.search(t):                                   return "not a student req"
    j["season"] = season(t, b)
    if j["season"] == "bad":
        return "off-target season in title" if SEASON_BAD.search(t) or SEASON_SPRING_RANGE.search(t) \
            else "no Summer/Fall 2027"
    if j["season"] == "unstated" and j.get("posted") and j["posted"] < SEASON_CYCLE_START:
        return "no Summer/Fall 2027"
    if GRAD_TITLE.search(t) and not UG_TITLE.search(t):        return "master's/PhD only"
    if not UG_ANY.search(blob) and (GRAD_HINT.search(b) or any(
            not GRAD_OK.search(sent) for sent in _sentences_with(GRAD_REQ, b))):
                                                                return "master's/PhD only"
    if SOFT_TITLE.search(t) and not ME_WORD.search(t):         return "software / data / AI role"
    loc = j.get("location") or ""
    if loc and abroad_only(loc, r):
        return "outside the US"
    # "Hardware Engineering Intern (Mechanical & Electrical)" is still a mechanical role
    if any(x in tl for x in r["exclude_title"]) and not ME_WORD.search(t):
                                                                return "non-ME discipline"
    if not any(x in tl for x in r["me_title_any"]) and \
       not any(x in b.lower() for x in r["me_body_any"]):       return "no mechanical signal"
    if any(x in blob.lower() for x in r["hard_blockers"]):      return "degree-level blocker"
    return None


WD_AGO = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def posted_date(v, now):
    """Normalise whatever a board calls its posting date to YYYY-MM-DD, or "".
    Handles ISO strings with any offset, epoch seconds/ms (Lever) and Workday's
    "Posted 3 Days Ago" / "Posted 30+ Days Ago"."""
    if v in (None, ""):
        return ""
    try:
        if isinstance(v, (int, float)) or str(v).isdigit():
            n = float(v)
            dt = datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc)
        else:
            m = WD_AGO.search(str(v))
            if m:
                ago = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" \
                    else int(m.group(2))
                dt = now - timedelta(days=ago)
            else:
                dt = datetime.fromisoformat(str(v).strip().replace(" UTC", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return ""
    return min(dt, now).astimezone(timezone.utc).strftime("%Y-%m-%d")


def freshness(posted, first_seen, now, r, board_first=""):
    """(points, age in days, basis) — basis is "posted" or "found".
    An undated posting picked up on its board's first scan has an unknown age (it
    may have been up for months), so it scores "unknown" rather than brand new."""
    f = r.get("freshness") or {}
    when, basis = (posted, "posted") if posted else (first_seen, "found")
    if not posted and board_first and first_seen <= board_first:
        return f.get("unknown", 0), None, "unknown"
    try:
        dt = datetime.fromisoformat(when)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # the posted date wins when the board gives one: a newly added board
        # reports weeks-old postings as found today
        age = max(0.0, (now - dt).total_seconds() / 86400)
    except (TypeError, ValueError):
        return f.get("unknown", 0), None, basis
    pts = f.get("older", 0)
    for limit, p in f.get("steps", []):
        if age <= limit:
            pts = p
            break
    return min(pts, r["caps"].get("freshness", pts)), round(age, 1), basis


# pay: "$35 - $44 per hour", "$28 — $40 USD The salary range", "$85,000.00 - $122,200.00 / year",
# "MIN $ 32.03 - MAX $ 51.43", "Expected Base Pay Range (USD) 33 - 66, $ per hour",
# "The weekly pay range for this role is: $1,300 — $1,650 USD"
_NUM = r"\$\s?(\d[\d,]*(?:\.\d+)?)(?:\s?([kK])(?![A-Za-z]))?"     # "$200K", not "$51.43 Keysight"
PAY_RE = re.compile(_NUM + r"(?:\s?(?:-|–|—|to)\s?(?:max(?:imum)?\s*:?\s*)?(?:USD\s?)?" + _NUM + r")?", re.I)
# ranges written without a dollar sign, introduced by a pay phrase or "(USD)"
PAY_RE_BARE = re.compile(r"(?:\(USD\)|\bUSD\b|pay range|pay rate|hourly rate|hourly range|wage range)"
                         r"[^0-9$]{0,25}(\d{2,3}(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d{2,3}(?:\.\d+)?)\b", re.I)
PAY_UNIT = [(re.compile(r"per hour|/\s?h(?:ou)?r|an hour|hourly|\bhr\b", re.I), 1.0),
            (re.compile(r"per week|/\s?w(?:ee)?k|weekly", re.I), 1 / 40),
            (re.compile(r"per month|/\s?mo(?:nth)?\b|monthly", re.I), 1 / 173),
            (re.compile(r"per year|/\s?y(?:ea)?r|annual|a year|salary of|\bbase salary", re.I), 1 / 2080)]
PAY_CTX = re.compile(r"pay|salary|compensation|wage|rate|usd|range|\bmin\b|\bmax\b", re.I)
PAY_GRAD = re.compile(r"master|ph\.?d|doctoral|\bgrad(?:uate)?\b(?! from)", re.I)
PAY_UG = re.compile(r"undergrad|bachelor|\bbs\b|b\.s\.", re.I)
FX = [(re.compile(r"\bCAD\b|C\$"), 0.72), (re.compile(r"€|\bEUR\b"), 1.08), (re.compile(r"£|\bGBP\b"), 1.27)]
PAY_MAX_SPREAD = 3.0     # "$18 - $48/hr" is usable; "$17.00 - $98.00 broad, minimum to maximum" is not


def _pay_matches(text):
    """(start, end, [values], unit multiplier or None) for every number or range."""
    for m in PAY_RE.finditer(text):
        vals = [float(v.replace(",", "")) * (1000 if k else 1)
                for v, k in ((m.group(1), m.group(2)), (m.group(3), m.group(4))) if v]
        yield m.start(), m.end(), vals
    for m in PAY_RE_BARE.finditer(text):
        yield m.start(1), m.end(), [float(m.group(1)), float(m.group(2))]


def pay_rate(text):
    """(low, high) USD/hour from the pay ranges a posting states, or None.
    Benefit amounts ($850 commute credit) are skipped by requiring a unit or a
    pay word next to the number. When undergrad and grad rates are both listed,
    the undergrad ones are used; when every range carries a grad label (Northrop:
    "Undergraduate (includes students ... Master's program): $18.50 to $33.50"),
    the lowest range is taken as the undergrad one."""
    text = text or ""
    ug, other, grad = [], [], []
    for start, end, vals in _pay_matches(text):
        after, before = text[end:end + 45], text[max(0, start - 60):start]
        mult = next((f for rx, f in PAY_UNIT if rx.search(after)), None)
        if mult is None:                                   # "weekly pay range ...: $1,300"
            mult = next((f for rx, f in PAY_UNIT if rx.search(before[-45:])), None)
        if mult is None:
            if not PAY_CTX.search(after + before):
                continue
            mult = 1 / 2080 if min(vals) >= 20000 else 1.0     # bare range: infer from size
        fx = next((f for rx, f in FX if rx.search(after + before[-10:])), 1.0)
        if len(vals) == 1 and re.search(r"(?:begin|begins|start|starts|starting)\s+at|starting\s+(?:from|rate)|"
                                        r"minimum of|as low as|from\s*$", before[-30:], re.I):
            vals = [vals[0], vals[0] * 1.5]        # "Pay rates ... begin at $24/hour and increase"
        hr = [v * mult * fx for v in vals]
        if not all(15 <= h <= 200 for h in hr):
            continue
        # a rate's own label is the nearest one, after ("$41/hr for Undergrad") or
        # before ("Undergraduate: $30/hour")
        a, b = after[:30], before[-30:]
        labels = [(m.start(), kind) for kind, rx in (("ug", PAY_UG), ("grad", PAY_GRAD))
                  for m in rx.finditer(a)] + \
                 [(len(b) - m.end(), kind) for kind, rx in (("ug", PAY_UG), ("grad", PAY_GRAD))
                  for m in rx.finditer(b)]
        kind = min(labels)[1] if labels else None
        own = re.match(r"[^$]{0,12}?\bfor\s+(?:the\s+)?", a)    # "$53/hr for Graduate"
        if own:
            tail = a[own.end():own.end() + 14]
            kind = "ug" if PAY_UG.match(tail) else "grad" if PAY_GRAD.match(tail) else kind
        (ug if kind == "ug" else other if kind is None else grad).append(hr)
    ranges = ug or other or ([min(grad, key=min)] if grad else [])
    vals = [v for r_ in ranges for v in r_]
    if not vals or max(vals) > PAY_MAX_SPREAD * min(vals):
        return None
    return (round(min(vals), 2), round(max(vals), 2))


def pay_points(rate, r):
    """Points for the midpoint hourly rate; 0 when the posting states no pay."""
    p = r.get("pay") or {}
    if not rate:
        return p.get("unknown", 0)
    mid = sum(rate) / 2
    for floor, pts in p.get("steps", []):
        if mid >= floor:
            return pts
    return p.get("below", 0)


def pay_estimate(company_rate, traj, r):
    """(low, high, source) for a posting that states no pay: the company's own
    stated rates from this scan first, else a typical rate for its trajectory."""
    if company_rate:
        return (*company_rate, "company")
    table = (r.get("pay") or {}).get("estimate_by_trajectory") or {}
    keys = sorted((int(k) for k in table), reverse=True)
    for k in keys:
        if traj >= k:
            v = table[str(k)]
            return (v, v, "trajectory")
    return None


def score(j, traj, r, fresh=(0, None, ""), company_rate=None):
    tl, bl, ll = j["title"].lower(), j["body"].lower(), j["location"].lower()
    fit, why = 0, []
    for kw, p in r["title_weights"].items():
        if kw in tl: fit += p; why.append([f"title: {kw}", p])
    for kw, p in r["body_weights"].items():
        if kw in bl: fit += p; why.append([f"body: {kw}", p])
    for kw, p in r["penalties"].items():
        if kw in bl or kw in tl: fit += p; why.append([kw, p])
    for kw, p in r.get("title_penalties", {}).items():     # "technician" in a body is just a coworker
        if kw in tl: fit += p; why.append([f"title: {kw}", p])
    for rx, p in r.get("title_penalty_patterns", {}).items():  # grad-only reqs, not "Undergraduate"
        m = re.search(rx, tl)
        if m: fit += p; why.append([f"title: {m.group(0)}", p])
    if j.get("listing_only"):          # feed posting: title only, credit a typical body score
        fit += r.get("listing_body_credit", 0); why.append(["description not fetched", r.get("listing_body_credit", 0)])
    fit = max(0, min(fit, r["caps"]["fit"]))
    # a posting open in Boston and Bangalore scores as Boston: the best listed
    # location wins, penalties apply only when no listed location is a plus
    hits = [p for kw, p in r["location_weights"].items() if kw in ll]
    pos = [p for p in hits if p > 0]
    loc = max(pos) if pos else min(hits) if hits else r["location_default"]
    loc = max(-10, min(loc, r["caps"]["location"]))
    if j.get("season") == "unstated":
        p = r.get("season_unstated", -3)
        fit += p; why.append(["season not stated", p])
    rate, rate_src = pay_rate(j["body"]), "stated"
    if not rate:
        # no pay in the posting: estimate rather than score it as unpaid-looking 0
        est = pay_estimate(company_rate, traj, r)
        if est:
            rate, rate_src = (est[0], est[1]), est[2]
    pay = min(pay_points(rate, r), r["caps"].get("pay", 99))
    if rate_src == "trajectory":                  # a guess never earns the top pay points
        pay = min(pay, (r.get("pay") or {}).get("estimate_cap", 6))
    if rate:
        label = "pay" if rate_src == "stated" else "est. pay"
        why.append([f"{label} ${(rate[0] + rate[1]) / 2:.0f}/hr", pay])
    traj = min(traj, r["caps"]["trajectory"])
    base = fit + loc + traj + pay
    pts, age, basis = fresh
    why.sort(key=lambda x: -x[1])
    # tier comes from base so an aging strong fit stays visible; the ranking
    # total adds freshness so a new good fit sorts above an older, saturated one
    return {"total": base + pts, "base": base, "fit": fit, "loc": loc, "traj": traj,
            "pay": pay, "rate": rate, "rate_src": rate_src if rate else None,
            "fresh": pts, "age": age, "age_basis": basis,
            "tier": "A" if base >= r["tier_a"] and fit >= r.get("tier_a_min_fit", 0) else
                    "B" if base >= r["tier_b"] else
                    "B" if any(k in tl for k in r.get("core_titles", [])) else "C",
            "why": why[:8]}


def run(dry=False, explain=False, only=None, skip=None, quiet=False):
    if not os.path.exists(RESOLVED):
        print("no resolved.json — run: python3 jobwatch/resolve.py", file=sys.stderr)
        sys.exit(1)
    resolved = json.load(open(RESOLVED))
    rules    = json.load(open(SCORING))
    seen     = json.load(open(STATE)) if os.path.exists(STATE) else {}
    # last published matches, so a board that errors this run (a Workday or SAP outage)
    # keeps showing its postings instead of dropping out of the dashboard
    try:
        prev_jobs = json.load(open(OUT)).get("jobs", [])
    except (OSError, ValueError):
        prev_jobs = []
    board_first = seen.setdefault("_boards", {})     # company -> first scan time
    # companies.txt is where trajectory is edited; resolved.json keeps a copy from probe time
    traj_of = {}
    for line in open(os.path.join(ROOT, "companies.txt"), encoding="utf-8"):
        n, _, t = line.strip().partition("|")
        if n and not n.startswith("#") and t.strip().isdigit():
            traj_of[n.strip()] = int(t)

    def traj_for(co, default):
        """Trajectory for a company; a second entry for the same company ("KLA
        Corporation", "KLA (workday)", "Shield AI (ashby)") takes the main one's."""
        base = re.sub(r"\s*\([^)]*\)\s*$", "", co).strip()
        short = re.sub(r"[,\s]+(?:corporation|corp\.?|inc\.?|incorporated|company|co\.?|llc|ltd\.?|"
                       r"technologies|group|holdings)$", "", base, flags=re.I).strip()
        found = [traj_of[x] for x in dict.fromkeys((co, base, short)) if x in traj_of]
        if co not in traj_of or co != base or base != short:
            found = [traj_of[x] for x in (base, short) if x in traj_of and x != co] or found
        return max(found) if found else default
    now_dt   = datetime.now(timezone.utc)
    now      = now_dt.isoformat(timespec="seconds")

    boards = [(n, v) for n, v in sorted(resolved.items()) if v.get("board")]
    live, errors, review, scanned, dropped = [], [], [], 0, {}

    todo = [(n, v) for n, v in boards
            if not (only and v["board"] not in only) and not (skip and v["board"] in skip)]

    def fetch(item):
        name, v = item
        c = {"name": name, **v}      # adapters read the keys their platform needs
        t0 = time.time()
        try:
            jobs = ADAPTERS[v["board"]](c)
            if v.get("match"):
                # company listed on a parent's board (Fluke on Fortive, Instron on ITW):
                # keep postings that name it
                m = re.compile(v["match"], re.I)
                jobs = [j for j in jobs if m.search(f"{j['title']} {j['location']} {j['body']}")]
            return name, v, jobs, None, time.time() - t0
        except Exception as e:
            return name, v, [], str(e) or type(e).__name__, time.time() - t0

    # boards are independent and Workday ones need a detail request per posting,
    # so scan several at once; results are folded in on this thread
    results = []
    with ThreadPoolExecutor(WORKERS) as ex:
        for k, r in enumerate(ex.map(fetch, todo), 1):
            name, v, jobs, err, dt = r
            if not quiet:
                msg = f"ERROR {err}" if err else f"{len(jobs):>5} postings  {dt:.1f}s"
                print(f"[{k:>3}/{len(todo)}] {name:<30} {v['board']:<16} {msg}", flush=True)
            results.append(r)

    # each company's stated pay, so its postings that state none can borrow it
    import statistics
    stated = {}
    for name, v, jobs, err, _ in results:
        for j in jobs or []:
            if STUDENT.search(j["title"]):
                rt = pay_rate(j["body"])
                if rt:
                    stated.setdefault(j.get("company") or name, []).append(rt)
    company_rate = {c: (round(statistics.median(x[0] for x in rs), 2), round(statistics.median(x[1] for x in rs), 2))
                    for c, rs in stated.items()}

    for name, v, jobs, err, _ in results:
        if err:
            kept = [{**pj, "stale": True} for pj in prev_jobs
                    if pj.get("src", pj.get("company")) == name]
            live.extend(kept)
            errors.append({"company": name, "board": v["board"], "error": err, "kept": len(kept)})
            continue
        bfirst = board_first.setdefault(name, now)
        scanned += len(jobs)
        for j in jobs:
            j["posted"] = posted_date(j.get("posted"), now_dt)
            why_drop = gate(j, rules)
            if why_drop:
                dropped[why_drop] = dropped.get(why_drop, 0) + 1
                if explain: print(f"    drop [{why_drop}] {j['title']}")
                sig = any(x in j["body"].lower() for x in rules["me_body_any"])
                if why_drop == "no mechanical signal" or (why_drop == "non-ME discipline" and sig):
                    review.append({"company": j.get("company") or name, "title": j["title"],
                                   "location": j["location"], "url": j["url"], "reason": why_drop})
                continue
            first = seen.get(j["id"], {}).get("first_seen", now)
            co = j.get("company") or name           # the listings board carries many companies
            s = score(j, traj_for(co, v.get("trajectory", 10) if co == name else 10), rules,
                      freshness(j["posted"], first, now_dt, rules, bfirst), company_rate.get(co))
            if s["tier"] == "C" and not rules.get("keep_tier_c"):
                dropped["below threshold"] = dropped.get("below threshold", 0) + 1
                review.append({"company": co, "title": j["title"], "location": j["location"],
                               "url": j["url"], "reason": f"scored {s['base']}, below tier B"})
                continue
            seen[j["id"]] = {"first_seen": first, "last_seen": now}
            live.append({**j, "company": co, "src": name, "score": s, "first_seen": first})

    # one company scanned under two names ("KLA" and "KLA Corporation", a second
    # Workday site) lists the same req twice; keep the better-scored copy
    def _k(x):
        n = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
        return (n(x["company"].split()[0]), n(x["title"]), n(x["location"])[:40])
    best = {}
    for x in sorted(live, key=lambda x: -x["score"]["total"]):
        best.setdefault(_k(x), x)
    live = list(best.values())
    live.sort(key=lambda x: -x["score"]["total"])
    run_url = (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
               f"/actions/runs/{os.environ['GITHUB_RUN_ID']}") if os.environ.get("GITHUB_RUN_ID") else ""
    payload = {"generated": now, "run_url": run_url, "scanned": scanned, "boards": len(todo),
               "errors": errors, "dropped": dropped, "jobs": live,
               "review": sorted(review, key=lambda x: (x["company"], x["title"]))}
    if not dry:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(payload, open(OUT, "w"), indent=1)
        json.dump(seen, open(STATE, "w"), separators=(",", ":"))

    print(f"\nscanned {scanned} postings / {len(boards)} boards · {len(live)} matches · "
          f"{len(review)} in review · {len(errors)} errors")
    if dropped:
        print("filtered: " + ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items())))
    for j in live[:15]:
        s = j["score"]
        age = f"{s['age']:.0f}d {s['age_basis']}" if s["age"] is not None else "age ?"
        print(f"  [{s['tier']}·{s['total']}] {j['company']} — {j['title']} ({j['location'][:40]}) [{age}]")
    return payload


def report_ci(payload):
    """Surface board errors in the Actions UI; exit non-zero if the scan is mostly broken."""
    errs, n = payload["errors"], payload["boards"]
    for e in errs:
        print(f"::warning title=board error::{e['company']} ({e['board']}): {e['error']}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### jobwatch scan\n\n{payload['scanned']} postings from {n - len(errs)}/{n} "
                    f"boards, {len(payload['jobs'])} matches, {len(errs)} board errors\n\n")
            if errs:
                f.write("| company | board | error |\n|---|---|---|\n")
                f.writelines(f"| {e['company']} | {e['board']} | {e['error']} |\n" for e in errs)
    # only an empty scan is a failure: when a provider is down (Workday returned 521 for
    # every tenant on 2026-11-14) the rest of the scan is still worth publishing, and the
    # failed boards keep their last postings
    if payload["scanned"] == 0:
        print("::error::no postings fetched from any board, treating the scan as broken")
        sys.exit(1)
    if len(errs) > n * FAIL_FRACTION:
        print(f"::warning title=provider outage?::{len(errs)}/{n} boards failed; "
              "their last good postings were kept")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--explain", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--ci", action="store_true", help="annotate errors for GitHub Actions")
    p.add_argument("--only"); p.add_argument("--skip")
    a = p.parse_args()
    out = run(a.dry, a.explain,
              set(a.only.split(",")) if a.only else None,
              set(a.skip.split(",")) if a.skip else None, a.quiet)
    if a.ci:
        report_ci(out)
