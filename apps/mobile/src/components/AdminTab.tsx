import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Card, Field, Label, PillButton, Select, TextInput } from '@magnm/design/react';
import {
  AdminError,
  changeMemberActive,
  changeMemberRole,
  fetchActivity,
  fetchAdminTeam,
  fetchAiOverview,
  fetchAudit,
  fetchMemberPitch,
  fetchMemberPreviews,
  fetchMemberRecords,
  fetchRecordDetail,
  fetchStorage,
  inviteMember,
  resetAiLimit,
  revokeInvite,
  saveAiLimits,
  type AdminActivityEvent,
  type AdminAiOverview,
  type AdminAuditEntry,
  type AdminInvite,
  type AdminMember,
  type AdminPitchLink,
  type AdminPitchRequest,
  type AdminPreviewInfo,
  type AdminRecord,
  type AdminStorage,
  type Role,
} from '../adminApi';
import { loadCache } from '../cache';
import type { NativeBridge } from '../native';
import { catalogFromProposals, pitchProposalsFromCache } from '../pitchPhone';
import type { AccountProfile } from '../types';

export const ADMIN_TABS = ['Team', 'Activity', 'Workspace', 'Pitch', 'Storage', 'AI keys', 'Audit log'] as const;
type AdminTabName = (typeof ADMIN_TABS)[number];

export interface AdminTabProps {
  profile: AccountProfile | null;
  bridge: NativeBridge;
}

