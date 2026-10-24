#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arch-gate: K/X/G/P import-boundary checker with a ratchet baseline.

Reads ``arch/architecture.yaml`` (the single classification ledger), walks every
first-party ``.py`` file, parses imports with :mod:`ast` (no import of the code
under test, no third-party scanner), and compares violation counts against
``arch/baseline.json``.  The gate only fails when a forbidden class-to-class edge
grows beyond the committed baseline.

Design commitments:
  * ``ast`` only for import discovery -- indented / delayed / ``try/except``
    imports are all seen, unlike a line-start grep.
  * Unclassified modules are never guessed: an edge touching ``unclassified`` is
    reported separately and is not a violation.  A ``.py`` file that matches no
    ledger rule is a *configuration error* (exit 2), forcing new modules to be
    classified instead of silently escaping the gate.
  * The baseline is only written with an explicit ``--update-baseline`` **and**
    a non-empty ``--reason``; the reason is stored in the baseline file.
  * Stdlib only (PyYAML is an existing project dependency, declared in
    ``pyproject.toml``, used solely to read the YAML ledger).

Exit codes:
  0  within baseline (or baseline updated)
  1  regression: a forbidden pair exceeds its baseline count
  2  configuration / scan error (bad ledger, missing baseline, unclassified file)
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

RECEIPT_SCHEMA = "gugu.arch-receipt/v1"
BASELINE_SCHEMA = "gugu.arch-baseline/v1"
ARCH_SCHEMA = "gugu.architecture/v1"
CLASSES = ("K", "X", "G", "P", "unclassified")
DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".pytest-cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "hermes_agent.egg-info",
    "site-packages",
}


class ConfigError(RuntimeError):
    """The ledger or the repository layout makes the check untrustworthy."""


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ModuleRule:
    id: str
    patterns: tuple[str, ...]
    cls: str
    owner: str
    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]
    note: str = ""
    reason: str = ""


@dataclass
class Architecture:
    path: Path
    sha256: str
    classes: dict[str, dict[str, Any]]
    modules: list[ModuleRule]
    extensions: tuple[str, ...]
    exclude_dirs: set[str]

    def match(self, relpath: str) -> ModuleRule | None:
        # Ordered, first match wins (documented in the ledger).
        for rule in self.modules:
            for pattern in rule.patterns:
                if fnmatch.fnmatchcase(relpath, pattern):
                    return rule
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ConfigError(
            "PyYAML is not importable; it is an existing project dependency "
            "(pyproject.toml). Install project deps before running arch-gate."
        ) from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def load_architecture(path: Path) -> Architecture:
    data = _load_yaml(path)
    schema = data.get("schema")
    if schema != ARCH_SCHEMA:
        raise ConfigError(f"{path}: schema must be {ARCH_SCHEMA!r}, got {schema!r}")

    raw_classes = data.get("classes")
    if not isinstance(raw_classes, dict):
        raise ConfigError(f"{path}: classes must be a mapping")
    classes: dict[str, dict[str, Any]] = {}
    for name, spec in raw_classes.items():
        if name not in CLASSES:
            raise ConfigError(f"{path}: unknown class {name!r} (allowed: {CLASSES})")
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: class {name} must be a mapping")
        allowed = tuple(spec.get("allowed_deps") or ())
        forbidden = tuple(spec.get("forbidden_deps") or ())
        _validate_edge_lists(f"{path}: class {name}", name, allowed, forbidden)
        classes[name] = {**spec, "allowed_deps": allowed, "forbidden_deps": forbidden}
    missing = [c for c in CLASSES if c not in classes]
    if missing:
        raise ConfigError(f"{path}: missing class definitions: {missing}")

    raw_modules = data.get("modules")
    if not isinstance(raw_modules, list) or not raw_modules:
        raise ConfigError(f"{path}: modules must be a non-empty list")
    modules: list[ModuleRule] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw_modules):
        where = f"{path}: modules[{idx}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{where} must be a mapping")
        mid = item.get("id")
        cls = item.get("class")
        if not mid or not isinstance(mid, str):
            raise ConfigError(f"{where}: id is required")
        if mid in seen_ids:
            raise ConfigError(f"{where}: duplicate module id {mid!r}")
        seen_ids.add(mid)
        if cls not in classes:
            raise ConfigError(f"{where}: unknown class {cls!r}")
        patterns = item.get("paths")
        if not isinstance(patterns, list) or not patterns:
            raise ConfigError(f"{where}: paths must be a non-empty list")
        patterns = tuple(str(p) for p in patterns)
        owner = str(item.get("owner") or "")
        if not owner:
            raise ConfigError(f"{where}: owner is required")
        allowed = tuple(item.get("allowed_deps") or ())
        forbidden = tuple(item.get("forbidden_deps") or ())
        _validate_edge_lists(where, cls, allowed, forbidden)
        # Modules must agree with the class default matrix -- one source of truth.
        class_allowed = classes[cls]["allowed_deps"]
        class_forbidden = classes[cls]["forbidden_deps"]
        if set(allowed) != set(class_allowed) or set(forbidden) != set(class_forbidden):
            raise ConfigError(
                f"{where}: allowed/forbidden deps drift from class {cls} "
                f"(class={sorted(class_allowed)}/{sorted(class_forbidden)}, "
                f"module={sorted(allowed)}/{sorted(forbidden)})"
            )
        reason = str(item.get("reason") or "").strip()
        if cls == "unclassified" and not reason:
            raise ConfigError(f"{where}: class unclassified requires a reason")
        modules.append(
            ModuleRule(
                id=mid,
                patterns=patterns,
                cls=cls,
                owner=owner,
                allowed=allowed,
                forbidden=forbidden,
                note=str(item.get("note") or ""),
                reason=reason,
            )
        )

    scan = data.get("scan") or {}
    extensions = tuple(scan.get("extensions") or (".py",))
    exclude = set(DEFAULT_EXCLUDES) | set(scan.get("exclude_dirs") or ())
    return Architecture(
        path=path,
        sha256=_sha256(path),
        classes=classes,
        modules=modules,
        extensions=extensions,
        exclude_dirs=exclude,
    )


