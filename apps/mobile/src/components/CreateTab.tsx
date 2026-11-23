import { useEffect, useMemo, useState } from 'react';
import { Card, Checkbox, Field, Label, PillButton, Select, TextArea, TextInput } from '@magnm/design/react';
import type { ActionInput } from '../actions';
import { stateLabel } from '../actions';
import type { ContextCacheState } from '../cache';
import {
  LAPTOP_STEPS,
  PHONE_STEPS,
  buildDraftInput,
  buildResearchInput,
  buildWorkflowInput,
  fetchTeamRepos,
  runPhoneStep,
  type DraftResult,
  type LaptopStepId,
  type PhoneStepId,
  type TeamRepo,
} from '../createPhone';
import { draftsFromCache } from '../mirrorView';
import type { NativeBridge } from '../native';
import type { PhoneAction, ProductRulesConfig } from '../types';

export interface CreateTabProps {
  ownerUid: string | null;
  signedIn: boolean;
  cache: ContextCacheState;
  phoneActions: PhoneAction[];
  bridge: NativeBridge;
  rules: ProductRulesConfig | null;
  onQueueAction: (input: ActionInput, okMessage: string) => Promise<boolean>;
}

const PLATFORMS = ['linkedin', 'x', 'instagram', 'youtube'] as const;
const NEEDS_REPO: LaptopStepId[] = ['post_image', 'blog', 'video_script'];
const CREATE_KINDS = ['draft_create', 'workflow_request'];

function isPhoneStep(id: string): id is PhoneStepId {
  return PHONE_STEPS.some((s) => s.id === id);
}

