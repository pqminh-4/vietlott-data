import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

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

describe("Vietlott relay", () => {
  it("exposes a public health check", async () => {
    const response = await dispatch(new IncomingRequest("https://relay.example/health"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      service: "vietlott-official-relay",
      status: "ok",
    });
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
    const response = await worker.fetch(request, { ...env, RELAY_TOKEN: "" });
    expect(response.status).toBe(401);
    await response.text();
  });

  it("rejects non-Vietlott targets", async () => {
    const response = await dispatch(relayRequest("https://example.com/private"));
    expect(response.status).toBe(400);
    await response.text();
  });

  it("forwards only an approved request to the private service", async () => {
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

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Vietlott-Source-Url")).toBe(
      "https://www.vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Result.ashx",
    );
    expect(await response.json()).toEqual({
      authorization: null,
      body: '{"PageIndex":0}',
      method: "POST",
      target: "https://www.vietlott.vn/ajaxpro/Vietlott.PlugIn.WebParts.Result.ashx",
      xAjaxProMethod: "ServerSideDrawResult",
    });
  });

  it("does not allow POST requests to the media host", async () => {
    const response = await dispatch(
      relayRequest("https://media.vietlott.vn/main/result.pdf", { method: "POST" }),
    );
    expect(response.status).toBe(400);
    await response.text();
  });
});
