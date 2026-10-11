# Submission (additive — does not change the kit)

Kit files stay as shipped in `build-the-builder-v3.zip`. Submission extras live beside them.

## What Freya emails for

Fill `submission.json` and send it before **20:00 UTC**:

```json
{
  "team": "bob-the-builder",
  "members": [{"name": "Hadi Hassan", "email": "hadihassan04@gmail.com"}],
  "repo": "https://github.com/hadihassan04/build-the-builder",
  "commit": "<git rev-parse HEAD after final run>",
  "run_command": "python3 builder/build.py {case_folder} {mock_url}",
  "workflow": "submission/final.workflow.json",
  "trace": "traces/final.log",
  "mock_url": "https://YOUR.workers.dev"
}
```

## During the final window (what the builder does in one command)

```bash
cd mock && npx wrangler login && cd ..        # once
python3 builder/build.py "<case folder>" https://freya-final-mock.freya-bank-mock.workers.dev --live-test
```

One run: ingests the folder → parses the service contract → generates + tests + deploys the Cloudflare mock from
`test-customers.json` → extracts the case policy with one logged model call → compiles, lints and offline-tests the
workflow → saves it to the Freya slot `final` → exports `submission/final.workflow.json` → writes
`traces/<timestamp>-builder.jsonl` and copies it to `traces/final.log` → (with `--live-test`) runs simulated calls.
Then commit, put the SHA in `submission.json`, e-mail it. See `builder/case/README.md`.

Do **not** hand-edit the exported workflow. Do **not** bake final-case rules into the builder.
