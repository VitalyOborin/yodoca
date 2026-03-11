import js from "@eslint/js";
import tseslint from "typescript-eslint";
import pluginVue from "eslint-plugin-vue";
import eslintConfigPrettier from "eslint-config-prettier";
import globals from "globals";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  ...pluginVue.configs["flat/recommended"],
  eslintConfigPrettier,
  {
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
  },
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },
  {
    rules: {
      "vue/multi-word-component-names": "off",
    },
  },
  {
    files: ["src/components/ui/**/*.vue"],
    rules: {
      "vue/require-default-prop": "off",
    },
  },
);
