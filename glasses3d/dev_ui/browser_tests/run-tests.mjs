/** Run the exact brief command, with durable logs and an exit receipt; no timeout wrapper. */
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {ROOT, DATA} from './mock-server.mjs';

fs.mkdirSync(DATA, {recursive: true});
const started = new Date().toISOString(), stamp = started.replace(/[:.]/g, '-');
const logPath = path.join(DATA, `browser-${stamp}.log`), receiptPath = path.join(DATA, `browser-${stamp}.json`);
const log = fs.openSync(logPath, 'w');
const args = ['--test', 'glasses3d/dev_ui/browser_tests/'];
const child = spawn(process.execPath, args, {cwd: ROOT, windowsHide: true, stdio: ['ignore', log, log]});
console.log(JSON.stringify({started, pid: child.pid, command: [process.execPath, ...args], logPath, receiptPath}));
child.on('error', error => {fs.closeSync(log); fs.writeFileSync(receiptPath, JSON.stringify({started, ended: new Date().toISOString(), pid: child.pid, error: error.message, exit_code: 1}, null, 2)); process.exitCode = 1;});
child.on('exit', (code, signal) => {
  fs.closeSync(log); const receipt = {started, ended: new Date().toISOString(), pid: child.pid, command: [process.execPath, ...args], logPath, exit_code: code, signal};
  fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2)); console.log(JSON.stringify(receipt)); process.exitCode = code ?? 1;
});
