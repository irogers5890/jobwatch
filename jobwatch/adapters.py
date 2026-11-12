"""Board adapters. Each returns a list of normalized job dicts."""
import html, json, re, time, urllib.error, urllib.parse, urllib.request

UA = "Mozilla/5.0 (compatible; jobwatch/3.0)"
TIMEOUT = 12
RETRIES = 3


def _text(url):
    last = None
    for i in range(RETRIES):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
            with urllib.request.urlopen(r, timeout=TIMEOUT * 2) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404):
                raise RuntimeError(f"HTTP {e.code} — check base url") from None
            last = f"HTTP {e.code}"
        except Exception as e:
            last = str(e)
        time.sleep(0.8)
    raise RuntimeError(f"failed after {RETRIES} tries: {last}")


def _req(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    last, waits = None, 0
    i = 0
    while i < RETRIES:
        try:
            r = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 422):
                raise RuntimeError(f"HTTP {e.code} — check slug/tenant/site") from None
            last = f"HTTP {e.code}"
            if e.code == 429 and waits < 6:        # Eightfold rate-limits; wait it out
                waits += 1                         # (up to ~2.5 min) without using a retry
                time.sleep(min(float(e.headers.get("Retry-After") or 0) or 8 * waits, 45))
                continue
        except Exception as e:
            last = str(e)
        i += 1
        time.sleep(0.8)
    raise RuntimeError(f"failed after {RETRIES} tries: {last}")


def clean(t):
    if not t:
        return ""
    # unescape twice: some feeds (Workday) double-encode, e.g. "&amp;#xa;"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(t)))).strip()


