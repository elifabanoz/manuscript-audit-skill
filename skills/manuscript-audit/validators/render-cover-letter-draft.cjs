'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { isDeepStrictEqual } = require('node:util');
const { allowedKeys, nonempty, readJson, safeFile } = require('./common.cjs');
const { buildPacket } = require('./prepare-cover-letter-packet.cjs');

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function render(run, claimsPath, reviewPath) {
  run = path.resolve(run);
  const expected = buildPacket(run, claimsPath);
  const packetBytes = fs.readFileSync(safeFile(run, 'COVER-LETTER-PACKET.json'));
  const packet = JSON.parse(packetBytes.toString('utf8'));
  if (!isDeepStrictEqual(packet, expected)) throw new Error('Cover letter packet is stale or changed');
  const review = readJson(safeFile(run, reviewPath));
  const errors = [];
  if (!allowedKeys(review, ['schema_version', 'packet_sha256', 'reviewer', 'reviewed_at', 'approved_claim_indexes', 'acknowledgement'], 'review', errors)) {
    throw new Error(errors.join('; '));
  }
  if (review.schema_version !== 1) errors.push('review.schema_version must be 1');
  if (review.packet_sha256 !== hash(packetBytes)) errors.push('review.packet_sha256 differs from the packet');
  if (!nonempty(review.reviewer) || /[\r\n\x00-\x1f]/.test(review.reviewer)) errors.push('review.reviewer must be a single-line name');
  if (typeof review.reviewed_at !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(review.reviewed_at)
      || !Number.isFinite(Date.parse(review.reviewed_at))) errors.push('review.reviewed_at must be a UTC timestamp');
  const indexes = packet.claims.map((_, index) => index);
  if (!isDeepStrictEqual(review.approved_claim_indexes, indexes)) {
    errors.push('review.approved_claim_indexes must include every claim in order');
  }
  if (review.acknowledgement !== 'I reviewed each claim against its cited manuscript line and approved this wording.') {
    errors.push('review.acknowledgement is required');
  }
  for (const [index, claim] of packet.claims.entries()) {
    if (/[\r\n\x00-\x1f]/.test(claim.statement)) errors.push(`claim ${index} must be a single line`);
  }
  if (errors.length) throw new Error(`Cover letter review validation failed:\n${errors.join('\n')}`);

  const lines = [
    'DRAFT — FINAL AUTHOR REVIEW REQUIRED',
    `Target journal: ${packet.target_journal.title.replace(/[\r\n]+/g, ' ')} (${packet.target_journal.issn})`,
    '',
    'Dear Editor,',
    '',
    ...packet.claims.flatMap((claim, index) => [claim.statement, `[Evidence ${index + 1}: ${claim.path}:${claim.line_id}]`, '']),
    'Sincerely,',
    '[Author signature]',
    '',
    'Evidence and review record: COVER-LETTER-PACKET.json and the private review JSON.',
    'Confirm journal fit, author identity, declarations, and final wording before submission.',
    ''
  ];
  const output = path.join(run, 'COVER-LETTER-DRAFT.txt');
  fs.writeFileSync(output, lines.join('\n'), { encoding: 'utf8', flag: 'wx' });
  return { path: output, status: 'final_author_review_required', claims: packet.claims.length };
}

if (require.main === module) {
  if (process.argv.length !== 5) {
    process.stderr.write('Usage: node render-cover-letter-draft.cjs <run-directory> <private-claims.json> <private-review.json>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(render(process.argv[2], process.argv[3], process.argv[4]))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { render };
