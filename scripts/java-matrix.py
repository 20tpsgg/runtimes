#!/usr/bin/env python3
"""Generate the build matrix from the complete, reviewed Java version catalog."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def load_catalog(path=ROOT / "java/versions.json"):
    catalog = json.loads(path.read_text())
    versions = catalog["versions"]
    if [version["major"] for version in versions] != list(range(8, catalog["latest_ga"] + 1)):
        raise ValueError("Catalog must cover every Java major from 8 through latest_ga exactly once")
    for version in versions:
        if version["support"] not in ["lts", "current", "eol"]:
            raise ValueError("Unknown JDK support status")
        if version["support"] != "eol" and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version.get("minimum_version", "")):
            raise ValueError("Supported Java versions require a minimum patch version")
        architectures = version["architectures"]
        if architectures not in [["amd64"], ["amd64", "arm64"]]:
            raise ValueError("Every runtime must provide amd64, optionally followed by arm64")
        if version["dockerfile"] == "java/Dockerfile.archive":
            if version["support"] != "eol" or set(version["archives"]) != set(architectures):
                raise ValueError("Archive records must match the EOL runtime's architectures")
            for archive in version["archives"].values():
                if not re.fullmatch(r"[a-f0-9]{64}", archive["sha256"]):
                    raise ValueError("Every archived JDK requires a pinned SHA-256 checksum")
                if not archive["url"].startswith(("https://cdn.azul.com/zulu/bin/", "https://github.com/adoptium/temurin")):
                    raise ValueError("Archived JDKs must come from their official distributors")
        elif version["dockerfile"] != "java/Dockerfile":
            raise ValueError("Unknown Java Dockerfile")
    return catalog


def build_matrix(catalog):
    return {"include": [
        {
            "java": version["major"],
            "dockerfile": version["dockerfile"],
            "base": version["base_image"],
            "arm64": "arm64" in version["architectures"],
            "platforms": ", ".join("linux/" + arch for arch in version["architectures"]),
            "support": version["support"],
            "vendor": version["vendor"],
            "minimum_version": version.get("minimum_version", ""),
        }
        for version in catalog["versions"]
    ]}


if __name__ == "__main__":
    print(json.dumps(build_matrix(load_catalog()), separators=(",", ":")))
