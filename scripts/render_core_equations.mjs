// Render the review document to a self-contained LaTeX source and an HTML
// print master. The PDF is printed from the latter with local KaTeX assets.
// Run with: node scripts/render_core_equations.mjs [document-stem]
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

let marked;
try {
  ({ marked } = await import('marked'));
} catch {
  const bundledMarked = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/marked/lib/marked.esm.js');
  ({ marked } = await import(`file://${bundledMarked}`));
}

let katex;
let cssPath;
try {
  katex = await import('katex');
  cssPath = fileURLToPath(import.meta.resolve('katex/dist/katex.min.css'));
} catch {
  const vscodeRoot = '/Applications/Visual Studio Code.app/Contents/Resources/app/extensions';
  const katexModule = path.join(vscodeRoot, 'markdown-language-features/markdown-editor-out/katex-QJ7O5CJ5.js');
  katex = await import(`file://${katexModule}`);
  cssPath = path.join(vscodeRoot, 'markdown-math/notebook-out/katex.min.css');
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const stem = process.argv[2] ?? 'core-equations';
if (!/^[a-z0-9-]+$/.test(stem)) throw new Error('Document stem must use lowercase letters, digits and hyphens.');
const markdownPath = path.join(root, `docs/${stem}.md`);
const texPath = path.join(root, `docs/${stem}.tex`);
const htmlPath = path.join(root, `tmp/pdfs/${stem}.html`);
const markdown = fs.readFileSync(markdownPath, 'utf8');

// Extract whole-block display math. This avoids a Markdown renderer treating
// TeX syntax such as underscores and backslashes as Markdown punctuation.
const math = [];
const placeholderMarkdown = markdown.replace(/\$\$\s*([\s\S]*?)\s*\$\$/g, (_all, expression) => {
  const index = math.push(expression.trim()) - 1;
  return `\n\n@@MATH${index}@@\n\n`;
});

for (const [index, expression] of math.entries()) {
  try {
    katex.renderToString(expression, { displayMode: true, throwOnError: true, strict: 'ignore' });
  } catch (error) {
    throw new Error(`Display equation ${index + 1} failed KaTeX validation: ${error.message}`);
  }
}

const tokens = marked.lexer(placeholderMarkdown, { gfm: true });
const title = tokens.find((token) => token.type === 'heading' && token.depth === 1)?.text ?? '핵심 방정식';

function escapeTeX(value) {
  return String(value)
    .replace(/\\/g, '\\textbackslash{}')
    .replace(/([{}$&#%_])/g, '\\$1')
    .replace(/~/g, '\\textasciitilde{}')
    .replace(/\^/g, '\\textasciicircum{}');
}

function inlineTeX(inlineTokens = []) {
  return inlineTokens.map((token) => {
    switch (token.type) {
      case 'text': return token.tokens ? inlineTeX(token.tokens) : escapeTeX(token.text);
      case 'strong': return `\\textbf{${inlineTeX(token.tokens)}}`;
      case 'em': return `\\emph{${inlineTeX(token.tokens)}}`;
      case 'codespan': return `\\texttt{${escapeTeX(token.text)}}`;
      case 'link': return `\\href{\\detokenize{${token.href}}}{${inlineTeX(token.tokens)}}`;
      case 'br': return '\\\\';
      case 'escape': return escapeTeX(token.text);
      default: return escapeTeX(token.text ?? token.raw ?? '');
    }
  }).join('');
}

function textTeX(text) {
  return inlineTeX(marked.Lexer.lexInline(text, tokens.links));
}

function blockTeX(blocks, nested = false) {
  const out = [];
  for (const token of blocks) {
    if (token.type === 'space' || token.type === 'def') continue;
    if (token.type === 'heading') {
      if (token.depth === 1 && !nested) continue;
      const command = token.depth === 2 ? 'section' : token.depth === 3 ? 'subsection' : 'paragraph';
      out.push(`\\${command}{${textTeX(token.text)}}\n`);
    } else if (token.type === 'paragraph' || token.type === 'text') {
      const match = /^@@MATH(\d+)@@$/.exec(token.text.trim());
      if (match) out.push(`\\begin{equation*}\n${math[Number(match[1])]}\n\\end{equation*}\n`);
      else out.push(`${inlineTeX(token.tokens ?? marked.Lexer.lexInline(token.text, tokens.links))}\n`);
    } else if (token.type === 'list') {
      out.push(`\\begin{${token.ordered ? 'enumerate' : 'itemize'}}`);
      for (const item of token.items) {
        out.push('\\item ' + blockTeX(item.tokens, true).trim());
      }
      out.push(`\\end{${token.ordered ? 'enumerate' : 'itemize'}}\n`);
    } else if (token.type === 'table') {
      // A description layout lets the Korean prose wrap naturally on A4.
      // Every source row and every named column is retained.
      out.push('\\begin{description}[leftmargin=0pt,style=nextline]');
      for (const row of token.rows) {
        const first = inlineTeX(row[0].tokens);
        const rest = row.slice(1).map((cell, index) =>
          `\\textbf{${inlineTeX(token.header[index + 1].tokens)}:} ${inlineTeX(cell.tokens)}`,
        ).join('; ');
        out.push(`\\item[${first}] ${rest}`);
      }
      out.push('\\end{description}\n');
    } else if (token.type === 'blockquote') {
      out.push(`\\begin{quote}\n${blockTeX(token.tokens, true)}\\end{quote}\n`);
    } else if (token.type === 'hr') {
      out.push('\\par\\noindent\\rule{\\linewidth}{0.4pt}\n');
    } else if (token.type === 'code') {
      out.push(`\\begin{verbatim}\n${token.text}\n\\end{verbatim}\n`);
    } else {
      throw new Error(`Unsupported Markdown block: ${token.type}`);
    }
  }
  return out.join('\n');
}

const tex = String.raw`% Generated from docs/${stem}.md. Compile with XeLaTeX and kotex.
\documentclass[11pt,a4paper]{article}
\usepackage{kotex}
\usepackage{amsmath,amssymb}
\usepackage[a4paper,margin=19mm]{geometry}
\usepackage{enumitem}
\usepackage{xurl}
\usepackage[colorlinks=true,linkcolor=blue,urlcolor=blue]{hyperref}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.55em}
\title{${escapeTeX(title)}}
\date{}
\begin{document}
\maketitle
${blockTeX(tokens)}
\end{document}
`;

// Check source completeness without a local TeX distribution.
for (const expression of math) {
  if (!tex.includes(expression)) throw new Error(`LaTeX export lost expression: ${expression.slice(0, 50)}`);
}
fs.writeFileSync(texPath, tex);

let body = marked.parse(placeholderMarkdown, { gfm: true });
body = body.replace(/<p>@@MATH(\d+)@@<\/p>/g, (_all, number) => {
  const rendered = katex.renderToString(math[Number(number)], {
    displayMode: true,
    throwOnError: true,
    strict: 'ignore',
    output: 'htmlAndMathml',
  });
  return `<div class="equation">${rendered}</div>`;
});
if (/@@MATH\d+@@/.test(body)) throw new Error('Unrendered math placeholder remains in HTML.');

const sourceLinks = Object.entries(tokens.links).filter(([_key, link]) => link && typeof link.href === 'string');
const escapeHTML = (value) => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const references = sourceLinks.map(([key, link]) =>
  `<li><strong>${escapeHTML(link.title || key.toUpperCase())}</strong> - <a href="${escapeHTML(link.href)}">${escapeHTML(link.href)}</a></li>`,
).join('\n');
const html = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>${title}</title>
<link rel="stylesheet" href="file://${cssPath}">
<style>
@page { size: A4; margin: 16mm 17mm 18mm 17mm; @bottom-center { content: counter(page); font: 9pt Arial, sans-serif; color: #748095; } }
html { font-family: -apple-system, 'Apple SD Gothic Neo', 'AppleGothic', sans-serif; color: #172334; }
body { margin: 0; font-size: 9.8pt; line-height: 1.48; }
h1 { font-size: 21pt; line-height: 1.25; color: #193d64; margin: 0 0 12mm; border-bottom: 2px solid #376b9b; padding-bottom: 4mm; }
h2 { font-size: 14.2pt; color: #1c527b; margin: 8mm 0 2.7mm; border-bottom: 0.5px solid #bfd0de; padding-bottom: 1.2mm; break-after: avoid; }
h3 { font-size: 11.5pt; color: #315873; margin: 5mm 0 2mm; break-after: avoid; }
p { margin: 0 0 3mm; orphans: 3; widows: 3; }
a { color: #1c5990; text-decoration: none; }
strong { font-weight: 680; }
code { font-family: Menlo, monospace; font-size: .86em; background: #edf1f5; padding: 0 2px; border-radius: 2px; white-space: normal; }
table { border-collapse: collapse; width: 100%; margin: 2.5mm 0 4mm; font-size: 8.3pt; line-height: 1.4; break-inside: avoid; }
thead { display: table-header-group; }
th, td { border-bottom: 0.4px solid #c7d1da; padding: 1.5mm 1.3mm; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #eaf1f7; color: #234b6a; font-weight: 700; }
tr { break-inside: avoid; }
ul, ol { margin: 1.5mm 0 3.5mm; padding-left: 6mm; }
li { margin-bottom: .8mm; }
.equation { margin: 3.5mm 0 4.5mm; padding: 2.8mm 2mm; background: #f5f8fb; border-left: 2px solid #4c83b3; font-size: 10.4pt; break-inside: avoid; }
.equation .katex-display { margin: 0; }
.equation .katex-html { white-space: nowrap; }
.references { margin-top: 3mm; padding-top: 2mm; border-top: 1px solid #9aacbb; font-size: 7.8pt; }
.references h2 { margin: 2mm 0; }
.references a { overflow-wrap: anywhere; }
.references li { margin-bottom: 1mm; }
@media screen { body { max-width: 850px; margin: 24px auto; padding: 0 20px; } }
</style></head><body>
${body}
<div class="references"><h2>논문 원문 링크</h2><ul>${references}</ul></div>
<script>
document.fonts.ready.then(() => {
  for (const wrapper of document.querySelectorAll('.equation')) {
    const inner = wrapper.querySelector('.katex');
    const available = wrapper.clientWidth - 14;
    if (inner && inner.scrollWidth > available) {
      const scale = Math.max(0.65, available / inner.scrollWidth);
      wrapper.style.fontSize = (10.4 * scale).toFixed(2) + 'pt';
    }
  }
  document.body.dataset.ready = 'true';
});
</script>
</body></html>`;
fs.mkdirSync(path.dirname(htmlPath), { recursive: true });
fs.writeFileSync(htmlPath, html);
console.log(JSON.stringify({ latex: texPath, html: htmlPath, mathBlocks: math.length, referenceLinks: sourceLinks.length }));
