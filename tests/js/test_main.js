'use strict';

// Test suite for the JavaScript port of TraceCLI Part 1 (+ Part 4's CLI
// dispatcher). Mirrors the coverage of the original Rust `#[cfg(test)]`
// modules and the C++ port's `tests/cpp/test_main.cpp`: same scenarios,
// same expected outcomes, translated to this codebase's API.
//
// Uses Node's built-in `node:test` + `node:assert` -- no framework
// dependency, in keeping with the original component's "minimize
// dependencies" rule. Run with `node --test tests/js`.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const { executeTarget } = require('../../src/execution');
const { classify, FailureClassification } = require('../../src/classification');
const { extractFirst, extractAll } = require('../../src/sourceLocation');
const { collectSourceContext } = require('../../src/sourceContext');
const { collectErrorContext } = require('../../src/errorContext');
const { parseArgs, parseDiagnoseArgs } = require('../../src/cli');
const { TraceErrorKind } = require('../../src/errors');
const { signalInfoFromName } = require('../../src/signal');

function shellSpec(script) {
  return { executable: '/bin/sh', arguments: ['-c', script] };
}

function makeTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'tracecli-js-test-'));
}

function writeNumberedLinesFile(dir, name, count) {
  const p = path.join(dir, name);
  let contents = '';
  for (let i = 1; i <= count; i++) contents += `line ${i}\n`;
  fs.writeFileSync(p, contents);
  return p;
}

// ---- execution.js ----

test('successful program is captured', () => {
  const r = executeTarget(shellSpec('exit 0'));
  assert.ok(r.ok);
  assert.equal(r.value.success, true);
  assert.equal(r.value.exit_code, 0);
  assert.equal(r.value.signal, null);
});

test('nonzero exit is captured', () => {
  const r = executeTarget(shellSpec('exit 7'));
  assert.ok(r.ok);
  assert.equal(r.value.success, false);
  assert.equal(r.value.exit_code, 7);
});

test('stdout and stderr are captured separately', () => {
  const r = executeTarget(shellSpec('echo out-line; echo err-line 1>&2'));
  assert.ok(r.ok);
  assert.ok(r.value.stdout.includes('out-line'));
  assert.ok(r.value.stderr.includes('err-line'));
  assert.ok(!r.value.stdout.includes('err-line'));
  assert.ok(!r.value.stderr.includes('out-line'));
});

test('sigsegv is captured', () => {
  const r = executeTarget(shellSpec('kill -SEGV $$'));
  assert.ok(r.ok);
  assert.equal(r.value.success, false);
  assert.equal(r.value.exit_code, null);
  assert.ok(r.value.signal);
  assert.equal(r.value.signal.name, 'SIGSEGV');
});

test('sigabrt is captured', () => {
  const r = executeTarget(shellSpec('kill -ABRT $$'));
  assert.ok(r.ok);
  assert.equal(r.value.success, false);
  assert.ok(r.value.signal);
  assert.equal(r.value.signal.name, 'SIGABRT');
});

test('missing executable is a structured error', () => {
  const r = executeTarget({ executable: '/no/such/path/tracecli-fixture', arguments: [] });
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.ExecutableNotFound);
});

test('invalid working directory is a structured error', () => {
  const spec = shellSpec('exit 0');
  spec.workingDirectory = '/no/such/directory/at/all';
  const r = executeTarget(spec);
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.InvalidWorkingDirectory);
});

test('duration is recorded', () => {
  const r = executeTarget(shellSpec('exit 0'));
  assert.ok(r.ok);
  assert.ok(r.value.duration_ms < 5000);
});

// ---- classification.js ----

test('success short-circuits', () => {
  assert.equal(classify(true, 0, null, ''), FailureClassification.Success);
});

test('segfault from signal', () => {
  const sig = signalInfoFromName('SIGSEGV', false);
  assert.equal(classify(false, null, sig, ''), FailureClassification.SegmentationFault);
});

test('abort from signal', () => {
  const sig = signalInfoFromName('SIGABRT', true);
  assert.equal(classify(false, null, sig, ''), FailureClassification.Abort);
});

test('plain nonzero exit is runtime error', () => {
  assert.equal(classify(false, 1, null, 'oops, bad input'), FailureClassification.RuntimeError);
});

test('compiler diagnostic heuristic matches', () => {
  const stderrText = "parser.cpp:142:17: error: expected ';' before '}' token";
  assert.equal(classify(false, 1, null, stderrText), FailureClassification.CompilationError);
});

test('compiler diagnostic heuristic does not misfire on plain error word', () => {
  const stderrText = 'error: something went wrong at runtime';
  assert.equal(classify(false, 1, null, stderrText), FailureClassification.RuntimeError);
});

