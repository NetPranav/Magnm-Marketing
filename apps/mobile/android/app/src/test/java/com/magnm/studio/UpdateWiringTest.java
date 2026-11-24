package com.magnm.studio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import org.junit.Test;

/** Source checks for the update wiring that needs a phone to exercise. */
public class UpdateWiringTest {

    private static String read(String path) throws Exception {
        return new String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
    }

    private static String methodBody(String source, String signature) {
        int at = source.indexOf(signature);
        assertTrue("missing " + signature, at > 0);
        int next = source.indexOf("@PluginMethod", at);
        return source.substring(at, next > 0 ? next : source.length());
    }

    @Test
    public void manifestAllowsInstallingAndTheUpdateFolderIsShareable() throws Exception {
        String manifest = read("src/main/AndroidManifest.xml");
        assertTrue(manifest.contains("android.permission.REQUEST_INSTALL_PACKAGES"));
        String paths = read("src/main/res/xml/file_paths.xml");
        assertTrue(paths.contains("<cache-path name=\"updates\" path=\"updates/\""));
        assertFalse(paths.contains("files-path"));
        assertFalse(paths.contains("root-path"));
        assertFalse(paths.contains("external-"));
    }

    @Test
    public void theInstallCallChecksTheUrlThenThePermissionThenDownloadsThroughTheChecker() throws Exception {
        String plugin = read("src/main/java/com/magnm/studio/MagnmNativePlugin.java");
        String body = methodBody(plugin, "public void downloadAndInstallUpdate(PluginCall call)");
        assertTrue(body.contains("urlPolicy.validateUpdateApkUrl("));
        assertTrue(body.contains("canRequestPackageInstalls()"));
        assertTrue(body.contains("ACTION_MANAGE_UNKNOWN_APP_SOURCES"));
        assertTrue(body.indexOf("validateUpdateApkUrl") < body.indexOf("canRequestPackageInstalls"));
        assertTrue(body.indexOf("canRequestPackageInstalls") < body.indexOf("executeUpdateDownload("));
        int exec = plugin.indexOf("private void executeUpdateDownload(");
        String download = plugin.substring(exec, plugin.indexOf("@PluginMethod", exec));
        assertTrue(download.contains("UpdateDownloader.download("));
        assertTrue(download.contains("getPackageArchiveInfo("));
        assertTrue(download.contains("application/vnd.android.package-archive"));
        assertTrue(download.contains("FileProvider.getUriForFile("));
    }

    @Test
    public void theUpdateConnectionsNeverFollowRedirectsThemselves() throws Exception {
        String plugin = read("src/main/java/com/magnm/studio/MagnmNativePlugin.java");
        int at = plugin.indexOf("private static UpdateDownloader.Opener updateOpener()");
        assertTrue(at > 0);
        String body = plugin.substring(at, plugin.indexOf("@PluginMethod", at));
        assertTrue(body.contains("setInstanceFollowRedirects(false)"));
        assertTrue(body.contains("Proxy.NO_PROXY"));
        assertFalse(body.contains("setRequestProperty"));
        assertFalse(body.toLowerCase().contains("authorization"));
        assertEquals(-1, body.indexOf("setInstanceFollowRedirects(true)"));
    }

    @Test
    public void theManifestCallTakesNoUrlFromTheWebLayer() throws Exception {
        String plugin = read("src/main/java/com/magnm/studio/MagnmNativePlugin.java");
        String body = methodBody(plugin, "public void fetchUpdateManifest(PluginCall call)");
        assertFalse(body.contains("call.getString"));
        assertTrue(body.contains("UpdateDownloader.fetchManifest("));
    }
}
