import { useEffect, useRef, useState } from 'react';

export type EventsState = 'connecting' | 'open' | 'closed';

/** Event types published by engine/server.py _serve_sse (excluding keepalive ping). */
export const STUDIO_EVENT_NAMES = [
  'job',
  'message',
  'workflow_step',
  'workflow_waiting',
  'workflow_done',
  'workflow_failed',
  'agent_step',
  'agent_waiting',
  'agent_done',
  'agent_failed',
  'agent_cancelled',
  'content_status',
  'repo_synced',
  'research_digest',
  'browser_frame',
] as const;

export type StudioEventHandler = (eventName: string, data: unknown) => void;

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 15000;

/** Subscribe to /api/events SSE stream with exponential-backoff reconnect and ping ignored. */
export function useEvents(handler: StudioEventHandler): EventsState {
  const [state, setState] = useState<EventsState>(() =>
    typeof EventSource === 'undefined' ? 'closed' : 'connecting',
  );
  const handlerRef = useRef<StudioEventHandler>(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (typeof EventSource === 'undefined') {
      setState('closed');
      return undefined;
    }

    let disposed = false;
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let backoffMs = INITIAL_BACKOFF_MS;

    const cleanupSource = () => {
      if (source) {
        source.close();
        source = null;
      }
    };

    const scheduleReconnect = () => {
      if (disposed || retryTimer !== null) return;
      const delay = backoffMs;
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
      retryTimer = setTimeout(() => {
        retryTimer = null;
        if (!disposed) {
          connect();
        }
      }, delay);
    };

    const connect = () => {
      cleanupSource();
      setState('connecting');
      const es = new EventSource('/api/events');
      source = es;

      es.onopen = () => {
        if (disposed) return;
        backoffMs = INITIAL_BACKOFF_MS;
        setState('open');
      };

      for (const eventName of STUDIO_EVENT_NAMES) {
        es.addEventListener(eventName, (event) => {
          if (disposed) return;
          const raw = (event as MessageEvent<string>).data;
          let parsed: unknown = raw;
          if (typeof raw === 'string' && raw.length > 0) {
            try {
              parsed = JSON.parse(raw);
            } catch {
              parsed = raw;
            }
          }
          handlerRef.current(eventName, parsed);
        });
      }

      es.onerror = () => {
        if (disposed) return;
        cleanupSource();
        setState('closed');
        scheduleReconnect();
      };
    };

    connect();

    return () => {
      disposed = true;
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      cleanupSource();
    };
  }, []);

  return state;
}
