package com.magnm.studio;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

/** The three update URL checks in NativeUrlPolicy: the manifest, the APK, and where a redirect may lead. */
public class NativeUpdateUrlsTest {

    private final NativeUrlPolicy policy = new NativeUrlPolicy("https://example-proj.supabase.co");

    private static final String MANIFEST =
            "https://github.com/NetPranav/Magnm-Marketing/releases/latest/download/android.json";
    private static final String APK =
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk";
    private static final String ASSET =
            "https://release-assets.githubusercontent.com/github-production-release-asset/1190541339/"
                    + "6a3c1d2e-8f4b-4c7a-9d10-0123456789ab?sp=r&sv=2018-11-09&sig=abc%2Fdef%3D&rscd=attachment";

    @Test
    public void acceptsTheManifestAndTheApk() {
        assertEquals(MANIFEST, policy.validateUpdateManifestUrl(MANIFEST));
        assertEquals(APK, policy.validateUpdateApkUrl(APK));
    }

    @Test
    public void theGeneralRequestPathStillRefusesGithub() {
        for (String url : new String[] {MANIFEST, APK, ASSET}) {
            assertThrows(url, NativeUrlPolicy.PolicyException.class, () -> policy.validateUrl(url));
        }
    }

    @Test
    public void theManifestIsExactlyOneUrl() {
        String[] bad = {
                null,
                "",
                MANIFEST + "?x=1",
                MANIFEST + "#frag",
                MANIFEST.replace("android.json", "android.json/"),
                MANIFEST.replace("android.json", "other.json"),
                MANIFEST.replace("latest/download", "download/v0.2.0"),
                MANIFEST.replace("https://", "http://"),
                MANIFEST.replace("github.com", "github.com:8443"),
                MANIFEST.replace("github.com", "github.com@evil.example"),
                MANIFEST.replace("github.com", "evil.example"),
                MANIFEST.replace("github.com", "github.com.evil.example"),
                MANIFEST.replace("NetPranav", "Evil"),
                MANIFEST.replace("Magnm-Marketing", "Other"),
                MANIFEST.replace("/releases/", "/releases/../"),
                MANIFEST.replace("/releases/", "//releases/"),
                MANIFEST.replace("/releases/", "/%2e%2e/releases/"),
                MANIFEST + "%00",
                " " + MANIFEST,
                MANIFEST.replace("github.com", "GITHUB.com.evil.example"),
                "ftp://github.com/NetPranav/Magnm-Marketing/releases/latest/download/android.json",
                "file:///etc/passwd",
        };
        for (String url : bad) {
            assertThrows("manifest url " + url, NativeUrlPolicy.PolicyException.class, () -> policy.validateUpdateManifestUrl(url));
        }
    }

    @Test
    public void theApkMustBeOurReleaseAssetWithMatchingVersions() {
        String[] bad = {
                null,
                "",
                APK + "?x=1",
                APK + "#frag",
                APK.replace("https://", "http://"),
                APK.replace("github.com", "evil.example"),
                APK.replace("github.com", "github.com.evil.example"),
                APK.replace("github.com", "user@github.com"),
                APK.replace("github.com", "github.com:444"),
                APK.replace("NetPranav", "Evil"),
                APK.replace("Magnm-Marketing", "Other"),
                // tag and file name must carry the same version
                APK.replace("magnm-studio-0.2.0.apk", "magnm-studio-0.3.0.apk"),
                APK.replace("/v0.2.0/", "/v0.3.0/"),
                APK.replace("/v0.2.0/", "/latest/"),
                APK.replace("/v0.2.0/", "/0.2.0/"),
                APK.replace("v0.2.0", "v0.2"),
                // not our file name
                APK.replace("magnm-studio-0.2.0.apk", "other.apk"),
                APK.replace("magnm-studio-0.2.0.apk", "magnm-studio-0.2.0.apk.sh"),
                APK.replace("magnm-studio-0.2.0.apk", "magnm-studio-0.2.0.zip"),
                APK.replace("magnm-studio-0.2.0.apk", "magnm-studio-0.2.0.apk/x"),
                APK.replace("magnm-studio-0.2.0.apk", "../magnm-studio-0.2.0.apk"),
                APK.replace("magnm-studio-0.2.0.apk", "%2e%2e/magnm-studio-0.2.0.apk"),
                APK.replace("magnm-studio-0.2.0.apk", "magnm-studio-0.2.0%2eapk"),
                APK.replace("magnm-studio-0.2.0.apk", "magnm-studio-0.2.0.apk%00"),
                APK.replace("/download/", "/archive/"),
                APK.replace("\\", "/") + "\\x",
                "ftp://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk",
        };
        for (String url : bad) {
            assertThrows("apk url " + url, NativeUrlPolicy.PolicyException.class, () -> policy.validateUpdateApkUrl(url));
        }
    }

