#!/usr/bin/env python3
"""Skip scheduled builds only after unchanged inputs and a clean APT simulation."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or "Command failed: " + command[0])
    return result.stdout


def platforms(value):
    architectures = [part.strip().removeprefix("linux/") for part in value.split(",")]
    if architectures not in [["amd64"], ["arm64"], ["amd64", "arm64"]]:
        raise ValueError("Expected amd64, arm64, or amd64 followed by arm64")
    return architectures


def complete_snapshot(directory):
    status = directory / "status"
    if not status.is_file() or "Status: install ok installed" not in status.read_text():
        return False
    return any(path.is_file() and re.search(r"^(deb\s|URIs:\s+\S)", path.read_text(), re.MULTILINE)
               for path in (directory / "sources").glob("*"))


def snapshot(state, image, architecture):
    actual = run(["docker", "image", "inspect", image, "--format", "{{.Architecture}}"])
    if actual.strip() != architecture:
        raise ValueError("Snapshot architecture does not match the tested image")
    directory = state / architecture
    if directory.exists():
        shutil.rmtree(directory)
    sources = directory / "sources"
    sources.mkdir(parents=True)
    status = run(["docker", "run", "--rm", "--platform", "linux/" + architecture,
                  "--entrypoint", "/bin/cat", image, "/var/lib/dpkg/status"])
    (directory / "status").write_text(status)
    source_files = run([
        "docker", "run", "--rm", "--platform", "linux/" + architecture,
        "--entrypoint", "/bin/sh", image, "-c", """
        for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
            if [ -f "$file" ]; then
                printf '%s\\0' "$file"
                cat "$file"
                printf '\\0'
            fi
        done
        """,
    ]).split("\0")
    for index in range(0, len(source_files) - 1, 2):
        name, content = source_files[index:index + 2]
        suffix = ".sources" if name.endswith(".sources") else ".list"
        (sources / (str(index) + suffix)).write_text(content)
    if not complete_snapshot(directory):
        raise ValueError("The tested image has no package state or Ubuntu sources")


def package_updates(directory, architecture):
    # Isolate all APT writes and configuration from the runner's own system.
    with tempfile.TemporaryDirectory(prefix="runtime-apt-") as temporary:
        root = Path(temporary)
        for name in ["empty", "lists/partial", "archives/partial", "log"]:
            (root / name).mkdir(parents=True)
        config = root / "apt.conf"
        settings = {
            "Dir::Etc::main": "/dev/null",
            "Dir::Etc::parts": root / "empty",
            "Dir::Etc::sourcelist": "/dev/null",
            "Dir::Etc::sourceparts": (directory / "sources").resolve(),
            "Dir::Etc::preferences": "/dev/null",
            "Dir::Etc::preferencesparts": root / "empty",
            "Dir::Etc::trusted": "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
            "Dir::Etc::trustedparts": root / "empty",
            "Dir::State::status": (directory / "status").resolve(),
            "Dir::State::lists": root / "lists",
            "Dir::State::extended_states": root / "extended_states",
            "Dir::Cache::pkgcache": root / "pkgcache.bin",
            "Dir::Cache::srcpkgcache": root / "srcpkgcache.bin",
            "Dir::Cache::archives": root / "archives",
            "Dir::Log": root / "log",
            "APT::Architecture": architecture,
            "Acquire::Languages": "none",
            "Acquire::Retries": "3",
            "Acquire::http::Timeout": "30",
            "Acquire::https::Timeout": "30",
            "Debug::NoLocking": "true",
        }
        config.write_text(
            "\n".join(key + " " + json.dumps(str(value)) + ";" for key, value in settings.items())
            + '\nAPT::Architectures { "' + architecture + '"; };\n'
        )
        environment = {**os.environ, "APT_CONFIG": str(config), "LC_ALL": "C"}
        # A partial/network/signature failure must fail the check, never mean "unchanged".
        run(["apt-get", "update", "--error-on=any"], env=environment)
        simulation = run(["apt-get", "--simulate", "upgrade"], env=environment)
        return any(line.startswith("Inst ") for line in simulation.splitlines())


def needs_build(state, fingerprint, architectures, event):
    if event != "schedule":
        return True, "Requested build"
    try:
        marker = json.loads((state / "published.json").read_text())
    except (FileNotFoundError, ValueError):
        return True, "No successful publication cached"
    if (not isinstance(marker, dict) or marker.get("schema") != 1 or marker.get("fingerprint") != fingerprint
            or marker.get("architectures") != architectures
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", marker.get("digest", ""))):
        return True, "Base image or build inputs changed"
    for architecture in architectures:
        directory = state / architecture
        if not complete_snapshot(directory):
            return True, "Package snapshot missing"
        if package_updates(directory, architecture):
            return True, "Ubuntu package updates available: " + architecture
    return False, "Unchanged base, build inputs and installed Ubuntu packages"


def mark_published(state, fingerprint, architectures, digest):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Expected the successfully published manifest digest")
    for architecture in architectures:
        directory = state / architecture
        if not complete_snapshot(directory):
            raise ValueError("Cannot cache publication without tested package snapshots")
    (state / "published.json").write_text(json.dumps({
        "schema": 1, "fingerprint": fingerprint, "architectures": architectures, "digest": digest,
    }) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["check", "snapshot", "published"])
    parser.add_argument("--state", type=Path, default=Path(".build-state"))
    parser.add_argument("--fingerprint")
    parser.add_argument("--platforms", default="linux/amd64")
    parser.add_argument("--event", default="workflow_dispatch")
    parser.add_argument("--image")
    parser.add_argument("--digest")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    architectures = platforms(args.platforms)
    if args.operation == "snapshot":
        if not args.image or len(architectures) != 1:
            parser.error("A snapshot requires one image and platform")
        snapshot(args.state, args.image, architectures[0])
        return
    if not args.fingerprint or not re.fullmatch(r"[a-f0-9]{64}", args.fingerprint):
        parser.error("A SHA-256 build fingerprint is required")
    if args.operation == "published":
        mark_published(args.state, args.fingerprint, architectures, args.digest or "")
        return
    build, reason = needs_build(args.state, args.fingerprint, architectures, args.event)
    print(reason)
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write("build=" + str(build).lower() + "\n")


if __name__ == "__main__":
    main()
