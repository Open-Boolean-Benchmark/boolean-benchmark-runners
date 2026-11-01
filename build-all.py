#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml>=6.0",
# ]
# ///
#
# Invoke with:   uv run build-all.py
# On Windows:    python -m uv run build-all.py   (or: uv run build-all.py if uv is on PATH)
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml


def run(cmd: list[str], cwd: Path) -> None:
    pretty = " ".join(f'"{x}"' if " " in x else x for x in cmd)
    print(f"> {pretty}  (in {cwd})")
    subprocess.run(cmd, cwd=cwd, check=True)


def find_repo_root() -> Path:
    return Path(__file__).resolve().parent


def find_runner_yamls(search_root: Path) -> list[Path]:
    return sorted(p for p in search_root.rglob("runner.yaml") if "_common" not in p.parts)


def runner_policy(runner_yaml: Path) -> str:
    """runner.policy from the manifest; a missing field means "latest" (see Runner Manifest Spec)."""
    with runner_yaml.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return (config.get("runner") or {}).get("policy", "latest")


def _version_key(version: str) -> list[tuple[int, object]]:
    """Natural-order sort key so e.g. 1.10.0 sorts above 1.9.8."""
    key: list[tuple[int, object]] = []
    for tok in re.split(r"(\d+)", version):
        if tok.isdigit():
            key.append((1, int(tok)))
        elif tok:
            key.append((0, tok))
    return key


def select_default(runner_yamls: list[Path], search_root: Path) -> tuple[list[Path], list[Path]]:
    """One runner per group, returning (selected, skipped).

    Groups follow the on-disk layout <family>/<version>/[<algo>/...]runner.yaml, so the
    versions of the same family+algorithm share a group. Per group, select the
    policy: latest runner; if there is none, the highest-version pinned runner.
    Runners without a version component are always selected.
    """
    groups: dict[tuple, list[Path]] = {}
    selected: list[Path] = []
    for runner_yaml in runner_yamls:
        parts = runner_yaml.parent.relative_to(search_root).parts
        if len(parts) < 2:
            selected.append(runner_yaml)  # no version component — can't group
            continue
        groups.setdefault((parts[0], parts[2:]), []).append(runner_yaml)

    skipped: list[Path] = []
    for members in groups.values():
        # latest before pinned, then highest version first
        members.sort(key=lambda p: (runner_policy(p) != "pinned",
                                    _version_key(p.parent.relative_to(search_root).parts[1])),
                     reverse=True)
        selected.append(members[0])
        skipped.extend(members[1:])

    return sorted(selected), sorted(skipped)


def build_runner(runner_yaml: Path, accept_licenses: bool = False) -> None:
    runner_dir = runner_yaml.parent

    with runner_yaml.open() as f:
        config = yaml.safe_load(f)

    script = config.get("build", {}).get("script")
    if not script:
        print(f"  [skip] no build.script defined in {runner_yaml}")
        return

    # script is relative to the runner directory (e.g. "./build.py")
    script_path = (runner_dir / script).resolve()
    if not script_path.exists():
        print(f"  [skip] build script not found: {script_path}")
        return

    cmd = ["uv", "run", "--script", str(script_path)]
    if accept_licenses:
        cmd.append("-y")
    run(cmd, cwd=runner_dir)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build boolean-benchmark runners (by default one per family: the latest runner, "
                    "or the newest pinned one if there is no latest).")
    parser.add_argument(
        "-y", "--accept-licenses", action="store_true",
        help="Accept all licenses non-interactively, including free non-commercial licenses",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Build every runner, including all pinned versions",
    )
    args = parser.parse_args()

    # Keep our headers in order with the child builds' output when redirected to a file.
    sys.stdout.reconfigure(line_buffering=True)

    repo_root = find_repo_root()

    runner_yamls = find_runner_yamls(repo_root)
    if not runner_yamls:
        print("No runner.yaml files found. Nothing to do.")
        return 0

    if args.all:
        to_build, skipped = runner_yamls, []
    else:
        to_build, skipped = select_default(runner_yamls, repo_root)

    print(f"Found {len(runner_yamls)} runner(s); building {len(to_build)}.")
    for path in skipped:
        print(f"  [skip {runner_policy(path)}] {path.parent.relative_to(repo_root)}")
    print()

    failed: list[tuple[Path, Exception]] = []
    for runner_yaml in to_build:
        runner_id = runner_yaml.parent.relative_to(repo_root)
        print(f"=== {runner_id} ===")
        try:
            build_runner(runner_yaml, accept_licenses=args.accept_licenses)
        except subprocess.CalledProcessError as e:
            print(f"  [FAILED] exit code {e.returncode}")
            failed.append((runner_yaml, e))
        print()

    # Summary, separated from the per-runner build logs above so it's easy to spot.
    failed_paths = {path for path, _ in failed}
    print("========== build-all summary ==========")
    print(f"Built {len(to_build)} runner(s):")
    for runner_yaml in to_build:
        status = "FAILED" if runner_yaml in failed_paths else "ok"
        print(f"  [{status}] {runner_yaml.parent.relative_to(repo_root)}")
    print()

    if failed:
        print(f"{len(failed)} runner(s) failed; rebuild individually with:")
        for runner_yaml, _ in failed:
            with runner_yaml.open() as f:
                script = yaml.safe_load(f).get("build", {}).get("script", "build.py")
            script_path = (runner_yaml.parent / script).resolve()
            print(f"  uv run --script {script_path}")
        print()
        print(f"FAIL: {len(failed)}/{len(to_build)} runner(s) failed to build.")
        return 1

    print(f"SUCCESS: all {len(to_build)} runner(s) built.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
