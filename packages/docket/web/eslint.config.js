// @ts-check
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["node_modules", "src/api/schema.d.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.strict],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "no-restricted-syntax": [
        "error",
        { selector: "CallExpression[callee.name='eval']", message: "the page's CSP refuses eval (DR-0706)" },
        { selector: "NewExpression[callee.name='Function']", message: "the page's CSP refuses new Function (DR-0706)" },
        {
          selector: "MemberExpression[object.name=/^(localStorage|sessionStorage|indexedDB)$/]",
          message: "the browser keeps only the theme and the keys switch (DR-0706)",
        },
      ],
    },
  },
  {
    files: ["scripts/**/*.js", "eslint.config.js"],
    extends: [js.configs.recommended],
    languageOptions: { ecmaVersion: 2023, sourceType: "module", globals: globals.node },
  },
);
