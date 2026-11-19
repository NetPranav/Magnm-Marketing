import { SheetHeader } from '@magnm/design/react';

/** Screen shown when the engine rejects requests with HTTP 401. */
export function SessionEndedScreen() {
  return (
    <div className="mg-root studio-mobile-shell">
      <main className="studio-main">
        <div className="mg-wrap">
          <SheetHeader
            subtitle="Magnm Studio"
            title="Session ended"
            lead="Your session with the local engine has ended. Reopen Magnm Studio to continue."
          />
        </div>
      </main>
    </div>
  );
}
