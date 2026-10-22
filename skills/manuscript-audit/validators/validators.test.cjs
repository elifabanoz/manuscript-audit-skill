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
const { corpusHash, coverageSubjectHash, reviewSubjectHash, validate: validateRun } = require('./validate-run.cjs');
const { escapeMarkdown, render } = require('./render-report.cjs');
const { compare } = require('./compare-runs.cjs');
const { compare: compareProfiles, validateDraft } = require('./compare-profiles.cjs');
const { validateAgreedProfile } = require('./validate-cached-profile.cjs');
const { adjudicate } = require('./adjudicate-profiles.cjs');

test('the 20 public profile fixtures retain valid source identities', () => {
  const directory = path.resolve(__dirname, '..', '..', '..', 'journal-profiles');
  const files = fs.readdirSync(directory).filter(file => file.endsWith('.json')).sort();
  assert.equal(files.length, 20);
  for (const file of files) {
    const profile = JSON.parse(fs.readFileSync(path.join(directory, file), 'utf8'));
    assert.deepEqual(validateProfile(profile), [], file);
    assert.equal(file, `${profile.issn}.json`);
    for (const [sourceId, source] of Object.entries(profile.sources)) {
      const expected = crypto.createHash('sha256').update(`${source.url}\0${source.sha256}`).digest('hex').slice(0, 16);
      assert.equal(sourceId, `src_${expected}`, file);
      assert.equal(source.path, `sources/${sourceId}.txt`, file);
    }
  }
});

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
  fs.mkdirSync(path.join(run, 'agents'));
  const manifest = manifestFor({ run, profile });
  for (const [key, role, decision] of [
    ['hunter', 'hunter', 'candidate'],
    ['validator', 'defense', 'confirmed'],
    ['record_verifier', 'record_verifier', 'confirmed']
  ]) {
    fs.writeFileSync(path.join(run, findings[0].provenance[key]), JSON.stringify({
      role,
      finding_id: findings[0].id,
      decision,
      actor_id: `actor-${role}`,
      subject_sha256: reviewSubjectHash(findings[0], manifest, profile),
      rationale: 'Synthetic check record.'
    }));
  }
  const ledger = [{
    unit_id: 'U-1',
    kind: 'compliance',
    rule_id: 'data_availability',
    section: 'sec-3',
    target_issn: '1234-567X',
    status: 'covered',
    checked_by: ['agents/coverage-U-1.json'],
    source_hash: corpusHash(manifest)
  }];
  fs.writeFileSync(path.join(run, ledger[0].checked_by[0]), JSON.stringify({
    unit_id: 'U-1', decision: 'checked', actor_id: 'actor-coverage',
    subject_sha256: coverageSubjectHash(ledger[0], manifest, profile),
    rationale: 'Synthetic coverage check.'
  }));
  return { run, profile, findings, ledger };
}

function manifestFor(data) {
  const skillHash = crypto.createHash('sha256').update('skill').digest('hex');
  return {
    schema_version: 1,
    created_at: '2026-10-19T09:12:00Z',
    host: 'manual',
    models: [],
    skill_sha256: skillHash,
    prompt_hashes: { 'SKILL.md': skillHash },
    config_hash: null,
    data_freeze: null,
    cross_host: false,
    manuscript: {
      path: 'manuscript.md',
      sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(data.run, 'manuscript.md'))).digest('hex')
    },
    supplementary: [],
    sources: structuredClone(data.profile.sources)
  };
}

function refreshCoverageRecord(data, manifest) {
  const file = path.join(data.run, data.ledger[0].checked_by[0]);
  const record = JSON.parse(fs.readFileSync(file, 'utf8'));
  record.subject_sha256 = coverageSubjectHash(data.ledger[0], manifest, data.profile);
  fs.writeFileSync(file, JSON.stringify(record));
}

test('valid records and local quotes pass', t => {
  const data = fixture(t);
  assert.deepEqual(validateProfile(data.profile), []);
  assert.deepEqual(validateLedger(data.ledger), []);
  assert.deepEqual(validateFindings(data.findings, data.profile), []);
  assert.deepEqual(verify(data.run, data.profile, data.findings), []);
});

