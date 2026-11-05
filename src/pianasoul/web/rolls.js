// The artwork is the piece itself: its notes, scrolling past as the piano plays them. Of a piece being made, those
// there so far (ready: how much of it is there, Infinity once all of it is), asked for again as more comes, and once
// it is made. Kept for the piece as it was added: its number may be given to the next new piece, as when one being
// made is not made after all, or one is deleted.
export function pieceNotes(ask) {
  const rolls = new Map();
  return (p, ready) => {
    const key = `${p.id} ${p.added_at}`, roll = rolls.get(key);
    const more = roll && !roll.asking && roll.ready < ready && (ready === Infinity || ready - roll.ready >= 8);  // a part or so
    if (!rolls.has(key) || more) {
      if (roll) roll.asking = true;
      else rolls.set(key, null);
      ask(p.id).then(notes => {
        let lo = 60, hi = 72;
        for (const n of notes) { lo = Math.min(lo, n[2]); hi = Math.max(hi, n[2]); }
        rolls.set(key, {notes, lo: lo - 3, hi: hi + 3, ready});
      }).catch(() => setTimeout(() => {  // asked again in a while, not every frame; what it has stays meanwhile
        if (roll) roll.asking = false;
        else rolls.delete(key);
      }, 10000));
    }
    return rolls.get(key);
  };
}
