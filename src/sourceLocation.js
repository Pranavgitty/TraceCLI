'use strict';

const { splitn, linesOf, parseU32, stripPrefix, trimEndChar } = require('./textUtil');

// Extraction of source file/line/column/function references from freeform
// compiler or runtime output. C++ is the only shape required for this
// component (the target programs TraceCLI diagnoses are C++).

const EXTENSIONS = ['.cpp', '.cc', '.cxx', '.c++', '.c', '.h', '.hpp', '.hh', '.hxx'];

function hasRecognizedExtension(token) {
  const lower = token.toLowerCase();
  return EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** Pulls a plausible function name out of assert-style trailer text like
 * " void foo(int): Assertion `x' failed.". Returns undefined for
 * diagnostic-severity words (error/warning/note), which are not function
 * names. */
function extractFunctionHint(rest) {
  const colon = rest.indexOf(':');
  const firstSegment = colon === -1 ? rest : rest.slice(0, colon);
  const candidate = firstSegment.trim();
  if (candidate.length === 0) return undefined;

  const lower = candidate.toLowerCase();
  if (lower.startsWith('error') || lower.startsWith('warning') || lower.startsWith('note')) {
    return undefined;
  }

  return candidate.includes('(') ? candidate : undefined;
}

/** Tries to parse a single line as `file:line[:col][: rest]`. */
function parseLine(rawLine) {
  const line = rawLine.trim();
  if (line.length === 0) return undefined;

  const parts = splitn(line, ':', 4);
  if (parts.length < 2) return undefined;

  const fileToken = parts[0];
  if (fileToken.length < 3 || /\s/.test(fileToken) || !hasRecognizedExtension(fileToken)) {
    return undefined;
  }

  const lineNo = parseU32(parts[1].trim());
  if (lineNo === undefined) return undefined;

  let column;
  let restIndex = 2;
  if (parts.length > 2) {
    const col = parseU32(parts[2].trim());
    if (col !== undefined) {
      column = col;
      restIndex = 3;
    }
  }

  const func = restIndex < parts.length ? extractFunctionHint(parts[restIndex]) : undefined;

  return {
    file: fileToken,
    line: lineNo,
    column: column ?? null,
    function: func ?? null,
  };
}

/** Parses the BSD/macOS libc `assert()` message shape, comma-separated as
 * `Assertion failed: (cond), function NAME, file FILE, line N.` (column
 * occasionally present too). Unrelated to the glibc colon-joined shape
 * handled by `parseLine`. */
function parseAssertStyle(line) {
  let file;
  let lineNo;
  let column;
  let func;

  for (const rawSegment of line.split(',')) {
    const segment = rawSegment.trim();
    let rest;
    if ((rest = stripPrefix(segment, 'file ')) !== undefined) {
      if (hasRecognizedExtension(rest)) file = rest;
    } else if ((rest = stripPrefix(segment, 'line ')) !== undefined) {
      lineNo = parseU32(trimEndChar(rest, '.').trim());
    } else if ((rest = stripPrefix(segment, 'column ')) !== undefined) {
      column = parseU32(trimEndChar(rest, '.').trim());
    } else if ((rest = stripPrefix(segment, 'function ')) !== undefined) {
      func = trimEndChar(rest, '.').trim();
    }
  }

  if (file === undefined || lineNo === undefined) return undefined;

  return {
    file,
    line: lineNo,
    column: column ?? null,
    function: func ?? null,
  };
}

/** Returns every location found in `text`, in the order encountered. */
function extractAll(text) {
  const result = [];
  for (const line of linesOf(text)) {
    const loc = parseLine(line) ?? parseAssertStyle(line);
    if (loc) result.push(loc);
  }
  return result;
}

/** Returns the first (typically most relevant) location found, if any. */
function extractFirst(text) {
  const all = extractAll(text);
  return all.length > 0 ? all[0] : undefined;
}

module.exports = { extractAll, extractFirst, extractFunctionHint };
