// Print the local KaTeX master made by render_core_equations.mjs.
// node scripts/print_equations.mjs [document-stem]
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const stem = process.argv[2] ?? 'core-equations';
if (!/^[a-z0-9-]+$/.test(stem)) throw new Error('Invalid document stem.');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch {
  const bundled = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
  ({ chromium } = await import(pathToFileURL(bundled).href));
}
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await chromium.launch({
  headless: true,
  ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}),
});
try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(path.join(root, `tmp/pdfs/${stem}.html`)).href);
  await page.waitForFunction(() => document.body.dataset.ready === 'true');
  await page.emulateMedia({ media: 'print' });
  const issues = await page.evaluate(() => ({
    katexErrors: document.querySelectorAll('.katex-error').length,
    equations: document.querySelectorAll('.equation').length,
    overflow: [...document.querySelectorAll('.equation')].filter(e => e.scrollWidth > e.clientWidth + 2).length,
  }));
  if (issues.katexErrors || issues.overflow) throw new Error(JSON.stringify(issues));
  const output = path.join(root, `output/pdf/${stem}.pdf`);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  await page.pdf({ path: output, preferCSSPageSize: true, printBackground: true });
  console.log(JSON.stringify({ output, ...issues }));
} finally {
  await browser.close();
}
