import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    ".next-live-smoke/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored VAD runtime assets are third-party generated JavaScript/WASM.
    "public/vad/**",
    // Separate Next build outputs from test, smoke and audit runs, and the result export's build.
    ".next-*/**",
    "result-export/.next/**",
    "result-export/out/**",
  ]),
  {
    rules: {
      // A leading underscore marks a parameter or variable as intentionally unused.
      "@typescript-eslint/no-unused-vars": ["warn", {
        argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_",
      }],
    },
  },
]);

export default eslintConfig;
