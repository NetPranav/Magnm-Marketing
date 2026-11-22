package com.magnm.studio;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Pure-Java validation and size/dimension helpers for share-in intents.
 */
public final class ShareIntakePolicy {

    public static final int MAX_SHARED_ITEMS = 10;
    public static final int MAX_TEXT_BYTES = 100 * 1024;
    public static final int MAX_IMAGE_READ_BYTES = 20 * 1024 * 1024;
    public static final int MAX_IMAGE_DIMENSION_PX = 2048;
    public static final int MAX_IMAGE_BASE64_BYTES = 7 * 1024 * 1024;

    private static final Set<String> ALLOWED_IMAGE_MIMES;

    static {
        Set<String> mimes = new HashSet<>();
        mimes.add("image/png");
        mimes.add("image/jpeg");
        mimes.add("image/webp");
        ALLOWED_IMAGE_MIMES = Collections.unmodifiableSet(mimes);
    }

    private ShareIntakePolicy() {}

    public static boolean isAllowedImageMime(String mimeType) {
        if (mimeType == null) {
            return false;
        }
        return ALLOWED_IMAGE_MIMES.contains(mimeType.trim().toLowerCase(Locale.ROOT));
    }

    /** Validate shared text against the 100 KB UTF-8 limit. Returns null if empty. */
    public static String validateSharedText(String rawText) {
        if (rawText == null || rawText.isEmpty()) {
            return null;
        }
        byte[] utf8 = rawText.getBytes(StandardCharsets.UTF_8);
        if (utf8.length > MAX_TEXT_BYTES) {
            throw new IllegalArgumentException("Shared text exceeds 100 KB limit.");
        }
        return rawText;
    }

    /** Extract trimmed URL if the shared text is a single http(s) URL without whitespace. */
    public static String extractSingleHttpUrl(String text) {
        if (text == null) {
            return null;
        }
        String trimmed = text.trim();
        if (trimmed.isEmpty()) {
            return null;
        }
        for (int i = 0; i < trimmed.length(); i++) {
            if (Character.isWhitespace(trimmed.charAt(i))) {
                return null;
            }
        }
        String lower = trimmed.toLowerCase(Locale.ROOT);
        if (lower.startsWith("https://") || lower.startsWith("http://")) {
            return trimmed;
        }
        return null;
    }

    /**
     * Validate a shared image URI: content: scheme only, and never targeting our own package/authorities.
     */
    public static void validateContentUri(
            String scheme,
            String authority,
            String host,
            String path,
            String ownPackageName
    ) {
        if (scheme == null || !"content".equalsIgnoreCase(scheme.trim())) {
            throw new SecurityException("Only content: URIs are accepted for shared images.");
        }
        if (authority == null || authority.trim().isEmpty()) {
            throw new SecurityException("Content URI authority is missing.");
        }
        String cleanAuth = authority.trim();
        for (int i = 0; i < cleanAuth.length(); i++) {
            char c = cleanAuth.charAt(i);
            if (c <= 0x20 || c >= 0x7f || c == '@' || c == ';') {
                throw new SecurityException("Content URI authority contains forbidden characters.");
            }
        }
        String lowerAuth = cleanAuth.toLowerCase(Locale.ROOT);
        if (lowerAuth.contains("%40")) {
            throw new SecurityException("Content URI userinfo is forbidden.");
        }
        if (host != null && !host.trim().isEmpty() && !lowerAuth.equals(host.trim().toLowerCase(Locale.ROOT))) {
            throw new SecurityException("Content URI authority mismatch.");
        }
        if (ownPackageName != null && !ownPackageName.trim().isEmpty()) {
            String lowerOwn = ownPackageName.trim().toLowerCase(Locale.ROOT);
            if (lowerAuth.equals(lowerOwn) || lowerAuth.startsWith(lowerOwn + ".")) {
                throw new SecurityException("Refusing content URI targeting app authority.");
            }
        }
        if (path != null && (path.indexOf('\0') >= 0 || path.toLowerCase(Locale.ROOT).contains("%00"))) {
            throw new SecurityException("Content URI path contains NUL byte.");
        }
    }