test('a profile draft is checked against its private run before comparison', t => {
  const { run, profile } = fixture(t);
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor({ run, profile })));
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(profile));
  assert.deepEqual(validateDraft(run, 'profile-a.json').profile, profile);
  assert.equal(fs.readdirSync(run).some(file => file.startsWith('profile-comparison-')), false);
  assert.equal(fs.existsSync(path.join(run, 'journal-profile.json')), false);

  const badQuote = structuredClone(profile);
  badQuote.rules[0].quote = 'An invented requirement';
  fs.writeFileSync(path.join(run, 'bad-quote.json'), JSON.stringify(badQuote));
  assert.throws(() => validateDraft(run, 'bad-quote.json'), /quote is not in source/);

  const badSource = structuredClone(profile);
  badSource.sources.src_guideline.url = 'https://elsewhere.invalid/guidelines';
  fs.writeFileSync(path.join(run, 'bad-source.json'), JSON.stringify(badSource));
  assert.throws(() => validateDraft(run, 'bad-source.json'), /sources differ from the run manifest/);
  assert.throws(() => validateDraft(run, '../profile-a.json'), /path must not contain traversal/);
});

test('independent profiles promote only after source-backed agreement', t => {
  const { run, profile } = fixture(t);
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor({ run, profile })));
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(profile));
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify({ ...profile, rules: [...profile.rules].reverse() }));
  assert.throws(() => validateAgreedProfile(run), /No promoted journal-profile.json/);
  const result = compareProfiles(run, 'profile-a.json', 'profile-b.json', true);
  assert.equal(result.status, 'agreed');
  assert.equal(result.promoted, true);
  assert.deepEqual(result.differing_fields, []);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(run, 'journal-profile.json'))), profile);
  const agreement = validateAgreedProfile(run);
  assert.equal(agreement.profile_sha256,
    crypto.createHash('sha256').update(fs.readFileSync(path.join(run, 'journal-profile.json'))).digest('hex'));
  assert.match(agreement.comparison_record, /^profile-comparison-v2-[a-f0-9-]+\.json$/);
  assert.match(agreement.comparison_sha256, /^[a-f0-9]{64}$/);
  fs.appendFileSync(path.join(run, 'profile-b.json'), '\n');
  assert.throws(() => validateAgreedProfile(run), /No current, agreed two-draft profile comparison/);
});

test('omitted rule status agrees with explicit verified status', t => {
  const { run, profile } = fixture(t);
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor({ run, profile })));
  const explicit = structuredClone(profile);
  explicit.rules[0].status = 'verified';
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(profile));
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify(explicit));
  const result = compareProfiles(run, 'profile-a.json', 'profile-b.json', true);
  assert.equal(result.status, 'agreed');
  assert.equal(result.promoted, true);
});

test('profile disagreements stay private and block promotion', t => {
  const { run, profile } = fixture(t);
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor({ run, profile })));
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(profile));
  const changed = structuredClone(profile);
  changed.rules[0].requirement = 'Use a different statement';
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify(changed));
  const result = compareProfiles(run, 'profile-a.json', 'profile-b.json', true);
  assert.equal(result.status, 'needs_validation');
  assert.deepEqual(result.differing_fields, ['rules.data_availability']);
  assert.equal(fs.existsSync(path.join(run, 'journal-profile.json')), false);
  assert.equal(JSON.parse(fs.readFileSync(result.output)).status, 'needs_validation');
});

test('partial promotion keeps disputed and one-sided rules unresolved', t => {
  const data = fixture(t);
  const { run, profile } = data;
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor(data)));
  const first = structuredClone(profile);
  first.scope = { quote: 'Example Journal guidelines.', source_ref: 'src_guideline' };
  const second = structuredClone(profile);
  second.scope = { quote: 'A data availability statement is required.', source_ref: 'src_guideline' };
  second.rules[0].requirement = 'Provide the required statement';
  second.rules.push({
    rule_id: 'extra_rule', kind: 'semantic', requirement: 'Follow the example journal guidelines',
    quote: 'Example Journal guidelines.', source_ref: 'src_guideline'
  });
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(first));
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify(second));
  const blocked = compareProfiles(run, 'profile-a.json', 'profile-b.json', true);
  assert.equal(blocked.promoted, false);
  const result = compareProfiles(run, 'profile-a.json', 'profile-b.json', 'partial');
  assert.equal(result.status, 'needs_validation');
  assert.equal(result.promoted, true);
  assert.equal(result.output, blocked.output);
  const promoted = JSON.parse(fs.readFileSync(path.join(run, 'journal-profile.json'), 'utf8'));
  assert.equal('scope' in promoted, false);
  assert.deepEqual(promoted.rules.map(rule => [rule.rule_id, rule.status]), [
    ['data_availability', 'needs_validation'], ['extra_rule', 'needs_validation']
  ]);
  assert.deepEqual(validateProfile(promoted), []);
  assert.match(validateFindings(data.findings, promoted).join('\n'), /rule_id has not been validated/);
  assert.throws(() => validateAgreedProfile(run), /source-backed adjudication/);
  assert.throws(() => compareProfiles(run, 'profile-a.json', 'profile-b.json', 'partial'), /Final profile already exists/);
});

