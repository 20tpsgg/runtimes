import java.lang.instrument.Instrumentation;

public class ProbeAgent {
    public static void premain(String arguments, Instrumentation instrumentation) {
        System.setProperty("twentytps.agent.loaded", "true");
    }
}
