# MEIKA — Unified AI Interactive Dashboard

A single-file Gradio app that merges two AI demos into one interface with tabs:

1. **💼 Career Conversation** — an AI alter-ego ("Siddharth Chakraborty") that
   answers career questions based on a CV, using function-calling to record
   user emails and unknown questions (via Pushover).
2. **📋 Quick Single-Turn Tests** — sanity, IQ, math problem generation,
   solving, and self-verification.
3. **🤖 3-Round Agent Simulation** — an Examiner agent grilling a Student agent,
   with a final grade.

Everything runs from **one Gradio app** and produces a **single public link**
(`share=True`).

## Features

- **OpenAI-compatible** — works with any OpenAI-compatible provider
  (Google AI Studio Gemini, AIMLAPI, etc.).
- **Model pool + auto rate-limit handling** — cools models down on `429` and
  queues work until a slot frees up.
- **Response caching** — identical prompts are answered from `cache.json` to
  save tokens.
- **Tool/function calling** for the career alter-ego.

## Requirements

- Python 3.10+
- An OpenAI-compatible API key (see below)

Install dependencies:

```bash
pip install gradio openai httpx requests python-dotenv
```

## Configuration

Copy the values into a `.env` file (`.env` is git-ignored — never commit keys):

```
API_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
API_KEY=YOUR_API_KEY
MODEL=gemini-2.5-flash
# Optional: comma-separated fallback pool
# MODEL_POOL=gemini-2.5-flash,gemini-2.0-flash
# Optional push notifications
# PUSHOVER_USER=...
# PUSHOVER_TOKEN=...
```

### Getting a free Gemini API key

1. Go to https://aistudio.google.com/apikey and sign in with a Google account.
2. Click **Create API key** → **Create API key in new project**.
3. Copy the key (starts with `AIza...`) into `API_KEY=` in your `.env`.

### Your CV (career alter-ego)

The alter-ego reads your CV from a CSV. The repo ships with a sample at
`me/SIDD.csv`. To use your own, either replace that file or change
`CV_CSV_PATH` in `meika.py`:

```
Name,Your Name
Degree,Your Degree
Skills,Skill1,Skill2,Skill3
Experience,<summarise your work history>
Contact,<your email>,<your phone>
```

## Run

```bash
python meika.py
```

A local URL is printed, and a **public .gradio.live link** appears shortly after
(you may need to scroll up in the console to see it).

There is also a watchdog launcher that auto-restarts the app:

```bash
python run.py
```

## Project layout

| File          | Purpose                                            |
|---------------|----------------------------------------------------|
| `meika.py`    | Merged single-file app (all features + UI)         |
| `run.py`      | Watchdog that restarts `meika.py` on crash         |
| `me/SIDD.csv` | CV used by the career alter-ego                    |
| `.env`        | Secrets / config (git-ignored)                     |
| `cache.json`  | Cached API responses (git-ignored)                |

## License

MIT