test('partial promotion rejects conflicting journal identity', t => {
  const data = fixture(t);
  const { run, profile } = data;
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor(data)));
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(profile));
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify({ ...profile, title: 'Another Journal' }));
  assert.throws(() => compareProfiles(run, 'profile-a.json', 'profile-b.json', 'partial'), /Profile identity differs: title/);
  assert.equal(fs.existsSync(path.join(run, 'journal-profile.json')), false);
});

test('adjudication requires a reason for every difference and cannot silently upgrade a rule', t => {
  const data = fixture(t);
  const { run, profile } = data;
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor(data)));
  const first = structuredClone(profile);
  first.rules[0].status = 'needs_validation';
  const second = structuredClone(profile);
  second.rules[0].requirement = 'Provide the required statement';
  second.rules.push({
    rule_id: 'extra_rule', kind: 'semantic', requirement: 'Follow the example journal guidelines',
    quote: 'Example Journal guidelines.', source_ref: 'src_guideline'
  });
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(first));
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify(second));
  const decisionsPath = path.join(run, 'decisions.json');
  fs.writeFileSync(decisionsPath, JSON.stringify({ schema_version: 1, resolutions: {} }));
  assert.throws(() => adjudicate(run, 'profile-a.json', 'profile-b.json', 'decisions.json'), /One resolution is required/);
  const resolutions = {
    'rules.data_availability': { choice: 'first', reason: 'The first wording follows the cited source.', status: 'verified' },
    'rules.extra_rule': { choice: 'omit', reason: 'It duplicates the first rule.' }
  };
  fs.writeFileSync(decisionsPath, JSON.stringify({ schema_version: 1, resolutions }));
  assert.throws(() => adjudicate(run, 'profile-a.json', 'profile-b.json', 'decisions.json'), /Cannot upgrade unresolved rule/);
  resolutions['rules.data_availability'].status = 'needs_validation';
  fs.writeFileSync(decisionsPath, JSON.stringify({ schema_version: 1, resolutions }));
  const result = adjudicate(run, 'profile-a.json', 'profile-b.json', 'decisions.json');
  assert.equal(result.rules, 1);
  assert.equal(result.unresolved_rules, 1);
  assert.deepEqual(validateProfile(JSON.parse(fs.readFileSync(result.output))), []);
  const agreement = validateAgreedProfile(run);
  assert.match(agreement.adjudication_record, /^profile-adjudication-v1-[a-f0-9-]+\.json$/);
  assert.match(agreement.adjudication_sha256, /^[a-f0-9]{64}$/);
  assert.equal(agreement.profile_sha256, result.profile_sha256);
  resolutions['rules.extra_rule'].reason = 'A changed decision after profile promotion';
  fs.writeFileSync(decisionsPath, JSON.stringify({ schema_version: 1, resolutions }));
  assert.throws(() => validateAgreedProfile(run), /source-backed adjudication/);
});

test('source-backed adjudication resolves article type naming without changing journal identity', t => {
  const data = fixture(t);
  const { run, profile } = data;
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor(data)));
  const second = structuredClone(profile);
  second.article_types = ['Research paper'];
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(profile));
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify(second));
  fs.writeFileSync(path.join(run, 'decisions.json'), JSON.stringify({ schema_version: 1, resolutions: {
    article_types: { choice: 'second', reason: 'The official submission guide uses this article type name.' }
  } }));
  const result = adjudicate(run, 'profile-a.json', 'profile-b.json', 'decisions.json');
  assert.deepEqual(JSON.parse(fs.readFileSync(result.output)).article_types, ['Research paper']);
  assert.equal(validateAgreedProfile(run).adjudication_record, path.basename(result.adjudication_record));
});

