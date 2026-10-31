// Files from the phone into the library folder on the Mac, one at a time, each one request with the file as its body
// (POST /api/uploads?name=…). XMLHttpRequest, not fetch: only it tells how much of a file has gone.
// Each file is an item: waiting, sending (sent of size bytes so far), done (saved: its name on the Mac) or failed
// (error: what the Mac said, such as type or too_large, or other when the upload broke off).
export function uploader(onChange, XHR = XMLHttpRequest) {
  const items = [];
  let sending = false, ids = 0;

  function add(files) {
    for (const file of files) items.push({id: ++ids, file, name: file.name, size: file.size, sent: 0, status: 'waiting', error: ''});
    onChange(items);
    next();
  }

  function next() {
    const item = !sending && items.find(i => i.status === 'waiting');
    if (!item) return;
    sending = true;
    item.status = 'sending';
    onChange(items);
    const xhr = new XHR();
    xhr.open('POST', '/api/uploads?name=' + encodeURIComponent(item.name));
    xhr.upload.onprogress = e => { item.sent = e.loaded; onChange(items); };
    xhr.onload = () => {
      let answer = {};
      try { answer = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) Object.assign(item, {status: 'done', sent: item.size, saved: answer.name});
      else Object.assign(item, {status: 'failed', error: answer.error || 'other'});
      done();
    };
    xhr.onerror = xhr.onabort = () => { Object.assign(item, {status: 'failed', error: 'other'}); done(); };
    xhr.send(item.file);
  }

  function done() {
    sending = false;
    onChange(items);
    next();
  }

  // The finished ones leave the list; those waiting or on their way stay.
  function tidy() {
    for (let i = items.length - 1; i >= 0; i--) if (items[i].status === 'done' || items[i].status === 'failed') items.splice(i, 1);
    onChange(items);
  }

  return {add, tidy, items};
}
