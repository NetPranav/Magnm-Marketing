package com.magnm.studio;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import org.junit.Test;

public class ShareIntakePolicyTest {

    private static final String OWN_PKG = "com.magnm.studio";

    @Test
    public void validatesSharedTextLimitsAndSingleUrlExtraction() {
        assertNull(ShareIntakePolicy.validateSharedText(null));
        assertNull(ShareIntakePolicy.validateSharedText(""));
        assertEquals("Launch Cero notes", ShareIntakePolicy.validateSharedText("Launch Cero notes"));

        byte[] exactLimit = new byte[ShareIntakePolicy.MAX_TEXT_BYTES];
        java.util.Arrays.fill(exactLimit, (byte) 'x');
        assertEquals(
                ShareIntakePolicy.MAX_TEXT_BYTES,
                ShareIntakePolicy.validateSharedText(new String(exactLimit, StandardCharsets.US_ASCII)).length()
        );

        byte[] overLimit = new byte[ShareIntakePolicy.MAX_TEXT_BYTES + 1];
        java.util.Arrays.fill(overLimit, (byte) 'x');
        String oversized = new String(overLimit, StandardCharsets.US_ASCII);
        assertThrows(IllegalArgumentException.class, () -> ShareIntakePolicy.validateSharedText(oversized));

        assertEquals(
                "https://example.com/launch/cero",
                ShareIntakePolicy.extractSingleHttpUrl("  https://example.com/launch/cero  ")
        );
        assertNull(ShareIntakePolicy.extractSingleHttpUrl("Check https://example.com/launch/cero today"));
        assertNull(ShareIntakePolicy.extractSingleHttpUrl("javascript:alert(1)"));
    }

    @Test
    public void enforcesContentSchemeAndBlocksOwnAuthoritiesFileTheft() {
        ShareIntakePolicy.validateContentUri(
                "content",
                "com.android.providers.media.documents",
                "com.android.providers.media.documents",
                "/document/image:123",
                OWN_PKG
        );

        // Reject file:, http:, and other non-content schemes
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "file", "", "", "/data/data/com.magnm.studio/shared_prefs/secret.xml", OWN_PKG));
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "https", "example.com", "example.com", "/img.png", OWN_PKG));

        // Reject own authorities and package prefixes (case-insensitive)
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "content", "com.magnm.studio.fileprovider", "com.magnm.studio.fileprovider", "/my_cache_images/secret", OWN_PKG));
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "content", "COM.MAGNM.STUDIO.fileprovider", "COM.MAGNM.STUDIO.fileprovider", "/secret", OWN_PKG));
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "content", "com.magnm.studio", "com.magnm.studio", "/secret", OWN_PKG));

        // Reject userinfo and authority mismatch bypass attempts
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "content", "trusted@com.magnm.studio.fileprovider", "com.magnm.studio.fileprovider", "/x", OWN_PKG));
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "content", "trusted%40com.magnm.studio.fileprovider", "trusted%40com.magnm.studio.fileprovider", "/x", OWN_PKG));
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "content", "com.other.provider", "com.magnm.studio.fileprovider", "/x", OWN_PKG));
        assertThrows(SecurityException.class, () -> ShareIntakePolicy.validateContentUri(
                "content", "com.other.provider", "com.other.provider", "/img\0.png", OWN_PKG));
    }

    @Test
    public void verifiesImageMagicBytesAndRejectsMimeSpoofing() {
        byte[] pngMagic = new byte[] { (byte) 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 0 };
        byte[] jpegMagic = new byte[] { (byte) 0xFF, (byte) 0xD8, (byte) 0xFF, (byte) 0xE0, 0, 0 };
        byte[] webpMagic = new byte[] { 'R', 'I', 'F', 'F', 1, 2, 3, 4, 'W', 'E', 'B', 'P', 'V', 'P', '8', ' ' };
        byte[] svgPayload = "<svg xmlns='http://www.w3.org/2000/svg'></svg>".getBytes(StandardCharsets.UTF_8);

        assertEquals("image/png", ShareIntakePolicy.verifyImageMimeAndMagic("image/png", "image/png", pngMagic, pngMagic.length));
        assertEquals("image/jpeg", ShareIntakePolicy.verifyImageMimeAndMagic("image/*", "image/jpeg", jpegMagic, jpegMagic.length));
        assertEquals("image/webp", ShareIntakePolicy.verifyImageMimeAndMagic("image/webp", null, webpMagic, webpMagic.length));

        // Spoofed MIME or unsupported formats must fail
        assertThrows(IllegalArgumentException.class, () -> ShareIntakePolicy.verifyImageMimeAndMagic(
                "image/png", "image/png", jpegMagic, jpegMagic.length));
        assertThrows(IllegalArgumentException.class, () -> ShareIntakePolicy.verifyImageMimeAndMagic(
                "image/png", "image/jpeg", pngMagic, pngMagic.length));
        assertThrows(IllegalArgumentException.class, () -> ShareIntakePolicy.verifyImageMimeAndMagic(
                "image/svg+xml", "image/svg+xml", svgPayload, svgPayload.length));
        assertThrows(IllegalArgumentException.class, () -> ShareIntakePolicy.verifyImageMimeAndMagic(
                "image/png", "image/png", svgPayload, svgPayload.length));
        assertThrows(IllegalArgumentException.class, () -> ShareIntakePolicy.verifyImageMimeAndMagic(
                "image/*", null, pngMagic, pngMagic.length));
    }

    @Test
    public void computesDownscaleBoundsSampleSizeAndStreamLimits() throws IOException {
        assertArrayEquals(new int[] { 1024, 768 }, ShareIntakePolicy.computeTargetDimensions(1024, 768, 2048));
        assertArrayEquals(new int[] { 2048, 1024 }, ShareIntakePolicy.computeTargetDimensions(4096, 2048, 2048));
        assertArrayEquals(new int[] { 1024, 2048 }, ShareIntakePolicy.computeTargetDimensions(2000, 4000, 2048));

        assertEquals(1, ShareIntakePolicy.computeInSampleSize(1200, 1800, 2048));
        assertEquals(2, ShareIntakePolicy.computeInSampleSize(4096, 4096, 2048));

        assertTrue(ShareIntakePolicy.isBase64WithinLimit(ShareIntakePolicy.MAX_IMAGE_BASE64_BYTES));
        assertFalse(ShareIntakePolicy.isBase64WithinLimit(ShareIntakePolicy.MAX_IMAGE_BASE64_BYTES + 1));
        assertFalse(ShareIntakePolicy.isBase64WithinLimit(0));

        byte[] okStream = new byte[1024];
        assertEquals(1024, ShareIntakePolicy.readBoundedBytes(new ByteArrayInputStream(okStream), 1024).length);

        byte[] tooBigStream = new byte[1025];
        assertThrows(
                IllegalArgumentException.class,
                () -> ShareIntakePolicy.readBoundedBytes(new ByteArrayInputStream(tooBigStream), 1024)
        );
    }
}
