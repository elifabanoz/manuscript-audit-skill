'use strict';

const { readJson } = require('./common.cjs');
const { subjectHash } = require('./validate-run.cjs');

if (process.argv.length !== 4) {
  process.stderr.write('Usage: node hash-finding.cjs <findings.json> <finding-id>\n');
  process.exitCode = 2;
} else {
  try {
    const findings = readJson(process.argv[2]);
    const matches = Array.isArray(findings) ? findings.filter(finding => finding?.id === process.argv[3]) : [];
    if (matches.length !== 1) throw new Error('Expected exactly one finding with that ID');
    process.stdout.write(`${subjectHash(matches[0])}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
