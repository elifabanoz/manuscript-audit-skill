'use strict';

const { allowedKeys, isObject, nonempty, printResult, readJson, requiredString, stringArray } = require('./common.cjs');
const { validate: validateProfile } = require('./validate-journal-profile.cjs');

function validate(findings, profile) {
  const errors = [];
  if (!Array.isArray(findings)) return ['findings must be an array'];
  if (profile !== undefined) errors.push(...validateProfile(profile).map(error => `profile: ${error}`));
  if (errors.length) return errors;
  const ids = new Set();
  const rules = new Map(Array.isArray(profile?.rules) ? profile.rules.map(rule => [rule.rule_id, rule]) : []);
  const sources = isObject(profile?.sources) ? profile.sources : {};
  for (const [index, finding] of findings.entries()) {
    const at = `findings[${index}]`;
    if (!allowedKeys(finding, ['id', 'verdict', 'category', 'severity', 'target_issn', 'rule_id', 'review_class', 'claim', 'evidence', 'unresolved_fact', 'rejection_reason', 'suggested_fix', 'provenance'], at, errors)) continue;
    for (const key of ['id', 'target_issn', 'claim']) requiredString(finding, key, at, errors);
    if (nonempty(finding.id) && !/^F-[0-9]+$/.test(finding.id)) errors.push(`${at}.id is invalid`);
    if (nonempty(finding.id)) {
      if (ids.has(finding.id)) errors.push(`${at}.id is duplicated`);
      ids.add(finding.id);
    }
    if (!['confirmed', 'needs_validation', 'rejected'].includes(finding.verdict)) errors.push(`${at}.verdict is invalid`);
    if (!['compliance', 'review', 'reference'].includes(finding.category)) errors.push(`${at}.category is invalid`);
    if (nonempty(finding.target_issn) && !/^[0-9]{4}-[0-9]{3}[0-9X]$/.test(finding.target_issn)) errors.push(`${at}.target_issn is invalid`);
    if (profile && finding.target_issn !== profile.issn) errors.push(`${at}.target_issn differs from profile.issn`);
    if ('suggested_fix' in finding) requiredString(finding, 'suggested_fix', at, errors);
    if (finding.category === 'compliance') {
      requiredString(finding, 'rule_id', at, errors);
      if (nonempty(finding.rule_id) && profile && !rules.has(finding.rule_id)) errors.push(`${at}.rule_id is absent from profile`);
    } else if ('rule_id' in finding && profile && !rules.has(finding.rule_id)) {
      errors.push(`${at}.rule_id is absent from profile`);
    }
    if (finding.verdict === 'confirmed' && rules.get(finding.rule_id)?.status === 'needs_validation') {
      errors.push(`${at}.rule_id has not been validated`);
    }
    if (finding.category === 'review') requiredString(finding, 'review_class', at, errors);
    if ('review_class' in finding && finding.category !== 'review') errors.push(`${at}.review_class requires review category`);
    if (finding.verdict === 'confirmed') {
      if (!['desk_reject', 'major', 'minor', 'note'].includes(finding.severity)) errors.push(`${at}.severity is required for confirmed findings`);
      if ('unresolved_fact' in finding || 'rejection_reason' in finding) errors.push(`${at} cannot have unresolved or rejection fields when confirmed`);
    } else {
      if ('severity' in finding) errors.push(`${at}.severity is only for confirmed findings`);
      if (finding.verdict === 'needs_validation') {
        requiredString(finding, 'unresolved_fact', at, errors);
        if ('rejection_reason' in finding) errors.push(`${at}.rejection_reason is only for rejected findings`);
      }
      if (finding.verdict === 'rejected') {
        requiredString(finding, 'rejection_reason', at, errors);
        if ('unresolved_fact' in finding) errors.push(`${at}.unresolved_fact is only for unresolved findings`);
      }
    }
    if (!allowedKeys(finding.provenance, ['hunter', 'validator', 'record_verifier'], `${at}.provenance`, errors)) {
      continue;
    }
    requiredString(finding.provenance, 'hunter', `${at}.provenance`, errors);
    if (finding.verdict === 'confirmed') {
      for (const key of ['validator', 'record_verifier']) requiredString(finding.provenance, key, `${at}.provenance`, errors);
      const roles = [finding.provenance.hunter, finding.provenance.validator, finding.provenance.record_verifier];
      if (roles.every(nonempty) && new Set(roles).size !== 3) errors.push(`${at}.provenance must name three distinct records`);
    } else {
      for (const key of ['validator', 'record_verifier']) if (key in finding.provenance) requiredString(finding.provenance, key, `${at}.provenance`, errors);
    }
    if (!Array.isArray(finding.evidence) || finding.evidence.length === 0) {
      errors.push(`${at}.evidence must be a nonempty array`);
      continue;
    }
    let guidelineSource = false;
    let manuscriptEvidence = false;
    let manuscriptQuote = false;
    let referenceMetadata = false;
    for (const [eIndex, evidence] of finding.evidence.entries()) {
      const ea = `${at}.evidence[${eIndex}]`;
      if (!isObject(evidence)) { errors.push(`${ea} must be an object`); continue; }
      if (evidence.type === 'guideline') {
        if (!allowedKeys(evidence, ['type', 'source_ref', 'quote'], ea, errors)) continue;
        for (const key of ['source_ref', 'quote']) requiredString(evidence, key, ea, errors);
        if (profile && nonempty(evidence.source_ref) && !Object.hasOwn(sources, evidence.source_ref)) errors.push(`${ea}.source_ref is absent from profile`);
        if (evidence.source_ref === rules.get(finding.rule_id)?.source_ref) guidelineSource = true;
      } else if (evidence.type === 'manuscript_quote') {
        if (!allowedKeys(evidence, ['type', 'path', 'line_id', 'quote'], ea, errors)) continue;
        for (const key of ['path', 'line_id', 'quote']) requiredString(evidence, key, ea, errors);
        if (nonempty(evidence.line_id) && !/^L[1-9][0-9]*$/.test(evidence.line_id)) errors.push(`${ea}.line_id must be a physical line number such as L12`);
        manuscriptEvidence = true;
        manuscriptQuote = true;
      } else if (evidence.type === 'manuscript_absence') {
        if (!allowedKeys(evidence, ['type', 'searched', 'patterns'], ea, errors)) continue;
        stringArray(evidence.searched, `${ea}.searched`, errors, { min: 1, unique: true });
        stringArray(evidence.patterns, `${ea}.patterns`, errors, { min: 1, unique: true });
        manuscriptEvidence = true;
      } else if (evidence.type === 'reference_metadata') {
        if (!allowedKeys(evidence, ['type', 'doi', 'result_index', 'checks_sha256'], ea, errors)) continue;
        requiredString(evidence, 'doi', ea, errors);
        if (!Number.isInteger(evidence.result_index) || evidence.result_index < 0) errors.push(`${ea}.result_index must be a nonnegative integer`);
        if (typeof evidence.checks_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(evidence.checks_sha256)) errors.push(`${ea}.checks_sha256 must be a SHA-256 hex digest`);
        if (finding.category !== 'reference') errors.push(`${ea} is only for reference findings`);
        referenceMetadata = true;
      } else {
        errors.push(`${ea}.type is invalid`);
      }
    }
    if (finding.verdict === 'confirmed') {
      if (finding.category === 'compliance') {
        if (!guidelineSource) errors.push(`${at} needs a guideline quote from the rule's source`);
        if (!manuscriptEvidence) errors.push(`${at} needs manuscript evidence`);
      } else if (!manuscriptQuote) {
        errors.push(`${at} needs a manuscript quote`);
      }
      if (finding.category === 'reference' && !referenceMetadata) {
        errors.push(`${at} needs a private reference metadata check`);
      }
    }
  }
  return errors;
}

if (require.main === module) {
  if (process.argv.length !== 4) {
    process.stderr.write('Usage: node validate-findings.cjs <findings.json> <profile.json>\n');
    process.exitCode = 2;
  } else {
    try { printResult(validate(readJson(process.argv[2]), readJson(process.argv[3]))); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { validate };
