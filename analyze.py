"""Scoring logic: visa sponsorship signals, role relevance, language requirements,
CV keyword match, and cover-letter drafting. Pure standard library."""
import html
import re

# ---------------------------------------------------------------- text helpers

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(raw):
    """Strip HTML and collapse whitespace."""
    if not raw:
        return ""
    txt = str(raw)
    if "&lt;" in txt:  # some APIs send escaped HTML
        txt = html.unescape(txt)
    txt = TAG_RE.sub(" ", txt)
    txt = html.unescape(txt)
    return WS_RE.sub(" ", txt).strip()


def norm(s):
    return " " + WS_RE.sub(" ", (s or "").lower()) + " "


# ---------------------------------------------------------------- sponsorship

SPONSOR_POSITIVE = [
    (r"visa sponsorship (is )?(available|provided|offered|possible)", 4),
    (r"(we|will|can|happy to|able to) (offer |provide )?sponsor", 4),
    (r"sponsor(ship)? (for )?(your |a |the )?(visa|work permit|blue card)", 4),
    (r"(offers?|provides?|including|includes|with) (full )?(visa sponsorship|visa support|relocation and visa)", 4),
    (r"\bvisa (support|assistance|sponsorship)\b", 3),
    (r"\bblue card\b", 3),
    (r"\bkennismigrant|highly skilled migrant|hsm visa\b", 3),
    (r"relocation (support|package|assistance|bonus|budget)", 3),
    (r"(help|support) (you )?(with|in) (your )?relocat", 3),
    (r"work permit (support|assistance|sponsorship)", 3),
    (r"\brelocat(e|ion)\b", 1),
    (r"international (team|candidates|applicants)", 1),
    (r"english[- ]speaking (team|company|environment)|our (working|company) language is english", 1),
]

SPONSOR_NEGATIVE = [
    r"(no|not|cannot|can't|can ?not|unable to|do not|don't|won't|will not) (currently )?(offer|provide|support)?\s?(any )?(visa )?sponsor",
    r"sponsorship (is )?not (available|provided|possible|offered)",
    r"without (the need for )?(visa )?sponsorship",
    r"must (already )?(have|hold|possess) (a |the )?(valid )?(right to work|work permit|eu work|eu citizenship|residence permit)",
    r"(eu|eea) (citizenship|passport|nationals?) (is )?(required|only)",
    r"only (eu|eea) (citizens|candidates|residents)",
    r"(existing|valid) (eu )?work (authori[sz]ation|permit) (is )?required",
    r"no relocation",
    r"(must|should|need to) be (legally )?(eligible|authori[sz]ed|permitted) to work in",
    r"(eligib(le|ility)|authori[sz]ation|right) to work in (the )?(eu|european union|eea|germany|the netherlands|netherlands|uk|ireland|austria|poland|france|spain|portugal|sweden|denmark|belgium|switzerland|czech)",
    r"(valid|existing|current) (eu |german |dutch )?(work|residence) (permit|visa|authori[sz]ation)",
    r"(eu|eea|german|dutch) (work permit|residence permit|citizenship) (is )?(required|mandatory|needed|a must)",
    r"(not|unable to|cannot|can't) (offer|provide|support|consider) (any )?(visa|relocation|work permit)",
    r"visa sponsorship\W{0,3}(no|not available|n/a)\b",
    r"(do|does|will) not (offer|provide|support) (visa|relocation|sponsorship)",
    r"only (consider|accept|hire) (candidates|applicants) (who are )?(based|located|residing) in",
    r"keine (visa|visum|arbeitserlaubnis)",
]

SPONSOR_POS_RE = [(re.compile(p), w) for p, w in SPONSOR_POSITIVE]
SPONSOR_NEG_RE = [re.compile(p) for p in SPONSOR_NEGATIVE]
HELP_RE = re.compile(r"(help|helps|support|supports|assist|assists|guide)( you)?( to| in| with)?( (obtain|get|getting|obtaining|apply|applying))?|we (will )?(arrange|handle|cover|sponsor)")


