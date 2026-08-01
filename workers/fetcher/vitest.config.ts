import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: {
        configPath: "./workers/fetcher/wrangler.jsonc",
      },
    }),
  ],
  test: {
    include: ["workers/fetcher/test/**/*.spec.ts"],
  },
});
