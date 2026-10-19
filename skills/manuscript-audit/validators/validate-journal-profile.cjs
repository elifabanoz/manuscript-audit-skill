'use strict';

const { allowedKeys, isObject, nonempty, printResult, readJson, requiredString, stringArray } = require('./common.cjs');

const ISSN = /^[0-9]{4}-[0-9]{3}[0-9X]$/;
const SHA256 = /^[a-fA-F0-9]{64}$/;

function validate(profile) {
  const errors = [];
  if (!allowedKeys(profile, ['issn', 'title', 'publisher', 'openalex_source_id', 'article_types', 'scope', 'ai_policy', 'rules', 'sources'], 'profile', errors)) return errors;
  for (const key of ['issn', 'title', 'publisher']) requiredString(profile, key, 'profile', errors);
  if (nonempty(profile.issn) && !ISSN.test(profile.issn)) errors.push('profile.issn has an invalid format');
  if ('openalex_source_id' in profile && !nonempty(profile.openalex_source_id)) errors.push('profile.openalex_source_id must be nonempty');
  stringArray(profile.article_types, 'profile.article_types', errors, { min: 1, unique: true });
  if (!isObject(profile.sources)) errors.push('profile.sources must be an object');
  const sources = isObject(profile.sources) ? profile.sources : {};
  if (Object.keys(sources).length === 0) errors.push('profile.sources must contain at least one source');
  for (const [id, source] of Object.entries(sources)) {
    const at = `profile.sources.${id}`;
    if (!nonempty(id)) errors.push('source IDs must be nonempty');
    if (!allowedKeys(source, ['url', 'retrieved_at', 'sha256', 'path'], at, errors)) continue;
    for (const key of ['url', 'retrieved_at', 'sha256', 'path']) requiredString(source, key, at, errors);
    try {
      if (new URL(source.url).protocol !== 'https:') errors.push(`${at}.url must use HTTPS`);
    } catch {
      errors.push(`${at}.url must be a valid HTTPS URL`);
    }
    if (nonempty(source.retrieved_at) && (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(source.retrieved_at) || Number.isNaN(Date.parse(source.retrieved_at)))) {
      errors.push(`${at}.retrieved_at must be an ISO date-time`);
    }
    if (nonempty(source.sha256) && !SHA256.test(source.sha256)) errors.push(`${at}.sha256 must be a SHA-256 hex digest`);
  }
  function quoted(item, at) {
    if (!allowedKeys(item, ['quote', 'source_ref'], at, errors)) return;
    requiredString(item, 'quote', at, errors);
    requiredString(item, 'source_ref', at, errors);
    if (nonempty(item.source_ref) && !Object.hasOwn(sources, item.source_ref)) errors.push(`${at}.source_ref is absent from sources`);
  }
  for (const key of ['scope', 'ai_policy']) if (key in profile) quoted(profile[key], `profile.${key}`);
  if (!Array.isArray(profile.rules)) {
    errors.push('profile.rules must be an array');
    return errors;
  }
  const ids = new Set();
  for (const [index, rule] of profile.rules.entries()) {
    const at = `profile.rules[${index}]`;
    if (!allowedKeys(rule, ['rule_id', 'kind', 'check', 'requirement', 'applies_to', 'quote', 'source_ref'], at, errors)) continue;
    for (const key of ['rule_id', 'quote', 'source_ref']) requiredString(rule, key, at, errors);
    if (nonempty(rule.rule_id)) {
      if (ids.has(rule.rule_id)) errors.push(`${at}.rule_id is duplicated`);
      ids.add(rule.rule_id);
    }
    if (!['deterministic', 'semantic'].includes(rule.kind)) errors.push(`${at}.kind is invalid`);
    if (nonempty(rule.source_ref) && !Object.hasOwn(sources, rule.source_ref)) errors.push(`${at}.source_ref is absent from sources`);
    if ('applies_to' in rule) {
      stringArray(rule.applies_to, `${at}.applies_to`, errors, { min: 1, unique: true });
      if (Array.isArray(rule.applies_to) && Array.isArray(profile.article_types)) {
        for (const type of rule.applies_to) if (!profile.article_types.includes(type)) errors.push(`${at}.applies_to includes an unknown article type`);
      }
    }
    if (rule.kind === 'deterministic') {
      if ('requirement' in rule) errors.push(`${at}.requirement is only for semantic rules`);
      if (allowedKeys(rule.check, ['metric', 'op', 'value', 'heading'], `${at}.check`, errors)) {
        requiredString(rule.check, 'metric', `${at}.check`, errors);
        if (!['<', '<=', '=', '>=', '>'].includes(rule.check.op)) errors.push(`${at}.check.op is invalid`);
        if (typeof rule.check.value !== 'number' || !Number.isFinite(rule.check.value)) errors.push(`${at}.check.value must be finite`);
        if (rule.check.metric === 'heading_count') requiredString(rule.check, 'heading', `${at}.check`, errors);
        else if ('heading' in rule.check) errors.push(`${at}.check.heading is only for heading_count`);
      }
    }
    if (rule.kind === 'semantic') {
      if ('check' in rule) errors.push(`${at}.check is only for deterministic rules`);
      requiredString(rule, 'requirement', at, errors);
    }
  }
  return errors;
}

if (require.main === module) {
  if (process.argv.length !== 3) {
    process.stderr.write('Usage: node validate-journal-profile.cjs <profile.json>\n');
    process.exitCode = 2;
  } else {
    try { printResult(validate(readJson(process.argv[2]))); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { validate };
