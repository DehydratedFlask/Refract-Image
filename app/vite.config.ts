import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server keeps a fixed port because `scripts/dev.sh` starts it and then points the native
// window at it with --dev-url; strictPort means a clash fails loudly instead of moving to 1421 and
// leaving the window showing yesterday's build.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: "../../application/web/dist",
    emptyOutDir: true,
    target: "safari16",
    sourcemap: false,
  },
});
