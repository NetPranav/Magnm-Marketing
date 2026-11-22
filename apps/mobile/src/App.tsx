import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Dialog, NumberedTabs, PillButton, StatusDot, Wordmark, type TabItem } from '@magnm/design/react';
import {
  ConfidentialRefusalError,
  confirmLaptopResults,
  extractLaptopResults,
  fetchLaptopAgents,
  fetchLaptopDraftDetail,
  fetchLaptopDrafts,
  fetchLaptopNotifications,
  fetchLaptopPendingResults,
  fetchLaptopReminders,
  fetchLaptopStatus,
  fetchLaptopSummary,
  fetchOwnMirrorRecords,
  fetchSharedProductRules,
  LaptopAuthError,
  postLaptopAgentMessage,
  postLaptopDraftComment,
  postLaptopDraftPosted,
  postLaptopInboxBatch,
  postLaptopNotificationsRead,
  postLaptopReminderDone,
  ProductRulesUnavailableError,
  sendPhoneLiteMessage,
} from './api';
import {
  fetchAccountProfile,
  getPersonalNimKey,
  getStoredSession,
  handleAuthDeepLink,
  setPersonalNimKey,
  signOut,
  startGoogleSignIn,
} from './auth';
import {
  formatSavedCopyLabel,
  getCachedProductRules,
  loadCache,
  saveLaptopSnapshot,
  type LaptopCachedSnapshot,
} from './cache';
import { ChatTab, DEFAULT_LITE_AGENTS } from './components/ChatTab';
import { DraftsTab } from './components/DraftsTab';
import { MoreTab } from './components/MoreTab';
import { ShareInModal } from './components/ShareInModal';
import { TodayTab } from './components/TodayTab';
import { getNativeBridge, type NativeBridge, type SharedItem } from './native';
import { HttpStatusError, NetworkPolicyError } from './net';
import {
  getPairingConfig,
  parsePairingPayload,
  pollPairRequest,
  submitPairRequest,
  unpairLaptop,
} from './pairing';
import {
  enqueueInboxItem,
  flushBoundQueue,
  getBoundQueueItems,
  loadQueue,
  removeQueuedItem,
} from './queue';
import { getStateGeneration } from './storage';
import type {
  AccountProfile,
  AuthSession,
  ChatTurn,
  DraftItem,
  LaptopStatus,
  MetricProposal,
  MobileTabId,
  PairingConfig,
  ParsedQrPayload,
  PhoneAgentInfo,
  PhoneNotification,
  QueueItemKind,
  QueuedInboxItem,
  ReminderItem,
  WeeklySummary,
} from './types';

const MOBILE_TABS: TabItem[] = [
  { id: 'today', label: 'Today' },
  { id: 'drafts', label: 'Drafts' },
  { id: 'chat', label: 'Chat' },
  { id: 'more', label: 'More' },
];

export interface AppProps {
  bridge?: NativeBridge;
  initialTab?: MobileTabId;
}

function isLaptopNetworkError(err: unknown): boolean {
  if (err instanceof LaptopAuthError) return false;
  if (err instanceof HttpStatusError) return false;
  if (err instanceof NetworkPolicyError) return false;
  if (err instanceof ConfidentialRefusalError) return false;
  if (err instanceof ProductRulesUnavailableError) return false;
  return true;
}

function mirrorDraftsFromCache(cache: ReturnType<typeof loadCache>): DraftItem[] {
  const cachedLaptop = cache.laptop?.drafts ?? [];
  if (cachedLaptop.length > 0) return cachedLaptop;

  const versionsByParent = new Map<string, Array<{ version: number; body: string }>>();
  for (const rec of cache.mirrorRecords) {
    if (rec.kind === 'content_version' && rec.parent_local_id && !rec.data?.confidential) {
      const body = typeof rec.data?.body === 'string' ? rec.data.body : '';
      const verNum = typeof rec.data?.version === 'number' ? rec.data.version : 0;
      if (body) {
        const list = versionsByParent.get(rec.parent_local_id) ?? [];
        list.push({ version: verNum, body });
        versionsByParent.set(rec.parent_local_id, list);
      }
    }
  }

  return cache.mirrorRecords
    .filter((rec) => rec.kind === 'content_item')
    .map((rec) => {
      const isConf = Boolean(rec.data?.confidential);
      const currentVer =
        typeof rec.data?.current_version === 'number' ? rec.data.current_version : null;
      const verList = versionsByParent.get(rec.local_id) ?? [];
      let chosenBody: string | null = null;
      if (!isConf && verList.length > 0) {
        const byCurrent =
          currentVer !== null ? verList.find((v) => v.version === currentVer) : undefined;
        if (byCurrent) {
          chosenBody = byCurrent.body;
        } else {
          const highest = verList.reduce((best, cur) => (cur.version >= best.version ? cur : best));
          chosenBody = highest.body;
        }
      }
      return {
        id: rec.local_id,
        title: isConf ? 'Confidential item' : rec.title || 'Untitled draft',
        platform: typeof rec.data?.platform === 'string' ? rec.data.platform : null,
        status: typeof rec.data?.status === 'string' ? rec.data.status : null,
        confidential: isConf,
        body: isConf ? null : chosenBody,
        current_version: currentVer,
        versions: isConf ? [] : verList.map((v) => ({ version: v.version, body: v.body })),
        updated_at: rec.local_updated_at,
      };
    });
}

