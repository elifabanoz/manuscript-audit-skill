'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const { nonempty, printResult, readJson, safeFile } = require('./common.cjs');
const { validate: validateFindings } = require('./validate-findings.cjs');

const normalize = text => text.replace(/\s+/g, ' ').trim();

function verify(runDirectory, profile, findings) {
  const errors = validateFindings(findings, profile);
  if (errors.length) return errors;
  const texts = new Map();
  for (const [id, source] of Object.entries(profile.sources)) {
    try {
      const file = safeFile(runDirectory, source.path);
      const bytes = fs.readFileSync(file);
      const digest = crypto.createHash('sha256').update(bytes).digest('hex');
      if (digest.toLowerCase() !== source.sha256.toLowerCase()) errors.push(`source ${id} has a SHA-256 mismatch`);
      texts.set(id, bytes.toString('utf8'));
    } catch (error) {
      errors.push(`source ${id}: ${error.message}`);
    }
  }
  function checkQuote(sourceRef, quote, at) {
    const text = texts.get(sourceRef);
    if (text !== undefined && !normalize(text).includes(normalize(quote))) errors.push(`${at} is not in source ${sourceRef}`);
  }
  for (const key of ['scope', 'ai_policy']) if (profile[key]) checkQuote(profile[key].source_ref, profile[key].quote, `profile.${key}.quote`);
  for (const [index, rule] of profile.rules.entries()) checkQuote(rule.source_ref, rule.quote, `profile.rules[${index}].quote`);
  for (const [index, finding] of findings.entries()) {
    for (const [eIndex, evidence] of finding.evidence.entries()) {
      const at = `findings[${index}].evidence[${eIndex}]`;
      if (evidence.type === 'guideline') checkQuote(evidence.source_ref, evidence.quote, `${at}.quote`);
      if (evidence.type === 'manuscript_quote') {
        try {
          const lines = fs.readFileSync(safeFile(runDirectory, evidence.path), 'utf8').split(/\r?\n/);
          const lineNumber = Number(evidence.line_id.slice(1));
          if (!lines[lineNumber - 1] || !normalize(lines[lineNumber - 1]).includes(normalize(evidence.quote))) {
            errors.push(`${at}.quote is not on ${evidence.path}:${lineNumber}`);
          }
        } catch (error) {
          errors.push(`${at}: ${error.message}`);
        }
      }
      if (evidence.type === 'manuscript_absence') {
        for (const searched of evidence.searched) {
          try {
            const text = fs.readFileSync(safeFile(runDirectory, searched), 'utf8').toLowerCase();
            for (const pattern of evidence.patterns) {
              if (nonempty(pattern) && text.includes(pattern.toLowerCase())) errors.push(`${at}: "${pattern}" occurs in ${searched}`);
            }
          } catch (error) {
            errors.push(`${at}: ${searched}: ${error.message}`);
          }
        }
      }
    }
  }
  return errors;
}

if (require.main === module) {
  if (process.argv.length !== 5) {
    process.stderr.write('Usage: node verify-quotes.cjs <run-directory> <profile.json> <findings.json>\n');
    process.exitCode = 2;
  } else {
    try { printResult(verify(process.argv[2], readJson(process.argv[3]), readJson(process.argv[4]))); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { verify };
