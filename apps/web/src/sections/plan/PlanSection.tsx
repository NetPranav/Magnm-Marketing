import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Card,
  Checkbox,
  Dialog,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  SheetHeader,
  StatusDot,
  TextInput,
} from '@magnm/design/react';
import {
  answerGate,
  cancelWorkflow,
  getContentItem,
  getStatus,
  listContent,
  listWorkflows,
  startWeeklyPlan,
  toggleSchedule,
  updateSchedule,
} from '../../api/plan';
import { useStudioEvents } from '../../events';
import type { ContentItem, EditorialPlanRow, ScheduleRecord, WorkflowRun } from '../../types';
import { formatTimestamp } from '../../ui/format';
import { EditorialCalendar } from './EditorialCalendar';

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** Read "30 9 * * 1" as { time: "09:30", day: "1" }. */
function parseCron(cron?: string | null): { time: string; day: string } | null {
  const parts = (cron || '').trim().split(/\s+/);
  if (parts.length !== 5 || !/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1])) return null;
  return { time: `${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}`, day: parts[4] };
}

function describeCron(cron?: string | null): string | null {
  const parsed = parseCron(cron);
  if (!parsed) return cron || null;
  const when = parsed.day === '*' ? 'Every day' : `Every ${WEEKDAYS[Number(parsed.day) % 7] ?? parsed.day}`;
  return `${when} at ${parsed.time} IST`;
}

interface PlanSlot {
  date: string;
  platform: string;
  topic: string;
  angle?: string;
  product_id?: string | null;
  reason?: string;
}

const PLAN_PLATFORMS = ['linkedin', 'x', 'instagram', 'youtube'] as const;

function parsePlanBodySlots(body: string | undefined): PlanSlot[] {
  if (!body) return [];
  try {
    const parsed = JSON.parse(body) as { slots?: unknown };
    if (!Array.isArray(parsed.slots)) return [];
    return parsed.slots.filter(
      (item): item is PlanSlot => Boolean(item && typeof item === 'object'),
    );
  } catch {
    return [];
  }
}

