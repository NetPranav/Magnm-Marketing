import { useMemo, useState } from 'react';
import type { ActionInput } from '../actions';
import { getCachedProductRules, type ContextCacheState } from '../cache';
import type { NativeBridge } from '../native';
import { rulesForQueue } from '../queueRules';
import type { PairingConfig, PhoneAction } from '../types';
import { BoardTab } from './BoardTab';
import { CreateTab } from './CreateTab';
import { LibraryTab } from './LibraryTab';
import { PlanTab } from './PlanTab';

export interface WorkTabProps {
  ownerUid: string | null;
  signedIn: boolean;
  laptopReachable: boolean;
  pairing: PairingConfig | null;
  /** The saved cloud copy (this account's mirror records). */
  cache: ContextCacheState;
  /** Actions queued on this phone for the laptop (this account's only). */
  phoneActions: PhoneAction[];
  bridge: NativeBridge;
  savedCopyLabel: string | null;
  /** Queue one action for the laptop (checks, saves and sends it). Returns true if it was queued. */
  onQueueAction: (input: ActionInput, okMessage: string) => Promise<boolean>;
  /** Something was queued here directly (an upload): refresh the list and send it. */
  onActionsChanged: () => void;
}

type Section = 'library' | 'plan' | 'create' | 'board';
const SECTIONS: ReadonlyArray<{ id: Section; label: string }> = [
  { id: 'library', label: 'Library' },
  { id: 'plan', label: 'Plan' },
  { id: 'create', label: 'Create' },
  { id: 'board', label: 'Board' },
];

/** Library, Plan, Create and Board on the phone. What needs the laptop is queued and says so; the rest works without it. */
export function WorkTab(props: WorkTabProps) {
  const [section, setSection] = useState<Section>('library');
  // The bundled baseline plus the team's rules: shared rules can only ADD confidential products to it.
  const rules = useMemo(
    () => rulesForQueue(props.ownerUid ? getCachedProductRules(props.ownerUid) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.ownerUid, props.cache],
  );
  return (
    <section aria-label="Work" className="mob-stack">
      <div role="tablist" aria-label="Work sections" className="mob-subtabs">
        {SECTIONS.map((s) => (
          <button key={s.id} type="button" role="tab" aria-selected={section === s.id} className="mob-subtab" onClick={() => setSection(s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      {!props.signedIn ? (
        <div className="mg-notice">Sign in (More tab) to add files, change schedules or create work while the laptop is away.</div>
      ) : null}
      {section === 'library' ? <LibraryTab {...props} rules={rules} /> : null}
      {section === 'plan' ? <PlanTab {...props} /> : null}
      {section === 'create' ? <CreateTab {...props} rules={rules} /> : null}
      {section === 'board' ? <BoardTab signedIn={props.signedIn} cache={props.cache} phoneActions={props.phoneActions} onQueueAction={props.onQueueAction} /> : null}
    </section>
  );
}
