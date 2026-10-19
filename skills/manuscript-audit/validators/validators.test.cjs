'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const { validate: validateProfile } = require('./validate-journal-profile.cjs');
const { validate: validateLedger } = require('./validate-coverage-ledger.cjs');
const { validate: validateFindings } = require('./validate-findings.cjs');
const { verify } = require('./verify-quotes.cjs');
const { validate: validateManifest } = require('./validate-run-manifest.cjs');

function fixture(t) {
  const run = fs.mkdtempSync(path.join(os.tmpdir(), 'manuscript-audit-'));
  t.after(() => fs.rmSync(run, { recursive: true, force: true }));
  fs.mkdirSync(path.join(run, 'sources'));
  const guideline = 'Example Journal guidelines. A data availability statement is required.';
  fs.writeFileSync(path.join(run, 'sources', 'guideline.txt'), guideline);
  fs.writeFileSync(path.join(run, 'manuscript.md'), 'Title\nIntroduction\nMethods and results\n');
  const digest = crypto.createHash('sha256').update(guideline).digest('hex');
  const profile = {
    issn: '1234-567X',
    title: 'Example Journal',
    publisher: 'Example Publisher',
    article_types: ['original_research'],
    rules: [{
      rule_id: 'data_availability',
      kind: 'semantic',
      requirement: 'Include a data availability statement',
      quote: 'A data availability statement is required.',
      source_ref: 'src_guideline'
    }],
    sources: {
      src_guideline: {
        url: 'https://example.invalid/guidelines',
        retrieved_at: '2026-10-19T09:12:00Z',
        sha256: digest,
        path: 'sources/guideline.txt'
      }
    }
  };
  const findings = [{
    id: 'F-1',
    verdict: 'confirmed',
    category: 'compliance',
    severity: 'major',
    target_issn: '1234-567X',
    rule_id: 'data_availability',
    claim: 'The manuscript has no data availability statement.',
    evidence: [
      { type: 'guideline', source_ref: 'src_guideline', quote: 'A data availability statement is required.' },
      { type: 'manuscript_absence', searched: ['manuscript.md'], patterns: ['data availability'] }
    ],
    suggested_fix: 'Add a data availability statement.',
    provenance: {
      hunter: 'agents/hunter.json',
      validator: 'agents/defense.json',
      record_verifier: 'agents/record-verifier.json'
    }
  }];
  const ledger = [{
    unit_id: 'U-1',
    kind: 'compliance',
    section: 'sec-3',
    target_issn: '1234-567X',
    status: 'covered',
    checked_by: ['agents/hunter.json'],
    source_hash: crypto.createHash('sha256').update('Methods and results').digest('hex')
  }];
  return { run, profile, findings, ledger };
}

test('valid records and local quotes pass', t => {
  const data = fixture(t);
  assert.deepEqual(validateProfile(data.profile), []);
  assert.deepEqual(validateLedger(data.ledger), []);
  assert.deepEqual(validateFindings(data.findings, data.profile), []);
  assert.deepEqual(verify(data.run, data.profile, data.findings), []);
});

test('a profile cannot cite an unknown source or duplicate a rule', t => {
  const { profile } = fixture(t);
  profile.rules.push({ ...profile.rules[0], source_ref: 'missing' });
  const errors = validateProfile(profile).join('\n');
  assert.match(errors, /duplicated/);
  assert.match(errors, /absent from sources/);
});

test('a covered ledger unit needs a check record and unique ID', t => {
  const { ledger } = fixture(t);
  ledger[0].checked_by = [];
  ledger.push({ ...ledger[0] });
  const errors = validateLedger(ledger).join('\n');
  assert.match(errors, /at least 1/);
  assert.match(errors, /duplicated/);
});

