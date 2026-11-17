# Chat assistant and driver assignments

The demo, backend replies, prompts and examples are English. User-supplied names and addresses are preserved. The assistant handles goals, form changes, solver advice, driver memory, truck limits and breakdowns. Its fast path handles known commands without an LLM key; other phrasings use the configured OpenAI-compatible extraction service. Extracted changes are validated against the operator's own evidence before application.

## Driver workflow

Use these commands in order:

1. `Remember Wang as an aggressive driver.` saves Wang's usual driving style.
2. `Assign Wang to Truck 2.` binds that saved driver to the existing truck. Truck 2 uses Aggressive unless a run override is set.
3. `Use Eco for Truck 2 on this run.` overrides only this run; Wang's usual style remains Aggressive.
4. `Restore driver default for Truck 2.` restores Aggressive.

`Show saved drivers` lists profiles. Assignment targets may be existing truck IDs, exact vehicle names or an assigned driver's name. Unknown or ambiguous targets require clarification; no new truck is created implicitly. A driver may operate only one truck per run, so reassignment clears the previous binding. Truck packs, capacity and weight are preserved. `Assign Wang to Truck 2 and use Eco for Truck 2` applies the two operations in order.

The Fleet Vehicles controls expose the same driver and run-style choices. `All trucks` is an explicit run override across the fleet. An unassigned truck defaults to Normal. The results show the driver, effective style and its source, and the comparison lists assignment and style changes. Changing a driver or style after solving triggers a new solve using the stored road matrices. A late response from an earlier solve cannot overwrite a newer driver configuration.

Memory remains in `fleet_memory.json`; older profiles without a style read as Normal. Historical truck specification fields are retained but are never copied into a truck assignment. A plan stores each assigned driver's snapshot; later memory edits do not change saved plans. Reassign the driver to apply an updated usual style.

## Verified Eco suggestions

After a solve, `/api/suggest` can test Eco run overrides when a truck is below its reserve or stops are unserved. It reuses the same locations, road matrices, departure/traffic settings, goals, driver snapshots and NN version. It first tests individual affected trucks, then a combined override when applicable.

An Eco suggestion must keep all previously served stops, satisfy the original reserve, and reduce unserved stops when that is the problem. It reports predicted energy, minimum battery percentage and finish-time changes. Any remaining unserved stops are disclosed. If no candidate qualifies, it does not promise a saving. Other existing candidates include changed goal weights, more battery packs and another truck.

Suggestions do not change the form automatically. `Yes` applies the tested override and re-solves; `No` discards it. A changed configuration or NN version invalidates a pending suggestion. A question such as `Would Eco help Truck 2?` does not change the run.

## Other supported operations

- `Temperature 30 C`, `6 battery packs`, `turn off AC`, or `add a stop at ...` change form values.
- `Battery is tight today`, `get everyone back sooner`, or `keep 20% battery in every truck` tune goals or reserve.
- `Use live traffic` or `leave at 7:30` selects a departure. Traffic without a departure prompts for one.
- `Which algorithm should I use?` explains the solver choice. `Re-optimize` reruns the plan.
- `How far can Truck 2 go?` uses that truck's assigned style and specifications.
- `Truck 2 broke down after its first stop` invokes the existing breakdown replanner. Driver styles remain attached to surviving trucks; a spare explicitly inherits the broken truck's effective style.

## Natural driver requests with an LLM

The model can extract operations from expressions such as:

- `Please note that Wang usually drives aggressively.` saves a usual style.
- `Put Wang behind the wheel of truck two today.` assigns Wang to Truck 2.
- `Could you have Truck 2 take it easy on this run?` requests an Eco run override.
- `Let Wang drive as usual today.` clears the override on Wang's assigned truck.
- `Have every truck drive gently today.` requests Eco for the current fleet.

The prompt includes the actual fleet, saved profiles and pending question. Each
driver change has a typed `vehicle_ids` list and `value_text` (driver name or
eco/normal/aggressive/default), supported by a quoted span of the user's message.
The backend validates these fields directly; it does not feed the sentence back
through the fixed command grammar. IDs must match named trucks, ordinals, exact
truck names or, for style changes, a currently assigned driver. Unknown or ambiguous
targets, pronoun-only references and conflicting styles require clarification.
Negative instructions, hypothetical questions and advice requests do not mutate
driver assignments or styles. Explicit temporary requests cannot be saved as a
driver's usual style. The model cannot supply a replacement fleet or truck specs.

The extraction schema separates persistent `memory_profiles[].driving_style`,
`driver_assignment` and `vehicle_style`. Validated changes use the existing
`driver_assignments` and `vehicle_style_overrides` frontend contract, preserving
truck specifications, driver snapshots and automatic re-solving. Multiple changes
use the existing pending-change flow; a later assignment retains earlier changes.

## Configure the model connection

Use the root [`.env.example`](../.env.example) as a template. Add `LLM_API_KEY`,
`LLM_BASE_URL` and `LLM_MODEL` to the backend `.env`, then restart the backend.
The base URL must omit `/chat/completions`; code appends it. The provider and model
must support OpenAI-compatible Chat Completions with strict JSON Schema output.
The default endpoint/model are OpenAI and `gpt-4o-mini`; another provider needs its
own matching URL, model and key. This works with both `run_demo.py` and the normal
launcher. Google credentials are separate.

No additional driver integration code is needed after configuring a compatible
provider. `tests/test_driver_llm.py` checks the real chat handler, request schema,
prompt context, driver validation, persistent memory and NN optimizer handoff with
only the external model response mocked. It cannot verify a real provider's access,
schema support or interpretation quality; those need a live-key integration check.

Offline regression coverage is in `tests/test_planning.py`, `tests/test_nn_drivers.py` and frontend tests. `prompt_test.py`, `prompt_demo.py` and `r4_test.py` exercise a running backend and may require an LLM key. Historical `*_results.txt` files describe older runs and are not evidence for the current NN or English-only behavior.
