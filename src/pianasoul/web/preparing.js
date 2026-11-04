// A piece being prepared (a job), tapped where it is listed: in the library, and in Add Pieces among the videos found.
// Once it can play as it is made (its job has its record then), the tap plays it from its start, first in the
// library's list, where it goes once it is made: what is there of it plays, and beyond that it waits, as a seek past
// it does. Before then there is nothing to play yet (null), and the tap says it is still being prepared. Its ring
// stops it, as before.
export const tapPreparing = (job, libraryIds) => job.recording == null ? null
  : {id: job.recording, ids: [job.recording, ...libraryIds.filter(id => id !== job.recording)]};
