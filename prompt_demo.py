"""Ten representative prompts, one per capability, with the full reply shown.

Companion to prompt_test.py (the 73-case regression battery). This one is the
walkthrough: it prints everything the pipeline decided, not just the intent.

    ./venv/bin/python prompt_demo.py > prompt_demo_results.txt
"""
import json
import time
import urllib.request

URL = "http://localhost:5001/api/chat"

# 14 stops on one vehicle: big enough that Held-Karp costs real time (~125 ms)
# but is still affordable, so the solver advice has a genuine trade to weigh.
FORM = {
    "depot": "1400 S Douglass Rd, Anaheim, CA",
    "addresses": ["13463 Washington Blvd, Marina Del Rey, CA",
                  "11232 Knott Ave, Stanton, CA",
                  "10903 Auto Square Dr, Cerritos, CA",
                  "2114 N Tustin St, Orange, CA",
                  "3301 E Imperial Hwy, Brea, CA",
                  "7777 Edinger Ave, Huntington Beach, CA",
                  "1 City Blvd W, Orange, CA",
                  "6600 Beach Blvd, Buena Park, CA",
                  "500 N Brookhurst St, Anaheim, CA",
                  "12345 Lakewood Blvd, Downey, CA",
                  "3200 E Airport Dr, Ontario, CA",
                  "9301 Tampa Ave, Northridge, CA",
                  "400 W Ocean Blvd, Long Beach, CA",
                  "1201 N Harbor Blvd, Fullerton, CA"],
    "vehicle_count": 1, "battery_packs": 4, "vehicle_weight_lbs": 13000,
    "temperature_c": 20, "unload_time_min": 30, "has_climate_control": True,
    "solver": "auto",
}

SOURCE_NOTE = {
    "lexicon": "lexicon  - matched a known phrasing, no model call",
    "model":   "model    - extracted by GPT-4o-mini, then validated by code",
    "session": "session  - answered an earlier question, released from the queue",
}

# Ten prompts, grouped 2-3 per capability. All English.
# (category, prompt, why this one is worth showing, follow-up turn or None)
CASES = [
    ("Solver recommendation",
     "I have 22 stops and 3 trucks, which algorithm?",
     "The size comes from the sentence, not the form. The model only extracts the two numbers; "
     "the pick, the runtimes and the comparison table are all computed by code.",
     None),

    ("Solver recommendation",
     "why not always use the optimal one?",
     "A plain question with no numbers: the lexicon classifies it and answers with zero model "
     "calls. The three runtimes quoted are measured values used to calibrate the estimator.",
     None),

    ("Solver recommendation",
     "is 2-opt good enough for my route?",
     "Naming a solver gets an answer about that solver first, then the pick. Three verdicts are "
     "possible: right choice / would work but not the best / wrong here, with the reason.",
     None),

    ("Parameter setting",
     "it's 86 degrees fahrenheit today",
     "Lexicon hit with unit conversion. No model call, no round trip.",
     None),

    ("Parameter setting",
     "add one more truck",
     "No absolute value in the sentence — resolved against the form's current count (1 -> 2).",
     None),

    ("Parameter setting",
     "switch to 2-opt",
     "A question about solvers is `recommend`; an instruction is `apply`. Aliases are normalised "
     "(2-opt, two-opt, exact, optimal, OR-Tools all resolve).",
     None),

    ("Validation and push-back",
     "give me 12 battery packs",
     "The hard ceiling is 9. Out-of-range is not silently clamped — it becomes a question.",
     None),

    ("Validation and push-back",
     "set the depot to Springfield",
     "A bare place name with no state, ZIP or street number is flagged ambiguous by code, not "
     "by the model's own judgement, and forces a question.",
     None),

    ("Scope and multi-axis",
     "what's the traffic on the 405?",
     "No guessing. The capability list is a constant in the source, not the model describing "
     "itself.",
     None),

    ("Scope and multi-axis",
     "depot in Anaheim CA, 2 trucks, 6 packs, 35 degrees, unload 20 min",
     "Five changes in one sentence: only the first is applied, the rest queue up for a yes each. "
     "Every change carries the exact span of your message that justifies it.",
     "yes"),
]


def ask(msg, pending=None, history=None):
    body = json.dumps({"message": msg, "history": history or [],
                       "form_state": FORM, "pending": pending}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=90))
        except Exception as e:
            err = e
            time.sleep(1 + attempt)
    return {"status": "error", "intent": "连接失败", "source": "-",
            "message": str(err), "changes": [], "rejected": [], "pending": None}


def wrap(text, indent, width=92):
    out, line = [], ""
    for word in str(text).split():
        if len(line) + len(word) + 1 > width:
            out.append(line); line = ""
        line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return ("\n" + " " * indent).join(out)


if __name__ == "__main__":
    print("Isuzu BEV Route Optimizer - chatbot Q&A")
    print(f"form: {len(FORM['addresses'])} stops, {FORM['vehicle_count']} vehicle, "
          f"{FORM['battery_packs']} battery packs, {FORM['temperature_c']} C, "
          f"{FORM['unload_time_min']} min unload, climate control on, solver auto")

    for i, (_cat, prompt, _note, followup) in enumerate(CASES, 1):
        print()
        print(f"Q{i}. {prompt}")
        d = ask(prompt)
        print(f"A.  {wrap(d.get('message', ''), 4)}")
        if followup:
            print()
            print(f"Q{i}b. {followup}")
            hist = [{"role": "user", "content": prompt},
                    {"role": "assistant", "content": d.get("message", "")}]
            print(f"A.  {wrap(ask(followup, pending=d.get('pending'), history=hist).get('message', ''), 4)}")
