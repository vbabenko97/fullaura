import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // The minifier drops license comments, so the notices of bundled libraries go in their own file.
  build: {
    license: { fileName: "assets/third-party-licenses.md" },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
