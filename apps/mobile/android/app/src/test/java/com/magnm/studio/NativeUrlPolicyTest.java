package com.magnm.studio;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.Test;

public class NativeUrlPolicyTest {

    private static final String SUPA_ORIGIN = "https://example-proj.supabase.co";
    private final NativeUrlPolicy policy = new NativeUrlPolicy(SUPA_ORIGIN);

    @Test
    public void acceptsValidTailscaleLaptopUrls() {
        assertEquals(
                "http://100.64.0.1:8788/phone/v1/status",
                policy.validateUrl("http://100.64.0.1:8788/phone/v1/status")
        );
        assertEquals(
                "http://100.127.255.254:8788/phone/v1/drafts?limit=20",
                policy.validateUrl("http://100.127.255.254:8788/phone/v1/drafts?limit=20")
        );
    }

    @Test
    public void rejectsLaptopBypassAttemptsAndNonTailscaleIps() {
        String[] badUrls = {
                // Wrong scheme or missing port
                "https://100.64.0.1:8788/phone/v1/status",
                "http://100.64.0.1/phone/v1/status",
                "http://100.64.0.1:08788/phone/v1/status",
                "http://100.64.0.1:65536/phone/v1/status",
                // Outside 100.64.0.0/10
                "http://100.63.255.255:8788/phone/v1/status",
                "http://100.128.0.0:8788/phone/v1/status",
                "http://127.0.0.1:8788/phone/v1/status",
                "http://169.254.169.254:8788/phone/v1/status",
                "http://192.168.1.50:8788/phone/v1/status",
                "http://10.0.0.1:8788/phone/v1/status",
                // Non-decimal / short / IPv6 / hostname tricks
                "http://0144.0100.0000.0001:8788/phone/v1/status",
                "http://0x64.0x40.0x00.0x01:8788/phone/v1/status",
                "http://1681915905:8788/phone/v1/status",
                "http://100.64.0.1.:8788/phone/v1/status",
                "http://100.064.0.1:8788/phone/v1/status",
                "http://100.64.1:8788/phone/v1/status",
                "http://[::ffff:100.64.0.1]:8788/phone/v1/status",
                "http://100.64.0.1.nip.io:8788/phone/v1/status",
                // Userinfo and fragment bypasses
                "http://user@100.64.0.1:8788/phone/v1/status",
                "http://100.64.0.1:8788@evil.example/phone/v1/status",
                "http://100.64.0.1:8788%40evil.example/phone/v1/status",
                "http://100.64.0.1:8788/phone/v1/status#frag",
                // Path restrictions and traversal bypasses
                "http://100.64.0.1:8788/phone/v1",
                "http://100.64.0.1:8788/phone/v1/",
                "http://100.64.0.1:8788/api/status",
                "http://100.64.0.1:8788/phone/v1/../admin",
                "http://100.64.0.1:8788/phone/v1/./status",
                "http://100.64.0.1:8788/phone/v1/%2e%2e/admin",
                "http://100.64.0.1:8788/phone/v1/%2E%2E/admin",
                "http://100.64.0.1:8788/phone/v1/%2fadmin",
                "http://100.64.0.1:8788/phone/v1/%5cadmin",
                "http://100.64.0.1:8788/phone/v1/..;/admin",
                "http://100.64.0.1:8788/phone/v1//status",
                "http://100.64.0.1:8788\\phone\\v1\\status",
                "http://100.64.0.1:8788/phone/v1/status\r\nHost: evil.example",
                // Other schemes
                "file:///etc/passwd",
                "javascript:alert(1)"
        };

        for (String url : badUrls) {
            NativeUrlPolicy.PolicyException ex = assertThrows(
                    "Expected rejection for " + url,
                    NativeUrlPolicy.PolicyException.class,
                    () -> policy.validateUrl(url)
            );
            assertFalse("Error leaked URL", ex.getMessage().contains("100.64"));
            assertFalse("Error leaked host", ex.getMessage().contains("evil.example"));
        }
    }

