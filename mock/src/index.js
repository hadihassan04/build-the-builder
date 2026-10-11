/**
 * Freya final — bank services mock (Cloudflare Workers).
 *
 * Replace CUSTOMERS with the test customers from the final zip's service
 * contract. Tool names stay the seven from the kit; fields/enums can change.
 *
 * Paths: POST /v1/<tool>  (or /<tool> — both work)
 * Tools: verify_identity, get_account, create_promise, add_note,
 *        schedule_callback, transfer_to_agent, close_call
 */

/** @type {Record<string, object>} keyed by customer_id */
const CUSTOMERS = {
  // Placeholder shaped like practice — overwrite from the final zip before deploy.
  demo: {
    identity: {
      date_of_birth: "1988-03-14",
      phone_last4: "4471",
    },
    products: [
      {
        product_id: "p1",
        name: "Credit card",
        past_due: 420.0,
        currency: "TRY",
      },
    ],
  },
};

const LEDGER = {
  promises: [],
  notes: [],
  callbacks: [],
  transfers: [],
  closes: [],
  verify: {},
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
    },
  });
}

function toolName(pathname) {
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  return parts[parts.length - 1] || "";
}

function customer(body) {
  const id = body.customer_id || body.customerId;
  return { id, row: CUSTOMERS[id] };
}

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET,POST,OPTIONS",
          "access-control-allow-headers": "content-type",
        },
      });
    }

    const url = new URL(request.url);
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      return json({ ok: true, customers: Object.keys(CUSTOMERS).length });
    }

    if (request.method !== "POST") {
      return json({ ok: false, error: "method_not_allowed" }, 405);
    }

    let body = {};
    try {
      body = await request.json();
    } catch {
      body = {};
    }

    const name = toolName(url.pathname);
    const { id, row } = customer(body);

    switch (name) {
      case "verify_identity": {
        if (!row) return json({ ok: false, error: "unknown_customer" });
        const key = id;
        LEDGER.verify[key] = LEDGER.verify[key] || { wrong: 0, ok_fields: [] };
        const st = LEDGER.verify[key];
        const field = body.field;
        const answer = String(body.answer ?? "").trim();
        const expected = String((row.identity || {})[field] ?? "");
        if (!expected) return json({ ok: false, error: "unknown_field", field });
        if (answer === expected) {
          if (!st.ok_fields.includes(field)) st.ok_fields.push(field);
          const all = Object.keys(row.identity || {});
          const done = all.every((f) => st.ok_fields.includes(f));
          return json({
            ok: true,
            field,
            verified: true,
            all_verified: done,
            wrong_answers_left: Math.max(0, 3 - st.wrong),
          });
        }
        st.wrong += 1;
        return json({
          ok: false,
          field,
          verified: false,
          all_verified: false,
          wrong_answers_left: Math.max(0, 3 - st.wrong),
        });
      }

      case "get_account": {
        if (!row) return json({ ok: false, error: "unknown_customer" });
        return json({
          ok: true,
          customer_id: id,
          products: row.products || [],
        });
      }

      case "create_promise": {
        const rec = {
          customer_id: id,
          product_id: body.product_id,
          amount: body.amount,
          date: body.date || body.promise_date,
          at: new Date().toISOString(),
        };
        LEDGER.promises.push(rec);
        return json({ ok: true, promise: rec });
      }

      case "add_note": {
        const rec = {
          customer_id: id,
          product_id: body.product_id,
          reason: body.reason,
          text: body.text || body.note,
          at: new Date().toISOString(),
        };
        LEDGER.notes.push(rec);
        return json({ ok: true, note: rec });
      }

      case "schedule_callback": {
        const rec = {
          customer_id: id,
          date: body.date,
          time: body.time,
          at: new Date().toISOString(),
        };
        LEDGER.callbacks.push(rec);
        return json({ ok: true, callback: rec });
      }

      case "transfer_to_agent": {
        const rec = {
          customer_id: id,
          reason: body.reason || body.queue,
          at: new Date().toISOString(),
        };
        LEDGER.transfers.push(rec);
        return json({ ok: true, transfer: rec });
      }

      case "close_call": {
        const rec = {
          customer_id: id,
          outcome: body.outcome,
          reason: body.reason,
          at: new Date().toISOString(),
        };
        LEDGER.closes.push(rec);
        return json({ ok: true, close: rec });
      }

      case "ledger":
        return json({ ok: true, ledger: LEDGER });

      default:
        return json({ ok: false, error: "unknown_tool", tool: name }, 404);
    }
  },
};
