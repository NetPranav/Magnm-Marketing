package com.magnm.studio;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Pure-Java validation, streaming limits, magic-byte checks, and safe cacheDir/share naming for share-out.
 */
public final class ShareOutPolicy {

    public static final long MAX_SHARE_FILE_BYTES = 200L * 1024L * 1024L;
    public static final int MAX_PDF_BYTES = 10 * 1024 * 1024;
    public static final int MAX_PDF_BASE64_CHARS = 14 * 1024 * 1024;
    public static final int MAGIC_HEADER_BYTES = 64;
    public static final String SHARE_SUBDIR = "share";
    // The receiving app reads the file after our chooser closes, and another share may still be downloading.
    private static final long KEEP_SHARED_FILES_MS = 60L * 60L * 1000L;

    private static final Map<String, String> MIME_TO_EXT;

    static {
        Map<String, String> exts = new HashMap<>();
        exts.put("video/mp4", ".mp4");
        exts.put("video/webm", ".webm");
        exts.put("image/png", ".png");
        exts.put("image/jpeg", ".jpg");
        exts.put("image/webp", ".webp");
        exts.put("application/pdf", ".pdf");
        MIME_TO_EXT = Collections.unmodifiableMap(exts);
    }

    private ShareOutPolicy() {}

    public static final class StreamResult {
        public final long totalBytes;
        public final byte[] header;
        public final int headerLength;

        public StreamResult(long totalBytes, byte[] header, int headerLength) {
            this.totalBytes = totalBytes;
            this.header = header;
            this.headerLength = headerLength;
        }
    }

    /** Normalize Content-Type and ensure it is one of the 6 allowed share-out types. */
    public static String normalizeAndValidateShareMime(String rawContentType) {
        if (rawContentType == null) {
            throw new IllegalArgumentException("Content-Type header is required.");
        }
        String clean = rawContentType.trim().toLowerCase(Locale.ROOT);
        int semi = clean.indexOf(';');
        if (semi >= 0) {
            clean = clean.substring(0, semi).trim();
        }
        if ("image/jpg".equals(clean)) {
            clean = "image/jpeg";
        }
        if (!MIME_TO_EXT.containsKey(clean)) {
            throw new IllegalArgumentException("Unsupported file type for sharing.");
        }
        return clean;
    }

    /** Detect mp4, webm, png, jpeg, webp, or pdf strictly from magic bytes. */
    public static String detectShareableMimeFromMagicBytes(byte[] data, int length) {
        if (data == null || length <= 0 || length > data.length) {
            return null;
        }

        // PDF: %PDF-
        if (length >= 5
                && data[0] == '%'
                && data[1] == 'P'
                && data[2] == 'D'
                && data[3] == 'F'
                && data[4] == '-') {
            return "application/pdf";
        }

        // PNG / JPEG / WebP
        String imageMime = ShareIntakePolicy.detectImageMimeFromMagicBytes(data, length);
        if (imageMime != null) {
            return imageMime;
        }

        // MP4: ISO Base Media File Format ('ftyp' at bytes 4..7)
        if (length >= 12
                && data[4] == 'f'
                && data[5] == 't'
                && data[6] == 'y'
                && data[7] == 'p') {
            // Reject QuickTime ('qt  ') if explicitly marked as QuickTime rather than MP4/ISO.
            if (!(data[8] == 'q' && data[9] == 't' && data[10] == ' ' && data[11] == ' ')) {
                return "video/mp4";
            }
        }

        // WebM: EBML header 1A 45 DF A3 (and reject Matroska doctype if present without webm)
        if (length >= 4
                && (data[0] & 0xFF) == 0x1A
                && (data[1] & 0xFF) == 0x45
                && (data[2] & 0xFF) == 0xDF
                && (data[3] & 0xFF) == 0xA3) {
            String asciiHead = new String(data, 0, Math.min(length, MAGIC_HEADER_BYTES), StandardCharsets.ISO_8859_1)
                    .toLowerCase(Locale.ROOT);
            if (asciiHead.contains("matroska") && !asciiHead.contains("webm")) {
                return null;
            }
            return "video/webm";
        }

        return null;
    }

    /** Verify declared Content-Type matches actual magic bytes for an allowed share-out type. */
    public static String verifyShareableTypeAndMagic(String contentType, byte[] header, int length) {
        String declared = normalizeAndValidateShareMime(contentType);
        String magic = detectShareableMimeFromMagicBytes(header, length);
        if (magic == null || !declared.equals(magic)) {
            throw new IllegalArgumentException("File content does not match declared file type.");
        }
        return magic;
    }

    public static String extensionForMime(String mime) {
        String ext = MIME_TO_EXT.get(mime);
        if (ext == null) {
            throw new IllegalArgumentException("Unsupported share MIME type.");
        }
        return ext;
    }