export function App({ bridge: propBridge, initialTab = 'today' }: AppProps) {
  const bridge = propBridge ?? getNativeBridge();
  const initialPairing = useMemo(() => getPairingConfig(), []);
  const initialCache = useMemo(
    () => loadCache(Date.now(), { laptopDeviceId: initialPairing?.deviceId ?? null }),
    [initialPairing?.deviceId],
  );

  const [tab, setTab] = useState<MobileTabId>(initialTab);
  const [pairing, setPairing] = useState<PairingConfig | null>(initialPairing);
  const [pairingStep, setPairingStep] = useState<'idle' | 'confirm' | 'waiting'>('idle');
  const [pendingPairPayload, setPendingPairPayload] = useState<ParsedQrPayload | null>(null);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pairingCancelledRef = useRef(false);
  const pairingGenRef = useRef(0);
  const appGenRef = useRef(0);
  const handledDeepLinksRef = useRef(new Set<string>());

  const [accountInitialized, setAccountInitialized] = useState(false);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [hasPersonalNimKey, setHasPersonalNimKey] = useState(false);

  const [laptopReachable, setLaptopReachable] = useState(false);
  const [laptopAuthError, setLaptopAuthError] = useState<string | null>(null);
  const [laptopStatus, setLaptopStatus] = useState<LaptopStatus | null>(null);
  const [drafts, setDrafts] = useState<DraftItem[]>(() => mirrorDraftsFromCache(initialCache));
  const [selectedDraft, setSelectedDraft] = useState<DraftItem | null>(null);
  const [reminders, setReminders] = useState<ReminderItem[]>(
    () => initialCache.laptop?.reminders ?? [],
  );
  const [notifications, setNotifications] = useState<PhoneNotification[]>(
    () => initialCache.laptop?.notifications ?? [],
  );
  const [unreadCount, setUnreadCount] = useState<number>(
    () => initialCache.laptop?.unreadCount ?? 0,
  );
  const [summary, setSummary] = useState<WeeklySummary | null>(
    () => initialCache.laptop?.summary ?? null,
  );
  const [pendingResults, setPendingResults] = useState<MetricProposal[]>(
    () => initialCache.laptop?.pendingResults ?? [],
  );
  const [agents, setAgents] = useState<PhoneAgentInfo[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState('writer');
  const [chatMessages, setChatMessages] = useState<ChatTurn[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);

  const [todayError, setTodayError] = useState<string | null>(null);
  const [draftsError, setDraftsError] = useState<string | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);

  const [queueItems, setQueueItems] = useState<QueuedInboxItem[]>(() =>
    initialPairing ? getBoundQueueItems(initialPairing.deviceId) : [],
  );
  const [cacheVersion, setCacheVersion] = useState(0);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const [sharedQueue, setSharedQueue] = useState<SharedItem[]>([]);
  const [extractedProposal, setExtractedProposal] = useState<MetricProposal | null>(null);
  const [shareError, setShareError] = useState<string | null>(null);
  const [shareBusy, setShareBusy] = useState(false);

  const [confirmDialog, setConfirmDialog] = useState<{
    kind: 'signout' | 'unpair';
    title: string;
    message: string;
    confirmLabel: string;
  } | null>(null);

  const pairingRef = useRef(pairing);
  pairingRef.current = pairing;
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const backStateRef = useRef({
    confirmOpen: false,
    shareOpen: false,
    draftOpen: false,
    tab: initialTab,
  });
  backStateRef.current = {
    confirmOpen: Boolean(confirmDialog),
    shareOpen: sharedQueue.length > 0,
    draftOpen: Boolean(selectedDraft),
    tab,
  };

  const showToast = useCallback((msg: string) => {
    setToast(msg);
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  const resetAllInMemoryState = useCallback((options: { keepAccount?: boolean } = {}) => {
    appGenRef.current += 1;
    pairingGenRef.current += 1;
    pairingCancelledRef.current = true;
    if (pollTimerRef.current !== null) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }

    setPairing(null);
    setPairingStep('idle');
    setPendingPairPayload(null);
    setPairingError(null);

    if (!options.keepAccount) {
      setSession(null);
      setProfile(null);
      setAuthError(null);
      setHasPersonalNimKey(false);
      setSharedQueue([]);
      setExtractedProposal(null);
      setShareError(null);
      setDrafts([]);
    }

    setLaptopReachable(false);
    setLaptopAuthError(null);
    setLaptopStatus(null);
    setSelectedDraft(null);
    setReminders([]);
    setNotifications([]);
    setUnreadCount(0);
    setSummary(null);
    setPendingResults([]);
    setAgents([]);
    setSelectedAgentId('writer');
    setChatMessages([]);
    setChatError(null);
    setTodayError(null);
    setDraftsError(null);
    setMoreError(null);
    setQueueItems([]);
    setConfirmDialog(null);
    // Requests cut off by the wipe never reach their own cleanup.
    setBusyAction(null);
    setChatBusy(false);
    setShareBusy(false);
    setCacheVersion((v) => v + 1);
  }, []);

  const refreshQueueState = useCallback(
    (pairingOverride?: PairingConfig | null) => {
      const activePairing = pairingOverride !== undefined ? pairingOverride : getPairingConfig();
      if (activePairing?.deviceId) {
        setQueueItems(getBoundQueueItems(activePairing.deviceId));
      } else {
        setQueueItems([]);
      }
    },
    [],
  );

  const hydrateFromCache = useCallback(
    (activePairing?: PairingConfig | null, activeSession?: AuthSession | null) => {
      const p = activePairing !== undefined ? activePairing : pairingRef.current;
      const s = activeSession !== undefined ? activeSession : sessionRef.current;
      const cache = loadCache(Date.now(), {
        ownerUid: s?.user_id ?? null,
        laptopDeviceId: p?.deviceId ?? null,
      });
      setDrafts(mirrorDraftsFromCache(cache));
      setReminders(cache.laptop?.reminders ?? []);
      setNotifications(cache.laptop?.notifications ?? []);
      setUnreadCount(cache.laptop?.unreadCount ?? 0);
      setSummary(cache.laptop?.summary ?? null);
      setPendingResults(cache.laptop?.pendingResults ?? []);
      setCacheVersion((v) => v + 1);
    },
    [],
  );

  const refreshAccount = useCallback(async () => {
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () => appGenRef.current !== gen || getStateGeneration() !== stateGen;

    let stored: AuthSession | null = null;
    let nimKey: string | null = null;
    try {
      [stored, nimKey] = await Promise.all([getStoredSession(bridge), getPersonalNimKey(bridge)]);
    } catch {
      if (!isStale()) setAuthError('The phone secure store could not be read. Restart the app.');
    }
    if (isStale()) return;

    setSession(stored);
    setHasPersonalNimKey(Boolean(nimKey));
    setAccountInitialized(true);

    if (stored) {
      try {
        const prof = await fetchAccountProfile(bridge);
        if (!isStale()) {
          setProfile(prof);
        }
      } catch {
        // Offline or unreachable Supabase is non-fatal when session exists.
      }
      if (!isStale() && !getCachedProductRules(stored.user_id)) {
        try {
          await fetchSharedProductRules(stored.user_id, bridge);
          if (!isStale()) {
            setCacheVersion((v) => v + 1);
          }
        } catch {
          // Will be retried when online / Phone Lite checks rules.
        }
      }
    } else {
      setProfile(null);
    }
  }, [bridge]);

  const refreshLaptop = useCallback(
    async (activePairing: PairingConfig | null, activeSession: AuthSession | null) => {
      if (!activePairing) {
        setLaptopReachable(false);
        setLaptopStatus(null);
        return;
      }
      const gen = appGenRef.current;
      const stateGen = getStateGeneration();
      const isStale = () => appGenRef.current !== gen || getStateGeneration() !== stateGen;

      setBusyAction('refresh');
      setTodayError(null);
      try {
        const statusRes = await fetchLaptopStatus(activePairing, bridge);
        if (isStale()) return;

        setLaptopAuthError(null);
        setLaptopReachable(true);
        setLaptopStatus(statusRes);

        const [draftsRes, remRes, notifRes, sumRes, resPending, agentsRes] =
          await Promise.allSettled([
            fetchLaptopDrafts(activePairing, 30, bridge),
            fetchLaptopReminders(activePairing, bridge),
            fetchLaptopNotifications(activePairing, bridge),
            fetchLaptopSummary(activePairing, bridge),
            fetchLaptopPendingResults(activePairing, bridge),
            fetchLaptopAgents(activePairing, bridge),
          ]);
        if (isStale()) return;

        const settled = [draftsRes, remRes, notifRes, sumRes, resPending, agentsRes];
        const authErr = settled.find(
          (r): r is PromiseRejectedResult =>
            r.status === 'rejected' && r.reason instanceof LaptopAuthError,
        );
        if (authErr) {
          const lapErr = authErr.reason as LaptopAuthError;
          setLaptopAuthError(lapErr.message);
          setLaptopReachable(false);
          hydrateFromCache(activePairing, activeSession);
          return;
        }

        const snapshotUpdate: Partial<Omit<LaptopCachedSnapshot, 'cachedAt' | 'deviceId'>> = {
          status: statusRes,
        };
        if (draftsRes.status === 'fulfilled') {
          setDrafts(draftsRes.value);
          snapshotUpdate.drafts = draftsRes.value;
        }
        if (remRes.status === 'fulfilled') {
          setReminders(remRes.value);
          snapshotUpdate.reminders = remRes.value;
        }
        if (notifRes.status === 'fulfilled') {
          setNotifications(notifRes.value.notifications);
          setUnreadCount(notifRes.value.unread_count);
          snapshotUpdate.notifications = notifRes.value.notifications;
          snapshotUpdate.unreadCount = notifRes.value.unread_count;
        }
        if (sumRes.status === 'fulfilled') {
          setSummary(sumRes.value);
          snapshotUpdate.summary = sumRes.value;
        }
        if (resPending.status === 'fulfilled') {
          setPendingResults(resPending.value);
          snapshotUpdate.pendingResults = resPending.value;
        }
        if (agentsRes.status === 'fulfilled') {
          setAgents(agentsRes.value);
        }

        saveLaptopSnapshot(activePairing.deviceId, snapshotUpdate);
        setCacheVersion((v) => v + 1);

        try {
          const flushRes = await flushBoundQueue(activePairing.deviceId, (items) =>
            postLaptopInboxBatch(activePairing, items, bridge),
          );
          if (isStale()) return;
          setQueueItems(getBoundQueueItems(activePairing.deviceId));
          if (flushRes.acknowledgedIds.length > 0 && draftsRes.status === 'fulfilled') {
            try {
              const refreshedDrafts = await fetchLaptopDrafts(activePairing, 30, bridge);
              if (!isStale()) {
                setDrafts(refreshedDrafts);
                saveLaptopSnapshot(activePairing.deviceId, { drafts: refreshedDrafts });
              }
            } catch {
              // Secondary drafts reload failure does not mark laptop offline.
            }
          }
        } catch (flushErr) {
          if (isStale()) return;
          setQueueItems(getBoundQueueItems(activePairing.deviceId));
          if (flushErr instanceof LaptopAuthError) {
            setLaptopAuthError(flushErr.message);
            setLaptopReachable(false);
          } else if (isLaptopNetworkError(flushErr)) {
            setLaptopReachable(false);
          }
        }
      } catch (err) {
        if (isStale()) return;
        setLaptopReachable(false);
        if (err instanceof LaptopAuthError) {
          setLaptopAuthError(err.message);
        }
        hydrateFromCache(activePairing, activeSession);
      } finally {
        if (!isStale()) {
          setBusyAction(null);
        }
      }
    },
    [bridge, hydrateFromCache],
  );

  const checkSharedItems = useCallback(async () => {
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    try {
      const items = await bridge.takeSharedItems();
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
      if (items.length > 0) {
        setSharedQueue((prev) => [...prev, ...items]);
      }
    } catch {
      // Ignore share-in polling errors.
    }
  }, [bridge]);

  useEffect(() => {
    void refreshAccount();
    void checkSharedItems();
  }, [refreshAccount, checkSharedItems]);

  const pairingDeviceId = pairing?.deviceId ?? null;
  const pairingHost = pairing?.host ?? null;
  const pairingPort = pairing?.port ?? null;
  const sessionUid = session?.user_id ?? null;

  useEffect(() => {
    if (!accountInitialized) return;
    void refreshLaptop(pairingRef.current, sessionRef.current);
  }, [accountInitialized, pairingDeviceId, pairingHost, pairingPort, sessionUid, refreshLaptop]);

  useEffect(() => {
    const unsub = bridge.onDeepLink((url) => {
      if (!url || handledDeepLinksRef.current.has(url)) {
        return;
      }
      handledDeepLinksRef.current.add(url);
      void (async () => {
        const res = await handleAuthDeepLink(url, bridge);
        if (!res.handled && res.reason === 'unsolicited') {
          return;
        }
        if (res.error) {
          setAuthError(res.error);
          return;
        }
        if (res.reason === 'signed_in' && res.session) {
          if (res.accountChanged) {
            resetAllInMemoryState({ keepAccount: false });
          }
          setAuthError(null);
          setSession(res.session);
          await refreshAccount();
          refreshQueueState(getPairingConfig());
          hydrateFromCache(getPairingConfig(), res.session);
          showToast('Signed in with Google');
        }
      })();
    });
    return unsub;
  }, [bridge, hydrateFromCache, refreshAccount, refreshQueueState, resetAllInMemoryState, showToast]);

  useEffect(() => {
    const handleResume = () => {
      void checkSharedItems();
      void refreshLaptop(pairingRef.current, sessionRef.current);
    };
    if (bridge.onResume) {
      return bridge.onResume(handleResume);
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        handleResume();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [bridge, checkSharedItems, refreshLaptop]);

  useEffect(() => {
    if (!bridge.onBackButton) return undefined;
    return bridge.onBackButton(() => {
      const state = backStateRef.current;
      if (state.confirmOpen) {
        setConfirmDialog(null);
        return;
      }
      if (state.shareOpen) {
        setSharedQueue((prev) => prev.slice(1));
        setExtractedProposal(null);
        setShareError(null);
        return;
      }
      if (state.draftOpen) {
        setSelectedDraft(null);
        setDraftsError(null);
        return;
      }
      if (state.tab !== 'today') {
        setTab('today');
        return;
      }
      void bridge.minimizeApp?.();
    });
  }, [bridge]);

  useEffect(() => {
    return () => {
      pairingCancelledRef.current = true;
      pairingGenRef.current += 1;
      appGenRef.current += 1;
      if (pollTimerRef.current !== null) {
        clearTimeout(pollTimerRef.current);
      }
    };
  }, []);

  const cacheSnapshot = useMemo(() => {
    void cacheVersion;
    return loadCache(Date.now(), {
      ownerUid: session?.user_id ?? null,
      laptopDeviceId: pairing?.deviceId ?? null,
    });
  }, [cacheVersion, session?.user_id, pairing?.deviceId]);

  const savedCopyLabel = useMemo(() => {
    const ts =
      cacheSnapshot.laptop?.cachedAt ??
      (cacheSnapshot.mirrorRecords[0]?.cached_at || null);
    return formatSavedCopyLabel(ts);
  }, [cacheSnapshot]);

  const hasProductRules = Boolean(cacheSnapshot.productRules?.rules);

  const visibleAgents =
    laptopReachable && agents.length > 0 ? agents : DEFAULT_LITE_AGENTS;
  const effectiveAgentId = visibleAgents.some((a) => a.id === selectedAgentId)
    ? selectedAgentId
    : visibleAgents[0].id;

  const handleLaptopActionError = useCallback((err: unknown) => {
    if (err instanceof LaptopAuthError) {
      setLaptopAuthError(err.message);
      setLaptopReachable(false);
    } else if (isLaptopNetworkError(err)) {
      setLaptopReachable(false);
    }
  }, []);

  async function handleSelectDraft(id: string) {
    setDraftsError(null);
    const local = drafts.find((d) => d.id === id) ?? null;
    if (local?.confidential) {
      setSelectedDraft(local);
      return;
    }
    const activePairing = pairingRef.current;
    if (activePairing && laptopReachable) {
      const gen = appGenRef.current;
      const stateGen = getStateGeneration();
      try {
        const detail = await fetchLaptopDraftDetail(activePairing, id, bridge);
        if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
        setSelectedDraft(detail);
        const existingDetails = loadCache(Date.now(), { laptopDeviceId: activePairing.deviceId })
          .laptop?.draftDetails ?? {};
        saveLaptopSnapshot(activePairing.deviceId, {
          draftDetails: { ...existingDetails, [id]: detail },
        });
        return;
      } catch (err) {
        if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
        handleLaptopActionError(err);
        if (!(err instanceof LaptopAuthError)) {
          setDraftsError(err instanceof Error ? err.message : 'Could not load draft detail.');
        }
      }
    }
    const cachedDetail = cacheSnapshot.laptop?.draftDetails?.[id];
    setSelectedDraft(cachedDetail ?? local);
  }

  async function handleAddComment(
    contentId: string,
    body: string,
    version?: number | null,
  ): Promise<boolean> {
    const activePairing = pairingRef.current;
    if (!activePairing) return false;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    setDraftsError(null);
    setBusyAction('comment');
    try {
      const comment = await postLaptopDraftComment(activePairing, contentId, body, version, bridge);
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      setSelectedDraft((prev) =>
        prev && prev.id === contentId
          ? { ...prev, comments: [...(prev.comments ?? []), comment] }
          : prev,
      );
      showToast('Comment added');
      return true;
    } catch (err) {
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      handleLaptopActionError(err);
      const msg = err instanceof Error ? err.message : 'Could not add comment';
      setDraftsError(msg);
      showToast(msg);
      return false;
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleMarkPosted(contentId: string, url?: string): Promise<boolean> {
    const activePairing = pairingRef.current;
    if (!activePairing) return false;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    setDraftsError(null);
    setBusyAction('posted');
    try {
      const updated = await postLaptopDraftPosted(activePairing, contentId, url, bridge);
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      setSelectedDraft((prev) => (prev && prev.id === contentId ? { ...prev, ...updated } : prev));
      setDrafts((prev) => prev.map((d) => (d.id === contentId ? { ...d, ...updated } : d)));
      showToast('Marked as posted');
      return true;
    } catch (err) {
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      handleLaptopActionError(err);
      const msg = err instanceof Error ? err.message : 'Could not mark as posted';
      setDraftsError(msg);
      showToast(msg);
      return false;
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleCopyText(text: string): Promise<boolean> {
    try {
      if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) {
        throw new Error('Clipboard is unavailable.');
      }
      await navigator.clipboard.writeText(text);
      showToast('Copied text');
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not copy text';
      setDraftsError(msg);
      showToast(msg);
      return false;
    }
  }

  async function handleShareOut(title: string, text: string): Promise<boolean> {
    try {
      await bridge.shareOut({ title, text });
      showToast('Shared');
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not share draft';
      setDraftsError(msg);
      showToast(msg);
      return false;
    }
  }

  async function handleSaveInboxOrQueue(input: {
    kind: QueueItemKind;
    title: string;
    body: string;
    platform?: string;
    url?: string;
  }): Promise<boolean> {
    const activePairing = pairingRef.current;
    if (!activePairing?.deviceId) {
      throw new Error('Pair with your laptop before saving inbox items.');
    }

    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    setBusyAction('create-item');
    setDraftsError(null);
    try {
      // Every inbox item (online or offline) is first saved to the queue with a random id.
      const queued = enqueueInboxItem({
        kind: input.kind,
        title: input.title,
        body: input.body,
        platform: input.platform,
        url: input.url,
        ownerUid: sessionRef.current?.user_id || '',
        laptopDeviceId: activePairing.deviceId,
      });
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      refreshQueueState(activePairing);

      if (laptopReachable) {
        try {
          const res = await flushBoundQueue(activePairing.deviceId, (items) =>
            postLaptopInboxBatch(activePairing, items, bridge),
          );
          if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
          refreshQueueState(activePairing);

          if (res.acknowledgedIds.includes(queued.client_id)) {
            showToast(`Sent ${input.kind} to laptop`);
            await refreshLaptop(activePairing, sessionRef.current);
            return true;
          }
          if (res.failedIds.includes(queued.client_id)) {
            const failedItem = res.remaining.find((r) => r.client_id === queued.client_id);
            const errMsg = failedItem?.last_error || 'Laptop rejected this item.';
            setDraftsError(errMsg);
            showToast(errMsg);
            return true;
          }
        } catch (err) {
          if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
          refreshQueueState(activePairing);
          handleLaptopActionError(err);
          showToast(`Queued ${input.kind} for laptop sync`);
          return true;
        }
      }

      showToast(`Queued ${input.kind} for laptop sync`);
      return true;
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleSendChatMessage(text: string, agentIdOverride?: string): Promise<boolean> {
    setChatError(null);
    const activeAgentId = agentIdOverride || effectiveAgentId;
    const activePairing = pairingRef.current;
    const activeUid = sessionRef.current?.user_id ?? null;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();

    setChatBusy(true);
    try {
      if (activePairing && laptopReachable) {
        const res = await postLaptopAgentMessage(activePairing, activeAgentId, text, bridge);
        if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
        if (!res.success) {
          setChatError(res.error || 'Agent request failed.');
          return false;
        }
        const nowIso = new Date().toISOString();
        setChatMessages((prev) => [
          ...prev,
          {
            id: `u_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
            role: 'user',
            agentId: activeAgentId,
            text,
            mode: 'laptop',
            ownerUid: activeUid,
            createdAt: nowIso,
          },
          {
            id: `a_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
            role: 'assistant',
            agentId: res.agent?.name || activeAgentId,
            text: res.response || '',
            mode: 'laptop',
            ownerUid: activeUid,
            confidential: Boolean(res.confidential),
            waitingUser: Boolean(res.waiting_user),
            question: res.question,
            options: res.options,
            createdAt: nowIso,
          },
        ]);
        return true;
      }

      // Cloud history contains ONLY successful Phone Lite turns from this account.
      const history = chatMessages
        .filter(
          (m) =>
            m.mode === 'phone-lite' &&
            !m.failed &&
            (m.ownerUid ?? null) === activeUid &&
            (m.role === 'user' || m.role === 'assistant'),
        )
        .map((m) => ({
          role: m.role as 'user' | 'assistant',
          content: m.text,
        }));

      const liteRes = await sendPhoneLiteMessage({
        agentId: activeAgentId,
        text,
        history,
        bridge,
      });
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      setCacheVersion((v) => v + 1);
      const nowIso = new Date().toISOString();
      setChatMessages((prev) => [
        ...prev,
        {
          id: `u_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          role: 'user',
          agentId: activeAgentId,
          text,
          mode: 'phone-lite',
          ownerUid: activeUid,
          createdAt: nowIso,
        },
        {
          id: `a_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          role: 'assistant',
          agentId: `${activeAgentId} (${liteRes.provider})`,
          text: liteRes.reply,
          mode: 'phone-lite',
          ownerUid: activeUid,
          createdAt: nowIso,
        },
      ]);
      return true;
    } catch (err) {
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      handleLaptopActionError(err);
      setChatError(err instanceof Error ? err.message : 'Message failed.');
      return false;
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setChatBusy(false);
      }
    }
  }

  function handleParsePairCode(raw: string) {
    setPairingError(null);
    try {
      const parsed = parsePairingPayload(raw);
      setPendingPairPayload(parsed);
      setPairingStep('confirm');
    } catch (err) {
      setPairingError(err instanceof Error ? err.message : 'Invalid pairing code.');
    }
  }

  async function handleScanQr() {
    setPairingError(null);
    try {
      const scanned = await bridge.scanQrCode();
      if (scanned) {
        handleParsePairCode(scanned);
      }
    } catch (err) {
      setPairingError(err instanceof Error ? err.message : 'QR scan failed.');
    }
  }

  async function handleConfirmPairing(deviceName: string) {
    if (!pendingPairPayload) return;
    const payload = pendingPairPayload;
    setPairingError(null);
    setBusyAction('pair');
    pairingCancelledRef.current = false;
    const pairGen = ++pairingGenRef.current;
    const appGen = appGenRef.current;
    const stateGen = getStateGeneration();
    const isCancelled = () =>
      pairingCancelledRef.current ||
      pairingGenRef.current !== pairGen ||
      appGenRef.current !== appGen ||
      getStateGeneration() !== stateGen;

    try {
      const req = await submitPairRequest(payload, deviceName, bridge);
      if (isCancelled()) return;
      setPairingStep('waiting');

      const pollOnce = async () => {
        if (isCancelled()) return;
        try {
          const poll = await pollPairRequest(
            payload,
            req.request_id,
            req.poll_secret,
            sessionRef.current?.user_id || '',
            bridge,
            isCancelled,
          );
          if (isCancelled() || poll.status === 'cancelled') return;
          if (poll.status === 'approved' && poll.pairing) {
            setPairing(poll.pairing);
            setPairingStep('idle');
            setPendingPairPayload(null);
            setLaptopAuthError(null);
            refreshQueueState(poll.pairing);
            showToast('Laptop paired');
            return;
          }
          if (poll.status === 'denied') {
            setPairingStep('idle');
            setPendingPairPayload(null);
            setPairingError('Pairing request was denied on the laptop.');
            return;
          }
          pollTimerRef.current = setTimeout(() => {
            void pollOnce();
          }, 1500);
        } catch (err) {
          if (isCancelled()) return;
          setPairingStep('idle');
          setPendingPairPayload(null);
          setPairingError(err instanceof Error ? err.message : 'Pairing poll failed.');
        }
      };

      await pollOnce();
    } catch (err) {
      if (!isCancelled()) {
        setPairingStep('idle');
        setPairingError(err instanceof Error ? err.message : 'Pairing failed.');
      }
    } finally {
      if (!isCancelled()) {
        setBusyAction(null);
      }
    }
  }

  function handleCancelPairing() {
    pairingCancelledRef.current = true;
    pairingGenRef.current += 1;
    if (pollTimerRef.current !== null) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setBusyAction(null);
    setPairingStep('idle');
    setPendingPairPayload(null);
  }

  async function performUnpair() {
    setBusyAction('unpair');
    setMoreError(null);
    try {
      appGenRef.current += 1;
      await unpairLaptop(bridge);
      resetAllInMemoryState({ keepAccount: true });
      hydrateFromCache(null, sessionRef.current);
      showToast('Laptop unpaired');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not unpair laptop.';
      setMoreError(msg);
      showToast(msg);
    } finally {
      setBusyAction(null);
    }
  }

  function requestUnpair() {
    const activePairing = pairingRef.current;
    const unsentCount = activePairing?.deviceId
      ? getBoundQueueItems(activePairing.deviceId).length
      : queueItems.length;
    if (unsentCount > 0) {
      setConfirmDialog({
        kind: 'unpair',
        title: 'Unpair laptop?',
        message: `You have ${unsentCount} unsent queued item${unsentCount === 1 ? '' : 's'} that will be lost if you unpair now.`,
        confirmLabel: 'Unpair laptop',
      });
      return;
    }
    void performUnpair();
  }

  async function performSignOut() {
    setBusyAction('signout');
    setMoreError(null);
    try {
      appGenRef.current += 1;
      await signOut(bridge);
      resetAllInMemoryState({ keepAccount: false });
      showToast('Signed out');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not sign out.';
      setMoreError(msg);
      showToast(msg);
    } finally {
      setBusyAction(null);
    }
  }

  function requestSignOut() {
    const unsentCount = loadQueue().length;
    setConfirmDialog({
      kind: 'signout',
      title: 'Sign out?',
      message: `Signing out wipes your session, laptop pairing, personal key, cached data, and ${unsentCount} unsent queued item${unsentCount === 1 ? '' : 's'} that will be lost.`,
      confirmLabel: 'Sign out',
    });
  }

  const activeShareItem = sharedQueue[0] ?? null;

  function dismissShareItem() {
    setSharedQueue((prev) => prev.slice(1));
    setExtractedProposal(null);
    setShareError(null);
  }

  return (
    <div className="mg-root mob-shell">
      {bridge.isPreview && bridge.previewLabel ? (
        <div className="mob-preview-banner" role="status">
          {bridge.previewLabel}
        </div>
      ) : null}

      <header className="mob-header">
        <Wordmark compact />
        <div className="mob-header-meta">
          <StatusDot
            state={
              laptopAuthError
                ? 'error'
                : laptopReachable
                  ? 'online'
                  : pairing
                    ? 'idle'
                    : 'off'
            }
            label={
              laptopAuthError
                ? '401 Auth'
                : laptopReachable
                  ? 'Laptop'
                  : pairing
                    ? 'Phone Lite'
                    : 'Unpaired'
            }
          />
          {queueItems.length > 0 ? (
            <span className="mg-chip">{queueItems.length} queued</span>
          ) : null}
        </div>
      </header>

      <nav className="mob-tabs-wrap" aria-label="Mobile sections">
        <NumberedTabs
          items={MOBILE_TABS}
          current={tab}
          onSelect={(next) => setTab(next as MobileTabId)}
          orientation="horizontal"
          ariaLabel="Mobile sections"
        />
      </nav>

      <main className="mob-main">
        {tab === 'today' ? (
          <TodayTab
            pairing={pairing}
            laptopReachable={laptopReachable}
            laptopAuthError={laptopAuthError}
            laptopStatus={laptopStatus}
            savedCopyLabel={savedCopyLabel}
            reminders={reminders}
            notifications={notifications}
            unreadCount={unreadCount}
            summary={summary}
            pendingResults={pendingResults}
            busyAction={busyAction}
            errorMessage={todayError}
            onRefresh={() => void refreshLaptop(pairingRef.current, sessionRef.current)}
            onGoToPairing={() => setTab('more')}
            onMarkReminderDone={(id) => {
              const activePairing = pairingRef.current;
              if (!activePairing) return;
              const gen = appGenRef.current;
              const stateGen = getStateGeneration();
              setTodayError(null);
              setBusyAction(`rem-${id}`);
              void postLaptopReminderDone(activePairing, id, bridge)
                .then(() => {
                  if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                  setReminders((prev) => prev.filter((r) => r.id !== id));
                  showToast('Reminder marked done');
                })
                .catch((err) => {
                  if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                  handleLaptopActionError(err);
                  const msg = err instanceof Error ? err.message : 'Could not update reminder.';
                  setTodayError(msg);
                  showToast(msg);
                })
                .finally(() => {
                  if (appGenRef.current === gen && getStateGeneration() === stateGen) {
                    setBusyAction(null);
                  }
                });
            }}
            onMarkAllNotificationsRead={() => {
              const activePairing = pairingRef.current;
              if (!activePairing) return;
              const gen = appGenRef.current;
              const stateGen = getStateGeneration();
              setTodayError(null);
              setBusyAction('notif-read');
              void postLaptopNotificationsRead(activePairing, { all: true }, bridge)
                .then((res) => {
                  if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                  const nowIso = new Date().toISOString();
                  setNotifications((prev) => prev.map((n) => ({ ...n, read_at: n.read_at || nowIso })));
                  setUnreadCount(typeof res?.unread_count === 'number' ? res.unread_count : 0);
                })
                .catch((err) => {
                  if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                  handleLaptopActionError(err);
                  const msg = err instanceof Error ? err.message : 'Could not mark notifications read.';
                  setTodayError(msg);
                  showToast(msg);
                })
                .finally(() => {
                  if (appGenRef.current === gen && getStateGeneration() === stateGen) {
                    setBusyAction(null);
                  }
                });
            }}
            onConfirmResult={async (proposalId, metrics, platform, contentId) => {
              const activePairing = pairingRef.current;
              if (!activePairing) return false;
              const gen = appGenRef.current;
              const stateGen = getStateGeneration();
              setTodayError(null);
              setBusyAction(`confirm-${proposalId}`);
              try {
                await confirmLaptopResults(
                  activePairing,
                  proposalId,
                  { metrics, platform, content_id: contentId },
                  bridge,
                );
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
                setPendingResults((prev) => prev.filter((p) => p.id !== proposalId));
                showToast('Results saved');
                return true;
              } catch (err) {
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
                handleLaptopActionError(err);
                const msg = err instanceof Error ? err.message : 'Could not save results.';
                setTodayError(msg);
                showToast(msg);
                return false;
              } finally {
                if (appGenRef.current === gen && getStateGeneration() === stateGen) {
                  setBusyAction(null);
                }
              }
            }}
          />
        ) : null}

        {tab === 'drafts' ? (
          <DraftsTab
            drafts={drafts}
            selectedDraft={selectedDraft}
            laptopReachable={laptopReachable}
            savedCopyLabel={savedCopyLabel}
            canQueueOffline={Boolean(pairing)}
            busyAction={busyAction}
            errorMessage={draftsError}
            onSelectDraft={(id) => void handleSelectDraft(id)}
            onCloseDraft={() => {
              setSelectedDraft(null);
              setDraftsError(null);
            }}
            onAddComment={handleAddComment}
            onMarkPosted={handleMarkPosted}
            onCopyText={handleCopyText}
            onShareOut={handleShareOut}
            onCreateOfflineItem={handleSaveInboxOrQueue}
          />
        ) : null}

        {tab === 'chat' ? (
          <ChatTab
            laptopReachable={laptopReachable}
            agents={agents}
            selectedAgentId={effectiveAgentId}
            onSelectAgent={setSelectedAgentId}
            messages={chatMessages}
            hasPersonalNimKey={hasPersonalNimKey}
            signedIn={Boolean(session)}
            hasProductRules={hasProductRules}
            busy={chatBusy}
            errorMessage={chatError}
            onSendMessage={handleSendChatMessage}
            onDictate={() => bridge.dictate()}
          />
        ) : null}

        {tab === 'more' ? (
          <MoreTab
            pairing={pairing}
            pairingStep={pairingStep}
            pendingPairPayload={pendingPairPayload}
            pairingError={pairingError}
            session={session}
            profile={profile}
            authError={authError}
            moreError={moreError}
            hasPersonalNimKey={hasPersonalNimKey}
            queueItems={queueItems}
            laptopReachable={laptopReachable}
            mirrorRecordCount={cacheSnapshot.mirrorRecords.length}
            savedCopyLabel={savedCopyLabel}
            busyAction={busyAction}
            onScanQr={handleScanQr}
            onParsePairCode={handleParsePairCode}
            onConfirmPairing={handleConfirmPairing}
            onCancelPairing={handleCancelPairing}
            onUnpair={requestUnpair}
            onGoogleSignIn={async () => {
              setAuthError(null);
              setBusyAction('signin');
              try {
                await startGoogleSignIn(bridge);
              } catch (err) {
                setAuthError(err instanceof Error ? err.message : 'Could not open Google sign-in.');
              } finally {
                setBusyAction(null);
              }
            }}
            onSignOut={requestSignOut}
            onSavePersonalKey={async (key) => {
              setMoreError(null);
              setBusyAction('nim-key');
              try {
                await setPersonalNimKey(key, bridge);
                setHasPersonalNimKey(true);
                showToast('Personal key saved');
                return true;
              } catch (err) {
                const msg = err instanceof Error ? err.message : 'Could not save personal key.';
                setMoreError(msg);
                showToast(msg);
                return false;
              } finally {
                setBusyAction(null);
              }
            }}
            onRemovePersonalKey={async () => {
              setMoreError(null);
              setBusyAction('nim-key');
              try {
                await setPersonalNimKey(null, bridge);
                setHasPersonalNimKey(false);
                showToast('Personal key removed');
              } catch (err) {
                const msg = err instanceof Error ? err.message : 'Could not remove personal key.';
                setMoreError(msg);
                showToast(msg);
              } finally {
                setBusyAction(null);
              }
            }}
            onFlushQueue={async () => {
              const activePairing = pairingRef.current;
              if (!activePairing) return;
              const gen = appGenRef.current;
              const stateGen = getStateGeneration();
              setMoreError(null);
              setBusyAction('flush-queue');
              try {
                const res = await flushBoundQueue(activePairing.deviceId, (items) =>
                  postLaptopInboxBatch(activePairing, items, bridge),
                );
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                refreshQueueState(activePairing);
                if (res.failedIds.length > 0) {
                  const msg = `Synced ${res.acknowledgedIds.length}; ${res.failedIds.length} item(s) rejected by laptop.`;
                  setMoreError(msg);
                  showToast(msg);
                } else {
                  showToast(`Synced ${res.acknowledgedIds.length} queued items`);
                }
              } catch (err) {
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                refreshQueueState(activePairing);
                handleLaptopActionError(err);
                const msg = err instanceof Error ? err.message : 'Sync failed';
                setMoreError(msg);
                showToast(msg);
              } finally {
                if (appGenRef.current === gen && getStateGeneration() === stateGen) {
                  setBusyAction(null);
                }
              }
            }}
            onRemoveQueuedItem={(clientId) => {
              removeQueuedItem(clientId);
              refreshQueueState(pairingRef.current);
            }}
            onSyncMirror={async () => {
              const uid = sessionRef.current?.user_id;
              if (!uid) return;
              const gen = appGenRef.current;
              const stateGen = getStateGeneration();
              setMoreError(null);
              setBusyAction('sync-mirror');
              try {
                await fetchOwnMirrorRecords(uid, bridge);
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                await fetchSharedProductRules(uid, bridge).catch(() => {});
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                hydrateFromCache(pairingRef.current, sessionRef.current);
                showToast('Context cache synced');
              } catch (err) {
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                const msg = err instanceof Error ? err.message : 'Cache sync failed';
                setMoreError(msg);
                showToast(msg);
              } finally {
                if (appGenRef.current === gen && getStateGeneration() === stateGen) {
                  setBusyAction(null);
                }
              }
            }}
          />
        ) : null}
      </main>

      <ShareInModal
        item={activeShareItem}
        laptopReachable={laptopReachable}
        busy={shareBusy}
        extractedProposal={extractedProposal}
        errorMessage={shareError}
        onDismiss={dismissShareItem}
        onSaveAsIdea={async (title, body, url) => {
          setShareBusy(true);
          setShareError(null);
          try {
            await handleSaveInboxOrQueue({ kind: 'idea', title, body, url });
            dismissShareItem();
          } catch (err) {
            setShareError(err instanceof Error ? err.message : 'Could not save idea');
          } finally {
            setShareBusy(false);
          }
        }}
        onSaveAsNote={async (title, body, url) => {
          setShareBusy(true);
          setShareError(null);
          try {
            await handleSaveInboxOrQueue({ kind: 'note', title, body, url });
            dismissShareItem();
          } catch (err) {
            setShareError(err instanceof Error ? err.message : 'Could not save note');
          } finally {
            setShareBusy(false);
          }
        }}
        onExtractScreenshot={async (imageBase64, platform) => {
          const activePairing = pairingRef.current;
          if (!activePairing) return;
          const gen = appGenRef.current;
          const stateGen = getStateGeneration();
          setShareBusy(true);
          setShareError(null);
          try {
            const res = await extractLaptopResults(
              activePairing,
              { image_base64: imageBase64, platform },
              bridge,
            );
            if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
            setExtractedProposal(res.proposal);
          } catch (err) {
            if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
            handleLaptopActionError(err);
            setShareError(err instanceof Error ? err.message : 'Screenshot read failed');
          } finally {
            if (appGenRef.current === gen && getStateGeneration() === stateGen) {
              setShareBusy(false);
            }
          }
        }}
        onConfirmExtracted={async (proposalId, metrics, platform, contentId) => {
          const activePairing = pairingRef.current;
          if (!activePairing) return;
          const gen = appGenRef.current;
          const stateGen = getStateGeneration();
          setShareBusy(true);
          setShareError(null);
          try {
            await confirmLaptopResults(
              activePairing,
              proposalId,
              { metrics, platform, content_id: contentId },
              bridge,
            );
            if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
            showToast('Results saved');
            dismissShareItem();
          } catch (err) {
            if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
            handleLaptopActionError(err);
            setShareError(err instanceof Error ? err.message : 'Could not save results');
          } finally {
            if (appGenRef.current === gen && getStateGeneration() === stateGen) {
              setShareBusy(false);
            }
          }
        }}
      />

      {confirmDialog ? (
        <Dialog
          open
          title={confirmDialog.title}
          onClose={() => setConfirmDialog(null)}
          actions={
            <div className="mob-actions">
              <PillButton
                variant="danger"
                size="sm"
                onClick={() => {
                  const kind = confirmDialog.kind;
                  setConfirmDialog(null);
                  if (kind === 'signout') {
                    void performSignOut();
                  } else {
                    void performUnpair();
                  }
                }}
              >
                {confirmDialog.confirmLabel}
              </PillButton>
              <PillButton variant="outline" size="sm" onClick={() => setConfirmDialog(null)}>
                Cancel
              </PillButton>
            </div>
          }
        >
          <p className="mob-body-text">{confirmDialog.message}</p>
        </Dialog>
      ) : null}

      {toast ? (
        <div className="mg-toast mob-toast" role="status" aria-live="polite">
          {toast}
        </div>
      ) : null}
    </div>
  );
}
