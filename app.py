"""JobRadar — personal EU job-search desktop app.
Run:  python app.py      (opens http://127.0.0.1:8765 in your browser)
Standard library only; no installs needed (Python 3.9+)."""
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import traceback
import urllib.error
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import analyze as A
import sources as S

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
DB_PATH = os.path.join(DATA, "jobradar.db")
SETTINGS_PATH = os.path.join(DATA, "settings.json")
CV_PATH = os.path.join(DATA, "cv.txt")                    # main / finance CV
CV2_PATH = os.path.join(DATA, "cv_analytics.txt")         # analytics & growth CV
WATCH_PATH = os.path.join(DATA, "watchlist.json")
PORT = int(os.environ.get("JOBRADAR_PORT", "8765"))

TARGET_REGIONS = ["Hungary", "Germany", "Netherlands", "Ireland", "Austria", "Poland", "Czechia", "Portugal",
                  "Spain", "France", "Belgium", "Luxembourg", "Nordics", "Other EU"]

DEFAULT_FORM_ANSWERS = [
    {"q": "Are you legally authorised to work in this country?", "a": "{work_auth}"},
    {"q": "Will you now or in the future require visa sponsorship?", "a": "{sponsorship_answer}"},
    {"q": "Are you willing to relocate?", "a": "Yes, I am open to relocating anywhere in the EU."},
    {"q": "When can you start / notice period?", "a": "I can start within [X] weeks of an offer."},
    {"q": "Salary expectation", "a": "[your range for this country, e.g. EUR 45,000-55,000 gross per year], negotiable for the right role."},
    {"q": "Languages", "a": "English (fluent)"},
    {"q": "Highest level of education", "a": "[Your highest degree, university and grade]"},
    {"q": "Years of relevant experience", "a": "[X] years"},
    {"q": "LinkedIn", "a": "{linkedin}"},
    {"q": "Portfolio / GitHub", "a": "[Your GitHub or portfolio link]"},
    {"q": "Why do you want to work at {company}?",
     "a": "[One specific reason: product, market or news about {company}.] The {title} role fits my experience in "
          "data-driven analysis and AI-assisted automation, and I want to contribute to an international team."},
]

DEFAULT_SETTINGS = {
    "name": "", "email": "", "linkedin": "",
    "permit_expiry": "2027-03-31",
    "weekly_goal": 15,
    "role_groups": list(A.TRACK_NAMES),
    "ok_languages": ["English"],
    "priority_countries": ["Hungary", "Germany", "Netherlands"],
    "home_country": "Hungary",
    "target_countries": list(TARGET_REGIONS),
    "allow_remote_europe": True,
    "allow_worldwide_remote": False,
    "require_sponsor_signal": True,
    "home_no_sponsorship_needed": True,
    "hide_senior": True,
    "letter_intro": "",
    "letter_growth": "",
    "work_auth_sentence": ("I am open to relocating within the EU and would need employer support "
                           "for a work permit / EU Blue Card."),
    "work_auth_home": "I live in Hungary on a residence permit for job seeking and would need employer support to switch to a work-based permit.",
    "work_auth_abroad": "Not yet: I would need visa sponsorship (e.g. an EU Blue Card or national skilled-worker permit).",
    "letter_auth_home": "",
    "sponsorship_answer_home": "No. I only need a signed employment contract to update my residence permit; no sponsorship process is required.",
    "sponsorship_answer_abroad": "Yes, I would need visa sponsorship.",
    "form_answers": DEFAULT_FORM_ANSWERS,
    "sources": ["arbeitnow", "themuse", "remotive", "remoteok", "jobicy", "himalayas", "adzuna", "jooble"],
    "jooble_key": "",
    "adzuna_app_id": "", "adzuna_app_key": "",
    "anthropic_api_key": "", "anthropic_model": "",
}

# ------------------------------------------------------------------ storage


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        if isinstance(default, dict):
            d = dict(default)
            d.update(v)
            return d
        return v
    except Exception:
        return default


def save_json(path, v):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v, f, indent=2, ensure_ascii=False)


def settings():
    return load_json(SETTINGS_PATH, DEFAULT_SETTINGS)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def cv_text():
    return _read(CV_PATH)


