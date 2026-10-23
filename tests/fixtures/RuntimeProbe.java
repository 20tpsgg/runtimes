import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.management.ManagementFactory;

public class RuntimeProbe {
    public static void main(String[] arguments) throws Exception {
        String actual = System.getProperty("java.specification.version").replace("1.", "");
        if (!actual.equals(System.getenv("EXPECTED_JAVA_MAJOR"))) {
            throw new AssertionError("Unexpected Java version: " + actual);
        }
        if (!"true".equals(System.getProperty("twentytps.agent.loaded"))) {
            throw new AssertionError("JAVA_TOOL_OPTIONS did not load the test agent");
        }
        if (!"preserved".equals(System.getProperty("twentytps.options"))) {
            throw new AssertionError("JAVA_TOOL_OPTIONS was changed");
        }
        boolean child = arguments.length > 0 && "child".equals(arguments[0]);
        long ceiling = (child ? 32L : 64L) * 1024 * 1024;
        long maximum = Runtime.getRuntime().maxMemory();
        if (maximum > ceiling || maximum < ceiling * 3 / 4) {
            throw new AssertionError("Daemon-provided heap was changed: " + maximum);
        }
        if (child) {
            System.out.println("FORKED_AGENT_OK");
            return;
        }
        // Under QEMU, /proc/1/cmdline can name the emulator instead of Java.
        String pid = ManagementFactory.getRuntimeMXBean().getName().split("@", 2)[0];
        if (!"1".equals(pid)) {
            throw new AssertionError("Java must be PID 1 for signal delivery; actual PID: " + pid);
        }
        Process fork = new ProcessBuilder("java", "-Xms16M", "-Xmx32M", "-cp", ".", "RuntimeProbe", "child")
            .inheritIO().start();
        if (fork.waitFor() != 0) {
            throw new AssertionError("Forked Java process failed");
        }
        Runtime.getRuntime().addShutdownHook(new Thread(() -> System.out.println("GRACEFUL_SHUTDOWN")));
        System.out.println("RUNTIME_READY java=" + actual + " pid=" + pid + " heap=" + maximum);
        BufferedReader input = new BufferedReader(new InputStreamReader(System.in));
        String line;
        while ((line = input.readLine()) != null) {
            if ("stop".equals(line)) {
                return;
            }
        }
    }
}
