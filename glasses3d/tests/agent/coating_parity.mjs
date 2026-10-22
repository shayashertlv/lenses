// Reference values from the Edit page's own JavaScript, for the Python port's parity test.
import {pathToFileURL} from 'node:url';
const root = process.argv[2];
const {coatingSwatchRGB} = await import(pathToFileURL(`${root}/dev_ui/lens_renderer/coating.js`));
const {findCoatingForColours} = await import(pathToFileURL(`${root}/dev_ui/static/coating-match.js`));
const cases = JSON.parse(process.argv[3]);
const out = cases.map(({values, cosines, targets}) => ({
  swatches: cosines.map(c => coatingSwatchRGB(values, c)),
  solve: targets ? findCoatingForColours(targets, values, coatingSwatchRGB) : null,
}));
console.log(JSON.stringify(out));
