#!/usr/bin/env bash
set -eo pipefail

export TZ="${TZ:-UTC}"
INTERNAL_IP=$(ip route get 1.0.0.0 2>/dev/null | awk '{for (i=1; i<NF; i++) if ($i == "src") {print $(i+1); exit}}') || INTERNAL_IP=
export INTERNAL_IP
cd "${HOME:-/home/container}"

message() {
    local prefix=${LOG_PREFIX:-$'\033[1m[\033[38;2;0;153;102m20\033[39mtps]\033[0m'}
    printf '%b %s\n' "$prefix" "$*"
}
abort() { message "$*" >&2; exit 1; }

if [[ ${OVERRIDE_STARTUP:-0} != 1 && -z ${STARTUP:-} ]]; then
    abort 'STARTUP must contain the daemon-provided launch command.'
fi
for configuration in eula.txt server.properties settings.yml velocity.toml config.yml; do
    if [[ -f $configuration ]]; then
        port=${SERVER_PORT:-}
        [[ $port =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) \
            || abort 'SERVER_PORT must be an integer between 1 and 65535.'
        break
    fi
done

message 'java -version'
java_version=$(java -version 2>&1)
printf '%s\n' "$java_version" >&2
JAVA_MAJOR_VERSION=$(printf '%s\n' "$java_version" | awk -F '"' '/^(openjdk|java) version / {split($2, n, /[._+-]/); print (n[1] == 1 ? n[2] : n[1]); exit}')

if [[ ${MALWARE_SCAN:-0} == 1 ]]; then
    if [[ -f /MCAntiMalware.jar ]]; then
        message 'Scanning server files for malware.'
        java -jar /MCAntiMalware.jar --scanDirectory . --singleScan true --disableAutoUpdate true \
            || abort 'Malware scan failed.'
    else
        message 'Malware scan skipped: /MCAntiMalware.jar is not installed.'
    fi
fi

prepare_launcher() {
    [[ -n ${SERVER_JARFILE:-} && ! -f $SERVER_JARFILE ]] || return 0
    if [[ ${OVERRIDE_STARTUP:-0} != 1 && ! ${STARTUP:-} =~ (^|[[:space:]])-jar[[:space:]] ]]; then
        return 0
    fi
    local family= directory temporary
    for directory in minecraftforge/forge neoforged/neoforge neoforged/forge; do
        if [[ -d libraries/net/$directory ]]; then
            case $directory in minecraftforge/*) family=forge ;; *) family=neoforge ;; esac
            break
        fi
    done
    [[ -n $family ]] || return 0
    temporary=$(mktemp)
    message "Fetching the $family launcher."
    if curl --fail --location --silent --show-error --connect-timeout 10 --max-time 120 --retry 2 \
        --proto '=https' --proto-redir '=https' \
        "https://s3.mcjars.app/$family/ForgeServerJAR.jar" --output "$temporary" \
        && [[ -s $temporary ]] && mv -- "$temporary" "$SERVER_JARFILE"; then
        message 'Launcher ready.'
    else
        rm -f -- "$temporary"
        abort "Could not prepare the $family launcher."
    fi
}

# Replace literal keys, keeping indentation and unrelated customer settings.
set_value() {
    local file=$1 key=$2 separator=$3 value=$4 append=${5:-1} temporary
    temporary=$(mktemp)
    if awk -v key="$key" -v separator="$separator" -v value="$value" -v append="$append" '
        BEGIN {delimiter=separator; gsub(/[ \t]/, "", delimiter)}
        {
            body=$0; sub(/^[ \t]*/, "", body)
            if (delimiter == ":") sub(/^-[ \t]+/, "", body)
            position=index(body, delimiter)
            candidate=substr(body, 1, position ? position-1 : 0)
            sub(/[ \t]*$/, "", candidate)
            if (position && candidate == key) {
                indentation=substr($0, 1, length($0)-length(body))
                print indentation key separator value
                found=1
            } else print
        }
        END {if (!found && append) print key separator value}
    ' "$file" > "$temporary" && cat "$temporary" > "$file"; then
        rm -f -- "$temporary"
    else
        rm -f -- "$temporary"
        abort "Could not update $file."
    fi
}

prepare_launcher
if [[ -f eula.txt ]]; then touch server.properties; fi
if [[ -f server.properties ]]; then
    set_value server.properties server-ip '=' 0.0.0.0
    set_value server.properties server-port '=' "$SERVER_PORT"
    set_value server.properties query.enabled '=' true
    set_value server.properties query.port '=' "$SERVER_PORT"