test('unresolved journal rules cannot be covered or used for confirmed findings', t => {
  const data = fixture(t);
  data.profile.rules[0].status = 'needs_validation';
  const manifest = manifestFor(data);
  refreshCoverageRecord(data, manifest);
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /rule_id has not been validated/);
  data.findings[0].category = 'review';
  data.findings[0].review_class = 'methods';
  assert.match(validateFindings(data.findings, data.profile).join('\n'), /rule_id has not been validated/);
  const pending = [{ ...data.ledger[0], status: 'pending', checked_by: [] }];
  fs.writeFileSync(path.join(data.run, 'run-manifest.json'), JSON.stringify(manifest));
  fs.writeFileSync(path.join(data.run, 'journal-profile.json'), JSON.stringify(data.profile));
  fs.writeFileSync(path.join(data.run, 'coverage-ledger.json'), JSON.stringify(pending));
  fs.writeFileSync(path.join(data.run, 'findings.json'), '[]');
  render(data.run);
  const report = fs.readFileSync(path.join(data.run, 'REPORT.md'), 'utf8');
  assert.match(report, /Journal rules needing validation/);
  assert.match(report, /data\\_availability/);
});

test('profile comparison rejects invented quotes and source drift', t => {
  const { run, profile } = fixture(t);
  fs.writeFileSync(path.join(run, 'run-manifest.json'), JSON.stringify(manifestFor({ run, profile })));
  fs.writeFileSync(path.join(run, 'profile-a.json'), JSON.stringify(profile));
  const changed = structuredClone(profile);
  changed.rules[0].quote = 'This sentence is absent from the guideline.';
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify(changed));
  assert.throws(() => compareProfiles(run, 'profile-a.json', 'profile-b.json', true), /not in source/);
  changed.rules[0].quote = profile.rules[0].quote;
  changed.sources.src_guideline.url = 'https://example.invalid/other';
  fs.writeFileSync(path.join(run, 'profile-b.json'), JSON.stringify(changed));
  assert.throws(() => compareProfiles(run, 'profile-a.json', 'profile-b.json', true), /sources differ/);
});

test('heading count rules require an explicit heading', t => {
  const { profile } = fixture(t);
  profile.rules.push({
    rule_id: 'data_heading', kind: 'deterministic',
    check: { metric: 'heading_count', op: '>=', value: 1 },
    quote: 'A data availability statement is required.', source_ref: 'src_guideline'
  });
  assert.match(validateProfile(profile).join('\n'), /heading/);
  profile.rules[1].check.heading = 'Data Availability';
  assert.deepEqual(validateProfile(profile), []);
  profile.rules[1].check.metric = 'abstract_words';
  assert.match(validateProfile(profile).join('\n'), /only for heading_count/);
});

test('profiles reject deterministic metrics without an implementation', t => {
  const { profile } = fixture(t);
  profile.rules.push({
    rule_id: 'unsupported_word_count', kind: 'deterministic',
    check: { metric: 'word_count_excluding_tables', op: '<=', value: 5000 },
    quote: 'A data availability statement is required.', source_ref: 'src_guideline'
  });
  assert.match(validateProfile(profile).join('\n'), /metric is not implemented/);
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

test('covered units reject missing, stale, and mismatched check records', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  const file = path.join(data.run, data.ledger[0].checked_by[0]);
  assert.deepEqual(validateRun(data.run, manifest, data.profile, data.ledger, data.findings), []);
  const record = JSON.parse(fs.readFileSync(file, 'utf8'));
  record.unit_id = 'U-2';
  fs.writeFileSync(file, JSON.stringify(record));
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /unit_id differs/);
  record.unit_id = 'U-1';
  fs.writeFileSync(file, JSON.stringify(record));
  fs.appendFileSync(path.join(data.run, 'manuscript.md'), 'A changed sentence.\n');
  const changed = manifestFor(data);
  assert.match(validateRun(data.run, changed, data.profile, data.ledger, data.findings).join('\n'), /coverage inputs/);
  fs.rmSync(file);
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /checked_by/);
});

