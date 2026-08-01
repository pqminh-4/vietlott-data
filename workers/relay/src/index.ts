const ALLOWED_HOSTS = new Set([
  "vietlott.vn",
  "www.vietlott.vn",
  "media.vietlott.vn",
]);

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "accept-language",
  "content-type",
  "cookie",
  "origin",
  "referer",
  "sec-fetch-dest",
  "sec-fetch-mode",
  "sec-fetch-site",
  "user-agent",
  "x-ajaxpro-method",
  "x-requested-with",
] as const;

const MAX_DECLARED_BODY_BYTES = 256 * 1024;
const MIN_TOKEN_LENGTH = 32;
const MAX_REDIRECTS = 3;

function jsonResponse(body: Record<string, unknown>, status: number): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

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

function parseApprovedTarget(rawTarget: string | null, method: string): URL | null {
  if (rawTarget === null) {
    return null;
  }

  let target: URL;
  try {
    target = new URL(rawTarget);
  } catch {
    return null;
  }

  if (
    target.protocol !== "https:" ||
    target.username !== "" ||
    target.password !== "" ||
    target.port !== "" ||
    !ALLOWED_HOSTS.has(target.hostname)
  ) {
    return null;
  }

  const isMedia = target.hostname === "media.vietlott.vn";
  if (isMedia) {
    return method === "GET" && target.pathname.startsWith("/main/") ? target : null;
  }

  if (target.pathname === "/ajaxpro/" || target.pathname.startsWith("/ajaxpro/")) {
    return method === "GET" || method === "POST" ? target : null;
  }

  const detailPrefix = "/vi/trung-thuong/ket-qua-trung-thuong/";
  return method === "GET" && target.pathname.startsWith(detailPrefix) ? target : null;
}

function buildUpstreamHeaders(request: Request): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) {
      headers.set(name, value);
    }
  }
  headers.set("Cache-Control", "no-cache");
  return headers;
}

async function proxyRequest(request: Request, target: URL): Promise<Response> {
  const contentLength = Number(request.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > MAX_DECLARED_BODY_BYTES) {
    return jsonResponse({ error: "Request body is too large" }, 413);
  }

  let currentTarget = target;
  let upstreamRequest = new Request(currentTarget, {
    method: request.method,
    headers: buildUpstreamHeaders(request),
    body: request.method === "POST" ? request.body : null,
    redirect: "manual",
  });
  let upstream = await fetch(upstreamRequest);

  for (let redirectCount = 0; redirectCount < MAX_REDIRECTS; redirectCount += 1) {
    if (upstream.status < 300 || upstream.status >= 400) {
      break;
    }
    const location = upstream.headers.get("Location");
    const redirectedTarget = location
      ? parseApprovedTarget(new URL(location, currentTarget).toString(), request.method)
      : null;
    await upstream.body?.cancel();
    if (request.method !== "GET" || redirectedTarget === null) {
      return jsonResponse({ error: "Upstream redirect is not allowed" }, 502);
    }
    currentTarget = redirectedTarget;
    upstreamRequest = new Request(currentTarget, {
      method: "GET",
      headers: buildUpstreamHeaders(request),
      redirect: "manual",
    });
    upstream = await fetch(upstreamRequest);
  }

  if (upstream.status >= 300 && upstream.status < 400) {
    await upstream.body?.cancel();
    return jsonResponse({ error: "Upstream redirect limit exceeded" }, 502);
  }
  const finalUrl = parseApprovedTarget(
    upstream.url || currentTarget.toString(),
    upstreamRequest.method,
  );
  if (finalUrl === null) {
    await upstream.body?.cancel();
    return jsonResponse({ error: "Upstream redirected outside the allowlist" }, 502);
  }

  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.set("Cache-Control", "no-store");
  responseHeaders.set("X-Content-Type-Options", "nosniff");
  responseHeaders.set("X-Vietlott-Source-Url", finalUrl.toString());
  responseHeaders.delete("Set-Cookie");

  console.log(
    JSON.stringify({
      message: "proxied Vietlott request",
      method: request.method,
      host: finalUrl.hostname,
      path: finalUrl.pathname,
      status: upstream.status,
    }),
  );

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
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

    try {
      return await proxyRequest(request, target);
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "Vietlott relay request failed",
          error: error instanceof Error ? error.message : String(error),
          host: target.hostname,
          path: target.pathname,
        }),
      );
      return jsonResponse({ error: "Upstream request failed" }, 502);
    }
  },
} satisfies ExportedHandler<Cloudflare.Env>;
