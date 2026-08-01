import { afterEach, describe, expect, it, vi } from "vitest";

import fetcher from "../src/index";

const IncomingRequest = Request<unknown, IncomingRequestCfProperties>;
type WorkerRequest = Request<unknown, IncomingRequestCfProperties>;

function fetcherRequest(
  target: string,
  init: { body?: BodyInit | null; headers?: HeadersInit; method?: string } = {},
): WorkerRequest {
  const headers = new Headers(init.headers);
  headers.set("X-Vietlott-Target", target);
  return new IncomingRequest("https://vietlott-fetcher.internal/proxy", {
    ...init,
    headers,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("private Vietlott fetcher", () => {
  it("rejects requests without an approved internal target", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const response = await fetcher.fetch(
      new IncomingRequest("https://vietlott-fetcher.internal/proxy"),
    );
    expect(response.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
    await response.text();
  });

  it("revalidates the target before fetching", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const response = await fetcher.fetch(fetcherRequest("https://example.com/private"));
    expect(response.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
    await response.text();
  });

  it("fetches Vietlott without forwarding the internal routing header", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const upstream = input instanceof Request ? input : new Request(input);
      expect(upstream.url).toBe(
        "https://www.vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Result.ashx",
      );
      expect(upstream.method).toBe("POST");
      expect(upstream.headers.get("X-Vietlott-Target")).toBeNull();
      expect(upstream.headers.get("X-AjaxPro-Method")).toBe("ServerSideDrawResult");
      expect(await upstream.text()).toBe('{"PageIndex":0}');
      return new Response('{"value":{"HtmlContent":"<table></table>"}}', {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Set-Cookie": "not-forwarded=true",
        },
      });
    });

    const target =
      "https://www.vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Result.ashx";
    const response = await fetcher.fetch(
      fetcherRequest(target, {
        method: "POST",
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "X-AjaxPro-Method": "ServerSideDrawResult",
        },
        body: '{"PageIndex":0}',
      }),
    );

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(response.status).toBe(200);
    expect(response.headers.get("Set-Cookie")).toBeNull();
    expect(response.headers.get("X-Vietlott-Source-Url")).toBe(target);
    expect(await response.text()).toContain("HtmlContent");
  });
});
