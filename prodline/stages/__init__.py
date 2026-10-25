"""Stage implementations (P1.2+).

``source.py`` is the read-only P1.2 implementation: it asserts the four release
version lines plus the registered tauri line (B14), hashes both lockfiles, and
emits a source-evidence document for the build stage.  Build / install /
runtime / quality implementations land in the A/B areas after the P1 contract
close.
"""
