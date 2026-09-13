'use strict';

const { spawnSync } = require('node:child_process');
const { linesOf } = require('./textUtil');

// Best-effort information about how the target executable was built.
// Deliberately does not parse build systems (CMake/Make/etc.); covers only
// the common case: which compiler is on PATH (or named by CXX/CC), and a
// weak heuristic for whether the binary retains debug symbols.

function runCapture(argv) {
  const result = spawnSync(argv[0], argv.slice(1), { encoding: 'utf8' });
  if (result.error) return { spawned: false };
  return {
    spawned: true,
    success: result.status === 0 && !result.signal,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
  };
}

function detectCompiler() {
  const candidates = [];
  if (process.env.CXX) candidates.push(process.env.CXX);
  if (process.env.CC) candidates.push(process.env.CC);
  candidates.push('c++', 'g++', 'clang++', 'cc', 'gcc', 'clang');

  for (const name of candidates) {
    const result = runCapture([name, '--version']);
    if (result.spawned && result.success) {
      const lines = linesOf(result.stdout);
      const firstLine = lines.length > 0 ? lines[0].trim() : '';
      return { compiler: name, compilerVersion: firstLine };
    }
  }
  return { compiler: null, compilerVersion: null };
}

function detectDebugBuild(executable) {
  {
    const result = runCapture(['file', executable]);
    if (result.spawned && result.success) {
      const text = result.stdout.toLowerCase();
      if (text.includes('not stripped')) return true;
      if (text.includes('stripped')) return false;
    }
  }

  {
    const result = runCapture(['nm', '-p', executable]);
    if (result.spawned) {
      const stderrLower = result.stderr.toLowerCase();
      if (stderrLower.includes('no symbols')) return false;
      if (result.success && result.stdout.trim().length > 0) return true;
    }
  }

  return null;
}

/** Detects best-effort build context for `executable`. Never fails: any
 * individual signal that can't be determined is simply null. */
function detectBuildContext(executable) {
  const { compiler, compilerVersion } = detectCompiler();
  return {
    executable_path: executable,
    compiler,
    compiler_version: compilerVersion,
    debug_build: detectDebugBuild(executable),
    build_configuration: null,
  };
}

module.exports = { detectBuildContext };
