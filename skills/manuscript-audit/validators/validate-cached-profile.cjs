'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { readJson, safeFile } = require('./common.cjs');
const { differences } = require('./compare-profiles.cjs');
const { validate: validateManifest } = require('./validate-run-manifest.cjs');
const { validate: validateProfile } = require('./validate-journal-profile.cjs');
const { verify } = require('./verify-quotes.cjs');
const { outsideGit } = require('./render-report.cjs');

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function validateAgreedProfile(run) {
  run = path.resolve(run);
  outsideGit(run);
  const manifest = readJson(path.join(run, 'run-manifest.json'));
  if (!fs.existsSync(path.join(run, 'journal-profile.json'))) {
    throw new Error('Profile agreement is not verified:\nNo promoted journal-profile.json exists in the control run');
  }
  const profilePath = safeFile(run, 'journal-profile.json');
  const profileBytes = fs.readFileSync(profilePath);
  const profile = JSON.parse(profileBytes.toString('utf8'));
  const errors = [...validateManifest(run, manifest), ...validateProfile(profile), ...verify(run, profile, [])];
  if (canonical(manifest.sources) !== canonical(profile.sources)) {
    errors.push('Profile sources differ from the source run manifest');
  }
  const retrieved = Object.values(manifest.sources || {}).map(source => Date.parse(source.retrieved_at));
  if (!retrieved.length || retrieved.some(value => !Number.isFinite(value))) {
    errors.push('Sources need valid retrieval times');
  }
  let comparisonHash = null;
  let comparisonName = null;
  for (const name of fs.readdirSync(run).filter(value => /^profile-comparison-v2-[a-f0-9-]+\.json$/.test(value)).sort()) {
    try {
      const recordBytes = fs.readFileSync(safeFile(run, name));
      const record = JSON.parse(recordBytes.toString('utf8'));
      if (record.schema_version !== 2 || record.status !== 'agreed'
          || !Array.isArray(record.profiles) || record.profiles.length !== 2
          || !Array.isArray(record.differing_fields) || record.differing_fields.length) continue;
      if (record.profiles[0]?.path === record.profiles[1]?.path) continue;
      const drafts = record.profiles.map(item => {
        const bytes = fs.readFileSync(safeFile(run, item.path));
        if (hash(bytes) !== item.sha256) throw new Error('Profile draft hash changed');
        const draft = JSON.parse(bytes.toString('utf8'));
        if (validateProfile(draft).length || verify(run, draft, []).length) throw new Error('Profile draft is invalid');
        return draft;
      });
      if (!differences(...drafts).length && canonical(drafts[0]) === canonical(profile)) {
        comparisonHash = hash(recordBytes);
        comparisonName = name;
        break;
      }
    } catch {
      // A stale comparison cannot authorize cache reuse.
    }
  }
  if (!comparisonHash) errors.push('No current, agreed two-draft profile comparison authorizes reuse');
  if (errors.length) throw new Error(`Profile agreement is not verified:\n${[...new Set(errors)].join('\n')}`);
  return { schema_version: 1, issn: profile.issn, profile_sha256: hash(profileBytes),
    manifest_sha256: hash(fs.readFileSync(path.join(run, 'run-manifest.json'))),
    comparison_record: comparisonName, comparison_sha256: comparisonHash,
    oldest_source_retrieved_at: new Date(Math.min(...retrieved)).toISOString() };
}

function validateCachedProfile(run, ttlDays = 30, now = Date.now()) {
  if (!Number.isInteger(ttlDays) || ttlDays < 1 || ttlDays > 365) {
    throw new Error('TTL must be an integer from 1 to 365 days');
  }
  let agreement;
  try { agreement = validateAgreedProfile(run); }
  catch (error) { throw new Error(`Cached profile is not reusable:\n${error.message}`); }
  const retrieved = Date.parse(agreement.oldest_source_retrieved_at);
  if (retrieved > now + 5 * 60 * 1000 || now - retrieved > ttlDays * 86400000) {
    throw new Error('Cached profile is not reusable:\nAt least one source is outside the cache TTL');
  }
  const { comparison_record, comparison_sha256, ...rest } = agreement;
  return { ...rest, ttl_days: ttlDays };
}

if (require.main === module) {
  try {
    if (process.argv.length < 3 || process.argv.length > 4) throw new Error('Usage: node validate-cached-profile.cjs <source-run> [ttl-days]');
    const ttl = process.argv[3] === undefined ? 30 : Number(process.argv[3]);
    process.stdout.write(`${JSON.stringify(validateCachedProfile(process.argv[2], ttl))}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = { validateAgreedProfile, validateCachedProfile };