def sponsorship(text, source_flag=None):
    """Return (label, score, evidence list). source_flag=True when the job
    board itself marks the job as visa-sponsoring."""
    t = norm(text)
    score, evidence = 0, []
    if source_flag:
        score += 5
        evidence.append("Job board marks this as visa-sponsoring")
    spots = []
    for rx, w in SPONSOR_POS_RE:
        m = rx.search(t)
        if m:
            score += w
            if all(abs(m.start() - x) > 90 for x in spots):  # avoid near-duplicate snippets
                spots.append(m.start())
                evidence.append(_snippet(t, m))
    for rx in SPONSOR_NEG_RE:
        m = rx.search(t)
        if m:
            before = t[max(0, m.start() - 50):m.start()]
            if HELP_RE.search(before):  # "we help you obtain a valid work permit" is positive
                continue
            score -= 8
            evidence.append("NEGATIVE: " + _snippet(t, m))
    negative = any(e.startswith("NEGATIVE") for e in evidence)
    if negative:
        evidence = [e for e in evidence if e.startswith("NEGATIVE") or e.startswith("Job board")]
    if negative:  # the ad's own words beat any board-level flag
        label = "No"
    elif score <= -3:
        label = "No"
    elif score >= 4:
        label = "Sponsors"
    elif score >= 1:
        label = "Likely"
    else:
        label = "Unknown"
    # dedupe evidence, keep it short
    seen, ev = set(), []
    for e in evidence:
        if e not in seen:
            seen.add(e)
            ev.append(e)
    return label, score, ev[:5]


def _snippet(t, m, pad=60):
    a, b = max(0, m.start() - pad), min(len(t), m.end() + pad)
    return "…" + t[a:b].strip() + "…"


# ---------------------------------------------------------------- languages

LANG_PATTERNS = {
    "German": r"(fluent|native|business[- ]level|proficient|excellent|very good|strong|c1|c2|b2)[^.]{0,40}\bgerman\b|\bgerman\b[^.]{0,25}(fluent|required|mandatory|c1|c2|b2|native|must)|deutschkenntnisse|flie(ss|ß)end(e|es)? deutsch|sehr gute deutsch",
    "Dutch": r"(fluent|native|proficient|excellent|c1|c2|b2)[^.]{0,40}\bdutch\b|\bdutch\b[^.]{0,25}(fluent|required|mandatory|native|must)|nederlands",
    "Hungarian": r"(fluent|native|proficient|excellent)[^.]{0,40}\bhungarian\b|\bhungarian\b[^.]{0,25}(fluent|required|mandatory|native|must)|magyar nyelv",
    "French": r"(fluent|native|proficient|excellent|c1|c2)[^.]{0,40}\bfrench\b|\bfrench\b[^.]{0,25}(fluent|required|mandatory|native|must)",
    "Polish": r"(fluent|native)[^.]{0,40}\bpolish\b|\bpolish\b[^.]{0,25}(fluent|required|native|must)",
    "Czech": r"(fluent|native)[^.]{0,40}\bczech\b|\bczech\b[^.]{0,25}(fluent|required|native|must)",
    "Spanish": r"(fluent|native)[^.]{0,40}\bspanish\b|\bspanish\b[^.]{0,25}(fluent|required|native|must)",
    "Portuguese": r"(fluent|native)[^.]{0,40}\bportuguese\b|\bportuguese\b[^.]{0,25}(fluent|required|native|must)",
    "Italian": r"(fluent|native)[^.]{0,40}\bitalian\b|\bitalian\b[^.]{0,25}(fluent|required|native|must)",
}
LANG_RE = {k: re.compile(v) for k, v in LANG_PATTERNS.items()}
# A job ad written in German is itself a strong signal.
GERMAN_AD_RE = re.compile(r"\b(wir suchen|deine aufgaben|ihre aufgaben|dein profil|ihr profil|was wir bieten|wir bieten|deine qualifikation)\b")