def _validate_edge_lists(where: str, cls: str, allowed: Sequence[str], forbidden: Sequence[str]) -> None:
    for name in (*allowed, *forbidden):
        if name not in CLASSES:
            raise ConfigError(f"{where}: unknown dependency class {name!r}")
    overlap = set(allowed) & set(forbidden)
    if overlap:
        raise ConfigError(f"{where}: classes both allowed and forbidden: {sorted(overlap)}")
    if cls == "unclassified":
        if allowed or forbidden:
            raise ConfigError(f"{where}: unclassified modules cannot declare edges")
        return
    decided = set(allowed) | set(forbidden)
    expected = set(CLASSES) - {"unclassified"}
    if decided != expected:
        raise ConfigError(
            f"{where}: every class must be explicitly allowed or forbidden; "
            f"missing={sorted(expected - decided)} unexpected={sorted(decided - expected)}"
        )


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


@dataclass
class ParseError:
    file: str
    line: int
    message: str


@dataclass
class Violation:
    file: str
    line: int
    source_module: str
    source_class: str
    target_module: str
    target_class: str
    import_stmt: str


@dataclass
class ScanResult:
    repo: Path
    commit: str
    arch_sha256: str
    files_scanned: int
    imports_seen: int
    violations: list[Violation] = field(default_factory=list)
    unclassified_edges: dict[str, int] = field(default_factory=dict)
    parse_errors: list[ParseError] = field(default_factory=list)

    def class_counts(self) -> dict[str, int]:
        counts = {pair: 0 for pair in forbidden_pairs()}
        for v in self.violations:
            key = f"{v.source_class}->{v.target_class}"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def module_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in self.violations:
            key = f"{v.source_module}->{v.target_module}"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def top_offenders(self, limit: int = 15) -> list[list[Any]]:
        return [[k.rsplit("->", 1)[0], k.rsplit("->", 1)[1], v]
                for k, v in sorted(self.module_counts().items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def forbidden_pairs() -> list[str]:
    """K->X, K->G, K->P, X->G, X->P, G->P -- the documented forbidden matrix."""
    pairs: list[str] = []
    for src, targets in (
        ("K", ("X", "G", "P")),
        ("X", ("G", "P")),
        ("G", ("P",)),
    ):
        pairs.extend(f"{src}->{t}" for t in targets)
    return pairs


def iter_python_files(repo: Path, arch: Architecture) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(repo):
        # Deterministic order; skip generated/vendored/hidden trees.
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in arch.exclude_dirs and not d.startswith(".")
        )
        for name in sorted(filenames):
            if any(name.endswith(ext) for ext in arch.extensions):
                yield Path(dirpath) / name


def _module_file(repo: Path, dotted: str) -> str | None:
    """Return the repo-relative file backing a dotted module, if it exists."""
    parts = dotted.split(".")
    if not all(parts):
        return None
    pkg = repo.joinpath(*parts)
    if pkg.with_suffix(".py").is_file():
        return pkg.with_suffix(".py").relative_to(repo).as_posix()
    init = pkg / "__init__.py"
    if init.is_file():
        return init.relative_to(repo).as_posix()
    return None


def _resolve_dotted(repo: Path, dotted: str) -> str | None:
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if _module_file(repo, cand) is not None:
            return cand
    return None


def _base_package(relpath: str) -> list[str]:
    parts = list(Path(relpath).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        return parts[:-1]
    return parts[:-1]


def _relative_targets(repo: Path, relpath: str, node: ast.ImportFrom) -> list[str]:
    base = _base_package(relpath)
    level = node.level or 0
    if level <= 0:
        return []
    climb = level - 1
    if climb > len(base):
        return []
    base = base[: len(base) - climb]
    if node.module:
        dotted = ".".join(base + node.module.split("."))
        resolved = _resolve_dotted(repo, dotted)
        return [resolved or dotted]
    out: list[str] = []
    for alias in node.names:
        if alias.name == "*":
            out.append(".".join(base))
            continue
        cand = ".".join(base + [alias.name])
        out.append(_resolve_dotted(repo, cand) or ".".join(base))
    return out


def _node_targets(repo: Path, relpath: str, node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        out = []
        for alias in node.names:
            resolved = _resolve_dotted(repo, alias.name)
            if resolved:
                out.append(resolved)
        return out
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return _relative_targets(repo, relpath, node)
        if not node.module:
            return []
        base = _resolve_dotted(repo, node.module)
        if base is None:
            return []
        out = [base]
        for alias in node.names:
            if alias.name == "*":
                continue
            cand = _resolve_dotted(repo, f"{base}.{alias.name}")
            if cand:
                out.append(cand)
        return out
    return []


def _import_text(node: ast.AST, source: str) -> str:
    try:
        seg = ast.get_source_segment(source, node) or ""
    except Exception:
        seg = ""
    seg = " ".join(seg.split())
    return seg[:160] or type(node).__name__


def _git_commit(repo: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def scan_repo(repo: Path, arch: Architecture) -> ScanResult:
    repo = repo.resolve()
    result = ScanResult(
        repo=repo,
        commit=_git_commit(repo),
        arch_sha256=arch.sha256,
        files_scanned=0,
        imports_seen=0,
    )
    unknown: list[str] = []
    for path in iter_python_files(repo, arch):
        rel = path.relative_to(repo).as_posix()
        rule = arch.match(rel)
        if rule is None:
            unknown.append(rel)
            continue
        result.files_scanned += 1
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.parse_errors.append(ParseError(rel, 0, f"read error: {exc}"))
            continue
        try:
            # Invalid-escape SyntaxWarnings in unrelated test/docstring sources are
            # not ours to fix; keep the gate output readable and deterministic.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source, filename=rel)
        except (SyntaxError, ValueError) as exc:
            line = getattr(exc, "lineno", 0) or 0
            result.parse_errors.append(ParseError(rel, line, f"{type(exc).__name__}: {exc}"))
            continue
        seen: set[tuple[int, str]] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            targets = _node_targets(repo, rel, node)
            if not targets:
                continue
            result.imports_seen += len(targets)
            for dotted in targets:
                target_file = _module_file(repo, dotted)
                if target_file is None:
                    continue  # third-party / stdlib
                target_rule = arch.match(target_file)
                if target_rule is None:
                    unknown.append(target_file)
                    continue
                key = (getattr(node, "lineno", 0), target_rule.id)
                if key in seen:
                    continue
                seen.add(key)
                source_cls = rule.cls
                target_cls = target_rule.cls
                if source_cls == "unclassified" or target_cls == "unclassified":
                    edge = f"{rule.id}->{target_rule.id}({source_cls}->{target_cls})"
                    result.unclassified_edges[edge] = result.unclassified_edges.get(edge, 0) + 1
                    continue
                if target_cls not in rule.allowed and target_cls not in rule.forbidden:
                    raise ConfigError(
                        f"{rel}:{getattr(node, 'lineno', 0)}: {source_cls}->{target_cls} "
                        f"is not decided by module {rule.id!r}"
                    )
                if target_cls in rule.forbidden:
                    result.violations.append(
                        Violation(
                            file=rel,
                            line=getattr(node, "lineno", 0),
                            source_module=rule.id,
                            source_class=source_cls,
                            target_module=target_rule.id,
                            target_class=target_cls,
                            import_stmt=_import_text(node, source),
                        )
                    )
    if unknown:
        unique = sorted(set(unknown))
        head = ", ".join(unique[:20])
        more = "" if len(unique) <= 20 else f" (+{len(unique) - 20} more)"
        raise ConfigError(
            "unclassified Python files/modules found: "
            f"{head}{more}. Add a rule to arch/architecture.yaml (class: unclassified "
            "with a reason is acceptable; guessing is not)."
        )
    result.violations.sort(key=lambda v: (v.file, v.line, v.target_module))
    return result


# --------------------------------------------------------------------------- #
# Baseline / gate
# --------------------------------------------------------------------------- #


@dataclass
class Comparison:
    regressions: list[dict[str, Any]] = field(default_factory=list)
    improvements: list[dict[str, Any]] = field(default_factory=list)
    missing_pairs: list[str] = field(default_factory=list)
    baseline_counts: dict[str, int] = field(default_factory=dict)
    module_regressions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.regressions


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"baseline not found: {path} (seed it with --update-baseline)") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"baseline is not valid JSON: {path}: {exc}") from exc
    if data.get("schema") != BASELINE_SCHEMA:
        raise ConfigError(f"{path}: schema must be {BASELINE_SCHEMA!r}")
    if not isinstance(data.get("counts"), dict):
        raise ConfigError(f"{path}: counts must be a mapping")
    return data


def compare(scan: ScanResult, baseline: dict[str, Any]) -> Comparison:
    base_counts = baseline.get("counts") or {}
    cmp = Comparison(baseline_counts=dict(base_counts))
    current = scan.class_counts()
    for pair in forbidden_pairs():
        cur = int(current.get(pair, 0))
        base = int(base_counts.get(pair, 0))
        if pair not in base_counts:
            cmp.missing_pairs.append(pair)
        if cur > base:
            cmp.regressions.append({"pair": pair, "current": cur, "baseline": base, "delta": cur - base})
        elif cur < base:
            cmp.improvements.append({"pair": pair, "current": cur, "baseline": base, "delta": base - cur})
    # Class pairs are the gate; module pairs are a locator.  A relocated import can
    # keep the class total flat, so surfacing the module delta matters for review.
    base_modules = baseline.get("module_counts") or {}
    for key, cur in scan.module_counts().items():
        base = int(base_modules.get(key, 0))
        if cur > base:
            cmp.module_regressions.append({"pair": key, "current": cur, "baseline": base, "delta": cur - base})
    cmp.module_regressions.sort(key=lambda r: (-r["delta"], r["pair"]))
    return cmp


def _baseline_payload(scan: ScanResult, reason: str) -> dict[str, Any]:
    return {
        "schema": BASELINE_SCHEMA,
        "commit": scan.commit,
        "architecture_sha256": scan.arch_sha256,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason,
        "counts": scan.class_counts(),
        "total": len(scan.violations),
        "module_counts": scan.module_counts(),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def receipt(scan: ScanResult, cmp: Comparison | None, verdict: str) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "commit": scan.commit,
        "architecture_sha256": scan.arch_sha256,
        "verdict": verdict,
        "summary": {
            "files_scanned": scan.files_scanned,
            "imports_resolved": scan.imports_seen,
            "violations": len(scan.violations),
            "parse_errors": len(scan.parse_errors),
            "unclassified_edges": sum(scan.unclassified_edges.values()),
        },
        "counts": scan.class_counts(),
        "module_counts": scan.module_counts(),
        "top_offenders": scan.top_offenders(),
        "regressions": cmp.regressions if cmp else [],
        "improvements": cmp.improvements if cmp else [],
        "module_regressions": cmp.module_regressions if cmp else [],
        "unclassified_edges": dict(sorted(scan.unclassified_edges.items())),
        "parse_error_samples": [
            {"file": e.file, "line": e.line, "message": e.message}
            for e in scan.parse_errors[:20]
        ],
        "violations": [
            {
                "file": v.file,
                "line": v.line,
                "source_module": v.source_module,
                "source_class": v.source_class,
                "target_module": v.target_module,
                "target_class": v.target_class,
                "import": v.import_stmt,
            }
            for v in scan.violations
        ],
    }


def print_text(scan: ScanResult, cmp: Comparison | None, verdict: str, limit: int) -> None:
    print(f"arch-gate: repo={scan.repo} commit={scan.commit}")
    print(f"  ledger sha256={scan.arch_sha256[:16]}… files={scan.files_scanned} "
          f"imports={scan.imports_seen} violations={len(scan.violations)}")
    if scan.parse_errors:
        print(f"  parse errors (skipped, not a gate failure): {len(scan.parse_errors)}")
        for err in scan.parse_errors[:5]:
            print(f"    {err.file}:{err.line}: {err.message}")
    if cmp is not None:
        print("  forbidden pairs (current / baseline):")
        current = scan.class_counts()
        for pair in forbidden_pairs():
            cur = current.get(pair, 0)
            base = cmp.baseline_counts.get(pair, 0)
            if cur > base:
                mark = f"  FAIL +{cur - base}"
            elif cur < base:
                mark = f"  improved -{base - cur}"
            else:
                mark = "  OK"
            print(f"    {pair:<8} {cur:>6} / {base:<6}{mark}")
    else:
        print("  forbidden pair counts:")
        for pair, cur in scan.class_counts().items():
            print(f"    {pair:<8} {cur:>6}")
    if scan.unclassified_edges:
        print("  unclassified edges (not judged):")
        for edge, n in sorted(scan.unclassified_edges.items()):
            print(f"    {edge}: {n}")
    if scan.top_offenders():
        print("  top offenders (source->target: count):")
        for src, dst, n in scan.top_offenders():
            print(f"    {src}->{dst}: {n}")
    if scan.violations and (cmp is None or cmp.ok):
        shown = scan.violations[: max(0, limit)]
        print(f"  violation sample (first {len(shown)}; full list via --format json):")
        for v in shown:
            print(f"    {v.file}:{v.line} -> {v.target_module} ({v.source_class}->{v.target_class})")
    if cmp and cmp.regressions:
        print("  regressions:")
        for r in cmp.regressions:
            print(f"    {r['pair']}: {r['current']} > baseline {r['baseline']} (+{r['delta']})")
        if cmp.module_regressions:
            print("  module pairs above baseline (locate the growth):")
            for r in cmp.module_regressions[:10]:
                print(f"    {r['pair']}: {r['current']} > {r['baseline']} (+{r['delta']})")
        pairs = {r["pair"] for r in cmp.regressions}
        shown = 0
        print("  sample violations for regressed pairs (file:line -> target):")
        for v in scan.violations:
            if f"{v.source_class}->{v.target_class}" not in pairs:
                continue
            print(f"    {v.file}:{v.line} -> {v.target_module} ({v.source_class}->{v.target_class})")
            shown += 1
            if shown >= limit:
                break
    if cmp and cmp.improvements:
        print("  improvements (baseline can be lowered with --update-baseline --reason ...):")
        for r in cmp.improvements:
            print(f"    {r['pair']}: {r['current']} < baseline {r['baseline']} (-{r['delta']})")
    if cmp and cmp.missing_pairs:
        print(f"  baseline missing pairs (treated as 0): {', '.join(cmp.missing_pairs)}")
    print(f"  verdict: {verdict}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _default_repo() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_update_baseline(scan: ScanResult, baseline_path: Path, reason: str) -> int:
    if not reason.strip():
        print("ERROR: --update-baseline requires a non-empty --reason", file=sys.stderr)
        return 2
    payload = _baseline_payload(scan, reason.strip())
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"baseline written: {baseline_path}")
    print(f"  commit={payload['commit']} total={payload['total']} counts={payload['counts']}")
    print(f"  reason={payload['reason']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="K/X/G/P import-boundary gate with a ratchet baseline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            examples:
              python3 scripts/arch/check_boundaries.py --baseline arch/baseline.json
              python3 scripts/arch/check_boundaries.py --baseline arch/baseline.json --format json
              python3 scripts/arch/check_boundaries.py --update-baseline --reason "seed at v0.8.0"
              python3 scripts/arch/check_boundaries.py --self-test
            """
        ),
    )
    parser.add_argument("--repo", type=Path, default=_default_repo(), help="repo root (default: script's repo)")
    parser.add_argument("--architecture", type=Path, default=None, help="classification ledger path")
    parser.add_argument("--baseline", type=Path, default=None, help="ratchet baseline path")
    parser.add_argument("--update-baseline", action="store_true", help="rewrite baseline from the measured graph")
    parser.add_argument("--reason", default="", help="required with --update-baseline; stored in the file")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--json-out", type=Path, default=None, help="also write the JSON receipt here")
    parser.add_argument("--limit", type=int, default=25, help="max sample violations printed in text mode")
    parser.add_argument("--self-test", action="store_true", help="run the built-in positive/negative fixtures")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo = args.repo.resolve()
    arch_path = args.architecture or repo / "arch" / "architecture.yaml"
    baseline_path = args.baseline or repo / "arch" / "baseline.json"
    try:
        arch = load_architecture(arch_path)
        scan = scan_repo(repo, arch)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        return _run_update_baseline(scan, baseline_path, args.reason)

    try:
        baseline = load_baseline(baseline_path)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    cmp = compare(scan, baseline)
    verdict = "baseline-ok" if cmp.ok else "regression"
    if baseline.get("architecture_sha256") and baseline["architecture_sha256"] != scan.arch_sha256:
        print(
            "WARN: architecture.yaml changed since the baseline was written; "
            "counts are compared as-is. Update the baseline explicitly if the rule change is intended.",
            file=sys.stderr,
        )

    if args.format == "json":
        print(json.dumps(receipt(scan, cmp, verdict), indent=2, ensure_ascii=False))
    else:
        print_text(scan, cmp, verdict, args.limit)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(receipt(scan, cmp, verdict), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"receipt written: {args.json_out}")
    return 0 if cmp.ok else 1


# --------------------------------------------------------------------------- #
# Built-in self-test (no pytest required)
# --------------------------------------------------------------------------- #

_FIXTURE_LEDGER = """\
schema: gugu.architecture/v1
scan:
  extensions: [".py"]
classes:
  K:
    allowed_deps: [K]
    forbidden_deps: [X, G, P]
  X:
    allowed_deps: [K, X]
    forbidden_deps: [G, P]
  G:
    allowed_deps: [G, K, X]
    forbidden_deps: [P]
  P:
    allowed_deps: [P, K, X, G]
    forbidden_deps: []
  unclassified:
    allowed_deps: []
    forbidden_deps: []
modules:
  - {id: agent, paths: ["agent/**"], class: K, owner: kernel-group, allowed_deps: [K], forbidden_deps: [X, G, P]}
  - {id: hermes_cli, paths: ["hermes_cli/**"], class: G, owner: glue-group, allowed_deps: [G, K, X], forbidden_deps: [P]}
  - {id: plugins, paths: ["plugins/**"], class: X, owner: plugin-authors, allowed_deps: [K, X], forbidden_deps: [G, P]}
  - {id: scripts, paths: ["scripts/**"], class: P, owner: release-manager, allowed_deps: [P, K, X, G], forbidden_deps: []}
  - {id: docs, paths: ["docs/**"], class: unclassified, owner: TBD, reason: "fixture: not a code module"}
"""

_FIXTURE_FILES = {
    "agent/__init__.py": "",
    "agent/a.py": textwrap.dedent(
        """\
        from hermes_cli import config
        import plugins.foo

        def delayed():
            from hermes_cli.config import helper
            return helper

        from . import sibling
        """
    ),
    "agent/sibling.py": "",
    "hermes_cli/__init__.py": "",
    "hermes_cli/config.py": "def helper():\n    return 1\n",
    "plugins/__init__.py": "",
    "plugins/foo.py": "from agent.a import delayed\n",
    "scripts/__init__.py": "",
    "scripts/tool.py": "from agent.a import delayed\n",
    "docs/__init__.py": "",
    "docs/gen.py": "from agent.a import delayed\n",
}


class _SelfTestFailure(AssertionError):
    pass


def _write_fixture(root: Path) -> Path:
    ledger = root / "arch" / "architecture.yaml"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(_FIXTURE_LEDGER, encoding="utf-8")
    for rel, content in _FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return ledger


def _check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise _SelfTestFailure(f"{name}: {detail}")
    print(f"  [PASS] {name}")


def run_self_test(verbose: bool = True) -> int:
    """Positive/negative fixtures; prints one line per case and returns 0/1."""
    print("arch-gate self-test")
    try:
        with tempfile.TemporaryDirectory(prefix="arch-gate-selftest-") as tmp:
            root = Path(tmp)
            _write_fixture(root)
            ledger = root / "arch" / "architecture.yaml"
            baseline_path = root / "arch" / "baseline.json"
            arch = load_architecture(ledger)
            scan = scan_repo(root, arch)

            # 1. Indented + from-import + relative imports are all counted.
            counts = scan.class_counts()
            _check("scan counts K->G == 2 (from-import + delayed/indented)", counts["K->G"] == 2, str(counts))
            _check("scan counts K->X == 1 (import plugin)", counts["K->X"] == 1, str(counts))
            _check("relative import resolved without violation", counts["K->P"] == 0, str(counts))
            delayed_lines = [v.line for v in scan.violations if v.source_module == "agent" and v.target_module == "hermes_cli"]
            _check("indented import line numbers captured", sorted(delayed_lines) == [1, 5], str(delayed_lines))

            # 2. Allowed edges (X->K, P->K, P->X...) are not violations.
            _check("allowed X->K not flagged", all(v.source_class != "X" for v in scan.violations))

            # 3. Unclassified edge is reported, not judged.
            _check(
                "unclassified source edge reported separately",
                any("docs" in edge for edge in scan.unclassified_edges),
                str(scan.unclassified_edges),
            )

            # 4. Baseline within -> exit 0.
            code = main(["--repo", str(root), "--baseline", str(baseline_path), "--update-baseline", "--reason", "fixture seed"])
            _check("--update-baseline writes baseline", code == 0 and baseline_path.exists(), f"code={code}")
            code = main(["--repo", str(root), "--baseline", str(baseline_path)])
            _check("within baseline -> exit 0", code == 0, f"code={code}")

            # 5. New violation -> exit 1.
            a_py = root / "agent" / "a.py"
            a_py.write_text(a_py.read_text(encoding="utf-8") + "from hermes_cli import colors\n", encoding="utf-8")
            scan2 = scan_repo(root, arch)
            cmp2 = compare(scan2, load_baseline(baseline_path))
            _check("new violation detected as regression", len(cmp2.regressions) == 1 and cmp2.regressions[0]["pair"] == "K->G", str(cmp2.regressions))
            code = main(["--repo", str(root), "--baseline", str(baseline_path)])
            _check("new violation -> exit 1", code == 1, f"code={code}")

            # 6. --update-baseline requires a reason.
            code = main(["--repo", str(root), "--baseline", str(baseline_path), "--update-baseline"])
            _check("--update-baseline without --reason -> exit 2", code == 2, f"code={code}")

            # 7. New top-level file that matches no rule -> configuration error (exit 2).
            mystery = root / "mystery" / "x.py"
            mystery.parent.mkdir(parents=True, exist_ok=True)
            mystery.write_text("import agent\n", encoding="utf-8")
            code = main(["--repo", str(root), "--baseline", str(baseline_path)])
            _check("unclassified source file -> exit 2", code == 2, f"code={code}")
            mystery.unlink()

            # 8. Baseline only ratchets down for the same numbers after re-seed.
            code = main(["--repo", str(root), "--baseline", str(baseline_path), "--update-baseline", "--reason", "accept fixture growth"])
            data = json.loads(baseline_path.read_text(encoding="utf-8"))
            _check("explicit baseline update records reason", code == 0 and data["reason"] == "accept fixture growth", str(data.get("reason")))
            _check("baseline matches measured violations", data["total"] == len(scan_repo(root, arch).violations), str(data["total"]))
    except _SelfTestFailure as exc:
        print(f"  [FAIL] {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"  [FAIL] configuration error: {exc}", file=sys.stderr)
        return 1
    print("arch-gate self-test: all cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
