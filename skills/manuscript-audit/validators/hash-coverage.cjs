'use strict';

const path = require('node:path');
const { readJson } = require('./common.cjs');
const { coverageSubjectHash } = require('./validate-run.cjs');

if (process.argv.length !== 4) {
  process.stderr.write('Usage: node hash-coverage.cjs <run-directory> <unit-id>\n');
  process.exitCode = 2;
} else {
  try {
    const run = path.resolve(process.argv[2]);
    const ledger = readJson(path.join(run, 'coverage-ledger.json'));
    const manifest = readJson(path.join(run, 'run-manifest.json'));
    const profile = readJson(path.join(run, 'journal-profile.json'));
    const matches = Array.isArray(ledger) ? ledger.filter(unit => unit?.unit_id === process.argv[3]) : [];
    if (matches.length !== 1) throw new Error('Expected exactly one coverage unit with that ID');
    process.stdout.write(`${coverageSubjectHash(matches[0], manifest, profile)}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
