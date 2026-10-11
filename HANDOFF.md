# Handoff — Guarded Freya phone-agent builder

Last updated: 2026-10-10 (~22:55 UTC+2)

Use this file to continue in a new chat. Do **not** re-derive the architecture from scratch; start from **Confirmed root causes** and the **Preemptive risk matrix**.

**Scoreboard (agent v29):** Fixed PUB-04 deadline loop (Brief A.23: TTS offer once → listen; re-insist → refusal, not infinite re-offer) + close_call duplicate no longer DNS-fails on `example.invalid`. Retest `PUB-04,PUB-08` **2/2** (`results/fix-loop-close-v29.json`). Prior pass^3 on v27 was **6/13** (`results/beast-pass3.json`).

---

## Can we read success criteria after a run?

**Yes.** After `tools/run_cases.py`:

| Source | What it gives you |
|---|---|
| `public-test-set.json` → each case `checks` | Exact success criteria the judge uses |
| `public-test-set.json` → `expected` | Ledger outcome, promises, notes, callbacks, transfers |
| `results/*.json` → `rows[].failed` | Scorer + judge failure reasons |
| `rows[].call_id` | Fetch full transcript: `python3 -c "import freya,json; print(json.dumps(freya.get_call('CALL_ID'), indent=1)[:8000])"` |
| Persona `successCriteria` | Built in `run_cases.py` from `checks` |

Scoring is **ledger + conversation policy + honesty**. No partial credit. Finals need **3/3** runs pass per customer.

---

## Goal

Builder that reads any brief (+ openapi) → Policy IR → Freya workflow. Prefer **code / routes / hooks** over prompts. Finals: unseen briefs, no brief values hardcoded in the builder.

Plan (reference only): `~/.cursor/plans/guarded_builder_architecture_27a46eb4.plan.md` — do not require editing it.

---

## Current Freya agents (`.freya-agents.json`)

| Slot | workflow_id | agent_id | Notes |
|---|---|---|---|
| **brief-a-b** (primary) | `7f801b26-2ba5-4d8f-a2f0-752e19626261` | `eee16ad8-b0c3-4333-a9ef-8c446d9e78fd` | Used for the 5-case run |
| brief-a | `4e60e1d7-f886-412d-a64d-952ba7711d56` | `2c1e48c5-9888-49ab-8288-833d57e98cbb` | Earlier compile |
| spike / spike-defang | (see json) | | Platform probes |

Reuse slots via `freya.save_workflow(slot, ...)`. Cap ~200 workflows/workspace.

---

## Latest test result: PUB-01–08 = 8/8

Agent version **8** on slot `brief-a-b` (`eee16ad8-…`). Result: `results/brief-a-1-8-v8.json`.

| Case | Pass | Mechanism |
|---|---|---|
| PUB-01–05 | YES | See prior handoff (MONTH_DAYS, callback snap, verify `0`, hardship node, deadline fail-closed) |
| PUB-06 Floor | YES | `stage_floor` → `floor_offer` TTS once (amount + rest overdue + Attempt-1 date); `floor_offered` bakes `gate_*` into `create_promise` |
| PUB-07 Holiday | YES | `date_facts` holiday→next-BD lines; gate blocks holiday with `offer_date=next BD`; `vague_period` intent forces “What exact day works?” |
| PUB-08 Paid | YES | `already_paid` → `close_paid` / `speak_paid` (fixed delay sentence) → close `paid` |

### Fixed root causes (do not regress)

1. **`MONTH_DAYS` / `dates_ready`** must ship inside `DATE_ENGINE_CODE`; gates **fail closed** if `deadlines_json` empty.
2. **Callback gate** enforces today/next BD + window; snaps illegal dates to `callback_tomorrow`; post-hook sets `has_callback` / forced outcome.
3. **Resolve prefix** must not use `A or B if C else D` (drops Freya flat keys).
4. **Verify post-hook** must not treat int `0` as missing.
5. **Hardship / floor / paid** are real subgraphs, not prompt-only inside `collection`.
6. **Holiday ≠ after deadline** — offer next BD from `date_facts` / gate `offer_date`, not band deadline.
7. **`add_note` refused blocked** while `proposed_date` is after a product deadline (PUB-04 flake).

