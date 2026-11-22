package com.magnm.studio;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentResolver;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ProviderInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Build;
import android.speech.RecognizerIntent;
import android.util.Base64;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.CookieHandler;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.json.JSONObject;

@CapacitorPlugin(name = "MagnmNative")
public class MagnmNativePlugin extends Plugin {

    private final ExecutorService executor = Executors.newCachedThreadPool();
    private final Object shareLock = new Object();
    private final List<JSObject> pendingSharedItems = new ArrayList<>();
    private final List<Future<?>> pendingShareTasks = new ArrayList<>();

    private SecureKeyStoreHelper secureStore;
    private NativeUrlPolicy urlPolicy;

    @Override
    public void load() {
        // Capacitor's core installs a process-wide cookie jar; our requests must neither store nor send cookies.
        CookieHandler.setDefault(null);
        secureStore = new SecureKeyStoreHelper(getContext());
        urlPolicy = new NativeUrlPolicy(BuildConfig.SUPABASE_ORIGIN);
    }

    @Override
    protected void handleOnDestroy() {
        executor.shutdownNow();
        super.handleOnDestroy();
    }

    @Override
    protected void handleOnNewIntent(Intent intent) {
        super.handleOnNewIntent(intent);
        if (intent == null) {
            return;
        }
        if ((intent.getFlags() & Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY) != 0) {
            return;
        }
        if (!Intent.ACTION_SEND.equals(intent.getAction())) {
            return;
        }

        final String intentMime = intent.getType();
        final String extraText;
        final Uri streamUri;
        try {
            extraText = intent.getStringExtra(Intent.EXTRA_TEXT);
            streamUri = extractStreamUri(intent);
        } catch (RuntimeException ex) {
            return; // Any app can send this intent; unreadable extras must not crash ours.
        }

        // Clear the Activity's intent once extracted so recreation cannot replay it.
        Activity activity = getActivity();
        if (activity != null && activity.getIntent() == intent) {
            activity.setIntent(new Intent(Intent.ACTION_MAIN));
        }

        if (streamUri != null) {
            Future<?> task = executor.submit(() -> ingestSharedImage(streamUri, intentMime));
            synchronized (shareLock) {
                pendingShareTasks.add(task);
            }
        } else if (extraText != null) {
            ingestSharedText(extraText);
        }
    }

    @PluginMethod
    public void secureStoreGet(PluginCall call) {
        String key = call.getString("key");
        if (key == null || key.trim().isEmpty()) {
            call.reject("Key is required.");
            return;
        }
        executor.execute(() -> {
            try {
                String value = secureStore.get(key);
                JSObject ret = new JSObject();
                ret.put("value", value != null ? value : JSONObject.NULL);
                call.resolve(ret);
            } catch (Exception ex) {
                call.reject("Secure store read failed.");
            }
        });
    }

    @PluginMethod
    public void secureStoreSet(PluginCall call) {
        String key = call.getString("key");
        String value = call.getString("value");
        if (key == null || key.trim().isEmpty() || value == null) {
            call.reject("Key and value are required.");
            return;
        }
        executor.execute(() -> {
            try {
                secureStore.set(key, value);
                call.resolve();
            } catch (Exception ex) {
                call.reject("Secure store write failed.");
            }
        });
    }

    @PluginMethod
    public void secureStoreRemove(PluginCall call) {
        String key = call.getString("key");
        if (key == null || key.trim().isEmpty()) {
            call.reject("Key is required.");
            return;
        }
        executor.execute(() -> {
            try {
                secureStore.remove(key);
                call.resolve();
            } catch (Exception ex) {
                call.reject("Secure store remove failed.");
            }
        });
    }

    @PluginMethod
    public void http(PluginCall call) {
        final String method;
        final String validatedUrl;
        final Map<String, String> validatedHeaders;
        final byte[] bodyBytes;
        final boolean hasBody;
        final int timeoutMs;

        try {
            method = urlPolicy.validateMethod(call.getString("method"));
            validatedUrl = urlPolicy.validateUrl(call.getString("url"));
            validatedHeaders = urlPolicy.validateHeaders(extractHeaders(call.getObject("headers")));
            String rawBody = call.getString("body");
            hasBody = rawBody != null;
            bodyBytes = urlPolicy.validateBody(rawBody);
            Integer requestedTimeout = call.getInt("timeoutMs");
            timeoutMs = urlPolicy.resolveTimeoutMs(requestedTimeout);
        } catch (NativeUrlPolicy.PolicyException ex) {
            call.reject(ex.getMessage());
            return;
        } catch (Exception ex) {
            call.reject("Invalid HTTP request.");
            return;
        }

        executor.execute(() -> executeHttp(call, method, validatedUrl, validatedHeaders, hasBody, bodyBytes, timeoutMs));
    }

