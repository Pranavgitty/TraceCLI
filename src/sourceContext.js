'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { linesOf } = require('./textUtil');
const { Ok, Err, sourceFileUnavailable } = require('./errors');

// Bounded excerpts of source files around a detected failure location.
// Never loads a whole source tree, and by default never loads a whole
// file either: only a configurable window of lines around the target line
// is captured.

const DEFAULT_CONTEXT_LINES = 5;
const DEFAULT_MAX_FILE_SIZE = 2 * 1024 * 1024;

function resolveSourcePath(file, workingDirectory) {
  if (path.isAbsolute(file)) {
    return fs.existsSync(file) ? file : undefined;
  }
  const candidate = path.join(workingDirectory, file);
  if (fs.existsSync(candidate)) return candidate;
  return fs.existsSync(file) ? file : undefined;
}

/**
 * Resolves `location.file` against `workingDirectory` when not already
 * absolute, then reads a bounded window of lines around `location.line`.
 * Returns `{ ok: false }` when the file cannot be found or is too large --
 * callers treat that as "no context available", not a fatal error.
 */
function collectSourceContext(location, workingDirectory, config = {}) {
  const contextLines = config.contextLines ?? DEFAULT_CONTEXT_LINES;
  const maxFileSize = config.maxFileSize ?? DEFAULT_MAX_FILE_SIZE;

  const resolved = resolveSourcePath(location.file, workingDirectory);
  if (resolved === undefined) return Err(sourceFileUnavailable(location.file));

  let stat;
  try {
    stat = fs.statSync(resolved);
  } catch {
    return Err(sourceFileUnavailable(location.file));
  }
  if (stat.size > maxFileSize) return Err(sourceFileUnavailable(location.file));

  let contents;
  try {
    contents = fs.readFileSync(resolved, 'utf8');
  } catch {
    return Err(sourceFileUnavailable(location.file));
  }

  const allLines = linesOf(contents);
  if (allLines.length === 0) return Err(sourceFileUnavailable(location.file));

  const target = Math.max(location.line, 1);
  const startLine = Math.max(target - contextLines, 1);
  const endLine = Math.min(target + contextLines, allLines.length);

  if (startLine > allLines.length) return Err(sourceFileUnavailable(location.file));

  return Ok({
    file: location.file,
    start_line: startLine,
    end_line: endLine,
    highlighted_line: target,
    lines: allLines.slice(startLine - 1, endLine),
  });
}

module.exports = { collectSourceContext, DEFAULT_CONTEXT_LINES, DEFAULT_MAX_FILE_SIZE };