    @Test
    public void aRedirectMayGoToOurReleaseOrTheGithubAssetHostOnly() {
        assertEquals(APK, policy.validateUpdateRedirectUrl(APK));
        assertEquals(
                "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/android.json",
                policy.validateUpdateRedirectUrl("https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/android.json"));
        assertEquals(ASSET, policy.validateUpdateRedirectUrl(ASSET));
        assertEquals(
                "https://objects.githubusercontent.com/github-production-release-asset-2e65be/1190541339/6a3c1d2e-8f4b?X-Amz-Signature=ab",
                policy.validateUpdateRedirectUrl(
                        "https://objects.githubusercontent.com/github-production-release-asset-2e65be/1190541339/6a3c1d2e-8f4b?X-Amz-Signature=ab"));
    }

    @Test
    public void aRedirectToAnythingElseIsRefused() {
        String[] bad = {
                null,
                "",
                "/relative/path",
                "//evil.example/x",
                "http://release-assets.githubusercontent.com/github-production-release-asset/1/a?x=1",
                "https://evil.example/github-production-release-asset/1/a?x=1",
                "https://githubusercontent.com/github-production-release-asset/1/a",
                "https://raw.githubusercontent.com/NetPranav/Magnm-Marketing/main/android.json",
                "https://user-images.githubusercontent.com/github-production-release-asset/1/a",
                "https://release-assets.githubusercontent.com.evil.example/github-production-release-asset/1/a",
                "https://release-assets.githubusercontent.com:8443/github-production-release-asset/1/a",
                "https://user@release-assets.githubusercontent.com/github-production-release-asset/1/a",
                "https://release-assets.githubusercontent.com/other/1/a",
                "https://release-assets.githubusercontent.com/github-production-release-asset/../x",
                "https://release-assets.githubusercontent.com/github-production-release-asset/1/%2e%2e",
                "https://release-assets.githubusercontent.com/github-production-release-asset/1/a#frag",
                "https://github.com/NetPranav/Magnm-Marketing/raw/main/x",
                "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/other.zip",
                "https://github.com/NetPranav/Magnm-Marketing/releases/download/latest/android.json",
                "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/android.json?x=1",
                "https://127.0.0.1/github-production-release-asset/1/a",
                "https://169.254.169.254/latest/meta-data",
                "https://[::1]/github-production-release-asset/1/a",
                "https://10.0.0.5/x",
                "http://100.64.0.1:8788/phone/v1/status",
                "https://example-proj.supabase.co/rest/v1/x",
                "javascript:alert(1)",
                "file:///data/data/com.magnm.studio/x",
        };
        for (String url : bad) {
            assertThrows("redirect " + url, NativeUrlPolicy.PolicyException.class, () -> policy.validateUpdateRedirectUrl(url));
        }
    }

    @Test
    public void refusalsNeverEchoTheUrl() {
        NativeUrlPolicy.PolicyException ex = assertThrows(
                NativeUrlPolicy.PolicyException.class,
                () -> policy.validateUpdateRedirectUrl("https://evil.example/secret-token-123"));
        assertEquals(false, ex.getMessage().contains("secret-token-123"));
    }
}
