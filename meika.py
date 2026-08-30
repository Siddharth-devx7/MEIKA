"""
=================================================================================
 MEIKA -- Unified AI Interactive Dashboard
=================================================================================
 A single-file merge of the original MEIKA project:

   1. orcarouter_demo.py
        * Quick single-turn tests (sanity / IQ / math problem / solve / verify)
        * 3-round Examiner <-> Student multi-agent simulation
        * Model pool with auto rate-limit handling + on-disk response cache
   2. career_conversation.py
        * "Professionally You!" -- an AI alter-ego ("Siddharth Chakraborty")
          that answers questions about your career using your CV.
        * Uses OpenAI-compatible tool/function calling so the model can record
          user emails and unknown questions via Pushover.

 The whole thing is exposed through ONE Gradio app with tabs, so it runs from a
 SINGLE launch and produces a SINGLE public link (share=True).

 README / CONFIG
 ----------------
 * All connection settings come from the .env file:
      API_BASE_URL  (OpenAI-compatible endpoint)
      API_KEY       (your provider key)
      MODEL         (primary model)
      MODEL_POOL    (comma-separated fallback pool, optional)
      PUSHOVER_USER / PUSHOVER_TOKEN  (optional phone push)

 * CV for the career alter-ego is read from a CSV (see "ADD YOUR CV HERE").

 HOW TO RUN
 ----------
   python meika.py
   Then open the local URL, or share the public ".gradio.live" link
   that is printed in the console (launched with share=True).
=================================================================================
"""

# ------------------------------------------------------------------------------
# 0. Imports
# ------------------------------------------------------------------------------
import os
import sys
import csv
import json
import time
import queue
import threading
import itertools
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import requests
import httpx
import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

# Ensure UTF-8 console output on Windows (emoji / accents)
sys.stdout.reconfigure(encoding="utf-8")

# ------------------------------------------------------------------------------
# 1. Shared configuration (single source of truth: .env)
# ------------------------------------------------------------------------------
load_dotenv(override=True)

BASE_URL = os.getenv("API_BASE_URL", "")
API_KEY = os.environ.get("API_KEY", "").strip()
MODEL = os.getenv("MODEL", "gemini-2.5-flash")
MODEL_POOL_ENV = os.getenv(
    "MODEL_POOL",
    "gemini-2.5-flash,gemini-2.0-flash",
)
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "1"))
_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_HERE, "cache.json")

if not API_KEY:
    raise RuntimeError(
        "No API key set. Add API_KEY= ... to your .env file "
        "(the project reads API_BASE_URL / API_KEY / MODEL from .env)."
    )

print(f"Connected to {BASE_URL} | primary model: {MODEL}", flush=True)

# ------------------------------------------------------------------------------
# 2. OpenAI client + model pool
# ------------------------------------------------------------------------------
client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    http_client=httpx.Client(
        headers={"User-Agent": "opencode/1.0.0"},
        timeout=60,
    ),
)

_MODEL_POOL = [MODEL]
for _m in MODEL_POOL_ENV.split(","):
    _m = _m.strip()
    if _m and _m not in _MODEL_POOL:
        _MODEL_POOL.append(_m)

print(f"Model pool: {', '.join(_MODEL_POOL)}", flush=True)

# ------------------------------------------------------------------------------
# 3. Pushover (optional) -- push notifications for the career alter-ego
# ------------------------------------------------------------------------------
pushover_user = os.getenv("PUSHOVER_USER")
pushover_token = os.getenv("PUSHOVER_TOKEN")
_PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def push(message: str) -> None:
    """Send a push notification to your phone (never breaks the conversation)."""
    print(f"Push: {message}", flush=True)
    if not pushover_user or not pushover_token:
        print("  (Pushover keys not configured - real notification not sent.)", flush=True)
        return
    payload = {"user": pushover_user, "token": pushover_token, "message": message}
    try:
        requests.post(_PUSHOVER_URL, data=payload, timeout=10)
    except Exception as exc:  # never let a push failure break the app
        print(f"  (Pushover send failed, ignoring: {exc})", flush=True)


