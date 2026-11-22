import { useCallback, useEffect, useRef, useState } from 'react';
import type { StatusState } from '@magnm/design/react';
import { ApiError, api, onUnauthorized } from './api';
import { getAccount, type AccountState } from './api/account';
import {
  listNotifications,
  markNotificationsRead,
  type NotificationRecord,
} from './api/notifications';
import { getSocialRun, getSocialStatus } from './api/social';
import { NotificationBell } from './components/NotificationBell';
import { SocialChallengeBanner } from './components/SocialChallengeBanner';
import { SocialLiveModal } from './components/SocialLiveModal';
import { StudioShell } from './components/StudioShell';
import { listenDesktopNotificationClick } from './desktop';
import { EventsProvider, useFallbackPoll, useRootEventBus } from './events';
import { showSystemNotification } from './notifications';
import {
  ADMIN_SECTION,
  SECTIONS,
  getVisibleSections,
  parseHash,
  parsePitchParam,
  useRoute,
  type SectionId,
} from './router';
import { PlaceholderScreen } from './screens/PlaceholderScreen';
import { SessionEndedScreen } from './screens/SessionEndedScreen';
import { AdminSection } from './sections/admin/AdminSection';
import { CreateSection } from './sections/create/CreateSection';
import { LibrarySection } from './sections/library/LibrarySection';
import { PitchPresent } from './sections/pitch/PitchPresent';
import { PitchSection } from './sections/pitch/PitchSection';
import { PlanSection } from './sections/plan/PlanSection';
import { ReposSection } from './sections/repos/ReposSection';
import { ResearchSection } from './sections/research/ResearchSection';
import { ReviewSection } from './sections/review/ReviewSection';
import { SettingsSection } from './sections/settings/SettingsSection';
import { TeamSection } from './sections/team/TeamSection';
import { TodaySection } from './sections/today/TodaySection';
import type { EngineStatusInfo, HealthInfo, JobRecord, SocialRunRecord } from './types';

function mergeJobUpdate(prev: JobRecord[], incoming: JobRecord): JobRecord[] {
  const index = prev.findIndex((item) => item.id === incoming.id);
  const next =
    index === -1
      ? [incoming, ...prev]
      : prev.map((item, i) => (i === index ? { ...item, ...incoming } : item));
  return next.slice(0, 10);
}

function isJobRecord(value: unknown): value is JobRecord {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<JobRecord>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.name === 'string' &&
    typeof candidate.status === 'string'
  );
}

function isNotificationRecord(value: unknown): value is NotificationRecord {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<NotificationRecord>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.kind === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.route === 'string'
  );
}

