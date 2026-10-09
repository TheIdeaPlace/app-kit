// Prints the apps in APPS (src/index.js) as JSON.
//   node app-ids.mjs           ["quickmail", ...]                         (for check-app-keys.sh)
//   node app-ids.mjs --repos   {"quickmail": "kellylford/QuickMail", ...} (for setup-github-app.py)
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'index.js'), 'utf8');
const block = source.match(/const APPS = \{([\s\S]*?)\n\};/);
if (!block) {
  console.error('Could not find APPS in src/index.js');
  process.exit(1);
}
const entries = [...block[1].matchAll(/^\s+([a-z0-9]+): \{ owner: '([^']+)', repo: '([^']+)' \}/gm)];
if (entries.length === 0) {
  console.error('APPS in src/index.js has no entries');
  process.exit(1);
}
if (process.argv.includes('--repos')) {
  console.log(JSON.stringify(Object.fromEntries(entries.map(([, id, owner, repo]) => [id, `${owner}/${repo}`]))));
} else {
  console.log(JSON.stringify(entries.map(([, id]) => id)));
}
