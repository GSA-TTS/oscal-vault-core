// Syntax-check Mermaid C4 diagrams with the real Mermaid parser.
//
//   npm i mermaid@12 jsdom      (once, in a scratch directory)
//   node validate.mjs diagram.mmd [notes.md ...]     # .md files: every ```mermaid block
//
// LIMITS (verified against mermaid 12.2.0):
//   mermaid.parse() checks grammar only. It ACCEPTS a Rel() that names an alias which was never
//   declared; only a full browser render rejects that ("references an unknown shape"), and jsdom
//   cannot render. Check aliases with validate_generated_c4() in compliance/lib/topology_eval.py,
//   or render in a real browser. Exit 0 = grammar OK, 1 = grammar error.
import { JSDOM } from 'jsdom';
import fs from 'fs';

const dom = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad: false });

function blocks(file) {
  const text = fs.readFileSync(file, 'utf8');
  if (!file.endsWith('.md')) return [[file, text]];
  return [...text.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m, i) => [`${file}#${i + 1}`, m[1]]);
}

let failed = false;
for (const file of process.argv.slice(2)) {
  for (const [name, code] of blocks(file)) {
    try {
      await mermaid.parse(code);
      console.log('OK  ', name);
    } catch (e) {
      failed = true;
      console.log('FAIL', name, String(e.message || e).split('\n')[0]);
    }
  }
}
process.exit(failed ? 1 : 0);
