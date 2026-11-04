// A piece being prepared, tapped on the phone (web/preparing.js): it plays once it can, as it is made, and before
// that the tap says it is still being prepared; and the words for it (web/strings.js), in both languages.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/pianasoul/web/preparing.js', 'utf8').replace(/^export /gm, '');
const {tapPreparing} = vm.runInNewContext(source + '\n({tapPreparing})', {});
const plain = value => JSON.parse(JSON.stringify(value));  // made in the page's own context: compared as plain values

function beforeItCanPlayATapSaysItIsStillBeingPrepared() {
  for (const status of ['queued', 'downloading', 'converting']) {
    assert.equal(tapPreparing({id: 1, status, recording: null}, [3, 2]), null, `Nothing to play yet: ${status}`);
  }
}

function onceItCanPlayATapPlaysItFromTheLibrarysListFirst() {
  assert.deepEqual(plain(tapPreparing({id: 1, status: 'converting', recording: 7}, [3, 2])), {id: 7, ids: [7, 3, 2]},
                   'Where it goes once made: the newest piece, first in the library');
  assert.deepEqual(plain(tapPreparing({id: 1, status: 'converting', recording: 7}, [7, 3])), {id: 7, ids: [7, 3]},
                   'Once only');
  assert.deepEqual(plain(tapPreparing({id: 1, status: 'converting', recording: 7}, [])), {id: 7, ids: [7]});
}

function theWordsForItInBothLanguages() {
  const sandbox = {};
  const strings = fs.readFileSync('src/pianasoul/web/strings.js', 'utf8').replace(/^export const LANGUAGES/m, 'this.LANGUAGES');
  vm.runInNewContext(strings, sandbox);
  const {zh, en} = sandbox.LANGUAGES;
  assert.equal(zh.stillPreparing, '仍在准备，稍后即可播放。');
  assert.equal(en.stillPreparing, 'Still preparing. It’ll be ready to play shortly.');
  assert.ok(!/\p{Script=Han}/u.test(en.stillPreparing), 'The English words are all English');
}

beforeItCanPlayATapSaysItIsStillBeingPrepared();
onceItCanPlayATapPlaysItFromTheLibrarysListFirst();
theWordsForItInBothLanguages();
console.log('Preparing passed: a tap says it is still being prepared until it can play, then plays it first in the ' +
            "library's list; and its words.");
