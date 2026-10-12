# Chat assistant

**Code:** the "AI Chat" and "Driver memory" sections of [`route_optimizer_backend.py`](../route_optimizer_backend.py#L1188)
(endpoint: [`ai_chat()`](../route_optimizer_backend.py#L2324)), and the UI in
[`AIChatbot.jsx`](../route-optimizer-frontend/src/AIChatbot.jsx).

The assistant fills in the form. It turns an operator's sentence into **one validated change at
a time**. The model only proposes changes; plain Python decides whether they are applied. It never
runs the optimizer and never reads results.

```
operator utterance
  ├─ 1. lexicon fast path      known phrasings ("temperature 30 C", "6 packs", "开空调") → no model call
  │      miss ↓
  ├─ 2. constrained extraction  GPT-4o-mini, strict JSON schema: an ordered list of steps (usually one)
  └─ 3. deterministic validation, step by step in order
         • schema + null coupling   exactly one typed value slot, matching the axis; ranges enforced
         • evidence span            the quoted `evidence` must literally appear in the utterance (anti-hallucination)
         • padding dropped          value-less changes, and fields restated as they already are, are discarded
         • ambiguity flag / term    model flag OR bare place name with no state/ZIP → forces a question
              ├─ apply change       one step, one axis; extra changes are queued and released per "yes"
              ├─ ask a question     `pending` is echoed back by the client, so the session remembers it
              ├─ recommend solver   which algorithm fits this problem size, and why
              ├─ memory             save / load / forget / list driver profiles
              └─ out of scope       replies with the fixed capability list from code, not the model
```

| Stage | Code |
|---|---|
| Fields the assistant may change, with their ranges | [`AXES`](../route_optimizer_backend.py#L1220) |
| What it says it can do | [`CAPABILITIES`](../route_optimizer_backend.py#L1259) |
| 1. Lexicon | [`LEXICON`](../route_optimizer_backend.py#L1596), [`lexicon_fast_path()`](../route_optimizer_backend.py#L1719) |
| 2. Extraction | [`EXTRACTION_SCHEMA`](../route_optimizer_backend.py#L1863), [`EXTRACTION_SYSTEM_PROMPT`](../route_optimizer_backend.py#L1871), [`call_extraction()`](../route_optimizer_backend.py#L1946) |
| 3. Validation | [`validate_change()`](../route_optimizer_backend.py#L1985), [`validate_extraction()`](../route_optimizer_backend.py#L2075), [`route_outcome()`](../route_optimizer_backend.py#L2191) |
| Running several steps | [`run_steps()`](../route_optimizer_backend.py#L2280) |
| Building the reply | `outcome_apply / outcome_ask / outcome_recommend / outcome_memory / outcome_out_of_scope` |
| Applying a change in the UI | [`applyChatChange`](../route-optimizer-frontend/src/RouteOptimizer.jsx#L272) in `RouteOptimizer.jsx` |

Fields (axes) and their limits: depot, stops (set / add / remove), vehicle count 1–10,
battery packs 3–9, vehicle weight 10,000–19,500 lbs, temperature −10 to 40 °C, unload time
5–120 min, climate control on/off, solver, and `fleet` (set only by a driver recall).

To apply every validated change in a single turn instead of one at a time, set
`ONE_CHANGE_PER_TURN = False`. Replies are in English or Chinese, matching the operator's language.

Without `OPENAI_API_KEY` in `.env`, the lexicon fast path still works. Everything else returns
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
  ([`advance_form_state()`](../route_optimizer_backend.py#L2238) carries the form forward between steps).
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

Code: [`do_remember()`](../route_optimizer_backend.py#L2512), [`do_recall()`](../route_optimizer_backend.py#L2544),
[`do_forget()`](../route_optimizer_backend.py#L2577), [`validate_profile()`](../route_optimizer_backend.py#L2435).

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
                 "has_climate_control": true, "solver": "auto", "fleet": [...]},
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

`intent` is one of `apply`, `ask`, `recommend`, `memory` or `out_of_scope`. `source` is `lexicon`,
`model` or `session`. `advice` is filled for `recommend`, and `profiles` for `memory`. For a
multi-step message, `intent` is that of the last step run, `message` has one line per step, and
`changes` holds every step's changes in order. The client applies everything in `changes`
whatever the `intent`. That includes the `fleet` change a driver load produces, which before
this fix was shown as "Loaded" but never applied.

## Regression scripts

These run against a live server on :5001 and need `OPENAI_API_KEY`:

- [`prompt_test.py`](../prompt_test.py): the prompt battery. Results are in `prompt_test_results.txt`.
- [`prompt_demo.py`](../prompt_demo.py): ten representative prompts, each with the full reply. Results are in `prompt_demo_results.txt`.
