'use strict';

// Small string helpers that reproduce specific Rust `&str` semantics
// (`splitn`, `rsplitn`, `.lines()`) exactly, since several parsers ported
// from the Rust implementation depend on their precise edge-case behavior
// (e.g. an empty trailing segment still counting as a "part").

/** Equivalent to Rust's `str::chars().all(is_ascii_digit)`: vacuously
 * true for an empty string -- callers rely on that. */
function allAsciiDigits(s) {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 48 || c > 57) return false;
  }
  return true;
}

/** Equivalent to Rust's `u32::from_str`: the entire trimmed string must be
 * digits (optionally a single leading '+'), and it must fit in 32 bits. */
function parseU32(s) {
  if (s.length === 0) return undefined;
  let i = 0;
  if (s[0] === '+') i = 1;
  if (i >= s.length) return undefined;
  let value = 0;
  for (; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 48 || c > 57) return undefined;
    value = value * 10 + (c - 48);
    if (value > 0xffffffff) return undefined;
  }
  return value;
}

/** Removes every trailing occurrence of `ch`, like Rust's
 * `str::trim_end_matches(char)`. */
function trimEndChar(s, ch) {
  let end = s.length;
  while (end > 0 && s[end - 1] === ch) end -= 1;
  return s.slice(0, end);
}

/** Rust's `str::splitn(n, delim)`: at most `n` parts, left to right; the
 * final part absorbs any remaining delimiters unsplit. */
function splitn(s, delim, n) {
  const parts = [];
  if (n === 0) return parts;
  let remaining = s;
  while (parts.length + 1 < n) {
    const pos = remaining.indexOf(delim);
    if (pos === -1) break;
    parts.push(remaining.slice(0, pos));
    remaining = remaining.slice(pos + 1);
  }
  parts.push(remaining);
  return parts;
}

/** Rust's `str::rsplitn(n, delim)`: at most `n` parts, scanning from the
 * right. Result is ordered rightmost-segment-first; the final (leftmost)
 * part absorbs any remaining delimiters unsplit. */
function rsplitn(s, delim, n) {
  const parts = [];
  if (n === 0) return parts;
  let remaining = s;
  while (parts.length + 1 < n) {
    const pos = remaining.lastIndexOf(delim);
    if (pos === -1) break;
    parts.push(remaining.slice(pos + 1));
    remaining = remaining.slice(0, pos);
  }
  parts.push(remaining);
  return parts;
}

/** Rust's `str::lines()`: splits on '\n', strips a trailing '\r' from each
 * line, and does not yield a trailing empty line for a string ending in
 * '\n'. */
function linesOf(text) {
  const result = [];
  let start = 0;
  while (start <= text.length) {
    const pos = text.indexOf('\n', start);
    if (pos === -1) {
      if (start < text.length) result.push(text.slice(start));
      break;
    }
    let line = text.slice(start, pos);
    if (line.endsWith('\r')) line = line.slice(0, -1);
    result.push(line);
    start = pos + 1;
  }
  return result;
}

function stripPrefix(s, prefix) {
  return s.startsWith(prefix) ? s.slice(prefix.length) : undefined;
}

module.exports = {
  allAsciiDigits,
  parseU32,
  trimEndChar,
  splitn,
  rsplitn,
  linesOf,
  stripPrefix,
};
