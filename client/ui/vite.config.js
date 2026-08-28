import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import { readFileSync } from "node:fs";

function pkgVersion(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8")).version ?? "unknown";
  } catch {
    return "unknown";
  }
}

export default defineConfig({
  base: "./", // relative paths so the build works mounted at /client/
  plugins: [react()],
  define: {
    __PIPECAT_CLIENT_JS_VERSION__: JSON.stringify(pkgVersion("node_modules/@pipecat-ai/client-js/package.json")),
    __UI_VERSION__: JSON.stringify(pkgVersion("package.json")),
  },
  publicDir: "public",
  server: {
    allowedHosts: true,
    proxy: {
      // `npm run dev` against a locally running proxy.py
      "/start": { target: "http://127.0.0.1:7861", changeOrigin: true },
    },
  },
});
