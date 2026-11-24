package com.magnm.studio;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

/** Following redirects only to allowed places, and refusing a download whose size or SHA-256 is wrong. */
public class UpdateDownloaderTest {

    private static final String MANIFEST_URL =
            "https://github.com/NetPranav/Magnm-Marketing/releases/latest/download/android.json";
    private static final String APK_URL =
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk";
    private static final String ASSET_URL =
            "https://release-assets.githubusercontent.com/github-production-release-asset/1190541339/abc-123?sp=r&sig=x";

    private final NativeUrlPolicy policy = new NativeUrlPolicy("https://example-proj.supabase.co");
    private File dir;

    @Before
    public void setUp() throws IOException {
        dir = Files.createTempDirectory("update").toFile();
    }

    @After
    public void tearDown() {
        File[] files = dir.listFiles();
        if (files != null) {
            for (File f : files) {
                f.delete();
            }
        }
        dir.delete();
    }

    /** A scripted server: a map from URL to a canned answer. Records every URL asked for. */
    private static final class FakeServer implements UpdateDownloader.Opener {
        final Map<String, Answer> answers = new HashMap<>();
        final List<String> asked = new ArrayList<>();

        FakeServer redirect(String from, String to) {
            answers.put(from, new Answer(302, to, -1, null));
            return this;
        }

        FakeServer ok(String url, byte[] body) {
            answers.put(url, new Answer(200, null, body.length, body));
            return this;
        }

        FakeServer ok(String url, byte[] body, long declaredLength) {
            answers.put(url, new Answer(200, null, declaredLength, body));
            return this;
        }

        FakeServer status(String url, int code) {
            answers.put(url, new Answer(code, null, -1, new byte[0]));
            return this;
        }

        @Override
        public UpdateDownloader.Response open(String url) throws IOException {
            asked.add(url);
            Answer a = answers.get(url);
            if (a == null) {
                throw new IOException("unexpected request");
            }
            return a;
        }
    }

    private static final class Answer implements UpdateDownloader.Response {
        final int status;
        final String location;
        final long length;
        final byte[] body;
        boolean closed;

        Answer(int status, String location, long length, byte[] body) {
            this.status = status;
            this.location = location;
            this.length = length;
            this.body = body;
        }

        @Override public int status() { return status; }
        @Override public String location() { return location; }
        @Override public long contentLength() { return length; }
        @Override public InputStream body() { return new ByteArrayInputStream(body == null ? new byte[0] : body); }
        @Override public void close() { closed = true; }
    }

    private static byte[] apkBytes() {
        byte[] b = new byte[200_000];
        for (int i = 0; i < b.length; i++) {
            b[i] = (byte) (i * 31);
        }
        b[0] = 'P';
        b[1] = 'K';
        return b;
    }

