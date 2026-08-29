import { defineConfig } from "vitest/config"
import react from "@vitejs/plugin-react"
import path from "path"

export default defineConfig({
  plugins: [react()],
  test: {
    // Suite default. Specs that assert on sanitized HTML need a fuller DOM
    // than happy-dom provides and opt into jsdom per file with a
    // `// @vitest-environment jsdom` pragma — see
    // src/lib/__tests__/dom-environment.test.ts.
    environment: "happy-dom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    exclude: ["**/node_modules/**", "**/e2e/**"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
})
