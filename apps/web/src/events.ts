import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

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
  'review.updated',
  'repo_synced',
  'research_digest',
  'browser_frame',
] as const;

export type StudioEventHandler = (eventName: string, data: unknown) => void;

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 15000;

/** Subscribe to /api/events SSE stream with exponential-backoff reconnect and ping ignored. */
export function useEvents(handler: StudioEventHandler, enabled = true): EventsState {
  const [state, setState] = useState<EventsState>(() =>
    !enabled || typeof EventSource === 'undefined' ? 'closed' : 'connecting',
  );
  const handlerRef = useRef<StudioEventHandler>(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') {
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
  }, [enabled]);

  return state;
}

export interface EventBus {
  state: EventsState;
  subscribe: (handler: StudioEventHandler) => () => void;
}

const EventsContext = createContext<EventBus | null>(null);

/** Provide a shared SSE event bus so sections reuse the app's single EventSource connection. */
export function EventsProvider({
  bus,
  children,
}: {
  bus: EventBus;
  children: ReactNode;
}) {
  return createElement(EventsContext.Provider, { value: bus }, children);
}

/** Create an EventBus backed by one useEvents() subscription in the root App component. */
export function useRootEventBus(onEvent?: StudioEventHandler): EventBus {
  const listenersRef = useRef(new Set<StudioEventHandler>());
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const state = useEvents((eventName, data) => {
    onEventRef.current?.(eventName, data);
    for (const listener of listenersRef.current) {
      listener(eventName, data);
    }
  });

  return useMemo<EventBus>(
    () => ({
      state,
      subscribe(handler: StudioEventHandler) {
        listenersRef.current.add(handler);
        return () => {
          listenersRef.current.delete(handler);
        };
      },
    }),
    [state],
  );
}

/** Subscribe to live engine SSE events via shared EventsProvider, or standalone useEvents in tests. */
export function useStudioEvents(handler: StudioEventHandler): EventsState {
  const ctx = useContext(EventsContext);
  const handlerRef = useRef<StudioEventHandler>(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!ctx) return undefined;
    return ctx.subscribe((name, data) => {
      handlerRef.current(name, data);
    });
  }, [ctx]);

  const fallbackState = useEvents(
    (name, data) => {
      if (!ctx) {
        handlerRef.current(name, data);
      }
    },
    !ctx,
  );

  return ctx ? ctx.state : fallbackState;
}

/** Poll callback at most every 5 s while visible and SSE is not open. */
export function useFallbackPoll(
  callback: () => void,
  active: boolean,
  eventsState: EventsState,
  intervalMs = 5000,
): void {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (!active || eventsState === 'open') {
      return undefined;
    }

    const waitMs = Math.max(5000, intervalMs);
    let timer: ReturnType<typeof setInterval> | null = null;

    const startTimer = () => {
      if (timer !== null) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      timer = setInterval(() => {
        if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
          callbackRef.current();
        }
      }, waitMs);
    };

    const stopTimer = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };

    const onVisibilityChange = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        stopTimer();
      } else {
        callbackRef.current();
        startTimer();
      }
    };

    startTimer();
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange);
    }

    return () => {
      stopTimer();
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibilityChange);
      }
    };
  }, [active, eventsState, intervalMs]);
}
