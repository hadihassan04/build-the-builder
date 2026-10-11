---
name: build-phone-agent
description: Build a Freya phone agent from a bank's brief via the guarded builder (policy IR → compile → test → repair policy only). Use when asked to build, improve, or debug a phone agent from a brief.
---

# Build a phone agent from a brief

Work from the kit root. Use the **guarded builder** in `builder/`. Prefer **routes, code nodes, and tool hooks** over prompts. The dialogue model proposes; code decides what gets written to the bank.

For practice Brief A+B the hand policy is `builder/policies/brief_a_b.policy.json`. Primary Freya slot: `brief-a-b`.

---

## Non-negotiable principles

1. **Never hand-edit the agent in Freya UI.** Change policy / guard source → recompile → `save_workflow`.
2. **Never invent tool fields.** Enums and bodies come only from `tools/openapi.json`.
3. **Fail closed on writes.** Empty `deadlines_json` / missing maps → block `create_promise` / notes.
4. **Honesty.** Say “recorded” only after `create_promise` / `add_note` returns `ok: true` (TTS node after one-shot flag).
5. **Leave `agent_config` out of the compiled workflow.** `build.py` hardens automatically after save (temp 0.2 + guardrails). Verify the `hardened:` line; a `harden warning:` means the published version is un-hardened — re-run build. Do not invoke `harden.py` as a CLI (no `__main__`).
6. **One simulation at a time per workspace.** Inside a sim, `FREYA_SIM_CONCURRENCY` (default 5) is concurrent *calls*, not concurrent sims. A second `run_cases` waits automatically (30s loops) — do not parallelize. Cancel (`POST /api/v2/simulations/{id}/cancel`) only if status is still `running` with no progress for >15 min; take the id from `run_cases` / sim list output.
7. **Do not assign `proposed_date=""`.** Freya date schema rejects empty strings. Only `fit_product_dates` sets `proposed_date_consumed=yes`; never write `proposed_date` from prompts to “clear” it.
8. **Never put doubled braces `{{` `}}` in generated hook/code strings** (except Freya template vars in `task_message` / TTS).
9. **Reuse the same Freya slot** (`brief-a-b`) for every repair via `freya.save_workflow` — never mint new slot names per attempt. Workspace caps at ~200 workflows.

---

## Pipeline (always this order)

```
briefs + tools/openapi.json
  → builder/extract.py          # Policy IR (A/B: load hand policy; LLM extract is a stub)
  → builder/policies/*.policy.json
  → builder/compile.py          # workflow graph + embedded guard code
  → builder/lint.py             # static checks (also run inside build.py)
  → freya.save_workflow(slot)   # create/update + publish
  → apply_hardening             # automatic inside build.py after save
  → tools/run_cases.py          # score ledger + judge
```

### Build command

```bash
source .env   # FREYA_API_KEY; run_cases also needs FREYA_BANK_TEAM_KEY (≥16 chars, stable)

# Practice A+B (primary) — lints, dry-runs, saves+publishes, hardens
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md --slot brief-a-b

# Offline only
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md --slot brief-a-b --skip-save
```

Prints `workflow_id`, `agent_id`, version. Writes `out/<slot>.policy.json`, `out/<slot>.workflow.json`, and a trace under `traces/`.

**Do not use** `build.py --test` or `--repair-rounds` (broken / no-op). Always run `tools/run_cases.py` yourself.

### Offline gates

`build.py` already lints + dry-runs before save. Standalone only when iterating without saving:

```bash
python3 builder/test_guards.py          # must be all-green; ignore stale counts elsewhere
python3 builder/lint.py out/brief-a-b.workflow.json
```

### Test

```bash
# Full brief
python3 -u tools/run_cases.py <agent_id> --brief A --out results/run-a.json
python3 -u tools/run_cases.py <agent_id> --brief B --out results/run-b.json

# Single / subset
python3 -u tools/run_cases.py <agent_id> --only PUB-12 --reps 2 --out results/pub12.json

# Style / robustness (finals prep)
python3 -u tools/run_cases.py <agent_id> --brief A --style talkative --out results/style-talk.json
# also: pieces, corrects, asks_back, in_words
# Slow-model guard: --rerun-if-slow 1 discards runs with median slowest turn >10s
```

Use `-u` (unbuffered). Run in foreground; a case takes minutes. Never assume a green once → always green (`--reps 2` or `3` for finals).

On FAIL:

