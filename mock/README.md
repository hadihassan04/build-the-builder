# Final mock (Cloudflare Workers)

Final scoring calls **your** mock through the phone agent's tools. If this is down, those conversations fail.

## Before the final zip

Scaffold matches the seven practice tools. When the zip opens:

1. Read the service contract + test customers.
2. Replace `CUSTOMERS` / request-response shapes in `src/index.js`.
3. Deploy and keep it running until results are published.

```bash
cd mock
npm install
npx wrangler login   # once
npx wrangler deploy
# → https://freya-final-mock.<you>.workers.dev
```

Pass that URL (no `/v1` suffix required) as `{mock_url}` to the builder:

```bash
python3 builder/build.py FreyaBank-Ekstre-Borc/ https://freya-final-mock.<you>.workers.dev
```