    @PluginMethod
    public void takeSharedItems(PluginCall call) {
        executor.execute(() -> {
            List<Future<?>> tasksToWait;
            synchronized (shareLock) {
                tasksToWait = new ArrayList<>(pendingShareTasks);
                pendingShareTasks.clear();
            }
            for (Future<?> task : tasksToWait) {
                try {
                    task.get();
                } catch (Exception ignored) {
                    // Ignore failed share ingestion item.
                }
            }

            JSArray items = new JSArray();
            synchronized (shareLock) {
                for (JSObject item : pendingSharedItems) {
                    items.put(item);
                }
                pendingSharedItems.clear();
            }
            JSObject ret = new JSObject();
            ret.put("items", items);
            call.resolve(ret);
        });
    }

    @PluginMethod
    public void dictate(PluginCall call) {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);

        PackageManager pm = getContext().getPackageManager();
        if (intent.resolveActivity(pm) == null) {
            call.reject("Speech recognition is not available on this device.");
            return;
        }

        try {
            startActivityForResult(call, intent, "onDictateResult");
        } catch (ActivityNotFoundException ex) {
            call.reject("Speech recognition is not available on this device.");
        }
    }

    @ActivityCallback
    private void onDictateResult(PluginCall call, ActivityResult result) {
        if (call == null) {
            return;
        }
        JSObject ret = new JSObject();
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null) {
            ret.put("text", JSONObject.NULL);
            call.resolve(ret);
            return;
        }
        ArrayList<String> matches = result.getData().getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
        String text = null;
        if (matches != null && !matches.isEmpty()) {
            String first = matches.get(0);
            if (first != null && !first.trim().isEmpty()) {
                text = first.trim();
            }
        }
        ret.put("text", text != null ? text : JSONObject.NULL);
        call.resolve(ret);
    }

    @PluginMethod
    public void shareOut(PluginCall call) {
        String fileUrl = call.getString("fileUrl");
        if (fileUrl != null) {
            call.reject("Sharing files is not supported.");
            return;
        }
        String text = call.getString("text");
        if (text == null || text.isEmpty()) {
            call.reject("Share text is required.");
            return;
        }
        String title = call.getString("title");
        String cleanTitle = (title != null && !title.trim().isEmpty()) ? title.trim() : null;

        Intent sendIntent = new Intent(Intent.ACTION_SEND);
        sendIntent.setType("text/plain");
        sendIntent.putExtra(Intent.EXTRA_TEXT, text);
        if (cleanTitle != null) {
            sendIntent.putExtra(Intent.EXTRA_SUBJECT, cleanTitle);
        }
        Intent chooser = Intent.createChooser(sendIntent, cleanTitle != null ? cleanTitle : "Share");

        Activity activity = getActivity();
        if (activity == null) {
            call.reject("Activity is unavailable.");
            return;
        }
        activity.runOnUiThread(() -> {
            try {
                activity.startActivity(chooser);
                call.resolve();
            } catch (ActivityNotFoundException ex) {
                call.reject("No app available to share text.");
            }
        });
    }

    private void executeHttp(
            PluginCall call,
            String method,
            String validatedUrl,
            Map<String, String> headers,
            boolean hasBody,
            byte[] bodyBytes,
            int timeoutMs
    ) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(validatedUrl);
            conn = (HttpURLConnection) url.openConnection(Proxy.NO_PROXY);
            conn.setInstanceFollowRedirects(false);
            conn.setUseCaches(false);
            conn.setConnectTimeout(Math.min(timeoutMs, 30_000));
            conn.setReadTimeout(timeoutMs);
            conn.setRequestMethod(method);

            for (Map.Entry<String, String> h : headers.entrySet()) {
                conn.setRequestProperty(h.getKey(), h.getValue());
            }

            if (hasBody && ("POST".equals(method) || "PUT".equals(method) || "PATCH".equals(method) || "DELETE".equals(method))) {
                conn.setDoOutput(true);
                conn.setFixedLengthStreamingMode(bodyBytes.length);
                if (bodyBytes.length > 0) {
                    try (var os = conn.getOutputStream()) {
                        os.write(bodyBytes);
                    }
                }
            }

            int status = conn.getResponseCode();
            URL finalUrl = conn.getURL();
            if (finalUrl != null && !url.getHost().equalsIgnoreCase(finalUrl.getHost())) {
                call.reject("HTTP redirects are refused.");
                return;
            }

            if (status >= 300 && status < 400) {
                JSObject ret = new JSObject();
                ret.put("status", status);
                ret.put("headers", new JSObject());
                ret.put("body", "");
                ret.put("redirected", true);
                call.resolve(ret);
                return;
            }

            long contentLength = conn.getContentLengthLong();
            if (contentLength > NativeUrlPolicy.MAX_RESPONSE_BYTES) {
                call.reject("HTTP response exceeds size limit.");
                return;
            }

            InputStream stream = status >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String respBody = "";
            if (stream != null) {
                try (InputStream is = stream) {
                    byte[] respBytes = readResponseBounded(is, NativeUrlPolicy.MAX_RESPONSE_BYTES);
                    respBody = new String(respBytes, StandardCharsets.UTF_8);
                }
            }

            JSObject respHeaders = new JSObject();
            Map<String, List<String>> rawRespHeaders = conn.getHeaderFields();
            if (rawRespHeaders != null) {
                for (Map.Entry<String, List<String>> entry : rawRespHeaders.entrySet()) {
                    String key = entry.getKey();
                    if (key == null) {
                        continue;
                    }
                    String lowerKey = key.toLowerCase(Locale.ROOT);
                    if ("set-cookie".equals(lowerKey) || "set-cookie2".equals(lowerKey)) {
                        continue;
                    }
                    List<String> values = entry.getValue();
                    if (values != null && !values.isEmpty()) {
                        respHeaders.put(lowerKey, String.join(", ", values));
                    }
                }
            }

            JSObject ret = new JSObject();
            ret.put("status", status);
            ret.put("headers", respHeaders);
            ret.put("body", respBody);
            ret.put("redirected", false);
            call.resolve(ret);
        } catch (SocketTimeoutException ex) {
            call.reject("HTTP request timed out.");
        } catch (ResponseTooLargeException ex) {
            call.reject("HTTP response exceeds size limit.");
        } catch (Exception ex) {
            call.reject("HTTP request failed.");
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static final class ResponseTooLargeException extends IOException {}

    private static byte[] readResponseBounded(InputStream is, int maxBytes) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int total = 0;
        int n;
        while ((n = is.read(buf)) != -1) {
            total += n;
            if (total > maxBytes) {
                throw new ResponseTooLargeException();
            }
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }

    private static Map<String, String> extractHeaders(JSObject headersObj) {
        Map<String, String> map = new LinkedHashMap<>();
        if (headersObj == null) {
            return map;
        }
        Iterator<String> keys = headersObj.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            Object val = headersObj.opt(key);
            if (val != null && val != JSONObject.NULL) {
                map.put(key, String.valueOf(val));
            }
        }
        return map;
    }

    @SuppressWarnings("deprecation")
    private static Uri extractStreamUri(Intent intent) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            return intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class);
        }
        return intent.getParcelableExtra(Intent.EXTRA_STREAM);
    }

    private void ingestSharedText(String rawText) {
        try {
            String validated = ShareIntakePolicy.validateSharedText(rawText);
            if (validated == null) {
                return;
            }
            JSObject item = new JSObject();
            item.put("id", UUID.randomUUID().toString());
            String singleUrl = ShareIntakePolicy.extractSingleHttpUrl(validated);
            if (singleUrl != null) {
                item.put("url", singleUrl);
            } else {
                item.put("text", validated);
            }
            item.put("receivedAt", Instant.now().toString());
            enqueueSharedItem(item);
        } catch (Exception ignored) {
            // Drop invalid or oversized shared text.
        }
    }

    private void ingestSharedImage(Uri uri, String intentMime) {
        try {
            String ownPackage = getContext().getPackageName();
            ShareIntakePolicy.validateContentUri(
                    uri.getScheme(),
                    uri.getAuthority(),
                    uri.getHost(),
                    uri.getPath(),
                    ownPackage
            );

            PackageManager pm = getContext().getPackageManager();
            String authority = uri.getAuthority();
            if (authority != null) {
                ProviderInfo providerInfo = pm.resolveContentProvider(authority, 0);
                if (providerInfo != null && ownPackage.equalsIgnoreCase(providerInfo.packageName)) {
                    return;
                }
            }

            ContentResolver resolver = getContext().getContentResolver();
            String resolverMime = resolver.getType(uri);
            byte[] rawBytes;
            try (InputStream is = resolver.openInputStream(uri)) {
                if (is == null) {
                    return;
                }
                rawBytes = ShareIntakePolicy.readBoundedBytes(is, ShareIntakePolicy.MAX_IMAGE_READ_BYTES);
            }

            String verifiedMime = ShareIntakePolicy.verifyImageMimeAndMagic(
                    intentMime,
                    resolverMime,
                    rawBytes,
                    rawBytes.length
            );

            EncodedImage encoded = downscaleAndEncodeImage(rawBytes, verifiedMime);
            if (encoded == null) {
                return;
            }

            JSObject item = new JSObject();
            item.put("id", UUID.randomUUID().toString());
            item.put("imageBase64", encoded.base64);
            item.put("mimeType", encoded.mimeType);
            item.put("receivedAt", Instant.now().toString());
            enqueueSharedItem(item);
        } catch (Exception ignored) {
            // Reject invalid, spoofed, or oversized shared image.
        }
    }

    private void enqueueSharedItem(JSObject item) {
        synchronized (shareLock) {
            if (pendingSharedItems.size() >= ShareIntakePolicy.MAX_SHARED_ITEMS) {
                return;
            }
            pendingSharedItems.add(item);
        }
    }

    private static final class EncodedImage {
        final String base64;
        final String mimeType;

        EncodedImage(String base64, String mimeType) {
            this.base64 = base64;
            this.mimeType = mimeType;
        }
    }

    private static EncodedImage downscaleAndEncodeImage(byte[] rawBytes, String mimeType) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.length, bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            return null;
        }

        BitmapFactory.Options decodeOpts = new BitmapFactory.Options();
        decodeOpts.inSampleSize = ShareIntakePolicy.computeInSampleSize(
                bounds.outWidth,
                bounds.outHeight,
                ShareIntakePolicy.MAX_IMAGE_DIMENSION_PX
        );
        decodeOpts.inPreferredConfig = Bitmap.Config.ARGB_8888;

        Bitmap bitmap = BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.length, decodeOpts);
        if (bitmap == null) {
            return null;
        }

        try {
            int[] target = ShareIntakePolicy.computeTargetDimensions(
                    bitmap.getWidth(),
                    bitmap.getHeight(),
                    ShareIntakePolicy.MAX_IMAGE_DIMENSION_PX
            );
            if (target[0] != bitmap.getWidth() || target[1] != bitmap.getHeight()) {
                Bitmap scaled = Bitmap.createScaledBitmap(bitmap, target[0], target[1], true);
                if (scaled != bitmap) {
                    bitmap.recycle();
                    bitmap = scaled;
                }
            }

            Bitmap current = bitmap;
            bitmap = null;
            try {
                for (int step = 0; step < 5; step++) {
                    EncodedImage candidate = tryEncodeWithinBase64Limit(current, mimeType);
                    if (candidate != null) {
                        return candidate;
                    }
                    int nextW = Math.max(1, (int) Math.round(current.getWidth() * 0.75));
                    int nextH = Math.max(1, (int) Math.round(current.getHeight() * 0.75));
                    if (nextW == current.getWidth() && nextH == current.getHeight()) {
                        break;
                    }
                    Bitmap smaller = Bitmap.createScaledBitmap(current, nextW, nextH, true);
                    if (smaller != current) {
                        current.recycle();
                        current = smaller;
                    }
                }
                return null;
            } finally {
                if (current != null && !current.isRecycled()) {
                    current.recycle();
                }
            }
        } finally {
            if (bitmap != null && !bitmap.isRecycled()) {
                bitmap.recycle();
            }
        }
    }

    private static EncodedImage tryEncodeWithinBase64Limit(Bitmap bitmap, String preferredMime) {
        String outMime = preferredMime;
        Bitmap.CompressFormat format;
        int[] qualities;
        if ("image/png".equals(preferredMime)) {
            format = Bitmap.CompressFormat.PNG;
            qualities = new int[] { 100 };
        } else if ("image/webp".equals(preferredMime)) {
            format = webpFormat();
            qualities = new int[] { 90, 80, 70, 55 };
        } else {
            format = Bitmap.CompressFormat.JPEG;
            outMime = "image/jpeg";
            qualities = new int[] { 90, 80, 70, 55 };
        }

        for (int q : qualities) {
            String b64 = compressToBase64(bitmap, format, q);
            if (ShareIntakePolicy.isBase64WithinLimit(b64.length())) {
                return new EncodedImage(b64, outMime);
            }
        }

        // If lossless PNG is still over 7 MB base64, re-encode as JPEG so it fits.
        if ("image/png".equals(preferredMime)) {
            for (int q : new int[] { 85, 75, 60 }) {
                String b64 = compressToBase64(bitmap, Bitmap.CompressFormat.JPEG, q);
                if (ShareIntakePolicy.isBase64WithinLimit(b64.length())) {
                    return new EncodedImage(b64, "image/jpeg");
                }
            }
        }
        return null;
    }

    @SuppressWarnings("deprecation")
    private static Bitmap.CompressFormat webpFormat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            return Bitmap.CompressFormat.WEBP_LOSSY;
        }
        return Bitmap.CompressFormat.WEBP;
    }

    private static String compressToBase64(Bitmap bitmap, Bitmap.CompressFormat format, int quality) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        bitmap.compress(format, quality, out);
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
    }
}