# ------------------------------------------------------------------------------
# 4. Background queue / worker system (from orcarouter)
#    Handles rate limits by cooling models down and queueing work until a slot
#    frees up. Also caches identical prompts (cache.json) to save tokens.
# ------------------------------------------------------------------------------
_COOLDOWN = {}   # model name -> timestamp when it can be used again
_QUEUE = queue.Queue()
_RESULTS = {}
_NEXT_ID = itertools.count(1)
_CACHE = {}


def _next_midnight() -> float:
    """Timestamp of the next UTC midnight (default cooldown reset point)."""
    now = datetime.now(timezone.utc)
    return (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()


def _pick_model():
    """Return the first model currently off cooldown, else None."""
    now = time.time()
    return next((m for m in _MODEL_POOL if _COOLDOWN.get(m, 0) <= now), None)


def _is_rate_limit(exc) -> bool:
    """Best-effort detection of rate-limit / quota errors."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return (
        getattr(exc, "status_code", None) == 429
        or any(k in msg for k in ("rate limit", "too many requests", "free usage", "limit exceeded"))
    )


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache() -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def _cache_key(messages) -> str:
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


def _resp(content: str):
    """Build a minimal fake response object shaped like a real chat completion."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _sleep_until_window() -> None:
    """Sleep until at least one model is off cooldown."""
    now = time.time()
    values = list(_COOLDOWN.values())
    soonest = min(values) if values else None
    if soonest is None:
        time.sleep(10)
        return
    wait = max(0.0, soonest - now) + 5
    print(f"[QUEUE] all models cooling down; pausing for {wait / 60:.1f} min", flush=True)
    time.sleep(wait)


def _worker() -> None:
    """Consumer thread: pulls queued prompts, calls the API, stores results."""
    while True:
        task_id, messages = _QUEUE.get()
        key = _cache_key(messages)

        # Hit the on-disk cache if the exact prompt was already answered.
        if key in _CACHE:
            _RESULTS[task_id] = (_CACHE[key], _pick_model() or MODEL)
            continue

        model = _pick_model()
        if model is None:
            _QUEUE.put((task_id, messages))
            _sleep_until_window()
            continue

        try:
            response = client.chat.completions.create(model=model, messages=messages)
            content = response.choices[0].message.content
            _CACHE[key] = content
            _save_cache()
            _RESULTS[task_id] = (content, model)
        except Exception as exc:
            if _is_rate_limit(exc):
                _COOLDOWN[model] = _next_midnight()
                print(f"[AUTO-SWITCH] {model} rate limited -> queued until a slot frees", flush=True)
                _QUEUE.put((task_id, messages))
                _sleep_until_window()
            else:
                _RESULTS[task_id] = (f"[ERROR] {type(exc).__name__}: {exc}", model)


def complete(messages, timeout: int = 600):
    """Submit messages to the worker queue and wait for the result."""
    key = _cache_key(messages)
    if key in _CACHE:
        return _resp(_CACHE[key])

    task_id = next(_NEXT_ID)
    _QUEUE.put((task_id, messages))

    deadline = time.time() + timeout
    while time.time() < deadline:
        if task_id in _RESULTS:
            content, model = _RESULTS.pop(task_id)
            if content.startswith("[ERROR] "):
                raise RuntimeError(content)
            return _resp(content)
        time.sleep(0.5)

    return _resp(
        f"[PENDING] queued locally (id {task_id}); it will complete automatically "
        "when a model is available. Watch the console for queue progress."
    )


def _start_workers() -> None:
    _CACHE.update(_load_cache())
    for _ in range(max(1, MAX_WORKERS)):
        threading.Thread(target=_worker, daemon=True).start()


_start_workers()


def ask(prompt: str) -> str:
    """Convenience helper: single-turn text completion via the queue."""
    try:
        chat = complete([{"role": "user", "content": prompt}])
        return chat.choices[0].message.content
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"


# ------------------------------------------------------------------------------
# 5. Career alter-ego: tools the LLM can call (from career_conversation)
# ------------------------------------------------------------------------------
def record_user_details(email, name="Name not provided", notes="not provided"):
    """Record that a user is interested in staying in touch."""
    push(f"Recording interest from {name} with email {email} and notes {notes}")
    return {"recorded": "ok"}


def record_unknown_question(question):
    """Record a question that the chatbot couldn't answer."""
    push(f"Recording {question} asked that I couldn't answer")
    return {"recorded": "ok"}


# JSON schemas describing the tools -- sent to the LLM so it knows what it can call.
_record_user_details_json = {
    "name": "record_user_details",
    "description": "Use this tool to record that a user is interested in being in touch and provided an email address",
    "parameters": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "The email address of this user"},
            "name": {"type": "string", "description": "The user's name, if they provided it"},
            "notes": {
                "type": "string",
                "description": "Any additional information about the conversation that's worth recording to give context",
            },
        },
        "required": ["email"],
        "additionalProperties": False,
    },
}