NICE_TO_HAVE_RE = re.compile(r"\b(a plus|plus|nice to have|nice-to-have|advantage|advantageous|bonus|beneficial|"
                             r"desirable|preferred|is an asset|not required|optional)\b")


STOPWORDS = {
    "English": "the and with for you are our will your this that have from team work we in of to is be as on".split(),
    "German": "und der die das mit für sie wir ihr ist von zu auf eine einen bei oder sowie unsere deine ihre wird nicht auch".split(),
    "Hungarian": "és a az hogy vagy nem egy is van munka feladatok elvárások előny amit kft munkavégzés valamint számára".split(),
    "Dutch": "en het een van je wij voor met zijn ons onze jouw bij wordt naar als ook niet heb".split(),
    "French": "et le la les des une pour avec vous nous est dans sur votre notre sont aux".split(),
    "Polish": "i w na z do się jest oraz dla nie od że jak będzie twoje nasz".split(),
    "Spanish": "y el la los las con para por una que del nuestro tu tus somos".split(),
    "Czech": "a v na s se je pro do že jako být nebo práce".split(),
}


def ad_language(text):
    """Guess the language an ad is written in from common words. Returns (lang, share)."""
    words = re.findall(r"[a-zäöüßáéíóöőúüűàèâêîôûçñłśżźćńąęěščřžýů]+", (text or "").lower()[:6000])
    if len(words) < 25:
        return "English", 1.0
    counts = {}
    for lang, sw in STOPWORDS.items():
        sset = set(sw)
        counts[lang] = sum(1 for w in words if w in sset)
    lang = max(counts, key=counts.get)
    total = sum(counts.values()) or 1
    return lang, counts[lang] / total


def language_flags(text, ok_languages=("English",)):
    t = norm(text)
    flags = []
    for lang, rx in LANG_RE.items():
        for m in rx.finditer(t):
            after = t[m.end():m.end() + 45]
            if NICE_TO_HAVE_RE.search(t[m.start():m.end()] + after):
                continue  # "German is a plus" is not a requirement
            flags.append(lang)
            break
    if GERMAN_AD_RE.search(t) and "German" not in flags:
        flags.append("German")
    lang, share = ad_language(text)
    if lang != "English" and share >= 0.4 and lang not in flags:
        flags.append(lang)  # the whole ad is written in another language
    return [f for f in flags if f not in ok_languages]


# ---------------------------------------------------------------- role relevance
# Three target tracks. A job only counts if its TITLE matches a track; the description
# can add a bonus but never qualifies a job on its own (that let unrelated jobs through).

