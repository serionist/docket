// @ts-check
/**
 * Every package the committed build bundles is in its licences.txt, under a licence the build may ship (DR-0705):
 * the build fails on any other, and this holds the committed file to the same list. The web job runs it.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ALLOWED } from "./build.js";

/**
 * The entries licences.txt lists, as [name, version, licence].
 * @param {string} text
 * @returns {[string, string, string][]}
 */
export function listed(text) {
  return [...text.matchAll(/^== (\S+) (\S+) \(([^)]+)\)$/gm)].map((m) => [m[1] ?? "", m[2] ?? "", m[3] ?? ""]);
}

/**
 * What doesn't fit: an entry under another licence, or a list without the packages the app is made of.
 * @param {string} text
 * @returns {string[]}
 */
export function problems(text) {
  const entries = listed(text);
  const out = entries.filter(([, , licence]) => !ALLOWED.has(licence)).map(([n, v, l]) => `${n} ${v} is ${l}`);
  const names = entries.map(([name]) => name);
  for (const needed of ["react", "react-dom", "shadcn/ui", "tailwindcss", "lucide-react"]) {
    if (!names.includes(needed)) out.push(`licences.txt doesn't list ${needed}`);
  }
  if (!names.some((name) => name.startsWith("@radix-ui/"))) out.push("licences.txt lists no Radix package");
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const file = path.resolve(import.meta.dirname, "../../src/docket/web/app/licences.txt");
  const found = problems(readFileSync(file, "utf8"));
  for (const line of found) console.error(line);
  if (found.length) process.exit(1);
  console.log(`licences.txt: ${listed(readFileSync(file, "utf8")).length} packages, each under a licence that fits`);
}