1. Read `results/<out>.json` → `final[]` for cross-rep status, then `all_rows[rep][i].failed` + `call_id` (there is **no** top-level `failed[]`).
2. Judge reason in results is truncated to **400 chars**. Full verdict: `freya.get_iteration(simulation_id, iteration_id)["runs"]` (match `callId`).
3. Transcript / routes: `freya.get_call(call_id)`.
4. Prefer fixing **guards/compile**, not prompts.

---

## Architecture map

| Path | Role |
|---|---|
| `builder/policies/brief_a_b.policy.json` | Hand Policy IR for practice A+B (`bands`, `calendar.holidays`, `intents`, `fixed_sentences`, `negotiation`, `notes`, `multi_product`, `never_say`) |
| `builder/ir_schema.json` | Schema for Policy IR (top-level `validate_policy()` is shallow — check nested fields yourself) |
| `builder/extract.py` | Brief → policy. **A/B loads hand policy. `_llm_extract` is a stub (always `None`) and falls back to A/B** — do not trust it for unseen briefs |
| `builder/compile.py` | Policy + openapi → Freya workflow JSON |
| `builder/guards/date_engine.py` | `MONTH_DAYS`, deadlines, Attempt-1, holiday shifts, `attempts_json` / `labels_json` |
| `builder/guards/policy_gate.py` | `pre_process_code` for promise / callback / transfer / note / close |
| `builder/guards/post_hooks.py` | `what_to_do_next`, ledger mirrors, `promise_just_ok`, `note_just_ok`, `noted_products` |
| `builder/guards/persuasion.py` | Per-product persuasion counters + TTS text (`PERSUASION_CODE`) |
| `builder/guards/outcome_resolver.py` | Bake `resolved_outcome` / `resolved_reason` before close |
| `builder/guards/flow.py`, `ledger_mirror.py` | **Not compiled** — legacy. Do not import to fix a failure; mechanisms live in `post_hooks.py` / `compile.py` |
| `builder/templates/tools.py` | Tool defs with hooks wired |
| `builder/harden.py` | `apply_hardening()` called from `build.py` after save |
| `builder/lint.py` | Graph / tool / unreachable checks |
| `freya.py` | API client (`User-Agent` required; Cloudflare 1010 without it) |

---

## Compiled graph (control flow)

Rough order of authority:

```
setup → opening
  ├─ identity → load_account → compute_dates → collection
  ├─ third_party → schedule_callback → resolve…
  └─ close_* (deceased / wrong_number / unclear / …)

collection
  ├─ proposes_date → fit_product_dates → collection
  ├─ full_too_much → stage_floor → floor_offer → ack_promise → promise_recorded
  ├─ hardship → hardship subgraph → close_hardship | transfer
  ├─ restructure → transfer restructuring
  ├─ already_paid → close_paid → speak_paid
  ├─ promise_just_ok → ack_promise → promise_recorded (TTS) → check_remaining
  └─ refuses | confirm_no → track_persuasion → route_persuasion
        ├─ resume_collection → collection          # confirm payable first
        ├─ continue_persuasion → speak_persuasion (TTS) → listen_persuasion
        └─ awaiting_notes → check_remaining → route_remaining
              ├─ need_notes → finalize_notes → check_remaining → …
              ├─ need_persuasion → track_persuasion
              ├─ continue_collection → collection
              └─ else → resolve_and_goodbye → speak_thanks → close_ledger → goodbye
```

**Key nodes (do not remove without replacing the mechanism):**

| Node | Job |
|---|---|
| `compute_dates` | Runs `DATE_ENGINE_CODE`; sets `deadlines_json`, `floors_json`, `past_dues_json`, `attempts_json`, `labels_json`, `date_facts` |
| `fit_product_dates` | Clamps proposed date per product; sets `proposed_date_consumed=yes` (never `proposed_date=""`) |
| `ack_promise` | Clears `promise_just_ok` so stale `create_promise ok` cannot re-enter recorded TTS |
| `promise_recorded` | Fixed TTS recorded + SMS line only after a fresh success |
| `track_persuasion` / `speak_persuasion` / `listen_persuasion` | One product at a time; attempts from policy `bands[].persuasion_attempts` (A/B: late=2, early=1) via `attempts_json`; TTS offer, listen-only (no LLM second ask) |
| `finalize_notes` | **Only** node with `add_note` tool; requires `awaiting_notes=yes`; exits on one-shot `note_just_ok` (not sticky `add_note_response.data.ok`) |
| `route_remaining` | After `check_remaining`: notes / persuasion / collection / resolve |
| `resolve_and_goodbye` | Outcome resolver then farewell → `close_ledger` → `goodbye` |

**Collection must NOT expose `add_note`.** Early refuse turns must not write the bank; notes wait for `finalize_notes`.

---

## Guard invariants (do not regress)