_record_unknown_question_json = {
    "name": "record_unknown_question",
    "description": "Always use this tool to record any question that couldn't be answered as you didn't know the answer",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question that couldn't be answered"},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}

career_tools = [
    {"type": "function", "function": _record_user_details_json},
    {"type": "function", "function": _record_unknown_question_json},
]


def handle_tool_calls(tool_calls):
    """Run each tool the LLM requested and return the results back to the LLM."""
    results = []
    for tool_call in tool_calls:
        tool_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)
        print(f"[Tool called: {tool_name}]", flush=True)

        # Call the global function whose name matches the tool.
        tool = globals().get(tool_name)
        result = tool(**arguments) if tool else {}

        results.append({
            "role": "tool",
            "content": json.dumps(result),
            "tool_call_id": tool_call.id,
        })
    return results


# =============================================================================
#  >>>>>>>>>>>>>>>>>>>  ADD YOUR CV HERE  <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
# -----------------------------------------------------------------------------
# Point CV_CSV_PATH at your CV / resume saved as a CSV file.
#   - Default: C:\Users\Siddharth\Desktop\SIDD.csv
#   - A backup lives at me/SIDD.csv in case the Desktop file moves.
#
#   Format: every row becomes one line of plain text the chatbot reads.
#     Name,Siddharth Chakraborty
#     Degree,BSc Computer Science
#     Skills,Python,Java,SQL,Gradio
#     Experience,<summarise your work history here>
#     Contact,<your email>,<your phone>
#
# Optional: put a short bio in  me/summary.txt  (chatbot runs off CV without it).
# =============================================================================

CV_CSV_PATH = r"C:\Users\Siddharth\Desktop\SIDD.csv"
BACKUP_CV_CSV_PATH = os.path.join(_HERE, "me", "SIDD.csv")


def load_cv_from_csv(csv_path: str) -> str:
    """Read a CV stored in a CSV file and flatten it into plain text."""
    if not os.path.exists(csv_path):
        return ""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            cells = [cell.strip() for cell in row if cell.strip()]
            if cells:
                rows.append(" | ".join(cells))
    return "\n".join(rows)


def load_summary(txt_path: str) -> str:
    """Read the optional summary.txt file (returns '' if absent)."""
    if not os.path.exists(txt_path):
        return ""
    with open(txt_path, "r", encoding="utf-8") as f:
        return f.read()


# The person this alter-ego represents.
name = "Siddharth Chakraborty"

# CV (falls back to the project-local copy if the Desktop file is missing).
cv = load_cv_from_csv(CV_CSV_PATH) or load_cv_from_csv(BACKUP_CV_CSV_PATH)
# Optional short bio.
summary = load_summary(os.path.join(_HERE, "me", "summary.txt"))


