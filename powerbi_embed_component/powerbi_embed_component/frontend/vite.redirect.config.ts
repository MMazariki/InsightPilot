import { defineConfig } from "vite";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
    base: "./",
    build: {
        outDir: "redirect-build",
        emptyOutDir: true,
        rollupOptions: {
            input: {
                auth: resolve(frontendDir, "auth.html"),
            },
        },
    },
});
