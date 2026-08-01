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
      },
    }),
  ],
  test: {
    include: ["workers/relay/test/**/*.spec.ts"],
  },
});
