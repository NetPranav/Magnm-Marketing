import { App } from '@capacitor/app';
import {
  CapacitorBarcodeScanner,
  CapacitorBarcodeScannerTypeHint,
} from '@capacitor/barcode-scanner';
import { Browser } from '@capacitor/browser';
import { Capacitor, registerPlugin } from '@capacitor/core';

export interface NativeHttpRequest {
  method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  url: string;
  headers?: Record<string, string>;
  body?: string;
  timeoutMs?: number;
}

export interface NativeHttpResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
  redirected?: boolean;
}

export interface SharedItem {
  id: string;
  text?: string;
  url?: string;
  imageBase64?: string;
  mimeType?: string;
  receivedAt: string;
}

export interface ShareOutOptions {
  title?: string;
  text?: string;
  fileUrl?: string;
}

export interface SecureStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
}

export interface NativeBridge {
  readonly isPreview: boolean;
  readonly previewLabel: string | null;
  readonly secureStore: SecureStore;
  http(request: NativeHttpRequest): Promise<NativeHttpResponse>;
  takeSharedItems(): Promise<SharedItem[]>;
  dictate(): Promise<string | null>;
  shareOut(options: ShareOutOptions): Promise<void>;
  openBrowser(url: string): Promise<void>;
  closeBrowser(): Promise<void>;
  scanQrCode(): Promise<string | null>;
  onDeepLink(handler: (url: string) => void): () => void;
  onResume?(handler: () => void): () => void;
  onBackButton?(handler: () => void): () => void;
  minimizeApp?(): Promise<void>;
}

export const PREVIEW_BANNER_LABEL = 'Preview (no phone features)';

interface MagnmNativePlugin {
  secureStoreGet(options: { key: string }): Promise<{ value: string | null }>;
  secureStoreSet(options: { key: string; value: string }): Promise<void>;
  secureStoreRemove(options: { key: string }): Promise<void>;
  http(request: NativeHttpRequest): Promise<NativeHttpResponse>;
  takeSharedItems(): Promise<{ items: SharedItem[] }>;
  dictate(): Promise<{ text: string | null }>;
  shareOut(options: ShareOutOptions): Promise<void>;
}

const MagnmNative = registerPlugin<MagnmNativePlugin>('MagnmNative');

export interface PreviewBridgeOptions {
  httpHandler?: (request: NativeHttpRequest) => Promise<NativeHttpResponse>;
  initialSharedItems?: SharedItem[];
  initialLaunchUrl?: string | null;
  dictateResult?: string | null;
  scanQrResult?: string | null;
  onOpenBrowser?: (url: string) => void;
  onShareOut?: (options: ShareOutOptions) => void;
  onMinimize?: () => void;
}

/** In-memory native fake for tests and browser preview; never persists secrets. */
export function createPreviewNativeBridge(
  options: PreviewBridgeOptions = {},
): NativeBridge & {
  emitDeepLink: (url: string) => void;
  emitResume: () => void;
  emitBackButton: () => void;
  getMinimizeCount: () => number;
  pushSharedItem: (item: SharedItem) => void;
  setDictateResult: (text: string | null) => void;
  setScanQrResult: (code: string | null) => void;
} {
  const memorySecrets = new Map<string, string>();
  const sharedQueue: SharedItem[] = [...(options.initialSharedItems ?? [])];
  const deepLinkListeners = new Set<(url: string) => void>();
  const resumeListeners = new Set<() => void>();
  const backListeners = new Set<() => void>();
  const deliveredLaunchUrls = new Set<string>();
  let dictateValue = options.dictateResult ?? null;
  let scanQrValue = options.scanQrResult ?? null;
  let minimizeCount = 0;

  return {
    isPreview: true,
    previewLabel: PREVIEW_BANNER_LABEL,
    secureStore: {
      async get(key: string) {
        return memorySecrets.get(key) ?? null;
      },
      async set(key: string, value: string) {
        memorySecrets.set(key, String(value));
      },
      async remove(key: string) {
        memorySecrets.delete(key);
      },
    },
    async http(request: NativeHttpRequest): Promise<NativeHttpResponse> {
      if (options.httpHandler) {
        return options.httpHandler(request);
      }
      throw new Error(
        `${PREVIEW_BANNER_LABEL}: native HTTP is unavailable in browser preview.`,
      );
    },
    async takeSharedItems(): Promise<SharedItem[]> {
      if (sharedQueue.length === 0) return [];
      const taken = sharedQueue.splice(0, sharedQueue.length);
      return taken;
    },
    async dictate(): Promise<string | null> {
      return dictateValue;
    },
    async shareOut(opts: ShareOutOptions): Promise<void> {
      options.onShareOut?.(opts);
    },
    async openBrowser(url: string): Promise<void> {
      options.onOpenBrowser?.(url);
    },
    async closeBrowser(): Promise<void> {
      // No-op in browser preview fake.
    },
    async scanQrCode(): Promise<string | null> {
      return scanQrValue;
    },
    onDeepLink(handler: (url: string) => void): () => void {
      let removed = false;
      const seen = new Set<string>();
      const deliver = (url: string) => {
        if (removed || !url || seen.has(url)) return;
        seen.add(url);
        handler(url);
      };
      deepLinkListeners.add(deliver);
      if (options.initialLaunchUrl && !deliveredLaunchUrls.has(options.initialLaunchUrl)) {
        const launchUrl = options.initialLaunchUrl;
        deliveredLaunchUrls.add(launchUrl);
        void Promise.resolve().then(() => deliver(launchUrl));
      }
      return () => {
        removed = true;
        deepLinkListeners.delete(deliver);
      };
    },
    onResume(handler: () => void): () => void {
      resumeListeners.add(handler);
      const onVis = () => {
        if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
          handler();
        }
      };
      if (typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', onVis);
      }
      return () => {
        resumeListeners.delete(handler);
        if (typeof document !== 'undefined') {
          document.removeEventListener('visibilitychange', onVis);
        }
      };
    },
    onBackButton(handler: () => void): () => void {
      backListeners.add(handler);
      return () => {
        backListeners.delete(handler);
      };
    },
    async minimizeApp(): Promise<void> {
      minimizeCount += 1;
      options.onMinimize?.();
    },
    emitDeepLink(url: string): void {
      for (const listener of deepLinkListeners) {
        listener(url);
      }
    },
    emitResume(): void {
      for (const listener of resumeListeners) {
        listener();
      }
    },
    emitBackButton(): void {
      for (const listener of backListeners) {
        listener();
      }
    },
    getMinimizeCount(): number {
      return minimizeCount;
    },
    pushSharedItem(item: SharedItem): void {
      sharedQueue.push(item);
    },
    setDictateResult(text: string | null): void {
      dictateValue = text;
    },
    setScanQrResult(code: string | null): void {
      scanQrValue = code;
    },
  };
}

