package com.magnm.studio;

import static org.junit.Assert.assertFalse;
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
    public void startingAShareKeepsFilesAnotherShareIsUsing() throws Exception {
        java.io.File dir = Files.createTempDirectory("share").toFile();
        java.io.File recent = new java.io.File(dir, "recent.mp4");
        java.io.File old = new java.io.File(dir, "old.mp4");
        assertTrue(recent.createNewFile() && old.createNewFile());
        assertTrue(old.setLastModified(System.currentTimeMillis() - 2L * 60 * 60 * 1000));
        ShareOutPolicy.clearShareDir(dir);
        assertTrue(recent.exists());
        assertTrue(!old.exists());
    }

    @Test
    public void sharedCookieJarIsSwitchedOff() throws Exception {
        assertTrue(read("src/main/java/com/magnm/studio/MagnmNativePlugin.java").contains("CookieHandler.setDefault(null)"));
    }

    @Test
    public void noFileProviderAndNoScannerTelemetry() throws Exception {
        String manifest = read("src/main/AndroidManifest.xml");
        assertTrue(manifest.contains("androidx.core.content.FileProvider"));
        assertTrue(manifest.contains("android:authorities=\"${applicationId}.fileprovider\""));
        assertTrue(manifest.contains("android:exported=\"false\""));
        assertTrue(manifest.contains("android:grantUriPermissions=\"true\""));
        assertTrue(manifest.contains("@xml/file_paths"));
        String filePaths = read("src/main/res/xml/file_paths.xml");
        assertTrue(filePaths.contains("<cache-path"));
        assertTrue(filePaths.contains("path=\"share/\""));
        assertTrue(!filePaths.contains("files-path"));
        assertTrue(!filePaths.contains("external-"));
        assertTrue(!filePaths.contains("root-path"));
        assertTrue(manifest.contains("TransportBackendDiscovery"));
    }

    @Test
    public void cloudFileShareValidatesTheSignedUrlAndSendsNoCredentials() throws Exception {
        String plugin = read("src/main/java/com/magnm/studio/MagnmNativePlugin.java");
        int at = plugin.indexOf("public void shareCloudFile(PluginCall call)");
        assertTrue(at > 0);
        int next = plugin.indexOf("@PluginMethod", at);
        String body = plugin.substring(at, next > 0 ? next : plugin.length());
        assertTrue(body.contains("urlPolicy.validateCloudFileUrl("));
        assertTrue(body.contains("Collections.emptyMap()"));
        assertFalse(body.contains("headers"));
        assertFalse(body.contains("Authorization"));
        // The download is the same bounded, redirect-refusing, type- and magic-checked path as the laptop file share.
        assertTrue(body.contains("executeShareLaptopFile("));
    }
}
