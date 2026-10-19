'use strict';

const path = require('node:path');
const { readJson } = require('./common.cjs');
const { reviewSubjectHash } = require('./validate-run.cjs');

if (process.argv.length !== 4) {
  process.stderr.write('Usage: node hash-finding.cjs <run-directory> <finding-id>\n');
  process.exitCode = 2;
} else {
  try {
    const run = path.resolve(process.argv[2]);
    const findings = readJson(path.join(run, 'findings.json'));
    const manifest = readJson(path.join(run, 'run-manifest.json'));
    const profile = readJson(path.join(run, 'journal-profile.json'));
    const matches = Array.isArray(findings) ? findings.filter(finding => finding?.id === process.argv[3]) : [];
    if (matches.length !== 1) throw new Error('Expected exactly one finding with that ID');
    process.stdout.write(`${reviewSubjectHash(matches[0], manifest, profile)}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
