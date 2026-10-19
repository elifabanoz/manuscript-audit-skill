'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { readJson } = require('./common.cjs');
const { corpusHash, subjectHash, validate } = require('./validate-run.cjs');
const { escapeMarkdown, outsideGit } = require('./render-report.cjs');

function loadRun(run) {
  const manifest = readJson(path.join(run, 'run-manifest.json'));
  const profile = readJson(path.join(run, 'journal-profile.json'));
  const ledger = readJson(path.join(run, 'coverage-ledger.json'));
  const findings = readJson(path.join(run, 'findings.json'));
  const errors = validate(run, manifest, profile, ledger, findings);
  if (errors.length) throw new Error(`Run validation failed for ${run}:\n${errors.join('\n')}`);
  return { manifest, profile, ledger, findings };
}

function sourceIdentity(manifest) {
  return Object.values(manifest.sources).map(item => `${item.url}\0${item.sha256}`).sort().join('\n');
}

function findingMap(findings) {
  return new Map(findings.map(item => [`${item.id}\0${subjectHash(item)}`, item]));
}

function coverageMap(ledger) {
  return new Map(ledger.map(item => [
    [item.unit_id, item.kind, item.rule_id ?? item.review_class, item.section].join('\0'), item
  ]));
}

function describeFinding(finding) {
  return `- ${escapeMarkdown(finding.id)} (${escapeMarkdown(finding.verdict)}): ${escapeMarkdown(finding.claim)}`;
}

function compare(previousRun, currentRun, replace = false) {
  previousRun = path.resolve(previousRun);
  currentRun = path.resolve(currentRun);
  outsideGit(previousRun);
  outsideGit(currentRun);
  if (previousRun === currentRun) throw new Error('Choose two different run directories');
  const previous = loadRun(previousRun);
  const current = loadRun(currentRun);
  if (previous.profile.issn !== current.profile.issn) throw new Error('Runs target different journals');
  const previousFindings = findingMap(previous.findings);
  const currentFindings = findingMap(current.findings);
  const previousOnly = [...previousFindings].filter(([hash]) => !currentFindings.has(hash)).map(([, item]) => item);
  const currentOnly = [...currentFindings].filter(([hash]) => !previousFindings.has(hash)).map(([, item]) => item);
  const oldCoverage = coverageMap(previous.ledger);
  const newCoverage = coverageMap(current.ledger);
  const keys = [...new Set([...oldCoverage.keys(), ...newCoverage.keys()])].sort();
  const coverageChanges = keys.filter(key => oldCoverage.get(key)?.status !== newCoverage.get(key)?.status)
    .map(key => {
      const old = oldCoverage.get(key);
      const next = newCoverage.get(key);
      const unit = next ?? old;
      const label = `${unit.kind}: ${unit.rule_id ?? unit.review_class} (${unit.section})`;
      return `- ${escapeMarkdown(label)}: ${escapeMarkdown(old?.status ?? 'absent')} → ${escapeMarkdown(next?.status ?? 'absent')}`;
    });
  const manuscriptChanged = corpusHash(previous.manifest) !== corpusHash(current.manifest);
  const guidelinesChanged = sourceIdentity(previous.manifest) !== sourceIdentity(current.manifest);
  const lines = [
    '# Revision comparison', '',
    `Journal: ${escapeMarkdown(current.profile.title)} (${escapeMarkdown(current.profile.issn)})`,
    `Manuscript inputs changed: ${manuscriptChanged ? 'yes' : 'no'}`,
    `Guideline sources changed: ${guidelinesChanged ? 'yes' : 'no'}`, '',
    'Both runs passed local validation. A record absent from the current run is not automatically resolved.', '',
    '## Finding records only in the previous run', '',
    ...(previousOnly.length ? previousOnly.map(describeFinding) : ['None.']), '',
    '## Finding records only in the current run', '',
    ...(currentOnly.length ? currentOnly.map(describeFinding) : ['None.']), '',
    '## Coverage status changes', '',
    ...(coverageChanges.length ? coverageChanges : ['None.']), ''
  ];
  const output = path.join(currentRun, 'REVISION-COMPARISON.md');
  if (!replace && fs.existsSync(output)) throw new Error('Comparison already exists; use --replace after reviewing the runs');
  fs.writeFileSync(output, lines.join('\n') + '\n', { encoding: 'utf8', flag: replace ? 'w' : 'wx' });
  return { previousOnly: previousOnly.length, currentOnly: currentOnly.length, coverageChanges: coverageChanges.length,
    manuscriptChanged, guidelinesChanged };
}

if (require.main === module) {
  if (process.argv.length < 4 || process.argv.length > 5 || (process.argv[4] && process.argv[4] !== '--replace')) {
    process.stderr.write('Usage: node compare-runs.cjs <previous-run> <current-run> [--replace]\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(compare(process.argv[2], process.argv[3], process.argv[4] === '--replace'))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { compare };
