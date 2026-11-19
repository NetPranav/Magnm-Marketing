import { useCallback, useEffect, useRef } from 'react';

export interface PitchPresentProps {
  proposalId: string;
  onExit: () => void;
}

/** Full-screen offline Pitch present view (#/pitch/<id>/present) with Studio shell hidden. */
export function PitchPresent({ proposalId, onExit }: PitchPresentProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  const handleExit = useCallback(() => {
    try {
      if (document.fullscreenElement && typeof document.exitFullscreen === 'function') {
        void document.exitFullscreen().catch(() => {});
      }
    } catch {
      // Ignore fullscreen exit errors in restricted environments.
    }
    onExit();
  }, [onExit]);

  useEffect(() => {
    const el = containerRef.current;
    if (el && typeof el.requestFullscreen === 'function') {
      try {
        const res = el.requestFullscreen();
        if (res && typeof res.catch === 'function') {
          res.catch(() => {});
        }
      } catch {
        // Fullscreen may be disallowed without user gesture; continue seamlessly.
      }
    }
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        handleExit();
      }
    };
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const msg = event.data as { type?: string } | null;
      if (msg?.type === 'magnm:pitch-exit-present') {
        handleExit();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('message', onMessage);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('message', onMessage);
    };
  }, [handleExit]);

  const handleIframeLoad = () => {
    const frame = iframeRef.current;
    if (!frame) return;
    try {
      frame.focus();
      frame.contentWindow?.focus?.();
      const doc = frame.contentDocument;
      if (doc) {
        doc.addEventListener('keydown', (ev) => {
          if (ev.key === 'Escape') {
            ev.preventDefault();
            handleExit();
          }
        });
      }
    } catch {
      // Ignore cross-origin or test-stub access failures.
    }
  };

  return (
    <div
      ref={containerRef}
      className="mg-root studio-present-shell"
      data-testid="pitch-present-screen"
    >
      <iframe
        ref={iframeRef}
        title={`Present proposal ${proposalId}`}
        src={`/pitch/?proposal=${encodeURIComponent(proposalId)}&mode=present`}
        className="studio-present-iframe"
        onLoad={handleIframeLoad}
      />
      <button
        type="button"
        className="studio-present-exit"
        onClick={handleExit}
      >
        Exit
      </button>
    </div>
  );
}
