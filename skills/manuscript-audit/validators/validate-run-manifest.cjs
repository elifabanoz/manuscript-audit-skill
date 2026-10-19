'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { allowedKeys, isObject, nonempty, printResult, readJson, safeFile } = require('./common.cjs');

const SHA256 = /^[a-f0-9]{64}$/;
const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

function checkTime(value, at, errors) {
  if (typeof value !== 'string' || !DATE_TIME.test(value) || Number.isNaN(Date.parse(value))) {
    errors.push(`${at} must be an ISO date-time with a timezone`);
  }
}

function checkCachedFile(root, record, at, errors) {
  if (!allowedKeys(record, ['path', 'sha256'], at, errors)) return;
  if (!nonempty(record.path)) errors.push(`${at}.path must be nonempty`);
  if (typeof record.sha256 !== 'string' || !SHA256.test(record.sha256)) {
    errors.push(`${at}.sha256 must be a SHA-256 hex digest`);
    return;
  }
  try {
    const file = safeFile(root, record.path);
    const actual = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    if (actual !== record.sha256) errors.push(`${at} SHA-256 mismatch`);
  } catch (error) {
    errors.push(`${at}: ${error.message}`);
  }
}

function validate(run, manifest) {
  const errors = [];
  if (!allowedKeys(manifest, ['schema_version', 'created_at', 'host', 'models', 'skill_sha256', 'prompt_hashes', 'config_hash', 'data_freeze', 'cross_host', 'manuscript', 'sources'], 'manifest', errors)) return errors;
  if (manifest.schema_version !== 1) errors.push('manifest.schema_version must be 1');
  checkTime(manifest.created_at, 'manifest.created_at', errors);
  if (!['codex', 'claude-code', 'manual'].includes(manifest.host)) errors.push('manifest.host is invalid');
  if (!Array.isArray(manifest.models) || manifest.models.some(model => !nonempty(model))) errors.push('manifest.models must be a string array');
  if (typeof manifest.skill_sha256 !== 'string' || !SHA256.test(manifest.skill_sha256)) errors.push('manifest.skill_sha256 must be a SHA-256 hex digest');
  if (!isObject(manifest.prompt_hashes) || manifest.prompt_hashes['SKILL.md'] !== manifest.skill_sha256) errors.push('manifest.prompt_hashes must include the skill hash');
  if (manifest.config_hash !== null && (typeof manifest.config_hash !== 'string' || !SHA256.test(manifest.config_hash))) errors.push('manifest.config_hash must be null or a SHA-256 hex digest');
  if (manifest.data_freeze !== null) errors.push('manifest.data_freeze must be null for a local run');
  if (manifest.cross_host !== false) errors.push('manifest.cross_host must be false for a local run');
  checkCachedFile(run, manifest.manuscript, 'manifest.manuscript', errors);
  if (!isObject(manifest.sources) || Object.keys(manifest.sources).length === 0) {
    errors.push('manifest.sources must contain at least one source');
  } else {
    for (const [id, source] of Object.entries(manifest.sources)) {
      const at = `manifest.sources.${id}`;
      if (!allowedKeys(source, ['url', 'retrieved_at', 'sha256', 'path'], at, errors)) continue;
      try {
        if (new URL(source.url).protocol !== 'https:') errors.push(`${at}.url must use HTTPS`);
      } catch {
        errors.push(`${at}.url must be a valid HTTPS URL`);
      }
      checkTime(source.retrieved_at, `${at}.retrieved_at`, errors);
      checkCachedFile(run, { path: source.path, sha256: source.sha256 }, at, errors);
    }
  }
  return errors;
}

if (require.main === module) {
  if (process.argv.length !== 3) {
    process.stderr.write('Usage: node validate-run-manifest.cjs <run-directory>\n');
    process.exitCode = 2;
  } else {
    try {
      const run = path.resolve(process.argv[2]);
      printResult(validate(run, readJson(path.join(run, 'run-manifest.json'))));
    } catch (error) {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
    }
  }
}

module.exports = { validate };
