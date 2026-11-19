import { useCallback, useEffect, useRef, useState } from 'react';
import type { StatusState } from '@magnm/design/react';
import { ApiError, api, onUnauthorized } from './api';
import { StudioShell } from './components/StudioShell';
import { useEvents } from './events';
import { SECTIONS, useRoute, type SectionId } from './router';
import { PlaceholderScreen } from './screens/PlaceholderScreen';
import { SessionEndedScreen } from './screens/SessionEndedScreen';
import { TodayScreen } from './screens/TodayScreen';
import type { EngineStatusInfo, HealthInfo, JobRecord } from './types';

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
  return typeof candidate.id === 'string' && typeof candidate.name === 'string' && typeof candidate.status === 'string';
}

/** Root Studio application shell, routing, API data loading, and live SSE wiring. */
export function App() {
  const { section, navigate } = useRoute();
  const [sessionEnded, setSessionEnded] = useState(false);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [status, setStatus] = useState<EngineStatusInfo | null>(null);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const prevEventsStateRef = useRef<string | null>(null);

  const loadSummary = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      api.get<HealthInfo>('/api/health'),
      api.get<EngineStatusInfo>('/api/status'),
      api.get<JobRecord[]>('/api/jobs?limit=10'),
    ]);

    let firstError: string | null = null;
    const [healthRes, statusRes, jobsRes] = results;

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
    }
  }, [sessionEnded, loadSummary]);

  const handleEvent = useCallback((eventName: string, data: unknown) => {
    if (eventName === 'job' && isJobRecord(data)) {
      setJobs((prev) => mergeJobUpdate(prev, data));
    }
  }, []);

  const eventsState = useEvents(handleEvent);

  useEffect(() => {
    const prev = prevEventsStateRef.current;
    prevEventsStateRef.current = eventsState;
    if (prev !== null && prev !== 'open' && eventsState === 'open' && !sessionEnded) {
      void loadSummary();
    }
  }, [eventsState, sessionEnded, loadSummary]);

  if (sessionEnded) {
    return <SessionEndedScreen />;
  }

  const sectionIndex = Math.max(
    0,
    SECTIONS.findIndex((item) => item.id === section),
  );
  const currentSpec = SECTIONS[sectionIndex] ?? SECTIONS[0];
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

  const tabItems = SECTIONS.map((item) => ({ id: item.id, label: item.label }));

  return (
    <StudioShell
      items={tabItems}
      current={currentSpec.id}
      onSelect={(nextId: SectionId) => navigate(nextId)}
      layout={currentSpec.layout}
      engineState={engineState}
      engineLabel={engineLabel}
    >
      {currentSpec.id === 'today' ? (
        <TodayScreen
          health={health}
          status={status}
          jobs={jobs}
          loading={loading}
          error={error}
        />
      ) : (
        <PlaceholderScreen
          page={sectionIndex + 1}
          total={SECTIONS.length}
          title={currentSpec.label}
          description={currentSpec.coming}
        />
      )}
    </StudioShell>
  );
}
