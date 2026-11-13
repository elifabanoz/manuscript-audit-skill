'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { isObject, nonempty, safeFile } = require('./common.cjs');

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function privateSource(file) {
  if (typeof file !== 'string' || !path.isAbsolute(file)) throw new Error('cited source path must be absolute');
  const resolved = fs.realpathSync(file);
  for (let current = path.dirname(resolved); ; current = path.dirname(current)) {
    if (fs.existsSync(path.join(current, '.git'))) throw new Error('cited source must stay outside Git');
    if (current === path.dirname(current)) break;
  }
  if (!fs.statSync(resolved).isFile() || !/\.(md|txt)$/i.test(resolved)) {
    throw new Error('cited source must be a private UTF-8 text file');
  }
  return resolved;
}

function checkCitationClaim(run, finding, evidence, manifest, at, errors) {
  try {
    const packetBytes = fs.readFileSync(safeFile(run, evidence.packet_path));
    if (packetBytes.length > 100_000 || hash(packetBytes) !== evidence.packet_sha256) {
      throw new Error('citation packet is oversized or has a changed hash');
    }
    const packet = JSON.parse(packetBytes.toString('utf8'));
    if (!isObject(packet) || packet.schema_version !== 1 || packet.status !== 'pending_human_citation_review'
        || packet.run_manifest_sha256 !== hash(fs.readFileSync(safeFile(run, 'run-manifest.json')))
        || typeof packet.cited_doi !== 'string' || !/^10\.\d{4,9}\/\S+$/i.test(packet.cited_doi)
        || !isObject(packet.claim) || !isObject(packet.source)
        || !nonempty(packet.claim.path) || !/^L[1-9]\d*$/.test(packet.claim.line_id)
        || !nonempty(packet.claim.quote) || !nonempty(packet.source.quote)
        || packet.claim.quote_sha256 !== hash(Buffer.from(packet.claim.quote, 'utf8'))
        || packet.source.quote_sha256 !== hash(Buffer.from(packet.source.quote, 'utf8'))) {
      throw new Error('citation packet is incomplete or stale');
    }
    const supplied = [manifest.manuscript, ...(manifest.supplementary || [])].map(item => item.path);
    if (!supplied.includes(packet.claim.path)
        || !finding.evidence.some(item => item.type === 'manuscript_quote'
          && item.path === packet.claim.path && item.line_id === packet.claim.line_id
          && item.quote === packet.claim.quote)) {
      throw new Error('citation packet does not match a manuscript quote in the finding');
    }
    const manuscript = fs.readFileSync(safeFile(run, packet.claim.path), 'utf8');
    const line = manuscript.split(/\r?\n/)[Number(packet.claim.line_id.slice(1)) - 1];
    if (!line || line.split(packet.claim.quote).length !== 2) {
      throw new Error('citation claim quote has changed or is ambiguous');
    }
    const escapedDoi = packet.cited_doi.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const doiPattern = new RegExp(`(^|[^A-Za-z0-9])${escapedDoi}(?![A-Za-z0-9/._-])`, 'i');
    if (![manifest.manuscript, ...(manifest.supplementary || [])].some(item =>
      doiPattern.test(fs.readFileSync(safeFile(run, item.path), 'utf8')))) {
      throw new Error('citation DOI is absent from the supplied corpus');
    }
    const sourceFile = privateSource(packet.source.file);
    const sourceBytes = fs.readFileSync(sourceFile);
    if (sourceBytes.length > 20_000_000 || hash(sourceBytes) !== packet.source.file_sha256
        || [manifest.manuscript, ...(manifest.supplementary || [])].some(item =>
          sourceFile === fs.realpathSync(safeFile(run, item.path)))) {
      throw new Error('cited source has changed or is the manuscript');
    }
    const sourceText = sourceBytes.toString('utf8');
    if (sourceText.split(packet.source.quote).length !== 2) {
      throw new Error('cited source quote has changed or is ambiguous');
    }
    if (finding.verdict !== 'confirmed') return;
    if (!nonempty(evidence.review_path) || !/^[a-f0-9]{64}$/.test(evidence.review_sha256 || '')) {
      throw new Error('confirmed citation claim needs a human review record');
    }
    const reviewBytes = fs.readFileSync(safeFile(run, evidence.review_path));
    if (reviewBytes.length > 65_536 || hash(reviewBytes) !== evidence.review_sha256) {
      throw new Error('citation review is oversized or has a changed hash');
    }
    const review = JSON.parse(reviewBytes.toString('utf8'));
    if (!isObject(review) || Object.keys(review).sort().join(',') !==
        'assessment,citation_link_verified,packet_sha256,rationale,reviewed_at,reviewer,schema_version,source_identity_verified'
        || review.schema_version !== 1 || review.packet_sha256 !== evidence.packet_sha256
        || !nonempty(review.reviewer) || !nonempty(review.rationale)
        || typeof review.reviewed_at !== 'string'
        || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(review.reviewed_at)
        || Number.isNaN(Date.parse(review.reviewed_at))
        || review.source_identity_verified !== true || review.citation_link_verified !== true
        || !['contradicts_claim', 'overstates_source'].includes(review.assessment)) {
      throw new Error('confirmed citation claim lacks a complete human contradiction review');
    }
  } catch (error) {
    errors.push(`${at}: ${error.message}`);
  }
}

module.exports = { checkCitationClaim };
