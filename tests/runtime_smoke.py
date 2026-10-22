#!/usr/bin/env python3
"""Exercise an actual runtime image with the 20tps container and JVM contract."""

import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*arguments, check=True):
    result = subprocess.run(["docker", *arguments], text=True, capture_output=True, timeout=90)
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout + result.stderr if arguments[0] == "logs" else result.stdout


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("major", type=int)
    parser.add_argument("--platform", default="linux/amd64")
    args = parser.parse_args()
    image = json.loads(docker("image", "inspect", args.image))[0]
    require(image["Config"]["User"] == "988:988", "Runtime must use UID/GID 988")
    require(image["Config"]["WorkingDir"] == "/home/container", "Incorrect working directory")
    name = "20tps-runtime-test-" + uuid.uuid4().hex[:12]
    module_flags = "--add-modules=jdk.incubator.vector " if args.major >= 16 else ""
    startup = "java " + module_flags + "-XX:+UseG1GC -Xms32M -Xmx{{SERVER_HEAP_MB}}M -jar {{SERVER_JARFILE}}"
    script = """set -eu
test "$(id -u)" = 988
test "$(id -g)" = 988
cp /fixtures/* .
javac ProbeAgent.java RuntimeProbe.java
printf 'Premain-Class: ProbeAgent\\n' > agent.mf
jar cfm probe-agent.jar agent.mf ProbeAgent.class
jar cfe server.jar RuntimeProbe RuntimeProbe.class
export JAVA_TOOL_OPTIONS='-javaagent:/home/container/probe-agent.jar -Dtwentytps.options=preserved'
printf 'eula=true\\n' > eula.txt
exec "$@"
"""
    try:
        docker(
            "run", "--detach", "--interactive", "--name", name,
            "--platform", args.platform, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/home/container:rw,mode=1777",
            "--mount", f"type=bind,src={ROOT / 'tests/fixtures'},dst=/fixtures,readonly",
            "--env", f"EXPECTED_JAVA_MAJOR={args.major}",
            "--env", "SERVER_PORT=25742", "--env", "SERVER_MEMORY=128",
            "--env", "SERVER_HEAP_MB=64", "--env", "SERVER_JARFILE=server.jar",
            "--env", "OVERRIDE_STARTUP=0", "--env", "AUTOMATIC_UPDATING=0",
            "--env", "MALWARE_SCAN=0", "--env", f"STARTUP={startup}",
            "--entrypoint", "/bin/bash", args.image, "-c", script, "runtime-probe",
            *(image["Config"].get("Entrypoint") or []), *image["Config"]["Cmd"],
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", name))[0]["State"]
            logs = docker("logs", name, check=False)
            if "RUNTIME_READY" in logs:
                break
            require(state["Running"], "Runtime failed before readiness:\n" + logs)
            time.sleep(0.5)
        else:
            raise RuntimeError("Runtime did not become ready:\n" + docker("logs", name, check=False))
        require("FORKED_AGENT_OK" in logs, "Forked JVM did not inherit the agent")
        require(docker("exec", name, "cat", "/proc/1/cmdline").split("\0")[0].endswith("java"),
                "Java must be PID 1 for signal delivery")
        properties = docker("exec", name, "cat", "/home/container/server.properties").splitlines()
        for line in ["server-port=25742", "server-ip=0.0.0.0", "query.port=25742"]:
            require(line in properties, "Missing managed property: " + line)
        docker("stop", "--time", "10", name)
        state = json.loads(docker("inspect", name))[0]["State"]
        require(state["ExitCode"] != 137, "Runtime had to be force-killed")
        require("GRACEFUL_SHUTDOWN" in docker("logs", name, check=False), "SIGTERM did not reach Java")
        print(f"PASS Java {args.major} {args.platform}: UID, heap, forked agent, ports, PID 1, SIGTERM")
    finally:
        docker("rm", "--force", name, check=False)


if __name__ == "__main__":
    main()
