// Prints the app ids in APPS (src/index.js) as a JSON array, for check-app-keys.sh.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'index.js'), 'utf8');
const block = source.match(/const APPS = \{([\s\S]*?)\n\};/);
if (!block) {
  console.error('Could not find APPS in src/index.js');
  process.exit(1);
}
const ids = [...block[1].matchAll(/^\s+([a-z0-9]+): \{/gm)].map((m) => m[1]);
if (ids.length === 0) {
  console.error('APPS in src/index.js has no entries');
  process.exit(1);
}
console.log(JSON.stringify(ids));
