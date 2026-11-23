import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Dialog, NumberedTabs, PillButton, StatusDot, Wordmark, type TabItem } from '@magnm/design/react';
import { enqueueAction, loadActions, removeAction, type ActionInput } from './actions';
import {
  ConfidentialRefusalError,
  cancelLaptopSocialRun,
  confirmLaptopResults,
  createLaptopSocialRun,
  extractLaptopResults,
  fetchLaptopAgents,
  fetchLaptopDraftDetail,
  fetchLaptopDrafts,
  fetchLaptopNotifications,
  fetchLaptopPendingResults,
  fetchLaptopPitchProposals,
  fetchLaptopReminders,
  fetchLaptopSocialRuns,
  fetchLaptopSocialStatus,
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
  postLaptopSocialChallenge,
  ProductRulesUnavailableError,
  sendPhoneLiteMessage,
  shareLaptopDraftFile,
  startLaptopSocialRun,
  summariseLaptopSocialRun,
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
  formatPricesAsOfLabel,
  formatSavedCopyLabel,
  getCachedProductRules,
  loadCache,
  saveLaptopSnapshot,
  type LaptopCachedSnapshot,
} from './cache';
import { decideRoute, getConfiguredAgent } from './agentTools';
import { syncPhoneActions } from './cloudActions';
import { PhoneCopyError, fetchPhoneCopies, getCopyOnPhone, isCopyUsable, type PhoneCopy } from './phoneCopies';
import { rulesForQueue } from './queueRules';
import { ChatTab, DEFAULT_LITE_AGENTS } from './components/ChatTab';
import { DraftsTab } from './components/DraftsTab';
import { MoreTab } from './components/MoreTab';
import { PitchTab } from './components/PitchTab';
import { ShareInModal } from './components/ShareInModal';
import { SocialLiveView } from './components/SocialLiveView';
import { TodayTab } from './components/TodayTab';
import { WelcomeGate } from './components/WelcomeGate';
import { getNativeBridge, type NativeBridge, type SharedItem } from './native';
import { draftsFromCache, mirrorIsNewer, remindersFromCache } from './mirrorView';
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
  PhoneAction,
  PhoneAgentInfo,
  PhoneNotification,
  PitchProposalItem,
  QueueItemKind,
  QueuedInboxItem,
  ReminderItem,
  SocialPlatform,
  SocialRecipe,
  SocialRunRecord,
  SocialStatusInfo,
  WeeklySummary,
} from './types';

const MOBILE_TABS: TabItem[] = [
  { id: 'today', label: 'Today' },
  { id: 'drafts', label: 'Drafts' },
  { id: 'pitch', label: 'Pitch' },
  { id: 'chat', label: 'Chat' },
  { id: 'more', label: 'More' },
];

export interface AppProps {
  bridge?: NativeBridge;
  initialTab?: MobileTabId;
}

const SCAN_CANCELLED_MESSAGE =
  'The scan was cancelled. You can also paste the pairing code from Settings > Phone on your laptop into the box below and tap Continue.';

