import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, vi } from "vitest";

import worker from "../src/index";

const TOKEN = "test-relay-token-with-at-least-32-bytes";
const IncomingRequest = Request<unknown, IncomingRequestCfProperties>;
type WorkerRequest = Request<unknown, IncomingRequestCfProperties>;

async function dispatch(request: WorkerRequest): Promise<Response> {
  return worker.fetch(request, env);
}

function relayRequest(
  target: string,
  init: { body?: BodyInit | null; headers?: HeadersInit; method?: string } = {},
): WorkerRequest {
  const url = new URL("https://relay.example/proxy");
  url.searchParams.set("url", target);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${TOKEN}`);
  return new IncomingRequest(url, { ...init, headers });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Vietlott relay", () => {
  it("exposes a public health check without proxying", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const response = await dispatch(new IncomingRequest("https://relay.example/health"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      service: "vietlott-official-relay",
      status: "ok",
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("requires the relay secret", async () => {
    const url =
      "https://relay.example/proxy?url=https%3A%2F%2Fwww.vietlott.vn%2Fajaxpro%2F";
    const response = await dispatch(new IncomingRequest(url));
    expect(response.status).toBe(401);
    await response.text();
  });

  it("fails closed when the Worker secret is missing", async () => {
    const request = relayRequest("https://www.vietlott.vn/ajaxpro/");
    const response = await worker.fetch(request, { RELAY_TOKEN: "" });
    expect(response.status).toBe(401);
    await response.text();
  });

  it("rejects non-Vietlott targets before fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const response = await dispatch(relayRequest("https://example.com/private"));
    expect(response.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
    await response.text();
  });

  it("streams an approved AjaxPro request and strips relay authorization", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const upstream = input instanceof Request ? input : new Request(input);
      expect(upstream.url).toBe(
        "https://www.vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Result.ashx",
      );
      expect(upstream.method).toBe("POST");
      expect(upstream.headers.get("Authorization")).toBeNull();
      expect(upstream.headers.get("X-AjaxPro-Method")).toBe("ServerSideDrawResult");
      expect(await upstream.text()).toBe('{"PageIndex":0}');
      return new Response('{"value":{"HtmlContent":"<table></table>"}}', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const response = await dispatch(
      relayRequest(
        "https://www.vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Result.ashx",
        {
          method: "POST",
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "X-AjaxPro-Method": "ServerSideDrawResult",
          },
          body: '{"PageIndex":0}',
        },
      ),
    );

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(response.status).toBe(200);
    expect(response.headers.get("X-Vietlott-Source-Url")).toBe(
      "https://www.vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Result.ashx",
    );
    expect(await response.text()).toContain("HtmlContent");
  });

  it("does not allow POST requests to the media host", async () => {
    const response = await dispatch(
      relayRequest("https://media.vietlott.vn/main/result.pdf", { method: "POST" }),
    );
    expect(response.status).toBe(400);
    await response.text();
  });
});
