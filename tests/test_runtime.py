import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        java = self.bin / "java"
        java.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-version" ]; then printf \'openjdk version "%s"\\n\' "${FAKE_JAVA_VERSION:-25.0.5}" >&2; exit 0; fi\n'
            'printf "arg:<%s>\\n" "$@"\n'
            'printf "tools:<%s>\\n" "${JAVA_TOOL_OPTIONS:-}"\n'
            'exit "${JAVA_EXIT_CODE:-0}"\n'
        )
        java.chmod(0o755)
        ip = self.bin / "ip"
        ip.write_text('#!/bin/sh\necho "1.0.0.0 via 172.17.0.1 dev eth0 src 172.17.0.2 uid 988"\n')
        ip.chmod(0o755)

    def run_entrypoint(self, startup="java -jar {{SERVER_JARFILE}}", **overrides):
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "STARTUP": startup,
            "SERVER_JARFILE": "server.jar",
            "SERVER_HEAP_MB": "64",
            "SERVER_MEMORY": "128",
            "SERVER_PORT": "25742",
            "OVERRIDE_STARTUP": "0",
            "AUTOMATIC_UPDATING": "0",
            "MALWARE_SCAN": "0",
        }
        environment.update(overrides)
        return subprocess.run(
            ["bash", str(ROOT / "java/entrypoint.sh")],
            env=environment, text=True, capture_output=True, timeout=10,
        )

    def test_uses_daemon_heap_and_inherits_agent(self):
        options = "-javaagent:/run/20tps/agent.jar -Dmarker=present"
        result = self.run_entrypoint(
            "java -Xms{{SERVER_HEAP_MB}}M -Xmx{{SERVER_HEAP_MB}}M -jar {{SERVER_JARFILE}} --nogui",
            JAVA_TOOL_OPTIONS=options, MAXIMUM_RAM="90",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("arg:<-Xms64M>", result.stdout)
        self.assertIn("arg:<-Xmx64M>", result.stdout)
        self.assertIn("arg:<--nogui>", result.stdout)
        self.assertIn(f"tools:<{options}>", result.stdout)

    def test_supports_imported_forge_argument_file(self):
        result = self.run_entrypoint("java -Xmx{{SERVER_HEAP_MB}}M @.20tps-import-args.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("arg:<@.20tps-import-args.txt>", result.stdout)

    def test_environment_values_are_not_executed(self):
        result = self.run_entrypoint(
            'java -jar "{{SERVER_JARFILE}}"',
            SERVER_JARFILE="server.jar; touch injected $(touch substituted)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("arg:<server.jar; touch injected $(touch substituted)>", result.stdout)
        self.assertFalse((self.home / "injected").exists())
        self.assertFalse((self.home / "substituted").exists())

    def test_exit_status_reaches_the_daemon(self):
        result = self.run_entrypoint(JAVA_EXIT_CODE="42")
        self.assertEqual(result.returncode, 42)

    def test_requires_launch_command(self):
        result = self.run_entrypoint("")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("STARTUP", result.stderr)

    def test_rejects_missing_or_invalid_placeholders(self):
        for placeholder in ["{{UNSET_RUNTIME_TEST_VARIABLE}}", "{{bad-name}}",
                            "{{SERVER_JARFILE}", "}} {{SERVER_JARFILE}}", "{{SERVER_JARFILE}} }}"]:
            result = self.run_entrypoint("java " + placeholder)
            self.assertNotEqual(result.returncode, 0)

    def test_updates_only_managed_minecraft_properties(self):
        properties = self.home / "server.properties"
        properties.write_text(
            "# Customer settings\nmotd=Keep this\nserver-port=25565\n"
            "server-ip=127.0.0.1\n"
            "enable-query=false\nquery.port=25565\nlevel-name=My World\n"
        )
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        content = properties.read_text()
        self.assertEqual(content.count("server-port="), 1)
        for expected in ["server-ip=0.0.0.0", "server-port=25742", "query.port=25742",
                         "enable-query=false", "level-name=My World", "motd=Keep this"]:
            self.assertIn(expected, content.splitlines())

    def test_creates_properties_for_world_servers(self):
        (self.home / "eula.txt").write_text("eula=true\n")
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("server-port=25742", (self.home / "server.properties").read_text())

    def test_rejects_invalid_ports(self):
        (self.home / "eula.txt").write_text("eula=true\n")
        for port in ["", "0", "65536", "-1", "123; touch injected"]:
            with self.subTest(port=port):
                result = self.run_entrypoint(SERVER_PORT=port)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.home / "server.properties").exists())

    def test_preserves_proxy_bindings_and_modpack_content(self):
        files = {
            "velocity.toml": 'bind = "0.0.0.0:25742"\n',
            "modrinth.index.json": '{"files": []}\n',
            "manifest.json": '{"files": []}\n',
        }
        for name, content in files.items():
            (self.home / name).write_text(content)
        for name in ["curl", "wget"]:
            path = self.bin / name
            path.write_text("#!/bin/sh\ntouch unexpected-download\nexit 1\n")
            path.chmod(0o755)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / "unexpected-download").exists())
        self.assertFalse((self.home / "server.properties").exists())
        for name, content in files.items():
            self.assertEqual((self.home / name).read_text(), content)

    def test_preserves_legacy_egg_override_when_explicitly_enabled(self):
        result = self.run_entrypoint(OVERRIDE_STARTUP="1", MAXIMUM_RAM="90")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("arg:<-Xms256M>", result.stdout)
        self.assertIn("arg:<-Xmx115M>", result.stdout)
        self.assertIn("arg:<nogui>", result.stdout)

    def fake_download(self):
        curl = self.bin / "curl"
        curl.write_text(
            '#!/bin/sh\n'
            'echo called >> download-calls\n'
            'printf "%s\\n" "$@" > download-arguments\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = "--output" ]; then shift; printf launcher > "$1"; fi\n'
            '  shift\n'
            'done\n'
            'exit "${DOWNLOAD_EXIT_CODE:-0}"\n'
        )
        curl.chmod(0o755)

    def test_preserves_proxy_settings_and_customer_keys(self):
        files = {
            "settings.yml": "ip: '127.0.0.1'\nport: 25565\nreport: keep\n",
            "velocity.toml": 'bind = "127.0.0.1:25565"\nforwarding-secret-file = "secret.key"\n',
            "config.yml": "listeners:\n  - query_port: 25565\n    host: 127.0.0.1:25565\n    hostname: keep\n"
                          "  - query_port: 25566\n    host: 127.0.0.1:25566\n",
        }
        for name, content in files.items():
            (self.home / name).write_text(content)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = (self.home / "settings.yml").read_text()
        self.assertIn("ip: '0.0.0.0'", settings)
        self.assertIn("port: 25742", settings)
        self.assertIn("report: keep", settings)
        self.assertIn('bind = "0.0.0.0:25742"', (self.home / "velocity.toml").read_text())
        self.assertIn('forwarding-secret-file = "secret.key"', (self.home / "velocity.toml").read_text())
        proxy = (self.home / "config.yml").read_text()
        self.assertEqual(proxy.count("  - query_port: 25742"), 2)
        self.assertEqual(proxy.count("    host: 0.0.0.0:25742"), 2)
        self.assertIn("    hostname: keep", proxy)

    def test_proxy_port_is_validated_before_writing_config(self):
        proxy = self.home / "velocity.toml"
        original = 'bind = "127.0.0.1:25565"\n'
        proxy.write_text(original)
        result = self.run_entrypoint(SERVER_PORT="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(proxy.read_text(), original)

    def test_disabled_properties_do_not_replace_active_properties(self):
        properties = self.home / "server.properties"
        properties.write_text("# server-port=25565\n# server-ip=127.0.0.1\nmotd=Keep this\n")
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = properties.read_text().splitlines()
        for expected in ["# server-port=25565", "# server-ip=127.0.0.1", "server-port=25742", "server-ip=0.0.0.0"]:
            self.assertIn(expected, lines)

    def test_legacy_launchers_are_downloaded_once_for_each_loader(self):
        self.fake_download()
        for directory, family in [("minecraftforge/forge", "forge"), ("neoforged/neoforge", "neoforge"),
                                  ("neoforged/forge", "neoforge")]:
            with self.subTest(loader=directory):
                loader = self.home / "libraries/net" / directory
                loader.mkdir(parents=True, exist_ok=True)
                jar = self.home / "server file.jar"
                try:
                    for _ in range(2):
                        result = self.run_entrypoint('java -jar "{{SERVER_JARFILE}}"', SERVER_JARFILE=jar.name)
                        self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(jar.read_text(), "launcher")
                    arguments = (self.home / "download-arguments").read_text().splitlines()
                    self.assertIn(f"https://s3.mcjars.app/{family}/ForgeServerJAR.jar", arguments)
                    self.assertIn("--fail", arguments)
                    self.assertEqual((self.home / "download-calls").read_text().splitlines(), ["called"])
                finally:
                    loader.rmdir()
                    jar.unlink(missing_ok=True)
                    (self.home / "download-calls").unlink(missing_ok=True)

    def test_failed_launcher_download_does_not_start_the_server(self):
        self.fake_download()
        (self.home / "libraries/net/minecraftforge/forge").mkdir(parents=True)
        result = self.run_entrypoint(DOWNLOAD_EXIT_CODE="22")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "server.jar").exists())
        self.assertNotIn("arg:<-jar>", result.stdout)
        self.assertIn("Could not prepare", result.stderr)

    def test_imported_forge_does_not_download_a_legacy_launcher(self):
        self.fake_download()
        (self.home / "libraries/net/minecraftforge/forge").mkdir(parents=True)
        result = self.run_entrypoint("java @.20tps-import-args.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("arg:<@.20tps-import-args.txt>", result.stdout)
        self.assertFalse((self.home / "download-calls").exists())

    def test_legacy_flags_agent_and_vector_version_boundaries(self):
        agent = self.home / "agent with spaces.jar"
        agent.touch()
        for version, vector in [("1.8.0_512", False), ("16.0.2", True), ("21.0.13", True), ("22.0.2", False)]:
            with self.subTest(java=version):
                result = self.run_entrypoint(
                    OVERRIDE_STARTUP="1", MAXIMUM_RAM="90", FAKE_JAVA_VERSION=version,
                    SIMD_OPERATIONS="1", REMOVE_UPDATE_WARNING="1", JAVA_AGENT=str(agent),
                    ADDITIONAL_FLAGS="Velocity Flags", JVM_FLAGS="-Dfirst=1\n-Dsecond=2",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("arg:<--add-modules=jdk.incubator.vector>" in result.stdout, vector)
                for expected in [f"arg:<-javaagent:{agent}>", "arg:<-DIReallyKnowWhatIAmDoingISwear>",
                                 "arg:<-XX:MaxInlineLevel=15>", "arg:<-Dfirst=1>", "arg:<-Dsecond=2>"]:
                    self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
