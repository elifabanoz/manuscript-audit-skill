'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { allowedKeys, nonempty, safeFile } = require('./common.cjs');
const { loadRun } = require('./compare-runs.cjs');
const { outsideGit } = require('./render-report.cjs');

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const normalize = value => value.replace(/\s+/g, ' ').trim();

function buildPacket(run, claimsPath) {
  run = path.resolve(run);
  outsideGit(run);
  const { manifest, profile } = loadRun(run);
  const inputPath = safeFile(run, claimsPath);
  const inputBytes = fs.readFileSync(inputPath);
  if (inputBytes.length > 128 * 1024) throw new Error('Claim input exceeds 128 KiB');
  const input = JSON.parse(inputBytes.toString('utf8'));
  const errors = [];
  if (!allowedKeys(input, ['schema_version', 'claims'], 'input', errors)) throw new Error(errors.join('; '));
  if (input.schema_version !== 1) errors.push('input.schema_version must be 1');
  if (!Array.isArray(input.claims) || input.claims.length < 1 || input.claims.length > 10) {
    errors.push('input.claims must contain one to ten claims');
  }
  const allowedPaths = new Map([manifest.manuscript, ...(manifest.supplementary || [])]
    .map(record => [record.path, record.sha256]));
  for (const [index, claim] of (Array.isArray(input.claims) ? input.claims : []).entries()) {
    const at = `input.claims[${index}]`;
    if (!allowedKeys(claim, ['statement', 'path', 'line_id', 'quote'], at, errors)) continue;
    for (const key of ['statement', 'path', 'line_id', 'quote']) {
      if (!nonempty(claim[key])) errors.push(`${at}.${key} must be a nonempty string`);
    }
    if (nonempty(claim.statement) && claim.statement.length > 500) errors.push(`${at}.statement exceeds 500 characters`);
    if (nonempty(claim.quote) && claim.quote.length > 500) errors.push(`${at}.quote exceeds 500 characters`);
    if (!allowedPaths.has(claim.path)) errors.push(`${at}.path is not a supplied manuscript file`);
    if (typeof claim.line_id !== 'string' || !/^L[1-9][0-9]*$/.test(claim.line_id)) {
      errors.push(`${at}.line_id must have the form L1`);
    }
    if (allowedPaths.has(claim.path) && /^L[1-9][0-9]*$/.test(claim.line_id) && nonempty(claim.quote)) {
      const lines = fs.readFileSync(safeFile(run, claim.path), 'utf8').split(/\r?\n/);
      const line = lines[Number(claim.line_id.slice(1)) - 1];
      if (!line || !normalize(line).includes(normalize(claim.quote))) {
        errors.push(`${at}.quote is not on ${claim.path}:${claim.line_id}`);
      }
    }
  }
  if (errors.length) throw new Error(`Cover letter input validation failed:\n${errors.join('\n')}`);

  const packet = {
    schema_version: 1,
    status: 'pending_author_review',
    note: 'Quoted manuscript lines were checked literally. Their support for each statement and suitability for a cover letter require author review. No scientific claim or submission declaration was generated.',
    target_journal: { title: profile.title, issn: profile.issn },
    run_manifest_sha256: hash(fs.readFileSync(path.join(run, 'run-manifest.json'))),
    claim_input_sha256: hash(inputBytes),
    claims: input.claims.map(claim => ({
      ...claim,
      manuscript_sha256: allowedPaths.get(claim.path)
    }))
  };
  return packet;
}

function prepare(run, claimsPath) {
  run = path.resolve(run);
  const packet = buildPacket(run, claimsPath);
  const output = path.join(run, 'COVER-LETTER-PACKET.json');
  fs.writeFileSync(output, `${JSON.stringify(packet, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  return { path: output, status: packet.status, claims: packet.claims.length };
}

if (require.main === module) {
  if (process.argv.length !== 4) {
    process.stderr.write('Usage: node prepare-cover-letter-packet.cjs <run-directory> <private-claims.json>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(prepare(process.argv[2], process.argv[3]))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { buildPacket, prepare };