// ---- sourceLocation.js ----

test('parses gcc-style diagnostic with column', () => {
  const loc = extractFirst("parser.cpp:142:17: error: expected ';' before '}' token");
  assert.ok(loc);
  assert.equal(loc.file, 'parser.cpp');
  assert.equal(loc.line, 142);
  assert.equal(loc.column, 17);
});

test('parses absolute path without message', () => {
  const loc = extractFirst('/project/src/parser.cpp:142:17');
  assert.ok(loc);
  assert.equal(loc.file, '/project/src/parser.cpp');
  assert.equal(loc.line, 142);
  assert.equal(loc.column, 17);
});

test('parses assert-style without column and extracts function', () => {
  const loc = extractFirst("main.cpp:142: void foo(int): Assertion `x > 0' failed.");
  assert.ok(loc);
  assert.equal(loc.file, 'main.cpp');
  assert.equal(loc.line, 142);
  assert.equal(loc.column, null);
  assert.equal(loc.function, 'void foo(int)');
});

test('parses macOS libc assert message', () => {
  const loc = extractFirst('Assertion failed: (x == 6), function main, file assert_fail.cpp, line 4.');
  assert.ok(loc);
  assert.equal(loc.file, 'assert_fail.cpp');
  assert.equal(loc.line, 4);
  assert.equal(loc.column, null);
  assert.equal(loc.function, 'main');
});

test('ignores lines without a recognized extension', () => {
  assert.equal(extractFirst('this is just some text: not a file'), undefined);
});

test('does not crash on malformed input', () => {
  const garbage = '::::\ncpp.cpp:\n.cpp:abc:def\n:::.cpp:1:2';
  assert.doesNotThrow(() => extractAll(garbage));
});

test('picks first of multiple matches and ignores prose prefix', () => {
  const text =
    'In file included from main.cpp:1:\nparser.cpp:142:17: error: bad token\nlexer.cpp:9:1: note: see here';
  const locs = extractAll(text);
  assert.equal(locs.length, 2);
  assert.equal(locs[0].file, 'parser.cpp');
  assert.equal(locs[1].file, 'lexer.cpp');
});

// ---- sourceContext.js ----

test('extracts bounded window around target line', () => {
  const dir = makeTempDir();
  const p = writeNumberedLinesFile(dir, 'sample.cpp', 200);
  const r = collectSourceContext({ file: p, line: 142 }, dir);
  assert.ok(r.ok);
  assert.equal(r.value.start_line, 137);
  assert.equal(r.value.end_line, 147);
  assert.equal(r.value.highlighted_line, 142);
  assert.equal(r.value.lines.length, 11);
  assert.equal(r.value.lines[0], 'line 137');
  assert.equal(r.value.lines[r.value.lines.length - 1], 'line 147');
});

test('clamps window near start and end of file', () => {
  const dir = makeTempDir();
  const p = writeNumberedLinesFile(dir, 'short.cpp', 5);
  const r = collectSourceContext({ file: p, line: 1 }, dir);
  assert.ok(r.ok);
  assert.equal(r.value.start_line, 1);
  assert.equal(r.value.end_line, 5);
  assert.equal(r.value.lines.length, 5);
});

test('missing file is reported as source file unavailable', () => {
  const dir = makeTempDir();
  const r = collectSourceContext({ file: 'does_not_exist.cpp', line: 10 }, dir);
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.SourceFileUnavailable);
});

test('oversized file is skipped rather than read', () => {
  const dir = makeTempDir();
  const p = writeNumberedLinesFile(dir, 'big.cpp', 10);
  const r = collectSourceContext({ file: p, line: 1 }, dir, { contextLines: 5, maxFileSize: 1 });
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.SourceFileUnavailable);
});

// ---- errorContext.js ----

function quietConfig() {
  return { collectBuildContext: false };
}

test('successful run classifies as success', () => {
  const exec = executeTarget(shellSpec('exit 0'));
  assert.ok(exec.ok);
  const ctx = collectErrorContext(exec.value, quietConfig());
  assert.ok(ctx.ok);
  assert.equal(ctx.value.classification, FailureClassification.Success);
  assert.equal(ctx.value.source_location, null);
});

test('segfault run classifies correctly', () => {
  const exec = executeTarget(shellSpec('kill -SEGV $$'));
  assert.ok(exec.ok);
  const ctx = collectErrorContext(exec.value, quietConfig());
  assert.ok(ctx.ok);
  assert.equal(ctx.value.classification, FailureClassification.SegmentationFault);
});

