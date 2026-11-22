package com.magnm.studio;

import com.getcapacitor.Plugin;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Empty stand-ins for core plugins Capacitor always registers. Registered later under the same names, they win.
 * CapacitorHttp can reach any URL, which would bypass NativeUrlPolicy. WebView can serve the app from another path.
 */
final class DisabledCorePlugins {

    private DisabledCorePlugins() {}

    @CapacitorPlugin(name = "CapacitorHttp")
    public static final class Http extends Plugin {}

    @CapacitorPlugin(name = "WebView")
    public static final class WebViewControl extends Plugin {}
}
