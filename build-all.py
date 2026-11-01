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
    parser = argparse.ArgumentParser(description="Build boolean-benchmark runners (every latest and pinned runner by default).")
    parser.add_argument(
        "-y", "--accept-licenses", action="store_true",
        help="Accept all licenses non-interactively, including free non-commercial licenses",
    )
    parser.add_argument(
        "--latest-only", action="store_true",
        help="Skip runners with policy: pinned (default: build every runner)",
    )
    args = parser.parse_args()

    # Keep our headers in order with the child builds' output when redirected to a file.
    sys.stdout.reconfigure(line_buffering=True)

    repo_root = find_repo_root()

    runner_yamls = find_runner_yamls(repo_root)
    if not runner_yamls:
        print("No runner.yaml files found. Nothing to do.")
        return 0

    if args.latest_only:
        to_build = [p for p in runner_yamls if runner_policy(p) != "pinned"]
        skipped = [p for p in runner_yamls if runner_policy(p) == "pinned"]
    else:
        to_build, skipped = runner_yamls, []

    print(f"Found {len(runner_yamls)} runner(s); building {len(to_build)}.")
    for path in skipped:
        print(f"  [skip pinned] {path.parent.relative_to(repo_root)}")
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
