'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { readJson, safeFile } = require('./common.cjs');
const { compare } = require('./compare-profiles.cjs');
const { validate: validateProfile } = require('./validate-journal-profile.cjs');
const { verify } = require('./verify-quotes.cjs');
const { outsideGit } = require('./render-report.cjs');

function adjudicate(run, firstName, secondName, decisionsName, outputName = 'journal-profile.json') {
  run = path.resolve(run);
  outsideGit(run);
  const comparison = compare(run, firstName, secondName);
  const decisionsPath = safeFile(run, decisionsName);
  if (outputName !== path.basename(outputName) || !/^[a-zA-Z0-9][a-zA-Z0-9._-]*\.json$/.test(outputName)) {
    throw new Error('Output must be a JSON file directly inside the private run');
  }
  const outputPath = path.join(run, outputName);
  if (fs.existsSync(outputPath)) throw new Error(`Final profile already exists: ${outputPath}`);
  const decisionsBytes = fs.readFileSync(decisionsPath);
  const decisions = JSON.parse(decisionsBytes.toString('utf8'));
  if (decisions.schema_version !== 1 || !decisions.resolutions || typeof decisions.resolutions !== 'object' || Array.isArray(decisions.resolutions)) {
    throw new Error('Invalid adjudication decisions');
  }
  const first = readJson(safeFile(run, firstName));
  const second = readJson(safeFile(run, secondName));
  if (comparison.differing_fields.some(field => !field.startsWith('rules.') && !['scope', 'ai_policy'].includes(field))) {
    throw new Error('Journal identity or source set differs; adjudication is unsafe');
  }
  const differing = new Set(comparison.differing_fields);
  const supplied = Object.keys(decisions.resolutions);
  if (supplied.length !== differing.size || supplied.some(field => !differing.has(field))) {
    throw new Error('One resolution is required for every differing field, with no extras');
  }
  const profile = { ...first };
  const firstRules = new Map(first.rules.map(rule => [rule.rule_id, rule]));
  const secondRules = new Map(second.rules.map(rule => [rule.rule_id, rule]));

  function choose(field, a, b) {
    const decision = decisions.resolutions[field];
    if (!decision || !['first', 'second', 'omit'].includes(decision.choice) || typeof decision.reason !== 'string' || !decision.reason.trim()) {
      throw new Error(`Invalid resolution: ${field}`);
    }
    if (decision.status && !['verified', 'needs_validation'].includes(decision.status)) throw new Error(`Invalid status: ${field}`);
    if (decision.status && !field.startsWith('rules.')) throw new Error(`Status applies only to rules: ${field}`);
    if (decision.choice === 'omit') {
      if (decision.status) throw new Error(`Omitted field cannot have a status: ${field}`);
      return undefined;
    }
    const selected = decision.choice === 'first' ? a : b;
    if (!selected) throw new Error(`Selected profile does not contain ${field}`);
    if (decision.status === 'verified' && selected.status === 'needs_validation') {
      throw new Error(`Cannot upgrade unresolved rule by adjudication: ${field}`);
    }
    if (field.startsWith('rules.')) return { ...selected, status: decision.status || selected.status || 'verified' };
    return selected;
  }

  for (const field of ['scope', 'ai_policy']) {
    if (differing.has(field)) {
      const selected = choose(field, first[field], second[field]);
      if (selected === undefined) delete profile[field]; else profile[field] = selected;
    }
  }
  profile.rules = [...new Set([...firstRules.keys(), ...secondRules.keys()])].sort().flatMap(id => {
    const field = `rules.${id}`;
    const selected = differing.has(field) ? choose(field, firstRules.get(id), secondRules.get(id)) : firstRules.get(id);
    return selected ? [{ ...selected, status: selected.status || 'verified' }] : [];
  });
  const errors = [...validateProfile(profile), ...verify(run, profile, [])];
  if (errors.length) throw new Error(`Invalid adjudicated profile:\n${[...new Set(errors)].join('\n')}`);
  const bytes = Buffer.from(`${JSON.stringify(profile, null, 2)}\n`);
  fs.writeFileSync(outputPath, bytes, { flag: 'wx' });
  return {
    comparison: comparison.output,
    decisions_sha256: crypto.createHash('sha256').update(decisionsBytes).digest('hex'),
    profile_sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    output: outputPath,
    rules: profile.rules.length,
    unresolved_rules: profile.rules.filter(rule => rule.status === 'needs_validation').length
  };
}

if (require.main === module) {
  if (process.argv.length < 6 || process.argv.length > 7) {
    process.stderr.write('Usage: node adjudicate-profiles.cjs <run-directory> <first-profile.json> <second-profile.json> <decisions.json> [output.json]\n');
    process.exitCode = 2;
  } else {
    try {
      process.stdout.write(`${JSON.stringify(adjudicate(...process.argv.slice(2)))}\n`);
    } catch (error) {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    }
  }
}

module.exports = { adjudicate };
