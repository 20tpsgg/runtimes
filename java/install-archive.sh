#!/usr/bin/env bash
set -euo pipefail

: "${JAVA_MAJOR:?JAVA_MAJOR is required}"
: "${TARGETARCH:?TARGETARCH is required}"
: "${JAVA_HOME:?JAVA_HOME is required}"

catalog=${1:-/usr/local/share/20tps/versions.json}
record=$(jq -ce --argjson major "$JAVA_MAJOR" --arg arch "$TARGETARCH" \
    '.versions[] | select(.major == $major) | .archives[$arch] | select(. != null)' "$catalog")
url=$(jq -er '.url' <<< "$record")
checksum=$(jq -er '.sha256' <<< "$record")
[[ "$checksum" =~ ^[a-f0-9]{64}$ ]]
case "$url" in
    https://cdn.azul.com/zulu/bin/*|https://github.com/adoptium/temurin*-binaries/releases/download/*) ;;
    *) echo 'Unexpected JDK archive host.' >&2; exit 1 ;;
esac

archive=$(mktemp)
trap 'rm -f "$archive"' EXIT
curl --fail --show-error --location --retry 3 --proto '=https' --proto-redir '=https' \
    "$url" --output "$archive"
printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
mkdir -p "$JAVA_HOME"
tar --extract --gzip --file "$archive" --directory "$JAVA_HOME" \
    --strip-components=1 --no-same-owner

actual=$("$JAVA_HOME/bin/java" -XshowSettings:properties -version 2>&1 \
    | awk -F '= ' '/^[[:space:]]*java.specification.version =/{print $2}')
if [[ ${actual#1.} != "$JAVA_MAJOR" ]]; then
    echo "JDK archive major mismatch: expected $JAVA_MAJOR, got $actual" >&2
    exit 1
fi