test('confirmed compliance findings require a covered unit for the same rule', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  const pending = [{ ...data.ledger[0], status: 'pending', checked_by: [] }];
  assert.match(validateRun(data.run, manifest, data.profile, pending, data.findings).join('\n'),
    /no covered compliance unit for its rule/);
  const unrelated = [{ ...data.ledger[0], rule_id: 'unrelated_rule' }];
  assert.match(validateRun(data.run, manifest, data.profile, unrelated, data.findings).join('\n'),
    /no covered compliance unit for its rule/);
  assert.deepEqual(validateRun(data.run, manifest, data.profile, data.ledger, data.findings), []);
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

test('confirmed review and reference findings require manuscript evidence', t => {
  const data = fixture(t);
  const finding = structuredClone(data.findings[0]);
  finding.category = 'review';
  finding.review_class = 'methods';
  delete finding.rule_id;
  assert.match(validateFindings([finding], data.profile).join('\n'), /needs a manuscript quote/);
  finding.category = 'reference';
  delete finding.review_class;
  assert.match(validateFindings([finding], data.profile).join('\n'), /needs a manuscript quote/);
  finding.evidence.push({ type: 'manuscript_quote', path: 'manuscript.md', line_id: 'L3', quote: 'Methods' });
  assert.match(validateFindings([finding], data.profile).join('\n'), /needs a private reference metadata check/);
});

test('confirmed review findings require a covered matching review class', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  const finding = { ...data.findings[0], category: 'review', review_class: 'methods',
    evidence: [{ type: 'manuscript_quote', path: 'manuscript.md', line_id: 'L3', quote: 'Methods and results' }] };
  delete finding.rule_id;
  for (const file of Object.values(finding.provenance)) {
    const recordPath = path.join(data.run, file);
    const record = JSON.parse(fs.readFileSync(recordPath, 'utf8'));
    record.subject_sha256 = reviewSubjectHash(finding, manifest, data.profile);
    fs.writeFileSync(recordPath, JSON.stringify(record));
  }
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, [finding]).join('\n'),
    /no covered review unit for its class/);
  const review = { unit_id: 'U-2', kind: 'review', review_class: 'methods', section: 'Methods',
    target_issn: data.profile.issn, status: 'covered', checked_by: ['agents/coverage-U-2.json'],
    source_hash: corpusHash(manifest) };
  fs.writeFileSync(path.join(data.run, review.checked_by[0]), JSON.stringify({
    unit_id: review.unit_id, decision: 'checked', actor_id: 'actor-review-coverage',
    subject_sha256: coverageSubjectHash(review, manifest, data.profile), rationale: 'Synthetic review check.'
  }));
  assert.deepEqual(validateRun(data.run, manifest, data.profile, [...data.ledger, review], [finding]), []);
});

test('confirmed reference leads bind a private check, registry record, and manuscript span', t => {
  const data = fixture(t);
  const line = 'Lovelace (2020). Example title. doi:10.1234/alpha';
  fs.appendFileSync(path.join(data.run, 'manuscript.md'), `${line}\n`);
  const manifest = manifestFor(data);
  data.ledger[0].source_hash = corpusHash(manifest);
  refreshCoverageRecord(data, manifest);
  const checksum = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
  const inventoryBytes = Buffer.from(JSON.stringify({ schema_version: 1, corpus_sha256: corpusHash(manifest),
    dois: [{ doi: '10.1234/alpha' }] }));
  fs.writeFileSync(path.join(data.run, 'doi-inventory.json'), inventoryBytes);
  const metadataBytes = Buffer.from(JSON.stringify({ schema_version: 1,
    inventory_sha256: checksum(inventoryBytes), corpus_sha256: corpusHash(manifest),
    results: [{ doi: '10.1234/alpha', status: 'registered_in_crossref', registered_doi: '10.1234/alpha' }] }));
  fs.writeFileSync(path.join(data.run, 'doi-metadata.json'), metadataBytes);
  const checksBytes = Buffer.from(JSON.stringify({ schema_version: 1,
    inventory_sha256: checksum(inventoryBytes), metadata_sha256: checksum(metadataBytes),
    corpus_sha256: corpusHash(manifest), results: [{ doi: '10.1234/alpha', path: 'manuscript.md',
      start_line: 4, end_line: 4, reference_span_sha256: checksum(line), status: 'needs_review',
      checks: { year: 'review' } }] }));
  fs.writeFileSync(path.join(data.run, 'reference-metadata-checks.json'), checksBytes);
  const finding = { ...data.findings[0], category: 'reference',
    claim: 'The cited year differs from the registry record.',
    evidence: [
      { type: 'manuscript_quote', path: 'manuscript.md', line_id: 'L4', quote: 'Lovelace (2020)' },
      { type: 'reference_metadata', doi: '10.1234/alpha', result_index: 0, checks_sha256: checksum(checksBytes) }
    ] };
  delete finding.rule_id;
  for (const file of Object.values(finding.provenance)) {
    const recordPath = path.join(data.run, file);
    const record = JSON.parse(fs.readFileSync(recordPath, 'utf8'));
    record.subject_sha256 = reviewSubjectHash(finding, manifest, data.profile);
    fs.writeFileSync(recordPath, JSON.stringify(record));
  }
  assert.deepEqual(validateRun(data.run, manifest, data.profile, data.ledger, [finding]), []);
  for (const [name, value] of [
    ['run-manifest.json', manifest], ['journal-profile.json', data.profile],
    ['coverage-ledger.json', data.ledger], ['findings.json', [finding]]
  ]) fs.writeFileSync(path.join(data.run, name), JSON.stringify(value));
  render(data.run);
  assert.match(fs.readFileSync(path.join(data.run, 'REPORT.md'), 'utf8'),
    /Reference metadata lead: DOI 10\\.1234\/alpha/);
  finding.evidence[1].checks_sha256 = '0'.repeat(64);
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, [finding]).join('\n'),
    /checks_sha256 differs/);
  finding.evidence[1].checks_sha256 = checksum(checksBytes);
  fs.writeFileSync(path.join(data.run, 'doi-metadata.json'), '{}');
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, [finding]).join('\n'),
    /stale DOI inventory or Crossref metadata/);
  fs.writeFileSync(path.join(data.run, 'doi-metadata.json'), metadataBytes);
  const fabricatedChecks = JSON.parse(checksBytes.toString('utf8'));
  fabricatedChecks.results[0].checks.post_publication_update = 'review';
  const fabricatedBytes = Buffer.from(JSON.stringify(fabricatedChecks));
  fs.writeFileSync(path.join(data.run, 'reference-metadata-checks.json'), fabricatedBytes);
  finding.evidence[1].checks_sha256 = checksum(fabricatedBytes);
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, [finding]).join('\n'),
    /lacks a Crossref post-publication update signal/);
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
  const manifest = manifestFor(data);
  assert.deepEqual(validateManifest(data.run, manifest), []);
  fs.appendFileSync(path.join(data.run, 'manuscript.md'), 'Changed');
  assert.match(validateManifest(data.run, manifest).join('\n'), /SHA-256 mismatch/);
  manifest.manuscript.path = '../outside.txt';
  assert.match(validateManifest(data.run, manifest).join('\n'), /traversal/);
});

