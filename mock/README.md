# Final mock (Cloudflare Workers)

`src/index.js` is **generated** by the builder from the case's test data: `builder/case/mockgen.py` embeds every
recorded answer (status + body) and serves it by matching the request's `data` values against the recorded keys
(customer number, card number, requested week...). Not-found requests get the service's 404 from the contract's
error table, or a 400 invalid-arguments body where the contract has no 404. Nothing is typed by hand.

```bash
cd mock && npx wrangler login          # once
python3 builder/build.py "<case folder>" https://freya-final-mock.<subdomain>.workers.dev   # regenerates, tests, deploys
python3 builder/case/test_mock.py "<case>/test-environment/test-customers.json" mock/src/index.js   # offline exact-match
npx wrangler tail freya-final-mock     # watch the Freya engine's requests during a simulation
```

Deployed: `https://freya-final-mock.freya-bank-mock.workers.dev` (`GET /health`, `POST /voice/v1/<service>`).
Keep it running until the results are out. Cloudflare returns 403 to Python-urllib user agents; curl and the Freya engine are fine.
