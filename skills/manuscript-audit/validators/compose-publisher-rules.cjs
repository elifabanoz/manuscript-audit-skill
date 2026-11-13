'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { isDeepStrictEqual } = require('node:util');
const { allowedKeys, isObject, nonempty, readJson, safeFile } = require('./common.cjs');
const { validate: validateProfile } = require('./validate-journal-profile.cjs');
const { verify } = require('./verify-quotes.cjs');
const { outsideGit } = require('./render-report.cjs');

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function buildComposition(run, publisherPath, journalPath, overridesPath) {
  run = path.resolve(run);
  outsideGit(run);
  const journalFile = safeFile(run, journalPath);
  if (journalFile === path.join(run, 'composed-profile.json')) throw new Error('Journal draft cannot be the composed profile');
  const { validateDraft } = require('./compare-profiles.cjs');
  const { profile: journal } = validateDraft(run, journalPath);
  const manifest = readJson(path.join(run, 'run-manifest.json'));
  const publisherFile = safeFile(run, publisherPath);
  const publisher = readJson(publisherFile);
  const overridesFile = safeFile(run, overridesPath);
  const overrides = readJson(overridesFile);
  const errors = [];
  if (!allowedKeys(publisher, ['schema_version', 'publisher', 'rules', 'sources'], 'publisher rules', errors)) {
    throw new Error(errors.join('\n'));
  }
  if (publisher.schema_version !== 1) errors.push('publisher rules schema_version must be 1');
  if (publisher.publisher !== journal.publisher) errors.push('publisher identity differs from the journal profile');
  if (!isObject(publisher.sources) || !Object.keys(publisher.sources).length) errors.push('publisher sources must be nonempty');
  for (const [id, source] of Object.entries(isObject(publisher.sources) ? publisher.sources : {})) {
    if (!isDeepStrictEqual(source, manifest.sources[id])) {
      errors.push(`publisher source ${id} differs from the run manifest`);
    }
  }
  const publisherProfile = { issn: journal.issn, title: journal.title, publisher: journal.publisher,
    article_types: journal.article_types, rules: publisher.rules, sources: publisher.sources };
  errors.push(...validateProfile(publisherProfile));
  if (!errors.length) errors.push(...verify(run, publisherProfile, []));

  if (!allowedKeys(overrides, ['schema_version', 'overrides'], 'overrides', errors)) {
    throw new Error(errors.join('\n'));
  }
  if (overrides.schema_version !== 1) errors.push('overrides.schema_version must be 1');
  if (!Array.isArray(overrides.overrides)) errors.push('overrides.overrides must be an array');
  if (errors.length) throw new Error(`Publisher composition failed:\n${errors.join('\n')}`);
  const publisherRules = new Map((Array.isArray(publisher.rules) ? publisher.rules : []).map(rule => [rule.rule_id, rule]));
  const journalRules = new Map(journal.rules.map(rule => [rule.rule_id, rule]));
  const collisions = new Set([...publisherRules.keys()].filter(id => journalRules.has(id)));
  const approved = new Set();
  for (const [index, item] of (Array.isArray(overrides.overrides) ? overrides.overrides : []).entries()) {
    const at = `overrides.overrides[${index}]`;
    if (!allowedKeys(item, ['rule_id', 'reason'], at, errors)) continue;
    if (!nonempty(item.rule_id) || !collisions.has(item.rule_id)) errors.push(`${at}.rule_id must name a collision`);
    if (!nonempty(item.reason)) errors.push(`${at}.reason must explain the journal-specific replacement`);
    if (approved.has(item.rule_id)) errors.push(`${at}.rule_id is duplicated`);
    approved.add(item.rule_id);
  }
  for (const id of collisions) if (!approved.has(id)) errors.push(`rule ${id} needs an explicit journal override`);
  if (errors.length) throw new Error(`Publisher composition failed:\n${errors.join('\n')}`);

  const rules = [...publisherRules.values()].map(rule => journalRules.get(rule.rule_id) || rule);
  for (const rule of journal.rules) if (!publisherRules.has(rule.rule_id)) rules.push(rule);
  const profile = { ...journal, rules };
  const validationErrors = [...validateProfile(profile), ...verify(run, profile, [])];
  if (validationErrors.length) throw new Error(`Composed profile is invalid:\n${validationErrors.join('\n')}`);
  const profileBytes = Buffer.from(`${JSON.stringify(profile, null, 2)}\n`, 'utf8');
  const record = {
    schema_version: 1,
    status: 'pending_independent_profile_review',
    inputs: { publisher_rules: publisherPath, journal_draft: journalPath, overrides: overridesPath },
    publisher_rules_sha256: hash(fs.readFileSync(publisherFile)),
    journal_draft_sha256: hash(fs.readFileSync(journalFile)),
    overrides_sha256: hash(fs.readFileSync(overridesFile)),
    composed_profile_sha256: hash(profileBytes),
    inherited_rule_ids: [...publisherRules.keys()].filter(id => !collisions.has(id)),
    overridden_rule_ids: [...collisions],
    journal_only_rule_ids: journal.rules.filter(rule => !publisherRules.has(rule.rule_id)).map(rule => rule.rule_id),
    note: 'Matching rule IDs require explicit replacement. Different IDs can still express conflicting requirements; independent source review is required.'
  };
  return { profileBytes, record };
}

function validateComposition(run) {
  run = path.resolve(run);
  outsideGit(run);
  const record = readJson(safeFile(run, 'publisher-composition.json'));
  if (!isObject(record) || !isObject(record.inputs)) throw new Error('Invalid publisher composition record');
  const { publisher_rules: publisherPath, journal_draft: journalPath, overrides: overridesPath } = record.inputs;
  const expected = buildComposition(run, publisherPath, journalPath, overridesPath);
  if (!isDeepStrictEqual(record, expected.record)) throw new Error('Publisher composition record is stale or changed');
  const profileBytes = fs.readFileSync(safeFile(run, 'composed-profile.json'));
  if (!profileBytes.equals(expected.profileBytes)) throw new Error('Composed profile differs from its current inputs');
  return record;
}

function compose(run, publisherPath, journalPath, overridesPath) {
  run = path.resolve(run);
  const { profileBytes, record } = buildComposition(run, publisherPath, journalPath, overridesPath);
  const output = path.join(run, 'composed-profile.json');
  const recordPath = path.join(run, 'publisher-composition.json');
  if (fs.existsSync(output) || fs.existsSync(recordPath)) throw new Error('Composition output already exists');
  fs.writeFileSync(output, profileBytes, { flag: 'wx' });
  fs.writeFileSync(recordPath, `${JSON.stringify(record, null, 2)}\n`, { flag: 'wx' });
  return { profile: output, record: recordPath, status: record.status };
}

if (require.main === module) {
  if (process.argv.length !== 6) {
    process.stderr.write('Usage: node compose-publisher-rules.cjs <run-directory> <publisher-rules.json> <journal-draft.json> <overrides.json>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(compose(...process.argv.slice(2)))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { compose, validateComposition };
