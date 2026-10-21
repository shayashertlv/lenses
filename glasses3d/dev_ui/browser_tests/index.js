// Node 26 resolves an explicitly passed directory through index.js.
// Preserve the brief's command: node --test glasses3d/dev_ui/browser_tests/
require('./ui.test.mjs');
require('./provenance.test.mjs');
require('./lens-colour.test.mjs');
require('./e3-lens-colour.test.mjs');
require('./general-edit.test.mjs');
require('./e4c-clarity.test.mjs');
require('./atomic-replace.test.mjs');
