package com.magnm.studio;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class ShareOutPolicyTest {

    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    @Test
    public void verifiesAllSixAllowedMimeTypesAndMagicBytes() {
        byte[] mp4Magic = new byte[] { 0, 0, 0, 0x18, 'f', 't', 'y', 'p', 'i', 's', 'o', 'm' };
        byte[] webmMagic = new byte[] { 0x1A, 0x45, (byte) 0xDF, (byte) 0xA3, 0x42, (byte) 0x82, 'w', 'e', 'b', 'm' };
        byte[] mkvMagic = new byte[] { 0x1A, 0x45, (byte) 0xDF, (byte) 0xA3, 0x42, (byte) 0x82, 'm', 'a', 't', 'r', 'o', 's', 'k', 'a' };
        byte[] qtMagic = new byte[] { 0, 0, 0, 0x18, 'f', 't', 'y', 'p', 'q', 't', ' ', ' ' };
        byte[] pngMagic = new byte[] { (byte) 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 0 };
        byte[] jpegMagic = new byte[] { (byte) 0xFF, (byte) 0xD8, (byte) 0xFF, (byte) 0xE0, 0, 0 };
        byte[] webpMagic = new byte[] { 'R', 'I', 'F', 'F', 1, 2, 3, 4, 'W', 'E', 'B', 'P', 'V', 'P', '8', ' ' };
        byte[] pdfMagic = "%PDF-1.4\n%%EOF".getBytes(StandardCharsets.US_ASCII);

        assertEquals("video/mp4", ShareOutPolicy.verifyShareableTypeAndMagic("video/mp4", mp4Magic, mp4Magic.length));
        assertEquals("video/webm", ShareOutPolicy.verifyShareableTypeAndMagic("video/webm; codecs=vp9", webmMagic, webmMagic.length));
        assertEquals("image/png", ShareOutPolicy.verifyShareableTypeAndMagic("image/png", pngMagic, pngMagic.length));
        assertEquals("image/jpeg", ShareOutPolicy.verifyShareableTypeAndMagic("image/jpg", jpegMagic, jpegMagic.length));
        assertEquals("image/webp", ShareOutPolicy.verifyShareableTypeAndMagic("image/webp", webpMagic, webpMagic.length));
        assertEquals("application/pdf", ShareOutPolicy.verifyShareableTypeAndMagic("application/pdf", pdfMagic, pdfMagic.length));

        // Reject unsupported types, Matroska masquerading as WebM, QuickTime, and MIME spoofing
        assertNull(ShareOutPolicy.detectShareableMimeFromMagicBytes(mkvMagic, mkvMagic.length));
        assertNull(ShareOutPolicy.detectShareableMimeFromMagicBytes(qtMagic, qtMagic.length));
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.verifyShareableTypeAndMagic("video/webm", mkvMagic, mkvMagic.length));
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.verifyShareableTypeAndMagic("text/plain", pdfMagic, pdfMagic.length));
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.verifyShareableTypeAndMagic("video/mp4", pngMagic, pngMagic.length));
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.verifyShareableTypeAndMagic(null, pngMagic, pngMagic.length));
    }

    @Test
    public void enforces200MbStreamingLimitAndNonEmptyStream() throws IOException {
        byte[] header = "%PDF-1.7\n1 0 obj\n".getBytes(StandardCharsets.US_ASCII);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ShareOutPolicy.StreamResult ok = ShareOutPolicy.streamBounded(
                new ByteArrayInputStream(header),
                out,
                ShareOutPolicy.MAX_SHARE_FILE_BYTES
        );
        assertEquals(header.length, ok.totalBytes);
        assertArrayEquals(header, out.toByteArray());

        assertThrows(
                IllegalArgumentException.class,
                () -> ShareOutPolicy.streamBounded(new ByteArrayInputStream(new byte[0]), new ByteArrayOutputStream(), 1024)
        );

        byte[] tooBig = new byte[1025];
        assertThrows(
                IllegalArgumentException.class,
                () -> ShareOutPolicy.streamBounded(new ByteArrayInputStream(tooBig), new ByteArrayOutputStream(), 1024)
        );
        assertEquals(200L * 1024L * 1024L, ShareOutPolicy.MAX_SHARE_FILE_BYTES);
    }

    @Test
    public void validatesPdfBase64MagicAnd10MbLimit() {
        byte[] smallPdf = "%PDF-1.4\n%%EOF".getBytes(StandardCharsets.US_ASCII);
        String b64 = Base64.getEncoder().encodeToString(smallPdf);
        assertArrayEquals(smallPdf, ShareOutPolicy.decodeAndValidatePdfBase64(b64));

        byte[] exactLimit = new byte[ShareOutPolicy.MAX_PDF_BYTES];
        exactLimit[0] = '%';
        exactLimit[1] = 'P';
        exactLimit[2] = 'D';
        exactLimit[3] = 'F';
        exactLimit[4] = '-';
        assertEquals(
                ShareOutPolicy.MAX_PDF_BYTES,
                ShareOutPolicy.decodeAndValidatePdfBase64(Base64.getEncoder().encodeToString(exactLimit)).length
        );

        byte[] overLimit = new byte[ShareOutPolicy.MAX_PDF_BYTES + 1];
        overLimit[0] = '%';
        overLimit[1] = 'P';
        overLimit[2] = 'D';
        overLimit[3] = 'F';
        overLimit[4] = '-';
        String overB64 = Base64.getEncoder().encodeToString(overLimit);
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.decodeAndValidatePdfBase64(overB64));

        String notPdf = Base64.getEncoder().encodeToString("NOTPDF".getBytes(StandardCharsets.US_ASCII));
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.decodeAndValidatePdfBase64(notPdf));
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.decodeAndValidatePdfBase64("data:application/pdf;base64," + b64));
        assertThrows(IllegalArgumentException.class, () -> ShareOutPolicy.decodeAndValidatePdfBase64(""));
    }

    @Test
    public void isolatesShareCacheFolderClearsOldFilesAndUsesRandomSafeNames() throws IOException {
        File cacheDir = tmp.newFolder("cache");
        File shareDir = ShareOutPolicy.resolveShareDir(cacheDir);
        assertEquals("share", shareDir.getName());
        assertEquals(cacheDir.getCanonicalFile(), shareDir.getParentFile());

        File stale = new File(shareDir, "old.pdf");
        try (FileOutputStream fos = new FileOutputStream(stale)) {
            fos.write(new byte[] { 1, 2, 3 });
        }
        assertTrue(stale.exists());
        assertTrue(stale.setLastModified(System.currentTimeMillis() - 2L * 60 * 60 * 1000));

        ShareOutPolicy.clearShareDir(shareDir);
        assertFalse(stale.exists());

        File safeFile = ShareOutPolicy.createSafeShareFile(shareDir, "video/mp4");
        assertEquals(shareDir.getCanonicalFile(), safeFile.getParentFile());
        assertTrue(safeFile.getName().startsWith("share-"));
        assertTrue(safeFile.getName().endsWith(".mp4"));
    }
}
