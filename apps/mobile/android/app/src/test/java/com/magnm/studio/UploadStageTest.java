package com.magnm.studio;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.util.Arrays;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

public class UploadStageTest {

    private static final String ID = "123e4567-e89b-42d3-a456-426614174000";
    private File dir;

    private static byte[] png(int size) {
        byte[] b = new byte[size];
        byte[] magic = {(byte) 0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a};
        System.arraycopy(magic, 0, b, 0, Math.min(8, size));
        return b;
    }

    @Before
    public void setUp() throws IOException {
        dir = Files.createTempDirectory("stage").toFile();
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

    @Test
    public void onlyUuidShapedIdsNameAFile() {
        for (String bad : new String[] {null, "", "x", "../" + ID, ID + "/../x", ID.toUpperCase(), "a/b", ID + ".part", "..\\" + ID}) {
            assertThrows("id " + bad, UploadStage.StageException.class, () -> UploadStage.validateId(bad));
        }
        assertTrue(UploadStage.fileFor(dir, ID).getParentFile().equals(dir));
    }

    @Test
    public void stagesPiecesInOrderAndVerifiesTheWholeFile() throws IOException {
        byte[] data = png(UploadStage.CHUNK_BYTES + 1000);
        UploadStage.appendChunk(dir, ID, 0, Arrays.copyOfRange(data, 0, UploadStage.CHUNK_BYTES));
        UploadStage.appendChunk(dir, ID, 1, Arrays.copyOfRange(data, UploadStage.CHUNK_BYTES, data.length));
        File ok = UploadStage.verify(dir, ID, data.length, "png", "image/png");
        assertArrayEquals(data, Files.readAllBytes(ok.toPath()));
    }

    @Test
    public void refusesPiecesOutOfOrderRepeatedOrOversize() throws IOException {
        byte[] piece = png(UploadStage.CHUNK_BYTES);
        assertThrows(UploadStage.StageException.class, () -> UploadStage.appendChunk(dir, ID, 1, piece));
        UploadStage.appendChunk(dir, ID, 0, piece);
        assertThrows(UploadStage.StageException.class, () -> UploadStage.appendChunk(dir, ID, 0, piece));
        assertThrows(UploadStage.StageException.class, () -> UploadStage.appendChunk(dir, ID, 3, piece));
        assertThrows(UploadStage.StageException.class, () -> UploadStage.appendChunk(dir, ID, -1, piece));
        assertThrows(UploadStage.StageException.class, () -> UploadStage.appendChunk(dir, ID, 1, new byte[0]));
        assertThrows(UploadStage.StageException.class, () -> UploadStage.appendChunk(dir, ID, 1, new byte[UploadStage.MAX_CHUNK_BYTES + 1]));
    }

    @Test
    public void refusesAWrongSizeAWrongTypeAndFilesThatAreNotWhatTheyClaim() throws IOException {
        byte[] data = png(500);
        UploadStage.appendChunk(dir, ID, 0, data);
        assertThrows(UploadStage.StageException.class, () -> UploadStage.verify(dir, ID, 501, "png", "image/png"));
        assertFalse("a refused file is deleted", UploadStage.fileFor(dir, ID).exists());

        UploadStage.appendChunk(dir, ID, 0, data);
        assertThrows(UploadStage.StageException.class, () -> UploadStage.verify(dir, ID, 500, "png", "text/html"));
        assertFalse(UploadStage.fileFor(dir, ID).exists());

        UploadStage.appendChunk(dir, ID, 0, "<html><script>x</script></html>".getBytes());
        assertThrows(UploadStage.StageException.class, () -> UploadStage.verify(dir, ID, 31, "png", "image/png"));
        assertFalse(UploadStage.fileFor(dir, ID).exists());

        UploadStage.appendChunk(dir, ID, 0, png(500));
        assertThrows(UploadStage.StageException.class, () -> UploadStage.verify(dir, ID, 500, "pdf", "application/pdf"));
        assertThrows(UploadStage.StageException.class, () -> UploadStage.verify(dir, ID, 500, "html", "text/html"));
    }

    @Test
    public void matchesTheMagicBytesOfEveryAllowedType() {
        assertTrue(UploadStage.magicMatches("jpg", new byte[] {(byte) 0xff, (byte) 0xd8, (byte) 0xff, 0}, 4));
        assertTrue(UploadStage.magicMatches("pdf", "xx%PDF-1.4".getBytes(), 10));
        assertTrue(UploadStage.magicMatches("mp4", new byte[] {0, 0, 0, 24, 'f', 't', 'y', 'p', 'm'}, 9));
        assertTrue(UploadStage.magicMatches("webm", new byte[] {0x1a, 0x45, (byte) 0xdf, (byte) 0xa3}, 4));
        byte[] webp = new byte[12];
        System.arraycopy("RIFF".getBytes(), 0, webp, 0, 4);
        System.arraycopy("WEBP".getBytes(), 0, webp, 8, 4);
        assertTrue(UploadStage.magicMatches("webp", webp, 12));
        assertFalse(UploadStage.magicMatches("png", "GIF89a".getBytes(), 6));
        assertFalse(UploadStage.magicMatches("svg", "<svg".getBytes(), 4));
        assertEquals("image/jpeg", UploadStage.contentTypeFor("jpeg"));
        assertThrows(UploadStage.StageException.class, () -> UploadStage.contentTypeFor("gif"));
    }

    @Test
    public void sweepsOldStagedFilesOnly() throws IOException {
        File old = UploadStage.fileFor(dir, ID);
        Files.write(old.toPath(), new byte[] {1});
        File other = new File(dir, "keep.txt");
        Files.write(other.toPath(), new byte[] {1});
        UploadStage.sweep(dir, 1000, System.currentTimeMillis() + 5000);
        assertFalse(old.exists());
        assertTrue("only staged parts are swept", other.exists());
    }
}
