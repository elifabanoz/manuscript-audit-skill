'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { corpusHash } = require('./validate-run.cjs');
const { loadRun, sourceIdentity } = require('./compare-runs.cjs');
const { outsideGit } = require('./render-report.cjs');

function hash(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function scopeIdentity(ledger) {
  return ledger.map(item => [item.unit_id, item.kind, item.rule_id ?? item.review_class, item.section].join('\0'))
    .sort().join('\n');
}

function summarize(runs, replace = false) {
  if (!Array.isArray(runs) || runs.length < 1 || runs.length > 3) {
    throw new Error('Choose one to three ordered run directories');
  }
  const directories = runs.map(run => path.resolve(run));
  if (new Set(directories).size !== directories.length) throw new Error('Run directories must be distinct');
  const records = directories.map(directory => {
    outsideGit(directory);
    return loadRun(directory);
  });
  const issn = records[0].profile.issn;
  if (records.some(record => record.profile.issn !== issn)) throw new Error('Runs target different journals');
  const dates = records.map(record => Date.parse(record.manifest.created_at));
  if (dates.some(value => !Number.isFinite(value))) throw new Error('Run creation time is invalid');
  if (dates.some((value, index) => index > 0 && value < dates[index - 1])) {
    throw new Error('Runs must be ordered by creation time');
  }
  const rows = records.map((record, index) => {
    const statuses = { covered: 0, pending: 0, stale: 0 };
    for (const unit of record.ledger) statuses[unit.status]++;
    const scope = scopeIdentity(record.ledger);
    const corpus = corpusHash(record.manifest);
    const sources = sourceIdentity(record.manifest);
    const previous = index ? records[index - 1] : null;
    const total = record.ledger.length;
    return {
      run_index: index + 1,
      created_at: record.manifest.created_at,
      run_manifest_sha256: hash(fs.readFileSync(path.join(directories[index], 'run-manifest.json'))),
      manuscript_corpus_sha256: corpus,
      coverage_scope_sha256: hash(scope),
      coverage_units: total,
      covered: statuses.covered,
      pending: statuses.pending,
      stale: statuses.stale,
      coverage_fraction: total ? Math.round(statuses.covered / total * 1e6) / 1e6 : null,
      confirmed_findings: record.findings.filter(item => item.verdict === 'confirmed').length,
      unresolved_findings: record.findings.filter(item => item.verdict === 'needs_validation').length,
      manuscript_changed_since_previous: previous ? corpus !== corpusHash(previous.manifest) : null,
      guidelines_changed_since_previous: previous ? sources !== sourceIdentity(previous.manifest) : null,
      coverage_scope_changed_since_previous: previous ? scope !== scopeIdentity(previous.ledger) : null
    };
  });
  const result = {
    schema_version: 1,
    status: 'local_records_only',
    target_issn: issn,
    note: 'This curve counts locally validated coverage records. A changing scope changes the denominator. Fewer findings or absent records do not prove resolution or scientific correctness.',
    runs: rows
  };
  const output = path.join(directories.at(-1), 'REVISION-SERIES.json');
  if (!replace && fs.existsSync(output)) throw new Error('Revision series already exists; use --replace after reviewing the runs');
  fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n', { encoding: 'utf8', flag: replace ? 'w' : 'wx' });
  return result;
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const replace = args.at(-1) === '--replace';
  const runs = replace ? args.slice(0, -1) : args;
  if (runs.length < 1 || runs.length > 3 || runs.includes('--replace')) {
    process.stderr.write('Usage: node summarize-revision-series.cjs <run-1> [run-2] [run-3] [--replace]\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(summarize(runs, replace))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { summarize };