/** Create: text-only steps run here with Phone Lite; everything that needs laptop tools is queued as a request. */
export function CreateTab({ ownerUid, signedIn, cache, phoneActions, bridge, onQueueAction }: CreateTabProps) {
  const [step, setStep] = useState<string>('post');
  const [platform, setPlatform] = useState<string>('linkedin');
  const [brief, setBrief] = useState('');
  const [original, setOriginal] = useState('');
  const [result, setResult] = useState<DraftResult | null>(null);
  const [resultPlatform, setResultPlatform] = useState('linkedin');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repos, setRepos] = useState<TeamRepo[]>([]);
  const [repoId, setRepoId] = useState('');
  const [topic, setTopic] = useState('');
  const [platforms, setPlatforms] = useState<string[]>(['linkedin', 'x']);
  const [duration, setDuration] = useState('30');
  const [aspect, setAspect] = useState('9:16');
  const [contentId, setContentId] = useState('');
  const drafts = useMemo(() => draftsFromCache(cache).filter((d) => !d.confidential), [cache]);

  useEffect(() => {
    if (!ownerUid) return;
    let cancelled = false;
    fetchTeamRepos(ownerUid, bridge)
      .then((list) => {
        if (!cancelled) setRepos(list);
      })
      .catch(() => {
        // The picker is optional: without it the work is not tied to one repository.
      });
    return () => {
      cancelled = true;
    };
  }, [ownerUid, bridge]);

  const onPhone = isPhoneStep(step);
  const laptopStep = LAPTOP_STEPS.find((s) => s.id === step) ?? null;

  async function write() {
    if (!isPhoneStep(step) || busy) return;
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const draft = await runPhoneStep({ step, platform, brief, original: step === 'rewrite' ? original : undefined }, bridge);
      setResult(draft);
      setResultPlatform(platform);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Phone Lite could not write this.');
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    if (!result || !isPhoneStep(step)) return;
    setError(null);
    const ok = await onQueueAction(
      buildDraftInput(result, resultPlatform, step),
      'Saved as a draft for your laptop. It adds it to Drafts when it is back.',
    );
    if (ok) {
      setResult(null);
      setBrief('');
      setOriginal('');
    }
  }

  async function queueLaptop() {
    if (!laptopStep) return;
    setError(null);
    let input: ActionInput;
    try {
      if (laptopStep.id === 'research') {
        input = buildResearchInput(topic);
      } else {
        input = buildWorkflowInput(laptopStep.id, {
          brief: brief || undefined,
          topic: topic || undefined,
          platforms: platforms.length > 0 ? platforms : undefined,
          repo_id: NEEDS_REPO.includes(laptopStep.id) && repoId ? repoId : undefined,
          duration_s: Number(duration),
          aspect,
          content_id: contentId || undefined,
          posts_per_week: 3,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'This could not be queued.');
      return;
    }
    const ok = await onQueueAction(input, `Queued for your laptop. It starts when your laptop is back and asks for your approval as usual.`);
    if (ok) {
      setBrief('');
      setTopic('');
    }
  }

  const recent = phoneActions.filter((a) => CREATE_KINDS.includes(a.kind) && a.target_id !== 'weekly_plan' || (a.kind === 'agent_task' && a.target_id === 'researcher')).slice(-6).reverse();

  return (
    <div className="mob-stack">
      <Card>
        <div className="mob-stack-sm">
          <Label>Create</Label>
          <Field label="What do you want to make?" htmlFor="create-step">
            <Select id="create-step" value={step} onChange={(e) => { setStep(e.target.value); setError(null); setResult(null); }} disabled={!signedIn}>
              <optgroup label="Written on this phone">
                {PHONE_STEPS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Needs the laptop">
                {LAPTOP_STEPS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </optgroup>
            </Select>
          </Field>

          {onPhone ? (
            <>
              <div className="mob-body-text">{PHONE_STEPS.find((s) => s.id === step)?.hint} It runs with Phone Lite, so it works without the laptop. Saving it queues a draft for the laptop.</div>
              <Field label="Platform" htmlFor="create-platform">
                <Select id="create-platform" value={platform} onChange={(e) => setPlatform(e.target.value)}>
                  {PLATFORMS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={step === 'rewrite' ? 'What should change?' : 'What is it about?'} htmlFor="create-brief">
                <TextArea id="create-brief" rows={3} maxLength={2000} value={brief} onChange={(e) => setBrief(e.target.value)} />
              </Field>
              {step === 'rewrite' ? (
                <Field label="Draft to rewrite" htmlFor="create-original">
                  <TextArea id="create-original" rows={5} maxLength={6000} value={original} onChange={(e) => setOriginal(e.target.value)} />
                </Field>
              ) : null}
            </>
          ) : (
            <>
              <div className="mg-notice">Needs your laptop. {laptopStep?.hint} It is queued and starts when the laptop is back, with the usual approvals.</div>
              {laptopStep?.id === 'post_image' ? (
                <Field label="What is it about?" htmlFor="create-brief">
                  <TextArea id="create-brief" rows={3} maxLength={2000} value={brief} onChange={(e) => setBrief(e.target.value)} />
                </Field>
              ) : null}
              {laptopStep && ['blog', 'video_script', 'research'].includes(laptopStep.id) ? (
                <Field label="Topic" htmlFor="create-topic">
                  <TextArea id="create-topic" rows={3} maxLength={1000} value={topic} onChange={(e) => setTopic(e.target.value)} />
                </Field>
              ) : null}
              {laptopStep && ['post_image', 'weekly_plan'].includes(laptopStep.id) ? (
                <div className="mob-asset-edit">
                  {PLATFORMS.map((p) => (
                    <Checkbox key={p} label={p} checked={platforms.includes(p)} onChange={(e) => setPlatforms((cur) => (e.target.checked ? [...cur, p] : cur.filter((x) => x !== p)))} />
                  ))}
                </div>
              ) : null}
              {laptopStep?.id === 'video_script' ? (
                <div className="mob-asset-edit">
                  <Select aria-label="Length" value={duration} onChange={(e) => setDuration(e.target.value)}>
                    {['15', '30', '60'].map((d) => (
                      <option key={d} value={d}>
                        {d} seconds
                      </option>
                    ))}
                  </Select>
                  <Select aria-label="Shape" value={aspect} onChange={(e) => setAspect(e.target.value)}>
                    <option value="9:16">Vertical 9:16</option>
                    <option value="16:9">Wide 16:9</option>
                  </Select>
                </div>
              ) : null}
              {laptopStep?.id === 'repurpose' ? (
                <Field label="Draft to repurpose" htmlFor="create-draft">
                  <Select id="create-draft" value={contentId} onChange={(e) => setContentId(e.target.value)}>
                    <option value="">Choose a draft</option>
                    {drafts.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.title}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : null}
              {laptopStep && NEEDS_REPO.includes(laptopStep.id) ? (
                <Field label="Repository (optional)" htmlFor="create-repo">
                  <Select id="create-repo" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
                    <option value="">Not tied to one repository</option>
                    {repos.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : null}
            </>
          )}

          {error ? (
            <div className="mob-banner-error" role="alert">
              {error}
            </div>
          ) : null}
          <div className="mob-actions">
            {onPhone ? (
              <PillButton size="sm" disabled={!signedIn || busy || !brief.trim() || (step === 'rewrite' && !original.trim())} onClick={() => void write()}>
                {busy ? 'Writing' : 'Write it on this phone'}
              </PillButton>
            ) : (
              <PillButton size="sm" disabled={!signedIn} onClick={() => void queueLaptop()}>
                Queue for the laptop
              </PillButton>
            )}
          </div>
        </div>
      </Card>

      {result ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Result</Label>
            <div className="mob-asset-meta">Written on this phone with Phone Lite. Nothing is saved on the laptop until you save it.</div>
            <Field label="Title" htmlFor="create-result-title">
              <TextInput id="create-result-title" value={result.title} maxLength={200} onChange={(e) => setResult({ ...result, title: e.target.value })} />
            </Field>
            <Field label="Text" htmlFor="create-result-body">
              <TextArea id="create-result-body" rows={8} maxLength={6000} value={result.body} onChange={(e) => setResult({ ...result, body: e.target.value })} />
            </Field>
            <div className="mob-actions">
              <PillButton size="sm" disabled={!result.title.trim() || !result.body.trim()} onClick={() => void saveDraft()}>
                Save as a draft for the laptop
              </PillButton>
              <PillButton size="sm" variant="outline" onClick={() => setResult(null)}>
                Discard
              </PillButton>
            </div>
          </div>
        </Card>
      ) : null}

      {recent.length > 0 ? (
        <Card>
          <div className="mob-stack-sm">
            <Label>Waiting and finished</Label>
            {recent.map((a) => (
              <div key={a.id} className="mob-asset">
                <div className="mob-wrap-text">{a.label ?? a.target_id}</div>
                <div className="mob-asset-meta">
                  {stateLabel(a)}
                  {a.result ? ` · ${a.result}` : ''}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