TRACKS = {
    "Analytics & Growth": {
        "strong": ["marketing analyst", "growth analyst", "revops", "revenue operations", "marketing operations",
                   "sales operations analyst", "gtm analyst", "gtm engineer", "go-to-market analyst",
                   "gtm operations", "seo analyst", "seo specialist", "seo manager", "seo strategist", "geo specialist",
                   "ai search", "aeo", "web analyst", "digital analyst", "marketing data analyst", "insights analyst",
                   "crm analyst", "hubspot", "marketing automation", "automation specialist", "automation analyst",
                   "ai operations", "ai automation", "ai implementation", "ai specialist", "ai analyst",
                   "no-code", "low-code automation", "growth marketing analyst", "performance marketing analyst",
                   "lifecycle analyst", "content analyst"],
        "weak": ["data analyst", "bi analyst", "business intelligence", "reporting analyst", "product analyst",
                 "analytics specialist", "analytics analyst", "growth", "crm specialist", "seo"],
        "context": ["ga4", "google analytics", "looker", "hubspot", "salesforce", "n8n", "zapier", "make.com",
                    "seo", "semrush", "funnel", "attribution", "llm", "chatgpt", "automation", "b2b", "saas"],
    },
    "Finance & Business": {
        "strong": ["financial analyst", "finance analyst", "fp&a", "fp & a", "financial planning", "financial reporting",
                   "reporting analyst", "controlling", "controller", "management accountant", "general ledger",
                   "record to report", "r2r", "business analyst", "commercial analyst", "pricing analyst",
                   "revenue analyst", "treasury analyst", "treasury", "transaction services", "valuation analyst",
                   "m&a analyst", "deals analyst", "corporate finance analyst", "finance associate",
                   "finance graduate", "graduate programme", "graduate program", "finance trainee", "budget analyst",
                   "cost analyst", "strategy analyst", "business operations analyst", "operations analyst",
                   "market research analyst", "market intelligence analyst", "research analyst", "economist"],
        "weak": ["finance", "financial", "accountant", "accounting", "audit", "analyst", "consultant", "strategy"],
        "context": ["sap", "s/4hana", "ifrs", "excel", "power bi", "forecast", "budget", "variance", "month-end",
                    "shared service", "global business services", "gbs", "big four", "english"],
    },
    "Investment & Dev Finance": {
        "strong": ["investment analyst", "investment associate", "investment officer", "portfolio analyst",
                   "private equity", "venture capital", "impact invest", "development finance", "trade finance",
                   "commodity", "commodities", "credit analyst", "credit risk analyst", "business development analyst",
                   "investor relations", "fund analyst", "origination", "structured finance", "blended finance",
                   "deal analyst", "deal team", "capital markets analyst", "financial inclusion", "microfinance"],
        "weak": ["investment", "portfolio", "credit", "fund"],
        "context": ["africa", "emerging market", "frontier market", "impact", "development finance", "dfi", "sme",
                    "ghana", "west africa", "sub-saharan", "cocoa", "agri", "export", "esg", "blended"],
    },
}
TRACK_NAMES = list(TRACKS)

EXCLUDE_TITLE = ["software engineer", "software developer", "web developer", "developer", "devops", "frontend",
                 "front-end", "backend", "back-end", "full stack", "fullstack", "sre", "machine learning engineer",
                 "data engineer", "android", "ios", "security analyst", "soc analyst", "cyber", "penetration",
                 "qa analyst", "qa engineer", "test analyst", "account executive", "sales representative",
                 "sales development", "sdr", "bdr", "sales manager", "inside sales", "customer support",
                 "customer service", "support specialist", "call center", "call centre", "recruiter", "talent acquisition",
                 "nurse", "driver", "warehouse", "werkstudent", "working student", "intern", "internship",
                 "praktikum", "ausbildung", "teacher", "tutor", "physician", "doctor", "electrician", "mechanic",
                 "cook", "chef", "cashier", "store", "retail assistant", "field sales", "telemarketing"]
EXCLUDE_OVERRIDE = ["business developer", "business development analyst", "gtm engineer", "automation engineer"]
SENIOR_TITLE = ["director", "vp", "svp", "evp", "vice president", "head of", "chief", "principal", "managing partner",
                "cfo", "coo", "ceo", "cto", "team lead", "lead analyst", "senior manager", "staff"]
JUNIOR_TITLE = ["junior", "graduate", "entry", "trainee", "associate", "analyst i", "assistant analyst"]