test('confirmed findings require independent records and matching rule evidence', t => {
  const { findings, profile } = fixture(t);
  findings[0].provenance.validator = findings[0].provenance.hunter;
  findings[0].evidence[0].source_ref = 'missing';
  const errors = validateFindings(findings, profile).join('\n');
  assert.match(errors, /three distinct/);
  assert.match(errors, /absent from profile/);
  assert.match(errors, /rule's source/);
});

test('unresolved findings cannot carry a severity', t => {
  const { findings, profile } = fixture(t);
  findings[0].verdict = 'needs_validation';
  findings[0].unresolved_fact = 'Guideline access is unavailable.';
  assert.match(validateFindings(findings, profile).join('\n'), /severity is only for confirmed/);
  delete findings[0].severity;
  assert.deepEqual(validateFindings(findings, profile), []);
});

test('a changed guideline is rejected by its recorded hash', t => {
  const data = fixture(t);
  fs.appendFileSync(path.join(data.run, 'sources', 'guideline.txt'), ' Changed.');
  assert.match(verify(data.run, data.profile, data.findings).join('\n'), /SHA-256 mismatch/);
});

test('a fabricated guideline quote is rejected', t => {
  const data = fixture(t);
  data.findings[0].evidence[0].quote = 'This rule does not exist.';
  assert.match(verify(data.run, data.profile, data.findings).join('\n'), /not in source/);
});

test('an absence claim fails when the searched text contains the pattern', t => {
  const data = fixture(t);
  fs.appendFileSync(path.join(data.run, 'manuscript.md'), 'Data availability: available on request.\n');
  assert.match(verify(data.run, data.profile, data.findings).join('\n'), /occurs in manuscript.md/);
});

test('a manuscript quote must occur on its cited physical line', t => {
  const data = fixture(t);
  data.findings[0].evidence[1] = {
    type: 'manuscript_quote', path: 'manuscript.md', line_id: 'L2', quote: 'Methods and results'
  };
  assert.match(verify(data.run, data.profile, data.findings).join('\n'), /not on manuscript.md:2/);
  data.findings[0].evidence[1].line_id = 'L3';
  assert.deepEqual(verify(data.run, data.profile, data.findings), []);
});

test('cached files cannot escape the run directory', t => {
  const data = fixture(t);
  data.profile.sources.src_guideline.path = '../outside.txt';
  assert.match(verify(data.run, data.profile, data.findings).join('\n'), /traversal/);
});

test('the command line validator returns a failing status for invalid input', t => {
  const data = fixture(t);
  const file = path.join(data.run, 'ledger.json');
  fs.writeFileSync(file, JSON.stringify([{ ...data.ledger[0], status: 'covered', checked_by: [] }]));
  const result = spawnSync(process.execPath, [path.join(__dirname, 'validate-coverage-ledger.cjs'), file], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /checked_by/);
});

test('a run manifest detects changed private files and escaped paths', t => {
  const data = fixture(t);
  const manuscript = fs.readFileSync(path.join(data.run, 'manuscript.md'));
  const source = fs.readFileSync(path.join(data.run, 'sources', 'guideline.txt'));
  const manifest = {
    schema_version: 1,
    created_at: '2026-10-19T09:12:00Z',
    host: 'manual',
    models: [],
    skill_sha256: crypto.createHash('sha256').update('skill').digest('hex'),
    prompt_hashes: {},
    config_hash: null,
    data_freeze: null,
    cross_host: false,
    manuscript: { path: 'manuscript.md', sha256: crypto.createHash('sha256').update(manuscript).digest('hex') },
    sources: { src_guideline: { ...data.profile.sources.src_guideline } }
  };
  manifest.prompt_hashes['SKILL.md'] = manifest.skill_sha256;
  assert.deepEqual(validateManifest(data.run, manifest), []);
  fs.appendFileSync(path.join(data.run, 'manuscript.md'), 'Changed');
  assert.match(validateManifest(data.run, manifest).join('\n'), /SHA-256 mismatch/);
  manifest.manuscript.path = '../outside.txt';
  assert.match(validateManifest(data.run, manifest).join('\n'), /traversal/);
});
