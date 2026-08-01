import {
  buildVietlottHeaders,
  hasOversizedDeclaredBody,
  INTERNAL_TARGET_HEADER,
  jsonResponse,
  parseApprovedTarget,
} from "../../shared/proxy";

const MIN_TOKEN_LENGTH = 32;

async function secureEqual(left: string, right: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [leftHash, rightHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  return crypto.subtle.timingSafeEqual(leftHash, rightHash);
}

async function isAuthorized(request: Request, expectedToken: string): Promise<boolean> {
  if (expectedToken.length < MIN_TOKEN_LENGTH) {
    return false;
  }
  const authorization = request.headers.get("Authorization") ?? "";
  const prefix = "Bearer ";
  const provided = authorization.startsWith(prefix)
    ? authorization.slice(prefix.length)
    : "";
  return secureEqual(provided, expectedToken);
}

function buildInternalRequest(request: Request, target: URL): Request {
  const headers = buildVietlottHeaders(request);
  headers.set(INTERNAL_TARGET_HEADER, target.toString());
  return new Request("https://vietlott-fetcher.internal/proxy", {
    method: request.method,
    headers,
    body: request.method === "POST" ? request.body : null,
    redirect: "manual",
  });
}

export default {
  async fetch(request, env): Promise<Response> {
    const requestUrl = new URL(request.url);
    if (request.method === "GET" && requestUrl.pathname === "/health") {
      return jsonResponse({ service: "vietlott-official-relay", status: "ok" }, 200);
    }

    if (requestUrl.pathname !== "/proxy") {
      return jsonResponse({ error: "Not found" }, 404);
    }
    if (request.method !== "GET" && request.method !== "POST") {
      return jsonResponse({ error: "Method not allowed" }, 405);
    }
    if (!(await isAuthorized(request, env.RELAY_TOKEN))) {
      return jsonResponse({ error: "Unauthorized" }, 401);
    }

    const target = parseApprovedTarget(requestUrl.searchParams.get("url"), request.method);
    if (target === null) {
      return jsonResponse({ error: "Target is not allowed" }, 400);
    }
    if (hasOversizedDeclaredBody(request)) {
      return jsonResponse({ error: "Request body is too large" }, 413);
    }

    try {
      return await env.UPSTREAM.fetch(buildInternalRequest(request, target));
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "Vietlott relay service request failed",
          error: error instanceof Error ? error.message : String(error),
          host: target.hostname,
          path: target.pathname,
        }),
      );
      return jsonResponse({ error: "Upstream service failed" }, 502);
    }
  },
} satisfies ExportedHandler<Cloudflare.Env>;