    /** Detect PNG, JPEG, or WebP from file magic bytes. Returns canonical MIME or null. */
    public static String detectImageMimeFromMagicBytes(byte[] data, int length) {
        if (data == null || length <= 0 || length > data.length) {
            return null;
        }
        // PNG: 89 50 4E 47 0D 0A 1A 0A
        if (length >= 8
                && (data[0] & 0xFF) == 0x89
                && data[1] == 'P'
                && data[2] == 'N'
                && data[3] == 'G'
                && data[4] == 0x0D
                && data[5] == 0x0A
                && data[6] == 0x1A
                && data[7] == 0x0A) {
            return "image/png";
        }
        // JPEG: FF D8 FF
        if (length >= 3
                && (data[0] & 0xFF) == 0xFF
                && (data[1] & 0xFF) == 0xD8
                && (data[2] & 0xFF) == 0xFF) {
            return "image/jpeg";
        }
        // WebP: RIFF....WEBP
        if (length >= 12
                && data[0] == 'R'
                && data[1] == 'I'
                && data[2] == 'F'
                && data[3] == 'F'
                && data[8] == 'W'
                && data[9] == 'E'
                && data[10] == 'B'
                && data[11] == 'P') {
            return "image/webp";
        }
        return null;
    }

    /**
     * Verify declared MIME type(s) against actual magic bytes.
     * Rejects missing/unrecognized MIME types and MIME/magic mismatches.
     */
    public static String verifyImageMimeAndMagic(
            String intentMime,
            String resolverMime,
            byte[] data,
            int length
    ) {
        String magicMime = detectImageMimeFromMagicBytes(data, length);
        if (magicMime == null) {
            throw new IllegalArgumentException("Unsupported or unrecognized image magic bytes.");
        }
        String cleanResolver = normalizeMime(resolverMime);
        String cleanIntent = normalizeMime(intentMime);

        if (cleanResolver != null) {
            if (!isAllowedImageMime(cleanResolver) || !cleanResolver.equals(magicMime)) {
                throw new IllegalArgumentException("Image MIME type does not match content.");
            }
        }
        if (cleanIntent != null) {
            if ("image/*".equals(cleanIntent)) {
                if (cleanResolver == null) {
                    throw new IllegalArgumentException("Specific image MIME type is required.");
                }
            } else if (!isAllowedImageMime(cleanIntent) || !cleanIntent.equals(magicMime)) {
                throw new IllegalArgumentException("Intent image MIME type does not match content.");
            }
        }
        if (cleanResolver == null && cleanIntent == null) {
            throw new IllegalArgumentException("Image MIME type is missing.");
        }
        return magicMime;
    }

    private static String normalizeMime(String mime) {
        if (mime == null) {
            return null;
        }
        String trimmed = mime.trim().toLowerCase(Locale.ROOT);
        int semi = trimmed.indexOf(';');
        if (semi >= 0) {
            trimmed = trimmed.substring(0, semi).trim();
        }
        return trimmed.isEmpty() ? null : trimmed;
    }

    /** Compute target dimensions fitting within maxDim while preserving aspect ratio. */
    public static int[] computeTargetDimensions(int width, int height, int maxDim) {
        if (width <= 0 || height <= 0 || maxDim <= 0) {
            throw new IllegalArgumentException("Image dimensions must be positive.");
        }
        if (width <= maxDim && height <= maxDim) {
            return new int[] { width, height };
        }
        double scale = (double) maxDim / (double) Math.max(width, height);
        int targetW = Math.max(1, Math.min(maxDim, (int) Math.round(width * scale)));
        int targetH = Math.max(1, Math.min(maxDim, (int) Math.round(height * scale)));
        return new int[] { targetW, targetH };
    }

    /** Compute power-of-2 BitmapFactory inSampleSize so decoded bounds stay manageable. */
    public static int computeInSampleSize(int width, int height, int maxDim) {
        if (width <= 0 || height <= 0 || maxDim <= 0) {
            throw new IllegalArgumentException("Image dimensions must be positive.");
        }
        int inSampleSize = 1;
        // Use the long edge: a very wide or tall image would otherwise decode at full size and exhaust memory.
        int halfLongEdge = Math.max(width, height) / 2;
        while (halfLongEdge / inSampleSize >= maxDim) {
            inSampleSize *= 2;
        }
        return inSampleSize;
    }

    public static boolean isBase64WithinLimit(int base64Length) {
        return base64Length > 0 && base64Length <= MAX_IMAGE_BASE64_BYTES;
    }

    /** Read up to maxBytes from stream, failing immediately if more bytes arrive. */
    public static byte[] readBoundedBytes(InputStream in, int maxBytes) throws IOException {
        if (in == null) {
            throw new IOException("Input stream is null.");
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int total = 0;
        int read;
        while ((read = in.read(buf)) != -1) {
            total += read;
            if (total > maxBytes) {
                throw new IllegalArgumentException("Shared image exceeds maximum read size.");
            }
            out.write(buf, 0, read);
        }
        return out.toByteArray();
    }
}