    /** Generate a safe random filename with no user-supplied characters or paths. */
    public static String generateSafeFilename(String mime) {
        return "share-" + UUID.randomUUID() + extensionForMime(mime);
    }

    /** Decode base64 PDF payload, enforcing 10 MB max and %PDF- magic bytes. */
    public static byte[] decodeAndValidatePdfBase64(String rawBase64) {
        if (rawBase64 == null) {
            throw new IllegalArgumentException("PDF payload is required.");
        }
        String trimmed = rawBase64.trim();
        if (trimmed.isEmpty()) {
            throw new IllegalArgumentException("PDF payload is empty.");
        }
        if (trimmed.length() > MAX_PDF_BASE64_CHARS) {
            throw new IllegalArgumentException("PDF exceeds 10 MB limit.");
        }
        if (trimmed.startsWith("data:")) {
            throw new IllegalArgumentException("Raw base64 PDF payload is required.");
        }
        final byte[] bytes;
        try {
            bytes = Base64.getDecoder().decode(trimmed);
        } catch (IllegalArgumentException ex) {
            throw new IllegalArgumentException("Invalid base64 PDF payload.", ex);
        }
        if (bytes.length == 0) {
            throw new IllegalArgumentException("PDF payload is empty.");
        }
        if (bytes.length > MAX_PDF_BYTES) {
            throw new IllegalArgumentException("PDF exceeds 10 MB limit.");
        }
        String magic = detectShareableMimeFromMagicBytes(bytes, Math.min(bytes.length, 16));
        if (!"application/pdf".equals(magic)) {
            throw new IllegalArgumentException("Invalid PDF magic header.");
        }
        return bytes;
    }

    /** Resolve cacheDir/share and verify it stays strictly inside cacheDir. */
    public static File resolveShareDir(File cacheDir) throws IOException {
        if (cacheDir == null) {
            throw new IOException("Cache directory is unavailable.");
        }
        File cacheCanonical = cacheDir.getCanonicalFile();
        File shareDir = new File(cacheCanonical, SHARE_SUBDIR);
        if (!shareDir.exists() && !shareDir.mkdirs()) {
            throw new IOException("Could not create share cache directory.");
        }
        File shareCanonical = shareDir.getCanonicalFile();
        if (!shareCanonical.getParentFile().equals(cacheCanonical)) {
            throw new SecurityException("Share directory escaped cache directory.");
        }
        return shareCanonical;
    }

    /** Delete files older than an hour inside shareDir so share artifacts never accumulate. */
    public static void clearShareDir(File shareDir) throws IOException {
        if (shareDir == null || !shareDir.exists()) {
            return;
        }
        File canonicalDir = shareDir.getCanonicalFile();
        File[] entries = canonicalDir.listFiles();
        if (entries == null) {
            return;
        }
        long cutoff = System.currentTimeMillis() - KEEP_SHARED_FILES_MS;
        for (File entry : entries) {
            if (entry.isFile() && entry.lastModified() < cutoff) {
                entry.delete();
            }
        }
    }

    /** Create a safe target File inside shareDir with a random generated name. */
    public static File createSafeShareFile(File shareDir, String mime) throws IOException {
        File canonicalDir = shareDir.getCanonicalFile();
        String name = generateSafeFilename(mime);
        File target = new File(canonicalDir, name);
        File targetCanonical = target.getCanonicalFile();
        if (!canonicalDir.equals(targetCanonical.getParentFile())) {
            throw new SecurityException("Target file escaped share directory.");
        }
        return targetCanonical;
    }

    /** Stream from InputStream to OutputStream up to maxBytes without buffering whole file in RAM. */
    public static StreamResult streamBounded(InputStream in, OutputStream out, long maxBytes)
            throws IOException {
        if (in == null || out == null) {
            throw new IOException("Stream is null.");
        }
        byte[] buf = new byte[64 * 1024];
        byte[] header = new byte[MAGIC_HEADER_BYTES];
        int headerLen = 0;
        long total = 0;
        int n;
        while ((n = in.read(buf)) != -1) {
            if (n == 0) {
                continue;
            }
            total += n;
            if (total > maxBytes) {
                throw new IllegalArgumentException("File exceeds 200 MB limit.");
            }
            if (headerLen < MAGIC_HEADER_BYTES) {
                int copy = Math.min(n, MAGIC_HEADER_BYTES - headerLen);
                System.arraycopy(buf, 0, header, headerLen, copy);
                headerLen += copy;
            }
            out.write(buf, 0, n);
        }
        if (total == 0) {
            throw new IllegalArgumentException("Downloaded file is empty.");
        }
        out.flush();
        return new StreamResult(total, header, headerLen);
    }
}
