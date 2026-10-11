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

## During the final window

1. Download the zip → read `BRIEF.md`.
2. Deploy `mock/` from their service contract (`cd mock && npx wrangler deploy`). Keep it up.
3. Run **your** builder on the case folder (whatever command you already use). Point tool URLs at your mock.
4. Export: `python3 -c "import freya; freya.export_workflow('<workflow_id>', 'submission/final.workflow.json')"`
5. Save that run’s stdout as `traces/final.log` (strip API keys).
6. Commit those two files, put the SHA + mock URL in `submission.json`, email it.

Do **not** hand-edit the exported workflow. Do **not** bake final-case rules into the builder.
