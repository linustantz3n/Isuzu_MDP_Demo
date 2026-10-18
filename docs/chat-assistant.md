# Chat assistant

**Code:** the "AI Chat" and "Driver memory" sections of [`route_optimizer_backend.py`](../route_optimizer_backend.py#L1950)
(endpoint: [`ai_chat()`](../route_optimizer_backend.py#L3610)), and the UI in
[`AIChatbot.jsx`](../route-optimizer-frontend/src/AIChatbot.jsx).

The assistant tunes the optimizer in plain language. The operator describes the day ("battery
is tight", "we're running late", "truck 2 broke down"). The assistant turns that into **one
validated change at a time**: a form field, the optimization goals, or a priority stop. It also
answers what a truck can handle, and simulates a breakdown on the current plan. The model only
proposes; plain Python decides what is applied and writes every number in the reply. After a
turn that changed something, the page re-solves and shows the plan before and after
([below](#after-every-change-re-solve-and-compare)). What the goals and the breakdown re-plan
do to the solver is in [planning.md](planning.md).

```
operator utterance
  ├─ 1. lexicon fast path      known phrasings ("temperature 30 C", "6 packs", "开空调", "re-optimize") → no model call
  │      miss ↓                  talk of goals, limits or breakdowns always goes on to the model
  ├─ 2. constrained extraction  GPT-4o-mini, strict JSON schema: an ordered list of steps (usually one)
  └─ 3. deterministic validation, step by step in order
         • schema + null coupling   exactly one typed value slot, matching the axis; ranges enforced
         • evidence span            the quoted `evidence` must literally appear in the utterance (anti-hallucination)
         • padding dropped          value-less changes, and fields restated as they already are, are discarded
         • ambiguity flag / term    model flag OR bare place name with no state/ZIP → forces a question
              ├─ apply change       one step, one axis; extra changes are queued and released per "yes"
              ├─ ask a question     `pending` is echoed back by the client, so the session remembers it
              ├─ recommend solver   which algorithm fits this problem size, and why
              ├─ goals              the situation → goal levels; code turns levels into weights and explains them
              ├─ limits             a truck's battery, payload and range, and whether today's routes fit
              ├─ disruption         a breakdown on the current plan → an action the page runs (/api/replan)
              ├─ memory             save / load / forget / list driver profiles
              └─ out of scope       replies with the fixed capability list from code, not the model
```

| Stage | Code |
|---|---|
| Fields the assistant may change, with their ranges | [`AXES`](../route_optimizer_backend.py#L1982) |
| What it says it can do | [`CAPABILITIES`](../route_optimizer_backend.py#L2030) |
| 1. Lexicon | [`LEXICON`](../route_optimizer_backend.py#L2713), [`lexicon_fast_path()`](../route_optimizer_backend.py#L2846) |
| 2. Extraction | [`EXTRACTION_SCHEMA`](../route_optimizer_backend.py#L3036), [`EXTRACTION_SYSTEM_PROMPT`](../route_optimizer_backend.py#L3044), [`call_extraction()`](../route_optimizer_backend.py#L3131) |
| 3. Validation | [`validate_change()`](../route_optimizer_backend.py#L3170), [`validate_extraction()`](../route_optimizer_backend.py#L3305), [`route_outcome()`](../route_optimizer_backend.py#L3457) |
| Running several steps | [`run_steps()`](../route_optimizer_backend.py#L3561) |
| Goals, limits, breakdowns | [`outcome_goals()`](../route_optimizer_backend.py#L2511), [`outcome_limits()`](../route_optimizer_backend.py#L2586), [`outcome_disruption()`](../route_optimizer_backend.py#L2624) |
| Building the reply | `outcome_apply / outcome_ask / outcome_recommend / outcome_memory / outcome_out_of_scope` |
| Re-solving after a turn | `handleChatTurn` → `runChatResolve` / `runBreakdown` in `RouteOptimizer.jsx`; `onTurn` in `AIChatbot.jsx` |
| Applying a change in the UI | [`applyChatChange`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L440) in `RouteOptimizer.jsx` |

Fields (axes) and their limits: depot, stops (set / add / remove), vehicle count 1–10,
battery packs 3–9, vehicle weight 10,000–19,500 lbs, temperature −10 to 40 °C, unload time
5–120 min, climate control on/off, solver, priority stops (add / remove / set; each must be a
stop already on the form), `goals` (set only by the goals intent) and `fleet` (set only by a
driver recall).

To apply every validated change in a single turn instead of one at a time, set
`ONE_CHANGE_PER_TURN = False`. Replies are in English or Chinese, matching the operator's language.

**Which model.** The extraction step calls any OpenAI-compatible chat endpoint. By default that
is OpenAI with `OPENAI_API_KEY`. To use the U-M GPT Toolkit, set `LLM_BASE_URL=https://api.toolkit.umgpt.umich.edu/v1`,
`LLM_API_KEY` and, optionally, `LLM_MODEL` (default `gpt-4o-mini`) in `.env`. The Toolkit serves
the same `gpt-4o-mini`, including the strict JSON-schema output the extraction relies on, and
the prompt battery scores the same through it. Another model needs a re-run of `prompt_test.py`.

Without a key in `.env`, the lexicon fast path still works. Everything else returns
*out of scope* with the capability list.

## Several requests in one message

One message can ask for several actions in sequence. The model returns them as an ordered list
of steps, and the server runs them in that order:

```
"delete driver Han and Linus, then list all drivers"
   -> Drivers Han, Linus deleted
      2 drivers saved:
      [Donghua] [Li]        one card per driver, with packs and weight when saved
```

- **A later step sees what an earlier one did.** The list runs after the deletion. "Use 3
  trucks, then tell me which solver to use" gets advice for 3 trucks
  ([`advance_form_state()`](../route_optimizer_backend.py#L3510) carries the form forward between steps).
- **Most messages are still one step.** Several values in one sentence ("2 trucks with 6 packs")
  form one step with several changes, and deleting several drivers is one step.
- **One form change per message still holds.** If a second step would change the form again, it
  is held behind a "yes / no" question, just as the second change of a single step is.
- **A question stops the sequence.** If a step needs an answer (an ambiguous address, a queued
  change, a solver-switch offer), the steps after it are not run, and the reply says so:
  "The rest of your request is on hold until you answer — say it again afterwards."
- **At most 5 steps** per message (`MAX_STEPS`).
- Each step's reply goes on its own line, and every change from every step is applied to the
  form in order.

## Optimizing in plain language

The operator talks about the day; the assistant decides which settings move.

| The operator says | What happens |
|---|---|
| "battery is tight today" | goals: Energy → high, Distance → low |
| "Battery is tight today — keep 20% in every truck" | Energy → high, and a 20% battery reserve |
| "we're running late, get everyone back sooner" | Time → high, Balance → medium or high |
| "drivers say the routes are unfair" | Balance → high |
| "make sure no truck runs out of battery" | battery limit enforced (0% reserve) |
| "how do the weights work?" | the current goals and weights, and the cost formula |
| "deliver to Brea first" / "Cerritos is urgent" | Brea / Cerritos becomes a priority stop |
| "what are the limits of a truck with 3 battery packs?" | 60 kWh, 6,559 lbs empty, 12,941 lbs payload, ~56–66 mi range at 20 °C, and whether today's longest route fits |
| "truck 2 broke down after its first stop" | a breakdown re-plan: the stranded stops move to the other trucks |
| "Wang's truck crashed an hour in, send a spare" | the same, 60 minutes after dispatch, with a spare truck allowed |
| "re-optimize" | runs the optimizer (geocoding first if the stops changed) |

The model picks a **level** per goal (off / low / medium / high, or raise / lower), from a
closed enum. [`goal_weights()`](../route_optimizer_backend.py#L714) turns levels
into weights, and the reply names each changed goal, what it does to the routes, and the new
weights. The same gates as every other change apply, plus a few of their own:

- **Goal changes need the operator's words.** The `evidence` span must appear in the
  message. A battery reserve above 0% also needs its number written in the message.
- **A situation is never a solver switch.** "Get everyone back sooner" once came back as
  "solver → nearest neighbor". A solver change now needs a solver word in its evidence:
  solver, algorithm, Held-Karp, 2-opt, exact, optimal and the like.
- **"I need a guaranteed optimal route for 12 stops"** names a problem size other than the
  form's, so it is answered as a recommendation for that size. The solver is not pinned.
- **A breakdown never guesses.** The truck must be named by its number (digits, a word or
  an ordinal) or by its driver, and the moment by a stop count or a time. "A truck broke
  down" asks which one. Stop 0 needs words like "leaving the depot", so "an hour in"
  cannot turn into stop 0. A spare truck needs the word spare, backup or extra.
- **Limits questions describe a hypothetical truck.** If the model returns "5 packs" as a
  form change, it is folded back into the question and the form is left alone.
- **Talk of goals, limits or breakdowns skips the lexicon.** Otherwise "truck 2 broke down
  after 2 stops" would set the fleet to 2 trucks, and "leave the battery at 20%" would ask
  for 20 battery packs.

## After every change: re-solve and compare

When a plan is on screen and a turn changes something, the chat hands the turn to the page
(`onTurn`). The page then:

1. keeps the current plan on screen as the "before" and flashes the sidebar section that
   changed;
2. re-solves with the new settings. The distance and time matrices and elevations from
   the last solve of the same locations are sent back (`precomputed`), so no Google calls
   are made. If the stops changed, it geocodes first. A breakdown goes to `/api/replan`
   instead;
3. stores the result as a new **plan version** and opens the **What changed** panel above
   the results. It shows what was said, the settings that changed, a Before | After | Δ
   table (distance, last truck back, energy, lowest battery left, unserved stops) and
   both plans' stop orders. Moved, re-assigned, pickup and breakdown stops are marked.
   Any earlier version can be picked as the baseline;
4. sends the measured effect back to the chat as a message, for example:
   "Re-solved (v2). Distance 113.3 mi → 57.7 mi (−49.0%); … Can't be served: Marina Del Rey."

While a "yes / no" question about a queued change is open, changes are collected, and the
plan is re-solved once the question is answered. Without a plan on screen, the assistant
only fills the form, as before.

## Solver recommendation

The assistant can advise which solver fits the problem size, and switch to it. Everything it
claims (runtimes, limits, the recommended solver) is computed by `recommend_solver()` and
`solver_table()`, never written by the model, so it cannot invent a number. The numbers and
thresholds are explained in [solvers.md](solvers.md#how-apioptimize-decides-what-to-run).

```
"which algorithm should I use?"        → recommendation + comparison table + offer to switch
"why not always use the optimal one?"  → the exponential-vs-O(n²) tradeoff, priced at your size
"what solver am I using now?"          → current setting and what it resolves to
```

Bare solver questions with no numbers in them hit the lexicon and cost no model call. A
hypothetical question ("if I had 22 stops and 3 trucks") goes to the model, which only extracts
the numbers (with an evidence span). The advice itself is still computed.

## Driver memory

The memory keeps **driver profiles** by name, and they can be loaded back in a later session. It
tracks drivers only: no licence plates and no vehicle names. A profile is the driver's name,
optionally with the battery packs and weight of the truck that driver runs. When those are
present, they feed the energy model when the driver is loaded.

```
"remember driver Li"
   -> Saved 1 driver: Li

"Wang runs 6 packs at 15000 lbs"
   -> Saved 1 driver: Wang — 6 packs (120 kWh) · 15,000 lbs

   ... later session ...

"Wang and Li are driving today"
   -> Loaded 2 drivers into the form   (sets the `fleet` axis: one vehicle per driver)
```

Four operations, all through `/api/chat`: **remember** (save or update), **recall** (load into
the form), **forget** (delete), and **list** (show who is stored).

Phrasing does not matter. "save driver Donghua", "can you save driver Donghua to the system?",
"is it possible to add driver Sun with 5 packs?" and "能帮我记住司机东华吗" all save a driver. Save,
add, store, register and create all mean *remember*. *Recall* is only for drivers who are already
saved. If the model recognises a save request but returns no name, the reply asks for the name
("I couldn't find a driver's name to save…") instead of guessing another operation.

- **Recall** sets the form to one vehicle per recalled driver. A driver with packs and weight
  brings them along. If a profile only has packs, the weight is derived from the packs, the same
  way the form's pack selector does it. A name-only driver keeps whatever packs and weight that
  vehicle slot already had. The driver's name then shows on the vehicle card and on that
  vehicle's route in the results (`routes[].driver`).
- **The same gates that protect form changes protect profiles.** Packs and weight go through the
  form's range checks (3–9 packs, 10,000–19,500 lbs). The driver's name must appear in the message
  as a whole word, so the model cannot invent a driver. When packs or weight are given, the model
  must also quote one exact, unbroken phrase from the message for that driver, so it cannot move
  one driver's numbers onto another. A name on its own needs no quote, so "add driver Linus and
  Han" saves both.
- **A driver is only deleted or loaded if you wrote their name.** "delete everything" or
  "remove all drivers" deletes nobody; the assistant asks "Which driver should I delete?". Any
  name the model adds that is not in the message is refused and listed as "not found in your
  message". To wipe the whole memory, use `DELETE /api/memory`.
- **An update merges** with what is stored, so "Wang runs 8 packs now" keeps the stored weight.
  Up to 40 drivers can be stored.
- **A saved driver's name, or words like "driver", "save" or "remember", disable the lexicon fast
  path** for that message. "Wang runs 8 packs now" must update the *profile*, while "set battery
  packs to 8" changes the *form*, and only the model can tell them apart. Without this, "add driver
  Linus with 6 packs" would set the form's packs to 6. Names are matched as whole words, so a
  driver called "Li" does not capture "climate".

Code: [`do_remember()`](../route_optimizer_backend.py#L3798), [`do_recall()`](../route_optimizer_backend.py#L3830),
[`do_forget()`](../route_optimizer_backend.py#L3863), [`validate_profile()`](../route_optimizer_backend.py#L3721).

The memory is stored in one JSON file, `fleet_memory.json`, next to the server. It is gitignored
because it is runtime state. Its shape is
`{"drivers": {"<lowercased name>": {"name", "battery_packs"?, "weight_lbs"?}}}`. A file written by
the earlier vehicle-and-plate version (keyed `"vehicles"`) is not carried over.

- `GET /api/memory` → `{"status": "ok", "drivers": [...]}`
- `DELETE /api/memory?name=Wang` removes one driver; with no `name`, it clears all of them.

## `POST /api/chat`

**Request**
```json
{ "message": "Use 2 trucks with 6 packs",
  "history": [{"role": "user", "content": "..."}],
  "form_state": {"depot": "...", "addresses": [], "vehicle_count": 1, "battery_packs": 4,
                 "vehicle_weight_lbs": 13000, "temperature_c": 20, "unload_time_min": 30,
                 "has_climate_control": true, "solver": "auto", "fleet": [...],
                 "goals": {"distance": "high", "time": "off", "energy": "off", "balance": "off",
                           "battery_reserve_pct": null},
                 "priority_stops": [],
                 "plan": {"kind": "fleet", "vehicles": [{"number": 1, "vehicle_index": 0, "name": "Vehicle 1",
                          "driver": "Wang", "stops": 3, "energy_kwh": 102.7, "battery_pct": 0}]}},
  "pending": null }
```

**Response**
```json
{ "status": "ok", "intent": "apply", "source": "model",
  "message": "Applied: Vehicles → 2. Also apply: Battery packs → 6 packs? (yes / no)",
  "changes": [{"axis": "vehicle_count", "op": "set", "value": 2, "evidence": "2 trucks", "label": "Vehicles → 2"}],
  "pending": {"question": "Also apply: Battery packs → 6 packs? (yes / no)", "axis": null, "queued": [...]},
  "capabilities": null, "rejected": [], "advice": null, "profiles": [] }
```

`plan` is a summary of the plan on screen: per truck, its stops and the energy its route
needs. Breakdowns are checked against it, and the limits answer compares it with the battery.
It is `null` before the first solve. The newer outcomes also carry:

| Field | When |
|---|---|
| `action` | `{"type": "breakdown", "vehicle_index", "after_stop" or "at_minute", "spare_truck", "lang"}` for a breakdown; `{"type": "resolve"}` for "re-optimize". The page runs it |
| `limits` | the `truck_limits()` result for a limits question: battery, payload, ranges per speed, plan check |
| `goals_info` | `{before, after, weights_before, weights_after, evidence}` for a goals change |

`intent` is one of `apply`, `ask`, `recommend`, `memory`, `goals`, `limits`, `disruption` or
`out_of_scope`. `source` is `lexicon`,
`model` or `session`. `advice` is filled for `recommend`, and `profiles` for `memory`. For a
multi-step message, `intent` is that of the last step run, `message` has one line per step, and
`changes` holds every step's changes in order. The client applies everything in `changes`
whatever the `intent`. That includes the `fleet` change a driver load produces, which before
this fix was shown as "Loaded" but never applied.

## Regression scripts

These run against a live server on :5001 and need a chat model key (`OPENAI_API_KEY` or `LLM_API_KEY`):

- [`prompt_test.py`](../prompt_test.py): the prompt battery. Results are in `prompt_test_results.txt`.
- [`prompt_demo.py`](../prompt_demo.py): ten representative prompts, each with the full reply. Results are in `prompt_demo_results.txt`.
