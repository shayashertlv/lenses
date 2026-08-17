/**
 * A minimal IndexedDB shelf for telemetry self-tests (anchoring-v3 R0).
 *
 * Exists for exactly one flow: the Browser-pane self-test, where
 * `record-telemetry.html?source=sample` records a fixture that
 * `telemetry-replay.html?fixture=idb:<key>` must then load — and the pane can
 * neither save a download nor pick a file. A localStorage string dies at ~5 MB
 * and a fixture is ~2–15 MB, so IndexedDB it is. The REAL capture flow never
 * touches this: a live session downloads its .ndjson and the replay loads it
 * by file input or URL.
 */

const DB_NAME = 'ar-telemetry';
const STORE = 'fixtures';

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function putFixture(key, text) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(text, key);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

export async function getFixture(key) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const req = tx.objectStore(STORE).get(key);
    req.onsuccess = () => { db.close(); resolve(req.result ?? null); };
    req.onerror = () => { db.close(); reject(req.error); };
  });
}