def cv_analytics():
    return _read(CV2_PATH)


def cv_for_track(track):
    """Analytics & Growth jobs use the analytics CV (if you have one); everything else the finance CV."""
    if track == "Analytics & Growth" and cv_analytics().strip():
        return "Analytics & Growth CV", cv_analytics()
    return "Finance & Investment CV", cv_text()


DB_LOCK = threading.RLock()


@contextlib.contextmanager
def db():
    """Open a connection, commit on success, always close."""
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with DB_LOCK, db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, source TEXT, title TEXT, company TEXT, location TEXT, remote INT, url TEXT,
            description TEXT, posted TEXT, salary TEXT, tags TEXT, visa_flag INT,
            sponsor_label TEXT, sponsor_score INT, sponsor_evidence TEXT, role_score INT, role_group TEXT,
            senior INT, match_score INT, matched TEXT, missing TEXT, lang_flags TEXT, region TEXT,
            rank INT, status TEXT DEFAULT 'new', notes TEXT DEFAULT '', applied_at TEXT, follow_up TEXT,
            first_seen TEXT, last_seen TEXT, dup_key TEXT)""")
        cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
        for col, typ in (("visible", "INT DEFAULT 1"), ("hide_reason", "TEXT DEFAULT ''"), ("cv_used", "TEXT DEFAULT ''")):
            if col not in cols:
                con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
        con.execute("CREATE INDEX IF NOT EXISTS ix_status ON jobs(status)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_dup ON jobs(dup_key)")
        con.execute("""CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, at TEXT, status TEXT)""")


# ------------------------------------------------------------------ region logic

EU_COUNTRIES = {
    "Hungary": ["hungary", "budapest", "debrecen", "szeged", "győr", "gyor", "pécs", "pecs", "miskolc"],
    "Germany": ["germany", "deutschland", "berlin", "munich", "münchen", "muenchen", "hamburg", "frankfurt",
                "cologne", "köln", "stuttgart", "düsseldorf", "dusseldorf", "leipzig", "dresden", "nuremberg",
                "nürnberg", "hannover", "bonn", "karlsruhe", "essen", "dortmund", "bremen", "mannheim"],
    "Netherlands": ["netherlands", "amsterdam", "rotterdam", "utrecht", "eindhoven", "the hague", "den haag",
                    "holland", "groningen"],
    "Ireland": ["ireland", "dublin", "cork", "galway"],
    "Austria": ["austria", "vienna", "wien", "graz", "linz"],
    "Poland": ["poland", "warsaw", "kraków", "krakow", "wrocław", "wroclaw", "gdańsk", "gdansk", "poznań"],
    "Czechia": ["czech", "prague", "praha", "brno"],
    "Portugal": ["portugal", "lisbon", "lisboa", "porto"],
    "Spain": ["spain", "madrid", "barcelona", "valencia", "málaga", "malaga"],
    "France": ["france", "paris", "lyon"],
    "Belgium": ["belgium", "brussels", "antwerp", "ghent"],
    "Luxembourg": ["luxembourg"],
    "Nordics": ["sweden", "stockholm", "denmark", "copenhagen", "finland", "helsinki", "norway", "oslo",
                "estonia", "tallinn"],
    "Other EU": ["italy", "milan", "rome", "romania", "bucharest", "cluj", "slovakia", "bratislava", "slovenia",
                 "ljubljana", "croatia", "zagreb", "greece", "athens", "cyprus", "malta", "lithuania", "vilnius",
                 "latvia", "riga", "bulgaria", "sofia"],
    "Switzerland/UK": ["switzerland", "zurich", "zürich", "geneva", "united kingdom", " uk ", "london",
                       "england", "manchester", "edinburgh"],
}
EUROPE_WIDE = ["europe", "emea", " eu ", "european union", "cet", "cest", "worldwide", "anywhere", "global",
               "international"]
OUTSIDE = ["usa", "united states", " us ", "u.s.", "canada", "north america", "americas", "latam",
           "latin america", "brazil", "mexico", "australia", "india", "philippines", "asia", "apac", "pst", "est ",
           "new york", "san francisco", "california", "texas", "toronto", "singapore", "remote - us", "us only",
           "us-only", "israel", "south africa", "japan"]


EUROPE_EXPLICIT = ["europe", "emea", " eu ", "(eu)", "eu-", "european union", " eea", "cet", "cest",
                   "european time", "european timezone"]
WORLDWIDE = ["worldwide", "anywhere", "global", "international", "world"]


def region_of(location, remote, text=""):
    loc = A.norm(location)
    for country, terms in EU_COUNTRIES.items():
        if any(t in loc for t in terms):
            return country
    if any(t in loc for t in EUROPE_EXPLICIT):
        return "Remote Europe" if remote or "remote" in loc else "Europe"
    if any(t in loc for t in OUTSIDE):
        return "Outside Europe"
    if any(t in loc for t in WORLDWIDE):
        return "Remote (worldwide)"
    if remote or "remote" in loc or "flexible" in loc:
        t = A.norm(text)[:5000]
        if any(x in t for x in [" us only", "based in the us", "us-based", "united states only", "within the us",
                                 "must be located in the us", "authorized to work in the us", "us citizens",
                                 "north america", "us time zone", "est time", "pst"]):
            return "Outside Europe"
        if any(x in t for x in ["based in europe", "within europe", "in the eu", "european time", "cet", "emea",
                                 "anywhere in europe", "eu-based", "europe-based"]):
            return "Remote Europe"
        return "Remote (unspecified)"
    return "Unknown"


def hide_reason(an, cfg):
    """Why a job is filtered out of your feed ('' = it is shown)."""
    if an["role_score"] <= 0:
        return "Not one of your target roles"
    langs = json.loads(an["lang_flags"])
    if langs:
        return "Requires " + ", ".join(langs)
    region = an["region"]
    home = cfg.get("home_country", "Hungary")
    at_home = region == home
    # In your home country a job contract is enough for your permit, so sponsorship wording doesn't matter.
    if an["sponsor_label"] == "No" and not (at_home and cfg.get("home_no_sponsorship_needed", True)):
        return "Says no sponsorship / needs existing work rights"
    allowed = set(cfg.get("target_countries") or TARGET_REGIONS)
    if cfg.get("allow_remote_europe", True):
        allowed |= {"Remote Europe", "Europe"}
    if cfg.get("allow_worldwide_remote"):
        allowed |= {"Remote (worldwide)"}
    if region not in allowed:
        return f"Location: {region}"
    if cfg.get("require_sponsor_signal", True) and not at_home and an["sponsor_label"] == "Unknown":
        return f"No sponsorship signal (outside {home})"
    if cfg.get("hide_senior", True) and an["senior"]:
        return "Senior role"
    return ""


# ------------------------------------------------------------------ analysis


def analyse(j, cfg, cv=None):
    text = f"{j['title']} {j['description']} {' '.join(j.get('tags') or [])}"
    s_label, s_score, s_ev = A.sponsorship(text, j.get("visa_flag"))
    r_score, r_group, senior = A.role_relevance(j["title"], text, cfg.get("role_groups"))
    cv_label, cv = cv_for_track(r_group)
    m = A.cv_match(cv, text)
    langs = A.language_flags(text, cfg.get("ok_languages") or ["English"])
    region = region_of(j.get("location", ""), j.get("remote"), text)
    rank = r_score * 0.45 + m["score"] * 0.25
    at_home = region == cfg.get("home_country", "Hungary") and cfg.get("home_no_sponsorship_needed", True)
    rank += {"Sponsors": 25, "Likely": 12, "Unknown": 0, "No": -40}[s_label] if not at_home else 15
    rank -= 25 * len(langs)
    if region == cfg.get("home_country", "Hungary"):
        rank += 12
    elif region in (cfg.get("priority_countries") or []):
        rank += 8
    if region == "Outside Europe":
        rank -= 40
    try:
        if j.get("posted") and (date.today() - date.fromisoformat(j["posted"][:10])).days <= 7:
            rank += 8
    except Exception:
        pass
    an = dict(sponsor_label=s_label, sponsor_score=s_score, sponsor_evidence=json.dumps(s_ev),
              role_score=r_score, role_group=r_group, senior=int(senior), match_score=m["score"],
              matched=json.dumps(m["matched"]), missing=json.dumps(m["missing"]),
              lang_flags=json.dumps(langs), region=region, rank=int(round(rank)), cv_used=cv_label)
    reason = hide_reason(an, cfg)
    an["hide_reason"] = reason
    an["visible"] = 0 if reason else 1
    return an


def job_id(j):
    raw = f"{j['source']}|{j.get('ext_id') or j.get('url')}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def dup_key(j):
    t = re.sub(r"[^a-z0-9]", "", (j.get("title") or "").lower())
    c = re.sub(r"[^a-z0-9]", "", (j.get("company") or "").lower())
    return f"{c}:{t}"


def upsert(jobs, cfg, cv):
    now = datetime.now().isoformat(timespec="seconds")
    new = 0
    with DB_LOCK, db() as con:
        for j in jobs:
            if not j.get("title"):
                continue
            jid, dk = job_id(j), dup_key(j)
            row = con.execute("SELECT id FROM jobs WHERE id=?", (jid,)).fetchone()
            if row:
                con.execute("UPDATE jobs SET last_seen=? WHERE id=?", (now, jid))
                continue
            # same job from another board? keep first, but merge visa flag info
            dup = con.execute("SELECT id, visa_flag FROM jobs WHERE dup_key=?", (dk,)).fetchone()
            if dup:
                con.execute("UPDATE jobs SET last_seen=? WHERE id=?", (now, dup["id"]))
                continue
            an = analyse(j, cfg, cv)
            row = dict(id=jid, source=j["source"], title=j["title"], company=j["company"],
                       location=j["location"], remote=int(bool(j["remote"])), url=j["url"],
                       description=j["description"], posted=j["posted"], salary=j.get("salary", ""),
                       tags=json.dumps(j.get("tags") or []),
                       visa_flag=None if j.get("visa_flag") is None else int(bool(j["visa_flag"])),
                       status="new", first_seen=now, last_seen=now, dup_key=dk, **an)
            con.execute(f"INSERT INTO jobs({','.join(row)}) VALUES({','.join('?' * len(row))})",
                        tuple(row.values()))
            new += 1
    return new


def reanalyse_all():
    cfg, cv = settings(), cv_text()
    with DB_LOCK, db() as con:
        rows = con.execute("SELECT * FROM jobs").fetchall()
        for r in rows:
            j = dict(r)
            j["tags"] = json.loads(j["tags"] or "[]")
            j["visa_flag"] = None if r["visa_flag"] is None else bool(r["visa_flag"])
            an = analyse(j, cfg, cv)
            sets = ",".join(f"{k}=?" for k in an)
            con.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*an.values(), r["id"]))
    return len(rows)


# ------------------------------------------------------------------ refresh runner

REFRESH = {"running": False, "log": [], "started": None, "finished": None, "watch_status": {}}


def run_refresh(selected):
    REFRESH.update(running=True, log=[], started=datetime.now().isoformat(timespec="seconds"), finished=None)

    def log(msg):
        REFRESH["log"].append(f"{datetime.now():%H:%M:%S}  {msg}")

    cfg, cv = settings(), cv_text()
    total_new = 0
    try:
        for key in selected:
            if key == "watchlist":
                continue
            if key not in S.SOURCES:
                continue
            label, fn = S.SOURCES[key]
            log(f"Fetching {label}…")
            try:
                jobs = fn(cfg, log)
                n = upsert(jobs, cfg, cv)
                total_new += n
                log(f"  → {n} new")
            except Exception as e:
                log(f"  ✗ {label} failed: {e}")
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    log("    Mac fix: open Applications › Python 3.x and double-click 'Install Certificates.command', "
                        "then restart JobRadar.")
        if "watchlist" in selected:
            log("Checking company watchlist…")
            jobs, status = S.watchlist(cfg, log, load_json(WATCH_PATH, []))
            REFRESH["watch_status"] = status
            n = upsert(jobs, cfg, cv)
            total_new += n
            log(f"  → {n} new")
            for k, v in status.items():
                if v.startswith("error"):
                    log(f"  ! {k}: {v}  (fix it in the Startups tab)")
        log(f"Done. {total_new} new jobs added.")
    except Exception:
        log("Unexpected error:\n" + traceback.format_exc())
    finally:
        REFRESH.update(running=False, finished=datetime.now().isoformat(timespec="seconds"))


# ------------------------------------------------------------------ AI cover letter (optional)


def _anthropic(cfg, path, payload=None):
    req = urllib.request.Request("https://api.anthropic.com" + path,
                                 data=json.dumps(payload).encode() if payload else None,
                                 method="POST" if payload else "GET",
                                 headers={"x-api-key": cfg["anthropic_api_key"], "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=90, context=S.CTX) as r:
        return json.loads(r.read().decode())


def newest_sonnet(cfg):
    """Ask the API which models exist and pick the newest Sonnet (list is newest-first)."""
    models = _anthropic(cfg, "/v1/models?limit=100").get("data", [])
    for m in models:
        if "sonnet" in m.get("id", ""):
            return m["id"]
    return models[0]["id"] if models else None


def letter_profile(cfg, j):
    """For jobs in your home country, the letter says no sponsorship is needed."""
    if j.get("region") == cfg.get("home_country", "Hungary") and cfg.get("home_no_sponsorship_needed", True):
        p = dict(cfg)
        p["work_auth_sentence"] = cfg.get("letter_auth_home") or (
            f"I am based in {cfg.get('home_country', 'Hungary')} and do not need visa sponsorship: "
            "a signed employment contract is all that is required for my work permit, so I can start promptly.")
        return p
    return cfg


def ai_letter(cfg, cv, j):
    prompt = A.ai_prompt(cfg, cv, j)
    model = cfg.get("anthropic_model") or ""
    for attempt in range(2):
        if not model:
            model = newest_sonnet(cfg)
        try:
            data = _anthropic(cfg, "/v1/messages", {"model": model, "max_tokens": 900,
                                                    "messages": [{"role": "user", "content": prompt}]})
            if model != cfg.get("anthropic_model"):
                cfg["anthropic_model"] = model  # remember the model that worked
                save_json(SETTINGS_PATH, cfg)
            return "".join(b.get("text", "") for b in data.get("content", []))
        except urllib.error.HTTPError as e:
            if e.code in (400, 404) and attempt == 0:
                model = ""  # model name retired or wrong: look up a current one and retry
                continue
            raise


# ------------------------------------------------------------------ HTTP API

LIST_COLS = ("id,source,title,company,location,remote,url,posted,salary,sponsor_label,sponsor_score,role_score,"
             "role_group,senior,match_score,lang_flags,region,rank,status,notes,applied_at,follow_up,first_seen,"
            "visible,hide_reason,cv_used")


def row_out(r, full=False):
    d = dict(r)
    for k in ("lang_flags", "matched", "missing", "sponsor_evidence", "tags"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def stats():
    cfg = settings()
    with DB_LOCK, db() as con:
        by = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
        week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        week = con.execute("SELECT COUNT(*) FROM jobs WHERE applied_at>=?", (week_start,)).fetchone()[0]
        due = con.execute("SELECT COUNT(*) FROM jobs WHERE follow_up IS NOT NULL AND follow_up!='' AND "
                          "follow_up<=? AND status IN ('applied','interview')", (date.today().isoformat(),)
                          ).fetchone()[0]
        sponsors = con.execute("SELECT COUNT(*) FROM jobs WHERE visible=1 AND status='new'").fetchone()[0]
        hidden = {}
        for r in con.execute("SELECT hide_reason, COUNT(*) n FROM jobs WHERE visible=0 AND status='new' "
                             "GROUP BY hide_reason"):
            key = (r["hide_reason"] or "Other").split(":")[0].split(" (")[0]
            key = "Requires another language" if key.startswith("Requires ") else key
            hidden[key] = hidden.get(key, 0) + r["n"]
        total = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    try:
        days_left = (date.fromisoformat(cfg["permit_expiry"]) - date.today()).days
    except Exception:
        days_left = None
    return {"by_status": by, "applied_this_week": week, "weekly_goal": cfg.get("weekly_goal", 15),
            "followups_due": due, "sponsor_new": sponsors, "hidden": hidden, "total": total, "days_left": days_left,
            "permit_expiry": cfg.get("permit_expiry"), "name": cfg.get("name", "")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype or "json" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def _trusted(self):
        """Only answer requests meant for this local app (blocks other websites
        from reading or changing your data through the browser)."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return False
        return True

    # ---- GET
    def do_GET(self):
        if not self._trusted():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        p, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if p in ("/", "/index.html"):
                with open(first_existing("static/index.html", "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if p == "/api/stats":
                return self._send(200, stats())
            if p == "/api/jobs":
                return self._send(200, self.list_jobs(q))
            if p.startswith("/api/job/"):
                with DB_LOCK, db() as con:
                    r = con.execute("SELECT * FROM jobs WHERE id=?", (p.split("/")[-1],)).fetchone()
                return self._send(200 if r else 404, row_out(r, True) if r else {"error": "not found"})
            if p == "/api/settings":
                s = settings()
                for k in ("adzuna_app_key", "anthropic_api_key", "jooble_key"):
                    s[k + "_set"] = bool(s.get(k))
                    s[k] = ""
                s["available_sources"] = {k: v[0] for k, v in S.SOURCES.items()}
                s["all_tracks"] = list(A.TRACK_NAMES)
                s["all_regions"] = list(TARGET_REGIONS)
                return self._send(200, s)
            if p == "/api/cv":
                return self._send(200, {"cv": cv_text(), "cv_analytics": cv_analytics()})
            if p.startswith("/api/kit/"):
                return self.kit(p.split("/")[-1])
            if p == "/api/watchlist":
                return self._send(200, {"companies": load_json(WATCH_PATH, []),
                                        "status": REFRESH.get("watch_status", {})})
            if p == "/api/refresh":
                return self._send(200, REFRESH)
            if p == "/api/export.csv":
                return self.export_csv()
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def list_jobs(self, q):
        cfg = settings()
        where, args = [], []
        status = q.get("status", "new")
        if status == "pipeline":
            where.append("status IN ('saved','applied','interview','offer','rejected')")
        elif status != "all":
            where.append("status=?")
            args.append(status)
        if q.get("sponsor") == "yes":
            where.append("sponsor_label IN ('Sponsors','Likely')")
        elif q.get("sponsor") == "strong":
            where.append("sponsor_label='Sponsors'")
        if q.get("nolang") == "1":
            where.append("lang_flags='[]'")
        if q.get("region"):
            where.append("region=?")
            args.append(q["region"])
        if q.get("group"):
            where.append("role_group=?")
            args.append(q["group"])
        if status == "new":
            where.append("visible=0" if q.get("hidden") == "1" else "visible=1")
        if q.get("q"):
            where.append("(title LIKE ? OR company LIKE ? OR location LIKE ? OR description LIKE ?)")
            args += [f"%{q['q']}%"] * 4
        sql = f"SELECT {LIST_COLS} FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        order = {"rank": "rank DESC", "date": "posted DESC", "match": "match_score DESC",
                 "applied": "applied_at DESC"}.get(q.get("sort", "rank"), "rank DESC")
        sql += f" ORDER BY {order} LIMIT {int(q.get('limit', 400))}"
        with DB_LOCK, db() as con:
            rows = [row_out(r) for r in con.execute(sql, args)]
            regions = [r[0] for r in con.execute("SELECT DISTINCT region FROM jobs ORDER BY region")]
        return {"jobs": rows, "regions": regions}

    def export_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = ["status", "title", "company", "location", "region", "sponsor_label", "match_score", "rank",
                "applied_at", "follow_up", "notes", "url", "source", "posted"]
        w.writerow(cols)
        with DB_LOCK, db() as con:
            for r in con.execute(f"SELECT {','.join(cols)} FROM jobs WHERE status!='new' AND status!='hidden' "
                                 "ORDER BY applied_at DESC"):
                w.writerow(list(r))
        body = buf.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", "attachment; filename=jobradar_applications.csv")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- POST
    def do_POST(self):
        if not self._trusted():
            return self._send(403, {"error": "forbidden"})
        p = urlparse(self.path).path
        b = self._body()
        try:
            if p == "/api/refresh":
                if REFRESH["running"]:
                    return self._send(409, {"error": "already running"})
                sel = b.get("sources") or settings().get("sources", []) + ["watchlist"]
                threading.Thread(target=run_refresh, args=(sel,), daemon=True).start()
                return self._send(200, {"ok": True})
            if p.startswith("/api/job/"):
                return self.update_job(p.split("/")[-1], b)
            if p == "/api/jobs/add":
                j = S.job(source="Manual", ext_id=b.get("url") or b.get("title", "") + b.get("company", ""),
                          title=b.get("title", ""), company=b.get("company", ""), location=b.get("location", ""),
                          remote="remote" in (b.get("location", "").lower()), url=b.get("url", ""),
                          description=b.get("description", ""), posted=date.today().isoformat())
                if not j["title"]:
                    return self._send(400, {"error": "title required"})
                upsert([j], settings(), cv_text())
                jid = job_id(j)
                with DB_LOCK, db() as con:
                    con.execute("UPDATE jobs SET status='saved' WHERE id=?", (jid,))
                return self._send(200, {"ok": True, "id": jid})
            if p == "/api/settings":
                s = settings()
                for k, v in b.items():
                    if k in DEFAULT_SETTINGS:
                        if k in ("adzuna_app_key", "anthropic_api_key", "jooble_key") and not v:
                            continue  # blank = keep existing secret
                        s[k] = v
                save_json(SETTINGS_PATH, s)
                n = reanalyse_all()
                return self._send(200, {"ok": True, "reanalysed": n})
            if p == "/api/cv":
                with open(CV_PATH, "w", encoding="utf-8") as f:
                    f.write(b.get("cv", ""))
                if "cv_analytics" in b:
                    with open(CV2_PATH, "w", encoding="utf-8") as f:
                        f.write(b.get("cv_analytics", ""))
                n = reanalyse_all()
                return self._send(200, {"ok": True, "reanalysed": n})
            if p == "/api/watchlist":
                save_json(WATCH_PATH, b.get("companies", []))
                return self._send(200, {"ok": True})
            if p == "/api/watchlist/parse":
                ats, token = S.parse_careers_url(b.get("url", ""))
                return self._send(200 if ats else 400, {"ats": ats, "token": token} if ats else
                                  {"error": "Not a Greenhouse / Lever / Ashby / Workable careers URL"})
            if p == "/api/watchlist/test":
                fn = S.ATS.get(b.get("ats"))
                try:
                    n = sum(1 for _ in fn(b.get("token", "")))
                    return self._send(200, {"ok": True, "count": n})
                except Exception as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if p == "/api/match":
                ms = [A.cv_match(c, b.get("text", "")) for c in (cv_text(), cv_analytics()) if c.strip()]
                best = max(ms, key=lambda m: m["score"]) if ms else A.cv_match("", "")
                return self._send(200, {**best,
                                        "role": A.role_relevance(b.get("title", ""), b.get("text", ""), None)[1],
                                        "sponsor": A.sponsorship(b.get("text", "")),
                                        "languages": A.language_flags(b.get("text", ""),
                                                                      settings().get("ok_languages"))})
            if p.startswith("/api/letter/"):
                return self.letter(p.split("/")[-1], b)
            if p == "/api/reanalyse":
                return self._send(200, {"reanalysed": reanalyse_all()})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def update_job(self, jid, b):
        allowed = {k: b[k] for k in ("status", "notes", "follow_up", "applied_at") if k in b}
        with DB_LOCK, db() as con:
            r = con.execute("SELECT status, applied_at FROM jobs WHERE id=?", (jid,)).fetchone()
            if not r:
                return self._send(404, {"error": "not found"})
            if allowed.get("status") == "applied" and not r["applied_at"] and "applied_at" not in allowed:
                allowed["applied_at"] = date.today().isoformat()
                if "follow_up" not in allowed:
                    allowed["follow_up"] = (date.today() + timedelta(days=7)).isoformat()
            if allowed:
                con.execute(f"UPDATE jobs SET {','.join(k + '=?' for k in allowed)} WHERE id=?",
                            (*allowed.values(), jid))
            if "status" in allowed and allowed["status"] != r["status"]:
                con.execute("INSERT INTO history(job_id, at, status) VALUES(?,?,?)",
                            (jid, datetime.now().isoformat(timespec="seconds"), allowed["status"]))
        return self._send(200, {"ok": True, **allowed})

    def kit(self, jid):
        """Everything needed to apply: which CV, cover letter, ready answers, email draft link."""
        cfg = settings()
        with DB_LOCK, db() as con:
            r = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            return self._send(404, {"error": "not found"})
        j = dict(r)
        cv_label, cv = cv_for_track(j.get("role_group"))
        letter = A.cover_letter(letter_profile(cfg, j), cv, j)
        home = j.get("region") == cfg.get("home_country", "Hungary")
        fills = {
            "company": j.get("company") or "the company", "title": j.get("title") or "this role",
            "country": j.get("region") or "", "linkedin": cfg.get("linkedin", ""),
            "work_auth": cfg.get("work_auth_home") if home else cfg.get("work_auth_abroad"),
            "sponsorship_answer": (cfg.get("sponsorship_answer_home") if home
                                   else cfg.get("sponsorship_answer_abroad")),
        }
        answers = []
        for qa in cfg.get("form_answers") or DEFAULT_FORM_ANSWERS:
            q, a = qa.get("q", ""), qa.get("a", "")
            for k, v in fills.items():
                q, a = q.replace("{" + k + "}", v or ""), a.replace("{" + k + "}", v or "")
            answers.append({"q": q, "a": a})
        emails = sorted(set(e.rstrip(".") for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", j.get("description") or "")
                            if not re.search(r"noreply|no-reply|example\.", e, re.I)))[:3]
        mailto = ""
        if emails:
            subject = f"Application: {j.get('title')} - {cfg.get('name', '')}".strip(" -")
            body = letter[:1800] + "\n\n[Attach your " + cv_label + " as PDF]"
            mailto = f"mailto:{emails[0]}?subject={quote(subject)}&body={quote(body)}"
        return self._send(200, {"cv_label": cv_label, "letter": letter, "answers": answers, "emails": emails,
                                "mailto": mailto, "url": j.get("url"), "track": j.get("role_group")})

    def letter(self, jid, b):
        cfg = settings()
        with DB_LOCK, db() as con:
            r = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            return self._send(404, {"error": "not found"})
        j = dict(r)
        cv = cv_for_track(j.get("role_group"))[1]
        if b.get("mode") == "ai":
            if not cfg.get("anthropic_api_key"):
                return self._send(400, {"error": "Add an Anthropic API key in Settings to use AI letters."})
            try:
                return self._send(200, {"letter": ai_letter(letter_profile(cfg, j), cv, j), "mode": "ai"})
            except Exception as e:
                return self._send(502, {"error": f"AI request failed: {e}"})
        return self._send(200, {"letter": A.cover_letter(letter_profile(cfg, j), cv, j), "mode": "template"})


def first_existing(*rel_paths):
    """Find a file whether the app is laid out in folders or flat (e.g. after a GitHub web upload)."""
    for rp in rel_paths:
        p = os.path.join(HERE, *rp.split("/"))
        if os.path.exists(p):
            return p
    return os.path.join(HERE, *rel_paths[0].split("/"))


def seed_files():
    seed_dir = os.path.join(HERE, "seed") if os.path.isdir(os.path.join(HERE, "seed")) else HERE
    for name, target in (("cv.txt", CV_PATH), ("cv_analytics.txt", CV2_PATH), ("watchlist.json", WATCH_PATH),
                         ("settings.json", SETTINGS_PATH)):
        src = os.path.join(seed_dir, name)
        if not os.path.exists(target) and os.path.exists(src):
            with open(src, encoding="utf-8") as f, open(target, "w", encoding="utf-8") as g:
                g.write(f.read())


def main():
    url = f"http://127.0.0.1:{PORT}"
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"JobRadar is already running - opening {url}")
            webbrowser.open(url)
            return
    seed_files()
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"JobRadar running at {url}  (close this window or press Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
