'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { readJson, safeFile } = require('./common.cjs');
const { validate: validateManifest } = require('./validate-run-manifest.cjs');
const { validate: validateProfile } = require('./validate-journal-profile.cjs');
const { verify } = require('./verify-quotes.cjs');
const { outsideGit } = require('./render-report.cjs');

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function normalizedRule(rule) {
  return { ...rule, applies_to: [...(rule.applies_to || [])].sort() };
}

function differences(first, second) {
  const paths = [];
  for (const field of ['issn', 'title', 'publisher', 'openalex_source_id', 'scope', 'ai_policy', 'sources']) {
    if (canonical(first[field] ?? null) !== canonical(second[field] ?? null)) paths.push(field);
  }
  if (canonical([...first.article_types].sort()) !== canonical([...second.article_types].sort())) paths.push('article_types');
  const firstRules = new Map(first.rules.map(rule => [rule.rule_id, normalizedRule(rule)]));
  const secondRules = new Map(second.rules.map(rule => [rule.rule_id, normalizedRule(rule)]));
  for (const id of [...new Set([...firstRules.keys(), ...secondRules.keys()])].sort()) {
    if (canonical(firstRules.get(id) ?? null) !== canonical(secondRules.get(id) ?? null)) paths.push(`rules.${id}`);
  }
  return paths;
}

function compare(run, firstPath, secondPath, promote = false) {
  run = path.resolve(run);
  outsideGit(run);
  if (firstPath === secondPath) throw new Error('Profiles must use distinct files and independent invocations');
  const manifest = readJson(path.join(run, 'run-manifest.json'));
  const manifestErrors = validateManifest(run, manifest);
  if (manifestErrors.length) throw new Error(`Invalid manifest:\n${manifestErrors.join('\n')}`);
  const files = [safeFile(run, firstPath), safeFile(run, secondPath)];
  if (files[0] === files[1]) throw new Error('Profiles resolve to the same file');
  const bytes = files.map(file => fs.readFileSync(file));
  const profiles = bytes.map(raw => JSON.parse(raw.toString('utf8')));
  for (const [index, profile] of profiles.entries()) {
    const errors = [...validateProfile(profile), ...verify(run, profile, [])];
    if (canonical(profile.sources) !== canonical(manifest.sources)) errors.push('sources differ from the run manifest');
    if (errors.length) throw new Error(`Invalid profile ${index + 1}:\n${[...new Set(errors)].join('\n')}`);
  }
  const hashes = bytes.map(raw => crypto.createHash('sha256').update(raw).digest('hex'));
  const disagreements = differences(...profiles);
  const record = {
    schema_version: 1,
    profiles: [firstPath, secondPath].map((file, index) => ({ path: file, sha256: hashes[index] })),
    status: disagreements.length ? 'needs_validation' : 'agreed',
    differing_fields: disagreements
  };
  const output = path.join(run, `profile-comparison-${hashes[0].slice(0, 12)}-${hashes[1].slice(0, 12)}.json`);
  if (fs.existsSync(output)) throw new Error(`Comparison already exists: ${output}`);
  const finalPath = path.join(run, 'journal-profile.json');
  if (promote && !disagreements.length && fs.existsSync(finalPath)) throw new Error(`Final profile already exists: ${finalPath}`);
  fs.writeFileSync(output, `${JSON.stringify(record, null, 2)}\n`, { flag: 'wx' });
  if (promote && !disagreements.length) fs.writeFileSync(finalPath, bytes[0], { flag: 'wx' });
  return { ...record, output, promoted: promote && !disagreements.length };
}

if (require.main === module) {
  if (process.argv.length < 5 || process.argv.length > 6 || (process.argv[5] && process.argv[5] !== '--promote')) {
    process.stderr.write('Usage: node compare-profiles.cjs <run-directory> <first-profile.json> <second-profile.json> [--promote]\n');
    process.exitCode = 2;
  } else {
    try {
      const result = compare(process.argv[2], process.argv[3], process.argv[4], process.argv[5] === '--promote');
      process.stdout.write(`${JSON.stringify(result)}\n`);
      if (result.status !== 'agreed') process.exitCode = 1;
    } catch (error) {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    }
  }
}

module.exports = { compare, differences };
