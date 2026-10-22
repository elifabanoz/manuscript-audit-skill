'use strict';

const { validateDraft } = require('./compare-profiles.cjs');

if (require.main === module) {
  if (process.argv.length !== 4) {
    process.stderr.write('Usage: node validate-profile-draft.cjs <run-directory> <profile-draft.json>\n');
    process.exitCode = 2;
  } else {
    try {
      validateDraft(process.argv[2], process.argv[3]);
      process.stdout.write('Valid source-backed draft; independent agreement is still required.\n');
    } catch (error) {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    }
  }
}