### Date engine (`date_engine.py`)

- Embed `MONTH_DAYS` inside `DATE_ENGINE_CODE` (Freya sandbox has no module globals).
- Per product: band → `deadline`, `persuasion_attempts` → `attempts_json`.
- Attempt-1 = last business day of this week (else next week if today is that day).
- Holiday lines in `date_facts`: offer **next BD**, not band deadline.
- Fail soft → empty maps + `dates_ready=no` so gates block writes.

### Promise gate (`create_promise_pre_process`)

- Fail closed if no deadlines / bad product / already promised.
- Prefer fitted dates; snap past mis-parsed “tomorrow”; clamp after-deadline per product (multi).
- Holiday / non-BD → block or snap with `offer_date`.
- Floor path: `floor_offered=yes` bakes `gate_*` and overwrites body.
- Recover `allowed_product_ids` from deadline keys if Freya drops the list.

### Callback gate

- Date ∈ {today … next BD}, time in window, strictly after `now`.
- Snap illegal → `callback_tomorrow`; post-hook sets `has_callback` / forced `not_reached/callback`.

### Notes gate

- `reason=refused` only when `awaiting_notes=yes`.
- Block duplicates via `noted_products` + `ledger_notes`.
- Block refused when `proposed_date` is after that product’s deadline (`after_deadline_not_refusal`).
- Persist **all** cross-turn lists/maps as **JSON strings** (`ledger_notes`, `noted_products`, `promised_products`, `fitted_dates_json`, `persuasion_used_json`, `attempts_json`, `note_pid_stack`) — Freya drops bare list/dict mutations.
- Transition out of `finalize_notes` on `note_just_ok=yes` only (cleared in `check_remaining`). Never route on sticky `add_note_response.data.ok`.
- **One product per finalize visit:** `check_remaining` sets `open_products` to a single pid when `need_notes`. Do not list all open products (parallel `add_note` races lose FIFO stack entries under Freya var merge).
- Pre-hook sets `note_attempted_<pid>=yes`; `check_remaining` **always** credits those markers into `noted_products` (not only when `note_race=yes`).

### Verify post-hook

- `wrong_answers_left` can be int `0` — never use `x or ""` (0 is falsy).
- `day_month_swapped` hint → re-ask month as a word; **not** counted as a wrong answer.
- Retry / decline rules come from `policy.identity.*`; edit there, not the identity `task_message`. Judges count how many times each question was asked.
- Three wrongs → `forced_outcome=not_verified`, `forced_reason=failed`.

### Persuasion (`persuasion.py`)

- Opening “pay today?” is **not** counted.
- On refuse: one product at a time (get_account order).
- Attempt counts from policy `bands[].persuasion_attempts` → `attempts_json` (A/B: late=2, early=1). Changing the count = edit policy, not `persuasion.py`.
- Attempt 1: Attempt-1 date + full (or floor if `full_too_much`).
- Attempt 2 (when band allows): floor on that product’s deadline.
- If fitted payables exist and none promised yet → `resume_collection` (confirm/`create_promise` first); do not burn attempts.
- After a promise with open products → `check_remaining` sets `need_persuasion` and routes into `track_persuasion` (no free-form collection chat).
- Exhausted → `awaiting_notes=yes` → `finalize_notes`.

### Close / outcomes

- Every terminal path sets `forced_*` or resolver fills `resolved_*`.
- Two senders, one latch: `close_ledger` node + lifecycle `close_call` both fire; pre-hook bakes outcome and sets `close_sent=yes` so the second send is skipped. Never reset `close_sent`; never add another close tool.
- Resolve prefix: **never** `A or B if C else D` (drops Freya flat response keys).

### Promise / note recorded transitions

- Promise: transition on `promise_just_ok=yes` (post-hook on ok), **not** sticky `create_promise_response.data.ok`.
- Notes: transition on `note_just_ok=yes`, **not** sticky `add_note_response.data.ok`.

---

## Repair loop

1. Reproduce with `--only PUB-XX` (and `--reps 2` if flaky).
2. Classify failure: **ledger** vs **conversation judge** vs **honesty**.
3. Prefer fixing, in order:
   1. `builder/guards/*` or `compile.py` graph/transitions
   2. Policy IR (`bands`, `fixed_sentences`, `intents`, `calendar.holidays`, …) in `builder/policies/*.policy.json`
   3. `task_message` text (**last resort only** — never substitute prompt text for hardship / floor / paid / restructure subgraphs or persuasion / note counters; new behavior → code node / route)
