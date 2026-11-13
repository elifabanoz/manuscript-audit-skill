'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { nonempty, readJson, safeFile } = require('./common.cjs');
const { validateCachedProfile } = require('./validate-cached-profile.cjs');
const { validateComposition } = require('./compose-publisher-rules.cjs');

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function validatePublisherCache(run, publisherName, ttlDays = 30, now = Date.now()) {
  if (!nonempty(publisherName)) throw new Error('An exact publisher name is required');
  run = path.resolve(run);
  const agreement = validateCachedProfile(run, ttlDays, now);
  const composition = validateComposition(run);
  const comparison = readJson(safeFile(run, agreement.comparison_record));
  if (comparison.status !== 'agreed' || !comparison.profiles.some(item => item.path === 'composed-profile.json')) {
    throw new Error('Publisher cache needs an agreed comparison involving the composed profile');
  }
  const profile = readJson(safeFile(run, 'journal-profile.json'));
  if (profile.publisher !== publisherName) throw new Error('Publisher identity differs from the requested publisher');
  const publisherFile = safeFile(run, composition.inputs.publisher_rules);
  const publisher = readJson(publisherFile);
  if (!Array.isArray(publisher.rules) || publisher.rules.length === 0) {
    throw new Error('Publisher rule set is empty');
  }
  const manifest = readJson(safeFile(run, 'run-manifest.json'));
  const sources = Object.fromEntries(Object.keys(publisher.sources).map(id => [id, manifest.sources[id]]));
  return {
    schema_version: 1,
    publisher: publisherName,
    source_issn: profile.issn,
    profile_sha256: agreement.profile_sha256,
    composition_sha256: hash(fs.readFileSync(safeFile(run, 'publisher-composition.json'))),
    publisher_rules_path: composition.inputs.publisher_rules,
    publisher_rules_sha256: composition.publisher_rules_sha256,
    ttl_days: ttlDays,
    sources
  };
}

if (require.main === module) {
  if (process.argv.length < 4 || process.argv.length > 5) {
    process.stderr.write('Usage: node validate-publisher-cache.cjs <source-run> <exact-publisher-name> [ttl-days]\n');
    process.exitCode = 2;
  } else {
    try {
      const ttl = process.argv[4] === undefined ? 30 : Number(process.argv[4]);
      process.stdout.write(`${JSON.stringify(validatePublisherCache(process.argv[2], process.argv[3], ttl))}\n`);
    } catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { validatePublisherCache };