/** Capacitor-backed bridge for Android (T6.3 native plugin + official Capacitor plugins). */
export function createCapacitorNativeBridge(): NativeBridge {
  return {
    isPreview: false,
    previewLabel: null,
    secureStore: {
      async get(key: string) {
        const res = await MagnmNative.secureStoreGet({ key });
        return res?.value ?? null;
      },
      async set(key: string, value: string) {
        await MagnmNative.secureStoreSet({ key, value: String(value) });
      },
      async remove(key: string) {
        await MagnmNative.secureStoreRemove({ key });
      },
    },
    async http(request: NativeHttpRequest): Promise<NativeHttpResponse> {
      return MagnmNative.http(request);
    },
    async takeSharedItems(): Promise<SharedItem[]> {
      const res = await MagnmNative.takeSharedItems();
      return Array.isArray(res?.items) ? res.items : [];
    },
    async dictate(): Promise<string | null> {
      const res = await MagnmNative.dictate();
      return typeof res?.text === 'string' && res.text.trim() ? res.text : null;
    },
    async shareOut(options: ShareOutOptions): Promise<void> {
      await MagnmNative.shareOut(options);
    },
    async openBrowser(url: string): Promise<void> {
      await Browser.open({ url });
    },
    async closeBrowser(): Promise<void> {
      try {
        await Browser.close();
      } catch {
        // Ignore if browser tab is already closed.
      }
    },
    async scanQrCode(): Promise<string | null> {
      const res = await CapacitorBarcodeScanner.scanBarcode({
        hint: CapacitorBarcodeScannerTypeHint.QR_CODE,
      });
      const text = typeof res?.ScanResult === 'string' ? res.ScanResult.trim() : '';
      return text || null;
    },
    onDeepLink(handler: (url: string) => void): () => void {
      let removed = false;
      const seenUrls = new Set<string>();
      const deliverOnce = (url: unknown) => {
        if (removed || typeof url !== 'string' || !url || seenUrls.has(url)) {
          return;
        }
        seenUrls.add(url);
        handler(url);
      };

      void App.getLaunchUrl()
        .then((res) => {
          deliverOnce(res?.url);
        })
        .catch(() => {});

      const handlePromise = App.addListener('appUrlOpen', ({ url }) => {
        deliverOnce(url);
      });
      return () => {
        removed = true;
        void handlePromise.then((handle) => handle.remove()).catch(() => {});
      };
    },
    onResume(handler: () => void): () => void {
      let removed = false;
      const handlePromise = App.addListener('appStateChange', ({ isActive }) => {
        if (!removed && isActive) {
          handler();
        }
      });
      return () => {
        removed = true;
        void handlePromise.then((handle) => handle.remove()).catch(() => {});
      };
    },
    onBackButton(handler: () => void): () => void {
      let removed = false;
      const handlePromise = App.addListener('backButton', () => {
        if (!removed) {
          handler();
        }
      });
      return () => {
        removed = true;
        void handlePromise.then((handle) => handle.remove()).catch(() => {});
      };
    },
    async minimizeApp(): Promise<void> {
      await App.minimizeApp();
    },
  };
}

let activeBridge: NativeBridge | null = null;

/** Return the active native bridge (Capacitor on Android, in-memory fake in browser/tests). */
export function getNativeBridge(): NativeBridge {
  if (!activeBridge) {
    const isNative =
      typeof Capacitor !== 'undefined' &&
      typeof Capacitor.isNativePlatform === 'function' &&
      Capacitor.isNativePlatform();
    activeBridge = isNative ? createCapacitorNativeBridge() : createPreviewNativeBridge();
  }
  return activeBridge;
}

/** Override the active native bridge (primarily for tests). */
export function setNativeBridge(bridge: NativeBridge | null): void {
  activeBridge = bridge;
}
