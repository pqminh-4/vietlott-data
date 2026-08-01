export const INTERNAL_TARGET_HEADER = "X-Vietlott-Target";

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

export const MAX_DECLARED_BODY_BYTES = 256 * 1024;

export function jsonResponse(
  body: Record<string, unknown>,
  status: number,
): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export function hasOversizedDeclaredBody(request: Request): boolean {
  const rawContentLength = request.headers.get("Content-Length");
  if (rawContentLength === null) {
    return false;
  }
  const contentLength = Number(rawContentLength);
  return Number.isFinite(contentLength) && contentLength > MAX_DECLARED_BODY_BYTES;
}

export function parseApprovedTarget(
  rawTarget: string | null,
  method: string,
): URL | null {
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

export function buildVietlottHeaders(request: Request): Headers {
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