4. Re-run `test_guards.py` → `build.py --slot …` → `run_cases.py`. Confirm the printed **version** incremented before trusting the retest.
5. At most three repair rounds per failure class before reassessing architecture.
6. **Do not** hand-edit `out/*.workflow.json` as source of truth; it is a compile artifact.

### Goodbye / `max_turns_exceeded` on `finalize_notes` (diagnose before coding)

Case checks often already pass (promise + notes + persuasion). Pull `get_call` / route `variable_table` and match **one** layer — do not invent a second mechanism if the first is missing from the **published** version:

| What you see | Layer | Fix (already in tree — verify published, don’t reinvent) |
|---|---|---|
| Re-enters `check_remaining` every turn with sticky `add_note_response.data.ok=true` and no new tool call | Sticky ok | `note_just_ok` one-shot + clear in `check_remaining` |
| After parallel `add_note`, `noted_products` missing a pid, `open_products` still set, model chats goodbye until `max_turns_exceeded` | Parallel race | One pid per finalize visit + `note_attempted_*` always credited in `check_remaining` |
| Farewell TTS / “Goodbye” prompt | Not the bug | Do **not** edit goodbye copy |

**Do not** add a third parallel-race design if those two are present in the compiled workflow. Read `out/<slot>.workflow.json` / the published version’s `finalize_notes` transitions and `check_remaining` code first.

### Recreate / E2E bench discipline

- If slot `brief-a-b` already has PUB-12 **2/2** on the current code, a new bench slot should **build + test only**. Prefer `CODE_CHANGED: no`.
- Only edit guards when your failing call’s variable_table shows a missing mechanism in the **version you just published**.
- Never “fix” a judge goodbye failure by lengthening prompts while `open_products` is non-empty or transitions still use sticky `.data.ok`.

### Unseen finals (blocker)

`extract.py` **cannot** extract a new brief — `_llm_extract` always returns `None` and falls back to the A/B hand policy. For a new brief: hand-author `builder/policies/<slot>.policy.json`, point the extract path / `HAND_POLICY` at it (or add a `--policy` flag), and confirm `out/<slot>.policy.json` is the new file before saving. No brief values in Python; all go in the policy.

---

## Freya / ops gotchas

| Symptom | Cause / fix |
|---|---|
| Cloudflare **1010** | Missing browser-like `User-Agent` — use `freya.py` |
| **503** `API key validation temporarily unavailable` | Platform auth blip; retry; only cancel orphan sims after a long stall |
| Sim stuck `running` | `run_simulation` waits in 30s loops (not an error). Cancel only after >15 min with no progress |
| Score unchanged after edit | Workflow not published — always go through `freya.save_workflow` / `build.py` |
| `customer-ended-call`, slow turns | Model lag; `run_cases` warns if median turn > 10s — re-run; try `--rerun-if-slow 1` |
| `exceeded-max-duration` / goodbye loop / `max_turns_exceeded` on `finalize_notes` | See **Goodbye / max_turns** diagnose table above. Not a farewell-prompt bug. `collection`=60, `finalize_notes`=6, `hardship`=4 |
| Bank `unknown product_id` | Model invented id; use `p1`… from `get_account` / `allowed_product_ids` |
| Route “No match” on identity mid-verify | Normal: stay on node until `all_verified` or fail branch |
| Duplicate refused notes | `add_note` must not be on `collection`; gate `noted_products` |

Inspect a call:

```bash
python3 -c "import freya,json; print(json.dumps(freya.get_call('CALL_ID'), indent=1)[:12000])"
# Full judge reason (results JSON truncates at 400 chars):
python3 -c "import freya,json; print(json.dumps(freya.get_iteration('SIM_ID','IT_ID'), indent=1)[:12000])"
```

Success criteria live in `public-test-set.json` (`checks` + `expected`). Scoring = ledger + judge + honesty; finals need **3/3** reps.

---

## Scoreboard

Operational scores live in `HANDOFF.md` and `results/log.jsonl` / `results/*.json` for the **currently published** version. This skill documents mechanisms, not pass counts. Re-verify claimed greens after any graph/guard change; one green run is a hint, not proof.

---

## Finish line

When done, report one line:

`workflow_id=… agent_id=… version=… passed=N/M (results/…)`

Update `HANDOFF.md` when the scoreboard changes. Update **this skill** only when you add a durable guard mechanism or graph node future builds must preserve.

---

## Rules (short)

- Prefer code / routes / hooks over prompts.
- Tool fields only from openapi.
- No Freya UI edits; policy → compile → save → harden → test.
- Never claim success when a write was blocked or `ok` is false.
- Keep `builder/test_guards.py` green before every deploy.
- Reuse slot `brief-a-b`; one sim at a time; wait before cancel.
