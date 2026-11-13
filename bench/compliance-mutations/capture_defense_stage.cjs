'use strict';

// Freeze the defense decisions before any record-verifier result exists.
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { readJson, safeFile } = require('../../skills/manuscript-audit/validators/common.cjs');
const { validate } = require('../../skills/manuscript-audit/validators/validate-run.cjs');
const { outsideGit } = require('../../skills/manuscript-audit/validators/render-report.cjs');

function digest(bytes) {
  return crypto.createHash('sha256').update(bytes).digest('hex');
}

function capture(run) {
  run = path.resolve(run);
  outsideGit(run);
  const input = name => fs.readFileSync(safeFile(run, name));
  const manifestBytes = input('run-manifest.json');
  const profileBytes = input('journal-profile.json');
  const ledgerBytes = input('coverage-ledger.json');
  const rawBytes = input('raw-findings.json');
  const findingsBytes = input('findings.json');
  const manifest = JSON.parse(manifestBytes);
  const profile = JSON.parse(profileBytes);
  const ledger = JSON.parse(ledgerBytes);
  const raw = JSON.parse(rawBytes);
  const findings = JSON.parse(findingsBytes);
  if (!Array.isArray(raw) || !Array.isArray(findings)) {
    throw new Error('raw-findings.json and findings.json must be arrays');
  }
  if (findings.some(item => !item || !/^F-[0-9]+$/.test(item.id))) {
    throw new Error('Every finding needs a valid ID');
  }
  if (new Set(findings.map(item => item.id)).size !== findings.length) {
    throw new Error('Finding IDs must be unique');
  }
  const rawRules = new Set();
  for (const item of raw) {
    if (!item || item.category !== 'compliance' || typeof item.rule_id !== 'string') {
      throw new Error('Each raw candidate must identify a compliance rule');
    }
    rawRules.add(item.rule_id);
  }
  const decisions = [];
  for (const finding of findings) {
    const provenance = finding.provenance;
    if (!provenance || !provenance.hunter || !provenance.validator || !provenance.record_verifier) {
      throw new Error(`${finding.id}: hunter, defense, and planned verifier paths are required`);
    }
    if (!/^agents\/[A-Za-z0-9_-]+\.json$/.test(provenance.record_verifier)) {
      throw new Error(`${finding.id}: planned verifier path must be under agents/`);
    }
    if (fs.existsSync(path.join(run, provenance.record_verifier))) {
      throw new Error(`${finding.id}: capture must precede record verification`);
    }
    if (finding.category === 'compliance' && !rawRules.has(finding.rule_id)) {
      throw new Error(`${finding.id}: compliance finding is absent from raw candidates`);
    }
    const errors = validate(run, manifest, profile, ledger, [finding],
      { pendingVerifierFindingId: finding.id });
    if (errors.length) throw new Error(`${finding.id}: ${errors.join('; ')}`);
    decisions.push({
      finding_id: finding.id,
      category: finding.category,
      ...(finding.category === 'compliance' ? { rule_id: finding.rule_id } : {}),
      decision: finding.verdict,
      hunter_record_sha256: digest(input(provenance.hunter)),
      defense_record_sha256: digest(input(provenance.validator))
    });
  }
  if (!findings.length) {
    const errors = validate(run, manifest, profile, ledger, []);
    if (errors.length) throw new Error(errors.join('; '));
  }
  const snapshot = {
    schema_version: 1,
    run_manifest_sha256: digest(manifestBytes),
    journal_profile_sha256: digest(profileBytes),
    coverage_ledger_sha256: digest(ledgerBytes),
    raw_findings_sha256: digest(rawBytes),
    phase4_findings_sha256: digest(findingsBytes),
    raw_rule_ids: [...rawRules].sort(),
    confirmed_rule_ids: [...new Set(decisions.filter(item => item.category === 'compliance'
      && item.decision === 'confirmed').map(item => item.rule_id))].sort(),
    decisions
  };
  for (const [name, bytes] of [
    ['run-manifest.json', manifestBytes], ['journal-profile.json', profileBytes],
    ['coverage-ledger.json', ledgerBytes], ['raw-findings.json', rawBytes],
    ['findings.json', findingsBytes]
  ]) {
    if (digest(input(name)) !== digest(bytes)) {
      throw new Error(`${name} changed during defense-stage capture`);
    }
  }
  for (const [index, finding] of findings.entries()) {
    if (digest(input(finding.provenance.hunter)) !== decisions[index].hunter_record_sha256
        || digest(input(finding.provenance.validator)) !== decisions[index].defense_record_sha256) {
      throw new Error(`${finding.id}: reviewer record changed during defense-stage capture`);
    }
  }
  const output = path.join(run, 'defense-stage.json');
  fs.writeFileSync(output, `${JSON.stringify(snapshot, null, 2)}\n`, { flag: 'wx' });
  return output;
}

if (require.main === module) {
  if (process.argv.length !== 3) {
    process.stderr.write('Usage: node capture_defense_stage.cjs <private-run-directory>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${capture(process.argv[2])}\n`); }
    catch (error) { process.stderr.write(`Error: ${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { capture };