def _word_in(phrase, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def role_relevance(title, text, wanted_groups):
    """Return (score 0-100, track name, senior flag). Score 0 = not one of your target roles."""
    tl = norm(title)
    tx = norm(text)[:8000]
    if any(_word_in(x, tl) for x in EXCLUDE_TITLE) and not any(x in tl for x in EXCLUDE_OVERRIDE):
        return 0, "", False
    wanted = [g for g in (wanted_groups or TRACK_NAMES) if g in TRACKS] or TRACK_NAMES
    best, track = 0, ""
    for g in wanted:
        t = TRACKS[g]
        if any(_word_in(k, tl) for k in t["strong"]):
            s = 70
        elif any(_word_in(k, tl) for k in t["weak"]):
            s = 45
        else:
            continue
        ctx = sum(1 for k in t["context"] if k in tx)
        s += min(20, ctx * 4)
        if s > best:
            best, track = s, g
    if not best:
        return 0, "", False
    senior = any(_word_in(x, tl) for x in SENIOR_TITLE) or _word_in("senior", tl) or _word_in("sr", tl)
    if senior:
        best -= 35
    if any(_word_in(x, tl) for x in JUNIOR_TITLE):
        best += 10
    return max(1, min(100, best)), track, senior


# ---------------------------------------------------------------- CV match

SKILLS = [
    # finance
    "financial modeling", "financial modelling", "financial analysis", "fp&a", "budgeting", "forecasting",
    "variance analysis", "valuation", "dcf", "m&a", "due diligence", "private equity", "venture capital",
    "capital raising", "fundraising", "pitch deck", "investment", "corporate finance", "risk management",
    "financial risk", "credit risk", "market risk", "operational risk", "accounting", "ifrs", "gaap",
    "reconciliation", "reporting", "financial reporting", "management reporting", "kpi", "p&l", "cash flow",
    "treasury", "audit", "compliance", "regulatory", "aml", "kyc", "controlling", "cost analysis", "pricing",
    "sap", "s/4hana", "erp", "oracle", "netsuite", "datev",
    # business
    "market research", "market intelligence", "competitive analysis", "business case", "strategy",
    "stakeholder management", "stakeholder", "project management", "process improvement", "process optimization",
    "restructuring", "cross-border", "international business", "international trade", "export", "economics",
    "econometrics", "statistics", "quantitative", "presentation", "powerpoint", "consulting",
    # data / tools
    "excel", "advanced excel", "vba", "power bi", "tableau", "looker", "looker studio", "google analytics", "ga4",
    "sql", "python", "r ", "matlab", "spss", "stata", "data analysis", "data visualization", "dashboard",
    "dashboards", "data governance", "data quality", "data hygiene", "etl", "api", "apis", "automation",
    "n8n", "zapier", "make.com", "ai", "llm", "llms", "chatgpt", "claude", "machine learning",
    # commercial / tech-adjacent
    "crm", "hubspot", "salesforce", "revops", "revenue operations", "sales operations", "marketing operations",
    "lead generation", "funnel", "pipeline", "customer success", "saas", "b2b", "fintech", "startup",
    "agile", "scrum", "jira", "notion", "google workspace", "lifecycle", "seo",
]


def _has(t, skill):
    s = skill.strip()
    if len(s) <= 3 or s in ("ai", "r", "sap", "sql", "api", "kpi", "crm", "dcf", "aml", "kyc", "seo", "etl", "vba"):
        return re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", t) is not None
    return s in t


def cv_match(cv_text, job_text):
    """Return dict with score (0-100), matched, missing skill lists."""
    cv = norm(cv_text)
    jd = norm(job_text)
    wanted = [s.strip() for s in SKILLS if _has(jd, s)]
    wanted = list(dict.fromkeys(wanted))
    if not wanted:
        return {"score": 0, "matched": [], "missing": []}
    matched = [s for s in wanted if _has(cv, s)]
    missing = [s for s in wanted if s not in matched]
    score = round(100 * len(matched) / len(wanted))
    return {"score": score, "matched": matched, "missing": missing}


# ---------------------------------------------------------------- cover letter

BULLET_RE = re.compile(r"^\s*[-•*]\s*(.+)$", re.M)


def cv_bullets(cv_text):
    return [b.strip() for b in BULLET_RE.findall(cv_text or "") if len(b.strip()) > 40]


def best_bullets(cv_text, job_text, n=3):
    jd = norm(job_text)
    jd_words = set(re.findall(r"[a-z][a-z&/+-]{3,}", jd))
    scored = []
    for b in cv_bullets(cv_text):
        bw = set(re.findall(r"[a-z][a-z&/+-]{3,}", b.lower()))
        skill_hits = sum(1 for s in SKILLS if _has(" " + b.lower() + " ", s) and _has(jd, s))
        scored.append((skill_hits * 3 + len(bw & jd_words), b))
    scored.sort(key=lambda x: -x[0])
    return [b for _, b in scored[:n]]


PRETTY = {"excel": "Excel", "advanced excel": "advanced Excel", "power bi": "Power BI", "python": "Python",
          "hubspot": "HubSpot", "salesforce": "Salesforce", "looker": "Looker", "looker studio": "Looker Studio",
          "tableau": "Tableau", "powerpoint": "PowerPoint", "matlab": "MATLAB", "s/4hana": "SAP S/4HANA",
          "claude": "Claude", "n8n": "n8n", "google analytics": "Google Analytics", "revops": "RevOps",
          "saas": "SaaS", "b2b": "B2B", "jira": "Jira", "notion": "Notion", "kpi": "KPIs", "apis": "APIs",
          "llms": "LLMs", "ifrs": "IFRS", "gaap": "GAAP", "p&l": "P&L"}


def pretty(skill):
    s = skill.strip()
    if s in PRETTY:
        return PRETTY[s]
    if len(s) <= 4 or "&" in s:
        return s.upper()
    return s


def _lower_first(s):
    s = s.rstrip(". ")
    return s[0].lower() + s[1:] if s else s


def cover_letter(profile, cv_text, job):
    """Template-based tailored letter (no AI key needed)."""
    name = profile.get("name", "")
    title = job.get("title", "the role")
    company = job.get("company", "your company")
    match = cv_match(cv_text, job.get("description", ""))
    skills = [pretty(x) for x in match["matched"][:5]]
    bullets = best_bullets(cv_text, job.get("description", ""), 3)
    skills_txt = ", ".join(skills[:-1]) + (" and " + skills[-1] if len(skills) > 1 else (skills[0] if skills else ""))

    p = []
    p.append(f"Dear Hiring Team at {company},")
    intro = profile.get("letter_intro") or (
        "I bring hands-on experience in analysis, research and data-driven decision making.")
    p.append(f"I am writing to apply for the {title} position. {intro}")
    if bullets:
        lines = [f"• {b.rstrip('.')}." for b in bullets]
        p.append("A few examples of what I have delivered that are directly relevant to this role:\n" + "\n".join(lines))
    if skills_txt:
        p.append(
            f"Your posting emphasises {skills_txt}. These are areas I use day to day"
            + ((", " + profile["letter_growth"].strip().rstrip(".") + ".") if profile.get("letter_growth") else ".")
        )
    p.append(
        f"I am particularly drawn to {company} because of the chance to contribute in a fast-moving, international "
        f"environment where rigorous analysis turns directly into decisions. [Add one specific sentence about the "
        f"company: a product, recent news, market or value that genuinely appeals to you.]"
    )
    if profile.get("work_auth_sentence"):
        p.append(profile["work_auth_sentence"])
    p.append(f"Thank you for your time and consideration. I would welcome the opportunity to discuss how I can "
             f"add value to {company}.")
    p.append(f"Kind regards,\n{name}\n{profile.get('email', '')}\n{profile.get('linkedin', '')}".rstrip())
    return "\n\n".join(p)


def ai_prompt(profile, cv_text, job):
    return (
        "Write a concise, specific cover letter (max 300 words, no clichés, no invented facts) for this candidate "
        "and job. Use only facts from the CV. End with one sentence on work authorisation using this text: "
        f"\"{profile.get('work_auth_sentence', '')}\".\n\n"
        f"CANDIDATE NAME: {profile.get('name', '')}\n\nCV:\n{cv_text}\n\n"
        f"JOB TITLE: {job.get('title')}\nCOMPANY: {job.get('company')}\nLOCATION: {job.get('location')}\n\n"
        f"JOB DESCRIPTION:\n{job.get('description', '')[:6000]}"
    )