function when(ts: string | null | undefined): string {
  if (!ts) return '';
  const ms = Date.parse(ts);
  return Number.isFinite(ms) ? new Date(ms).toLocaleString() : '';
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function message(err: unknown): string {
  return err instanceof AdminError ? err.message : 'Something went wrong. Try again.';
}

/** Run a loader when the inputs change; the answer is kept in memory only. A stale answer is dropped. */
function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    load().then(
      (data) => {
        if (seq.current === mine) setState({ data, error: null, loading: false });
      },
      (err) => {
        if (seq.current === mine) setState({ data: null, error: message(err), loading: false });
      },
    );
    return () => {
      seq.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

function ErrorBanner({ text }: { text: string | null }) {
  return text ? (
    <div className="mob-banner-error mob-wrap-text" role="alert">
      {text}
    </div>
  ) : null;
}

interface PanelProps {
  bridge: NativeBridge;
  members: AdminMember[];
}

function MemberSelect({ members, value, onChange }: { members: AdminMember[]; value: string; onChange: (id: string) => void }) {
  return (
    <Field label="Member" htmlFor="admin-member-select">
      <Select id="admin-member-select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a member</option>
        {members.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </Select>
    </Field>
  );
}

// --- Team ---

function TeamPanel({ bridge, selfId, team, reload }: { bridge: NativeBridge; selfId: string; team: { members: AdminMember[]; invites: AdminInvite[] }; reload: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('member');
  const stubs = team.members.map((m) => ({ id: m.id, role: m.role, active: m.active }));

  async function run(task: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await task();
      reload();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mob-stack">
      <ErrorBanner text={error} />
      <Card>
        <div className="mob-stack-sm">
          <Label>Team ({team.members.length})</Label>
          {team.members.map((m) => (
            <div key={m.id} className="mob-admin-member" data-testid="admin-member">
              <div className="mob-admin-row">
                <span className="mob-wrap-text">{m.name}</span>
                <span className="mg-chip">{m.presence}</span>
              </div>
              <p className="mob-mono mob-wrap-text">
                {m.email} · {m.role}
                {m.active ? '' : ' · deactivated'}
              </p>
              {m.running_agents.length > 0 ? <p className="mob-mono mob-wrap-text">Running: {m.running_agents.join(', ')}</p> : null}
              {m.devices.length > 0 ? (
                <p className="mob-mono mob-wrap-text">
                  {m.devices.map((d) => `${d.device_kind}${d.app_version ? ` ${d.app_version}` : ''}`).join(', ')}
                  {m.last_active_at ? ` · last active ${when(m.last_active_at)}` : ''}
                </p>
              ) : m.last_active_at ? (
                <p className="mob-mono">Last active {when(m.last_active_at)}</p>
              ) : null}
              {m.id === selfId ? (
                <span className="mg-chip">You</span>
              ) : (
                <div className="mob-admin-member-actions">
                  <PillButton
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void run(() => changeMemberRole(m.id, m.role === 'admin' ? 'member' : 'admin', selfId, stubs, bridge))}
                  >
                    {m.role === 'admin' ? 'Make member' : 'Make admin'}
                  </PillButton>
                  <PillButton
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void run(() => changeMemberActive(m.id, !m.active, selfId, stubs, bridge))}
                  >
                    {m.active ? 'Deactivate' : 'Activate'}
                  </PillButton>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
      <Card>
        <div className="mob-stack-sm">
          <Label>Invite someone</Label>
          <Field label="Invite email" htmlFor="admin-invite-email">
            <TextInput id="admin-invite-email" type="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Invite role" htmlFor="admin-invite-role">
            <Select id="admin-invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </Select>
          </Field>
          <div className="mob-actions">
            <PillButton
              size="sm"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await inviteMember(email, role, bridge);
                  setEmail('');
                })
              }
            >
              Send invite
            </PillButton>
          </div>
        </div>
      </Card>
      {team.invites.length > 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Invites</Label>
            {team.invites.map((inv) => {
              const state = inv.revoked_at ? 'revoked' : inv.accepted_at ? 'accepted' : 'open';
              return (
                <div key={inv.email} className="mob-admin-row">
                  <span className="mob-wrap-text">
                    {inv.email} · {inv.role} · {state}
                  </span>
                  {state === 'open' ? (
                    <PillButton variant="outline" size="sm" disabled={busy} onClick={() => void run(() => revokeInvite(inv.email, bridge))}>
                      Revoke
                    </PillButton>
                  ) : null}
                </div>
              );
            })}
          </div>
        </Card>
      ) : null}
    </div>
  );
}

// --- Activity ---

function ActivityPanel({ bridge, members }: PanelProps) {
  const [member, setMember] = useState('');
  const [more, setMore] = useState<AdminActivityEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const first = useLoad(() => fetchActivity({ member: member || undefined }, bridge), [member]);
  useEffect(() => {
    setMore([]);
    setCursor(null);
  }, [member]);
  const events = [...(first.data?.events ?? []), ...more];
  const next = cursor ?? (more.length === 0 ? first.data?.next_before ?? null : null);

  async function loadMore() {
    if (!next) return;
    try {
      const page = await fetchActivity({ member: member || undefined, before: next }, bridge);
      setMore((m) => [...m, ...page.events]);
      setCursor(page.next_before);
    } catch (err) {
      setError(message(err));
    }
  }

  return (
    <div className="mob-stack">
      <ErrorBanner text={first.error ?? error} />
      <Field label="Member" htmlFor="admin-activity-member">
        <Select id="admin-activity-member" value={member} onChange={(e) => setMember(e.target.value)}>
          <option value="">Everyone</option>
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </Select>
      </Field>
      <Card>
        <div className="mob-stack-sm">
          <Label>Activity</Label>
          {first.loading ? <p className="mob-mono">Loading...</p> : null}
          {!first.loading && events.length === 0 && !first.error ? <p className="mob-body-text">No activity yet.</p> : null}
          {events.map((e) => (
            <div key={e.id} className="mob-admin-member">
              <span className="mob-wrap-text">{e.summary || e.kind}</span>
              <span className="mob-mono mob-wrap-text">
                {e.member_name} · {e.kind}
                {e.agent_id ? ` · ${e.agent_id}` : ''} · {when(e.ts)}
              </span>
            </div>
          ))}
          {next ? (
            <div className="mob-actions">
              <PillButton variant="outline" size="sm" onClick={() => void loadMore()}>
                Load more
              </PillButton>
            </div>
          ) : null}
        </div>
      </Card>
    </div>
  );
}

// --- Workspace ---

function WorkspacePanel({ bridge, members }: PanelProps) {
  const [member, setMember] = useState('');
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<AdminRecord | null>(null);
  const records = useLoad<{ records: AdminRecord[]; previews: AdminPreviewInfo[] } | null>(async () => {
    if (!member) return null;
    const [r, p] = await Promise.all([fetchMemberRecords(member, { q: search.trim() || undefined }, bridge), fetchMemberPreviews(member, bridge)]);
    return { records: r.records, previews: p.previews };
  }, [member, search]);
  const detail = useLoad<{ data: Record<string, unknown>; children: AdminRecord[] } | null>(async () => {
    if (!member || !open) return null;
    const d = await fetchRecordDetail(member, open.kind, open.local_id, bridge);
    return { data: d.record.data, children: d.children };
  }, [member, open?.kind, open?.local_id]);

  return (
    <div className="mob-stack">
      <ErrorBanner text={records.error ?? detail.error} />
      <MemberSelect
        members={members}
        value={member}
        onChange={(id) => {
          setMember(id);
          setOpen(null);
        }}
      />
      {member ? (
        <Field label="Search titles" htmlFor="admin-record-search">
          <TextInput id="admin-record-search" value={search} maxLength={100} onChange={(e) => setSearch(e.target.value)} />
        </Field>
      ) : null}
      {open ? (
        <Card>
          <div className="mob-stack-sm">
            <div className="mob-row-between">
              <Label>{open.title || open.kind}</Label>
              <PillButton variant="outline" size="sm" onClick={() => setOpen(null)}>
                Back to list
              </PillButton>
            </div>
            <p className="mob-mono mob-wrap-text">
              {open.kind} · {when(open.local_updated_at)}
            </p>
            {detail.loading ? <p className="mob-mono">Loading...</p> : null}
            {detail.data ? <pre className="mob-body-text mob-wrap-text">{JSON.stringify(detail.data.data, null, 2).slice(0, 6000)}</pre> : null}
            {detail.data && detail.data.children.length > 0 ? (
              <p className="mob-mono mob-wrap-text">{detail.data.children.length} linked records</p>
            ) : null}
          </div>
        </Card>
      ) : records.data ? (
        <>
          <Card>
            <div className="mob-stack-sm">
              <Label>Records ({records.data.records.length})</Label>
              {records.data.records.length === 0 ? <p className="mob-body-text">Nothing here.</p> : null}
              {records.data.records.map((r) => (
                <button key={r.id} type="button" className="mob-admin-member mob-wrap-text" style={{ textAlign: 'left' }} onClick={() => setOpen(r)}>
                  <span>{r.title || r.local_id}</span>
                  <span className="mob-mono">
                    {r.kind} · {when(r.local_updated_at)}
                  </span>
                </button>
              ))}
            </div>
          </Card>
          <Card>
            <div className="mob-stack-sm">
              <Label>Previews ({records.data.previews.length})</Label>
              <p className="mob-mono">Opening the image is done in the desktop app.</p>
              {records.data.previews.map((p) => (
                <p key={p.id} className="mob-mono mob-wrap-text">
                  {p.kind} · {p.content_type} · {size(p.bytes)}
                  {p.expires_at ? ` · until ${when(p.expires_at)}` : ''}
                </p>
              ))}
            </div>
          </Card>
        </>
      ) : (
        <p className="mob-body-text">Choose a member to see their records.</p>
      )}
    </div>
  );
}

// --- Pitch ---

function PitchPanel({ bridge, members, names }: PanelProps & { names: Record<string, string> }) {
  const [member, setMember] = useState('');
  const pitch = useLoad<{ proposals: AdminPitchLink[]; submissions: AdminPitchRequest[] } | null>(
    async () => (member ? fetchMemberPitch(member, names, bridge) : null),
    [member],
  );
  return (
    <div className="mob-stack">
      <ErrorBanner text={pitch.error} />
      <MemberSelect members={members} value={member} onChange={setMember} />
      {pitch.data ? (
        <>
          <Card>
            <div className="mob-stack-sm">
              <Label>Links ({pitch.data.proposals.length})</Label>
              {pitch.data.proposals.map((p) => (
                <div key={`${p.share_id}-${p.local_proposal_id}`} className="mob-admin-member">
                  <span className="mob-wrap-text">{[p.business_name, p.client_name].filter(Boolean).join(' · ') || 'Untitled'}</span>
                  <span className="mob-mono">
                    {p.kind} · {p.status} · {when(p.updated_at)}
                  </span>
                </div>
              ))}
            </div>
          </Card>
          <Card>
            <div className="mob-stack-sm">
              <Label>Quote requests ({pitch.data.submissions.length})</Label>
              {pitch.data.submissions.map((s) => (
                <div key={s.id} className="mob-admin-member mob-wrap-text">
                  <span>{[s.name, s.business].filter(Boolean).join(' · ') || 'A client'}</span>
                  {s.phone ? <span className="mob-mono">{s.phone}</span> : null}
                  {s.items.length > 0 ? <span className="mob-mono">Chose: {s.items.join(', ')}</span> : null}
                  {s.total_shown !== null ? <span className="mob-mono">Total shown: {s.total_shown.toLocaleString('en-IN')}</span> : null}
                  {s.notes ? <span className="mob-mono">{s.notes}</span> : null}
                  <span className="mob-mono">{when(s.created_at)}</span>
                </div>
              ))}
            </div>
          </Card>
        </>
      ) : (
        <p className="mob-body-text">Choose a member to see their links and quote requests.</p>
      )}
    </div>
  );
}

// --- Storage ---

function StoragePanel({ bridge }: { bridge: NativeBridge }) {
  const res = useLoad<AdminStorage>(() => fetchStorage(bridge), []);
  const s = res.data;
  return (
    <div className="mob-stack">
      <ErrorBanner text={res.error} />
      {s ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Storage</Label>
            <p className="mob-body-text">
              {s.files} files · {size(s.bytes)} of {size(s.limit_bytes)} ({s.percent}%)
            </p>
            <progress value={Math.min(100, s.percent)} max={100} aria-label="Storage used" style={{ width: '100%' }} />
            {s.warning ? <span className="mg-chip">Over 80% full</span> : null}
            {s.by_kind.map((k) => (
              <p key={k.kind} className="mob-mono">
                {k.kind} · {size(k.bytes)} · {k.files} items
              </p>
            ))}
          </div>
        </Card>
      ) : res.loading ? (
        <p className="mob-mono">Loading...</p>
      ) : null}
    </div>
  );
}

// --- AI keys ---

function AiPanel({ bridge, members }: PanelProps) {
  const ai = useLoad<AdminAiOverview>(() => fetchAiOverview(bridge), []);
  const [teamReq, setTeamReq] = useState('');
  const [teamTok, setTeamTok] = useState('');
  const [member, setMember] = useState('');
  const [memReq, setMemReq] = useState('');
  const [memTok, setMemTok] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = ai.data;
  const shownReq = teamReq !== '' ? teamReq : data ? String(data.default_limit.daily_requests) : '';
  const shownTok = teamTok !== '' ? teamTok : data ? String(data.default_limit.daily_tokens) : '';

  const num = (text: string) => (/^\d+$/.test(text.trim()) ? Number(text.trim()) : NaN);

  async function run(task: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await task();
      ai.reload();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mob-stack">
      <ErrorBanner text={ai.error ?? error} />
      {data ? (
        <>
          <Card>
            <div className="mob-stack-sm">
              <Label>Shared keys ({data.keys.length})</Label>
              {data.keys.length === 0 ? <p className="mob-body-text">No shared keys.</p> : null}
              {data.keys.map((k) => (
                <div key={k.label} className="mob-admin-member">
                  <span className="mob-wrap-text">{k.label}</span>
                  <span className="mob-mono mob-wrap-text">
                    {k.last_status === null ? 'not used yet' : `last status ${k.last_status}`}
                    {k.last_ok_at ? ` · ok ${when(k.last_ok_at)}` : ''}
                    {k.cooldown_until ? ` · cooling until ${when(k.cooldown_until)}` : ''}
                    {k.last_error ? ` · ${k.last_error}` : ''}
                  </span>
                </div>
              ))}
              <p className="mob-mono">Sharing keys is done on the laptop, in Settings.</p>
            </div>
          </Card>
          <Card>
            <div className="mob-stack-sm">
              <Label>Daily limit for the team</Label>
              <Field label="Team requests per day" htmlFor="admin-team-req">
                <TextInput id="admin-team-req" inputMode="numeric" value={shownReq} onChange={(e) => setTeamReq(e.target.value)} />
              </Field>
              <Field label="Team tokens per day" htmlFor="admin-team-tok">
                <TextInput id="admin-team-tok" inputMode="numeric" value={shownTok} onChange={(e) => setTeamTok(e.target.value)} />
              </Field>
              <div className="mob-actions">
                <PillButton size="sm" disabled={busy} onClick={() => void run(() => saveAiLimits(null, num(shownReq), num(shownTok), bridge))}>
                  Save team limit
                </PillButton>
              </div>
            </div>
          </Card>
          <Card>
            <div className="mob-stack-sm">
              <Label>Limit for one member</Label>
              <Field label="Limit for member" htmlFor="admin-limit-member">
                <Select id="admin-limit-member" value={member} onChange={(e) => setMember(e.target.value)}>
                  <option value="">Choose a member</option>
                  {members.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Member requests per day" htmlFor="admin-mem-req">
                <TextInput id="admin-mem-req" inputMode="numeric" value={memReq} onChange={(e) => setMemReq(e.target.value)} />
              </Field>
              <Field label="Member tokens per day" htmlFor="admin-mem-tok">
                <TextInput id="admin-mem-tok" inputMode="numeric" value={memTok} onChange={(e) => setMemTok(e.target.value)} />
              </Field>
              <div className="mob-actions">
                <PillButton size="sm" disabled={busy || !member} onClick={() => void run(() => saveAiLimits(member, num(memReq), num(memTok), bridge))}>
                  Save member limit
                </PillButton>
                <PillButton variant="outline" size="sm" disabled={busy || !member} onClick={() => void run(() => resetAiLimit(member, bridge))}>
                  Back to team limit
                </PillButton>
              </div>
            </div>
          </Card>
          <Card>
            <div className="mob-stack-sm">
              <Label>Usage</Label>
              {data.usage.map((u) => (
                <div key={u.owner_id} className="mob-admin-member">
                  <span className="mob-wrap-text">{u.name}</span>
                  <span className="mob-mono mob-wrap-text">
                    today {u.today.requests} requests, {u.today.tokens.toLocaleString('en-US')} tokens · 7 days {u.seven_days.requests} requests · limit{' '}
                    {u.limit.daily_requests} requests, {u.limit.daily_tokens.toLocaleString('en-US')} tokens{u.limit.is_override ? ' (own limit)' : ''}
                  </span>
                </div>
              ))}
              {data.models.length > 0 ? <p className="mob-mono mob-wrap-text">Models: {data.models.filter((m) => m.enabled).map((m) => m.model).join(', ')}</p> : null}
            </div>
          </Card>
        </>
      ) : ai.loading ? (
        <p className="mob-mono">Loading...</p>
      ) : null}
    </div>
  );
}

// --- Audit log ---

function AuditPanel({ bridge }: { bridge: NativeBridge }) {
  const first = useLoad(() => fetchAudit({}, bridge), []);
  const [more, setMore] = useState<AdminAuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const entries = [...(first.data?.entries ?? []), ...more];
  const next = cursor ?? (more.length === 0 ? first.data?.next_before ?? null : null);

  async function loadMore() {
    if (!next) return;
    try {
      const page = await fetchAudit({ before: next }, bridge);
      setMore((m) => [...m, ...page.entries]);
      setCursor(page.next_before);
    } catch (err) {
      setError(message(err));
    }
  }

  return (
    <div className="mob-stack">
      <ErrorBanner text={first.error ?? error} />
      <Card>
        <div className="mob-stack-sm">
          <Label>Audit log</Label>
          {first.loading ? <p className="mob-mono">Loading...</p> : null}
          {entries.map((e) => (
            <div key={e.id} className="mob-admin-member">
              <span className="mob-wrap-text">{e.action}</span>
              <span className="mob-mono mob-wrap-text">
                {e.actor_name}
                {e.target_label ? ` · ${e.target_label}` : ''} · {when(e.created_at)}
              </span>
            </div>
          ))}
          {next ? (
            <div className="mob-actions">
              <PillButton variant="outline" size="sm" onClick={() => void loadMore()}>
                Load more
              </PillButton>
            </div>
          ) : null}
        </div>
      </Card>
    </div>
  );
}

// --- The screen ---

function AdminConsole({ profile, bridge }: { profile: AccountProfile; bridge: NativeBridge }) {
  const [tab, setTab] = useState<AdminTabName>('Team');
  const team = useLoad(() => fetchAdminTeam(profile.id, bridge), [profile.id]);
  const members = team.data?.members ?? [];
  const names = useMemo(() => {
    try {
      const cache = loadCache(Date.now(), { ownerUid: profile.id });
      const catalog = catalogFromProposals(pitchProposalsFromCache(cache));
      return Object.fromEntries(Object.entries(catalog?.items ?? {}).map(([id, it]) => [id, it.n]));
    } catch {
      return {};
    }
  }, [profile.id]);

  const select = useCallback((next: AdminTabName) => setTab(next), []);

  return (
    <section aria-label="Admin" className="mob-admin">
      <div role="tablist" aria-label="Admin sections" className="mob-subtabs">
        {ADMIN_TABS.map((name) => (
          <button key={name} type="button" role="tab" aria-selected={tab === name} className="mob-subtab" onClick={() => select(name)}>
            {name}
          </button>
        ))}
      </div>
      {team.error && tab !== 'Team' ? <ErrorBanner text={team.error} /> : null}
      {tab === 'Team' ? (
        team.data ? (
          <TeamPanel bridge={bridge} selfId={profile.id} team={team.data} reload={team.reload} />
        ) : (
          <>
            <ErrorBanner text={team.error} />
            {team.loading ? <p className="mob-mono">Loading...</p> : null}
          </>
        )
      ) : null}
      {tab === 'Activity' ? <ActivityPanel bridge={bridge} members={members} /> : null}
      {tab === 'Workspace' ? <WorkspacePanel bridge={bridge} members={members} /> : null}
      {tab === 'Pitch' ? <PitchPanel bridge={bridge} members={members} names={names} /> : null}
      {tab === 'Storage' ? <StoragePanel bridge={bridge} /> : null}
      {tab === 'AI keys' ? <AiPanel bridge={bridge} members={members} /> : null}
      {tab === 'Audit log' ? <AuditPanel bridge={bridge} /> : null}
    </section>
  );
}

/** Admin on the phone. Only an active admin gets the screen; anyone else gets a sentence and no requests. */
export function AdminTab({ profile, bridge }: AdminTabProps) {
  if (!profile || profile.role !== 'admin' || profile.active === false) {
    return <div className="mg-notice">Admins only.</div>;
  }
  return <AdminConsole profile={profile} bridge={bridge} />;
}