### Success criteria for those cases (from public-test-set)

- **PUB-02**: callback tomorrow ~11; no debt disclosed to relative; `not_reached` / `callback`
- **PUB-03**: check ends after three wrong answers; no debt stated; `not_verified` / `failed`
- **PUB-04**: AI disclosure; date after deadline → offer deadline; promise on deadline date
- **PUB-05**: one sympathy sentence; **no** persuasion; offer support once; decline → `no_promise` / `hardship`; no discount

---

## Architecture (what exists)

```
briefs + tools/openapi.json
  → builder/ingest.py + extract.py  (A/B uses hand policy)
  → builder/policies/brief_a_b.policy.json
  → builder/compile.py              (18-node workflow)
  → builder/lint.py + harden.py     (temp 0.2 + guardrailsConfig merge)
  → freya.save_workflow
  → tools/run_cases.py
```

Key dirs: `builder/guards/` (date_engine, policy_gate, ledger_mirror, outcome_resolver, post_hooks), `builder/templates/tools.py`, `out/*.workflow.json`, `out/*.policy.json`.

**Repair rule:** edit **policy JSON only**, then `python3 builder/build.py briefs/brief-a.md briefs/brief-b.md --slot brief-a-b`. Never hand-edit Freya UI.

Skill: `.claude/skills/build-phone-agent/SKILL.md`  
Agent SDK entry: `examples/agent_builder.py`

---

## Platform spike findings (`builder/spike_results.json`)

- Chat `/chat/message` often **503/504** — hook runtime not observed live.
- Hooks / `maxPerNTurns` **stored** on tools.
- Partial `agent_config` **rejected**; merge into platform config after save works (temperature + guardrailsConfig).
- Write defang: redirect blocked writes to `https://example.invalid/...` (Grok update). Still **failed to stop PUB-04** — gate likely not seeing deadlines / not baking pending_date, or model filled args after gate.
- Prefer **gate-before-tool** (write tool only on node after code sets `pending_*` + confirmation) until chat probes prove pre_hooks rewrite args reliably.
- `close_call`: in-graph + lifecycle; pre-hook should skip duplicate. PUB-03/05 still got **null outcomes** — fix outcome resolver wiring first.

---

## Env

```bash
source .env   # FREYA_API_KEY, FREYA_BANK_TEAM_KEY, etc.
# One simulation at a time per workspace; FREYA_SIM_CONCURRENCY=5 concurrent calls inside it
```

Offline sanity (no wait):

```bash
python3 builder/test_guards.py          # must stay 7/7
python3 builder/lint.py out/brief-a-b.workflow.json
```

---

## Results verdict (1/5)

Happy path works. **Structural authority is not actually holding** on the four failures: the model (or empty vars) still chooses illegal dates, and closes often leave **null ledger outcomes**. Fixing prompts alone will not get you past PUB-04/02. Prefer code-set `gate_*` + `gate_ok` and dedicated hardship/identity close nodes.

---

## Confirmed root causes (from `out/brief-a-b.workflow.json` gates)

Inspected the **saved** workflow (what Freya ran), not only source:

1. **PUB-04 — deadlines_json empty or unused**  
   `create_promise` pre_hook only blocks when `limit and date > limit`. If `deadlines_json` is `{}` / unset (compute_dates or get_account post-hook never populated it for that call), **after-deadline dates pass**. Bank mock records them.  
   Also: gate trusts **model body** `date`/`amount`/`product_id` instead of code-baked `gate_date` / `gate_ok` (source `policy_gate.render_gate` exists but **is not what this workflow uses**).

