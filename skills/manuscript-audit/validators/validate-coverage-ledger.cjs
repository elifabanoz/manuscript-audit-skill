'use strict';

const { allowedKeys, nonempty, printResult, readJson, requiredString, stringArray } = require('./common.cjs');

function validate(ledger) {
  const errors = [];
  if (!Array.isArray(ledger)) return ['ledger must be an array'];
  const ids = new Set();
  for (const [index, unit] of ledger.entries()) {
    const at = `ledger[${index}]`;
    if (!allowedKeys(unit, ['unit_id', 'kind', 'section', 'rule_id', 'review_class', 'target_issn', 'status', 'checked_by', 'source_hash'], at, errors)) continue;
    for (const key of ['unit_id', 'section', 'target_issn', 'source_hash']) requiredString(unit, key, at, errors);
    if (nonempty(unit.unit_id) && !/^U-[0-9]+$/.test(unit.unit_id)) errors.push(`${at}.unit_id is invalid`);
    if (nonempty(unit.unit_id)) {
      if (ids.has(unit.unit_id)) errors.push(`${at}.unit_id is duplicated`);
      ids.add(unit.unit_id);
    }
    if (!['compliance', 'review'].includes(unit.kind)) errors.push(`${at}.kind is invalid`);
    if (unit.kind === 'compliance') requiredString(unit, 'rule_id', at, errors);
    if ('rule_id' in unit && unit.kind !== 'compliance') errors.push(`${at}.rule_id requires compliance kind`);
    if (unit.kind === 'review') requiredString(unit, 'review_class', at, errors);
    if ('review_class' in unit && unit.kind !== 'review') errors.push(`${at}.review_class requires review kind`);
    if (nonempty(unit.target_issn) && !/^[0-9]{4}-[0-9]{3}[0-9X]$/.test(unit.target_issn)) errors.push(`${at}.target_issn is invalid`);
    if (!['pending', 'covered', 'stale'].includes(unit.status)) errors.push(`${at}.status is invalid`);
    stringArray(unit.checked_by, `${at}.checked_by`, errors, { min: unit.status === 'covered' ? 1 : 0, unique: true });
    if (nonempty(unit.source_hash) && !/^[a-fA-F0-9]{64}$/.test(unit.source_hash)) errors.push(`${at}.source_hash must be a SHA-256 hex digest`);
  }
  return errors;
}

if (require.main === module) {
  if (process.argv.length !== 3) {
    process.stderr.write('Usage: node validate-coverage-ledger.cjs <ledger.json>\n');
    process.exitCode = 2;
  } else {
    try { printResult(validate(readJson(process.argv[2]))); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { validate };