    @Test
    public void acceptsAndRestrictsSupabaseAndNimDestinations() {
        assertEquals(
                SUPA_ORIGIN + "/auth/v1/token?grant_type=pkce",
                policy.validateUrl(SUPA_ORIGIN + "/auth/v1/token?grant_type=pkce")
        );
        assertEquals(
                SUPA_ORIGIN + "/rest/v1/profiles?select=id",
                policy.validateUrl("HTTPS://EXAMPLE-PROJ.SUPABASE.CO:443/rest/v1/profiles?select=id")
        );
        assertEquals(
                SUPA_ORIGIN + "/functions/v1/ai-proxy",
                policy.validateUrl(SUPA_ORIGIN + "/functions/v1/ai-proxy")
        );
        assertEquals(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                policy.validateUrl("https://integrate.api.nvidia.com/v1/chat/completions")
        );

        String[] forbiddenHttps = {
                "http://example-proj.supabase.co/rest/v1/profiles",
                SUPA_ORIGIN + ":8443/rest/v1/profiles",
                SUPA_ORIGIN + "/storage/v1/object/secret",
                SUPA_ORIGIN + "/functions/v1/other",
                SUPA_ORIGIN + "/functions/v1/ai-proxy/extra",
                SUPA_ORIGIN + "/rest/v1/../auth/v1/admin",
                "https://attacker@example-proj.supabase.co/rest/v1/profiles",
                "https://evil.example/v1/chat/completions",
                "https://integrate.api.nvidia.com/v1/",
                "https://integrate.api.nvidia.com/v2/models",
                "https://integrate.api.nvidia.com:8443/v1/chat/completions"
        };

        for (String url : forbiddenHttps) {
            NativeUrlPolicy.PolicyException ex = assertThrows(
                    "Expected rejection for " + url,
                    NativeUrlPolicy.PolicyException.class,
                    () -> policy.validateUrl(url)
            );
            assertFalse("Error leaked supabase host", ex.getMessage().contains("example-proj"));
            assertFalse("Error leaked evil host", ex.getMessage().contains("evil.example"));
        }
    }

    @Test
    public void rejectsCrlfAndForbiddenHeadersWithoutLeakingValues() {
        Map<String, String> ok = new LinkedHashMap<>();
        ok.put("Authorization", "Bearer secret-token-123");
        ok.put("Content-Type", "application/json");
        assertEquals(2, policy.validateHeaders(ok).size());

        Map<String, String> crlfVal = Collections.singletonMap("X-Custom", "abc\r\nEvil: true");
        NativeUrlPolicy.PolicyException valEx = assertThrows(
                NativeUrlPolicy.PolicyException.class,
                () -> policy.validateHeaders(crlfVal)
        );
        assertFalse(valEx.getMessage().contains("abc"));
        assertFalse(valEx.getMessage().contains("Evil"));

        Map<String, String> crlfKey = Collections.singletonMap("X-Test\nInjected", "1");
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateHeaders(crlfKey));