2. **PUB-02 — callback gate ignores calendar**  
   `schedule_callback` pre_hook only checks time ∈ 10:00–19:00 and date non-empty. **No** check that date ≥ `now`, business day, or “today or next business day”. Hence ledger got `2026-10-11` before `now=2026-10-14`.  
   Outcome `no_promise/refused`: success path never forced `not_reached/callback` into close.

3. **PUB-03 / PUB-05 — close_call with empty outcome**  
   Tools fired `close_call` but ledger outcome `(None, None)`. Lifecycle/in-graph pre_hook did not bake `resolved_outcome` / `forced_outcome`. Identity-fail and hardship paths must **set outcome in a code node**, then enter a node whose close body is fully baked (no model-filled outcome).

4. **PUB-05 — no finite hardship state machine**  
   Hardship is prompt text inside `collection`. Model loops sympathy + support offers until **exceeded-max-duration**. Need a passthrough/conversation node: fixed sympathy TTS → one support ask → transfer **or** `_force_close(hardship)` → goodbye. Cap turns at 4.

Source drift: `builder/guards/flow.py` + `render_gate(gate_ok=...)` look stricter than the **deployed** workflow. Next build must compile the stricter path and re-save `brief-a-b`, or the same bugs remain.

---

## Preemptive risk matrix (PUB-06 … PUB-13, not yet run)

| Case | Risk | Why it will likely fail with current agent |
|---|---|---|
| **PUB-06** Floor amount | **HIGH** | Needs “never ask how much”, offer floor once, promise **637.50 on Fri 16 Oct**. Amounts still model-driven; no code clamping to floor on confirm. |
| **PUB-07** Vague → holiday | **HIGH** | Same deadline/holiday engine gap as PUB-04. Must reject 26 Oct holiday → **27 Oct**. If `deadlines_json`/holiday shift missing, wrong date lands. |
| **PUB-08** Already paid | **MED** | Needs exact delay sentence + outcome `paid` with no promise. If `forced_outcome` broken (like PUB-03/05), closes null or takes a promise. |
| **PUB-09** Restructuring | **MED** | Needs `transfer_to_agent` queue `restructuring` and close `transferred`. Transfer tool exists; outcome baking may still null. |
| **PUB-10** Promise 500 then OK | **HIGH** | Honesty: must not say “recorded” before ok; retry once. Prompt + TTS “recorded” node may fire on blocked/failed first call; gate/post-hook `what_to_do_next` must drive retry. |
| **PUB-11** 3 products one date | **HIGH** | Brief B: state all products, **one** confirmation, 3× `create_promise`. Current `collection` is single-product-shaped; likely one promise or wrong confirm loop. |
| **PUB-12** 1 promise + 2 notes | **HIGH** | Wrong phone then correct; per-product refuse + `add_note`; identity retry rules. Notes + multi-product + verify retry all fragile. |
| **PUB-13** Split deadlines | **HIGH** | One customer date → clamp **per product** (30 Oct card / 23 Oct loan). Without per-product deadline map in the gate, both get same illegal or same legal date. |

**Untested Brief A also:** PUB-06–10. Expect ≤2/5 more passes without gate + close + multi-product work.

---

## Recommended fixes (priority)

| # | Fix | Mechanism |
|---|---|---|
| 1 | **Bake deadlines after get_account** | Code node always writes `deadlines_json`, `floors_json`, `past_dues_json`, `allowed_product_ids`. Fail closed if missing. |
| 2 | **Promise writes only via gate_ok** | Confirm → code validates/clamps date+amount → sets `gate_ok=yes`, `gate_date`, `gate_amount`, `gate_product_id` → model-only node that can call `create_promise` with body **fully overwritten** from those vars (use `render_gate`). Never trust model date. |
| 3 | **Callback calendar in pre_hook** | Require date ∈ {today…next BD}, time 10:00–19:00, after `now`. On success code sets `forced_outcome=not_reached`, `forced_reason=callback`. |
| 4 | **Close outcome always baked** | Every terminal path: code sets `resolved_outcome`/`resolved_reason`; close tool body defaults to those vars; strip model freedom. Test PUB-03/05 ledger. |
| 5 | **Hardship subgraph** | Leave `collection` on hardship intent → fixed TTS sympathy → one-turn support ask → transfer or hardship close. `max_node_turns=4`. |
| 6 | **Holiday shift in date_engine** | Shared helper: weekend/holiday → next BD; if after deadline → deadline. Used for PUB-07 and PUB-04 offer. |
| 7 | **Multi-product compiler path** | When `multi_product.enabled`: loop products, single read-back, N promises + notes (PUB-11–13). |
| 8 | **Honesty for PUB-10** | “Recorded” TTS only on transition from `create_promise ok`; first failure → retry instruction in post-hook only. |

