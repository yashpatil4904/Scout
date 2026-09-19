import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/sessions": "http://127.0.0.1:8787",
      "/agent.py": "http://127.0.0.1:8787",
      "/health": "http://127.0.0.1:8787",
    },
  },
});
