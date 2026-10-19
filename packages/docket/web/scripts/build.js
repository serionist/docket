// @ts-check
/**
 * The build's own step (DR-0705): beside what Vite writes, the build folder gets
 *
 * - licences.txt: every package whose code, styles or fonts the build bundles, with its version, licence and licence
 *   text, the build failing on a licence outside ALLOWED (each fits Apache-2.0);
 * - routes.json: the paths the service answers with the app's document, from src/routes.json;
 * - build.json: the hash of the build's inputs (scripts/inputs.js), which the gate checks.
 *
 * It also preloads the main font, so the first paint is in Atkinson Hyperlegible.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { inputsHash } from "./inputs.js";

export const ALLOWED = new Set(["MIT", "ISC", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "OFL-1.1"]);
/** @typedef {{ name: string; version: string; license?: unknown; author?: string | { name?: string } }} Meta */
/** @typedef {{ name: string; version: string; licence: string; text: string }} Entry */

const LICENCE_FILE = /^(licen[cs]e|copying)(\.(md|txt))?$/i;
// The font preloaded: the Latin half of the sans, which every page's first words need.
const PRELOAD = "atkinson-hyperlegible-next-latin-wght-normal.woff2";

/**
 * The package a file under node_modules belongs to, as its folder: `<...>/node_modules/@scope/name`.
 * @param {string} file
 * @returns {string | undefined}
 */
export function packageDir(file) {
  const normal = file.replaceAll("\\", "/");
  const at = normal.lastIndexOf("/node_modules/");
  if (at < 0) return undefined;
  const rest = normal.slice(at + "/node_modules/".length).split("/");
  const parts = rest[0]?.startsWith("@") ? rest.slice(0, 2) : rest.slice(0, 1);
  if (!parts.length || parts.some((/** @type {string} */ p) => !p)) return undefined;
  return `${normal.slice(0, at)}/node_modules/${parts.join("/")}`;
}

// A package that names MIT in its package.json but ships no licence file gets the licence's own words, with its
// author as the holder: MIT asks for the notice to travel with the code.
const MIT = `Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.`;

/** @param {Meta} meta */
function author(meta) {
  const who = meta.author;
  if (typeof who === "string") return who.replace(/\s*[<(].*$/, "");
  if (who && typeof who.name === "string") return who.name;
  return `the authors of ${meta.name}`;
}

/**
 * @param {string} dir
 * @param {Meta} meta
 * @param {string} licence
 * @returns {string | undefined}
 */
function licenceText(dir, meta, licence) {
  const found = readdirSync(dir).find((name) => LICENCE_FILE.test(name));
  if (found) return readFileSync(path.join(dir, found), "utf8").replaceAll("\r\n", "\n").trim();
  if (licence !== "MIT") return undefined;
  const note = "MIT License, as its package.json names it: the package ships no licence file";
  return `${note}\n\nCopyright (c) ${author(meta)}\n\n${MIT}`;
}

/**
 * The packages src/styles imports by name, as files inside them, so packageDir finds each.
 * @param {string} root
 * @returns {string[]}
 */
export function cssImports(root) {
  const styles = path.join(root, "src", "styles");
  const out = [];
  for (const name of readdirSync(styles).filter((n) => n.endsWith(".css"))) {
    for (const found of readFileSync(path.join(styles, name), "utf8").matchAll(/@import\s+"([^".][^"]*)"/g)) {
      const spec = found[1] ?? "";
      const parts = spec.split("/");
      const pkg = spec.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
      out.push(path.join(root, "node_modules", pkg ?? "", "package.json"));
    }
  }
  return out;
}

/**
 * licences.txt's entries: one per package, sorted by name, each failing loudly when its licence doesn't fit.
 * @param {Iterable<string>} dirs
 * @param {Entry[]} extra
 * @returns {Entry[]}
 */
export function licences(dirs, extra) {
  /** @type {Map<string, Entry>} */
  const entries = new Map();
  for (const dir of dirs) {
    /** @type {Meta} */
    const meta = JSON.parse(readFileSync(path.join(dir, "package.json"), "utf8"));
    const licence = typeof meta.license === "string" ? meta.license : "UNKNOWN";
    if (!ALLOWED.has(licence))
      throw new Error(`${meta.name} ${meta.version} is ${licence}, which the build may not bundle`);
    const text = licenceText(dir, meta, licence);
    if (!text) throw new Error(`${meta.name} ${meta.version} ships no licence file to copy into licences.txt`);
    entries.set(meta.name, { name: meta.name, version: meta.version, licence, text });
  }
  for (const entry of extra) entries.set(entry.name, entry);
  return [...entries.values()].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/**
 * @param {Entry[]} entries
 * @returns {string}
 */
export function licencesText(entries) {
  const head =
    "Docket's web app (Apache-2.0) bundles the packages below. Each is listed with its version and licence, then\n" +
    "the licence's text as the package ships it.\n";
  const body = entries.map((e) => `== ${e.name} ${e.version} (${e.licence})\n\n${e.text}\n`);
  return [head, ...body].join("\n");
}

/** @param {{ root: string }} options @returns {import("vite").Plugin} */
export function docketBuild({ root }) {
  return {
    name: "docket-build",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        const font = Object.keys(ctx.bundle ?? {}).find((name) => name.endsWith(PRELOAD));
        if (!font) throw new Error(`the build holds no ${PRELOAD} to preload`);
        return [
          {
            tag: "link",
            attrs: { rel: "preload", href: `/static/app/${font}`, as: "font", type: "font/woff2", crossorigin: "" },
            injectTo: "head",
          },
        ];
      },
    },
    generateBundle(_options, bundle) {
      // The scripts' modules, the fonts' files and the packages the stylesheets import, which Tailwind inlines
      // without making them modules of the bundle.
      const files = new Set(cssImports(root));
      for (const output of Object.values(bundle)) {
        if (output.type === "chunk") for (const id of output.moduleIds) files.add(id);
        else for (const name of output.originalFileNames ?? []) files.add(path.resolve(root, name));
      }
      /** @type {Set<string>} */
      const dirs = new Set();
      for (const file of files) {
        const dir = packageDir(file.replace(/^\0/, "").split("?")[0] ?? "");
        if (dir && existsSync(path.join(dir, "package.json"))) dirs.add(dir);
      }
      const shadcn = {
        name: "shadcn/ui",
        version: "4.21.4",
        licence: "MIT",
        text: readFileSync(path.join(root, "src/components/ui/LICENSE.md"), "utf8").replaceAll("\r\n", "\n").trim(),
      };
      this.emitFile({ type: "asset", fileName: "licences.txt", source: licencesText(licences(dirs, [shadcn])) });
      const routes = JSON.parse(readFileSync(path.join(root, "src/routes.json"), "utf8"));
      this.emitFile({ type: "asset", fileName: "routes.json", source: `${JSON.stringify(routes, null, 2)}\n` });
      const stamp = { inputs: inputsHash(root) };
      this.emitFile({ type: "asset", fileName: "build.json", source: `${JSON.stringify(stamp, null, 2)}\n` });
    },
  };
}
