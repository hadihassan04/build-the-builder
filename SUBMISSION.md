# Submission checklist (Freya × YC — Build the Builder)

Deadline: **Sunday 20:00 UTC (23:00 Istanbul)**. Email `submission.json` to the address in your kit email.

## What Freya needs from you

| Item | Where | Notes |
|---|---|---|
| Builder repo | GitHub URL in `submission.json` | One command: case folder in → phone agent out. No hand edits after the run. |
| Commit SHA | `submission.json` → `commit` | Must match the code that produced the agent. |
| Run command | `submission.json` → `run_command` | Example: `python3 builder/build.py {case_folder} {mock_url}` |
| Exported workflow | `submission/final.workflow.json` | From `freya.export_workflow` — this is what they load and score. |
| Builder trace | `traces/final.log` | Same run that made the workflow. Strip API keys. |
| Mock URL | Cloudflare Workers | Agent tools call **only** this mock. Keep it up until results are out. |

### `submission.json` shape

```json
{
  "team": "your-team-name",
  "members": [{"name": "Your Name", "email": "you@example.com"}],
  "repo": "https://github.com/you/your-builder",
  "commit": "9f2c1ab",
  "run_command": "python3 builder/build.py {case_folder} {mock_url}",
  "workflow": "submission/final.workflow.json",
  "trace": "traces/final.log",
  "mock_url": "https://your-mock.your-subdomain.workers.dev"
}
```

Solo = one member. Paths/commands above are the ones this repo uses.

## Final window steps (17:00–20:00 UTC)

1. Download the zip → read `BRIEF.md` / `README.md`.
2. Deploy `mock/` from the service contract + test customers:
   ```bash
   cd mock && npm i && npx wrangler deploy
   ```
3. Run the builder once (or more), keep the chosen run’s stdout:
   ```bash
   source .env
   python3 builder/build.py FreyaBank-Ekstre-Borc/ https://YOUR.workers.dev 2>&1 | tee traces/final.log
   ```
4. Confirm `submission/final.workflow.json` was written (build does `export_workflow`).
5. Commit workflow + trace (no secrets). Do **not** hand-edit the workflow JSON.
6. Fill `submission.json` with the commit SHA and mock URL → email it.

## Trace must show

- start / end time
- each model call (model + one-line purpose)
- Freya create/update (workflow id, agent id, steps/tools)
- any self-tests and results

## Hard rules

- Phone agent comes only from the builder at that commit — no UI / manual JSON edits.
- After the final opens you may fix builder bugs; you may **not** write final-case rules/names/values into the builder.
- Practice tools → kit bank mock. Final tools → **your** Workers mock only.
