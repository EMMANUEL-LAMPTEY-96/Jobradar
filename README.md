# JobRadar

**An AI-assisted job-search app for international candidates looking for visa-sponsoring jobs in Europe.**

JobRadar pulls fresh listings from 7 public job APIs plus startup careers pages, scores every job on how likely it is to sponsor a visa, how well it fits your target roles and CV, and whether it needs a language you don't speak. It then drafts a tailored cover letter and tracks each application through to an offer.

Built by **Nii Lante Emmanuel Lamptey** ([LinkedIn](https://www.linkedin.com/in/emmanuel-lamptey-7120161a2/)) with Claude as an AI coding partner.

![Dashboard](docs/dashboard.png)

## Why I built it
Job boards don't filter for visa sponsorship, and most "remote" roles quietly require existing work rights. As a non-EU graduate in Hungary, I was spending hours reading ads only to find "no sponsorship" or "fluent German required" at the bottom. JobRadar reads them for me and ranks what's left.

## What it does

| Feature | How it works |
|---|---|
| **Job aggregation** | Arbeitnow (with its visa-sponsorship filter), The Muse, Remotive, RemoteOK, Jobicy, Himalayas, optional Adzuna, plus a watchlist of startups on Greenhouse, Lever, Ashby and Workable |
| **Visa-sponsorship detection** | Pattern matching on the ad text for positive signals (visa sponsorship, relocation package, EU Blue Card, highly-skilled migrant) and negative ones ("unable to sponsor", "must already have the right to work"), with the evidence shown |
| **Language filter** | Flags ads that require German, Dutch, Hungarian, French and others, while ignoring "a plus" or "nice to have" |
| **Role fit** | Classifies titles into Finance, Business/Strategy and Tech-adjacent groups; filters out engineering roles and pushes down senior titles |
| **CV match** | Compares ~150 skills between the job ad and your CV, listing what matches and what is missing |
| **Ranking** | One fit score combining role fit, CV match, sponsorship, location priority, language needs and freshness |
| **Cover letters** | A tailored template letter built from your most relevant CV bullets, or a fully AI-written letter through the Claude API |
| **Application tracker** | Pipeline from Saved to Applied, Interview and Offer, automatic 7-day follow-up reminders, weekly goal, permit-expiry countdown, CSV export |

![Job detail with sponsorship evidence and CV match](docs/job-detail.png)

![Tailored cover letter](docs/cover-letter.png)

![Application pipeline](docs/pipeline.png)

*Screenshots use sample data and fictional companies.*

## Tech
- **Python 3 standard library only:** no installs; `http.server` backend, SQLite storage, `urllib` API clients
- **Vanilla HTML/CSS/JS** single-page front end, light and dark mode
- **Claude API** (optional) for AI-written cover letters; picks the newest available model automatically
- Runs locally on `127.0.0.1`; your data never leaves your computer. Requests from other websites are blocked.

## Run it
1. Install Python 3.9+ ([python.org](https://www.python.org/downloads/)). On Windows, tick "Add python.exe to PATH".
2. Download this repo (green **Code** button → **Download ZIP**) and unzip it.
3. Double-click `start_windows.bat` (Windows) or `start_mac_linux.command` (Mac), or run `python app.py`.
4. Your browser opens at http://127.0.0.1:8765. Go to **Settings**, paste your CV and profile, then press **Fetch jobs now**.

Optional: a free [Adzuna](https://developer.adzuna.com/) key adds on-site jobs in Germany, the Netherlands, Austria, Poland and more; an [Anthropic API key](https://console.anthropic.com/) enables AI-written letters.

**Troubleshooting**
- *Mac: every source fails with CERTIFICATE_VERIFY_FAILED.* Open Applications › Python 3.x and run `Install Certificates.command`.
- *Port in use?* Run `JOBRADAR_PORT=8800 python app.py`.

## Project structure
```
app.py        local web server, API routes, SQLite storage, ranking
sources.py    job-board and careers-page (ATS) fetchers
analyze.py    sponsorship, language, role-fit, CV-match and cover-letter logic
static/       single-page user interface
seed/         sample CV, settings and startup watchlist used on first run
```

## Notes
- "Sponsorship unknown" does not mean no; many companies sponsor without saying so.
- Please don't fetch more than a few times a day; the job APIs are free community services.
- Always confirm visa rules on official government sites.

## License
MIT
