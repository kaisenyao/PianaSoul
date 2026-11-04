// A piece's notes for its artwork (web/rolls.js): asked for once, of one being made again as more of it comes and
// once it is made, and a new piece given the number of one gone has its own.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/pianasoul/web/rolls.js', 'utf8').replace(/^export /gm, '');
const {pieceNotes} = vm.runInNewContext(source + '\n({pieceNotes})', {setTimeout});
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

// The Mac, answering each piece's notes with those it has of it now.
function harness() {
  const h = {asked: [], notes: {}};
  h.notesFor = pieceNotes(id => { h.asked.push(id); return Promise.resolve(h.notes[id]); });
  return h;
}
const A = {id: 5, added_at: '2026-11-04T10:00:00+00:00'}, B = {id: 5, added_at: '2026-11-04T10:01:30+00:00'};
const pitches = roll => roll.notes.map(n => n[2]);

async function ofAPieceBeingMadeItsNotesAreAskedForAgainAsMoreComesAndOnceItIsMade() {
  const h = harness();
  h.notes[5] = [[0, 1, 60, 80]];
  assert.equal(h.notesFor(A, 10), null, 'On its way');
  await tick();
  assert.deepEqual(pitches(h.notesFor(A, 15)), [60]);
  assert.deepEqual(h.asked, [5], 'Not again for less than a part or so more');
  h.notes[5] = [[0, 1, 60, 80], [12, 1, 64, 80]];
  h.notesFor(A, 18);
  await tick();
  assert.deepEqual(pitches(h.notesFor(A, 18)), [60, 64]);
  h.notesFor(A, Infinity);
  await tick();
  assert.deepEqual(h.asked, [5, 5, 5], 'And once it is made');
  h.notesFor(A, Infinity);
  assert.deepEqual(h.asked, [5, 5, 5], 'Then never again');
}

async function aNewPieceGivenTheNumberOfOneGoneHasItsOwnNotes() {
  const h = harness();
  h.notes[5] = [[0, 1, 60, 80]];
  h.notesFor(A, 120);  // well ahead of where it plays, as the transcription runs
  await tick();
  h.notes[5] = [[0, 1, 72, 80]];  // A not made after all, the next new piece has its number
  h.notesFor(B, 3);
  await tick();
  assert.deepEqual(pitches(h.notesFor(B, 3)), [72], 'Its own, not those of the piece that had its number');
}

(async () => {
  await ofAPieceBeingMadeItsNotesAreAskedForAgainAsMoreComesAndOnceItIsMade();
  await aNewPieceGivenTheNumberOfOneGoneHasItsOwnNotes();
  console.log("Rolls passed: a piece's notes asked for once, again as more of one being made comes and once it is " +
              'made, and a new piece given the number of one gone has its own.');
})().catch(error => { console.error(error); process.exitCode = 1; });
