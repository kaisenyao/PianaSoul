// Uploading from the phone (web/upload.js): one file at a time, each with how far it has got and how it ended.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/pianasoul/web/upload.js', 'utf8').replace(/^export /gm, '');

function harness() {
  const h = {requests: [], seen: []};
  class FakeXHR {
    constructor() { this.upload = {}; h.requests.push(this); }
    open(method, url) { Object.assign(this, {method, url}); }
    send(body) { this.body = body; }
    answer(status, json) { this.status = status; this.responseText = JSON.stringify(json); this.onload(); }
  }
  const sandbox = {encodeURIComponent, JSON};
  vm.runInNewContext(source + '\nthis.uploader = uploader;', sandbox);
  h.up = sandbox.uploader(items => h.seen.push(items.map(i => `${i.name}:${i.status}`).join(' ')), FakeXHR);
  return h;
}
const file = (name, size = 100) => ({name, size});
const plain = value => JSON.parse(JSON.stringify(value));  // made in the page's own context: compared as plain values

(function oneAtATimeWithProgress() {
  const h = harness();
  h.up.add([file('晴天.mid'), file('Clair de Lune.mp3', 1000)]);
  assert.equal(h.requests.length, 1, 'One file at a time');
  const [first] = h.requests;
  assert.equal(first.method, 'POST');
  assert.equal(first.url, '/api/uploads?name=%E6%99%B4%E5%A4%A9.mid', 'The name, in the address');
  assert.equal(first.body.name, '晴天.mid', 'The file itself is the body');
  first.upload.onprogress({loaded: 40});
  assert.equal(h.up.items[0].sent, 40);
  first.answer(200, {name: '晴天 (2).mid'});
  assert.equal(h.up.items[0].status, 'done');
  assert.equal(h.up.items[0].saved, '晴天 (2).mid', 'The name it has on the Mac');
  assert.equal(h.requests.length, 2, 'Then the next');
  assert.equal(h.up.items[1].status, 'sending');
})();

(function failuresSayWhyAndTheRestGoOn() {
  const h = harness();
  h.up.add([file('notes.txt'), file('huge.wav'), file('cut.mid'), file('fine.mid')]);
  h.requests[0].answer(415, {error: 'type'});
  h.requests[1].answer(413, {error: 'too_large'});
  h.requests[2].onerror();  // the phone lost the network
  h.requests[3].answer(200, {name: 'fine.mid'});
  assert.deepEqual(plain(h.up.items.map(i => [i.status, i.error])),
                   [['failed', 'type'], ['failed', 'too_large'], ['failed', 'other'], ['done', '']]);
  assert.equal(h.seen.at(-1), 'notes.txt:failed huge.wav:failed cut.mid:failed fine.mid:done');
})();

(function chosenWhileSendingWaitTheirTurn() {
  const h = harness();
  h.up.add([file('a.mid')]);
  h.up.add([file('b.mid')]);
  assert.equal(h.requests.length, 1);
  assert.equal(h.up.items[1].status, 'waiting');
  h.requests[0].answer(200, {name: 'a.mid'});
  assert.equal(h.requests.length, 2);
  h.up.tidy();
  assert.deepEqual(plain(h.up.items.map(i => i.name)), ['b.mid'], 'Tidying leaves what is still on its way');
})();

console.log('upload ok');