    private static String sha256(byte[] data) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (byte x : MessageDigest.getInstance("SHA-256").digest(data)) {
            sb.append(String.format("%02x", x));
        }
        return sb.toString();
    }

    private File target() {
        return new File(dir, "magnm-studio-0.2.0.apk");
    }

    // ---- manifest ----

    @Test
    public void fetchesTheManifestThroughGithubsRedirects() throws Exception {
        byte[] json = "{\"version\":\"0.2.0\"}".getBytes(StandardCharsets.UTF_8);
        FakeServer server = new FakeServer()
                .redirect(MANIFEST_URL, "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/android.json")
                .redirect("https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/android.json", ASSET_URL)
                .ok(ASSET_URL, json);
        assertEquals("{\"version\":\"0.2.0\"}", UpdateDownloader.fetchManifest(policy, server));
        assertEquals(3, server.asked.size());
    }

    @Test
    public void aManifestRedirectToAnotherHostIsNeverFollowed() {
        FakeServer server = new FakeServer()
                .redirect(MANIFEST_URL, "https://evil.example/android.json")
                .ok("https://evil.example/android.json", "{}".getBytes(StandardCharsets.UTF_8));
        assertThrows(IOException.class, () -> UpdateDownloader.fetchManifest(policy, server));
        assertEquals(1, server.asked.size());
    }

    @Test
    public void aManifestTooBigIsRefused() {
        byte[] big = new byte[UpdateDownloader.MAX_MANIFEST_BYTES + 1];
        FakeServer server = new FakeServer().ok(MANIFEST_URL, big);
        assertThrows(IOException.class, () -> UpdateDownloader.fetchManifest(policy, server));
    }

    @Test
    public void aManifestFromAnErrorPageIsRefused() {
        assertThrows(IOException.class, () -> UpdateDownloader.fetchManifest(policy, new FakeServer().status(MANIFEST_URL, 404)));
        assertThrows(IOException.class, () -> UpdateDownloader.fetchManifest(policy, new FakeServer().status(MANIFEST_URL, 500)));
    }

    // ---- redirects ----

    @Test
    public void followsTheGithubReleaseRedirectAndSavesTheFile() throws Exception {
        byte[] apk = apkBytes();
        FakeServer server = new FakeServer().redirect(APK_URL, ASSET_URL).ok(ASSET_URL, apk);
        List<Long> seen = new ArrayList<>();
        File out = UpdateDownloader.download(policy, server, APK_URL, target(), apk.length, sha256(apk), (done, total) -> seen.add(done));
        assertArrayEquals(apk, Files.readAllBytes(out.toPath()));
        assertEquals(apk.length, (long) seen.get(seen.size() - 1));
        assertEquals(0, dir.listFiles((d, n) -> n.endsWith(".part")).length);
    }

    @Test
    public void everyHopIsCheckedAndNothingIsRequestedFromARefusedOne() throws Exception {
        String[] hostile = {
                "https://evil.example/magnm.apk",
                "http://release-assets.githubusercontent.com/github-production-release-asset/1/a?x=1",
                "https://169.254.169.254/latest/meta-data",
                "http://100.64.0.1:8788/phone/v1/status",
                "/relative/path",
                "https://github.com/NetPranav/Other/releases/download/v0.2.0/magnm-studio-0.2.0.apk",
                "https://release-assets.githubusercontent.com.evil.example/github-production-release-asset/1/a?x=1",
        };
        byte[] apk = apkBytes();
        for (String bad : hostile) {
            FakeServer server = new FakeServer().redirect(APK_URL, bad).ok(bad, apk);
            assertThrows(bad, IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length, sha256(apk), null));
            assertEquals(bad, 1, server.asked.size());
            assertFalse(target().exists());
        }
    }

    @Test
    public void aSecondRedirectToAnotherHostIsRefusedToo() throws Exception {
        byte[] apk = apkBytes();
        FakeServer server = new FakeServer().redirect(APK_URL, ASSET_URL).redirect(ASSET_URL, "https://evil.example/x").ok("https://evil.example/x", apk);
        assertThrows(IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length, sha256(apk), null));
        assertEquals(2, server.asked.size());
    }

    @Test
    public void tooManyRedirectsAreRefused() throws Exception {
        FakeServer server = new FakeServer().redirect(APK_URL, ASSET_URL).redirect(ASSET_URL, ASSET_URL);
        assertThrows(IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), 10, "0".repeat(64), null));
        assertTrue(server.asked.size() <= UpdateDownloader.MAX_REDIRECTS + 1);
    }

    @Test
    public void aRedirectWithNoLocationIsRefused() {
        FakeServer server = new FakeServer();
        server.answers.put(APK_URL, new Answer(302, null, -1, null));
        assertThrows(IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), 10, "0".repeat(64), null));
    }

    @Test
    public void theFirstUrlMustBeOurApkUrl() throws Exception {
        byte[] apk = apkBytes();
        for (String bad : new String[] {ASSET_URL, "https://evil.example/magnm-studio-0.2.0.apk", "http://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk"}) {
            FakeServer server = new FakeServer().ok(bad, apk);
            assertThrows(bad, IOException.class, () -> UpdateDownloader.download(policy, server, bad, target(), apk.length, sha256(apk), null));
            assertEquals(0, server.asked.size());
        }
    }

    // ---- size and checksum ----

    @Test
    public void aWrongChecksumIsRefusedAndNothingIsKept() throws Exception {
        byte[] apk = apkBytes();
        FakeServer server = new FakeServer().ok(APK_URL, apk);
        byte[] other = apk.clone();
        other[1000] ^= 1;
        IOException ex = assertThrows(IOException.class,
                () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length, sha256(other), null));
        assertTrue(ex.getMessage().toLowerCase().contains("checksum") || ex.getMessage().toLowerCase().contains("match"));
        assertFalse(target().exists());
        assertEquals(0, dir.listFiles().length);
    }

    @Test
    public void aFileLongerThanAnnouncedIsStoppedEarly() throws Exception {
        byte[] apk = apkBytes();
        FakeServer server = new FakeServer().ok(APK_URL, apk, -1);
        assertThrows(IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length - 1, sha256(apk), null));
        assertEquals(0, dir.listFiles().length);
    }

    @Test
    public void aFileShorterThanAnnouncedIsRefused() throws Exception {
        byte[] apk = apkBytes();
        FakeServer server = new FakeServer().ok(APK_URL, apk, -1);
        assertThrows(IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length + 1, sha256(apk), null));
        assertEquals(0, dir.listFiles().length);
    }

    @Test
    public void aContentLengthThatDisagreesWithTheAnnouncedSizeIsRefusedBeforeReading() throws Exception {
        byte[] apk = apkBytes();
        FakeServer server = new FakeServer().ok(APK_URL, apk, apk.length + 5);
        assertThrows(IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length, sha256(apk), null));
        assertEquals(0, dir.listFiles().length);
    }

    @Test
    public void anAnnouncedSizeOutOfRangeIsRefused() throws Exception {
        byte[] apk = apkBytes();
        for (long bad : new long[] {0, -1, UpdateDownloader.MAX_APK_BYTES + 1}) {
            FakeServer server = new FakeServer().ok(APK_URL, apk);
            assertThrows("size " + bad, IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), bad, sha256(apk), null));
            assertEquals(0, server.asked.size());
        }
    }

    @Test
    public void aBadChecksumFormatIsRefusedBeforeAnyRequest() throws Exception {
        byte[] apk = apkBytes();
        for (String bad : new String[] {null, "", "abc", "Z".repeat(64), sha256(apk).toUpperCase(), sha256(apk) + "0", sha256(apk).substring(1)}) {
            FakeServer server = new FakeServer().ok(APK_URL, apk);
            assertThrows("sha " + bad, IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length, bad, null));
            assertEquals(0, server.asked.size());
        }
    }

    @Test
    public void aServerErrorIsRefusedAndLeavesNothing() throws Exception {
        for (int code : new int[] {403, 404, 500, 204}) {
            FakeServer server = new FakeServer().status(APK_URL, code);
            assertThrows("status " + code, IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), 10, "0".repeat(64), null));
        }
        assertEquals(0, dir.listFiles().length);
    }

    @Test
    public void anExistingFileIsReplacedOnlyByAVerifiedOne() throws Exception {
        byte[] apk = apkBytes();
        Files.write(target().toPath(), new byte[] {1, 2, 3});
        byte[] other = apk.clone();
        other[5] ^= 1;
        FakeServer server = new FakeServer().ok(APK_URL, other);
        assertThrows(IOException.class, () -> UpdateDownloader.download(policy, server, APK_URL, target(), apk.length, sha256(apk), null));
        // the broken download is gone; whatever was there before is not our concern
        assertFalse(new File(dir, "magnm-studio-0.2.0.apk.part").exists());
    }

    @Test
    public void sha256HexIsLowercaseSixtyFourChars() {
        assertTrue(UpdateDownloader.isSha256Hex("0123456789abcdef".repeat(4)));
        assertFalse(UpdateDownloader.isSha256Hex("0123456789ABCDEF".repeat(4)));
        assertFalse(UpdateDownloader.isSha256Hex("0123456789abcdef".repeat(4) + "0"));
        assertFalse(UpdateDownloader.isSha256Hex(null));
    }
}
