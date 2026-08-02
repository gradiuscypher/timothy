import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

// Type-aware linting, because the rules worth having here need types: a floating promise
// in a mutation handler and an `any` leaking out of the generated client are both
// invisible without them. Scoped to TypeScript — this config file is itself JavaScript
// and belongs to no project.
export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts", "node_modules"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.strictTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],

      // `onChange={(e) => setThing(e.target.value)}` is how React is written. The rule is
      // aimed at accidentally returning a value; a JSX handler returns nothing to anyone.
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      // `T[]` for simple types, `Array<T>` for unions — which is what reads better for
      // the cursor stacks these screens keep.
      "@typescript-eslint/array-type": ["error", { default: "array-simple" }],
      // Counts in strings: "3076 listed", "List 2 users".
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
    },
  },
  {
    // Tests build fixtures shaped like the backend's JSON and reach into rendered output
    // by index. Both are the point of a fixture, and neither is a risk in a test.
    files: ["src/test/**"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      // Test helpers are not components and are not hot-reloaded.
      "react-refresh/only-export-components": "off",
      "@typescript-eslint/no-empty-function": "off",
    },
  },
);