def greenhouse(c):
    d = _req(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    return [{
        "id": f"gh:{c['slug']}:{j.get('id')}",
        "title": j.get("title", ""),
        "location": (j.get("location") or {}).get("name", "") or "",
        "url": j.get("absolute_url", ""),
        "posted": j.get("first_published") or j.get("updated_at", "") or "",
        "body": clean(j.get("content", "")),
    } for j in d.get("jobs", [])]


def lever(c):
    d = _req(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    return [{
        "id": f"lv:{c['slug']}:{j.get('id')}",
        "title": j.get("text", ""),
        "location": (j.get("categories") or {}).get("location", "") or "",
        "url": j.get("hostedUrl", ""),
        "posted": j.get("createdAt", "") or "",
        "body": clean(j.get("descriptionPlain") or j.get("description", "")),
    } for j in d]


def ashby(c):
    d = _req(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}?includeCompensation=true")
    jobs = d.get("jobs", d if isinstance(d, list) else [])

    def pay(j):        # "$200K – $250K"; only some boards fill it in
        t = (j.get("compensation") or {}).get("compensationTierSummary")
        return f" Pay range: {t}." if t else ""
    return [{
        "id": f"ab:{c['slug']}:{j.get('id')}",
        "title": j.get("title", ""),
        "location": j.get("location", "") or "",
        "url": j.get("jobUrl") or j.get("applyUrl", ""),
        "posted": j.get("publishedAt", "") or "",
        "body": clean(j.get("descriptionPlain") or j.get("descriptionHtml", "")) + pay(j),
    } for j in jobs]


def smartrecruiters(c):
    """The list endpoint carries no description and its "ref" is an API URL, so
    student titles get a detail request and links point at the public job page."""
    api = f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings"
    items, offset = [], 0
    while True:
        d = _req(f"{api}?limit=100&offset={offset}")
        page = d.get("content", [])
        items += page
        offset += len(page)
        if len(page) < 100 or offset >= d.get("totalFound", 0):
            break

    def one(j):
        body = ""
        if WD_TITLE.search(j.get("name", "")):
            try:
                secs = (_req(f"{api}/{j['id']}").get("jobAd") or {}).get("sections") or {}
                body = " ".join((v or {}).get("text", "") for v in secs.values() if isinstance(v, dict))
            except RuntimeError:
                pass
        loc = j.get("location") or {}
        return {
            "id": f"sr:{c['slug']}:{j.get('id')}",
            "title": j.get("name", ""),
            "location": ", ".join(x for x in [loc.get("city"), loc.get("region"),
                                              loc.get("country")] if x),
            "url": f"https://jobs.smartrecruiters.com/{c['slug']}/{j.get('id')}",
            "posted": j.get("releasedDate", "") or "",
            "body": clean(body),
        }
    return _pool(one, items)


def workable(c):
    d = _req(f"https://apply.workable.com/api/v1/widget/accounts/{c['slug']}?details=true")
    return [{
        "id": f"wk:{c['slug']}:{j.get('shortcode')}",
        "title": j.get("title", ""),
        "location": ", ".join(x for x in [j.get("city"), j.get("state"),
                                          j.get("country")] if x),
        "url": j.get("url", ""),
        "posted": j.get("published_on", "") or "",
        "body": clean((j.get("description") or "") + " " + (j.get("requirements") or "")),
    } for j in d.get("jobs", [])]


def pinpoint(c):
    """Pinpoint: https://<slug>.pinpointhq.com/postings.json, all postings, no paging.
    Body is split across several HTML fields. The feed has no date; the job page's
    JSON-LD has datePosted, so student titles get one page fetch each."""
    d = _req(f"https://{c['slug']}.pinpointhq.com/postings.json")

    def one(j):
        loc = j.get("location") or {}
        body = " ".join(j.get(k) or "" for k in ("description", "key_responsibilities",
                                                  "skills_knowledge_expertise", "benefits"))
        if j.get("compensation_visible") and j.get("compensation"):
            body += f" Compensation: {j['compensation']}."      # "$110,000 - $180,000 / year"
        posted = ""
        if WD_TITLE.search(j.get("title", "")) and j.get("url"):
            try:
                m = re.search(r'"datePosted"\s*:\s*"([^"]+)"', _text(j["url"]))
                posted = m.group(1) if m else ""
            except RuntimeError:
                pass
        return {
            "id": f"pp:{c['slug']}:{j.get('id')}",
            "title": j.get("title", ""),
            "location": ", ".join(x.strip() for x in [loc.get("city"), loc.get("province")] if x),
            "url": j.get("url", ""),
            "posted": posted,
            "body": clean(body),
        }
    return _pool(one, d.get("data", []))


# Workday CXS. Verified live against nvidia, micron, globalhr (RTX), ngc and others.
#   POST {base}/wday/cxs/{tenant}/{site}/jobs  {"appliedFacets", "limit"<=20, "offset", "searchText"}
#   GET  {base}/wday/cxs/{tenant}/{site}{externalPath}  -> jobPostingInfo.jobDescription
# Quirks: "total" is only filled on the first page (0 afterwards), and searchText is
# fuzzy ("intern" matches nearly every req at Micron), so student roles are selected
# with the tenant's own facets (workerSubType "Intern (Fixed Term)", jobFamilyGroup
# "Interns", ...) and searchText is only a fallback when no such facet exists.
WD_STUDENT = re.compile(r"intern|co-?op|student|universit|univ\b|campus|trainee|"
                        r"praktik|werkstudent|stagiaire|placement", re.I)
WD_TITLE   = re.compile(r"\b(intern|interns|internship|co[-\s]?op|student|undergrad|undergraduate)\b", re.I)
WD_PAGE, WD_MAX_PAGES = 20, 40


def _wd_facets(facets):
    """{facetParameter: [value ids]} for every non-location facet value that names
    a student worker type or job family."""
    out = {}
    for f in facets or []:
        param, vals = f.get("facetParameter", ""), f.get("values") or []
        if vals and "facetParameter" in vals[0]:          # nested group (locations)
            continue
        if param.lower().startswith("location"):
            continue
        ids = [v["id"] for v in vals if v.get("id") and WD_STUDENT.search(v.get("descriptor", ""))]
        if ids:
            out[param] = ids
    return out


def _wd_list(api, facets, q):
    posts, offset = [], 0
    for _ in range(WD_MAX_PAGES):
        d = _req(api, data=json.dumps({"appliedFacets": facets, "limit": WD_PAGE,
                                       "offset": offset, "searchText": q}).encode())
        if offset == 0:
            total = d.get("total", 0)
        page = d.get("jobPostings") or []
        posts += page
        offset += WD_PAGE
        if len(page) < WD_PAGE or offset >= total:
            break
    return posts


def workday(c):
    """c needs tenant, site (str or list of str) and host (wd1, wd5, ...)."""
    from concurrent.futures import ThreadPoolExecutor
    host, tenant = c.get("host") or "wd1", c["tenant"]
    sites = c["site"] if isinstance(c["site"], list) else [c["site"]]
    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    found = {}
    for site in sites:
        api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
        first = _req(api, data=json.dumps({"appliedFacets": {}, "limit": 1, "offset": 0,
                                           "searchText": ""}).encode())
        facets = _wd_facets(first.get("facets"))
        # values inside one facet OR together, separate facets AND, so query each. Tenants
        # tag students inconsistently (Boeing marks 1 of its US interns), so the plain
        # keyword search always runs as well and the title filter does the rest.
        queries = [({k: v}, "") for k, v in facets.items()] + \
                  [({}, q) for q in (c.get("q") or "intern,co-op").split(",")]
        for fac, q in queries:
            for j in _wd_list(api, fac, q.strip()):
                path = j.get("externalPath")
                if path and WD_TITLE.search(j.get("title", "")):
                    found.setdefault(path, (site, j))

    def detail(item):
        path, (site, j) = item
        info = {}
        try:
            info = _req(f"{base}/wday/cxs/{tenant}/{site}{path}").get("jobPostingInfo") or {}
        except RuntimeError:
            pass
        locs = [info.get("location") or j.get("locationsText", "")] + \
               list(info.get("additionalLocations") or [])
        country = ((info.get("country") or {}).get("descriptor") or "").strip()
        if country and locs[0] and country.lower() not in locs[0].lower():
            locs[0] = f"{locs[0]}, {country}"          # "Manching" -> "Manching, Germany"
        req = info.get("jobReqId") or path.rsplit("/", 1)[-1]
        return {
            "id": f"wd:{tenant}:{req}",
            "title": j.get("title", ""),
            "location": "; ".join(x for x in locs if x),
            "url": info.get("externalUrl") or f"{base}/en-US/{site}{path}",
            "posted": info.get("startDate") or j.get("postedOn", "") or "",
            "body": clean(info.get("jobDescription", "")),
        }

    out, ids = [], set()
    with ThreadPoolExecutor(8) as ex:
        for j in ex.map(detail, found.items()):
            if j["id"] not in ids:            # same req listed on several sites
                ids.add(j["id"]); out.append(j)
    return out


# --- career-site platforms used by big employers that are not on Workday ----------
# None of these can be filtered to student reqs server side (keyword search is fuzzy),
# so each lists broadly, keeps titles matching WD_TITLE, then fetches detail only for
# those. Verified live 2026-10: eightfold (Lam, Qualcomm, Boston Scientific, Microsoft),
# oracle (TI, Honeywell), successfactors (Teradyne), jibe (AMD).

def _pool(fn, items, n=6):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, items))


