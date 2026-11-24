package com.magnm.studio;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Pure-Java download of the update manifest and the APK, testable on the JVM.
 *
 * Redirects are followed by hand, never by the connection: the first URL must be our manifest or APK address and
 * every Location after it must pass {@link NativeUrlPolicy#validateUpdateRedirectUrl}, up to {@link #MAX_REDIRECTS}
 * hops. The APK is only kept when its size is exactly the announced size and its SHA-256 equals the announced one;
 * anything else is deleted. All messages are plain sentences and never contain a URL.
 */
public final class UpdateDownloader {

    public static final long MAX_APK_BYTES = 250L * 1024 * 1024;
    public static final int MAX_MANIFEST_BYTES = 64 * 1024;
    public static final int MAX_REDIRECTS = 5;
    private static final Pattern SHA256_HEX = Pattern.compile("^[0-9a-f]{64}$");

    /** One answer from the server. The caller closes it. */
    public interface Response extends Closeable {
        int status();

        /** The Location header of a redirect, or null. */
        String location();

        /** The Content-Length, or -1 when unknown. */
        long contentLength();

        InputStream body() throws IOException;

        @Override
        void close();
    }

    /** Sends one GET (no redirect following, no cookies, no credentials) and returns the answer. */
    public interface Opener {
        Response open(String url) throws IOException;
    }

    public interface Progress {
        void onProgress(long done, long total);
    }

    private UpdateDownloader() {}

    public static boolean isSha256Hex(String value) {
        return value != null && SHA256_HEX.matcher(value).matches();
    }

    private static boolean isRedirect(int status) {
        return status == 301 || status == 302 || status == 303 || status == 307 || status == 308;
    }

    /** Open `firstUrl` (already validated by the caller) and follow allowed redirects until a 200 answer. */
    private static Response openFollowing(NativeUrlPolicy policy, Opener opener, String firstUrl) throws IOException {
        String url = firstUrl;
        for (int hop = 0; hop <= MAX_REDIRECTS; hop++) {
            Response response = opener.open(url);
            int status = response.status();
            if (status == 200) {
                return response;
            }
            try {
                if (!isRedirect(status)) {
                    throw new IOException("The update server answered with an error (" + status + "). Try again later.");
                }
                String location = response.location();
                if (location == null || location.isEmpty()) {
                    throw new IOException("The update server sent a broken redirect.");
                }
                try {
                    url = policy.validateUpdateRedirectUrl(location);
                } catch (NativeUrlPolicy.PolicyException ex) {
                    throw new IOException("The update server sent the app somewhere it may not go, so nothing was downloaded.");
                }
            } finally {
                response.close();
            }
        }
        throw new IOException("The update server redirected too many times.");
    }

    /** Fetch the manifest text (UTF-8, at most 64 KB) from the fixed manifest address. */
    public static String fetchManifest(NativeUrlPolicy policy, Opener opener) throws IOException {
        final String url;
        try {
            url = policy.validateUpdateManifestUrl(NativeUrlPolicy.UPDATE_MANIFEST_URL);
        } catch (NativeUrlPolicy.PolicyException ex) {
            throw new IOException("The update address is not valid.");
        }
        try (Response response = openFollowing(policy, opener, url)) {
            long length = response.contentLength();
            if (length > MAX_MANIFEST_BYTES) {
                throw new IOException("The update information is too big to be right.");
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            try (InputStream in = response.body()) {
                int n;
                while ((n = in.read(buf)) != -1) {
                    if (out.size() + n > MAX_MANIFEST_BYTES) {
                        throw new IOException("The update information is too big to be right.");
                    }
                    out.write(buf, 0, n);
                }
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    /**
     * Download the APK at `apkUrl` into `dest`, keeping it only if it is exactly `expectedBytes` long and its SHA-256
     * is `expectedSha256` (lower-case hex). Returns `dest`.
     */
    public static File download(
            NativeUrlPolicy policy,
            Opener opener,
            String apkUrl,
            File dest,
            long expectedBytes,
            String expectedSha256,
            Progress progress) throws IOException {
        final String url;
        try {
            url = policy.validateUpdateApkUrl(apkUrl);
        } catch (NativeUrlPolicy.PolicyException ex) {
            throw new IOException("The update address is not one of ours, so nothing was downloaded.");
        }
        if (expectedBytes < 1 || expectedBytes > MAX_APK_BYTES) {
            throw new IOException("The update's announced size is not right, so nothing was downloaded.");
        }
        if (!isSha256Hex(expectedSha256)) {
            throw new IOException("The update's checksum is missing or malformed, so nothing was downloaded.");
        }
        File part = new File(dest.getParentFile(), dest.getName() + ".part");
        boolean done = false;
        try (Response response = openFollowing(policy, opener, url)) {
            long length = response.contentLength();
            if (length >= 0 && length != expectedBytes) {
                throw new IOException("The update is not the size it was announced to be, so it was not kept.");
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            long total = 0;
            byte[] buf = new byte[32 * 1024];
            try (InputStream in = response.body(); FileOutputStream out = new FileOutputStream(part)) {
                int n;
                while ((n = in.read(buf)) != -1) {
                    total += n;
                    if (total > expectedBytes) {
                        throw new IOException("The update is bigger than it was announced to be, so it was not kept.");
                    }
                    digest.update(buf, 0, n);
                    out.write(buf, 0, n);
                    if (progress != null) {
                        progress.onProgress(total, expectedBytes);
                    }
                }
            }
            if (total != expectedBytes) {
                throw new IOException("The download stopped early, so the update was not kept. Try again.");
            }
            byte[] want = hexToBytes(expectedSha256);
            if (!MessageDigest.isEqual(digest.digest(), want)) {
                throw new IOException("The downloaded update's checksum does not match, so it was not kept.");
            }
            if (dest.exists() && !dest.delete()) {
                throw new IOException("Could not replace the earlier download.");
            }
            if (!part.renameTo(dest)) {
                throw new IOException("Could not save the update.");
            }
            done = true;
            return dest;
        } catch (NoSuchAlgorithmException ex) {
            throw new IOException("This phone cannot check the update's checksum.");
        } finally {
            if (!done && part.exists()) {
                part.delete();
            }
        }
    }

    /** Lower-case hex of a SHA-256 (or any) digest of a file. */
    public static String sha256Of(File file) throws IOException {
        try (InputStream in = new java.io.FileInputStream(file)) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buf = new byte[32 * 1024];
            int n;
            while ((n = in.read(buf)) != -1) {
                digest.update(buf, 0, n);
            }
            StringBuilder sb = new StringBuilder();
            for (byte b : digest.digest()) {
                sb.append(String.format(Locale.ROOT, "%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException ex) {
            throw new IOException("This phone cannot check the update's checksum.");
        }
    }

    private static byte[] hexToBytes(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(hex.substring(2 * i, 2 * i + 2), 16);
        }
        return out;
    }
}
