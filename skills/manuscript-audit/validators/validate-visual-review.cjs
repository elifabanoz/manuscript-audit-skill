'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { isObject, nonempty, safeFile } = require('./common.cjs');

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function privateFile(file) {
  if (typeof file !== 'string' || !path.isAbsolute(file)) throw new Error('visual input path must be absolute');
  const resolved = fs.realpathSync(file);
  for (let current = path.dirname(resolved); ; current = path.dirname(current)) {
    if (fs.existsSync(path.join(current, '.git'))) throw new Error('visual input must stay outside Git');
    if (current === path.dirname(current)) break;
  }
  if (!fs.statSync(resolved).isFile()) throw new Error('visual input must be a file');
  return resolved;
}

function lineNumber(value) {
  if (typeof value !== 'string' || !/^L[1-9]\d*$/.test(value)) throw new Error('visual item needs a line ID');
  return Number(value.slice(1));
}

function exactOnce(text, quote) {
  return typeof quote === 'string' && quote.length >= 8 && quote.length <= 800
    && !/[\r\n]/.test(quote) && quote.trim() === quote && text.split(quote).length === 2;
}

function activeMarkdownLines(lines) {
  let start = 0;
  if (lines[0]?.trim() === '---') {
    const closing = lines.findIndex((line, index) => index > 0 && ['---', '...'].includes(line.trim()));
    start = closing < 0 ? lines.length : closing + 1;
  }
  const active = new Set();
  let fence = null;
  for (let index = start; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (fence) {
      if (trimmed.length >= fence.length && [...trimmed].every(character => character === fence[0])) fence = null;
      continue;
    }
    const opening = /^ {0,3}(`{3,}|~{3,})/.exec(lines[index]);
    if (opening) { fence = opening[1]; continue; }
    active.add(index + 1);
  }
  return active;
}

function checkVisualReview(run, finding, evidence, manifest, at, errors) {
  try {
    const packetBytes = fs.readFileSync(safeFile(run, evidence.packet_path));
    if (packetBytes.length > 2_000_000 || hash(packetBytes) !== evidence.packet_sha256) {
      throw new Error('visual packet is oversized or has a changed hash');
    }
    const packet = JSON.parse(packetBytes.toString('utf8'));
    if (!isObject(packet) || Object.keys(packet).sort().join(',') !==
        'items,note,recipe_file,recipe_sha256,run_directory,run_manifest_sha256,schema_version,status'
        || packet.schema_version !== 1 || packet.status !== 'pending_human_visual_review'
        || fs.realpathSync(packet.run_directory) !== fs.realpathSync(run)
        || packet.run_manifest_sha256 !== hash(fs.readFileSync(safeFile(run, 'run-manifest.json')))
        || !Array.isArray(packet.items) || !packet.items.length || packet.items.length > 100) {
      throw new Error('visual packet is incomplete or stale');
    }
    const recipeFile = privateFile(packet.recipe_file);
    const recipeBytes = fs.readFileSync(recipeFile);
    if (recipeBytes.length > 65_536 || hash(recipeBytes) !== packet.recipe_sha256) {
      throw new Error('visual recipe is oversized or has a changed hash');
    }
    const recipe = JSON.parse(recipeBytes.toString('utf8'));
    if (!isObject(recipe) || recipe.schema_version !== 1 || !Array.isArray(recipe.items)
        || recipe.items.length !== packet.items.length) {
      throw new Error('visual recipe differs from the packet');
    }
    const supplied = new Set([manifest.manuscript, ...(manifest.supplementary || [])].map(item => item.path));
    const ids = new Set();
    let selected = null;
    for (const [index, item] of packet.items.entries()) {
      const input = recipe.items[index];
      if (!isObject(item) || !isObject(input) || !nonempty(item.item_id) || ids.has(item.item_id)
          || item.item_id !== input.item_id || item.kind !== input.kind || item.path !== input.path
          || !supplied.has(item.path) || path.extname(item.path).toLowerCase() !== '.md') {
        throw new Error('visual item differs from its supplied Markdown recipe');
      }
      ids.add(item.item_id);
      const lines = fs.readFileSync(safeFile(run, item.path), 'utf8').split(/\r?\n/);
      const active = activeMarkdownLines(lines);
      if (item.kind === 'figure') {
        const number = lineNumber(item.line_id);
        if (Object.keys(item).sort().join(',') !== 'asset_file,asset_sha256,item_id,kind,line_id,path,quote'
            || item.line_id !== input.line_id || item.quote !== input.quote
            || number > lines.length || !active.has(number) || !/!\[[^\]]*\]\([^)]*\)/.test(lines[number - 1])
            || !exactOnce(lines[number - 1], item.quote)
            || privateFile(item.asset_file) !== privateFile(input.asset_file)) {
          throw new Error('figure marker or asset link differs from its recipe');
        }
        const asset = privateFile(item.asset_file);
        const bytes = fs.readFileSync(asset);
        const signature = path.extname(asset).toLowerCase() === '.png'
          ? bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
          : ['.jpg', '.jpeg'].includes(path.extname(asset).toLowerCase())
            && bytes.subarray(0, 3).equals(Buffer.from([255, 216, 255]));
        if (bytes.length > 50_000_000 || !signature || hash(bytes) !== item.asset_sha256) {
          throw new Error('figure asset has changed or has an unsupported signature');
        }
      } else if (item.kind === 'table') {
        const start = lineNumber(item.start_line);
        const end = lineNumber(item.end_line);
        if (Object.keys(item).sort().join(',') !== 'end_line,item_id,kind,path,quote,span_sha256,start_line'
            || item.start_line !== input.start_line || item.end_line !== input.end_line
            || item.quote !== input.quote || end < start || end > Math.min(lines.length, start + 39)) {
          throw new Error('table span differs from its recipe');
        }
        const selectedLines = lines.slice(start - 1, end);
        const span = selectedLines.join('\n');
        const marker = selectedLines.some((line, index) => active.has(start + index)
          && (/^\s*Table\s+[1-9]\d*\s*[:.\-]/i.test(line)
            || /^\s*\|?\s*:?-{3,}:?\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$/.test(line)));
        if (!marker || !exactOnce(span, item.quote) || hash(Buffer.from(span, 'utf8')) !== item.span_sha256) {
          throw new Error('table marker or span has changed');
        }
      } else {
        throw new Error('visual item kind is invalid');
      }
      if (item.item_id === evidence.item_id) selected = item;
    }
    if (!selected || !finding.evidence.some(item => item.type === 'manuscript_quote'
        && item.path === selected.path && item.quote === selected.quote
        && (selected.kind === 'figure' ? item.line_id === selected.line_id
          : Number(item.line_id?.slice(1)) >= lineNumber(selected.start_line)
            && Number(item.line_id?.slice(1)) <= lineNumber(selected.end_line)))) {
      throw new Error('visual item lacks a matching manuscript quote in the finding');
    }
    if (finding.verdict !== 'confirmed') return;
    if (!nonempty(evidence.review_path) || !/^[a-f0-9]{64}$/.test(evidence.review_sha256 || '')) {
      throw new Error('confirmed visual finding needs a human review record');
    }
    const reviewBytes = fs.readFileSync(safeFile(run, evidence.review_path));
    if (reviewBytes.length > 65_536 || hash(reviewBytes) !== evidence.review_sha256) {
      throw new Error('visual review is oversized or has a changed hash');
    }
    const review = JSON.parse(reviewBytes.toString('utf8'));
    if (!isObject(review) || Object.keys(review).sort().join(',') !==
        'assessment,finding_claim_sha256,item_id,object_checked,packet_sha256,rationale,rendered_manuscript_checked,reviewed_at,reviewer,schema_version'
        || review.schema_version !== 1 || review.packet_sha256 !== evidence.packet_sha256
        || review.item_id !== evidence.item_id || review.finding_claim_sha256 !== hash(Buffer.from(finding.claim, 'utf8'))
        || !nonempty(review.reviewer) || !nonempty(review.rationale)
        || typeof review.reviewed_at !== 'string'
        || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(review.reviewed_at)
        || Number.isNaN(Date.parse(review.reviewed_at))
        || review.rendered_manuscript_checked !== true || review.object_checked !== true
        || review.assessment !== 'defect_supported') {
      throw new Error('confirmed visual finding lacks a complete human visual review');
    }
  } catch (error) {
    errors.push(`${at}: ${error.message}`);
  }
}

module.exports = { checkVisualReview };
