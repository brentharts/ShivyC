#!/bin/bash
# Build CCC (Claude's C Compiler) so run_benchmarks.py can use it as a third
# peer alongside gcc -O0. Requires a Rust toolchain (cargo).
# The benchmark auto-detects ../ccc/target/release/ccc, or set $CCC.
set -e
DIR=${1:-"$(cd "$(dirname "$0")/../.." && pwd)/ccc"}
if ! command -v cargo >/dev/null; then
    echo "cargo not found. Install Rust (https://rustup.rs) or your distro's"
    echo "cargo package, then re-run."; exit 1
fi
if [ ! -d "$DIR" ]; then
    git clone --depth 1 https://github.com/anthropics/claudes-c-compiler "$DIR"
fi
cd "$DIR" && cargo build --release
echo "Built: $DIR/target/release/ccc"
echo "run_benchmarks.py auto-detects it (or export CCC=$DIR/target/release/ccc)"