### Do not waste time on
- More system_prompt anti-injection text  
- Relying on chat spike while 504s persist — use `run_cases` + `get_call`  
- Redteam / styles / `--reps 3` before PUB-02–05 are green  
- Hand-editing Freya UI

---

## Sol / Grok review notes

### Sol ([gpt-5.6-sol](d394258c-5e93-4707-8109-15f85cc3c62f)) — done

Agrees the failures are architectural, not “bad luck”:

1. **`flow.py` is not compiled** — deterministic state machine exists but `compile.py` never wires it; dialogue still owns dates/outcomes/hardship/multi-product.
2. Write guards **fail open** when deadline maps missing; they reject but do **not** clamp and re-stage a legal date (PUB-04).
3. Callback gate omits date policy (PUB-02) — matches our saved-workflow inspection.
4. **`update_ledger_from_tool` unused in runtime** — post-hooks don’t set `has_callback` / `ledger_transferred` / notes → resolver blind → null/wrong closes (PUB-03/05/08/09 risk).
5. Two close paths + `close_sent` set too early → first null close cannot recover.
6. Hardship: no terminal `decline_transfer` path; discount triggers manipulation global → loops (PUB-05).
7. Collection `max_node_turns=60` + default `refused` masks unresolved states.
8. Offline tests don’t cover deployed hook bodies / routes.

Sol’s preemptive matrix: **HIGH** for PUB-06,07,08,09,11,12,13; **MED** for PUB-10. Retest: offline route tests → PUB-02–05 → probes 07/08/09/11/13 → 06/10/12 → full 13 → then 3× full for finals.

### Grok ([grok-4.7](d39f6aa3-dcaf-406a-b62f-2073773d3434)) — done

Same diagnosis, sharper on **live wiring**:

1. Deployed graph is prompt-driven `collection`; **`compile.py` never imports `flow.py`**; **`render_gate` unused**; tool bodies still `{{date}}` etc. from the model (`templates/tools.py`).
2. PUB-04: pre_hook blocks only if `deadlines_json[pid]` set — never **clamps** to deadline / never runs `fit_named_date`. Bank kept 6 Nov → hook did not replace the body.
3. PUB-02: booked **2026-10-11** looks like the **real-world** “tomorrow” (run day Sat 10 Oct), not scenario `now=2026-10-14` → tomorrow = **15 Oct**. Callback gate ignores `now` entirely. Prompt “set forced_outcome mentally” cannot set vars.
4. PUB-03/05 null close: `end_call` available on identity/collection **before** code sets `resolved_*`; lifecycle templates empty vars. Ledger mirror not in post-hooks → resolver defaults / misses callback.
5. PUB-05: no graph edge for hardship / `decline_transfer`; 60-turn collection loop until max-duration.

Grok preemptive matrix matches Sol (HIGH on 06–09, 11–13; MED on 10). Extra: PUB-08 `already_paid` edge does not set `forced_outcome=paid` → resolver → refused. PUB-09 transfer post-hook never sets `ledger_transferred`.

**Agreed next build order (Sol + Grok + saved-workflow inspection):** wire `flow.py` (or equivalent) into compile → code-bake all writes → full `callback_allowed` + scenario `now` → single close after resolved outcome → hardship subgraph → then retest PUB-02–05 at concurrency 5.

---

## τ-bench alignment + beast-mode synthetic suite (2026-10-10)

