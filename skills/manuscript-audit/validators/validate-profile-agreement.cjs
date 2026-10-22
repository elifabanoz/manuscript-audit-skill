'use strict';

const { validateAgreedProfile } = require('./validate-cached-profile.cjs');

if (require.main === module) {
  if (process.argv.length !== 3) {
    process.stderr.write('Usage: node validate-profile-agreement.cjs <control-run>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(validateAgreedProfile(process.argv[2]))}\n`); }
    catch (error) {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    }
  }
}
