'use strict';

const path = require('node:path');
const { isObject, printResult, readJson } = require('./common.cjs');
const { validate: validateManifest } = require('./validate-run-manifest.cjs');
const { validate: validateLedger } = require('./validate-coverage-ledger.cjs');
const { verify } = require('./verify-quotes.cjs');

function validate(run, manifest, profile, ledger, findings) {
  const errors = [
    ...validateManifest(run, manifest),
    ...validateLedger(ledger).map(error => `ledger: ${error}`),
    ...verify(run, profile, findings).map(error => `evidence: ${error}`)
  ];
  if (!isObject(manifest?.sources) || !isObject(profile?.sources)) return errors;

  const manifestIds = Object.keys(manifest.sources).sort();
  const profileIds = Object.keys(profile.sources).sort();
  if (JSON.stringify(manifestIds) !== JSON.stringify(profileIds)) {
    errors.push('profile.sources must contain exactly the manifest source IDs');
  }
  for (const id of manifestIds) {
    const expected = manifest.sources[id];
    const actual = profile.sources[id];
    if (!isObject(expected) || !isObject(actual)) continue;
    for (const field of ['url', 'retrieved_at', 'sha256', 'path']) {
      if (actual[field] !== expected[field]) errors.push(`profile.sources.${id}.${field} differs from the manifest`);
    }
  }

  const supplied = [manifest.manuscript?.path, ...(Array.isArray(manifest.supplementary) ? manifest.supplementary.map(file => file?.path) : [])];
  const suppliedPaths = new Set(supplied.filter(value => typeof value === 'string'));
  if (suppliedPaths.size !== supplied.length) errors.push('manifest manuscript paths must be distinct');
  if (Array.isArray(ledger)) {
    for (const [index, unit] of ledger.entries()) {
      if (unit?.target_issn !== profile.issn) errors.push(`ledger[${index}].target_issn differs from profile.issn`);
    }
  }
  if (Array.isArray(findings)) {
    for (const [index, finding] of findings.entries()) {
      if (!Array.isArray(finding?.evidence)) continue;
      for (const [eIndex, evidence] of finding.evidence.entries()) {
        const at = `findings[${index}].evidence[${eIndex}]`;
        if (evidence?.type === 'manuscript_quote' && !suppliedPaths.has(evidence.path)) {
          errors.push(`${at}.path is not a supplied manuscript file`);
        }
        if (evidence?.type === 'manuscript_absence' && Array.isArray(evidence.searched)) {
          const searched = new Set(evidence.searched);
          for (const file of suppliedPaths) if (!searched.has(file)) errors.push(`${at}.searched omits ${file}`);
          for (const file of searched) if (!suppliedPaths.has(file)) errors.push(`${at}.searched includes an unsupplied file: ${file}`);
        }
      }
    }
  }
  return errors;
}

if (require.main === module) {
  if (process.argv.length !== 3) {
    process.stderr.write('Usage: node validate-run.cjs <run-directory>\n');
    process.exitCode = 2;
  } else {
    try {
      const run = path.resolve(process.argv[2]);
      printResult(validate(
        run,
        readJson(path.join(run, 'run-manifest.json')),
        readJson(path.join(run, 'journal-profile.json')),
        readJson(path.join(run, 'coverage-ledger.json')),
        readJson(path.join(run, 'findings.json'))
      ));
    } catch (error) {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    }
  }
}

module.exports = { validate };