# ------------------------------------------------------------------------------
# 6. Career alter-ego: system prompt + chat loop
# ------------------------------------------------------------------------------
system_prompt = (
    f"You are acting as {name}. You are answering questions on {name}'s website, "
    f"particularly questions related to {name}'s career, background, skills and experience. "
    f"Your responsibility is to represent {name} for interactions on the website as faithfully as possible. "
    f"You are given {name}'s CV below which you can use to answer questions. "
    f"Be professional and engaging, as if talking to a potential client or future employer who came across the website. "
    f"If you don't know the answer to any question, use your record_unknown_question tool to record the question "
    f"that you couldn't answer, even if it's about something trivial or unrelated to career. "
    f"If the user is engaging in discussion, try to steer them towards getting in touch via email; "
    f"ask for their email and record it using your record_user_details tool. "
)
system_prompt += f"\n\n## CV:\n{cv or '[No CV loaded yet - add me/SIDD.csv]'}\n"
if summary:
    system_prompt += f"\n## Summary:\n{summary}\n"
system_prompt += f"\nWith this context, please chat with the user, always staying in character as {name}."


def _api_call(messages, with_tools: bool = True):
    """Call the LLM with automatic retries (handles transient rate limits).

    If a tool-enabled call fails (some models/endpoints don't support tools),
    retry that same call without tools instead of giving up.
    """
    last_error = None
    for attempt in range(4):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=(career_tools if with_tools else None),
            )
        except Exception as exc:
            last_error = exc
            if with_tools:
                print(
                    f"[api] tools call failed ({type(exc).__name__}: {exc}); retrying without tools",
                    flush=True,
                )
                return _api_call(messages, with_tools=False)
            print(
                f"[api] attempt {attempt + 1}/4 failed "
                f"({type(exc).__name__}: {exc}); retrying in {2 * (attempt + 1)}s",
                flush=True,
            )
            time.sleep(2 * (attempt + 1))
    raise last_error


def career_chat(message: str, history):
    """Chat function for the career alter-ego (with tool calling)."""
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": message}]
    )

    done = False
    turns = 0  # safety cap so we can never loop forever
    while not done and turns < 8:
        turns += 1
        response = _api_call(messages)
        finish_reason = response.choices[0].finish_reason

        # If the LLM wants to call a tool, run it and feed the result back.
        if finish_reason == "tool_calls":
            message_obj = response.choices[0].message
            results = handle_tool_calls(message_obj.tool_calls)
            messages.append(message_obj)
            messages.extend(results)
        else:
            done = True

    return response.choices[0].message.content


# ------------------------------------------------------------------------------
# 7. AI Model Showcase: quick tests + multi-agent simulation (from orcarouter)
# ------------------------------------------------------------------------------
def run_quick_tests():
    """Run the five-step single-turn test pipeline."""
    print("Running quick tests...", flush=True)
    sanity = ask("What is 2+2?")
    iq = ask("Please propose a hard, challenging question to assess someone's IQ. Respond only with the question.")
    math_prob = ask("Please generate a grade-8 math word problem.")
    math_sol = ask(f"Please solve the math problem: {math_prob} and provide the answer.")
    math_ver = ask(f"Verify the solution: {math_sol} and explain the steps taken to arrive at the answer.")
    return sanity, iq, math_prob, math_sol, math_ver


