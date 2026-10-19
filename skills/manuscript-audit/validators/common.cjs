'use strict';

const fs = require('node:fs');
const path = require('node:path');

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function nonempty(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function allowedKeys(value, keys, at, errors) {
  if (!isObject(value)) {
    errors.push(`${at} must be an object`);
    return false;
  }
  for (const key of Object.keys(value)) {
    if (!keys.includes(key)) errors.push(`${at} has an unknown field: ${key}`);
  }
  return true;
}

function requiredString(value, key, at, errors) {
  if (!nonempty(value[key])) errors.push(`${at}.${key} must be a nonempty string`);
}

function stringArray(value, at, errors, { min = 0, unique = false } = {}) {
  if (!Array.isArray(value) || value.length < min || value.some(item => !nonempty(item))) {
    errors.push(`${at} must contain at least ${min} nonempty strings`);
    return;
  }
  if (unique && new Set(value).size !== value.length) errors.push(`${at} contains duplicates`);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function printResult(errors) {
  if (errors.length) {
    for (const error of errors) process.stderr.write(`- ${error}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write('Valid\n');
  }
}

function safeFile(root, relativePath) {
  if (!nonempty(relativePath) || path.isAbsolute(relativePath) || /^[a-zA-Z]:/.test(relativePath)) {
    throw new Error('path must be relative to the run directory');
  }
  const parts = relativePath.replaceAll('\\', '/').split('/');
  if (parts.some(part => part === '..' || part === '' || part === '.')) {
    throw new Error('path must not contain traversal or empty components');
  }
  const base = fs.realpathSync(root);
  const target = fs.realpathSync(path.join(base, ...parts));
  const rel = path.relative(base, target);
  if (rel.startsWith('..' + path.sep) || rel === '..' || path.isAbsolute(rel)) {
    throw new Error('path resolves outside the run directory');
  }
  if (!fs.statSync(target).isFile()) throw new Error('path does not name a file');
  return target;
}

module.exports = { allowedKeys, isObject, nonempty, printResult, readJson, requiredString, safeFile, stringArray };
