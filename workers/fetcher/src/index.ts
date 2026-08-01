import {
  buildVietlottHeaders,
  hasOversizedDeclaredBody,
  INTERNAL_TARGET_HEADER,
  jsonResponse,
  parseApprovedTarget,
} from "../../shared/proxy";

const MAX_REDIRECTS = 3;

async function proxyRequest(request: Request, target: URL): Promise<Response> {
  let currentTarget = target;
  let upstreamRequest = new Request(currentTarget, {
    method: request.method,
    headers: buildVietlottHeaders(request),
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
      headers: buildVietlottHeaders(request),
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
  async fetch(request): Promise<Response> {
    const requestUrl = new URL(request.url);
    if (requestUrl.pathname !== "/proxy") {
      return jsonResponse({ error: "Not found" }, 404);
    }
    if (request.method !== "GET" && request.method !== "POST") {
      return jsonResponse({ error: "Method not allowed" }, 405);
    }
    const target = parseApprovedTarget(
      request.headers.get(INTERNAL_TARGET_HEADER),
      request.method,
    );
    if (target === null) {
      return jsonResponse({ error: "Target is not allowed" }, 400);
    }
    if (hasOversizedDeclaredBody(request)) {
      return jsonResponse({ error: "Request body is too large" }, 413);
    }

    try {
      return await proxyRequest(request, target);
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "Vietlott fetcher request failed",
          error: error instanceof Error ? error.message : String(error),
          host: target.hostname,
          path: target.pathname,
        }),
      );
      return jsonResponse({ error: "Upstream request failed" }, 502);
    }
  },
} satisfies ExportedHandler<Cloudflare.Env>;
