'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
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

function checkProvenance(run, finding, index, manifest, profile, errors, options = {}) {
  if (!isObject(finding.provenance)) return;
  const roles = { hunter: 'hunter', validator: 'defense', record_verifier: 'record_verifier' };
  const actors = new Set();
  for (const [key, role] of Object.entries(roles)) {
    const file = finding.provenance[key];
    if (!nonempty(file)) continue;
    const at = `findings[${index}].provenance.${key}`;
    if (role === 'record_verifier' && options.pendingVerifierFindingId === finding.id) {
      if (!/^agents\/[A-Za-z0-9_-]+\.json$/.test(file)) errors.push(`${at} must name a future file under agents/`);
      else if (fs.existsSync(path.join(run, file))) errors.push(`${at} already exists; use the full run gate`);
      continue;
    }
    try {
      const record = readJson(safeFile(run, file));
      if (!isObject(record) || Object.keys(record).some(field => !['role', 'finding_id', 'decision', 'actor_id', 'host', 'model', 'subject_sha256', 'rationale'].includes(field))) {
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
      if (record.host !== undefined || record.model !== undefined) {
        if (!['codex', 'claude-code', 'manual'].includes(record.host) || !nonempty(record.model)) {
          errors.push(`${at}.host and .model must identify the actual invocation together`);
        } else {
          if (manifest.host !== 'manual' && record.host !== manifest.host && !manifest.cross_host) {
            errors.push(`${at}.host differs from the local run host`);
          }
          if (!Array.isArray(manifest.models) || !manifest.models.includes(record.model)) {
            errors.push(`${at}.model is absent from manifest.models`);
          }
        }
      }
      if (record.subject_sha256 !== reviewSubjectHash(finding, manifest, profile)) errors.push(`${at}.subject_sha256 differs from the current review inputs`);
      if (!nonempty(record.rationale)) errors.push(`${at}.rationale must be nonempty`);
    } catch (error) {
      errors.push(`${at}: ${error.message}`);
    }
  }
}

function checkReferenceMetadata(run, finding, evidence, at, manifest, suppliedPaths, errors) {
  try {
    const checksFile = safeFile(run, 'reference-metadata-checks.json');
    const checksBytes = fs.readFileSync(checksFile);
    if (crypto.createHash('sha256').update(checksBytes).digest('hex') !== evidence.checks_sha256) {
      errors.push(`${at}.checks_sha256 differs from the private reference checks`);
      return;
    }
    const checks = JSON.parse(checksBytes.toString('utf8'));
    const result = Array.isArray(checks.results) ? checks.results[evidence.result_index] : null;
    if (checks.schema_version !== 1 || checks.corpus_sha256 !== corpusHash(manifest)
        || !result || result.doi?.toLowerCase() !== evidence.doi?.toLowerCase()
        || result.status !== 'needs_review' || !Object.values(result.checks || {}).includes('review')) {
      errors.push(`${at} does not identify a current reference mismatch lead`);
      return;
    }
    const inventoryBytes = fs.readFileSync(safeFile(run, 'doi-inventory.json'));
    const metadataBytes = fs.readFileSync(safeFile(run, 'doi-metadata.json'));
    if (checks.inventory_sha256 !== crypto.createHash('sha256').update(inventoryBytes).digest('hex')
        || checks.metadata_sha256 !== crypto.createHash('sha256').update(metadataBytes).digest('hex')) {
      errors.push(`${at} has stale DOI inventory or Crossref metadata`);
      return;
    }
    const metadata = JSON.parse(metadataBytes.toString('utf8'));
    const registered = Array.isArray(metadata.results) ? metadata.results.find(item =>
      item?.doi?.toLowerCase() === evidence.doi?.toLowerCase()) : null;
    if (metadata.inventory_sha256 !== checks.inventory_sha256
        || metadata.corpus_sha256 !== checks.corpus_sha256
        || registered?.status !== 'registered_in_crossref'
        || registered.registered_doi?.toLowerCase() !== evidence.doi?.toLowerCase()) {
      errors.push(`${at} lacks a matching Crossref registry record`);
      return;
    }
    if (result.checks?.post_publication_update === 'review'
        && (registered.update_metadata_status !== 'reported'
            || !Array.isArray(registered.update_signals) || !registered.update_signals.length)) {
      errors.push(`${at} lacks a Crossref post-publication update signal`);
      return;
    }
    if (!suppliedPaths.has(result.path) || !Number.isInteger(result.start_line)
        || !Number.isInteger(result.end_line) || result.start_line < 1
        || result.end_line < result.start_line || result.end_line > result.start_line + 7) {
      errors.push(`${at} has an invalid reference span`);
      return;
    }
    const lines = fs.readFileSync(safeFile(run, result.path), 'utf8').split(/\r?\n/);
    const span = lines.slice(result.start_line - 1, result.end_line).join('\n');
    if (result.end_line > lines.length || !span.toLowerCase().includes(evidence.doi.toLowerCase())
        || crypto.createHash('sha256').update(span).digest('hex') !== result.reference_span_sha256) {
      errors.push(`${at} has a changed reference span`);
      return;
    }
    const quoted = finding.evidence.some(item => item?.type === 'manuscript_quote'
      && item.path === result.path && /^L[1-9][0-9]*$/.test(item.line_id)
      && Number(item.line_id.slice(1)) >= result.start_line && Number(item.line_id.slice(1)) <= result.end_line);
    if (!quoted) errors.push(`${at} needs a manuscript quote within the selected reference span`);
  } catch (error) {
    errors.push(`${at}: ${error.message}`);
  }
}

function validate(run, manifest, profile, ledger, findings, options = {}) {
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
      if ((unit?.kind === 'compliance' || unit?.kind === 'review') && unit.source_hash !== corpusHash(manifest)) errors.push(`ledger[${index}].source_hash is stale for supplied files`);
      checkCoverage(run, unit, index, manifest, profile, errors);
    }
  }
  if (Array.isArray(findings)) {
    for (const [index, finding] of findings.entries()) {
      if (isObject(finding)) checkProvenance(run, finding, index, manifest, profile, errors, options);
      if (finding?.verdict === 'confirmed' && finding.category === 'compliance' && Array.isArray(ledger)) {
        const checked = ledger.some(unit => unit?.kind === 'compliance'
          && unit.rule_id === finding.rule_id && unit.target_issn === finding.target_issn
          && unit.status === 'covered');
        if (!checked) errors.push(`findings[${index}] has no covered compliance unit for its rule`);
      }
      if (finding?.verdict === 'confirmed' && finding.category === 'review' && Array.isArray(ledger)) {
        const checked = ledger.some(unit => unit?.kind === 'review'
          && unit.review_class === finding.review_class && unit.target_issn === finding.target_issn
          && unit.status === 'covered');
        if (!checked) errors.push(`findings[${index}] has no covered review unit for its class`);
      }
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
        if (evidence?.type === 'reference_metadata') {
          checkReferenceMetadata(run, finding, evidence, at, manifest, suppliedPaths, errors);
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