def run_agent_simulation():
    """Run a 3-round Examiner <-> Student interleaved multi-agent loop."""
    examiner_history = [
        {"role": "system", "content": "You are a strict math examiner. Ask a question. In subsequent rounds, score the student's previous answer out of 10, then ask a significantly harder math question."}
    ]
    student_history = [
        {"role": "system", "content": "You are a high school student taking a math exam. Answer the examiner's questions to the best of your ability. Keep answers concise."}
    ]
    current_turn_text = "Begin the exam by asking the first question."
    messages = []

    for round_num in range(1, 4):
        examiner_history.append({"role": "user", "content": current_turn_text})
        examiner_response = complete(examiner_history)
        examiner_text = examiner_response.choices[0].message.content
        examiner_history.append({"role": "assistant", "content": examiner_text})
        messages.append({"role": "assistant", "content": f"🎓 Examiner (Round {round_num}): {examiner_text}"})

        student_history.append({"role": "user", "content": examiner_text})
        student_response = complete(student_history)
        student_text = student_response.choices[0].message.content
        student_history.append({"role": "assistant", "content": student_text})
        messages.append({"role": "user", "content": f"👦 Student (Round {round_num}): {student_text}"})

        current_turn_text = student_text

    examiner_history.append({"role": "user", "content": "The exam is over. Grade the student's overall performance based on the conversation history."})
    final_grade = complete(examiner_history)
    messages.append({"role": "assistant", "content": f"📝 Final Grade: {final_grade.choices[0].message.content}"})

    return messages


# ------------------------------------------------------------------------------
# 8. Single Gradio UI -- every feature in one app (one public link)
# ------------------------------------------------------------------------------
with gr.Blocks(theme=gr.themes.Soft(), title=f"MEIKA - AI Interactive Dashboard") as demo:
    gr.Markdown(f"# 🤖 MEIKA -- AI Interactive Dashboard")
    gr.Markdown(f"Everything in one place: the {name} career alter-ego, quick model tests, "
                f"and a multi-agent simulation. Running on **`{MODEL}`**.")

    # ---------- Tab 1: Career alter-ego ----------
    with gr.Tab(f"💼 {name} - Career Conversation"):
        gr.Markdown(
            f"Chat with an AI acting as **{name}**, based on the CV in "
            "`C:\\Users\\Siddharth\\Desktop\\SIDD.csv`. "
            "Questions it cannot answer get recorded, and if you want to get in "
            "touch it will ask for your email."
        )
        gr.ChatInterface(career_chat)

    # ---------- Tab 2: Quick single-turn tests ----------
    with gr.Tab("📋 1. Quick Single-Turn Tests"):
        gr.Markdown("Testing basic single-hop generations, problem solving, and solution self-verification pipelines.")
        run_tests_btn = gr.Button("⚡ Execute Test Run", variant="secondary")

        with gr.Row():
            sanity_out = gr.Textbox(label="1. Quick Sanity Check (2+2)", lines=1)
            iq_out = gr.Textbox(label="2. Generating IQ Question", lines=2)

        math_prob_out = gr.Textbox(label="3. Generated Grade-8 Math Problem", lines=3)
        math_sol_out = gr.Textbox(label="4. Solved Math Output", lines=4)
        math_ver_out = gr.Textbox(label="5. Verified Analysis Steps", lines=8)

        run_tests_btn.click(
            fn=run_quick_tests,
            outputs=[sanity_out, iq_out, math_prob_out, math_sol_out, math_ver_out],
        )

    # ---------- Tab 3: 3-round agent simulation ----------
    with gr.Tab("🤖 2. 3-Round Agent Simulation"):
        gr.Markdown("Watch an **Examiner Agent** and a **Student Agent** maintain continuous conversational "
                    "state natively across multiple back-and-forth turns.")
        start_agent_btn = gr.Button("🚀 Launch Interleaved Multi-Agent Loop", variant="primary")
        chatbot_ui = gr.Chatbot(label="Live Agent State & Message Logs")

        start_agent_btn.click(fn=run_agent_simulation, outputs=chatbot_ui)


# ------------------------------------------------------------------------------
# 9. Launch
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Starting Gradio webserver... [{MODEL}]", flush=True)
    # share=True -> a single public .gradio.live link is printed in the console.
    demo.launch(share=True)