test('source location and context are populated when available', () => {
  const dir = makeTempDir();
  const srcPath = path.join(dir, 'broken.cpp');
  let contents = '';
  for (let i = 1; i <= 20; i++) contents += `line ${i}\n`;
  fs.writeFileSync(srcPath, contents);

  const spec = shellSpec("echo 'broken.cpp:10:3: error: bad token' 1>&2; exit 1");
  spec.workingDirectory = dir;
  const exec = executeTarget(spec);
  assert.ok(exec.ok);
  const ctx = collectErrorContext(exec.value, quietConfig());
  assert.ok(ctx.ok);
  assert.equal(ctx.value.classification, FailureClassification.CompilationError);
  assert.ok(ctx.value.source_location);
  assert.equal(ctx.value.source_location.line, 10);
  assert.ok(ctx.value.source_context);
  assert.equal(ctx.value.source_context.highlighted_line, 10);
  assert.ok(ctx.value.source_context.lines.includes('line 10'));
});

test('serializes to JSON', () => {
  const exec = executeTarget(shellSpec('exit 3'));
  assert.ok(exec.ok);
  const ctx = collectErrorContext(exec.value, quietConfig());
  assert.ok(ctx.ok);
  const json = JSON.stringify(ctx.value);
  assert.ok(json.includes('"classification"'));
  assert.doesNotThrow(() => JSON.parse(json));
});

test('malformed stderr does not crash or produce a bogus location', () => {
  const exec = executeTarget(shellSpec("printf '::garbage::\\x00\\x01not-a-line\\n' 1>&2; exit 1"));
  assert.ok(exec.ok);
  const ctx = collectErrorContext(exec.value, quietConfig());
  assert.ok(ctx.ok);
  assert.equal(ctx.value.classification, FailureClassification.RuntimeError);
  assert.equal(ctx.value.source_location, null);
});

test('unresolvable source file yields no source context', () => {
  const exec = executeTarget(shellSpec("echo 'ghost.cpp:5:1: error: nope' 1>&2; exit 1"));
  assert.ok(exec.ok);
  const ctx = collectErrorContext(exec.value, quietConfig());
  assert.ok(ctx.ok);
  assert.ok(ctx.value.source_location);
  assert.equal(ctx.value.source_context, null);
});

// ---- cli.js ----

test('parses bare executable', () => {
  const r = parseArgs(['run', './program']);
  assert.ok(r.ok);
  assert.equal(r.value.executable, './program');
  assert.deepEqual(r.value.targetArguments, []);
});

test('parses executable with target args', () => {
  const r = parseArgs(['run', './program', 'arg1', 'arg2']);
  assert.ok(r.ok);
  assert.deepEqual(r.value.targetArguments, ['arg1', 'arg2']);
});

test('target args that look like flags pass through untouched', () => {
  const r = parseArgs(['run', './program', '--context-lines', '3']);
  assert.ok(r.ok);
  assert.equal(r.value.contextLines, undefined);
  assert.deepEqual(r.value.targetArguments, ['--context-lines', '3']);
});

test('parses context-lines flag before executable', () => {
  const r = parseArgs(['run', '--context-lines', '8', './program']);
  assert.ok(r.ok);
  assert.equal(r.value.contextLines, 8);
  assert.equal(r.value.executable, './program');
});

test('missing executable is invalid arguments', () => {
  const r = parseArgs(['run']);
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.InvalidArguments);
});

test('unknown command is invalid arguments', () => {
  const r = parseArgs(['debug', './program']);
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.InvalidArguments);
});

test('no command is invalid arguments', () => {
  const r = parseArgs([]);
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.InvalidArguments);
});

test('diagnose parses bare executable', () => {
  const r = parseDiagnoseArgs(['diagnose', './program']);
  assert.ok(r.ok);
  assert.equal(r.value.executable, './program');
  assert.deepEqual(r.value.targetArguments, []);
  assert.equal(r.value.contextLines, undefined);
});

test('diagnose parses context-lines and target args', () => {
  const r = parseDiagnoseArgs(['diagnose', '--context-lines', '8', './program', 'arg1']);
  assert.ok(r.ok);
  assert.equal(r.value.contextLines, 8);
  assert.equal(r.value.executable, './program');
  assert.deepEqual(r.value.targetArguments, ['arg1']);
});

test('diagnose rejects run command', () => {
  const r = parseDiagnoseArgs(['run', './program']);
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.InvalidArguments);
});

test('diagnose missing executable is invalid arguments', () => {
  const r = parseDiagnoseArgs(['diagnose']);
  assert.ok(!r.ok);
  assert.equal(r.error.kind, TraceErrorKind.InvalidArguments);
});
