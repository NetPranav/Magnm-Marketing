package com.magnm.studio;

import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import org.junit.Test;

/** Review regressions. Source checks cover wiring that needs a device to exercise. */
public class NativeHardeningTest {

    private static String read(String path) throws Exception {
        return new String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
    }

    @Test
    public void wideImagesNeverDecodeAtFullSize() {
        for (int[] size : new int[][] { { 16000, 4095 }, { 30000, 2100 }, { 4095, 16000 } }) {
            int sample = ShareIntakePolicy.computeInSampleSize(size[0], size[1], 2048);
            assertTrue((long) (size[0] / sample) * (size[1] / sample) <= 4096L * 4096L);
            assertTrue(Math.max(size[0], size[1]) / sample < 4096);
        }
    }

    @Test
    public void coreHttpAndWebViewPluginsAreReplaced() throws Exception {
        String activity = read("src/main/java/com/magnm/studio/MainActivity.java");
        assertTrue(activity.contains("registerPlugin(DisabledCorePlugins.Http.class)"));
        assertTrue(activity.contains("registerPlugin(DisabledCorePlugins.WebViewControl.class)"));
    }

    @Test
    public void sharedCookieJarIsSwitchedOff() throws Exception {
        assertTrue(read("src/main/java/com/magnm/studio/MagnmNativePlugin.java").contains("CookieHandler.setDefault(null)"));
    }

    @Test
    public void noFileProviderAndNoScannerTelemetry() throws Exception {
        String manifest = read("src/main/AndroidManifest.xml");
        assertTrue(!manifest.contains("FileProvider"));
        assertTrue(manifest.contains("TransportBackendDiscovery"));
    }
}
