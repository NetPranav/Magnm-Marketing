package com.magnm.studio;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Pure-Java staging of one file being uploaded from the phone, testable on the JVM.
 *
 * The web layer sends the file in small pieces (never one huge message). Each piece is appended, in order, to one
 * private temp file named by a random id. Before the file leaves the phone it is checked again here: its size must
 * be exactly what was announced and at most 50 MB, and its first bytes must match its extension (the same types the
 * cloud bucket accepts). The staged file is only ever read and sent: nothing here opens, decodes or runs it.
 */
public final class UploadStage {

    public static final long MAX_UPLOAD_BYTES = 50L * 1024 * 1024;
    public static final int MAX_CHUNK_BYTES = 1024 * 1024;
    private static final Pattern UPLOAD_ID = Pattern.compile("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");

    public static final class StageException extends IllegalArgumentException {
        public StageException(String message) {
            super(message);
        }
    }

    private UploadStage() {}

    public static String validateId(String id) {
        if (id == null || !UPLOAD_ID.matcher(id).matches()) {
            throw new StageException("The upload id is not valid.");
        }
        return id;
    }

    /** The staged file of one upload inside the private staging folder (the id has the shape of a uuid, nothing else). */
    public static File fileFor(File stageDir, String id) {
        return new File(stageDir, validateId(id) + ".part");
    }

    /** Append one piece. Pieces must arrive in order (index 0, 1, 2 ...) and the total may never pass 50 MB. */
    public static long appendChunk(File stageDir, String id, int index, byte[] data) throws IOException {
        File file = fileFor(stageDir, id);
        if (data == null || data.length == 0 || data.length > MAX_CHUNK_BYTES) {
            throw new StageException("An upload piece must be 1 byte to 1 MB.");
        }
        if (index < 0) {
            throw new StageException("The piece number is not valid.");
        }
        if (!stageDir.isDirectory() && !stageDir.mkdirs()) {
            throw new IOException("Could not create the staging folder.");
        }
        long current = file.exists() ? file.length() : 0;
        if (index == 0 && current != 0) {
            throw new StageException("This upload was already started.");
        }
        if (index > 0 && (!file.exists() || chunkCount(file) != index)) {
            throw new StageException("Upload pieces must arrive in order.");
        }
        if (current + data.length > MAX_UPLOAD_BYTES) {
            file.delete();
            throw new StageException("Files can be at most 50 MB.");
        }
        try (FileOutputStream out = new FileOutputStream(file, true)) {
            out.write(data);
        }
        return current + data.length;
    }

    // Pieces are all the same size except the last: the count is the length divided by the piece size of piece 0.
    // To stay simple and exact the web layer always sends full pieces of CHUNK_BYTES except the final one, so the
    // expected index is derived from the file length and the fixed piece size.
    public static final int CHUNK_BYTES = 384 * 1024;

    private static long chunkCount(File file) {
        long len = file.length();
        return len % CHUNK_BYTES == 0 ? len / CHUNK_BYTES : -1;
    }

    /** The file extension (lower case, without a dot) of an upload URL's object name. */
    public static String extensionOf(String url) {
        int dot = url.lastIndexOf('.');
        if (dot < 0 || dot == url.length() - 1) {
            throw new StageException("The upload has no extension.");
        }
        return url.substring(dot + 1).toLowerCase(Locale.ROOT);
    }

    /** True if the first bytes are what the extension says (the types the cloud bucket accepts). */
    public static boolean magicMatches(String ext, byte[] head, int length) {
        switch (ext) {
            case "png":
                return length >= 8 && (head[0] & 0xff) == 0x89 && head[1] == 'P' && head[2] == 'N' && head[3] == 'G'
                        && head[4] == 0x0d && head[5] == 0x0a && head[6] == 0x1a && head[7] == 0x0a;
            case "jpg":
            case "jpeg":
                return length >= 3 && (head[0] & 0xff) == 0xff && (head[1] & 0xff) == 0xd8 && (head[2] & 0xff) == 0xff;
            case "webp":
                return length >= 12 && head[0] == 'R' && head[1] == 'I' && head[2] == 'F' && head[3] == 'F'
                        && head[8] == 'W' && head[9] == 'E' && head[10] == 'B' && head[11] == 'P';
            case "mp4":
                return length >= 8 && head[4] == 'f' && head[5] == 't' && head[6] == 'y' && head[7] == 'p';
            case "webm":
                return length >= 4 && (head[0] & 0xff) == 0x1a && (head[1] & 0xff) == 0x45 && (head[2] & 0xff) == 0xdf
                        && (head[3] & 0xff) == 0xa3;
            case "pdf":
                return new String(head, 0, Math.min(length, 1024), StandardCharsets.ISO_8859_1).contains("%PDF-");
            default:
                return false;
        }
    }

    /** The content type the cloud accepts for an extension, or a refusal. */
    public static String contentTypeFor(String ext) {
        switch (ext) {
            case "png": return "image/png";
            case "jpg":
            case "jpeg": return "image/jpeg";
            case "webp": return "image/webp";
            case "mp4": return "video/mp4";
            case "webm": return "video/webm";
            case "pdf": return "application/pdf";
            default: throw new StageException("That type of file cannot be uploaded.");
        }
    }

    /**
     * Check the staged file before it is sent: exactly the announced size, within the cap, the declared content type
     * is the one of the extension, and the first bytes match. Returns the file to send.
     */
    public static File verify(File stageDir, String id, long announcedBytes, String ext, String declaredContentType)
            throws IOException {
        File file = fileFor(stageDir, id);
        if (!file.isFile()) {
            throw new StageException("Nothing was staged for this upload.");
        }
        long len = file.length();
        if (len < 1 || len > MAX_UPLOAD_BYTES || len != announcedBytes) {
            file.delete();
            throw new StageException("The staged file does not match its announced size.");
        }
        if (!contentTypeFor(ext).equals(declaredContentType)) {
            file.delete();
            throw new StageException("The content type does not match the file type.");
        }
        byte[] head = new byte[1024];
        int read;
        try (InputStream in = new java.io.FileInputStream(file)) {
            read = in.read(head);
        }
        if (read < 1 || !magicMatches(ext, head, read)) {
            file.delete();
            throw new StageException("The file is not what its name says.");
        }
        return file;
    }

    /** Forget one upload (best effort). */
    public static void discard(File stageDir, String id) {
        File file = fileFor(stageDir, id);
        if (file.exists()) {
            file.delete();
        }
    }

    /** Remove every staged file older than the given age (left behind by a crash). */
    public static void sweep(File stageDir, long maxAgeMs, long nowMs) {
        File[] files = stageDir.listFiles();
        if (files == null) {
            return;
        }
        for (File f : files) {
            if (f.isFile() && f.getName().endsWith(".part") && nowMs - f.lastModified() > maxAgeMs) {
                f.delete();
            }
        }
    }
}
