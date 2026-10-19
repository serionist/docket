// @ts-check
/**
 * The first page's script stays under 250 KB gzipped (DR-0712): app.js and every chunk it imports statically,
 * read from the committed build. The web job runs it after the build.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

export const BUDGET = 250 * 1024;
const ENTRY = "app.js";

/**
 * The entry and the chunks it imports statically, as file names in the build folder.
 * @param {string} folder
 * @returns {string[]}
 */
export function firstLoad(folder) {
  const seen = new Set([ENTRY]);
  const queue = [ENTRY];
  while (queue.length) {
    const file = /** @type {string} */ (queue.shift());
    const code = readFileSync(path.join(folder, file), "utf8");
    for (const found of code.matchAll(/(?:^|[;\s])import\s*(?:[^"'()]*?from\s*)?["']\.\/([\w.-]+\.js)["']/g)) {
      const next = found[1] ?? "";
      if (!seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return [...seen];
}

/**
 * @param {string} folder
 * @returns {number}
 */
export function gzipped(folder) {
  return firstLoad(folder).reduce((sum, file) => sum + gzipSync(readFileSync(path.join(folder, file))).length, 0);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const folder = path.resolve(import.meta.dirname, "../../src/docket/web/app");
  const size = gzipped(folder);
  const line = `the first page's script: ${(size / 1024).toFixed(1)} KB gzipped of ${BUDGET / 1024} KB`;
  if (size > BUDGET) {
    console.error(`${line}: over the budget`);
    process.exit(1);
  }
  console.log(line);
}
