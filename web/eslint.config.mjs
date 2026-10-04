import next from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...next,
  ...nextTs,
  // explicit React version: eslint-plugin-react's auto detection breaks with ESLint 10 (same as fahrschule-aare)
  { settings: { react: { version: "19.3" } } },
  { ignores: [".next/**", "node_modules/**", "drizzle/**", "next-env.d.ts"] },
];

export default config;