def eightfold(c):
    """Eightfold PCSX: c needs host (careers.qualcomm.com) and domain (qualcomm.com).
    Search pages are fixed at 10 results; Microsoft returns 429 when pushed, so pages
    are fetched one at a time."""
    host, dom = c["host"], c["domain"]
    api = f"https://{host}/api/pcsx"
    found = {}
    for q in (c.get("q") or "intern,co-op").split(","):
        start = 0
        while start < 1000:
            d = _req(f"{api}/search?domain={dom}&start={start}&num=10"
                     f"&query={urllib.parse.quote(q.strip())}").get("data") or {}
            page = d.get("positions") or []
            for p in page:
                if WD_TITLE.search(p.get("name", "")):
                    found.setdefault(p["id"], p)
            start += 10
            if not page or start >= (d.get("count") or 0):
                break
            time.sleep(1.0)                        # Microsoft and Lockheed rate-limit faster paging

    def detail(p):
        info = {}
        try:
            info = _req(f"{api}/position_details?position_id={p['id']}&domain={dom}&hl=en").get("data") or {}
        except RuntimeError:
            pass
        ts = p.get("postedTs") or p.get("creationTs")
        return {
            "id": f"ef:{dom}:{p.get('displayJobId') or p['id']}",
            "title": p.get("name", ""),
            "location": "; ".join(p.get("standardizedLocations") or p.get("locations") or []),
            "url": f"https://{host}{p.get('positionUrl') or '/careers/job/' + str(p['id'])}",
            "posted": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else "",
            "body": clean(info.get("jobDescription", "")),
        }
    def paced(p):
        time.sleep(0.5)
        return detail(p)
    return _pool(paced, list(found.values()), 1)


