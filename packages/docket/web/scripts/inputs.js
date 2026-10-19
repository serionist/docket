// @ts-check
/**
 * The hash of what the build reads (DR-0705): build-inputs.txt names git pathspecs from this folder; the tracked
 * files they match, in path order, each as its path, its length and its bytes with CRLF made LF. The gate computes
 * the same hash in Python (tools/devkit/src/devkit/web.py) and compares it with build.json's, so a source changed
 * since the last build fails before a push, with no Node.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

export const LIST = "build-inputs.txt";
const HOOK_VARIABLES = ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"];

/**
 * The pathspecs build-inputs.txt names: one a line, blank lines and # comments left out.
 * @param {string} text
 * @returns {string[]}
 */
export function pathspecs(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
}

/**
 * The tracked files the pathspecs match, relative to `root`, sorted by their UTF-8 bytes.
 * @param {string} root
 * @returns {string[]}
 */
export function inputFiles(root) {
  const specs = pathspecs(readFileSync(path.join(root, LIST), "utf8"));
  // A git hook's variables would make git take this folder for the work tree's top; git finds the repo itself.
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !HOOK_VARIABLES.includes(name)));
  const out = execFileSync("git", ["ls-files", "-z", "--", ...specs], { cwd: root, encoding: "utf8", env });
  const files = out.split("\0").filter(Boolean);
  return files.sort((a, b) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")));
}

/** @param {Buffer} data */
function lf(data) {
  const out = [];
  for (let i = 0; i < data.length; i++) {
    if (data[i] === 0x0d && data[i + 1] === 0x0a) continue;
    out.push(data[i]);
  }
  return Buffer.from(out);
}

/**
 * The inputs' hash, as build.json records it.
 * @param {string} root
 * @returns {string}
 */
export function inputsHash(root) {
  const hash = createHash("sha256");
  for (const file of inputFiles(root)) {
    const data = lf(readFileSync(path.join(root, file)));
    hash.update(Buffer.from(`${file}\n${data.length}\n`, "utf8"));
    hash.update(data);
  }
  return hash.digest("hex");
}