test('the full run gate binds profile sources to the manifest', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  assert.deepEqual(validateRun(data.run, manifest, data.profile, data.ledger, data.findings), []);
  data.profile.sources.src_guideline.url = 'https://example.invalid/other';
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /url differs from the manifest/);
});

test('absence evidence must search every supplied manuscript file', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  fs.mkdirSync(path.join(data.run, 'supplementary'));
  const supplementary = 'Additional methods without the required statement.\n';
  fs.writeFileSync(path.join(data.run, 'supplementary', 'supp-1.md'), supplementary);
  manifest.supplementary.push({
    path: 'supplementary/supp-1.md',
    sha256: crypto.createHash('sha256').update(supplementary).digest('hex')
  });
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /searched omits supplementary\/supp-1.md/);
  data.findings[0].evidence[1].searched.push('supplementary/supp-1.md');
  for (const file of Object.values(data.findings[0].provenance)) {
    const recordPath = path.join(data.run, file);
    const record = JSON.parse(fs.readFileSync(recordPath, 'utf8'));
    record.subject_sha256 = reviewSubjectHash(data.findings[0], manifest, data.profile);
    fs.writeFileSync(recordPath, JSON.stringify(record));
  }
  data.ledger[0].source_hash = corpusHash(manifest);
  refreshCoverageRecord(data, manifest);
  assert.deepEqual(validateRun(data.run, manifest, data.profile, data.ledger, data.findings), []);
  fs.writeFileSync(path.join(data.run, 'supplementary', 'supp-1.md'), 'Data availability: available on request.\n');
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /occurs in supplementary\/supp-1.md/);
});

test('the full run gate rejects missing or stale independent records', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  data.findings[0].claim = 'Changed claim after review.';
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /subject_sha256 differs/);
  data.findings[0].claim = 'The manuscript has no data availability statement.';
  fs.rmSync(path.join(data.run, 'agents', 'defense.json'));
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /provenance.validator/);
});

test('a revised manuscript makes earlier review records stale', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  fs.appendFileSync(path.join(data.run, 'manuscript.md'), 'Additional background.\n');
  manifest.manuscript.sha256 = crypto.createHash('sha256').update(fs.readFileSync(path.join(data.run, 'manuscript.md'))).digest('hex');
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /current review inputs/);
});

