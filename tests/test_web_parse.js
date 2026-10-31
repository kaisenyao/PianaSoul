// Every script of the page parses as what the browser loads it as, a module: a name declared twice, or any other
// such error, stops the whole page before anything shows. (node --check alone, given a file, misses these.)
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const web = path.join(__dirname, '..', 'src', 'pianasoul', 'web');
const scripts = fs.readdirSync(web).filter(name => name.endsWith('.js'));
assert.ok(scripts.includes('app.js'));
for (const name of scripts) {
  const checked = spawnSync(process.execPath, ['--input-type=module', '--check'], {input: fs.readFileSync(`${web}/${name}`)});
  assert.equal(checked.status, 0, `${name}: ${checked.stderr}`);
}
console.log(`ok: ${scripts.length} scripts parse as modules`);
