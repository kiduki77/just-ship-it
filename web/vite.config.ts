import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API refuses a change whose Origin is not the Host it received. The dev proxy forwards the target's Host, so the
// browser's Origin (this dev server) must follow it. Dev only: a built console is served by the API itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        configure: (proxy) => {
          proxy.on("proxyReq", (req) => {
            if (req.getHeader("origin")) req.setHeader("origin", `http://${req.getHeader("host")}`);
          });
        },
      },
    },
  },
});
