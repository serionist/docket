// lib/passlog.mjs: writes each journey file's `### Shots` block for a pass, from the manifest. Code writes shot
// references, never an agent. Idempotent (the same manifest gives the same bytes) and atomic.
//
//   ## Pass p1
//   <!-- passlog:p1:start --> ... <!-- passlog:p1:end -->
//
// From a part:  await logJourneyPass(config, 'J-07')   (lib/part.mjs does it at each journey's end)
// From a shell: node lib/passlog.mjs --run p1 [--journey J-07]   (run from e2e/)

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { writeTextAtomic } from './util.mjs';

function readManifest(e2eRoot, run) {
  const p = resolve(e2eRoot, `manifest.${run}.jsonl`);
  if (!existsSync(p)) return [];
  return readFileSync(p, 'utf8').split('\n').filter(Boolean).flatMap((l) => {
    try {
      return [JSON.parse(l)];
    } catch {
      return [];
    }
  });
}

// Manifest paths are relative to e2e/; links are relative to the journey file, with forward slashes.
const link = (journeyFile, e2eRoot, target) =>
  relative(dirname(journeyFile), isAbsolute(target) ? target : resolve(e2eRoot, target)).split(sep).join('/');

function summary(m) {
  const b = m.budget ?? {};
  const noise = (b.consoleNoise?.length ?? 0) + (b.networkNoise?.length ?? 0) + (b.foreignRequests?.length ?? 0) + (b.vitalsHits?.length ?? 0);
  const bits = [`console: ${(m.consoleErrors ?? []).length}`, noise ? '**budget: hits**' : 'budget: clean'];
  if (m.axe?.length) bits.push(`**axe: ${m.axe.length}**`);
  if (m.hitTargets?.length) bits.push(`**targets: ${m.hitTargets.length}**`);
  if (m.overflow?.length) bits.push(`**overflow: ${m.overflow.length}**`);
  if (m.rawKeys?.length || m.bannedText?.length) bits.push('**copy: hits**');
  if (typeof m.vitals?.cls === 'number') bits.push(`cls ${m.vitals.cls.toFixed(3)}`);
  if (m.flags?.prePaintError) bits.push('**error before input**');
  if (m.flags?.suspect) bits.push('**suspect cell**');
  if (m.flags?.refused) bits.push(`**refused: ${m.flags.refused.length} cells**`);
  return bits.join(' · ');
}

function block(journeyFile, e2eRoot, lines) {
  const out = ['### Shots (written by passlog; do not edit)'];
  const byState = new Map(); // the last line per state wins: a re-drive in the same pass appends
  const skipped = new Set();
  for (const m of lines) {
    const a = String(m.action ?? '');
    if (a.startsWith('capture:')) byState.set(m.state, m);
    if (a.startsWith('capture-skip:')) skipped.add(m.state);
  }
  for (const st of byState.keys()) skipped.delete(st);
  for (const m of byState.values()) {
    const canon = (m.clipShots ?? []).find((s) => s.includes(String(m.flags?.canonicalCombo ?? '\0'))) ?? (m.clipShots ?? [])[0];
    const parts = [`- \`${m.state}\``];
    if (m.flags?.secrets) parts.push('(shows a secret: shots kept in the run folder, not linked)');
    else if (canon) {
      parts.push(`![${m.state}](${link(journeyFile, e2eRoot, canon)})`);
      if (m.shots?.[0]) parts.push(`· [full](${link(journeyFile, e2eRoot, m.shots[0])})`);
      parts.push(`· [all cells](${link(journeyFile, e2eRoot, dirname(canon))}/)`);
    }
    parts.push(`· ${summary(m)}`);
    out.push(parts.join(' '));
  }
  for (const st of skipped) out.push(`- \`${st}\`: **SKIPPED** (signed out, or the state's marker never showed)`);
  const battery = lines.filter((m) => String(m.action ?? '').startsWith('battery:'));
  if (battery.length) out.push(`- battery: ${battery.length} case(s) driven`);
  for (const m of lines.filter((x) => String(x.action ?? '').startsWith('FAILURE:'))) {
    out.push(`- **FAILURE** at \`${m.step}\`: see failures/ (script doctor)`);
  }
  if (out.length === 1) out.push('- (nothing captured for this journey in this pass)');
  return out.join('\n');
}

function upsert(text, run, body) {
  const start = `<!-- passlog:${run}:start -->`;
  const end = `<!-- passlog:${run}:end -->`;
  const wrapped = `${start}\n${body}\n${end}`;
  const i = text.indexOf(start);
  const j = text.indexOf(end);
  if (i >= 0 && j > i) return text.slice(0, i) + wrapped + text.slice(j + end.length);
  const header = `## Pass ${run}`;
  if (text.includes(header)) return text.replace(header, `${header}\n\n${wrapped}`);
  return `${text.replace(/\s*$/, '')}\n\n${header}\n\n${wrapped}\n`;
}

/** Write or refresh one journey's block. Returns the journey file, or null when there is none. */
export function logJourneyPassFrom({ e2eRoot, journeysDir, run, journey }) {
  e2eRoot = resolve(e2eRoot);
  journeysDir = resolve(journeysDir);
  const name = readdirSync(journeysDir).find((f) => f.startsWith(`${journey}-`) && f.endsWith('.md'));
  if (!name) return null;
  const file = resolve(journeysDir, name);
  const lines = readManifest(e2eRoot, run).filter((m) => m.journey === journey);
  writeTextAtomic(file, upsert(readFileSync(file, 'utf8'), run, block(file, e2eRoot, lines)));
  return file;
}

export async function logJourneyPass(config, journey) {
  const e2eRoot = resolve(config.paths?.e2eRoot ?? '.');
  return logJourneyPassFrom({ e2eRoot, journeysDir: resolve(e2eRoot, config.paths?.journeysDir ?? '../journeys'), run: config.run, journey });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const arg = (k) => {
    const i = process.argv.indexOf(`--${k}`);
    return i > -1 ? process.argv[i + 1] : undefined;
  };
  const run = arg('run');
  if (!run) {
    console.error('usage: node lib/passlog.mjs --run p1 [--journey J-07]   (from e2e/)');
    process.exit(2);
  }
  const e2eRoot = resolve('.');
  const journeysDir = resolve(e2eRoot, '../journeys');
  const targets = arg('journey') ? [arg('journey')] : [...new Set(readManifest(e2eRoot, run).map((m) => m.journey).filter(Boolean))];
  const n = targets.filter((journey) => logJourneyPassFrom({ e2eRoot, journeysDir, run, journey })).length;
  console.log(`passlog: ${n} of ${targets.length} journey files updated for ${run}`);
}
