'use strict';

const crypto = require('node:crypto');
const path = require('node:path');
const { isObject, nonempty, printResult, readJson, safeFile } = require('./common.cjs');
const { validate: validateManifest } = require('./validate-run-manifest.cjs');
const { validate: validateLedger } = require('./validate-coverage-ledger.cjs');
const { verify } = require('./verify-quotes.cjs');

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (isObject(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

function subjectHash(finding) {
  const subject = {
    target_issn: finding.target_issn,
    rule_id: finding.rule_id ?? null,
    review_class: finding.review_class ?? null,
    claim: finding.claim,
    evidence: finding.evidence
  };
  return crypto.createHash('sha256').update(canonical(subject)).digest('hex');
}

function corpusHash(manifest) {
  const files = [manifest.manuscript, ...(Array.isArray(manifest.supplementary) ? manifest.supplementary : [])];
  const entries = files.map(file => [file.path, file.sha256]).sort((left, right) => left[0].localeCompare(right[0], 'en'));
  if (manifest.extraction) entries.push([`original:${manifest.extraction.format}`, manifest.extraction.source_sha256]);
  entries.sort((left, right) => left[0].localeCompare(right[0], 'en'));
  return crypto.createHash('sha256').update(canonical(entries)).digest('hex');
}

function reviewSubjectHash(finding, manifest, profile) {
  const sources = Object.entries(manifest.sources).map(([id, source]) => [id, source.url, source.retrieved_at, source.sha256]);
  sources.sort((left, right) => left[0].localeCompare(right[0], 'en'));
  return crypto.createHash('sha256').update(canonical({ finding: subjectHash(finding), corpus: corpusHash(manifest), sources, profile })).digest('hex');
}

function coverageSubjectHash(unit, manifest, profile) {
  const subject = {
    unit_id: unit.unit_id,
    kind: unit.kind,
    section: unit.section,
    rule_id: unit.rule_id ?? null,
    review_class: unit.review_class ?? null,
    target_issn: unit.target_issn,
    source_hash: unit.source_hash
  };
  return crypto.createHash('sha256').update(canonical({ subject, corpus: corpusHash(manifest), profile })).digest('hex');
}

function checkCoverage(run, unit, index, manifest, profile, errors) {
  if (unit.status !== 'covered' || !Array.isArray(unit.checked_by)) return;
  for (const file of unit.checked_by) {
    const at = `ledger[${index}].checked_by.${file}`;
    try {
      const record = readJson(safeFile(run, file));
      if (!isObject(record) || Object.keys(record).some(field => !['unit_id', 'decision', 'actor_id', 'subject_sha256', 'rationale'].includes(field))) {
        errors.push(`${at} must be a coverage check record with known fields`);
        continue;
      }
      if (record.unit_id !== unit.unit_id) errors.push(`${at}.unit_id differs from the coverage unit`);
      if (record.decision !== 'checked') errors.push(`${at}.decision must be checked`);
      if (!nonempty(record.actor_id)) errors.push(`${at}.actor_id must be nonempty`);
      if (record.subject_sha256 !== coverageSubjectHash(unit, manifest, profile)) errors.push(`${at}.subject_sha256 differs from the current coverage inputs`);
      if (!nonempty(record.rationale)) errors.push(`${at}.rationale must be nonempty`);
    } catch (error) {
      errors.push(`${at}: ${error.message}`);
    }
  }
}

function checkProvenance(run, finding, index, manifest, profile, errors) {
  if (!isObject(finding.provenance)) return;
  const roles = { hunter: 'hunter', validator: 'defense', record_verifier: 'record_verifier' };
  const actors = new Set();
  for (const [key, role] of Object.entries(roles)) {
    const file = finding.provenance[key];
    if (!nonempty(file)) continue;
    const at = `findings[${index}].provenance.${key}`;
    try {
      const record = readJson(safeFile(run, file));
      if (!isObject(record) || Object.keys(record).some(field => !['role', 'finding_id', 'decision', 'actor_id', 'subject_sha256', 'rationale'].includes(field))) {
        errors.push(`${at} must be an agent record with known fields`);
        continue;
      }
      if (record.role !== role) errors.push(`${at}.role must be ${role}`);
      if (record.finding_id !== finding.id) errors.push(`${at}.finding_id differs from the finding`);
      const decision = role === 'hunter' ? 'candidate' : finding.verdict;
      if (record.decision !== decision) errors.push(`${at}.decision must be ${decision}`);
      if (!nonempty(record.actor_id)) errors.push(`${at}.actor_id must be nonempty`);
      else if (actors.has(record.actor_id)) errors.push(`${at}.actor_id must differ from other roles`);
      else actors.add(record.actor_id);
      if (record.subject_sha256 !== reviewSubjectHash(finding, manifest, profile)) errors.push(`${at}.subject_sha256 differs from the current review inputs`);
      if (!nonempty(record.rationale)) errors.push(`${at}.rationale must be nonempty`);
    } catch (error) {
      errors.push(`${at}: ${error.message}`);
    }
  }
}

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
      if (!isObject(unit)) continue;
      if (unit?.target_issn !== profile.issn) errors.push(`ledger[${index}].target_issn differs from profile.issn`);
      if (unit?.kind === 'compliance') {
        const rule = profile.rules.find(candidate => candidate.rule_id === unit.rule_id);
        if (!rule) errors.push(`ledger[${index}].rule_id is absent from the profile`);
        else if (unit.status === 'covered' && rule.status === 'needs_validation') {
          errors.push(`ledger[${index}].rule_id has not been validated`);
        }
      }
      if ((unit?.section === 'all-supplied-files' || unit?.kind === 'review') && unit.source_hash !== corpusHash(manifest)) errors.push(`ledger[${index}].source_hash is stale for supplied files`);
      checkCoverage(run, unit, index, manifest, profile, errors);
    }
  }
  if (Array.isArray(findings)) {
    for (const [index, finding] of findings.entries()) {
      if (isObject(finding)) checkProvenance(run, finding, index, manifest, profile, errors);
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

module.exports = { corpusHash, coverageSubjectHash, reviewSubjectHash, subjectHash, validate };
