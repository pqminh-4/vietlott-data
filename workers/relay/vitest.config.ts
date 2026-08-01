import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: {
        configPath: "./workers/relay/wrangler.jsonc",
      },
      miniflare: {
        bindings: {
          RELAY_TOKEN: "test-relay-token-with-at-least-32-bytes",
        },
        serviceBindings: {
          UPSTREAM: async (request) => {
            const target = request.headers.get("X-Vietlott-Target");
            return Response.json(
              {
                authorization: request.headers.get("Authorization"),
                body: await request.text(),
                method: request.method,
                target,
                xAjaxProMethod: request.headers.get("X-AjaxPro-Method"),
              },
              {
                headers: target ? { "X-Vietlott-Source-Url": target } : {},
              },
            );
          },
        },
      },
    }),
  ],
  test: {
    include: ["workers/relay/test/**/*.spec.ts"],
  },
});
