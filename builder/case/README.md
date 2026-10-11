# Final-case pipeline (`builder/case/`)

`python3 builder/build.py <case_folder> <mock_url> [--slot final] [--live-test] [--no-deploy-mock] [--skip-save]`

| Step | Module | What it does | Case-specific input |
|---|---|---|---|
| 1 | `ingest_case.py` | Walks the case folder; classifies files by content (brief, service contract, test data, sources, PDF). | the folder |
| 2 | `contract.py` | Parses the markdown service contract (or an OpenAPI file) into a services IR: names, URLs, request fields, response examples, error table, envelope. | the contract |
| 3 | `mockgen.py` + `test_mock.py` | Generates `mock/src/index.js` (Cloudflare Worker) from the test-data JSON with exact status + body per recorded answer; not-found fallbacks come from the contract's error table. Exact-match tests run under Node. The worker is deployed with `npx wrangler deploy` (needs a prior `wrangler login`). | test data + contract |
| 4 | `extract_case.py` | One logged model call (`claude -p`) reads every source document and fills `case_schema.json`: service roles, field names, currencies, every spoken sentence, limits, handling decisions. Validated; retried once with the errors. | all sources |
| 5 | `compile_case.py` + `runtime_case.py` | Compiles the policy into a Freya workflow: `api_request` nodes with baked bodies for every service, Python code nodes that render Turkish (or any language) sentences from the policy, one dispatcher code node for routing, a single `listen` node whose only job is extraction, a `calm` node for upset callers, and one `end` node whose farewell comes from a variable. | the policy |
| 6 | `test_case.py` | Executes the compiled code nodes locally against the test data (cards, debt variants, e-mail, week change, silence, attempts, agent/pay/dispute, injection, mock lookup). | policy + test data |
| 7 | `build_case.py` | Orchestrates, lints, saves to Freya (`slot`), hardens temperature, exports `submission/<slot>.workflow.json`, writes the trace (`traces/<ts>-builder.jsonl`, copied to `traces/final.log`). | — |

Nothing in this package names a bank, a product, a currency, a sentence or a limit; locale tables (number words, month names, ordinals) are language resources keyed by the policy's `language`.

Live calls: `python3 tools/run_final_cases.py <agent_id> [--only S1,S7]` runs scripted simulated customers against the deployed mock and prints full transcripts.
