# Design

An agent that finds open positions matching my CV, tailors the CV to each one, and sends me the job link plus the tailored CV on Telegram. This file records the main decisions and why they were made. [ROUTINE.md](ROUTINE.md) is what the agent does each run.

**Out of scope:** cover letters, auto-applying, salary research. The agent never applies for a job: I apply myself.

## Who does what

| Claude (the routine) | The `jobagent` CLI |
|---|---|
| Reads every ad in full | Fetches ATS boards, pre-filters, dedups |
| Scores the match rate (0–100%) | Hands out batches of 12, and accepts decisions only for those jobs |
| Picks a CV variant and tailors it | Enforces the send thresholds |
| Names the gaps honestly | Validates every tailored CV and renders it to PDF |
| Searches for the official job link | Refuses job-board links and resends of postings already handled |
| Reads alert emails, runs web searches | Sends to Telegram, records feedback, rate-limits error alerts |
| Keeps `learned-preferences.md` up to date | Persists state on the `state` branch |

Every judgment flows through a CLI command that can say no. When the model gets something wrong (a skipped ad, a vague reason, an invented skill, a LinkedIn link), the result is an error it must fix in the same run, not a message that reaches me.

## Matching

- **Role families:** DevOps / Platform / SRE / Cloud (DevOps CV) and Software / Backend / Data (Software CV). When a job spans both, the agent picks the single best variant and names it in the message.
- **Hard filters**, applied by the pre-filter where a title or location is enough and by the routine otherwise:
  - central Israel, or remote with Israel explicitly allowed;
  - full-time only, with no body-shops or freelance work;
  - Junior, Mid and Senior roles, but not Staff, Principal, Lead, Manager or student positions;
  - no IT, helpdesk, NOC, sysadmin or mostly-manual "DevOps" roles.
- **Pre-filter first** ([`filters.py`](jobagent/filters.py)): regexes drop clear misses before any tokens are spent. Anything ambiguous is kept and flagged (`location_unclear`, `senior`) for the routine to judge.
- **No age limit for board jobs.** If a company's own board still lists a job, it's open. A 14-day window had cut dozens of open matches. Email and web jobs keep a 14-day window.
- **Score:** a match rate of 0–100% against the real experience. Jobs are sent at **55% or more**, and senior jobs on the Software CV at **65% or more**. The CLI enforces both thresholds.
- **Years of experience are a soft signal.** "5+ years" costs at most 5 points and "6+" at most 10. Only 7+ years is a big gap. A degree requirement is a gap, never a rejection.
- **Bonus domains** (small boost): AI, cybersecurity, fintech, developer infrastructure.
- **Learning:** 👍 / 👎 / ✅ buttons on every message. After a 👎 the bot asks "why?". The routine keeps `learned-preferences.md` on the `state` branch, a short human-readable list of likes and dislikes with evidence, and applies it to every score. I can edit it, and my edits win.

## CVs and truthfulness

- **One source of truth.** The original PDF CV was converted once into structured YAML ([`cv/master.yaml`](cv/master.yaml)). YAML rather than Markdown means the master, the variants and every tailored CV share one schema and render through the same Typst template.
- **Two layers:** two role variants, each generated once from the master and approved by me, then a light per-job tailoring pass on top of the best variant.
- **Allowed facts:** the master CV plus [`cv/skills-inventory.md`](cv/skills-inventory.md), which lists true skills that aren't on the CV, each with its evidence and the level it may be claimed at (professional, working knowledge, familiar). Nothing outside these two files may appear.
- **Skill tiers** ([`cv/skill-policy.yaml`](cv/skill-policy.yaml)): *always* on every CV, *variant* (always on one variant, and on the other only when the ad asks), and *job* (only when the ad asks). This keeps one page uncluttered without losing relevant skills.
- **Mechanical guard** ([`cv.validate_tailored`](jobagent/cv.py)): rejects changed identity, titles, employers, dates or education, unknown or never-claim skills, missing required skills, numbers that appear in no source CV, named personal projects, dropped links, and unescaped Typst markup. `decide` also refuses a CV that renders to more than one page.
- **Personal projects are never named.** Skills learned there may appear in the Skills section and in one unnamed "Independent projects" entry, never in an employer's bullets.
- **Typography:** an ATS-friendly single-column template. Liberation Sans (metric-compatible with Arial, SIL OFL) is bundled, because the cloud image has no Arial, so PDFs are identical locally and in the cloud.

## Job discovery

