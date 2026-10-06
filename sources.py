"""Job source fetchers. Each returns a list of normalised job dicts:
{source, ext_id, title, company, location, remote, url, description, posted, salary, tags, visa_flag}
Only free, public endpoints are used. Adzuna is optional (free API key)."""
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from analyze import clean_text

UA = "Mozilla/5.0 (JobRadar personal job-search tool)"
try:  # use certifi's certificates when available (fixes some Mac Python installs)
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl.create_default_context()


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_json(url, payload, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"User-Agent": UA, "Accept": "application/json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _iso(v):
    if v is None or v == "":
        return ""
    try:
        if isinstance(v, (int, float)) or str(v).isdigit():
            return datetime.fromtimestamp(int(v), tz=timezone.utc).strftime("%Y-%m-%d")
        return str(v)[:10]
    except Exception:
        return ""


def job(**kw):
    base = dict(source="", ext_id="", title="", company="", location="", remote=False, url="",
                description="", posted="", salary="", tags=[], visa_flag=None)
    base.update(kw)
    base["description"] = clean_text(base["description"])
    base["title"] = clean_text(base["title"])
    return base


# ------------------------------------------------------------------ Arbeitnow
# Germany-focused board with an explicit visa-sponsorship filter.
def arbeitnow(cfg, log):
    out = []
    for visa in (True, False):
        for page in range(1, (4 if visa else 3)):
            q = {"page": page}
            if visa:
                q["visa_sponsorship"] = "true"
            url = "https://www.arbeitnow.com/api/job-board-api?" + urllib.parse.urlencode(q)
            try:
                data = get_json(url)
            except Exception as e:
                if not out and page == 1 and visa:
                    raise
                log(f"Arbeitnow page {page}: {e}")
                break
            items = data.get("data", [])
            for it in items:
                out.append(job(source="Arbeitnow", ext_id=it.get("slug", ""), title=it.get("title", ""),
                               company=it.get("company_name", ""), location=it.get("location", ""),
                               remote=bool(it.get("remote")), url=it.get("url", ""),
                               description=it.get("description", ""), posted=_iso(it.get("created_at")),
                               tags=(it.get("tags") or []) + (it.get("job_types") or []),
                               visa_flag=True if visa else (it.get("visa_sponsorship") or None)))
            if not items or not (data.get("links") or {}).get("next"):
                break
            time.sleep(0.5)
    log(f"Arbeitnow: {len(out)} jobs")
    return out


# ------------------------------------------------------------------ Remotive
def remotive(cfg, log):
    # One request returns all current remote jobs; Remotive asks users not to poll too often.
    data = get_json("https://remotive.com/api/remote-jobs")
    out = []
    for it in data.get("jobs", []):
        out.append(job(source="Remotive", ext_id=str(it.get("id")), title=it.get("title", ""),
                       company=it.get("company_name", ""),
                       location=it.get("candidate_required_location", "") or "Remote",
                       remote=True, url=it.get("url", ""), description=it.get("description", ""),
                       posted=_iso(it.get("publication_date")), salary=it.get("salary", "") or "",
                       tags=(it.get("tags") or []) + [it.get("category", "")]))
    log(f"Remotive: {len(out)} jobs")
    return out


# ------------------------------------------------------------------ RemoteOK
def remoteok(cfg, log):
    data = get_json("https://remoteok.com/api")
    out = []
    for it in data:
        if not isinstance(it, dict) or not it.get("position"):
            continue
        sal = ""
        if it.get("salary_min"):
            sal = f"${it.get('salary_min'):,}–${it.get('salary_max') or it.get('salary_min'):,}"
        out.append(job(source="RemoteOK", ext_id=str(it.get("id")), title=it.get("position", ""),
                       company=it.get("company", ""), location=it.get("location", "") or "Remote", remote=True,
                       url=it.get("url") or it.get("apply_url", ""), description=it.get("description", ""),
                       posted=_iso(it.get("date")), salary=sal, tags=it.get("tags") or []))
    log(f"RemoteOK: {len(out)} jobs")
    return out


# ------------------------------------------------------------------ Jobicy
JOBICY_GEOS = ["europe", "hungary", "germany", "netherlands", "emea"]


def jobicy(cfg, log):
    out = []
    for geo in JOBICY_GEOS:
        try:
            data = get_json(f"https://jobicy.com/api/v2/remote-jobs?count=50&geo={geo}")
        except Exception as e:
            log(f"Jobicy {geo}: {e}")
            continue
        for it in data.get("jobs", []) or []:
            sal = ""
            if it.get("annualSalaryMin"):
                sal = f"{it.get('salaryCurrency', '')} {it.get('annualSalaryMin')}–{it.get('annualSalaryMax', '')}"
            out.append(job(source="Jobicy", ext_id=str(it.get("id")), title=it.get("jobTitle", ""),
                           company=it.get("companyName", ""), location=it.get("jobGeo", "") or "Remote",
                           remote=True, url=it.get("url", ""),
                           description=it.get("jobDescription") or it.get("jobExcerpt", ""),
                           posted=_iso(it.get("pubDate")), salary=sal,
                           tags=(it.get("jobIndustry") or []) + (it.get("jobType") or [])))
        time.sleep(1)
    log(f"Jobicy: {len(out)} jobs")
    return out


# ------------------------------------------------------------------ Himalayas
def himalayas(cfg, log):
    out = []
    for offset in range(0, 200, 20):
        try:
            data = get_json(f"https://himalayas.app/jobs/api?limit=20&offset={offset}")
        except Exception as e:
            if not out:
                raise
            log(f"Himalayas offset {offset}: {e}")
            break
        items = data.get("jobs", []) or []
        for it in items:
            locs = it.get("locationRestrictions") or []
            out.append(job(source="Himalayas", ext_id=it.get("guid") or it.get("applicationLink", ""),
                           title=it.get("title", ""), company=it.get("companyName", ""),
                           location=", ".join(locs) if locs else "Remote (worldwide)", remote=True,
                           url=it.get("applicationLink") or it.get("guid", ""),
                           description=it.get("description") or it.get("excerpt", ""),
                           posted=_iso(it.get("pubDate")),
                           salary=(f"{it.get('currency', '')} {it.get('minSalary')}–{it.get('maxSalary')}"
                                   if it.get("minSalary") else ""),
                           tags=it.get("categories") or []))
        if not items:
            break
        time.sleep(0.5)
    log(f"Himalayas: {len(out)} jobs")
    return out


# ------------------------------------------------------------------ The Muse
MUSE_LOCATIONS = ["Budapest, Hungary", "Berlin, Germany", "Munich, Germany", "Hamburg, Germany",
                  "Frankfurt, Germany", "Amsterdam, Netherlands", "Dublin, Ireland", "Lisbon, Portugal",
                  "Warsaw, Poland", "Prague, Czech Republic", "Vienna, Austria", "Stockholm, Sweden",
                  "Copenhagen, Denmark", "Barcelona, Spain", "Paris, France"]
MUSE_CATS = ["Accounting and Finance", "Business Operations", "Data and Analytics", "Project Management",
             "Sales", "Customer Service", "Product Management"]


def themuse(cfg, log):
    out = []
    params = [("category", c) for c in MUSE_CATS] + [("location", l) for l in MUSE_LOCATIONS]
    for page in range(0, 3):
        q = urllib.parse.urlencode(params + [("page", page)])
        try:
            data = get_json("https://www.themuse.com/api/public/jobs?" + q)
        except Exception as e:
            if not out:
                raise
            log(f"The Muse page {page}: {e}")
            break
        for it in data.get("results", []):
            locs = ", ".join(l.get("name", "") for l in it.get("locations", []))
            out.append(job(source="The Muse", ext_id=str(it.get("id")), title=it.get("name", ""),
                           company=(it.get("company") or {}).get("name", ""), location=locs,
                           remote="remote" in locs.lower(), url=(it.get("refs") or {}).get("landing_page", ""),
                           description=it.get("contents", ""), posted=_iso(it.get("publication_date")),
                           tags=[c.get("name", "") for c in it.get("categories", [])] +
                                [l.get("name", "") for l in it.get("levels", [])]))
        if page + 1 >= data.get("page_count", 0):
            break
        time.sleep(0.5)
    log(f"The Muse: {len(out)} jobs")
    return out


# ------------------------------------------------------------------ Adzuna (optional key)
ADZUNA_COUNTRIES = ["de", "nl", "at", "pl", "be", "fr", "es", "it"]


def adzuna(cfg, log):
    app_id, app_key = cfg.get("adzuna_app_id"), cfg.get("adzuna_app_key")
    if not app_id or not app_key:
        log("Adzuna: skipped (add a free API key in Settings to enable Germany/Netherlands/Austria/Poland… search)")
        return []
    out = []
    queries = cfg.get("adzuna_queries") or ["marketing analyst english", "growth analyst", "revops",
                                            "financial analyst english", "fp&a analyst english",
                                            "investment analyst", "visa sponsorship analyst"]
    for c in cfg.get("adzuna_countries") or ADZUNA_COUNTRIES:
        for qtext in queries:
            q = urllib.parse.urlencode({"app_id": app_id, "app_key": app_key, "what": qtext,
                                        "results_per_page": 50, "max_days_old": 30,
                                        "content-type": "application/json"})
            try:
                data = get_json(f"https://api.adzuna.com/v1/api/jobs/{c}/search/1?{q}")
            except Exception as e:
                log(f"Adzuna {c} '{qtext}': {e}")
                continue
            for it in data.get("results", []):
                sal = ""
                if it.get("salary_min"):
                    sal = f"€{int(it['salary_min']):,}–€{int(it.get('salary_max') or it['salary_min']):,}"
                out.append(job(source=f"Adzuna-{c.upper()}", ext_id=str(it.get("id")), title=it.get("title", ""),
                               company=(it.get("company") or {}).get("display_name", ""),
                               location=(it.get("location") or {}).get("display_name", "") + f" ({c.upper()})",
                               url=it.get("redirect_url", ""), description=it.get("description", ""),
                               posted=_iso(it.get("created")), salary=sal))
            time.sleep(0.4)
    log(f"Adzuna: {len(out)} jobs")
    return out


# ------------------------------------------------------------------ Startup watchlist (ATS boards)
def _greenhouse(token):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    for it in data.get("jobs", []):
        yield dict(ext_id=str(it.get("id")), title=it.get("title", ""),
                   location=(it.get("location") or {}).get("name", ""), url=it.get("absolute_url", ""),
                   description=clean_text(it.get("content", "")), posted=_iso(it.get("updated_at")))


def _lever(token):
    data = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    for it in data:
        cats = it.get("categories") or {}
        desc = (it.get("descriptionPlain") or "") + " " + " ".join(
            (l.get("text", "") + " " + clean_text(l.get("content", ""))) for l in it.get("lists", []))
        yield dict(ext_id=it.get("id", ""), title=it.get("text", ""), location=cats.get("location", ""),
                   url=it.get("hostedUrl", ""), description=desc + " " + (it.get("additionalPlain") or ""),
                   posted=_iso((it.get("createdAt") or 0) // 1000 if it.get("createdAt") else ""))


def _ashby(token):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    for it in data.get("jobs", []):
        yield dict(ext_id=it.get("id", ""), title=it.get("title", ""), location=it.get("location", ""),
                   url=it.get("jobUrl", ""), description=it.get("descriptionPlain") or clean_text(
                       it.get("descriptionHtml", "")), posted=_iso(it.get("publishedAt")),
                   remote=bool(it.get("isRemote")))


def _workable(token):
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true")
    for it in data.get("jobs", []):
        loc = ", ".join(x for x in [it.get("city"), it.get("country")] if x)
        yield dict(ext_id=it.get("shortcode", ""), title=it.get("title", ""), location=loc,
                   url=it.get("url") or it.get("shortlink", ""), description=clean_text(it.get("description", "")),
                   posted=_iso(it.get("published_on")), remote=bool(it.get("telecommuting")))


def _title_ok(title):
    """Only fetch full details for jobs whose title matches one of your tracks (saves requests)."""
    from analyze import role_relevance
    return role_relevance(title, "", None)[0] > 0


def _workday(token, max_details=40):
    """Workday career sites (used by many Budapest finance & service centres).
    token = careers URL, e.g. https://company.wd3.myworkdayjobs.com/External?q=Budapest"""
    u = urllib.parse.urlparse(token if "://" in token else "https://" + token)
    host = u.netloc
    tenant = host.split(".")[0]
    parts = [p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    site = parts[0] if parts else tenant
    search = urllib.parse.parse_qs(u.query).get("q", ["Budapest"])[0]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    details = 0
    for offset in range(0, 200, 20):
        data = post_json(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search})
        posts = data.get("jobPostings") or []
        for it in posts:
            title = it.get("title", "")
            path = it.get("externalPath", "")
            desc, loc = " ".join(it.get("bulletFields") or []), it.get("locationsText", "")
            if _title_ok(title) and details < max_details:
                try:
                    info = get_json(base + path).get("jobPostingInfo", {})
                    desc = clean_text(info.get("jobDescription", "")) or desc
                    loc = info.get("location") or loc
                    details += 1
                except Exception:
                    pass
            yield dict(ext_id=path or title, title=title, location=loc or search,
                       url=f"https://{host}/{site}{path}", description=desc, posted="")
        if len(posts) < 20:
            break
        time.sleep(0.3)


def _smartrecruiters(token):
    """SmartRecruiters public postings. token = company id, optionally '?country=hu'."""
    company, _, q = token.partition("?")
    country = urllib.parse.parse_qs(q).get("country", ["hu"])[0]
    data = get_json(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&country={country}")
    for it in data.get("content", []):
        loc = it.get("location") or {}
        title = it.get("name", "")
        desc = ""
        if _title_ok(title):
            try:
                d = get_json(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{it.get('id')}")
                secs = (d.get("jobAd") or {}).get("sections") or {}
                desc = " ".join(clean_text((secs.get(k) or {}).get("text", "")) for k in
                                ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
            except Exception:
                pass
        yield dict(ext_id=str(it.get("id")), title=title,
                   location=", ".join(x for x in [loc.get("city"), (loc.get("country") or "").upper()] if x),
                   url=f"https://jobs.smartrecruiters.com/{company}/{it.get('id')}", description=desc,
                   posted=_iso(it.get("releasedDate")), remote=bool(loc.get("remote")))


ATS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "workable": _workable,
       "workday": _workday, "smartrecruiters": _smartrecruiters}


def parse_careers_url(url):
    """Turn a careers-page URL into (ats, token) if recognisable."""
    u = urllib.parse.urlparse(url.strip())
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    if "greenhouse.io" in host:
        if "for" in urllib.parse.parse_qs(u.query):
            return "greenhouse", urllib.parse.parse_qs(u.query)["for"][0]
        if parts:
            return "greenhouse", parts[-1] if parts[0] == "v1" else parts[0]
    if "lever.co" in host and parts:
        return "lever", parts[0]
    if "ashbyhq.com" in host and parts:
        return "ashby", parts[-1] if "job-board" in parts else parts[0]
    if "workable.com" in host and parts:
        return "workable", parts[0]
    if "myworkdayjobs.com" in host:
        site = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        return "workday", f"https://{u.netloc}/{site[0] if site else ''}?q=Budapest"
    if "smartrecruiters.com" in host and parts:
        return "smartrecruiters", parts[-1] if host.startswith("api.") else parts[0]
    return None, None


def watchlist(cfg, log, companies):
    out, status = [], {}
    for c in companies:
        if not c.get("enabled", True):
            continue
        fn = ATS.get(c.get("ats"))
        if not fn:
            continue
        try:
            n = 0
            for it in fn(c["token"]):
                out.append(job(source="Company watchlist",
                               company=re.sub(r"\s*\(.*?\)\s*$", "", c.get("name", c["token"])),
                               visa_flag=True if c.get("known_sponsor") else None, **it))
                n += 1
            status[c["name"]] = f"ok ({n} roles)"
        except Exception as e:
            status[c["name"]] = f"error: {e}"
        time.sleep(0.3)
    log(f"Company watchlist: {len(out)} jobs from {sum(1 for v in status.values() if v.startswith('ok'))} companies")
    return out, status


# ------------------------------------------------------------------ Jooble (optional free key)
# Aggregates Hungarian job boards and company sites. Free key: jooble.org/api/about
JOOBLE_QUERIES = ["marketing analyst", "growth analyst", "revops", "data analyst", "business analyst",
                  "financial analyst", "fp&a", "financial reporting analyst", "controlling", "investment analyst",
                  "credit analyst", "graduate programme"]


def jooble(cfg, log):
    key = cfg.get("jooble_key")
    if not key:
        log("Jooble: skipped (add a free Jooble API key in Settings for many more Budapest jobs)")
        return []
    out = []
    locations = cfg.get("jooble_locations") or ["Budapest", "Hungary"]
    for loc in locations:
        for q in cfg.get("jooble_queries") or JOOBLE_QUERIES:
            try:
                data = post_json(f"https://jooble.org/api/{key}", {"keywords": q, "location": loc, "page": "1"})
            except Exception as e:
                log(f"Jooble '{q}' {loc}: {e}")
                continue
            for it in data.get("jobs", []) or []:
                out.append(job(source="Jooble", ext_id=str(it.get("id") or it.get("link")), title=it.get("title", ""),
                               company=it.get("company", ""), location=it.get("location", "") or loc,
                               url=it.get("link", ""), description=it.get("snippet", ""),
                               posted=_iso(it.get("updated")), salary=it.get("salary", "") or "",
                               remote="remote" in (it.get("type", "") + it.get("location", "")).lower()))
            time.sleep(0.4)
    log(f"Jooble: {len(out)} jobs")
    return out


SOURCES = {
    "arbeitnow": ("Arbeitnow (Germany, visa filter)", arbeitnow),
    "themuse": ("The Muse (EU cities)", themuse),
    "remotive": ("Remotive (remote)", remotive),
    "remoteok": ("RemoteOK (remote)", remoteok),
    "jobicy": ("Jobicy (remote, Europe)", jobicy),
    "himalayas": ("Himalayas (remote)", himalayas),
    "adzuna": ("Adzuna (DE/NL/AT/PL/… needs free key)", adzuna),
    "jooble": ("Jooble (Hungary job boards, needs free key)", jooble),
}