/** Root Studio application shell, routing, API data loading, and live SSE wiring. */
export function App() {
  const { section, param, navigate } = useRoute();
  const [sessionEnded, setSessionEnded] = useState(false);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [status, setStatus] = useState<EngineStatusInfo | null>(null);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [account, setAccount] = useState<AccountState | null>(null);
  const [accountLoaded, setAccountLoaded] = useState(false);
  const [notifications, setNotifications] = useState<NotificationRecord[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [waitingSocialRun, setWaitingSocialRun] = useState<SocialRunRecord | null>(null);
  const [socialLiveOpen, setSocialLiveOpen] = useState(false);
  const waitingSocialRunRef = useRef<SocialRunRecord | null>(null);
  waitingSocialRunRef.current = waitingSocialRun;
  const prevEventsStateRef = useRef<string | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notifMutationSeqRef = useRef(0);

  const navigateToRoute = useCallback(
    (routeStr: string) => {
      const parsed = parseHash(routeStr);
      navigate(parsed.section, parsed.param);
    },
    [navigate],
  );

  const loadNotifications = useCallback(async () => {
    const seq = notifMutationSeqRef.current;
    try {
      const res = await listNotifications(50);
      if (res && Array.isArray(res.notifications)) {
        if (seq === notifMutationSeqRef.current) {
          setNotifications(res.notifications);
          setUnreadCount(Number(res.unread_count ?? 0));
        } else {
          setNotifications((prev) => {
            const seen = new Set(res.notifications.map((n) => n.id));
            const extra = prev.filter((n) => !seen.has(n.id));
            return [...extra, ...res.notifications].slice(0, 200);
          });
        }
      }
    } catch {
      // Non-fatal if notifications endpoint is unavailable in isolated tests.
    }
  }, []);

  const loadSocialChallenge = useCallback(async () => {
    try {
      const st = await getSocialStatus();
      const active = st?.active_run;
      if (active && active.status === 'waiting_user') {
        setWaitingSocialRun(active);
      } else {
        setWaitingSocialRun(null);
        setSocialLiveOpen(false);
      }
    } catch {
      // Non-fatal if social status endpoint is unavailable in isolated tests.
    }
  }, []);

  const showToast = useCallback((message: string) => {
    setToast(message);
    if (toastTimerRef.current !== null) {
      clearTimeout(toastTimerRef.current);
    }
    toastTimerRef.current = setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, 4000);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, []);

  const loadAccountState = useCallback(async () => {
    try {
      const acct = await getAccount();
      setAccount(acct);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        return;
      }
      setAccount(null);
    } finally {
      setAccountLoaded(true);
    }
  }, []);

  const loadSummary = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      api.get<HealthInfo>('/api/health'),
      api.get<EngineStatusInfo>('/api/status'),
      api.get<JobRecord[]>('/api/jobs?limit=10'),
      getAccount(),
    ]);

    let firstError: string | null = null;
    const [healthRes, statusRes, jobsRes, accountRes] = results;

    if (healthRes.status === 'fulfilled') {
      setHealth(healthRes.value);
    } else if (healthRes.reason instanceof ApiError && healthRes.reason.status === 401) {
      return;
    } else {
      firstError = firstError ?? 'Could not reach the local engine.';
    }

    if (statusRes.status === 'fulfilled') {
      setStatus(statusRes.value);
    } else if (statusRes.reason instanceof ApiError && statusRes.reason.status === 401) {
      return;
    } else if (statusRes.reason instanceof Error) {
      firstError = firstError ?? statusRes.reason.message;
    }

    if (jobsRes.status === 'fulfilled') {
      setJobs(Array.isArray(jobsRes.value) ? jobsRes.value : []);
    } else if (jobsRes.reason instanceof ApiError && jobsRes.reason.status === 401) {
      return;
    } else if (jobsRes.reason instanceof Error) {
      firstError = firstError ?? jobsRes.reason.message;
    }

    if (accountRes.status === 'fulfilled') {
      setAccount(accountRes.value);
    } else if (accountRes.reason instanceof ApiError && accountRes.reason.status === 401) {
      return;
    } else {
      setAccount(null);
    }
    setAccountLoaded(true);

    setError(firstError);
    setLoading(false);
  }, []);

  useEffect(() => {
    const unsubscribe = onUnauthorized(() => {
      setSessionEnded(true);
    });
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!sessionEnded) {
      void loadSummary();
      void loadNotifications();
      void loadSocialChallenge();
    }
  }, [sessionEnded, loadSummary, loadNotifications, loadSocialChallenge]);

  useEffect(() => {
    return listenDesktopNotificationClick((routeStr) => {
      navigateToRoute(routeStr);
    });
  }, [navigateToRoute]);

  useEffect(() => {
    const onAccountChanged = () => {
      if (!sessionEnded) {
        void loadAccountState();
      }
    };
    window.addEventListener('magnm:account-changed', onAccountChanged);
    return () => {
      window.removeEventListener('magnm:account-changed', onAccountChanged);
    };
  }, [sessionEnded, loadAccountState]);

  const handleEvent = useCallback(
    (eventName: string, data: unknown) => {
      if (eventName === 'job' && isJobRecord(data)) {
        setJobs((prev) => mergeJobUpdate(prev, data));
      } else if (eventName === 'team_knowledge.updated' && data && typeof data === 'object') {
        const payload = data as { label?: string; key?: string; updated_by?: string };
        const label = payload.label || payload.key || 'Team knowledge';
        const by = payload.updated_by ? ` by ${payload.updated_by}` : '';
        showToast(`${label} updated${by}`);
      } else if (eventName === 'notification' && isNotificationRecord(data)) {
        notifMutationSeqRef.current += 1;
        let addedUnread = false;
        setNotifications((prev) => {
          if (prev.some((item) => item.id === data.id)) {
            return prev;
          }
          if (!data.read_at) {
            addedUnread = true;
          }
          return [data, ...prev].slice(0, 200);
        });
        if (addedUnread || !data.read_at) {
          setUnreadCount((prev) => prev + 1);
        }
        void showSystemNotification(data, navigateToRoute);
        if (data.kind === 'social_challenge') {
          void loadSocialChallenge();
        }
      } else if (eventName === 'social_run.status' && data && typeof data === 'object') {
        const ev = data as Partial<SocialRunRecord>;
        if (!ev.id) return;
        if (ev.status === 'waiting_user') {
          void getSocialRun(ev.id)
            .then((full) => {
              if (full.status === 'waiting_user') {
                setWaitingSocialRun(full);
              }
            })
            .catch(() => {
              void loadSocialChallenge();
            });
        } else if (waitingSocialRunRef.current?.id === ev.id) {
          setWaitingSocialRun(null);
          setSocialLiveOpen(false);
        }
      } else if (eventName === 'social_challenge_cleared') {
        setWaitingSocialRun(null);
        setSocialLiveOpen(false);
      }
    },
    [navigateToRoute, showToast, loadSocialChallenge],
  );

  const eventBus = useRootEventBus(handleEvent);
  const eventsState = eventBus.state;

  useFallbackPoll(
    loadSocialChallenge,
    Boolean(waitingSocialRun || socialLiveOpen) && !sessionEnded,
    eventsState,
    5000,
  );

  useEffect(() => {
    const prev = prevEventsStateRef.current;
    prevEventsStateRef.current = eventsState;
    if (prev !== null && prev !== 'open' && eventsState === 'open' && !sessionEnded) {
      void loadSummary();
      void loadNotifications();
      void loadSocialChallenge();
    }
  }, [eventsState, sessionEnded, loadSummary, loadNotifications, loadSocialChallenge]);

  const handleSelectNotification = useCallback(
    (item: NotificationRecord) => {
      if (!item.read_at) {
        notifMutationSeqRef.current += 1;
        const nowIso = new Date().toISOString();
        setNotifications((prev) =>
          prev.map((entry) => (entry.id === item.id ? { ...entry, read_at: nowIso } : entry)),
        );
        setUnreadCount((prev) => Math.max(0, prev - 1));
        void markNotificationsRead({ ids: [item.id] })
          .then((res) => {
            if (typeof res?.unread_count === 'number') {
              setUnreadCount(res.unread_count);
            }
          })
          .catch(() => {});
      }
      navigateToRoute(item.route);
    },
    [navigateToRoute],
  );

  const handleMarkAllRead = useCallback(() => {
    notifMutationSeqRef.current += 1;
    const nowIso = new Date().toISOString();
    setNotifications((prev) => prev.map((entry) => (entry.read_at ? entry : { ...entry, read_at: nowIso })));
    setUnreadCount(0);
    void markNotificationsRead({ all: true })
      .then((res) => {
        if (typeof res?.unread_count === 'number') {
          setUnreadCount(res.unread_count);
        }
      })
      .catch(() => {});
  }, []);

  if (sessionEnded) {
    return <SessionEndedScreen />;
  }

  const isAdmin = Boolean(
    account?.configured &&
      account?.signed_in &&
      !account?.deactivated &&
      account?.user?.role === 'admin' &&
      account?.user?.active,
  );
  const visibleSections = getVisibleSections(isAdmin);
  const allSpecs = [...SECTIONS, ADMIN_SECTION];
  const sectionIndex = Math.max(
    0,
    allSpecs.findIndex((item) => item.id === section),
  );
  const currentSpec = allSpecs[sectionIndex] ?? SECTIONS[0];
  const anyJobRunning =
    jobs.some((job) => job.status === 'running') || Boolean(status?.current_job?.running);

  let engineState: StatusState = 'off';
  let engineLabel = 'Disconnected';
  if (eventsState === 'open') {
    engineState = anyJobRunning ? 'working' : 'online';
    engineLabel = anyJobRunning ? 'Working' : 'Online';
  } else if (eventsState === 'connecting' && health?.ok) {
    engineState = 'idle';
    engineLabel = 'Connecting';
  }

  const tabItems = visibleSections.map((item) => ({ id: item.id, label: item.label }));

  const renderSection = () => {
    switch (currentSpec.id) {
      case 'today':
        return (
          <TodaySection
            health={health}
            status={status}
            jobs={jobs}
            loading={loading}
            error={error}
            focusParam={param}
            onRefreshSummary={() => void loadSummary()}
          />
        );
      case 'team':
        return <TeamSection />;
      case 'research':
        return <ResearchSection />;
      case 'plan':
        return <PlanSection />;
      case 'create':
        return <CreateSection />;
      case 'review':
        return (
          <ReviewSection
            contentId={param}
            onNavigate={(nextId) => navigate('review', nextId ?? null)}
          />
        );
      case 'pitch':
        return (
          <PitchSection
            param={param}
            onNavigate={(nextParam) => navigate('pitch', nextParam ?? null)}
          />
        );
      case 'library':
        return <LibrarySection />;
      case 'repos':
        return <ReposSection />;
      case 'settings':
        return <SettingsSection />;
      case 'admin':
        if (!accountLoaded) {
          return <div className="studio-hint">Checking access...</div>;
        }
        if (!isAdmin) {
          return (
            <div className="mg-notice" role="alert">
              Admins only.
            </div>
          );
        }
        return (
          <AdminSection
            param={param}
            onNavigate={(nextParam) => navigate('admin', nextParam ?? null)}
            onOpenSettings={() => navigate('settings')}
          />
        );
      default:
        return (
          <PlaceholderScreen
            page={sectionIndex + 1}
            total={SECTIONS.length}
            title={currentSpec.label}
            description={currentSpec.coming}
          />
        );
    }
  };

  const pitchRoute = currentSpec.id === 'pitch' ? parsePitchParam(param) : { proposalId: null, present: false };
  if (currentSpec.id === 'pitch' && pitchRoute.present && pitchRoute.proposalId) {
    const presentProposalId = pitchRoute.proposalId;
    return (
      <EventsProvider bus={eventBus}>
        <PitchPresent
          proposalId={presentProposalId}
          onExit={() => navigate('pitch', presentProposalId)}
        />
      </EventsProvider>
    );
  }

  return (
    <EventsProvider bus={eventBus}>
      <StudioShell
        items={tabItems}
        current={currentSpec.id}
        onSelect={(nextId: SectionId) => navigate(nextId)}
        layout={currentSpec.layout}
        engineState={engineState}
        engineLabel={engineLabel}
        notificationBell={
          <NotificationBell
            notifications={notifications}
            unreadCount={unreadCount}
            onSelectNotification={handleSelectNotification}
            onMarkAllRead={handleMarkAllRead}
          />
        }
      >
        {waitingSocialRun && waitingSocialRun.status === 'waiting_user' ? (
          <div className="studio-social-alert-wrap">
            <SocialChallengeBanner
              run={waitingSocialRun}
              onSolveNow={() => setSocialLiveOpen(true)}
              onResolved={() => {
                setWaitingSocialRun(null);
                setSocialLiveOpen(false);
              }}
            />
          </div>
        ) : null}
        {renderSection()}
      </StudioShell>
      <SocialLiveModal
        open={socialLiveOpen && Boolean(waitingSocialRun && waitingSocialRun.status === 'waiting_user')}
        run={waitingSocialRun}
        onClose={() => setSocialLiveOpen(false)}
        onCheckStatus={() => void loadSocialChallenge()}
        onCleared={() => {
          setWaitingSocialRun(null);
          setSocialLiveOpen(false);
        }}
      />
      {toast ? (
        <div className="studio-toast" role="status" aria-live="polite">
          {toast}
        </div>
      ) : null}
    </EventsProvider>
  );
}
