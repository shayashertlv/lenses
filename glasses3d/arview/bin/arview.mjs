#!/usr/bin/env node
/** arview command line (package.json "bin"):
 *    arview capture <model.glb> --poses default|junction|all --out <dir> ...   runs qa/capture.mjs (posed AR captures)
 *    arview serve <model.glb> [--port 8251] [--detach] ...                     runs qa/serve.mjs (the owner's webcam page)
 *    arview serve --stop|--status [--port 8251] */
const [command, ...rest] = process.argv.slice(2);
if (command === 'capture' || command === 'serve') {
  const {main} = await import(command === 'capture' ? '../qa/capture.mjs' : '../qa/serve.mjs');
  main(rest).then(code => process.exit(code), error => {console.error(error?.stack ?? error); process.exit(1);});
} else {
  const [{USAGE}, {SERVE_USAGE}] = await Promise.all([import('../qa/capture/cli.ts'), import('../qa/serve/cli.ts')]);
  const help = command === undefined || command === '--help' || command === '-h';
  console.error(`${help ? '' : `arview: unknown command "${command}"\n`}${USAGE}\n\n${SERVE_USAGE}`);
  process.exit(help ? 0 : 2);
}
