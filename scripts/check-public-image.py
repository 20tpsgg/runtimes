#!/usr/bin/env python3
"""Verify a GHCR manifest is anonymously readable, without Docker credentials."""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def verify(repository, digest):
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
    with urllib.request.urlopen("https://ghcr.io/token?" + query, timeout=20) as response:
        token = json.load(response)["token"]
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{repository}/manifests/{digest}",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.headers.get("Docker-Content-Digest") != digest:
            raise ValueError("Anonymous manifest digest differs from the published digest")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="For example: 20tpsgg/runtimes")
    parser.add_argument("digest", help="Published sha256 digest")
    args = parser.parse_args()
    for attempt in range(5):
        try:
            verify(args.repository.lower(), args.digest)
            print("Anonymous GHCR pull verified: " + args.digest)
            return
        except (urllib.error.URLError, ValueError):
            if attempt < 4:
                time.sleep(2)
    raise SystemExit(
        "The image is not anonymously readable. Set the GHCR package visibility "
        "to Public in GitHub Package settings, then rerun this workflow. "
        "No stable alias was promoted."
    )


if __name__ == "__main__":
    main()
