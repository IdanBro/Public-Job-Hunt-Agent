# Job Hunt Agent

> **STOP: are you the scheduled routine run?** (Your prompt says "running one hourly cycle".) If so, **ignore this whole file.** Your only instructions are your routine prompt and [ROUTINE.md](ROUTINE.md), and you push only to the `state` branch.
>
> Everything below is for interactive development sessions.

An hourly Claude Code cloud routine finds open jobs in central Israel, scores each against the candidate's real experience, tailors one of two CVs for every match at 55% or higher, and sends the job plus the PDF to Telegram.

## Where to start
1. **[DESIGN.md](DESIGN.md)**: every decision and the reason behind it. Read it before changing behaviour, and record new decisions there.
2. **[ROUTINE.md](ROUTINE.md)**: what the cloud agent does each run. **[config/routine-prompt.md](config/routine-prompt.md)**: the prompt stored in the routine.

## Truthfulness rules (the heart of the project)
`cv/master.yaml` and `cv/skills-inventory.md` are **the only allowed facts about the candidate**. Tailoring may reorder, emphasise and rephrase only what they support, at the level they state. Employers, titles, dates and numbers stay exact. Personal projects are never named, and `never_claim` skills in `cv/skill-policy.yaml` are never listed. `jobagent/cv.py` enforces this mechanically (`validate_tailored`). When the candidate reports new experience, ask what they built, where and for how long. Then add it to `master.yaml`, the inventory and both variants, and re-render to confirm each CV still fits on one page.

## Working on it
- **Tests:** `python -m pytest`. Add tests for every behaviour change. The routine pulls `main` every run, so a push to `main` changes the next run.
- **Local dry run:** `python -m jobagent --state-dir work/dry-state --dry-run work/dry-run fetch` (then `next` and `decide`). Messages and PDFs land in `work/dry-run/` instead of Telegram.
- **Thresholds** live in `jobagent/cli.py`: send at 55%, senior Software at 65%, batches of 12.
- **Finding more companies:** `python -m jobagent discover "Name" ...` probes Greenhouse/Lever/Ashby. For Comeet, pass the `comeet.com/jobs/...` URL. Add a board when its `central_israel` count is above 0.
- **Editing the `state` branch** (re-opening jobs): work in a temporary `git worktree`, and only between runs.

## Gotchas
- **YAML inline lists split on commas:** write `["AWS (EC2, ECR)"]`, quoted. A test guards this.
- **Typst can only read files inside the repo.** Tailored CVs live under `work/`.
- **The cloud image:** bare `pip` targets a different Python, so always use `python -m pip` and `python -m jobagent`. Debian owns PyYAML/requests, which is why requirements use version ranges.
- **Comeet's API is admin-only**, but its public career pages embed the job list as JSON, which `fetch_comeet` reads. A Comeet board is `<slug>/<uid>` from the page URL and can't be guessed, so find pages by web search.
- **Secrets** (the Telegram bot token) live only in the cloud environment. Keep them out of chat, files, git and logs.
