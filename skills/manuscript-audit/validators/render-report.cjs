'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { readJson } = require('./common.cjs');
const { validate } = require('./validate-run.cjs');

const SEVERITY = { desk_reject: 0, major: 1, minor: 2, note: 3 };

function escapeMarkdown(value) {
  return String(value).replace(/\r?\n/g, ' ').replace(/[\x00-\x1f]/g, ' ')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/([\\`*_{}\[\]()#+.!|~-])/g, '\\$1');
}

function outsideGit(directory) {
  for (let current = fs.realpathSync(directory); ; current = path.dirname(current)) {
    if (fs.existsSync(path.join(current, '.git'))) throw new Error('Reports must stay outside a Git checkout');
    if (current === path.dirname(current)) break;
  }
}

function sourceLines(finding, profile) {
  return finding.evidence.map(item => {
    if (item.type === 'guideline') {
      const source = profile.sources[item.source_ref];
      return `  - Guideline (${escapeMarkdown(item.source_ref)}, retrieved ${escapeMarkdown(source.retrieved_at)}): ${escapeMarkdown(item.quote)} — ${escapeMarkdown(source.url)}`;
    }
    if (item.type === 'manuscript_quote') {
      return `  - Manuscript (${escapeMarkdown(item.path)}:${escapeMarkdown(item.line_id)}): ${escapeMarkdown(item.quote)}`;
    }
    return `  - Absence check: searched ${item.searched.map(escapeMarkdown).join(', ')} for ${item.patterns.map(escapeMarkdown).join(', ')}`;
  });
}

function render(run, replace = false) {
  run = path.resolve(run);
  outsideGit(run);
  const manifest = readJson(path.join(run, 'run-manifest.json'));
  const profile = readJson(path.join(run, 'journal-profile.json'));
  const ledger = readJson(path.join(run, 'coverage-ledger.json'));
  const findings = readJson(path.join(run, 'findings.json'));
  const errors = validate(run, manifest, profile, ledger, findings);
  if (errors.length) throw new Error(`Run validation failed:\n${errors.join('\n')}`);

  const confirmed = findings.filter(item => item.verdict === 'confirmed').sort((a, b) => SEVERITY[a.severity] - SEVERITY[b.severity] || a.id.localeCompare(b.id));
  const unresolved = findings.filter(item => item.verdict === 'needs_validation');
  const covered = ledger.filter(item => item.status === 'covered').length;
  const pending = ledger.filter(item => item.status === 'pending').length;
  const stale = ledger.filter(item => item.status === 'stale').length;
  const report = [
    '# Manuscript audit',
    '',
    `Journal: ${escapeMarkdown(profile.title)} (${escapeMarkdown(profile.issn)})`,
    `Coverage: ${covered} covered, ${pending} pending, ${stale} stale`,
    '',
    'This report summarizes validated local records. It does not predict an editorial decision.',
    '',
    '## Confirmed findings',
    ''
  ];
  if (!confirmed.length) report.push('None.', '');
  for (const finding of confirmed) {
    report.push(`### ${escapeMarkdown(finding.id)} — ${escapeMarkdown(finding.severity)}`, '', escapeMarkdown(finding.claim), '');
    if (finding.rule_id) report.push(`Rule: ${escapeMarkdown(finding.rule_id)}`, '');
    report.push('Evidence:', '', ...sourceLines(finding, profile), '');
    if (finding.suggested_fix) report.push(`Suggested fix: ${escapeMarkdown(finding.suggested_fix)}`, '');
  }
  report.push('## Needs validation', '', `${unresolved.length} finding(s). See NEEDS-VALIDATION.md.`, '');
  const openUnits = ledger.filter(item => item.status !== 'covered');
  report.push('## Open coverage', '');
  if (!openUnits.length) report.push('None.', '');
  for (const unit of openUnits) {
    const work = unit.kind === 'compliance'
      ? `rule ${escapeMarkdown(unit.rule_id)}`
      : `review class ${escapeMarkdown(unit.review_class)}`;
    report.push(`- ${escapeMarkdown(unit.unit_id)} (${escapeMarkdown(unit.status)}): ${work}; section ${escapeMarkdown(unit.section)}`);
  }
  if (openUnits.length) report.push('');
  const pendingReport = ['# Needs validation', '', `Journal: ${escapeMarkdown(profile.title)} (${escapeMarkdown(profile.issn)})`, ''];
  if (!unresolved.length) pendingReport.push('None.', '');
  for (const finding of unresolved) {
    pendingReport.push(`## ${escapeMarkdown(finding.id)}`, '', escapeMarkdown(finding.claim), '', `Unresolved: ${escapeMarkdown(finding.unresolved_fact)}`, '');
  }
  const revisionPlan = [
    '# Revision plan',
    '',
    `Journal: ${escapeMarkdown(profile.title)} (${escapeMarkdown(profile.issn)})`,
    '',
    'Only confirmed findings appear as revision tasks. Check each change against the manuscript and journal guidelines.',
    '',
    '## Confirmed revision tasks',
    ''
  ];
  if (!confirmed.length) revisionPlan.push('None.', '');
  for (const [index, finding] of confirmed.entries()) {
    revisionPlan.push(`${index + 1}. ${escapeMarkdown(finding.id)} (${escapeMarkdown(finding.severity)}): ${escapeMarkdown(finding.claim)}`);
    if (finding.suggested_fix) revisionPlan.push(`   Suggested change: ${escapeMarkdown(finding.suggested_fix)}`);
  }
  if (confirmed.length) revisionPlan.push('');
  revisionPlan.push('## Pending review', '', `${unresolved.length} unresolved finding(s). See NEEDS-VALIDATION.md before treating them as revision tasks.`, '');
  const outputs = [
    [path.join(run, 'REPORT.md'), report.join('\n') + '\n'],
    [path.join(run, 'NEEDS-VALIDATION.md'), pendingReport.join('\n') + '\n'],
    [path.join(run, 'REVISION-PLAN.md'), revisionPlan.join('\n') + '\n']
  ];
  if (!replace && outputs.some(([file]) => fs.existsSync(file))) throw new Error('A report already exists; use --replace after reviewing the run');
  for (const [file, content] of outputs) fs.writeFileSync(file, content, { encoding: 'utf8', flag: replace ? 'w' : 'wx' });
  return { confirmed: confirmed.length, unresolved: unresolved.length, covered, pending, stale };
}

if (require.main === module) {
  if (process.argv.length < 3 || process.argv.length > 4 || (process.argv[3] && process.argv[3] !== '--replace')) {
    process.stderr.write('Usage: node render-report.cjs <run-directory> [--replace]\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(render(process.argv[2], process.argv[3] === '--replace'))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { escapeMarkdown, outsideGit, render };