def oracle(c):
    """Oracle Cloud HCM candidate experience: c needs host (edbz.fa.us2.oraclecloud.com)
    and site (CX). Lists every requisition 200 at a time."""
    host, site = c["host"], c["site"]
    api = f"https://{host}/hcmRestApi/resources/latest"
    reqs, offset = [], 0
    while offset < 20000:
        d = (_req(f"{api}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
                  f"&finder=findReqs;siteNumber={site},limit=200,offset={offset},"
                  f"sortBy=POSTING_DATES_DESC").get("items") or [{}])[0]
        page = d.get("requisitionList") or []
        reqs += [r for r in page if WD_TITLE.search(r.get("Title", ""))]
        offset += 200
        if not page or offset >= (d.get("TotalJobsCount") or 0):
            break

    def detail(r):
        info = {}
        try:
            info = (_req(f"{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                         f"&finder=ById;Id=%22{r['Id']}%22,siteNumber={site}").get("items") or [{}])[0]
        except RuntimeError:
            pass
        locs = [r.get("PrimaryLocation")] + [x.get("Name") for x in info.get("secondaryLocations") or []]
        body = " ".join(info.get(k) or r.get(k) or "" for k in
                        ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
        return {
            "id": f"or:{host.split('.')[0]}:{r['Id']}",
            "title": r.get("Title", ""),
            "location": "; ".join(x for x in locs if x),
            "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}",
            "posted": r.get("PostedDate", "") or "",
            "body": clean(body),
        }
    return _pool(detail, reqs)


def successfactors(c):
    """SAP SuccessFactors Career Site Builder: c needs base (https://jobs.teradyne.com).
    No JSON API and the RSS feed stops at 20 items, so the HTML search is paged
    and each matching job page is read for its description. Job links are
    site-absolute, so base is reduced to its origin for those."""
    base = c["base"].rstrip("/")
    origin = "/".join(base.split("/")[:3])
    found = {}
    for q in (c.get("q") or "intern,co-op").split(","):
        row, seen = 0, set()
        while row < 1000:
            t = _text(f"{base}/search/?q={urllib.parse.quote(q.strip())}&startrow={row}")
            # two layouts: table rows (Teradyne) and tiles (Arthrex); both mark the
            # title with class="jobTitle-link" and repeat it for the phone view
            links = list(re.finditer(r'<a\b([^>]*class="jobTitle-link[^"]*"[^>]*)>\s*([^<]+?)\s*</a>', t))
            new = 0
            for k, a in enumerate(links):
                href = re.search(r'href="([^"]+)"', a.group(1))
                if not href or href.group(1) in seen:
                    continue
                seen.add(href.group(1)); new += 1
                if href.group(1) in found or not WD_TITLE.search(a.group(2)):
                    continue
                tail = t[a.end():links[k + 1].start() if k + 1 < len(links) else a.end() + 4000]
                loc = re.search(r'(?:class="jobLocation"|section-location-value")>\s*([^<]+?)\s*<', tail)
                found[href.group(1)] = (html.unescape(a.group(2)).strip(), loc.group(1) if loc else "")
            total = re.search(r"(?:Results <b>[^<]*</b> of <b>|Showing \d+ to \d+ of )(\d+)", t)
            row += new
            if not new or not total or row >= int(total.group(1)):
                break

    def detail(item):
        path, (title, loc) = item
        body, posted = "", ""
        try:
            t = _text(origin + path)
            i = t.find('class="jobdescription"')
            if i >= 0:
                end = min([k for k in (t.find('class="jobmarkets"', i), t.find("social-apply", i))
                           if k > 0] or [i + 30000])
                body = t[i + 22:end]
            m = re.search(r'itemprop="datePosted" content="([^"]+)"', t)
            if m:                                  # "Fri Sep 25 07:00:00 UTC 2026"
                try:
                    posted = time.strftime("%Y-%m-%d", time.strptime(m.group(1), "%a %b %d %H:%M:%S %Z %Y"))
                except ValueError:
                    posted = m.group(1)
        except RuntimeError:
            pass
        jid = re.search(r"/(\d+)/?$", path)
        return {
            "id": f"sf:{origin.split('//')[-1]}:{jid.group(1) if jid else path}",
            "title": title, "location": html.unescape(loc),
            "url": origin + path, "posted": posted, "body": clean(body),
        }
    return _pool(detail, list(found.items()))


def jibe(c):
    """Jibe (Google-hosted career sites, e.g. careers.amd.com): c needs host.
    /api/jobs returns full descriptions inline, 100 per page."""
    host = c["host"]
    out, ids = [], set()
    for q in (c.get("q") or "intern,co-op").split(","):
        page = 1
        while page <= 20:
            d = _req(f"https://{host}/api/jobs?keywords={urllib.parse.quote(q.strip())}"
                     f"&page={page}&limit=100")
            jobs = d.get("jobs") or []
            for j in jobs:
                j = j.get("data") or {}
                rid = j.get("req_id") or j.get("slug")
                if rid in ids or not WD_TITLE.search(j.get("title", "")):
                    continue
                ids.add(rid)
                out.append({
                    "id": f"jb:{host}:{rid}",
                    "title": j.get("title", ""),
                    "location": j.get("full_location") or j.get("location_name", "") or "",
                    "url": f"https://{host}/careers-home/jobs/{j.get('slug') or rid}",
                    "posted": j.get("posted_date", "") or "",
                    "body": clean(" ".join(j.get(k) or "" for k in
                                           ("description", "responsibilities", "qualifications"))
                                  # AMD puts its pay range in tag fields ("USD $83,200.00/Yr.")
                                  + " Pay: " + " ".join(str(t) for k, v in j.items() if k.startswith("tags")
                                                        for t in (v if isinstance(v, list) else [v])
                                                        if "$" in str(t))),
                })
            page += 1
            if len(jobs) < 100 or (page - 1) * 100 >= (d.get("totalCount") or 0):
                break
    return out


def rippling(c):
    """Rippling ATS: c needs slug (boom-supersonic). The list has no description, so
    student titles are read one by one from the detail endpoint."""
    api = f"https://api.rippling.com/platform/api/ats/v1/board/{c['slug']}/jobs"
    keep = [j for j in _req(api) if WD_TITLE.search(j.get("name", ""))]

    def detail(j):
        d = {}
        try:
            d = _req(f"{api}/{j['uuid']}")
        except RuntimeError:
            pass
        desc = d.get("description") or {}
        return {
            "id": f"rp:{c['slug']}:{j['uuid']}",
            "title": j.get("name", ""),
            "location": (j.get("workLocation") or {}).get("label", "") or "",
            "url": j.get("url", ""),
            "posted": d.get("createdOn", "") or "",
            "body": clean(" ".join(v for v in desc.values() if isinstance(v, str))),
        }
    return _pool(detail, keep)


def phenom(c):
    """Phenom People career sites: c needs base, the site's locale root
    (https://careers.mitre.org/us/en). Search is POST /widgets "refineSearch";
    each job page embeds its description in phApp.ddo."""
    base = c["base"].rstrip("/")
    origin = "/".join(base.split("/")[:3])
    found = {}
    for q in (c.get("q") or "intern,co-op").split(","):
        start = 0
        while start < 1000:
            d = _req(f"{origin}/widgets", data=json.dumps({
                "ddoKey": "refineSearch", "pageName": "search-results", "from": start,
                "size": 50, "jobs": True, "counts": False, "keywords": q.strip(),
                "global": True, "siteType": "external", "selected_fields": {}}).encode())
            r = d.get("refineSearch") or {}
            page = (r.get("data") or {}).get("jobs") or []
            for j in page:
                if WD_TITLE.search(j.get("title", "")):
                    found.setdefault(j.get("jobId"), j)
            start += len(page)
            if not page or start >= (r.get("totalHits") or 0):
                break

    def detail(j):
        body = j.get("descriptionTeaser", "")
        try:
            t = _text(f"{base}/job/{j['jobId']}")
            m = re.search(r"phApp\.ddo\s*=\s*(\{.*?\});\s*phApp", t, re.S)
            if m:
                body = ((json.loads(m.group(1)).get("jobDetail") or {}).get("data") or {}) \
                    .get("job", {}).get("description") or body
        except (RuntimeError, ValueError):
            pass
        return {
            "id": f"ph:{origin.split('//')[-1]}:{j['jobId']}",
            "title": j.get("title", ""),
            "location": "; ".join(j.get("multi_location") or [j.get("location", "")]),
            "url": f"{base}/job/{j['jobId']}",
            "posted": j.get("postedDate", "") or "",
            "body": clean(body),
        }
    return _pool(detail, list(found.values()))


# --- internship feeds: discovery source, and a fallback for unsupported sites -----
LISTING_FEEDS = ["https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/"
                 ".github/scripts/listings.json"]


def listing_ok(x):
    """Active, hardware, and for Summer or Fall 2027."""
    return bool(x.get("active") and x.get("is_visible", True)
                and "hardware" in (x.get("category") or "").lower()
                and any(t in ("Summer 2027", "Fall 2027") for t in x.get("terms") or []))


def board_from_url(u):
    """Resolved-entry dict for an apply URL, or None if the site is not one we scan."""
    p = urllib.parse.urlparse(u)
    host, path = p.netloc.lower(), [x for x in p.path.split("/") if x]
    first = path[0] if path else ""
    if "greenhouse.io" in host:
        slug = (urllib.parse.parse_qs(p.query).get("for") or [first])[0]
        return {"board": "greenhouse", "slug": slug} if slug and slug != "embed" else None
    if host == "jobs.lever.co" and first:
        return {"board": "lever", "slug": first}
    if host == "jobs.ashbyhq.com" and first:
        return {"board": "ashby", "slug": first}
    if host.endswith("smartrecruiters.com") and first:
        return {"board": "smartrecruiters", "slug": first}
    if host == "apply.workable.com" and first:
        return {"board": "workable", "slug": first}
    if host.endswith(".pinpointhq.com"):
        return {"board": "pinpoint", "slug": host.split(".")[0]}
    m = re.match(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if m and path:
        site = path[1] if re.match(r"[a-z]{2}-[A-Z]{2}$", first) and len(path) > 1 else first
        return {"board": "workday", "tenant": m.group(1), "host": m.group(2), "site": site}
    if host.endswith(".eightfold.ai"):
        return {"board": "eightfold", "host": host, "domain": host.split(".")[0] + ".com"}
    if "oraclecloud.com" in host and "sites" in path and path.index("sites") + 1 < len(path):
        return {"board": "oracle", "host": host, "site": path[path.index("sites") + 1],
                "careers": host}
    if "ats=successfactors" in p.query or re.search(r"/job/[^/]+/\d+/?$", p.path):
        return {"board": "successfactors", "base": f"{p.scheme}://{p.netloc}"}
    return None


def board_dedupe_key(v):
    """Case-insensitive board identity; Oracle and Eightfold by host or domain alone,
    since one company can expose several site names."""
    b = v.get("board")
    if b == "workday":
        return ("workday", (v.get("tenant") or "").lower(), str(v.get("site")).lower())
    if b == "oracle":
        return ("oracle", (v.get("host") or "").lower())
    if b == "eightfold":
        return ("eightfold", (v.get("domain") or "").lower())
    if b == "successfactors":
        return ("successfactors", (v.get("base") or "").lower().split("//")[-1].split("/")[0])
    return (b, (v.get("slug") or "").lower())


def board_dedupe_keys(v):
    """Every key an entry answers to (one per Workday site when site is a list)."""
    if v.get("board") == "workday" and isinstance(v.get("site"), list):
        return {board_dedupe_key({**v, "site": x}) for x in v["site"]}
    return {board_dedupe_key(v)}


def listings(c):
    """Feed postings from companies that are not otherwise scanned (custom sites,
    iCIMS, Taleo, boards not resolved yet). The feed has title, terms and locations
    but no description, so these rank on title, location and pay-free trajectory."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolved.json")
    res = json.load(open(path))
    scanned_names = {n.lower() for n, v in res.items() if v.get("board") and v["board"] != "listings"}
    scanned_keys = {k for v in res.values() if v.get("board") and v["board"] != "listings"
                    for k in board_dedupe_keys(v)}
    out = []
    for feed in c.get("feeds") or LISTING_FEEDS:
        for x in _req(feed):
            if not listing_ok(x):
                continue
            name = re.sub(r"\s*&\s*", " and ", x.get("company_name") or "").strip()
            b = board_from_url(x.get("url") or "")
            if name.lower() in scanned_names or (b and board_dedupe_key(b) in scanned_keys):
                continue
            terms = ", ".join(x.get("terms") or [])
            out.append({
                "id": f"ls:{x.get('id')}",
                "listing_only": True,
                "company": name,
                "title": x.get("title", ""),
                "location": "; ".join(x.get("locations") or []),
                "url": x.get("url", ""),
                "posted": x.get("date_posted") or "",
                "body": f"{x.get('title', '')}. Term: {terms}. Listed on {x.get('source', 'Simplify')}; "
                        f"full description on the company site.",
            })
    return out


ADAPTERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "workday": workday,
    "pinpoint": pinpoint,
    "eightfold": eightfold,
    "oracle": oracle,
    "successfactors": successfactors,
    "jibe": jibe,
    "rippling": rippling,
    "phenom": phenom,
    "listings": listings,
}
