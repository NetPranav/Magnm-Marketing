package com.magnm.studio;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Pure-Java HTTP allowlist, URL validator, header sanitizer, and payload/timeout guard.
 * Independent of JS validation in net.ts and testable directly on the JVM.
 */
public final class NativeUrlPolicy {

    public static final int DEFAULT_TIMEOUT_MS = 30_000;
    public static final int MAX_TIMEOUT_MS = 200_000;
    public static final int MAX_REQUEST_BYTES = 12 * 1024 * 1024;
    public static final int MAX_RESPONSE_BYTES = 10 * 1024 * 1024;

    private static final int MAX_URL_LENGTH = 32_768;
    private static final String NIM_HOST = "integrate.api.nvidia.com";
    private static final Pattern STRICT_OCTET = Pattern.compile("^(0|[1-9][0-9]{0,2})$");
    private static final Pattern STRICT_PORT = Pattern.compile("^[1-9][0-9]{0,4}$");
    // The one storage path the app may use: <owner id>/<64 hex>.<extension> in the private phone-files bucket.
    private static final Pattern PHONE_COPY_PATH = Pattern.compile(
            "^/storage/v1/object/sign/phone-files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                    + "/[0-9a-f]{64}\\.(?:png|jpg|jpeg|webp|mp4|webm|pdf)$");
    // The one storage path the app may WRITE: its own object in the private phone-uploads bucket. It is reachable only
    // through validateCloudUploadUrl (never through the general validateUrl), and never with a query.
    private static final Pattern PHONE_UPLOAD_PATH = Pattern.compile(
            "^/storage/v1/object/phone-uploads/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                    + "/[0-9a-f]{64}\\.(?:png|jpg|jpeg|webp|mp4|webm|pdf)$");
    private static final Pattern PHONE_COPY_QUERY = Pattern.compile("^\\?token=[A-Za-z0-9._-]{8,4000}$");

    // The app updates itself from our own GitHub releases: the manifest and the APK on github.com, and the one host
    // GitHub redirects release downloads to. Nothing else, and only through the three validateUpdate* methods below.
    private static final String RELEASE_HOST = "github.com";
    private static final String RELEASE_BASE = "/NetPranav/Magnm-Marketing/releases/";
    private static final String UPDATE_MANIFEST_PATH = RELEASE_BASE + "latest/download/android.json";
    /** The address of the update manifest. */
    public static final String UPDATE_MANIFEST_URL = "https://" + RELEASE_HOST + UPDATE_MANIFEST_PATH;
    private static final Pattern UPDATE_APK_PATH = Pattern.compile(
            "^" + RELEASE_BASE + "download/v([0-9]{1,5}\\.[0-9]{1,2}\\.[0-9]{1,2})/magnm-studio-([0-9]{1,5}\\.[0-9]{1,2}\\.[0-9]{1,2})\\.apk$");
    private static final Pattern UPDATE_TAGGED_MANIFEST_PATH = Pattern.compile(
            "^" + RELEASE_BASE + "download/v[0-9]{1,5}\\.[0-9]{1,2}\\.[0-9]{1,2}/android\\.json$");
    private static final Set<String> UPDATE_ASSET_HOSTS;
    private static final Pattern UPDATE_ASSET_PATH = Pattern.compile(
            "^/github-production-release-asset[-0-9a-f]{0,16}/[0-9]{1,20}/[A-Za-z0-9._-]{1,128}$");
    private static final int MAX_UPDATE_QUERY = 4096;

    private static final Set<String> ALLOWED_METHODS;
    private static final Set<String> FORBIDDEN_HEADERS;

    static {
        Set<String> methods = new HashSet<>();
        methods.add("GET");
        methods.add("POST");
        methods.add("PATCH");
        methods.add("PUT");
        methods.add("DELETE");
        ALLOWED_METHODS = Collections.unmodifiableSet(methods);

        Set<String> headers = new HashSet<>();
        headers.add("host");
        headers.add("cookie");
        headers.add("connection");
        headers.add("content-length");
        headers.add("transfer-encoding");
        headers.add("proxy-authorization");
        headers.add("proxy-connection");
        headers.add("upgrade");
        FORBIDDEN_HEADERS = Collections.unmodifiableSet(headers);

        Set<String> assetHosts = new HashSet<>();
        assetHosts.add("release-assets.githubusercontent.com");
        assetHosts.add("objects.githubusercontent.com");
        UPDATE_ASSET_HOSTS = Collections.unmodifiableSet(assetHosts);
    }

    public static final class PolicyException extends IllegalArgumentException {
        public PolicyException(String message) {
            super(message);
        }
    }

    private final String supabaseHost;

    public NativeUrlPolicy(String supabaseOrigin) {
        this.supabaseHost = parseSupabaseOriginHost(supabaseOrigin);
    }

    private static String parseSupabaseOriginHost(String supabaseOrigin) {
        if (supabaseOrigin == null) {
            throw new PolicyException("Configured Supabase origin is invalid.");
        }
        String trimmed = supabaseOrigin.trim().replaceAll("/+$", "");
        if (trimmed.isEmpty() || containsForbiddenUrlChars(trimmed) || trimmed.contains("#")) {
            throw new PolicyException("Configured Supabase origin is invalid.");
        }
        URI uri;
        try {
            uri = URI.create(trimmed);
        } catch (IllegalArgumentException ex) {
            throw new PolicyException("Configured Supabase origin is invalid.");
        }
        if (!"https".equalsIgnoreCase(uri.getScheme())) {
            throw new PolicyException("Configured Supabase origin must use https.");
        }
        if (uri.getRawUserInfo() != null) {
            throw new PolicyException("Configured Supabase origin must not include userinfo.");
        }
        String host = uri.getHost();
        if (host == null || host.isEmpty()) {
            throw new PolicyException("Configured Supabase origin host is missing.");
        }
        int port = uri.getPort();
        if (port != -1 && port != 443) {
            throw new PolicyException("Configured Supabase origin port is invalid.");
        }
        String rawPath = uri.getRawPath();
        if ((rawPath != null && !rawPath.isEmpty()) || uri.getRawQuery() != null) {
            throw new PolicyException("Configured Supabase origin must not include path or query.");
        }
        return host.toLowerCase(Locale.ROOT);
    }

    public String getSupabaseHost() {
        return supabaseHost;
    }

    public String validateMethod(String method) {
        if (method == null || method.isEmpty() || containsForbiddenUrlChars(method)) {
            throw new PolicyException("HTTP method is required.");
        }
        String upper = method.toUpperCase(Locale.ROOT);
        if (!ALLOWED_METHODS.contains(upper)) {
            throw new PolicyException("HTTP method is not allowed.");
        }
        return upper;
    }

    public int resolveTimeoutMs(Integer timeoutMs) {
        if (timeoutMs == null) {
            return DEFAULT_TIMEOUT_MS;
        }
        if (timeoutMs <= 0) {
            throw new PolicyException("Timeout must be positive.");
        }
        return Math.min(MAX_TIMEOUT_MS, timeoutMs);
    }

    public byte[] validateBody(String body) {
        if (body == null) {
            return new byte[0];
        }
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_REQUEST_BYTES) {
            throw new PolicyException("Request body exceeds size limit.");
        }
        return bytes;
    }

    public Map<String, String> validateHeaders(Map<String, String> rawHeaders) {
        Map<String, String> out = new LinkedHashMap<>();
        if (rawHeaders == null || rawHeaders.isEmpty()) {
            return out;
        }
        for (Map.Entry<String, String> entry : rawHeaders.entrySet()) {
            String key = entry.getKey();
            if (key == null || key.isEmpty() || !isValidHeaderName(key)) {
                throw new PolicyException("Header name contains forbidden characters.");
            }
            if (FORBIDDEN_HEADERS.contains(key.toLowerCase(Locale.ROOT))) {
                throw new PolicyException("Forbidden request header.");
            }
            String val = entry.getValue();
            if (val == null) {
                throw new PolicyException("Header value is invalid.");
            }
            if (!isValidHeaderValue(val)) {
                throw new PolicyException("Header value contains forbidden control characters.");
            }
            out.put(key, val);
        }
        return out;
    }

    /**
     * Validate a URL against the native allowlist without ever embedding the URL in errors.
     */
    public String validateUrl(String rawUrl) {
        return validateUrlInternal(rawUrl, false);
    }

    /**
     * Validate the upload URL of one phone file: https, the configured Supabase host, exactly the phone-uploads object
     * path (owner folder, 64 hex name, allowed extension) and no query. The folder is checked against the account in JS
     * and by the cloud's row level security; here only the shape is fixed.
     */
    public String validateCloudUploadUrl(String rawUrl) {
        String normalized = validateUrlInternal(rawUrl, true);
        if (!normalized.startsWith("https://") || normalized.indexOf('?') >= 0) {
            throw new PolicyException("An upload URL takes no query.");
        }
        int slashIdx = normalized.indexOf('/', "https://".length());
        if (slashIdx < 0 || !PHONE_UPLOAD_PATH.matcher(normalized.substring(slashIdx)).matches()) {
            throw new PolicyException("Upload URL must be a phone upload path.");
        }
        return normalized;
    }

    private String validateUrlInternal(String rawUrl, boolean allowUploadPath) {
        if (rawUrl == null || rawUrl.isEmpty()) {
            throw new PolicyException("Request URL is required.");
        }
        if (rawUrl.length() > MAX_URL_LENGTH) {
            throw new PolicyException("Request URL exceeds length limit.");
        }
        if (containsForbiddenUrlChars(rawUrl)) {
            throw new PolicyException("Request URL contains forbidden characters.");
        }
        if (rawUrl.indexOf('#') >= 0) {
            throw new PolicyException("URL fragments are not permitted.");
        }

        int schemeSep = rawUrl.indexOf("://");
        if (schemeSep <= 0) {
            throw new PolicyException("Request URL must include an explicit scheme.");
        }
        String scheme = rawUrl.substring(0, schemeSep).toLowerCase(Locale.ROOT);
        if (!"http".equals(scheme) && !"https".equals(scheme)) {
            throw new PolicyException("Unsupported URL scheme.");
        }

        String rest = rawUrl.substring(schemeSep + 3);
        if (rest.isEmpty()) {
            throw new PolicyException("URL authority is missing.");
        }

        int slashIdx = rest.indexOf('/');
        int queryIdx = rest.indexOf('?');
        int authEnd = rest.length();
        if (slashIdx >= 0) {
            authEnd = Math.min(authEnd, slashIdx);
        }
        if (queryIdx >= 0) {
            authEnd = Math.min(authEnd, queryIdx);
        }

        String authority = rest.substring(0, authEnd);
        if (authority.isEmpty()) {
            throw new PolicyException("URL authority is empty.");
        }
        if (authority.indexOf('@') >= 0 || authority.toLowerCase(Locale.ROOT).contains("%40")) {
            throw new PolicyException("URL userinfo is forbidden.");
        }
        if (authority.indexOf('[') >= 0 || authority.indexOf(']') >= 0) {
            throw new PolicyException("IPv6 hosts are not permitted.");
        }
        if (authority.indexOf('%') >= 0) {
            throw new PolicyException("Encoded authority characters are forbidden.");
        }

        int firstColon = authority.indexOf(':');
        if (firstColon >= 0 && authority.indexOf(':', firstColon + 1) >= 0) {
            throw new PolicyException("Malformed URL authority.");
        }
        String host = firstColon < 0 ? authority : authority.substring(0, firstColon);
        String portStr = firstColon < 0 ? null : authority.substring(firstColon + 1);
        if (host.isEmpty() || host.endsWith(".")) {
            throw new PolicyException("URL host is invalid.");
        }
        Integer port = null;
        if (portStr != null) {
            if (!STRICT_PORT.matcher(portStr).matches()) {
                throw new PolicyException("URL port syntax is invalid.");
            }
            int parsedPort = Integer.parseInt(portStr);
            if (parsedPort < 1 || parsedPort > 65535) {
                throw new PolicyException("URL port is out of range.");
            }
            port = parsedPort;
        }

        String remainder = rest.substring(authEnd);
        int remQueryIdx = remainder.indexOf('?');
        String path = remQueryIdx < 0 ? remainder : remainder.substring(0, remQueryIdx);
        String query = remQueryIdx < 0 ? "" : remainder.substring(remQueryIdx);
        if (path.isEmpty()) {
            path = "/";
        }
        validatePath(path);

        if ("http".equals(scheme)) {
            String normalizedIp = parseStrictTailscaleIpv4(host);
            if (port == null) {
                throw new PolicyException("Laptop HTTP URL requires an explicit port.");
            }
            if (!path.startsWith("/phone/v1/") || path.length() <= "/phone/v1/".length()) {
                throw new PolicyException("Laptop HTTP URL path is not allowed.");
            }
            return "http://" + normalizedIp + ":" + port + path + query;
        }

        // HTTPS: Supabase origin (3 path groups) or NVIDIA NIM (/v1/)
        if (port != null && port != 443) {
            throw new PolicyException("Custom HTTPS ports are not permitted.");
        }
        String lowerHost = host.toLowerCase(Locale.ROOT);
        if (lowerHost.equals(supabaseHost)) {
            if (!isAllowedSupabasePath(path) && !(allowUploadPath && PHONE_UPLOAD_PATH.matcher(path).matches())) {
                throw new PolicyException("Supabase endpoint path is not allowed.");
            }
            if (PHONE_COPY_PATH.matcher(path).matches() && !query.isEmpty() && !PHONE_COPY_QUERY.matcher(query).matches()) {
                throw new PolicyException("A phone copy link takes only its token.");
            }
            return "https://" + supabaseHost + path + query;
        }
        if (lowerHost.equals(NIM_HOST)) {
            if (!path.startsWith("/v1/") || path.length() <= "/v1/".length()) {
                throw new PolicyException("NIM endpoint path is not allowed.");
            }
            return "https://" + NIM_HOST + path + query;
        }
        throw new PolicyException("Destination host is not allowed.");
    }

    /** The one URL of the update manifest: the latest release's android.json. No query, no port, nothing else. */
    public String validateUpdateManifestUrl(String rawUrl) {
        String[] parts = splitUpdateUrl(rawUrl, Collections.singleton(RELEASE_HOST));
        if (!UPDATE_MANIFEST_PATH.equals(parts[1]) || !parts[2].isEmpty()) {
            throw new PolicyException("That is not the update manifest address.");
        }
        return "https://" + RELEASE_HOST + parts[1];
    }

    /**
     * The URL of one release APK: a release asset of our repository named magnm-studio-X.Y.Z.apk under the tag vX.Y.Z
     * with the same X.Y.Z, and no query.
     */
    public String validateUpdateApkUrl(String rawUrl) {
        String[] parts = splitUpdateUrl(rawUrl, Collections.singleton(RELEASE_HOST));
        java.util.regex.Matcher m = UPDATE_APK_PATH.matcher(parts[1]);
        if (!m.matches() || !m.group(1).equals(m.group(2)) || !parts[2].isEmpty()) {
            throw new PolicyException("That is not an update download address.");
        }
        return "https://" + RELEASE_HOST + parts[1];
    }

    /**
     * Where a redirect from the manifest or the APK may lead: one of our release URLs, or GitHub's release-asset host
     * on a release-asset path (with its signed query).
     */
    public String validateUpdateRedirectUrl(String rawUrl) {
        String[] parts = splitUpdateUrl(rawUrl, null);
        String host = parts[0];
        if (RELEASE_HOST.equals(host)) {
            boolean ours = parts[2].isEmpty()
                    && (UPDATE_MANIFEST_PATH.equals(parts[1])
                    || UPDATE_TAGGED_MANIFEST_PATH.matcher(parts[1]).matches()
                    || isConsistentApkPath(parts[1]));
            if (!ours) {
                throw new PolicyException("The update server sent the app somewhere it may not go.");
            }
            return "https://" + host + parts[1];
        }
        if (UPDATE_ASSET_HOSTS.contains(host) && UPDATE_ASSET_PATH.matcher(parts[1]).matches()) {
            return "https://" + host + parts[1] + parts[2];
        }
        throw new PolicyException("The update server sent the app somewhere it may not go.");
    }

    private static boolean isConsistentApkPath(String path) {
        java.util.regex.Matcher m = UPDATE_APK_PATH.matcher(path);
        return m.matches() && m.group(1).equals(m.group(2));
    }

    /**
     * Split a https update URL into {host, path, query-with-question-mark-or-empty}. The authority must be exactly a
     * host name (lower case): no userinfo, no port, no IPv6, no encoding. Hosts are checked against {@code onlyHosts}
     * when given. Errors never include the URL.
     */
    private String[] splitUpdateUrl(String rawUrl, Set<String> onlyHosts) {
        if (rawUrl == null || rawUrl.isEmpty()) {
            throw new PolicyException("An update address is required.");
        }
        if (rawUrl.length() > MAX_URL_LENGTH || containsForbiddenUrlChars(rawUrl) || rawUrl.indexOf('#') >= 0) {
            throw new PolicyException("That update address is not valid.");
        }
        if (!rawUrl.startsWith("https://")) {
            throw new PolicyException("Updates are only downloaded over https.");
        }
        String rest = rawUrl.substring("https://".length());
        int end = rest.length();
        int slash = rest.indexOf('/');
        int question = rest.indexOf('?');
        if (slash >= 0) {
            end = Math.min(end, slash);
        }
        if (question >= 0) {
            end = Math.min(end, question);
        }
        String authority = rest.substring(0, end);
        if (authority.isEmpty() || !authority.matches("^[a-z0-9][a-z0-9.-]*[a-z0-9]$") || authority.contains("..")) {
            throw new PolicyException("That update address is not allowed.");
        }
        if (onlyHosts != null && !onlyHosts.contains(authority)) {
            throw new PolicyException("That update address is not allowed.");
        }
        String remainder = rest.substring(end);
        int q = remainder.indexOf('?');
        String path = q < 0 ? remainder : remainder.substring(0, q);
        String query = q < 0 ? "" : remainder.substring(q);
        if (path.isEmpty() || query.length() > MAX_UPDATE_QUERY || query.equals("?")) {
            throw new PolicyException("That update address is not allowed.");
        }
        validatePath(path);
        return new String[] {authority, path, query};
    }

    /**
     * Validate that rawUrl is strictly GET-able from paired laptop /phone/v1/files?...
     */
    public String validateLaptopFileUrl(String rawUrl) {
        String normalized = validateUrl(rawUrl);
        if (!normalized.startsWith("http://")) {
            throw new PolicyException("Shareable laptop files must use paired Tailscale HTTP.");
        }
        int schemeEnd = "http://".length();
        int slashIdx = normalized.indexOf('/', schemeEnd);
        if (slashIdx < 0) {
            throw new PolicyException("Laptop file URL path is missing.");
        }
        String remainder = normalized.substring(slashIdx);
        int qIdx = remainder.indexOf('?');
        String path = qIdx < 0 ? remainder : remainder.substring(0, qIdx);
        String query = qIdx < 0 ? "" : remainder.substring(qIdx);
        if (!"/phone/v1/files".equals(path) || query.length() <= 1) {
            throw new PolicyException("Laptop file URL must target /phone/v1/files?...");
        }
        return normalized;
    }

    private static boolean isAllowedSupabasePath(String path) {
        return path.startsWith("/auth/v1/")
                || path.startsWith("/rest/v1/")
                || "/functions/v1/ai-proxy".equals(path)
                || PHONE_COPY_PATH.matcher(path).matches();
    }

    /**
     * Validate the signed download link of a draft file copy: the exact storage path of one object in the private
     * phone-files bucket, with exactly one token query and nothing else.
     */
    public String validateCloudFileUrl(String rawUrl) {
        String normalized = validateUrl(rawUrl);
        if (!normalized.startsWith("https://")) {
            throw new PolicyException("Cloud files must use https.");
        }
        int slashIdx = normalized.indexOf('/', "https://".length());
        if (slashIdx < 0) {
            throw new PolicyException("Cloud file URL path is missing.");
        }
        String remainder = normalized.substring(slashIdx);
        int qIdx = remainder.indexOf('?');
        String path = qIdx < 0 ? remainder : remainder.substring(0, qIdx);
        String query = qIdx < 0 ? "" : remainder.substring(qIdx);
        if (!PHONE_COPY_PATH.matcher(path).matches() || !PHONE_COPY_QUERY.matcher(query).matches()) {
            throw new PolicyException("Cloud file URL must be a signed phone copy link.");
        }
        return normalized;
    }

    private static void validatePath(String path) {
        if (!path.startsWith("/")) {
            throw new PolicyException("URL path must start with slash.");
        }
        if (path.contains("//")) {
            throw new PolicyException("Empty path segments are forbidden.");
        }
        if (path.indexOf(';') >= 0) {
            throw new PolicyException("Matrix parameters are forbidden in URL paths.");
        }
        String lowerPath = path.toLowerCase(Locale.ROOT);
        if (lowerPath.contains("%2e") || lowerPath.contains("%2f") || lowerPath.contains("%5c")) {
            throw new PolicyException("Encoded traversal sequences are forbidden.");
        }
        if (hasEncodedControlChars(lowerPath)) {
            throw new PolicyException("Encoded control characters are forbidden.");
        }
        String[] segments = path.split("/", -1);
        for (String seg : segments) {
            if (".".equals(seg) || "..".equals(seg)) {
                throw new PolicyException("Path traversal segments are forbidden.");
            }
        }
    }

    private static boolean hasEncodedControlChars(String lower) {
        int idx = 0;
        while ((idx = lower.indexOf('%', idx)) >= 0) {
            if (idx + 2 >= lower.length()) {
                return true;
            }
            char h1 = lower.charAt(idx + 1);
            char h2 = lower.charAt(idx + 2);
            if (!isHex(h1) || !isHex(h2)) {
                return true;
            }
            int code = (Character.digit(h1, 16) << 4) | Character.digit(h2, 16);
            if (code < 0x20 || code == 0x7f) {
                return true;
            }
            idx += 3;
        }
        return false;
    }

    private static boolean isHex(char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    }

    public static String parseStrictTailscaleIpv4(String host) {
        if (host == null || host.isEmpty()) {
            throw new PolicyException("Laptop host is required.");
        }
        String[] parts = host.split("\\.", -1);
        if (parts.length != 4) {
            throw new PolicyException("Laptop host must be a dotted-quad IPv4 address.");
        }
        int[] octets = new int[4];
        for (int i = 0; i < 4; i++) {
            String part = parts[i];
            if (!STRICT_OCTET.matcher(part).matches()) {
                throw new PolicyException("Invalid IPv4 octet.");
            }
            int val = Integer.parseInt(part);
            if (val < 0 || val > 255) {
                throw new PolicyException("IPv4 octet out of range.");
            }
            octets[i] = val;
        }
        // 100.64.0.0/10 => 100.64.0.0 .. 100.127.255.255
        if (octets[0] != 100 || octets[1] < 64 || octets[1] > 127) {
            throw new PolicyException("Laptop IP is outside allowed Tailscale range.");
        }
        return octets[0] + "." + octets[1] + "." + octets[2] + "." + octets[3];
    }

    private static boolean containsForbiddenUrlChars(String value) {
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c <= 0x20 || c >= 0x7f || c == '\\') {
                return true;
            }
        }
        return false;
    }

    private static boolean isValidHeaderName(String name) {
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            if (c <= 0x20 || c >= 0x7f || c == ':') {
                return false;
            }
        }
        return true;
    }

    private static boolean isValidHeaderValue(String value) {
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '\r' || c == '\n' || c == '\0' || (c < 0x20 && c != '\t') || c == 0x7f) {
                return false;
            }
        }
        return true;
    }
}
