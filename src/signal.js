'use strict';

const os = require('node:os');

// Best-effort POSIX signal identification. Node's own `os.constants.signals`
// gives us the *current platform's* correct signal numbers (unlike the
// Rust/C++ ports, which had to hardcode a macOS/BSD table and documented a
// known Linux mismatch for SIGBUS/SIGUSR1/2) -- so this port doesn't
// inherit that limitation.

/** Returns the platform-correct signal number for a name like 'SIGSEGV',
 * or undefined if unknown. */
function signalNumber(name) {
  const num = os.constants.signals[name];
  return typeof num === 'number' ? num : undefined;
}

/** Builds a SignalInfo from a signal *name* as reported by
 * `child_process` (Node reports the name directly, not a raw number).
 * `coreDumped` is best-effort: Node's public API does not expose the
 * WCOREDUMP bit, so this is always `false` -- a documented limitation,
 * not a correctness target. */
function signalInfoFromName(name, coreDumped = false) {
  return {
    number: signalNumber(name) ?? 0,
    name: name ?? null,
    core_dumped: coreDumped,
  };
}

module.exports = { signalNumber, signalInfoFromName };