/** Plan section: weekly plan workflow, briefs, month editorial calendar, and schedules. */
export function PlanSection() {
  const [workflows, setWorkflows] = useState<WorkflowRun[]>([]);
  const [contentItems, setContentItems] = useState<ContentItem[]>([]);
  const [storedPlanItem, setStoredPlanItem] = useState<ContentItem | null>(null);
  const [schedules, setSchedules] = useState<ScheduleRecord[]>([]);
  const [editorialPlan, setEditorialPlan] = useState<EditorialPlanRow[]>([]);

  const [postsPerWeek, setPostsPerWeek] = useState('3');
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['linkedin', 'x']);
  const [gateFeedback, setGateFeedback] = useState<Record<string, string>>({});

  const [editingSched, setEditingSched] = useState<ScheduleRecord | null>(null);
  const [editInterval, setEditInterval] = useState('');
  const [editEnabled, setEditEnabled] = useState(true);
  const [editTime, setEditTime] = useState('');
  const [editDay, setEditDay] = useState('*');

  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      listWorkflows(30),
      listContent(100),
      getStatus(),
    ]);

    let firstErr: string | null = null;
    const [wfRes, contentRes, statusRes] = results;

    if (wfRes.status === 'fulfilled') {
      setWorkflows(Array.isArray(wfRes.value) ? wfRes.value : []);
    } else if (wfRes.reason instanceof Error) {
      firstErr = firstErr ?? wfRes.reason.message;
    }

    if (contentRes.status === 'fulfilled') {
      const items = Array.isArray(contentRes.value) ? contentRes.value : [];
      setContentItems(items);
      const latestPlan = items.find((item) => item.type === 'plan');
      if (latestPlan) {
        try {
          const full = await getContentItem(latestPlan.id);
          setStoredPlanItem(full);
        } catch {
          setStoredPlanItem(latestPlan);
        }
      } else {
        setStoredPlanItem(null);
      }
    } else if (contentRes.reason instanceof Error) {
      firstErr = firstErr ?? contentRes.reason.message;
    }

    if (statusRes.status === 'fulfilled') {
      setSchedules(Array.isArray(statusRes.value?.schedules) ? statusRes.value.schedules : []);
      setEditorialPlan(
        Array.isArray(statusRes.value?.editorial_plan) ? statusRes.value.editorial_plan : [],
      );
    } else if (statusRes.reason instanceof Error) {
      firstErr = firstErr ?? statusRes.reason.message;
    }

    setError(firstErr);
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useStudioEvents(
    useCallback(
      (eventName) => {
        if (
          eventName === 'workflow_step' ||
          eventName === 'workflow_waiting' ||
          eventName === 'workflow_done' ||
          eventName === 'workflow_failed' ||
          eventName === 'content_status'
        ) {
          void loadAll();
        }
      },
      [loadAll],
    ),
  );

  const latestWeeklyPlanRun = useMemo(
    () => workflows.find((w) => w.kind === 'weekly_plan') ?? null,
    [workflows],
  );

  const weeklyPlanSlots = useMemo<PlanSlot[]>(() => {
    const runSlots = latestWeeklyPlanRun?.state?.proposed_slots;
    if (Array.isArray(runSlots) && runSlots.length > 0) {
      return runSlots as PlanSlot[];
    }
    return parsePlanBodySlots(storedPlanItem?.body);
  }, [latestWeeklyPlanRun, storedPlanItem]);

  const briefs = useMemo(() => {
    return workflows.filter((wf) => {
      const brief = wf.inputs?.brief || wf.inputs?.topic || wf.inputs?.content_id;
      return Boolean(brief || wf.kind === 'weekly_plan');
    });
  }, [workflows]);

  const togglePlatform = (plat: string) => {
    setSelectedPlatforms((prev) =>
      prev.includes(plat) ? prev.filter((p) => p !== plat) : [...prev, plat],
    );
  };

  const handlePlanMyWeek = async () => {
    setBusyId('plan-week');
    setError(null);
    try {
      const count = Math.max(1, Number.parseInt(postsPerWeek, 10) || 2);
      const platforms = selectedPlatforms.length > 0 ? selectedPlatforms : ['linkedin', 'x'];
      await startWeeklyPlan({ posts_per_week: count, platforms });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start weekly plan.');
    } finally {
      setBusyId(null);
    }
  };

  const handleAnswerGate = async (gateId: string, option: string) => {
    const fb = (gateFeedback[gateId] || '').trim();
    if ((option.toLowerCase() === 'change' || option.toLowerCase() === 'revise') && !fb) {
      setError(`Please enter feedback before choosing ${option}.`);
      return;
    }
    setBusyId(gateId);
    setError(null);
    try {
      await answerGate(gateId, option, fb || null);
      setGateFeedback((prev) => ({ ...prev, [gateId]: '' }));
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to answer gate.');
    } finally {
      setBusyId(null);
    }
  };

  const handleCancelRun = async (runId: string) => {
    setBusyId(runId);
    setError(null);
    try {
      await cancelWorkflow(runId);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel workflow.');
    } finally {
      setBusyId(null);
    }
  };

  const handleToggleSchedule = async (schedId: string) => {
    setBusyId(schedId);
    setError(null);
    try {
      await toggleSchedule(schedId);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle schedule.');
    } finally {
      setBusyId(null);
    }
  };

  const openScheduleEdit = (sched: ScheduleRecord) => {
    setEditingSched(sched);
    setEditInterval(sched.interval_hours ? String(sched.interval_hours) : '');
    setEditEnabled(Boolean(sched.enabled));
    const parsed = parseCron(sched.cron);
    setEditTime(parsed?.time ?? '');
    setEditDay(parsed?.day ?? '*');
  };

  const handleSaveScheduleEdit = async () => {
    if (!editingSched) return;
    setBusyId(editingSched.id);
    setError(null);
    try {
      const payload: { id: string; interval_hours?: number | null; time?: string; day?: string; enabled: boolean } = {
        id: editingSched.id,
        enabled: editEnabled,
      };
      if (editingSched.interval_hours !== undefined && editingSched.interval_hours !== null) {
        const parsed = Number.parseInt(editInterval, 10);
        if (!Number.isFinite(parsed) || parsed < 1) {
          setError('Interval hours must be a positive integer.');
          setBusyId(null);
          return;
        }
        payload.interval_hours = parsed;
      } else if (parseCron(editingSched.cron)) {
        payload.time = editTime;
        payload.day = editDay;
      }
      await updateSchedule(payload);
      setEditingSched(null);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update schedule.');
    } finally {
      setBusyId(null);
    }
  };

  const pendingPlanGates = (latestWeeklyPlanRun?.gates || []).filter(
    (g) => g.status === 'pending',
  );

  return (
    <div className="studio-stack">
      <SheetHeader
        page={4}
        total={10}
        subtitle="Editorial & Automation"
        title="Plan"
        lead="Weekly editorial planning, active briefs, month publishing calendar, and automated schedules."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <section className="studio-section" aria-labelledby="plan-weekly-heading">
        <div className="studio-section-bar">
          <h2 id="plan-weekly-heading" className="studio-section-title">
            Weekly plan
          </h2>
          {latestWeeklyPlanRun ? (
            <span className="mg-chip">
              {latestWeeklyPlanRun.status}
              {latestWeeklyPlanRun.inputs?.week_start
                ? ` · ${String(latestWeeklyPlanRun.inputs.week_start)}`
                : ''}
            </span>
          ) : null}
        </div>

        <Card>
          <div className="studio-stack-sm">
            <div className="studio-plan-form-row">
              <Field label="Posts per week" htmlFor="plan-posts-count">
                <TextInput
                  id="plan-posts-count"
                  type="number"
                  min={1}
                  max={14}
                  value={postsPerWeek}
                  onChange={(e) => setPostsPerWeek(e.target.value)}
                />
              </Field>
              <div className="studio-field-grow">
                <span className="mg-label">Platforms</span>
                <div className="studio-chip-row">
                  {PLAN_PLATFORMS.map((plat) => (
                    <Checkbox
                      key={plat}
                      label={plat}
                      checked={selectedPlatforms.includes(plat)}
                      onChange={() => togglePlatform(plat)}
                    />
                  ))}
                </div>
              </div>
              <div className="studio-plan-submit">
                <PillButton
                  disabled={busyId === 'plan-week'}
                  onClick={() => void handlePlanMyWeek()}
                >
                  {busyId === 'plan-week' ? 'Starting...' : 'Plan my week'}
                </PillButton>
              </div>
            </div>
          </div>
        </Card>

        {pendingPlanGates.map((gate) => (
          <Card key={gate.id} selected>
            <div className="studio-card-head">
              <div>
                <div className="mg-label">Slot approval gate</div>
                <div className="studio-card-title">{gate.question}</div>
              </div>
              {latestWeeklyPlanRun ? (
                <PillButton
                  variant="outline"
                  size="sm"
                  disabled={busyId === latestWeeklyPlanRun.id}
                  onClick={() => void handleCancelRun(latestWeeklyPlanRun.id)}
                >
                  Cancel plan
                </PillButton>
              ) : null}
            </div>
            <div className="studio-gate-box">
              <TextInput
                aria-label={`Feedback for ${gate.id}`}
                placeholder="Change notes (required when choosing Change)"
                value={gateFeedback[gate.id] ?? ''}
                onChange={(e) =>
                  setGateFeedback((prev) => ({ ...prev, [gate.id]: e.target.value }))
                }
              />
              <div className="studio-btn-group">
                {(gate.options || []).map((opt, idx) => (
                  <PillButton
                    key={opt}
                    variant={idx === 0 ? 'primary' : 'outline'}
                    size="sm"
                    disabled={busyId === gate.id}
                    onClick={() => void handleAnswerGate(gate.id, opt)}
                  >
                    {opt}
                  </PillButton>
                ))}
              </div>
            </div>
          </Card>
        ))}

        {loading && weeklyPlanSlots.length === 0 ? (
          <div className="mg-empty">Loading latest weekly plan.</div>
        ) : weeklyPlanSlots.length === 0 ? (
          <div className="mg-empty">
            No weekly plan generated yet. Use Plan my week above to create slots.
          </div>
        ) : (
          <RowList aria-label="Weekly plan slots">
            {weeklyPlanSlots.map((slot, idx) => (
              <Row
                key={`${slot.date}-${slot.platform}-${idx}`}
                title={slot.topic || `Slot ${idx + 1}`}
                description={
                  <span>
                    {slot.angle || slot.reason || 'Proposed weekly slot'}
                    {slot.product_id ? ` · ${slot.product_id}` : ''}
                  </span>
                }
                meta={`${slot.date} · ${slot.platform}`}
              />
            ))}
          </RowList>
        )}
      </section>

      <section className="studio-section" aria-labelledby="plan-briefs-heading">
        <h2 id="plan-briefs-heading" className="studio-section-title">
          Briefs
        </h2>
        {loading && briefs.length === 0 && editorialPlan.length === 0 ? (
          <div className="mg-empty">Loading briefs.</div>
        ) : briefs.length === 0 && editorialPlan.length === 0 ? (
          <div className="mg-empty">No workflow briefs or editorial slots yet.</div>
        ) : (
          <RowList aria-label="Briefs">
            {briefs.map((wf) => {
              const title = String(
                wf.inputs?.brief || wf.inputs?.topic || wf.inputs?.content_id || wf.kind,
              );
              const firstCid = wf.content_ids?.[0];
              return (
                <Row
                  key={wf.id}
                  title={
                    firstCid ? (
                      <a href={`#/review/${encodeURIComponent(firstCid)}`}>{title}</a>
                    ) : (
                      title
                    )
                  }
                  description={
                    <span className="studio-mono">
                      {wf.kind} · step {wf.current_step}
                      {wf.created_at ? ` · ${formatTimestamp(wf.created_at)}` : ''}
                    </span>
                  }
                  meta={<span className="mg-chip">{wf.status}</span>}
                />
              );
            })}
            {editorialPlan.map((row, idx) => (
              <Row
                key={`ed-${idx}-${row.slot}`}
                title={row.topic}
                description={`${row.slot} · ${row.platforms}`}
                meta={<span className="mg-chip">{row.status}</span>}
              />
            ))}
          </RowList>
        )}
      </section>

      <section className="studio-section" aria-labelledby="plan-calendar-heading">
        <h2 id="plan-calendar-heading" className="studio-section-title">
          Editorial calendar
        </h2>
        <EditorialCalendar
          items={contentItems}
          plannedSlots={weeklyPlanSlots}
          loading={loading}
        />
      </section>

      <section className="studio-section" aria-labelledby="plan-schedules-heading">
        <h2 id="plan-schedules-heading" className="studio-section-title">
          Schedules &amp; timers
        </h2>
        {loading && schedules.length === 0 ? (
          <div className="mg-empty">Loading schedules.</div>
        ) : schedules.length === 0 ? (
          <div className="mg-empty">No schedules configured.</div>
        ) : (
          <RowList aria-label="Schedules">
            {schedules.map((sched) => {
              const cadence = sched.interval_hours
                ? `Every ${sched.interval_hours}h`
                : describeCron(sched.cron) || 'Manual';
              return (
                <Row
                  key={sched.id}
                  title={sched.name}
                  description={
                    <div className="studio-stack-xs">
                      {sched.description ? <span>{sched.description}</span> : null}
                      <span className="studio-mono">
                        {cadence}
                        {sched.last_run ? ` · last ${formatTimestamp(sched.last_run)}` : ' · never run'}
                      </span>
                    </div>
                  }
                  meta={
                    <div className="studio-row-actions">
                      <StatusDot
                        state={sched.enabled ? 'online' : 'off'}
                        label={sched.enabled ? 'Active' : 'Paused'}
                      />
                      <PillButton
                        variant="outline"
                        size="sm"
                        disabled={busyId === sched.id}
                        onClick={() => void handleToggleSchedule(sched.id)}
                      >
                        {sched.enabled ? 'Pause' : 'Enable'}
                      </PillButton>
                      <PillButton
                        variant="quiet"
                        size="sm"
                        disabled={busyId === sched.id}
                        onClick={() => openScheduleEdit(sched)}
                      >
                        Edit
                      </PillButton>
                    </div>
                  }
                />
              );
            })}
          </RowList>
        )}
      </section>

      <Dialog
        open={Boolean(editingSched)}
        onClose={() => setEditingSched(null)}
        title={editingSched?.name || 'Edit schedule'}
        subtitle="Schedule settings"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setEditingSched(null)}>
              Cancel
            </PillButton>
            <PillButton
              size="sm"
              disabled={Boolean(editingSched && busyId === editingSched.id)}
              onClick={() => void handleSaveScheduleEdit()}
            >
              Save changes
            </PillButton>
          </>
        }
      >
        {editingSched ? (
          <div className="studio-stack-sm">
            {editingSched.interval_hours !== undefined && editingSched.interval_hours !== null ? (
              <Field label="Interval (hours)" htmlFor="edit-schedule-interval">
                <TextInput
                  id="edit-schedule-interval"
                  type="number"
                  min={1}
                  value={editInterval}
                  onChange={(e) => setEditInterval(e.target.value)}
                />
              </Field>
            ) : parseCron(editingSched.cron) ? (
              <>
                <Field label="Time (IST)" htmlFor="edit-schedule-time">
                  <TextInput
                    id="edit-schedule-time"
                    type="time"
                    value={editTime}
                    onChange={(e) => setEditTime(e.target.value)}
                  />
                </Field>
                <Field label="Day" htmlFor="edit-schedule-day">
                  <Select id="edit-schedule-day" value={editDay} onChange={(e) => setEditDay(e.target.value)}>
                    {editingSched.command === 'weekly_summary' ? null : <option value="*">Every day</option>}
                    {WEEKDAYS.map((name, i) => (
                      <option key={name} value={String(i)}>
                        {name}
                      </option>
                    ))}
                  </Select>
                </Field>
              </>
            ) : (
              <p className="mg-sub">Runs on a fixed schedule.</p>
            )}
            <Checkbox
              label="Schedule enabled"
              checked={editEnabled}
              onChange={(e) => setEditEnabled(e.target.checked)}
            />
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
