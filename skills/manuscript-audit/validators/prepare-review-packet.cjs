'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { readJson, safeFile } = require('./common.cjs');
const { reviewSubjectHash, validate } = require('./validate-run.cjs');
const { outsideGit } = require('./render-report.cjs');

function cachedText(run, record) {
  return { path: record.path, sha256: record.sha256,
    ...(record.submitted_filename === undefined ? {} : { submitted_filename: record.submitted_filename }),
    text: fs.readFileSync(safeFile(run, record.path), 'utf8') };
}

function prepare(run, findingId, role) {
  run = path.resolve(run);
  outsideGit(run);
  if (!/^F-[0-9]+$/.test(findingId)) throw new Error('finding ID must have the form F-N');
  if (!['defense', 'record_verifier'].includes(role)) throw new Error('role must be defense or record_verifier');
  const manifest = readJson(path.join(run, 'run-manifest.json'));
  const profile = readJson(path.join(run, 'journal-profile.json'));
  const ledger = readJson(path.join(run, 'coverage-ledger.json'));
  const findings = readJson(path.join(run, 'findings.json'));
  const finding = findings.find(item => item.id === findingId);
  if (!finding || findings.filter(item => item.id === findingId).length !== 1) throw new Error('finding ID must identify exactly one finding');
  const provenance = finding.provenance || {};
  if (role === 'defense') {
    if (finding.verdict !== 'needs_validation' || Object.keys(provenance).sort().join(',') !== 'hunter') {
      throw new Error('defense requires a pending finding with only a hunter record');
    }
  } else if (!provenance.hunter || !provenance.validator || !provenance.record_verifier) {
    throw new Error('record verification requires hunter and defense records and a planned verifier path');
  }
  const options = role === 'record_verifier' ? { pendingVerifierFindingId: findingId } : {};
  const errors = validate(run, manifest, profile, ledger, [finding], options);
  if (errors.length) throw new Error(`Review input validation failed:\n${errors.join('\n')}`);

  const subjectSha256 = reviewSubjectHash(finding, manifest, profile);
  const claim = {
    id: finding.id,
    category: finding.category,
    target_issn: finding.target_issn,
    claim: finding.claim,
    evidence: finding.evidence
  };
  if (finding.rule_id) claim.rule_id = finding.rule_id;
  if (finding.review_class) claim.review_class = finding.review_class;
  if (role === 'record_verifier') {
    claim.verdict = finding.verdict;
    if (finding.severity) claim.severity = finding.severity;
    if (finding.unresolved_fact) claim.unresolved_fact = finding.unresolved_fact;
    if (finding.rejection_reason) claim.rejection_reason = finding.rejection_reason;
    if (finding.suggested_fix) claim.suggested_fix = finding.suggested_fix;
  }
  const packet = {
    schema_version: 1,
    role,
    finding_id: findingId,
    subject_sha256: subjectSha256,
    profile,
    manuscript: cachedText(run, manifest.manuscript),
    supplementary: (manifest.supplementary || []).map(record => cachedText(run, record)),
    guideline_sources: Object.fromEntries(Object.entries(manifest.sources).map(([id, source]) => [id, {
      url: source.url,
      retrieved_at: source.retrieved_at,
      ...cachedText(run, source)
    }])),
    finding: claim
  };
  if (manifest.extraction) packet.extraction = cachedText(run, manifest.extraction);
  if (finding.evidence.some(item => item.type === 'reference_metadata')) {
    packet.reference_checks = Object.fromEntries([
      'doi-inventory.json', 'doi-metadata.json', 'reference-metadata-checks.json'
    ].map(file => [file, {
      sha256: crypto.createHash('sha256').update(fs.readFileSync(safeFile(run, file))).digest('hex'),
      record: readJson(safeFile(run, file))
    }]));
  }
  if (role === 'record_verifier') {
    const defense = readJson(safeFile(run, provenance.validator));
    packet.defense_decision = defense.decision;
  }
  const bytes = Buffer.from(`${JSON.stringify(packet, null, 2)}\n`, 'utf8');
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  const directory = path.join(run, 'review-packets');
  fs.mkdirSync(directory, { recursive: true });
  const relative = path.relative(fs.realpathSync(run), fs.realpathSync(directory));
  if (relative.startsWith(`..${path.sep}`) || relative === '..' || path.isAbsolute(relative)) {
    throw new Error('review packet directory resolves outside the private run');
  }
  const output = path.join(directory, `${findingId}-${role}-${digest.slice(0, 12)}.json`);
  fs.writeFileSync(output, bytes, { flag: 'wx' });
  return { path: output, sha256: digest };
}

if (require.main === module) {
  if (process.argv.length !== 5) {
    process.stderr.write('Usage: node prepare-review-packet.cjs <run-directory> <finding-id> <defense|record_verifier>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(prepare(process.argv[2], process.argv[3], process.argv[4]))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { prepare };
