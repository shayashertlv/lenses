/** Start `arview serve` as a process that outlives the terminal (and whatever started it): `arview serve --detach`.
 *
 *  Windows: the server is created through WMI (Win32_Process.Create, via PowerShell), so its parent is the WMI provider
 *  host: it is outside the caller's process tree and job object. Measured on this laptop 2026-09-29 (memory
 *  preview-servers-kill-child-runs): a child started with Node's `detached` (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP),
 *  even with CREATE_BREAKAWAY_FROM_JOB, dies when an IDE run console, a closed console window or the Claude app's
 *  preview job is torn down; a WMI-created process survives. It runs hidden (SW_HIDE) with the user's default environment
 *  and writes its own log (--log). The command line and folder reach PowerShell through environment variables, never
 *  through the script text. If WMI fails, the fallback is a plain detached spawn, and the caller is told what that loses.
 *  POSIX: a detached spawn in its own session (nothing there kills a process tree by parent id). */
import {spawn} from 'node:child_process';
import {runBounded} from '../capture/chromium.mjs';

export const WMI_TIMEOUT_MS = 60_000;
const WMI_SCRIPT = "$ErrorActionPreference = 'Stop'; "
  + "$si = New-CimInstance -ClassName Win32_ProcessStartup -Namespace root/cimv2 -ClientOnly -Property @{ShowWindow = [uint16]0}; "
  + "$r = Invoke-CimMethod -ClassName Win32_Process -Namespace root/cimv2 -MethodName Create -Arguments "
  + "@{CommandLine = $env:ARVIEW_DETACH_COMMAND_LINE; CurrentDirectory = $env:ARVIEW_DETACH_CWD; ProcessStartupInformation = $si}; "
  + "[Console]::Out.Write(('{{\"return_value\": {0}, \"pid\": {1}}}' -f [int]$r.ReturnValue, [int]$r.ProcessId))";

/** One argument quoted for CommandLineToArgvW (the rules the C runtime and Node use to split a Windows command line). */
export function quoteWindowsArg(value) {
  const text = String(value);
  if (text !== '' && !/[\s"]/.test(text)) return text;
  let out = '"', backslashes = 0;
  for (const character of text) {
    if (character === '\\') {backslashes++; continue;}
    if (character === '"') {out += '\\'.repeat(backslashes * 2 + 1) + '"'; backslashes = 0; continue;}
    out += '\\'.repeat(backslashes) + character; backslashes = 0;
  }
  return out + '\\'.repeat(backslashes * 2) + '"';
}

/** Launch `command args...` detached in `cwd`. Resolves {pid, method: 'wmi' | 'spawn', warning}; rejects when nothing
 *  could be started. */
export async function launchDetached(command, args, cwd) {
  if (process.platform === 'win32') {
    const commandLine = [command, ...args].map(quoteWindowsArg).join(' ');
    const result = await runBounded('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', WMI_SCRIPT], WMI_TIMEOUT_MS,
      {env: {...process.env, ARVIEW_DETACH_COMMAND_LINE: commandLine, ARVIEW_DETACH_CWD: cwd}});
    let parsed = null;
    try {parsed = JSON.parse(result.stdout);} catch {/* reported below */}
    if (result.status === 0 && parsed?.return_value === 0 && Number.isInteger(parsed.pid) && parsed.pid > 0) return {pid: parsed.pid, method: 'wmi', warning: null};
    const why = result.timedOut ? `PowerShell did not answer within ${WMI_TIMEOUT_MS / 1000} s`
      : result.error ?? `PowerShell exit ${result.status}, Win32_Process.Create return ${parsed?.return_value ?? '?'}: ${(result.stderr || result.stdout).slice(0, 300)}`;
    const pid = spawnDetached(command, args, cwd);
    return {pid, method: 'spawn', warning: `WMI could not create the server (${why}); it was started with a plain detached spawn instead, which a job `
      + 'object (an IDE run console, the Claude app\'s preview) can still kill when it closes. Start it from an ordinary terminal to be safe.'};
  }
  return {pid: spawnDetached(command, args, cwd), method: 'spawn', warning: null};
}

function spawnDetached(command, args, cwd) {
  const child = spawn(command, args, {cwd, detached: true, stdio: 'ignore', windowsHide: true});
  child.on('error', () => {/* a failed start has no pid: thrown below */});
  child.unref();
  if (!child.pid) throw new Error(`could not start ${command}`);
  return child.pid;
}