[τ-bench](https://arxiv.org/abs/2406.12045) scores tool agents by **final DB state** + **required communication**, and stresses reliability with **pass^k**. We already match that shape:

| τ-bench | Freya kit |
|---|---|
| `db_check` (goal DB state) | `expected` ledger in case JSON + `tools/score.py` |
| `communicate_checks` | `checks[]` → judge `successCriteria` |
| `pass^k` | `tools/run_cases.py --reps k` (finals: **k=3**, all must pass) |
| Policy + tools + LM user | Brief + openapi + Freya personas |

**Constraint:** hosted practice bank only accepts **PUB-01…PUB-13**. New `SYN-*` IDs cannot open sessions (`unknown practice case`). Monetary fail-closed paths for skipped Brief rules are covered **offline**.

| Artifact | Role |
|---|---|
| `synthetic-test-set.json` | 40 SYN cases + coverage matrix (Brief gaps, critical monetary) |
| `builder/synthetic_cases.py` | Source of truth for SYN catalog |
| `builder/test_synthetic_scenarios.py` | Offline gate/outcome assertions (τ-bench action check) |
| `out/redteam-cases.json` | RT-01…RT-12 injection / privacy / honesty stress |
| `tools/beast_mode.py` | One-shot offline preflight (+ optional `--live`) |

```bash
# Offline beast (no Freya wait)
python3 tools/beast_mode.py

# Live pass^k on hosted PUB cases
FREYA_SIM_CONCURRENCY=5 python3 -u tools/run_cases.py eee16ad8-b0c3-4333-a9ef-8c446d9e78fd \
  --reps 3 --out results/beast-pass3.json

# Human-style robustness (finals prep)
python3 -u tools/run_cases.py eee16ad8-… --style talkative --reps 2
python3 -u tools/run_cases.py eee16ad8-… --style in_words --reps 2
```

**Critical SYN families (bank loss / privacy):** wrong number, deceased, KYC decline, below-floor, above-past-due, after-deadline, empty deadlines fail-closed, invented `product_id`, callback snap before `now`, dispute/paid partial multi-product, transfer-after-promise, double `create_promise` failure honesty, relative KYC.

Public set still thin on: A.3/7/8/9/13/15/22/27/40/42/43/45, B.9–11, B.close all-paid — those are the SYN rows.

---

## Next session checklist (priority order)

1. `python3 tools/beast_mode.py` green, then live PUB `--reps 3`.
2. PUB-09 restructuring transfer + `transferred` outcome bake.
3. PUB-10 honesty: “recorded” only after `create_promise` ok; retry once.
4. Full Brief A PUB-01–10 (prefer 2× runs — dialogue flakes exist).
5. Brief B multi-product PUB-11–13 (compiler loop / single confirm / per-product deadlines).
6. Offline: `python3 builder/test_guards.py` must stay green before every save.

---

## Commands cheat sheet

```bash
# Rebuild + save
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md --slot brief-a-b

# Re-test failing five
FREYA_SIM_CONCURRENCY=5 python3 tools/run_cases.py eee16ad8-b0c3-4333-a9ef-8c446d9e78fd \
  --brief A --only PUB-02,PUB-03,PUB-04,PUB-05 --out results/rerun-fail.json

# Inspect one call
python3 -c "import freya,json,sys; c=freya.get_call(sys.argv[1]); print(c.get('transcript','')[:4000]); print('---'); print([(m.get('role'), (m.get('message') or '')[:120]) for m in c.get('messages',[])[:40]])" CALL_ID

# Case success criteria
python3 -c "import json; c=next(x for x in json.load(open('public-test-set.json')) if x['id']=='PUB-04'); print(c['checks']); print(c['expected'])"
```

---

## Context note

A Grok subagent also edited the same tree (no git worktree). Prefer current files under `builder/`; if something looks duplicated or contradictory, trust `builder/test_guards.py` + latest `out/brief-a-b.workflow.json` + this handoff’s failure table.