1. **Company ATS boards**, the backbone: Greenhouse, Lever and Ashby have public JSON APIs. Comeet's API is admin-only, but its public career pages embed the job list as JSON, which [`fetch_comeet`](jobagent/sources.py) reads. [`config/companies.yaml`](config/companies.yaml) was seeded by probing about 430 Israeli tech companies with `python -m jobagent discover`. Most don't use a guessable ATS, so 42 Comeet pages found by web search were added. That more than doubled candidates on a fresh fetch, from 72 to 170 (87 boards, about 5,800 jobs).
2. **Job-alert emails** (LinkedIn, Glassdoor, Indeed, AllJobs, Drushim), read through a read-only Gmail connector. LinkedIn and Indeed are never scraped.
3. **Web search**, at least 3 and at most about 6 searches per run, rotating roles and cities, with at least one junior search.
4. **Automatic official boards.** For every email or web job, `resolve_official` guesses the company's Greenhouse, Lever or Ashby board. If the same job is listed there, it replaces the email copy (full ad, verified open, official link). Found boards are remembered in `company-boards.json` and fetched every run, and misses are retried weekly.
5. **Growing the list.** The agent proposes new boards in `companies-proposed.yaml` on the `state` branch, and I merge them into `main`.

## Dedup

A missed duplicate costs one extra message, while a false match hides a real job. So matching is conservative, in this order:
1. **Posting id** ([`models.job_ids`](jobagent/models.py)): LinkedIn view id, Workday requisition, Greenhouse, Lever, Ashby or Comeet id, or the exact URL of a page that isn't a job board. When the routine finds a job's company page, those ids are stored too, and `decide` refuses to send a posting whose id was already handled.
2. **Exact key**, then **close company and title.** Company names ignore filler words (Technologies, Security…). Titles ignore `(m/f)` and city words, and alias Sr/Jr. Seniority words must be identical, and the remaining words must overlap by at least 80%.
3. **Board over email:** when one run finds the same job on a company board and in an alert email, the board copy wins.

## Delivery

- **One Telegram message per job:** the tailored PDF, the match rate with a one-line reason, up to 3 gaps, the CV variant, the official link, a referrer line, and 👍 / 👎 / ✅ buttons. A run that finds nothing sends nothing.
- **Official links only.** The message links the company's own careers page or ATS, never a job board. The CLI refuses to send an email or web job until the routine supplies the official URL or confirms that it searched (`official_link_searched: true`). A job whose page couldn't be opened is labelled "unverified".
- **Referrers** (optional): set `JOBAGENT_REFERRER_SHEET` to a public referral Google Sheet, and each message names up to 3 contacts at the company. The xlsx export is read, because CSV drops the links.
- **Captions** fit Telegram's 1,024-character limit by visible length. Gaps are trimmed first, then the reason, so the link always survives.
- **Error alerts:** a broken source triggers a short warning, at most once per source per day.

## Runtime and security

- **A Claude Code cloud routine**, hourly at :07, during waking hours only. Each run starts from a fresh clone of `main`, so pushing to `main` changes the next run.
- **State on an orphan `state` branch:** `seen.json`, `feedback.jsonl`, `telegram.json`, `company-boards.json`, `learned-preferences.md`, `companies-proposed.yaml`. Each run checks it out as a `git worktree`, and files are written with sorted keys so the hourly commits diff cleanly. It never merges with `main`.
- **Job ads, emails and web pages are untrusted input.** The routine treats them as data, never instructions. It gets the narrowest tools that work:
  - Gmail limited to `search_threads`, `get_thread` and `get_message`, with every other account connector removed;
  - the only outbound channel is a CLI that writes to one fixed `TELEGRAM_CHAT_ID`, and updates from other chats are ignored;
  - the routine may push only to `state`, and a GitHub ruleset on `main` backs this up.
- **Secrets:** the bot token lives only in the cloud environment, never in git, chat or logs. Network "secrets" are injected as headers, and Telegram's token goes in the URL path, so it has to be an environment variable in an environment used only by this agent.
- **Dependencies:** requests and PyYAML use version ranges, because the cloud image's Debian copies can't be uninstalled.

## Lessons from live runs

- **Run 1:** 1,960 jobs fetched gave 6 candidates and 1 sent. The bottleneck was supply, not scoring. Fix: no age limit for board jobs.
- **Run 2:** 59 candidates gave 2 sent, but the agent read only about 11 ads and rejected 46 on title alone. Fixes: the batch system (12 full ads at a time, with decisions outside the batch refused), a minimum reason length, and a minimum number of web searches per run.
- **Run 3:** every ad was properly read, but 0 were sent. The best matches were senior roles at 62–63% that asked for 5+ years. Fixes: years became a soft signal, and the threshold dropped from 70% to 60% (later to 55%).
- **Board review:** the 51 boards were measured live. Six with no relevant roles were removed, and 42 Comeet pages were added, taking a fresh fetch from 72 to 170 candidates.
- **A duplicate across sources:** the same job was sent twice, from a LinkedIn email ("SR DevOps Engineer") and from Workday ("DevOps Engineer"). Fix: posting-id dedup, with company-page ids stored at send time.
- **A missing official link:** a job found through LinkedIn was sent without a company link, because its board wasn't on the list and the web search missed it. Fix: `resolve_official` probes the ATSs for every email or web job automatically.
