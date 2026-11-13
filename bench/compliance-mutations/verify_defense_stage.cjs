'use strict';

// Replay the private phase-4 snapshot against the inputs of a completed run.
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { safeFile } = require('../../skills/manuscript-audit/validators/common.cjs');
const { reviewSubjectHash } = require('../../skills/manuscript-audit/validators/validate-run.cjs');
const { outsideGit } = require('../../skills/manuscript-audit/validators/render-report.cjs');

const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function verifyStage(run) {
  run = path.resolve(run);
  outsideGit(run);
  const input = name => fs.readFileSync(safeFile(run, name));
  const snapshotBytes = input('defense-stage.json');
  const snapshot = JSON.parse(snapshotBytes);
  const expectedKeys = ['schema_version', 'run_manifest_sha256', 'journal_profile_sha256',
    'coverage_ledger_sha256', 'raw_findings_sha256', 'phase4_findings_sha256',
    'raw_rule_ids', 'confirmed_rule_ids', 'decisions', 'phase4_findings_json', 'review_records'];
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)
      || !same(Object.keys(snapshot).sort(), expectedKeys.sort()) || snapshot.schema_version !== 2) {
    throw new Error('Unsupported defense-stage snapshot');
  }
  for (const [file, field] of [
    ['run-manifest.json', 'run_manifest_sha256'],
    ['journal-profile.json', 'journal_profile_sha256'],
    ['coverage-ledger.json', 'coverage_ledger_sha256'],
    ['raw-findings.json', 'raw_findings_sha256']
  ]) {
    if (digest(input(file)) !== snapshot[field]) throw new Error(`${file} changed after defense capture`);
  }
  if (typeof snapshot.phase4_findings_json !== 'string'
      || digest(Buffer.from(snapshot.phase4_findings_json, 'utf8')) !== snapshot.phase4_findings_sha256) {
    throw new Error('Phase-4 finding bytes differ from the snapshot hash');
  }
  const manifest = JSON.parse(input('run-manifest.json'));
  const profile = JSON.parse(input('journal-profile.json'));
  const raw = JSON.parse(input('raw-findings.json'));
  const phase4 = JSON.parse(snapshot.phase4_findings_json);
  const final = JSON.parse(input('findings.json'));
  if (!Array.isArray(raw) || !Array.isArray(phase4) || !Array.isArray(final)
      || !Array.isArray(snapshot.decisions) || !Array.isArray(snapshot.review_records)
      || snapshot.decisions.length !== phase4.length || snapshot.review_records.length !== phase4.length) {
    throw new Error('Defense-stage candidate or decision arrays are incomplete');
  }
  const rawRules = [...new Set(raw.filter(item => item?.category === 'compliance')
    .map(item => item.rule_id))].sort();
  if (!same(rawRules, snapshot.raw_rule_ids)) throw new Error('Defense-stage raw rules differ');
  const finalById = new Map(final.map(item => [item.id, item]));
  if (finalById.size !== final.length) throw new Error('Final finding IDs are duplicated');
  const confirmed = new Set();
  for (const [index, finding] of phase4.entries()) {
    const decision = snapshot.decisions[index];
    const records = snapshot.review_records[index];
    const later = finalById.get(finding.id);
    if (!later || later.category !== finding.category || later.rule_id !== finding.rule_id) {
      throw new Error(`${finding.id}: final finding does not retain the phase-4 candidate identity`);
    }
    if (decision.finding_id !== finding.id || decision.category !== finding.category
        || decision.decision !== finding.verdict || records.finding_id !== finding.id
        || (finding.category === 'compliance' && decision.rule_id !== finding.rule_id)) {
      throw new Error(`${finding.id}: frozen decision differs from phase-4 finding`);
    }
    if (typeof records.hunter_json !== 'string' || typeof records.defense_json !== 'string'
        || digest(Buffer.from(records.hunter_json, 'utf8')) !== decision.hunter_record_sha256
        || digest(Buffer.from(records.defense_json, 'utf8')) !== decision.defense_record_sha256) {
      throw new Error(`${finding.id}: frozen reviewer records differ from their hashes`);
    }
    const hunter = JSON.parse(records.hunter_json);
    const defense = JSON.parse(records.defense_json);
    const subject = reviewSubjectHash(finding, manifest, profile);
    if (hunter.role !== 'hunter' || hunter.finding_id !== finding.id
        || hunter.decision !== 'candidate' || hunter.subject_sha256 !== subject
        || defense.role !== 'defense' || defense.finding_id !== finding.id
        || defense.decision !== finding.verdict || defense.subject_sha256 !== subject
        || !hunter.actor_id || !defense.actor_id || hunter.actor_id === defense.actor_id) {
      throw new Error(`${finding.id}: frozen reviewer records do not support the phase-4 decision`);
    }
    if (finding.category === 'compliance' && finding.verdict === 'confirmed') {
      if (!rawRules.includes(finding.rule_id)) throw new Error(`${finding.id}: rule absent from raw candidates`);
      confirmed.add(finding.rule_id);
    }
  }
  const confirmedRules = [...confirmed].sort();
  if (!same(confirmedRules, snapshot.confirmed_rule_ids)) {
    throw new Error('Frozen confirmed rules differ from reviewer decisions');
  }
  return { snapshot_sha256: digest(snapshotBytes), confirmed_rule_ids: confirmedRules };
}

if (require.main === module) {
  if (process.argv.length !== 3) {
    process.stderr.write('Usage: node verify_defense_stage.cjs <private-run-directory>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(verifyStage(process.argv[2]))}\n`); }
    catch (error) { process.stderr.write(`Error: ${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { verifyStage };