test('the full run gate rejects reused actor identities', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  const file = path.join(data.run, 'agents', 'defense.json');
  const record = JSON.parse(fs.readFileSync(file, 'utf8'));
  record.actor_id = 'actor-hunter';
  fs.writeFileSync(file, JSON.stringify(record));
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /actor_id must differ/);
});

test('whole-corpus coverage becomes stale when its fingerprint differs', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  data.ledger[0].section = 'all-supplied-files';
  data.ledger[0].source_hash = corpusHash(manifest);
  refreshCoverageRecord(data, manifest);
  assert.deepEqual(validateRun(data.run, manifest, data.profile, data.ledger, data.findings), []);
  data.ledger[0].source_hash = crypto.createHash('sha256').update('old corpus').digest('hex');
  assert.match(validateRun(data.run, manifest, data.profile, data.ledger, data.findings).join('\n'), /source_hash is stale/);
});

test('review fingerprints include the original converted file hash', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  const textOnly = corpusHash(manifest);
  manifest.extraction = { format: 'pdf', source_sha256: 'a'.repeat(64), path: 'extraction.json', sha256: 'b'.repeat(64) };
  const first = corpusHash(manifest);
  manifest.extraction.source_sha256 = 'c'.repeat(64);
  assert.notEqual(first, textOnly);
  assert.notEqual(corpusHash(manifest), first);
});

test('review coverage becomes stale when supplied files change', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  const reviewUnit = { unit_id: 'U-2', kind: 'review', review_class: 'methods', section: 'Methods',
    target_issn: data.profile.issn, status: 'pending', checked_by: [], source_hash: corpusHash(manifest) };
  assert.deepEqual(validateRun(data.run, manifest, data.profile, [...data.ledger, reviewUnit], data.findings), []);
  reviewUnit.source_hash = crypto.createHash('sha256').update('old corpus').digest('hex');
  assert.match(validateRun(data.run, manifest, data.profile, [...data.ledger, reviewUnit], data.findings).join('\n'), /source_hash is stale/);
});

test('the hash command identifies the exact finding under review', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  fs.writeFileSync(path.join(data.run, 'run-manifest.json'), JSON.stringify(manifest));
  fs.writeFileSync(path.join(data.run, 'journal-profile.json'), JSON.stringify(data.profile));
  fs.writeFileSync(path.join(data.run, 'findings.json'), JSON.stringify(data.findings));
  const result = spawnSync(process.execPath, [path.join(__dirname, 'hash-finding.cjs'), data.run, 'F-1'], { encoding: 'utf8' });
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), reviewSubjectHash(data.findings[0], manifest, data.profile));
});

test('the coverage hash command identifies the current unit', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  fs.writeFileSync(path.join(data.run, 'run-manifest.json'), JSON.stringify(manifest));
  fs.writeFileSync(path.join(data.run, 'journal-profile.json'), JSON.stringify(data.profile));
  fs.writeFileSync(path.join(data.run, 'coverage-ledger.json'), JSON.stringify(data.ledger));
  const result = spawnSync(process.execPath, [path.join(__dirname, 'hash-coverage.cjs'), data.run, 'U-1'], { encoding: 'utf8' });
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), coverageSubjectHash(data.ledger[0], manifest, data.profile));
});

test('report rendering uses validated findings and shows remaining coverage', t => {
  const data = fixture(t);
  const manifest = manifestFor(data);
  fs.writeFileSync(path.join(data.run, 'run-manifest.json'), JSON.stringify(manifest));
  fs.writeFileSync(path.join(data.run, 'journal-profile.json'), JSON.stringify(data.profile));
  const pending = { unit_id: 'U-2', kind: 'review', review_class: 'methods', section: 'Methods',
    target_issn: data.profile.issn, status: 'pending', checked_by: [], source_hash: corpusHash(manifest) };
  fs.writeFileSync(path.join(data.run, 'coverage-ledger.json'), JSON.stringify([...data.ledger, pending]));
  fs.writeFileSync(path.join(data.run, 'findings.json'), JSON.stringify(data.findings));
  const counts = render(data.run);
  assert.equal(counts.confirmed, 1);
  assert.equal(counts.pending, 1);
  const report = fs.readFileSync(path.join(data.run, 'REPORT.md'), 'utf8');
  assert.match(report, /1 pending/);
  assert.match(report, /Open coverage/);
  assert.match(report, /U\\-2 \(pending\): review class methods; section Methods/);
  assert.match(report, /data availability statement/);
  assert.match(report, /does not predict an editorial decision/);
  assert.match(report, /No verified AI policy was recorded/);
  const plan = fs.readFileSync(path.join(data.run, 'REVISION-PLAN.md'), 'utf8');
  assert.match(plan, /F\\-1 \(major\)/);
  assert.match(plan, /Suggested change: Add a data availability statement/);
  assert.match(plan, /0 unresolved finding\(s\)/);
  assert.throws(() => render(data.run), /already exists/);
});

