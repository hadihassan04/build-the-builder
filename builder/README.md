# Guarded builder

Turns bank briefs into a Freya phone-agent workflow where **code and tool results hold authority**, not the dialogue model.

## Quick start

```bash
# offline compile + lint + guard tests (no API needed for most of this)
python3 builder/test_guards.py
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md --skip-save

# save to Freya and optionally test
source .env   # or env.sh — needs FREYA_API_KEY
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md --slot brief-a
python3 tools/run_cases.py <agent_id> --brief A

# platform spike (writes builder/spike_results.json)
python3 builder/spike.py
```

## Spike findings

Chat probes returned HTTP 504, so hook execution on a live turn was not observed. A saved workflow did keep `pre_process_code`, `post_process_code`, and `maxPerNTurns` on tool config. A partial `agent_config` is rejected; merging temperature and `guardrailsConfig` into the platform's full config after save does persist them. The workflow file still omits `agent_config`. Whether a simulation model override keeps temperature was not observed.

Writes are gated in `pre_process_code`. `close_call` runs in the graph after the goodbye line and again at end of call; the pre-hook skips the second send.

## Pipeline

1. **ingest** — chunk briefs with citation ids; memo supersession
2. **extract** — Policy IR (`builder/ir_schema.json`). For Brief A/B uses `builder/policies/brief_a_b.policy.json`
3. **tools_parse** — deterministic `tools/openapi.json` parser (never LLM)
4. **compile** — IR → workflow with deterministic routes, extraction enums, policy gates, fixed TTS, lifecycle `close_call`
5. **lint** — static checks + optional dry-run
6. **redteam** — adversarial cases → `out/*-redteam.json`
7. **repair** — edit `policy.json` only, recompile (skill / agent_builder)

## Runtime defences

| Layer | Mechanism |
|---|---|
| Control flow | Fixed graph; privileged transitions on tool/code vars only |
| Least privilege | Tools only on nodes that need them; `customer_id` baked |
| Intent quarantine | `customer_intent` enum via extraction; code/priority resolve conflicts |
| Facts | Date engine + account post-hook write `date_facts` / `account_summary` |
| Write gates | `pre_process_code` on create_promise / callback / transfer / add_note |
| Instruction channel | `what_to_do_next` in post-hooks; customer text is data |
| Honesty | Fixed TTS "recorded" node only after `ok=true`; close outcome from code |
| Circuit breakers | `max_node_turns` + failure counters |
| Manipulation | Global node with fixed redirect |

## Repair rule

Never hand-edit the workflow on the platform. Change `*.policy.json` (or the brief extract), re-run `builder/build.py`.
