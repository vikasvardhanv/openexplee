export interface Env {
  EMAIL: {
    send: (message: Record<string, unknown>) => Promise<void>;
  };
  WORKER_AUTH_TOKEN?: string;
  FORWARD_TO_EMAIL?: string;
  OPENEXPLEE_WEBHOOK_URL?: string;
}

interface ForwardableEmailMessage {
  readonly from: string;
  readonly to: string;
  readonly headers: Headers;
  forward(rcptTo: string, headers?: Headers): Promise<void>;
}

interface SendPayload {
  from: string;
  fromName?: string;
  to: string | string[];
  cc?: string | string[];
  replyTo?: string;
  subject: string;
  text?: string;
  html?: string;
}

function normalizeRecipients(value?: string | string[]): Array<{ email: string }> {
  if (!value) return [];
  const items = Array.isArray(value) ? value : value.split(",");
  return items
    .map((entry) => entry.trim())
    .filter((entry) => entry.includes("@"))
    .map((email) => ({ email }));
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResponse({ ok: true, service: "openexplee-cf-mailer" });
    }

    if (request.method === "POST" && url.pathname === "/send") {
      if (!env.WORKER_AUTH_TOKEN) {
        return jsonResponse({ error: "WORKER_AUTH_TOKEN secret not configured" }, 500);
      }

      const authHeader = request.headers.get("Authorization") || "";
      if (authHeader !== `Bearer ${env.WORKER_AUTH_TOKEN}`) {
        return jsonResponse({ error: "Unauthorized" }, 401);
      }

      let payload: SendPayload;
      try {
        payload = (await request.json()) as SendPayload;
      } catch {
        return jsonResponse({ error: "Invalid JSON payload" }, 400);
      }

      const toList = normalizeRecipients(payload.to);
      if (!payload.from || toList.length === 0 || !payload.subject || (!payload.text && !payload.html)) {
        return jsonResponse({ error: "Missing required fields: from, to, subject, text/html" }, 400);
      }

      const emailParams: Record<string, unknown> = {
        from: payload.fromName ? { email: payload.from, name: payload.fromName } : { email: payload.from },
        to: toList,
        subject: payload.subject,
      };

      const ccList = normalizeRecipients(payload.cc);
      if (ccList.length > 0) emailParams.cc = ccList;
      if (payload.replyTo) emailParams.replyTo = { email: payload.replyTo };
      if (payload.text) emailParams.text = payload.text;
      if (payload.html) emailParams.html = payload.html;

      try {
        await env.EMAIL.send(emailParams);
        return jsonResponse({ ok: true, from: payload.from, to: toList.map((r) => r.email) });
      } catch (err) {
        const message = err instanceof Error ? err.message : "Cloudflare Email send failed";
        return jsonResponse({ ok: false, error: message }, 502);
      }
    }

    return jsonResponse({ error: "Not found" }, 404);
  },

  async email(message: ForwardableEmailMessage, env: Env, ctx: { waitUntil: (p: Promise<unknown>) => void }): Promise<void> {
    if (env.FORWARD_TO_EMAIL) {
      await message.forward(env.FORWARD_TO_EMAIL);
    }

    if (env.OPENEXPLEE_WEBHOOK_URL) {
      const subject = message.headers.get("subject") || "";
      ctx.waitUntil(
        fetch(env.OPENEXPLEE_WEBHOOK_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(env.WORKER_AUTH_TOKEN ? { Authorization: `Bearer ${env.WORKER_AUTH_TOKEN}` } : {}),
          },
          body: JSON.stringify({
            from_email: message.from,
            to_email: message.to,
            subject,
          }),
        }).catch(() => undefined)
      );
    }
  },
};