test('report quotes a verified journal AI policy with its source', t => {
  const data = fixture(t);
  const guideline = `${fs.readFileSync(path.join(data.run, 'sources', 'guideline.txt'), 'utf8')} Disclose AI-assisted writing.`;
  fs.writeFileSync(path.join(data.run, 'sources', 'guideline.txt'), guideline);
  data.profile.sources.src_guideline.sha256 = crypto.createHash('sha256').update(guideline).digest('hex');
  data.profile.ai_policy = { quote: 'Disclose AI-assisted writing.', source_ref: 'src_guideline' };
  fs.writeFileSync(path.join(data.run, 'run-manifest.json'), JSON.stringify(manifestFor(data)));
  fs.writeFileSync(path.join(data.run, 'journal-profile.json'), JSON.stringify(data.profile));
  fs.writeFileSync(path.join(data.run, 'coverage-ledger.json'), JSON.stringify([{ ...data.ledger[0], status: 'pending', checked_by: [] }]));
  fs.writeFileSync(path.join(data.run, 'findings.json'), '[]');
  render(data.run);
  const report = fs.readFileSync(path.join(data.run, 'REPORT.md'), 'utf8');
  assert.match(report, /Journal AI policy/);
  assert.match(report, /Disclose AI\\-assisted writing\\\./);
  assert.match(report, /https:\/\/example\\\.invalid\/guidelines/);
});

test('report rendering refuses unverified records and escapes untrusted Markdown', t => {
  const data = fixture(t);
  fs.writeFileSync(path.join(data.run, 'run-manifest.json'), JSON.stringify(manifestFor(data)));
  fs.writeFileSync(path.join(data.run, 'journal-profile.json'), JSON.stringify(data.profile));
  fs.writeFileSync(path.join(data.run, 'coverage-ledger.json'), JSON.stringify(data.ledger));
  fs.writeFileSync(path.join(data.run, 'findings.json'), JSON.stringify(data.findings));
  fs.rmSync(path.join(data.run, 'agents', 'defense.json'));
  assert.throws(() => render(data.run), /Run validation failed/);
  assert.equal(fs.existsSync(path.join(data.run, 'REPORT.md')), false);
  assert.equal(fs.existsSync(path.join(data.run, 'REVISION-PLAN.md')), false);
  assert.equal(escapeMarkdown('![image](https://example.org)'), '\\!\\[image\\]\\(https://example\\.org\\)');
});

test('revision comparison shows record and coverage changes without inferring resolution', t => {
  const previous = fixture(t);
  const current = fixture(t);
  fs.appendFileSync(path.join(current.run, 'manuscript.md'), 'Additional background.\n');
  const writeRun = (data, findings, ledger) => {
    fs.writeFileSync(path.join(data.run, 'run-manifest.json'), JSON.stringify(manifestFor(data)));
    fs.writeFileSync(path.join(data.run, 'journal-profile.json'), JSON.stringify(data.profile));
    fs.writeFileSync(path.join(data.run, 'coverage-ledger.json'), JSON.stringify(ledger));
    fs.writeFileSync(path.join(data.run, 'findings.json'), JSON.stringify(findings));
  };
  writeRun(previous, previous.findings, previous.ledger);
  writeRun(current, [], [{ ...current.ledger[0], status: 'pending', checked_by: [],
    source_hash: corpusHash(manifestFor(current)) }]);
  const result = compare(previous.run, current.run);
  assert.equal(result.previousOnly, 1);
  assert.equal(result.currentOnly, 0);
  assert.equal(result.coverageChanges, 1);
  assert.equal(result.manuscriptChanged, true);
  const report = fs.readFileSync(path.join(current.run, 'REVISION-COMPARISON.md'), 'utf8');
  assert.match(report, /not automatically resolved/);
  assert.match(report, /covered → pending/);
  assert.throws(() => compare(previous.run, current.run), /already exists/);
  fs.writeFileSync(path.join(current.run, 'findings.json'), '{invalid');
  assert.throws(() => compare(previous.run, current.run, true));
});
