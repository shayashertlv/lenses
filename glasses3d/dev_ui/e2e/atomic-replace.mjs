/** Same-file atomic replacement with a bounded Windows sharing-denial retry.
 * Never delete or copy over the destination: readers keep the last complete report.
 */
import fs from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';

// Matches agent/procs.py's replace_with_retry schedule (3.185 s total).
export const REPLACE_DELAYS_MS = Object.freeze([10, 25, 50, 100, 200, 400, 800, 1600]);
const TRANSIENT = new Set(['EPERM', 'EACCES', 'EBUSY']);

export async function replaceWithRetry(source, destination, {
  rename = fs.rename, sleep = delay, log = record => console.warn(JSON.stringify(record)),
} = {}) {
  const maxAttempts = REPLACE_DELAYS_MS.length + 1;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      await rename(source, destination);
      return;
    } catch (error) {
      if (!TRANSIENT.has(error.code)) throw error;
      const retryMs = REPLACE_DELAYS_MS[attempt - 1];
      log({event: retryMs === undefined ? 'atomic_replace_failed' : 'atomic_replace_retry',
        at: new Date().toISOString(), source, destination, attempt, max_attempts: maxAttempts,
        code: error.code, ...(retryMs === undefined ? {} : {retry_ms: retryMs})});
      if (retryMs === undefined) {
        const failure = new Error(`Atomic replace failed after ${attempt} attempts (${error.code}): ${source} -> ${destination}`, {cause: error});
        failure.code = error.code;
        throw failure;
      }
      await sleep(retryMs);
    }
  }
}