function isLaptopNetworkError(err: unknown): boolean {
  if (err instanceof LaptopAuthError) return false;
  if (err instanceof HttpStatusError) return false;
  if (err instanceof NetworkPolicyError) return false;
  if (err instanceof ConfidentialRefusalError) return false;
  if (err instanceof ProductRulesUnavailableError) return false;
  return true;
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
  const socialGenRef = useRef(0);
  const isForegroundRef = useRef(true);
  const handledDeepLinksRef = useRef(new Set<string>());

  const [accountInitialized, setAccountInitialized] = useState(false);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [hasPersonalNimKey, setHasPersonalNimKey] = useState(false);

  const [laptopReachable, setLaptopReachable] = useState(false);
  const [laptopAuthError, setLaptopAuthError] = useState<string | null>(null);
  const [laptopStatus, setLaptopStatus] = useState<LaptopStatus | null>(null);
  const [drafts, setDrafts] = useState<DraftItem[]>(() => draftsFromCache(initialCache));
  const [selectedDraft, setSelectedDraft] = useState<DraftItem | null>(null);
  const [reminders, setReminders] = useState<ReminderItem[]>(() => remindersFromCache(initialCache));
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
  const [pitchProposals, setPitchProposals] = useState<PitchProposalItem[]>(
    () => initialCache.laptop?.pitchProposals ?? [],
  );
  const [pitchPricesAsOf, setPitchPricesAsOf] = useState<number | null>(
    () => initialCache.laptop?.pitchPricesAsOf ?? null,
  );
  const [presentingPitch, setPresentingPitch] = useState<PitchProposalItem | null>(null);
  const [pitchError, setPitchError] = useState<string | null>(null);
  const [agents, setAgents] = useState<PhoneAgentInfo[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState('writer');
  const [chatMessages, setChatMessages] = useState<ChatTurn[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);

  // Social research state lives strictly in memory; never persisted to cache or localStorage.
  const [socialStatus, setSocialStatus] = useState<SocialStatusInfo | null>(null);
  const [socialRuns, setSocialRuns] = useState<SocialRunRecord[]>([]);
  const [socialLiveOpen, setSocialLiveOpen] = useState(false);
  const [socialError, setSocialError] = useState<string | null>(null);
  const [isForeground, setIsForeground] = useState(true);

  const [todayError, setTodayError] = useState<string | null>(null);
  const [draftsError, setDraftsError] = useState<string | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);

  const [queueItems, setQueueItems] = useState<QueuedInboxItem[]>(() =>
    initialPairing ? getBoundQueueItems(initialPairing.deviceId) : [],
  );
  const [phoneActions, setPhoneActions] = useState<PhoneAction[]>([]);
  const [phoneCopies, setPhoneCopies] = useState<PhoneCopy[]>([]);
  const phoneActionsRef = useRef<PhoneAction[]>([]);
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
  const laptopReachableRef = useRef(false);
  laptopReachableRef.current = laptopReachable;
  const lastMirrorAtRef = useRef(0);
  const backStateRef = useRef({
    confirmOpen: false,
    socialLiveOpen: false,
    shareOpen: false,
    pitchPresentOpen: false,
    draftOpen: false,
    tab: initialTab,
  });
  backStateRef.current = {
    confirmOpen: Boolean(confirmDialog),
    socialLiveOpen,
    shareOpen: sharedQueue.length > 0,
    pitchPresentOpen: Boolean(presentingPitch),
    draftOpen: Boolean(selectedDraft),
    tab,
  };

  const showToast = useCallback((msg: string) => {
    setToast(msg);
  }, []);

  useEffect(() => {
    phoneActionsRef.current = phoneActions;
  }, [phoneActions]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  const resetAllInMemoryState = useCallback((options: { keepAccount?: boolean } = {}) => {
    appGenRef.current += 1;
    pairingGenRef.current += 1;
    socialGenRef.current += 1;
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
    setPitchProposals([]);
    setPitchPricesAsOf(null);
    setPresentingPitch(null);
    setPitchError(null);
    setAgents([]);
    setSelectedAgentId('writer');
    setChatMessages([]);
    setChatError(null);
    setSocialStatus(null);
    setSocialRuns([]);
    setSocialLiveOpen(false);
    setSocialError(null);
    setTodayError(null);
    setDraftsError(null);
    setMoreError(null);
    setQueueItems([]);
    setPhoneActions([]);
    setPhoneCopies([]);
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
      setDrafts(draftsFromCache(cache));
      setReminders(remindersFromCache(cache));
      setNotifications(cache.laptop?.notifications ?? []);
      setUnreadCount(cache.laptop?.unreadCount ?? 0);
      setSummary(cache.laptop?.summary ?? null);
      setPendingResults(cache.laptop?.pendingResults ?? []);
      setPitchProposals(cache.laptop?.pitchProposals ?? []);
      setPitchPricesAsOf(cache.laptop?.pitchPricesAsOf ?? null);
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
    setPhoneActions(loadActions(stored?.user_id));
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

  // Cloud side of the app: send waiting phone actions, read back what the laptop did, and refresh the mirror
  // (so drafts, comments and reminders show without any laptop connection and without the Sync button).
  const syncCloud = useCallback(
    async (opts: { mirror?: boolean; force?: boolean } = {}) => {
      const uid = sessionRef.current?.user_id;
      if (!uid) return;
      const gen = appGenRef.current;
      const stateGen = getStateGeneration();
      const isStale = () =>
        appGenRef.current !== gen || getStateGeneration() !== stateGen || sessionRef.current?.user_id !== uid;
      try {
        await syncPhoneActions(uid, bridge);
        if (isStale()) return;
        const nextActions = loadActions(uid);
        // An agent that answered while the phone was open: tell the owner (never the reply text).
        const known = new Map(phoneActionsRef.current.map((a) => [a.id, a.state]));
        for (const a of nextActions) {
          if (a.kind === 'agent_task' && a.state === 'applied' && known.get(a.id) === 'waiting') {
            showToast(`${getConfiguredAgent(a.target_id)?.name ?? 'An agent'} replied`);
          }
        }
        setPhoneActions(nextActions);
        const wantMirror =
          opts.mirror !== false && (opts.force === true || Date.now() - lastMirrorAtRef.current > 5000);
        if (!wantMirror) return;
        lastMirrorAtRef.current = Date.now();
        await fetchOwnMirrorRecords(uid, bridge);
        if (isStale()) return;
        // The full-quality copies of finished draft files the laptop keeps in the cloud (this account's only).
        void fetchPhoneCopies(uid, bridge)
          .then((copies) => {
            if (!isStale()) setPhoneCopies(copies);
          })
          .catch(() => undefined);
        // Refresh the shared rules every time (a failed fetch keeps the saved copy).
        await fetchSharedProductRules(uid, bridge).catch(() => undefined);
        if (isStale()) return;
        if (laptopReachableRef.current) {
          setCacheVersion((v) => v + 1);
        } else {
          hydrateFromCache(pairingRef.current, sessionRef.current);
        }
      } catch {
        // Offline or the cloud is down: the next open, foreground or timer tries again.
      }
    },
    [bridge, hydrateFromCache, showToast],
  );

  const refreshLaptop = useCallback(
    async (activePairing: PairingConfig | null, activeSession: AuthSession | null) => {
      if (!activePairing) {
        setLaptopReachable(false);
        setLaptopStatus(null);
        return;
      }
      const gen = appGenRef.current;
      const socGen = socialGenRef.current;
      const stateGen = getStateGeneration();
      const isStale = () => appGenRef.current !== gen || getStateGeneration() !== stateGen;
      const isSocialStale = () =>
        isStale() || socialGenRef.current !== socGen || !isForegroundRef.current;

      setBusyAction('refresh');
      setTodayError(null);
      try {
        const statusRes = await fetchLaptopStatus(activePairing, bridge);
        if (isStale()) return;

        setLaptopAuthError(null);
        setLaptopReachable(true);
        setLaptopStatus(statusRes);

        const [
          draftsRes,
          remRes,
          notifRes,
          sumRes,
          resPending,
          agentsRes,
          pitchRes,
          socStatusRes,
          socRunsRes,
        ] = await Promise.allSettled([
          fetchLaptopDrafts(activePairing, 30, bridge),
          fetchLaptopReminders(activePairing, bridge),
          fetchLaptopNotifications(activePairing, bridge),
          fetchLaptopSummary(activePairing, bridge),
          fetchLaptopPendingResults(activePairing, bridge),
          fetchLaptopAgents(activePairing, bridge),
          fetchLaptopPitchProposals(activePairing, bridge),
          isForegroundRef.current
            ? fetchLaptopSocialStatus(activePairing, bridge)
            : Promise.resolve(null),
          isForegroundRef.current
            ? fetchLaptopSocialRuns(activePairing, 30, bridge)
            : Promise.resolve(null),
        ]);
        if (isStale()) return;

        const settled = [
          draftsRes,
          remRes,
          notifRes,
          sumRes,
          resPending,
          agentsRes,
          pitchRes,
          socStatusRes,
          socRunsRes,
        ];
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
        if (pitchRes.status === 'fulfilled') {
          const syncedAt = Date.now();
          setPitchProposals(pitchRes.value);
          setPitchPricesAsOf(syncedAt);
          snapshotUpdate.pitchProposals = pitchRes.value;
          snapshotUpdate.pitchPricesAsOf = syncedAt;
        }
        if (!isSocialStale()) {
          if (socStatusRes.status === 'fulfilled' && socStatusRes.value) {
            setSocialStatus(socStatusRes.value);
          }
          if (socRunsRes.status === 'fulfilled' && Array.isArray(socRunsRes.value)) {
            setSocialRuns(socRunsRes.value);
          }
        }

        saveLaptopSnapshot(activePairing.deviceId, snapshotUpdate);
        setCacheVersion((v) => v + 1);

        try {
          const flushRes = await flushBoundQueue(activePairing.deviceId, (items) =>
            postLaptopInboxBatch(activePairing, items, bridge),
          );
          if (isStale()) return;
          setQueueItems(getBoundQueueItems(activePairing.deviceId));
          if (flushRes.acknowledgedIds.length > 0) {
            if (draftsRes.status === 'fulfilled') {
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
            if (!isSocialStale()) {
              try {
                const refreshedRuns = await fetchLaptopSocialRuns(activePairing, 30, bridge);
                if (!isSocialStale()) {
                  setSocialRuns(refreshedRuns);
                }
              } catch {
                // Non-fatal if social runs route unavailable.
              }
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

  // On open and sign-in, and whenever the phone gets its connection back.
  useEffect(() => {
    if (!accountInitialized || !sessionUid) return undefined;
    void syncCloud({ force: true });
    const onOnline = () => void syncCloud({ force: true });
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [accountInitialized, sessionUid, syncCloud]);

  // While something waits for the laptop and the app is open, look again every 30 seconds.
  const waitingActionCount = phoneActions.filter((a) => a.state === 'waiting').length;
  useEffect(() => {
    if (!sessionUid || waitingActionCount === 0 || !isForeground) return undefined;
    const timer = setInterval(() => void syncCloud({ mirror: false }), 30_000);
    return () => clearInterval(timer);
  }, [sessionUid, waitingActionCount, isForeground, syncCloud]);

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
      isForegroundRef.current = true;
      setIsForeground(true);
      void checkSharedItems();
      void refreshLaptop(pairingRef.current, sessionRef.current);
      void syncCloud({ force: true });
    };
    const handleBackground = () => {
      isForegroundRef.current = false;
      setIsForeground(false);
      socialGenRef.current += 1;
      setSocialLiveOpen(false);
      setSocialRuns([]);
      setSocialStatus(null);
      setSocialError(null);
    };

    const unsubs: Array<() => void> = [];
    if (bridge.onResume) {
      unsubs.push(bridge.onResume(handleResume));
    }
    if (bridge.onBackground) {
      unsubs.push(bridge.onBackground(handleBackground));
    }
    if (!bridge.onResume || !bridge.onBackground) {
      const onVis = () => {
        if (document.visibilityState === 'hidden') {
          if (!bridge.onBackground) handleBackground();
        } else if (document.visibilityState === 'visible') {
          if (!bridge.onResume) handleResume();
        }
      };
      document.addEventListener('visibilitychange', onVis);
      unsubs.push(() => document.removeEventListener('visibilitychange', onVis));
    }
    return () => {
      for (const fn of unsubs) fn();
    };
  }, [bridge, checkSharedItems, refreshLaptop, syncCloud]);

  useEffect(() => {
    if (!bridge.onBackButton) return undefined;
    return bridge.onBackButton(() => {
      const state = backStateRef.current;
      if (state.confirmOpen) {
        setConfirmDialog(null);
        return;
      }
      if (state.socialLiveOpen) {
        setSocialLiveOpen(false);
        return;
      }
      if (state.shareOpen) {
        setSharedQueue((prev) => prev.slice(1));
        setExtractedProposal(null);
        setShareError(null);
        return;
      }
      if (state.pitchPresentOpen) {
        setPresentingPitch(null);
        setPitchError(null);
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
      socialGenRef.current += 1;
      if (pollTimerRef.current !== null) {
        clearTimeout(pollTimerRef.current);
      }
    };
  }, []);

  const hasActiveSocialRun = Boolean(
    socialLiveOpen ||
      socialStatus?.active_run?.status === 'running' ||
      socialStatus?.active_run?.status === 'waiting_user' ||
      socialRuns.some((r) => r.status === 'running' || r.status === 'waiting_user'),
  );

  const socialStatusRef = useRef(socialStatus);
  socialStatusRef.current = socialStatus;

  useEffect(() => {
    const activePairing = pairing;
    if (!isForeground || !activePairing || !laptopReachable) {
      return undefined;
    }
    // The status is small, so an idle phone can watch it; the runs are fetched only when it changes.
    const intervalMs = hasActiveSocialRun ? 2500 : 10000;
    const activeKey = (st: SocialStatusInfo | null) =>
      st?.active_run ? `${st.active_run.id}:${st.active_run.status}` : '';

    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const gen = appGenRef.current;
    const socGen = socialGenRef.current;
    const stateGen = getStateGeneration();

    const isStale = () =>
      disposed ||
      !isForegroundRef.current ||
      appGenRef.current !== gen ||
      socialGenRef.current !== socGen ||
      getStateGeneration() !== stateGen;

    const pollSocial = async () => {
      if (isStale()) return;
      try {
        const stRes = await fetchLaptopSocialStatus(activePairing, bridge);
        if (isStale()) return;
        if (activeKey(stRes) !== activeKey(socialStatusRef.current) || (hasActiveSocialRun && !stRes?.active_run)) {
          const runsRes = await fetchLaptopSocialRuns(activePairing, 30, bridge);
          if (isStale()) return;
          setSocialRuns(runsRes);
        }
        setSocialStatus(stRes);
      } catch (err) {
        if (isStale()) return;
        if (err instanceof LaptopAuthError) {
          setLaptopAuthError(err.message);
          setLaptopReachable(false);
          return;
        }
      }
      if (!isStale()) {
        timer = setTimeout(() => {
          void pollSocial();
        }, intervalMs);
      }
    };

    timer = setTimeout(() => {
      void pollSocial();
    }, intervalMs);

    return () => {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [isForeground, pairing, laptopReachable, hasActiveSocialRun, bridge]);

  const waitingSocialRun = useMemo(() => {
    const fromList = socialRuns.find((r) => r.status === 'waiting_user');
    if (fromList) return fromList;
    if (socialStatus?.active_run?.status === 'waiting_user') {
      return socialStatus.active_run;
    }
    return null;
  }, [socialRuns, socialStatus]);

  useEffect(() => {
    if (socialLiveOpen && (!waitingSocialRun || waitingSocialRun.status !== 'waiting_user')) {
      setSocialLiveOpen(false);
    }
  }, [socialLiveOpen, waitingSocialRun]);

  const queuedSocialRequests = useMemo(
    () => queueItems.filter((item) => item.kind === 'social_request'),
    [queueItems],
  );

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

  const pricesAsOfLabel = useMemo(() => {
    return formatPricesAsOfLabel(pitchPricesAsOf ?? cacheSnapshot.laptop?.pitchPricesAsOf ?? null);
  }, [pitchPricesAsOf, cacheSnapshot]);

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
    // The cloud copy (with its comments) wins when it is newer than the last live copy from the laptop.
    setSelectedDraft(mirrorIsNewer(cacheSnapshot) && local ? local : cachedDetail ?? local);
  }

  /**
   * Queue an action for the laptop through the cloud (the laptop is away). It shows as waiting at once, goes to the
   * cloud by itself and is applied when the laptop is back. Text is checked against the product rules first.
   */
  async function queueCloudAction(
    input: ActionInput,
    okMessage: string,
    setError: (message: string | null) => void,
  ): Promise<boolean> {
    const uid = sessionRef.current?.user_id;
    if (!uid) {
      const msg = 'Sign in to queue actions while the laptop is away.';
      setError(msg);
      showToast(msg);
      return false;
    }
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () =>
      appGenRef.current !== gen || getStateGeneration() !== stateGen || sessionRef.current?.user_id !== uid;
    let rules = getCachedProductRules(uid);
    if (!rules) {
      try {
        rules = await fetchSharedProductRules(uid, bridge);
      } catch {
        rules = null;
      }
      if (isStale()) return false;
    }
    try {
      // The bundled baseline guards the queue even if the team never published rules; shared rules can only add.
      enqueueAction(input, uid, rulesForQueue(rules));
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not queue this action.';
      setError(msg);
      showToast(msg);
      return false;
    }
    setPhoneActions(loadActions(uid));
    showToast(okMessage);
    void syncCloud({ mirror: false });
    return true;
  }

  async function handleAddComment(
    contentId: string,
    body: string,
    version?: number | null,
  ): Promise<boolean> {
    const activePairing = pairingRef.current;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    setDraftsError(null);
    if (activePairing && laptopReachable) {
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
        if (!isLaptopNetworkError(err)) {
          const msg = err instanceof Error ? err.message : 'Could not add comment';
          setDraftsError(msg);
          showToast(msg);
          return false;
        }
        // The laptop went away: queue it through the cloud instead.
      } finally {
        if (appGenRef.current === gen && getStateGeneration() === stateGen) {
          setBusyAction(null);
        }
      }
    }
    const draft = drafts.find((d) => d.id === contentId) ?? selectedDraft;
    return queueCloudAction(
      {
        kind: 'draft_comment',
        target_id: contentId,
        payload: { body, version: version ?? undefined },
        label: draft?.title ?? null,
        targetConfidential: Boolean(draft?.confidential),
      },
      'Comment saved: waiting for laptop',
      setDraftsError,
    );
  }

  async function handleMarkPosted(contentId: string, url?: string): Promise<boolean> {
    const activePairing = pairingRef.current;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    setDraftsError(null);
    if (activePairing && laptopReachable) {
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
        if (!isLaptopNetworkError(err)) {
          const msg = err instanceof Error ? err.message : 'Could not mark as posted';
          setDraftsError(msg);
          showToast(msg);
          return false;
        }
      } finally {
        if (appGenRef.current === gen && getStateGeneration() === stateGen) {
          setBusyAction(null);
        }
      }
    }
    const draft = drafts.find((d) => d.id === contentId) ?? selectedDraft;
    return queueCloudAction(
      {
        kind: 'draft_posted',
        target_id: contentId,
        payload: url ? { url } : {},
        label: draft?.title ?? null,
        targetConfidential: Boolean(draft?.confidential),
      },
      'Marked as posted: waiting for laptop',
      setDraftsError,
    );
  }

  async function handleMarkReminderDone(id: string): Promise<void> {
    const activePairing = pairingRef.current;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    setTodayError(null);
    if (activePairing && laptopReachable) {
      setBusyAction(`rem-${id}`);
      try {
        await postLaptopReminderDone(activePairing, id, bridge);
        if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
        setReminders((prev) => prev.filter((r) => r.id !== id));
        showToast('Reminder marked done');
        return;
      } catch (err) {
        if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
        handleLaptopActionError(err);
        if (!isLaptopNetworkError(err)) {
          const msg = err instanceof Error ? err.message : 'Could not update reminder.';
          setTodayError(msg);
          showToast(msg);
          return;
        }
      } finally {
        if (appGenRef.current === gen && getStateGeneration() === stateGen) {
          setBusyAction(null);
        }
      }
    }
    const reminder = reminders.find((r) => r.id === id);
    await queueCloudAction(
      {
        kind: 'reminder_done',
        target_id: id,
        payload: {},
        label: reminder?.title ?? null,
        targetConfidential: Boolean(reminder?.confidential),
      },
      'Reminder done: waiting for laptop',
      setTodayError,
    );
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

  /**
   * Get a draft file on the phone and offer to save or share it. The direct laptop path is used when the laptop is
   * reachable and lists the file; otherwise (or if the laptop turns out to be away) the 14-day cloud copy.
   */
  async function handleGetOnPhone(
    contentId: string,
    filePath: string,
    title?: string,
  ): Promise<boolean> {
    const activePairing = pairingRef.current;
    const uid = sessionRef.current?.user_id ?? null;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    const norm = (p: string) => p.replace(/\\/g, '/').replace(/^(\.\/)+/, '');
    const copy = uid
      ? phoneCopies.find((c) => c.content_id === contentId && norm(c.file_path) === norm(filePath)) ?? null
      : null;
    const liveFile = (selectedDraft?.files ?? []).some(
      (f) => norm(typeof f === 'string' ? f : f?.path ?? '') === norm(filePath),
    );
    setDraftsError(null);
    setBusyAction(`share-file:${filePath}`);
    try {
      if (activePairing && laptopReachable && liveFile) {
        try {
          await shareLaptopDraftFile(activePairing, contentId, filePath, title, bridge);
          if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
          showToast('Opened share sheet');
          return true;
        } catch (err) {
          if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
          handleLaptopActionError(err);
          if (!isLaptopNetworkError(err) || !copy || !uid || !isCopyUsable(copy, uid)) throw err;
          // The laptop went away: fall back to the cloud copy.
        }
      }
      if (!copy || !uid) {
        throw new PhoneCopyError('Connect to your laptop to get this file.');
      }
      await getCopyOnPhone(uid, copy, title, bridge);
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      showToast('Opened share sheet');
      return true;
    } catch (err) {
      if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return false;
      const msg = err instanceof Error ? err.message : 'Could not get the file.';
      setDraftsError(msg);
      showToast(msg);
      return false;
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleSharePitchPdf(base64: string, title?: string): Promise<void> {
    setPitchError(null);
    try {
      if (typeof bridge.sharePdf !== 'function') {
        throw new Error('PDF sharing is unavailable on this platform.');
      }
      await bridge.sharePdf({ base64, title });
      showToast('Opened PDF share sheet');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not share quotation PDF.';
      setPitchError(msg);
      showToast(msg);
    }
  }

  async function handleOpenNotification(notif: PhoneNotification): Promise<void> {
    if (notif.confidential || !notif.route) return;
    const route = notif.route;
    const activePairing = pairingRef.current;
    const gen = appGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () => appGenRef.current !== gen || getStateGeneration() !== stateGen;

    if (activePairing && laptopReachable && !notif.read_at) {
      void postLaptopNotificationsRead(activePairing, { ids: [notif.id] }, bridge)
        .then((res) => {
          if (isStale()) return;
          const nowIso = new Date().toISOString();
          setNotifications((prev) =>
            prev.map((n) => (n.id === notif.id ? { ...n, read_at: n.read_at || nowIso } : n)),
          );
          if (typeof res?.unread_count === 'number') {
            setUnreadCount(res.unread_count);
          }
        })
        .catch(() => {});
    }

    if (route.startsWith('#/pitch/')) {
      const rawSeg = route.slice('#/pitch/'.length).split('/')[0]?.split('?')[0] ?? '';
      let targetId = rawSeg;
      try {
        targetId = decodeURIComponent(rawSeg);
      } catch {
        // Keep raw segment if malformed.
      }
      if (!targetId) return;
      let list = pitchProposals;
      if (!list.some((p) => p.id === targetId) && activePairing && laptopReachable) {
        try {
          const fresh = await fetchLaptopPitchProposals(activePairing, bridge);
          if (isStale()) return;
          const syncedAt = Date.now();
          setPitchProposals(fresh);
          setPitchPricesAsOf(syncedAt);
          saveLaptopSnapshot(activePairing.deviceId, {
            pitchProposals: fresh,
            pitchPricesAsOf: syncedAt,
          });
          list = fresh;
        } catch {
          // Use existing list if fetch fails.
        }
      }
      if (isStale()) return;
      const match = list.find((p) => p.id === targetId);
      setTab('pitch');
      if (!match) {
        showToast('Proposal is not available on this phone yet.');
        return;
      }
      if (match.confidential) {
        showToast('Confidential proposal — open Magnm Studio on your laptop.');
        return;
      }
      setPresentingPitch(match);
      return;
    }

    if (route.startsWith('#/review/')) {
      const rawSeg = route.slice('#/review/'.length).split('/')[0]?.split('?')[0] ?? '';
      let contentId = rawSeg;
      try {
        contentId = decodeURIComponent(rawSeg);
      } catch {
        // Keep raw segment if malformed.
      }
      if (!contentId) return;
      setTab('drafts');
      void handleSelectDraft(contentId);
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
        // The agent may have asked for a social run: show its card, with Start, straight away.
        void reloadSocialRuns(activePairing);
        return true;
      }

      // A request that needs one of the agent's laptop tools is queued for the laptop (decided from the agent's tool
      // list and the owner's words only, never from a model reply); everything else is answered here in text.
      if (decideRoute(activeAgentId, text).route === 'laptop') {
        return queueCloudAction(
          {
            kind: 'agent_task',
            target_id: activeAgentId,
            payload: { message: text },
            label: getConfiguredAgent(activeAgentId)?.name ?? null,
          },
          'Queued for your laptop',
          setChatError,
        );
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
      } else {
        setPairingError(SCAN_CANCELLED_MESSAGE);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      setPairingError(/cancel/i.test(msg) ? SCAN_CANCELLED_MESSAGE : msg || 'QR scan failed.');
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

  async function reloadSocialRuns(target: PairingConfig): Promise<void> {
    const gen = appGenRef.current;
    const socGen = socialGenRef.current;
    const stateGen = getStateGeneration();
    try {
      const runs = await fetchLaptopSocialRuns(target, 30, bridge);
      if (
        appGenRef.current !== gen ||
        socialGenRef.current !== socGen ||
        getStateGeneration() !== stateGen ||
        !isForegroundRef.current
      ) {
        return;
      }
      setSocialRuns(runs);
    } catch {
      // The next poll or refresh shows them.
    }
  }

  function mergeSocialRun(incoming: SocialRunRecord) {
    setSocialRuns((prev) => {
      const idx = prev.findIndex((r) => r.id === incoming.id);
      if (idx === -1) return [incoming, ...prev];
      return prev.map((r, i) => (i === idx ? { ...r, ...incoming } : r));
    });
  }

  async function handleCreateSocialRun(payload: {
    platform: SocialPlatform;
    recipe: SocialRecipe;
    argument: string;
  }): Promise<boolean> {
    const activePairing = pairingRef.current;
    if (!activePairing?.deviceId) {
      setSocialError('Pair with your laptop before requesting social research.');
      return false;
    }
    const gen = appGenRef.current;
    const socGen = socialGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () =>
      appGenRef.current !== gen ||
      socialGenRef.current !== socGen ||
      getStateGeneration() !== stateGen ||
      !isForegroundRef.current;

    setSocialError(null);
    setBusyAction('social-create');
    try {
      if (laptopReachable) {
        try {
          const res = await createLaptopSocialRun(activePairing, payload, bridge);
          if (isStale()) return false;
          if (res?.run) {
            mergeSocialRun(res.run);
            showToast('Social run created (tap Start to run)');
            return true;
          }
        } catch (err) {
          if (isStale()) return false;
          if (!isLaptopNetworkError(err)) {
            handleLaptopActionError(err);
            setSocialError(err instanceof Error ? err.message : 'Could not create social run.');
            return false;
          }
          handleLaptopActionError(err);
          // Fall through to queue when laptop went unreachable.
        }
      }

      enqueueInboxItem({
        kind: 'social_request',
        title: `${payload.platform} ${payload.recipe}`,
        body: payload.argument,
        platform: payload.platform,
        recipe: payload.recipe,
        argument: payload.argument,
        ownerUid: sessionRef.current?.user_id || '',
        laptopDeviceId: activePairing.deviceId,
      });
      refreshQueueState(activePairing);
      showToast('Queued social request for laptop sync');
      return true;
    } catch (err) {
      if (!isStale()) {
        setSocialError(err instanceof Error ? err.message : 'Could not queue social request.');
      }
      return false;
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleStartSocialRun(run: SocialRunRecord): Promise<void> {
    const activePairing = pairingRef.current;
    if (!activePairing || !run.approval_hash) return;
    const gen = appGenRef.current;
    const socGen = socialGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () =>
      appGenRef.current !== gen ||
      socialGenRef.current !== socGen ||
      getStateGeneration() !== stateGen ||
      !isForegroundRef.current;

    setSocialError(null);
    setBusyAction(`social-start-${run.id}`);
    try {
      const res = await startLaptopSocialRun(activePairing, run.id, run.approval_hash, bridge);
      if (isStale()) return;
      if (res?.run) mergeSocialRun(res.run);
      showToast('Social run started');
    } catch (err) {
      if (isStale()) return;
      handleLaptopActionError(err);
      setSocialError(err instanceof Error ? err.message : 'Could not start social run.');
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleCancelSocialRun(runId: string): Promise<void> {
    const activePairing = pairingRef.current;
    if (!activePairing) return;
    const gen = appGenRef.current;
    const socGen = socialGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () =>
      appGenRef.current !== gen ||
      socialGenRef.current !== socGen ||
      getStateGeneration() !== stateGen ||
      !isForegroundRef.current;

    setSocialError(null);
    setBusyAction(`social-cancel-${runId}`);
    try {
      const res = await cancelLaptopSocialRun(activePairing, runId, bridge);
      if (isStale()) return;
      if (res?.run) mergeSocialRun(res.run);
      if (waitingSocialRun?.id === runId) {
        setSocialLiveOpen(false);
      }
      showToast('Social run stopped');
    } catch (err) {
      if (isStale()) return;
      handleLaptopActionError(err);
      setSocialError(err instanceof Error ? err.message : 'Could not stop social run.');
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleSummariseSocialRun(runId: string): Promise<void> {
    const activePairing = pairingRef.current;
    if (!activePairing) return;
    const gen = appGenRef.current;
    const socGen = socialGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () =>
      appGenRef.current !== gen ||
      socialGenRef.current !== socGen ||
      getStateGeneration() !== stateGen ||
      !isForegroundRef.current;

    setSocialError(null);
    setBusyAction(`summarise-${runId}`);
    try {
      const res = await summariseLaptopSocialRun(activePairing, runId, bridge);
      if (isStale()) return;
      if (res?.run) mergeSocialRun(res.run);
    } catch (err) {
      if (isStale()) return;
      handleLaptopActionError(err);
      setSocialError(err instanceof Error ? err.message : 'Could not summarise social run.');
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  async function handleSocialChallengeAction(
    action: 'later' | 'skip',
    runId: string,
  ): Promise<void> {
    const activePairing = pairingRef.current;
    if (!activePairing) return;
    const gen = appGenRef.current;
    const socGen = socialGenRef.current;
    const stateGen = getStateGeneration();
    const isStale = () =>
      appGenRef.current !== gen ||
      socialGenRef.current !== socGen ||
      getStateGeneration() !== stateGen ||
      !isForegroundRef.current;

    setSocialError(null);
    setBusyAction(`social-challenge-${action}`);
    try {
      const res = await postLaptopSocialChallenge(activePairing, action, runId, bridge);
      if (isStale()) return;
      if (res?.run) mergeSocialRun(res.run);
      setSocialLiveOpen(false);
    } catch (err) {
      if (isStale()) return;
      handleLaptopActionError(err);
      setSocialError(err instanceof Error ? err.message : 'Could not update security check.');
    } finally {
      if (appGenRef.current === gen && getStateGeneration() === stateGen) {
        setBusyAction(null);
      }
    }
  }

  const socialResearchProps = {
    paired: Boolean(pairing),
    laptopReachable,
    socialStatus,
    socialRuns,
    queuedSocialRequests,
    busyAction,
    errorMessage: socialError,
    onCreateRun: handleCreateSocialRun,
    onStartRun: handleStartSocialRun,
    onCancelRun: handleCancelSocialRun,
    onSummariseRun: handleSummariseSocialRun,
    onChallengeAction: handleSocialChallengeAction,
    onSolveNow: (run: SocialRunRecord) => {
      mergeSocialRun(run);
      setSocialLiveOpen(true);
    },
  };

  async function handleGoogleSignIn() {
    setAuthError(null);
    setBusyAction('signin');
    try {
      await startGoogleSignIn(bridge);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : 'Could not open Google sign-in.');
    } finally {
      setBusyAction(null);
    }
  }

  // Signing in is required before the app is used. Pairing and everything else follow after sign-in.
  if (accountInitialized && !session) {
    return (
      <div className="mg-root mob-shell">
        {bridge.isPreview && bridge.previewLabel ? (
          <div className="mob-preview-banner" role="status">
            {bridge.previewLabel}
          </div>
        ) : null}
        <WelcomeGate error={authError} busy={busyAction === 'signin'} onSignIn={() => void handleGoogleSignIn()} />
      </div>
    );
  }

  const isPresentingPitch = Boolean(presentingPitch);

  return (
    <div className="mg-root mob-shell">
      {!isPresentingPitch && bridge.isPreview && bridge.previewLabel ? (
        <div className="mob-preview-banner" role="status">
          {bridge.previewLabel}
        </div>
      ) : null}

      {!isPresentingPitch ? (
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
            {waitingActionCount > 0 ? (
              <span className="mg-chip">{waitingActionCount} waiting for laptop</span>
            ) : null}
          </div>
        </header>
      ) : null}

      {!isPresentingPitch ? (
        <nav className="mob-tabs-wrap" aria-label="Mobile sections">
          <NumberedTabs
            items={MOBILE_TABS}
            current={tab}
            onSelect={(next) => setTab(next as MobileTabId)}
            orientation="horizontal"
            ariaLabel="Mobile sections"
          />
        </nav>
      ) : null}

      <main className={isPresentingPitch ? 'mob-main mob-main--present' : 'mob-main'}>
        {isPresentingPitch ? (
          <PitchTab
            proposals={pitchProposals}
            presentingProposal={presentingPitch}
            laptopReachable={laptopReachable}
            pricesAsOfLabel={pricesAsOfLabel}
            errorMessage={pitchError}
            onSelectProposal={(prop) => {
              if (!prop.confidential) {
                setPitchError(null);
                setPresentingPitch(prop);
              }
            }}
            onExitPresent={() => {
              setPresentingPitch(null);
              setPitchError(null);
            }}
            onSharePdf={handleSharePitchPdf}
          />
        ) : null}

        {!isPresentingPitch && tab === 'today' ? (
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
            onMarkReminderDone={(id) => void handleMarkReminderDone(id)}
            phoneActions={phoneActions}
            canQueue={Boolean(session)}
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
            onOpenNotification={(notif) => void handleOpenNotification(notif)}
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
            socialResearch={socialResearchProps}
          />
        ) : null}

        {!isPresentingPitch && tab === 'drafts' ? (
          <DraftsTab
            drafts={drafts}
            selectedDraft={selectedDraft}
            laptopReachable={laptopReachable}
            savedCopyLabel={savedCopyLabel}
            canQueueOffline={Boolean(pairing)}
            phoneActions={phoneActions}
            canQueue={Boolean(session)}
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
            onGetOnPhone={handleGetOnPhone}
            phoneCopies={phoneCopies}
            ownerUid={session?.user_id ?? null}
            onCreateOfflineItem={handleSaveInboxOrQueue}
          />
        ) : null}

        {!isPresentingPitch && tab === 'pitch' ? (
          <PitchTab
            proposals={pitchProposals}
            presentingProposal={null}
            laptopReachable={laptopReachable}
            pricesAsOfLabel={pricesAsOfLabel}
            errorMessage={pitchError}
            onSelectProposal={(prop) => {
              if (!prop.confidential) {
                setPitchError(null);
                setPresentingPitch(prop);
              }
            }}
            onExitPresent={() => {
              setPresentingPitch(null);
              setPitchError(null);
            }}
            onSharePdf={handleSharePitchPdf}
          />
        ) : null}

        {!isPresentingPitch && tab === 'chat' ? (
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
            phoneActions={phoneActions}
            socialResearch={socialResearchProps}
          />
        ) : null}

        {!isPresentingPitch && tab === 'more' ? (
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
            phoneActions={phoneActions}
            onRemovePhoneAction={(id) => {
              const uid = sessionRef.current?.user_id;
              if (!uid) return;
              removeAction(id, uid);
              setPhoneActions(loadActions(uid));
            }}
            laptopReachable={laptopReachable}
            mirrorRecordCount={cacheSnapshot.mirrorRecords.length}
            savedCopyLabel={savedCopyLabel}
            busyAction={busyAction}
            onScanQr={handleScanQr}
            onParsePairCode={handleParsePairCode}
            onConfirmPairing={handleConfirmPairing}
            onCancelPairing={handleCancelPairing}
            onUnpair={requestUnpair}
            onGoogleSignIn={handleGoogleSignIn}
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
                await syncPhoneActions(uid, bridge);
                if (appGenRef.current !== gen || getStateGeneration() !== stateGen) return;
                setPhoneActions(loadActions(uid));
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

      <SocialLiveView
        open={socialLiveOpen && Boolean(waitingSocialRun && waitingSocialRun.status === 'waiting_user')}
        foreground={isForeground}
        pairing={pairing}
        run={waitingSocialRun}
        bridge={bridge}
        sessionGen={socialGenRef.current}
        getSessionGen={() => socialGenRef.current}
        onClose={() => setSocialLiveOpen(false)}
        onCleared={(clearedRun) => {
          if (clearedRun) mergeSocialRun(clearedRun);
          setSocialLiveOpen(false);
          if (pairingRef.current && isForegroundRef.current) {
            void reloadSocialRuns(pairingRef.current);
          }
        }}
        onAuthError={handleLaptopActionError}
      />
    </div>
  );
}