fi
if [[ -f settings.yml ]]; then
    set_value settings.yml ip ': ' "'0.0.0.0'" 0
    set_value settings.yml port ': ' "$SERVER_PORT" 0
fi
if [[ -f velocity.toml ]]; then
    set_value velocity.toml bind ' = ' "\"0.0.0.0:$SERVER_PORT\""
fi
if [[ -f config.yml ]]; then
    set_value config.yml query_port ': ' "$SERVER_PORT"
    set_value config.yml host ': ' "0.0.0.0:$SERVER_PORT"
fi

launch_override() {
    local options=(-Dterminal.jline=false -Dterminal.ansi=true) extra=() heap
    if [[ ${SIMD_OPERATIONS:-0} == 1 ]] && (( JAVA_MAJOR_VERSION >= 16 && JAVA_MAJOR_VERSION <= 21 )); then
        options+=(--add-modules=jdk.incubator.vector)
    fi
    if [[ ${REMOVE_UPDATE_WARNING:-0} == 1 ]]; then options+=(-DIReallyKnowWhatIAmDoingISwear); fi
    if [[ -n ${JAVA_AGENT:-} ]]; then
        if [[ -f $JAVA_AGENT ]]; then options+=("-javaagent:$JAVA_AGENT"); else message 'JAVA_AGENT file is missing; skipping it.'; fi
    fi
    case ${ADDITIONAL_FLAGS:-} in
        "Aikar's Flags")
            options+=(
                -XX:+DisableExplicitGC -XX:+ParallelRefProcEnabled -XX:+PerfDisableSharedMem
                -XX:+UnlockExperimentalVMOptions -XX:+UseG1GC -XX:G1HeapRegionSize=8M
                -XX:G1HeapWastePercent=5 -XX:G1MaxNewSizePercent=40 -XX:G1MixedGCCountTarget=4
                -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1NewSizePercent=30
                -XX:G1RSetUpdatingPauseTimePercent=5 -XX:G1ReservePercent=20
                -XX:InitiatingHeapOccupancyPercent=15 -XX:MaxGCPauseMillis=200
                -XX:MaxTenuringThreshold=1 -XX:SurvivorRatio=32
                -Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true
            ) ;;
        'Velocity Flags')
            options+=(-XX:+ParallelRefProcEnabled -XX:+UnlockExperimentalVMOptions
                -XX:+UseG1GC -XX:G1HeapRegionSize=4M -XX:MaxInlineLevel=15) ;;
    esac
    if [[ -n ${JVM_FLAGS:-} ]]; then
        read -r -a extra <<< "${JVM_FLAGS//$'\n'/ }"
        options+=("${extra[@]}")
    fi
    [[ ${SERVER_MEMORY:-} =~ ^[0-9]+$ && ${MAXIMUM_RAM:-} =~ ^[0-9]+$ ]] \
        || abort 'The startup override requires numeric SERVER_MEMORY and MAXIMUM_RAM.'
    heap=$((10#$SERVER_MEMORY * 10#$MAXIMUM_RAM / 100))
    message 'Starting Java server.'
    exec java "${options[@]}" -Xms256M "-Xmx${heap}M" -jar "${SERVER_JARFILE:?SERVER_JARFILE is required}" nogui
}

launch_daemon() {
    local remaining=$STARTUP command= name
    while [[ $remaining == *'{{'* ]]; do
        command+=${remaining%%'{{'*}
        remaining=${remaining#*'{{'}
        [[ $remaining == *'}}'* ]] || abort 'STARTUP contains an unresolved placeholder.'
        name=${remaining%%'}}'*}
        [[ $name =~ ^[A-Z_][A-Z0-9_]*$ ]] || abort 'STARTUP contains an invalid placeholder.'
        declare -p "$name" >/dev/null 2>&1 || abort "Missing startup environment variable: $name"
        command+='${'"$name"'}'
        remaining=${remaining#*'}}'}
    done
    command+=$remaining
    [[ $command != *'}}'* ]] || abort 'STARTUP contains an invalid placeholder.'
    message 'Starting server.'
    exec /bin/bash -c "exec $command"
}

if [[ ${OVERRIDE_STARTUP:-0} == 1 ]]; then launch_override; else launch_daemon; fi
