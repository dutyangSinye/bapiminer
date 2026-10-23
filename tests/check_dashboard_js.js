// Syntax-check inline <script> of dashboard index.html
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.log('NO SCRIPT FOUND'); process.exit(1); }
try {
  new Function(m[1]);
  console.log('JS SYNTAX OK');
} catch (e) {
  console.log('JS SYNTAX FAIL:', e.message);
  process.exit(1);
}