        Map<String, String> trailingCrlfKey = Collections.singletonMap("X-Test\r\n", "1");
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateHeaders(trailingCrlfKey));

        Map<String, String> cookieHeader = Collections.singletonMap("Cookie", "sid=1");
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateHeaders(cookieHeader));

        Map<String, String> hostHeader = Collections.singletonMap("Host", "attacker.example");
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateHeaders(hostHeader));
    }

    @Test
    public void enforcesMethodTimeoutAndBodyLimits() {
        assertEquals("POST", policy.validateMethod("post"));
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateMethod("TRACE"));
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateMethod("GET\r\n"));

        assertEquals(NativeUrlPolicy.DEFAULT_TIMEOUT_MS, policy.resolveTimeoutMs(null));
        assertEquals(180_000, policy.resolveTimeoutMs(180_000));
        assertEquals(NativeUrlPolicy.MAX_TIMEOUT_MS, policy.resolveTimeoutMs(999_999));
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.resolveTimeoutMs(0));
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.resolveTimeoutMs(-10));

        assertArrayEquals("hi".getBytes(StandardCharsets.UTF_8), policy.validateBody("hi"));

        byte[] overLimit = new byte[NativeUrlPolicy.MAX_REQUEST_BYTES + 1];
        java.util.Arrays.fill(overLimit, (byte) 'a');
        String hugeBody = new String(overLimit, StandardCharsets.US_ASCII);
        NativeUrlPolicy.PolicyException bodyEx = assertThrows(
                NativeUrlPolicy.PolicyException.class,
                () -> policy.validateBody(hugeBody)
        );
        assertTrue(bodyEx.getMessage().contains("size limit"));
        assertFalse(bodyEx.getMessage().contains("aaaa"));
    }

    @Test
    public void validatesLaptopFileUrlsStrictly() {
        assertEquals(
                "http://100.64.0.1:8788/phone/v1/files?path=outputs%2Fdemo.mp4",
                policy.validateLaptopFileUrl("http://100.64.0.1:8788/phone/v1/files?path=outputs%2Fdemo.mp4")
        );

        String[] badFileUrls = {
                "https://100.64.0.1:8788/phone/v1/files?path=a.mp4",
                "http://100.64.0.1:8788/phone/v1/status",
                "http://100.64.0.1:8788/phone/v1/files/../drafts",
                "http://100.64.0.1:8788/api/files?path=a.mp4",
                "http://192.168.1.5:8788/phone/v1/files?path=a.mp4",
                "http://user@100.64.0.1:8788/phone/v1/files?path=a.mp4",
                "http://100.64.0.1:8788/phone/v1/files?path=a.mp4#frag",
                SUPA_ORIGIN + "/rest/v1/profiles",
        };
        for (String url : badFileUrls) {
            assertThrows(
                    "Expected laptop file URL rejection for " + url,
                    NativeUrlPolicy.PolicyException.class,
                    () -> policy.validateLaptopFileUrl(url)
            );
        }
    }

    private static final String UID = "11111111-2222-4333-8444-555555555501";
    private static final String HASH = "a".repeat(64);
    private static final String TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJ1cmwiOiJ4In0.c2lnbmF0dXJl";
    private static final String SIGN = SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/" + UID + "/" + HASH;

    @Test
    public void allowsOnlyTheExactPhoneCopyStoragePath() {
        assertEquals(SIGN + ".png", policy.validateUrl(SIGN + ".png"));
        assertEquals(SIGN + ".pdf?token=" + TOKEN, policy.validateUrl(SIGN + ".pdf?token=" + TOKEN));
        String[] bad = {
                SUPA_ORIGIN + "/storage/v1/object/sign/previews/" + UID + "/" + HASH + ".png",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/" + UID + "/x/" + HASH + ".png",
                SIGN + ".html",
                SIGN + ".svg",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/" + UID + "/evil.png",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/" + UID + "/" + "A".repeat(64) + ".png",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/" + UID + "/" + "a".repeat(63) + ".png",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/not-a-uuid/" + HASH + ".png",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/" + UID + "/../" + UID + "/" + HASH + ".png",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/" + UID + "/..%2F" + HASH + ".png",
                SUPA_ORIGIN + "/storage/v1/object/sign/phone-files/",
                SUPA_ORIGIN + "/storage/v1/object/phone-files/" + UID + "/" + HASH + ".png",
                SUPA_ORIGIN + "/storage/v1/object/authenticated/phone-files/" + UID + "/" + HASH + ".png",
                SUPA_ORIGIN + "/storage/v1/object/public/phone-files/" + UID + "/" + HASH + ".png",
                SUPA_ORIGIN + "/storage/v1/object/list/phone-files",
                SUPA_ORIGIN + "/storage/v1/bucket",
                SIGN + ".png?token=" + TOKEN + "&download=1",
                SIGN + ".png?other=" + TOKEN,
                SIGN + ".png?token=",
                SIGN + ".png?token=a b",
                SIGN + ".png;x",
                SIGN + ".png#frag",
                "https://evil.example.com/storage/v1/object/sign/phone-files/" + UID + "/" + HASH + ".png",
                "http://example-proj.supabase.co/storage/v1/object/sign/phone-files/" + UID + "/" + HASH + ".png",
        };
        for (String url : bad) {
            assertThrows("Expected rejection for " + url, NativeUrlPolicy.PolicyException.class, () -> policy.validateUrl(url));
        }
    }

    @Test
    public void validatesSignedCloudFileUrlsStrictly() {
        assertEquals(SIGN + ".mp4?token=" + TOKEN, policy.validateCloudFileUrl(SIGN + ".mp4?token=" + TOKEN));
        String[] bad = {
                SIGN + ".mp4",
                SIGN + ".mp4?",
                SIGN + ".mp4?token=",
                SIGN + ".mp4?token=" + TOKEN + "&x=1",
                SIGN + ".mp4?x=" + TOKEN,
                SIGN + ".mp4?token=" + "x".repeat(5000),
                SUPA_ORIGIN + "/rest/v1/profiles?token=" + TOKEN,
                "http://100.64.0.1:8788/phone/v1/files?path=a.mp4",
                "https://evil.example.com/storage/v1/object/sign/phone-files/" + UID + "/" + HASH + ".png?token=" + TOKEN,
        };
        for (String url : bad) {
            assertThrows("Expected cloud file URL rejection for " + url, NativeUrlPolicy.PolicyException.class, () -> policy.validateCloudFileUrl(url));
        }
    }

    @Test
    public void earlierSupabasePathsStayAsNarrowAsBefore() {
        assertEquals(SUPA_ORIGIN + "/rest/v1/phone_files?owner_id=eq.x", policy.validateUrl(SUPA_ORIGIN + "/rest/v1/phone_files?owner_id=eq.x"));
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateUrl(SUPA_ORIGIN + "/storage/v1/object/previews/" + UID + "/x.webp"));
        assertThrows(NativeUrlPolicy.PolicyException.class, () -> policy.validateUrl(SUPA_ORIGIN + "/functions/v1/other"));
    }
}
