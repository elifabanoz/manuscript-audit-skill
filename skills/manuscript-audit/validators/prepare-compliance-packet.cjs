'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { readJson, safeFile } = require('./common.cjs');
const { coverageSubjectHash, validate } = require('./validate-run.cjs');
const { outsideGit } = require('./render-report.cjs');

function cachedText(run, record) {
  return { path: record.path, sha256: record.sha256, text: fs.readFileSync(safeFile(run, record.path), 'utf8') };
}

function prepare(run, unitId, articleType) {
  run = path.resolve(run);
  outsideGit(run);
  if (!/^U-[1-9][0-9]*$/.test(unitId)) throw new Error('unit ID must have the form U-N');
  if (typeof articleType !== 'string' || !articleType.trim()) throw new Error('article type is required');
  const manifest = readJson(path.join(run, 'run-manifest.json'));
  const profile = readJson(path.join(run, 'journal-profile.json'));
  const ledger = readJson(path.join(run, 'coverage-ledger.json'));
  const findingsPath = path.join(run, 'findings.json');
  const findings = fs.existsSync(findingsPath) ? readJson(findingsPath) : [];
  const errors = validate(run, manifest, profile, ledger, findings);
  if (errors.length) throw new Error(`Compliance packet input validation failed:\n${errors.join('\n')}`);
  if (!Array.isArray(profile.article_types) || !profile.article_types.includes(articleType)) {
    throw new Error('article type is absent from the journal profile');
  }
  const units = ledger.filter(item => item.unit_id === unitId);
  if (units.length !== 1 || units[0].kind !== 'compliance' || units[0].status !== 'pending') {
    throw new Error('unit ID must identify one pending compliance unit');
  }
  const unit = units[0];
  const rules = profile.rules.filter(item => item.rule_id === unit.rule_id);
  if (rules.length !== 1 || rules[0].status === 'needs_validation'
      || (Array.isArray(rules[0].applies_to) && !rules[0].applies_to.includes(articleType))) {
    throw new Error('unit rule must be verified and applicable to the article type');
  }
  const rule = rules[0];
  const source = manifest.sources[rule.source_ref];
  if (!source) throw new Error('rule source is absent from the run manifest');
  const packet = {
    schema_version: 1,
    role: 'compliance_hunter',
    unit_id: unitId,
    article_type: articleType,
    subject_sha256: coverageSubjectHash(unit, manifest, profile),
    journal: { issn: profile.issn, title: profile.title },
    unit: { unit_id: unit.unit_id, kind: unit.kind, rule_id: unit.rule_id,
      section: unit.section, target_issn: unit.target_issn },
    rule,
    guideline_source: { url: source.url, retrieved_at: source.retrieved_at, ...cachedText(run, source) },
    manuscript: cachedText(run, manifest.manuscript),
    supplementary: (manifest.supplementary || []).map(record => cachedText(run, record))
  };
  if (manifest.extraction) packet.extraction = cachedText(run, manifest.extraction);
  const bytes = Buffer.from(`${JSON.stringify(packet, null, 2)}\n`, 'utf8');
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  const directory = path.join(run, 'hunter-packets');
  fs.mkdirSync(directory, { recursive: true });
  const relative = path.relative(fs.realpathSync(run), fs.realpathSync(directory));
  if (relative.startsWith(`..${path.sep}`) || relative === '..' || path.isAbsolute(relative)) {
    throw new Error('hunter packet directory resolves outside the private run');
  }
  const output = path.join(directory, `${unitId}-${digest.slice(0, 12)}.json`);
  fs.writeFileSync(output, bytes, { flag: 'wx' });
  return { path: output, sha256: digest };
}

if (require.main === module) {
  if (process.argv.length !== 5) {
    process.stderr.write('Usage: node prepare-compliance-packet.cjs <run-directory> <unit-id> <article-type>\n');
    process.exitCode = 2;
  } else {
    try { process.stdout.write(`${JSON.stringify(prepare(process.argv[2], process.argv[3], process.argv[4]))}\n`); }
    catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
  }
}

module.exports = { prepare };
