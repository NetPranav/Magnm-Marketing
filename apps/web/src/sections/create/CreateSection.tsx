import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Card,
  Checkbox,
  Field,
  PillButton,
  Row,
  RowList,
  Select,
  SheetHeader,
  StatusDot,
  TextArea,
  TextInput,
  type StatusState,
} from '@magnm/design/react';
import {
  answerGate,
  cancelWorkflow,
  checkText,
  draftPrReply,
  getProductRules,
  listAssets,
  listContent,
  listWorkflows,
  renderCard,
  startWorkflow,
  type CardRenderResult,
  type PrDraftResult,
} from '../../api/create';
import { useFallbackPoll, useStudioEvents } from '../../events';
import type {
  AssetRecord,
  CheckTextWarning,
  ContentItem,
  ProductRule,
  WorkflowRun,
} from '../../types';
import { formatTimestamp } from '../../ui/format';

type CreateKind =
  | 'post'
  | 'thread'
  | 'blog'
  | 'repurpose'
  | 'video_script'
  | 'weekly_plan'
  | 'pr_reply'
  | 'card';

interface CreateOption {
  id: CreateKind;
  title: string;
  description: string;
  meta: string;
}

const CREATE_OPTIONS: CreateOption[] = [
  { id: 'post', title: 'Social post', description: 'Multi-platform post drafted, optionally illustrated, and reviewed.', meta: 'workflow' },
  { id: 'thread', title: 'X thread', description: 'Numbered X thread checked against limits and product rules.', meta: 'workflow' },
  { id: 'blog', title: 'Blog article', description: 'Outline approval gate, section drafting, review, SEO, and repurpose.', meta: 'workflow' },
  { id: 'repurpose', title: 'Repurpose content', description: 'Turn an existing draft into X threads, LinkedIn, or YouTube copy.', meta: 'workflow' },
  { id: 'video_script', title: 'Video script', description: 'Timed scenes, voice-over, shotlist, and captions.srt.', meta: 'workflow' },
  { id: 'weekly_plan', title: 'Weekly plan', description: 'CMO proposes weekly slots with Keep / Change / Drop gates.', meta: 'workflow' },
  { id: 'pr_reply', title: 'PR / crisis reply', description: 'Draft a grounded external or client reply via @community.', meta: 'instant' },
  { id: 'card', title: 'Visual card', description: 'Render multi-ratio Palette v3 cards at zero LLM cost.', meta: 'card studio' },
];

const PLATFORMS = ['linkedin', 'x', 'instagram', 'youtube'] as const;

const CARD_FIELDS = [
  { value: 'sky', label: 'Sky' },
  { value: 'stone', label: 'Stone' },
  { value: 'ink', label: 'Ink' },
  { value: 'lime', label: 'Lime' },
  { value: 'mint', label: 'Mint' },
  { value: 'butter', label: 'Butter' },
  { value: 'coral', label: 'Coral' },
  { value: 'lilac', label: 'Lilac' },
] as const;

function mapRunState(status: string): StatusState {
  switch (status) {
    case 'running':
      return 'working';
    case 'queued':
    case 'waiting_user':
      return 'idle';
    case 'done':
      return 'online';
    case 'failed':
      return 'error';
    default:
      return 'off';
  }
}

function mapStepState(status: string): StatusState {
  switch (status) {
    case 'done':
      return 'online';
    case 'current':
      return 'working';
    case 'waiting_user':
      return 'idle';
    case 'failed':
      return 'error';
    default:
      return 'off';
  }
}

function isRunActive(status: string): boolean {
  return status === 'queued' || status === 'running' || status === 'waiting_user';
}

/** Create section: pick what to make, run workflows with live steps & gates, Card Studio, PR replies, and quick text check. */
export function CreateSection() {
  const [selectedKind, setSelectedKind] = useState<CreateKind>('post');

  const [products, setProducts] = useState<ProductRule[]>([]);
  const [assets, setAssets] = useState<AssetRecord[]>([]);
  const [contentList, setContentList] = useState<ContentItem[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowRun[]>([]);

  // Shared workflow inputs
  const [productId, setProductId] = useState('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['linkedin', 'x']);
  const [briefOrTopic, setBriefOrTopic] = useState('');
  const [sourceContentId, setSourceContentId] = useState('');
  const [selectedAssetId, setSelectedAssetId] = useState('');
  const [withImage, setWithImage] = useState(false);
  const [blogAudience, setBlogAudience] = useState('developers and technical builders');
  const [blogWords, setBlogWords] = useState('1200-2000');
  const [videoDuration, setVideoDuration] = useState('30');
  const [videoAspect, setVideoAspect] = useState('9:16');
  const [postsPerWeek, setPostsPerWeek] = useState('3');

  // Card studio inputs
  const [cardPostId, setCardPostId] = useState('custom-card');
  const [cardField, setCardField] = useState('sky');
  const [cardKicker, setCardKicker] = useState('CERO');
  const [cardBig, setCardBig] = useState('ASKS\nFIRST.');
  const [cardSerif, setCardSerif] = useState('before anything changes your files.');
  const [cardFoot, setCardFoot] = useState('magnm-org.vercel.app');
  const [cardAssetId, setCardAssetId] = useState('');
  const [cardResult, setCardResult] = useState<CardRenderResult | null>(null);

  // PR reply inputs
  const [prScenario, setPrScenario] = useState('misunderstanding');
  const [prSeverity, setPrSeverity] = useState('L1');
  const [prContext, setPrContext] = useState('');
  const [prResult, setPrResult] = useState<PrDraftResult | null>(null);
  const [prCopied, setPrCopied] = useState(false);

  // Quick text check inputs
  const [checkInput, setCheckInput] = useState('');
  const [checkPlatform, setCheckPlatform] = useState('');
  const [checkType, setCheckType] = useState('post');
  const [checkWarnings, setCheckWarnings] = useState<CheckTextWarning[] | null>(null);

  // Gate feedback & UI status
  const [gateFeedback, setGateFeedback] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      listWorkflows(20),
      getProductRules(),
      listContent(50),
      listAssets(),
    ]);

    let firstErr: string | null = null;
    const [wfRes, rulesRes, contentRes, assetsRes] = results;

    if (wfRes.status === 'fulfilled') {
      setWorkflows(Array.isArray(wfRes.value) ? wfRes.value : []);
    } else if (wfRes.reason instanceof Error) {
      firstErr = firstErr ?? wfRes.reason.message;
    }

    if (rulesRes.status === 'fulfilled') {
      setProducts(Array.isArray(rulesRes.value?.products) ? rulesRes.value.products : []);
    } else if (rulesRes.reason instanceof Error) {
      firstErr = firstErr ?? rulesRes.reason.message;
    }

    if (contentRes.status === 'fulfilled') {
      setContentList(Array.isArray(contentRes.value) ? contentRes.value : []);
    }

    if (assetsRes.status === 'fulfilled') {
      setAssets(Array.isArray(assetsRes.value) ? assetsRes.value : []);
    }

    setError(firstErr);
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const eventsState = useStudioEvents(
    useCallback(
      (eventName) => {
        if (
          eventName === 'workflow_step' ||
          eventName === 'workflow_waiting' ||
          eventName === 'workflow_done' ||
          eventName === 'workflow_failed' ||
          eventName === 'content_status'
        ) {
          void listWorkflows(20)
            .then((next) => setWorkflows(Array.isArray(next) ? next : []))
            .catch(() => {});
        }
      },
      [],
    ),
  );

  const hasActiveRun = useMemo(
    () => workflows.some((run) => isRunActive(run.status)),
    [workflows],
  );

  useFallbackPoll(
    useCallback(() => {
      void listWorkflows(20)
        .then((next) => setWorkflows(Array.isArray(next) ? next : []))
        .catch(() => {});
    }, []),
    hasActiveRun,
    eventsState,
    5000,
  );

  const selectedProduct = useMemo(
    () => products.find((p) => p.id === productId) ?? null,
    [products, productId],
  );

  const imageAssets = useMemo(
    () => assets.filter((a) => a.file_group === 'image'),
    [assets],
  );

  const togglePlatform = (plat: string) => {
    setSelectedPlatforms((prev) =>
      prev.includes(plat) ? prev.filter((p) => p !== plat) : [...prev, plat],
    );
  };

  const handleSelectKind = (nextKind: CreateKind) => {
    setSelectedKind(nextKind);
    setError(null);
    setNotice(null);
    if (nextKind === 'thread' && !selectedPlatforms.includes('x')) {
      setSelectedPlatforms(['x']);
    }
  };

  const handleStartWorkflow = async () => {
    setError(null);
    setNotice(null);
    const trimmedText = briefOrTopic.trim();
    const plats = selectedPlatforms.length > 0 ? selectedPlatforms : ['linkedin', 'x'];

    let apiKind: string = selectedKind;
    const inputs: Record<string, unknown> = {};
    if (productId) {
      inputs.product_id = productId;
    }

    if (selectedKind === 'post' || selectedKind === 'thread') {
      if (!trimmedText) {
        setError('Please enter a brief before starting.');
        return;
      }
      apiKind = 'post';
      inputs.brief = selectedKind === 'thread' ? `Write a numbered X thread: ${trimmedText}` : trimmedText;
      inputs.platforms = selectedKind === 'thread' ? (selectedPlatforms.length > 0 ? selectedPlatforms : ['x']) : plats;
      inputs.with_image = withImage;
      if (selectedKind === 'thread') {
        inputs.format = 'thread';
      }
      if (selectedAssetId) {
        inputs.asset_id = selectedAssetId;
      }
    } else if (selectedKind === 'blog') {
      if (!trimmedText) {
        setError('Please enter a blog topic before starting.');
        return;
      }
      inputs.topic = trimmedText;
      if (blogAudience.trim()) inputs.audience = blogAudience.trim();
      if (blogWords.trim()) inputs.length_words = blogWords.trim();
    } else if (selectedKind === 'repurpose') {
      const cid = sourceContentId.trim() || trimmedText;
      if (!cid) {
        setError('Please pick or enter a source content ID to repurpose.');
        return;
      }
      inputs.content_id = cid;
      inputs.targets = plats.map((p) =>
        p === 'x' ? 'x_thread' : p === 'youtube' ? 'youtube_description' : p,
      );
    } else if (selectedKind === 'video_script') {
      if (!trimmedText) {
        setError('Please enter a video topic before starting.');
        return;
      }
      inputs.topic = trimmedText;
      inputs.duration_s = Number.parseInt(videoDuration, 10) || 30;
      inputs.aspect = videoAspect;
    } else if (selectedKind === 'weekly_plan') {
      inputs.posts_per_week = Math.max(1, Number.parseInt(postsPerWeek, 10) || 3);
      inputs.platforms = plats;
    }

    setBusyId('start-workflow');
    try {
      const created = await startWorkflow(apiKind, inputs);
      setWorkflows((prev) => [created, ...prev.filter((w) => w.id !== created.id)]);
      setNotice(`Started ${created.kind} workflow (${created.id}).`);
      if (selectedKind !== 'weekly_plan') {
        setBriefOrTopic('');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start workflow.');
    } finally {
      setBusyId(null);
    }
  };

  const handleRenderCard = async () => {
    setError(null);
    setNotice(null);
    const slug = cardPostId.trim() || 'custom-card';
    setBusyId('render-card');
    try {
      const res = await renderCard({
        post_id: slug,
        field: cardField,
        kicker: cardKicker.trim(),
        big: cardBig,
        serif: cardSerif.trim(),
        foot: cardFoot.trim(),
        asset_id: cardAssetId || undefined,
        sizes: ['4x5', '1x1', '16x9', '9x16'],
      });
      if (res.error) {
        setError(res.error);
      } else {
        setCardResult(res);
        setNotice(`Rendered ${(res.rendered_files || []).length} card formats.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to render card.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDraftPrReply = async () => {
    setError(null);
    setNotice(null);
    setPrCopied(false);
    const ctx = prContext.trim();
    if (!ctx) {
      setError('Paste the incoming comment, thread, or inquiry first.');
      return;
    }
    setBusyId('pr-reply');
    try {
      const res = await draftPrReply({
        scenario: prScenario,
        severity: prSeverity,
        source_context: ctx,
      });
      setPrResult(res);
      if (!res.success && res.error) {
        setError(res.error);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to draft PR reply.');
    } finally {
      setBusyId(null);
    }
  };

  const handleCopyPrDraft = async () => {
    const text = prResult?.draft || '';
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setPrCopied(true);
    } catch {
      setError('Could not copy to clipboard.');
    }
  };

  const handleAnswerGate = async (gateId: string, option: string) => {
    const fb = (gateFeedback[gateId] || '').trim();
    const norm = option.trim().toLowerCase();
    if ((norm === 'revise' || norm === 'change') && !fb) {
      setError(`Please enter a note before choosing ${option}.`);
      return;
    }
    setError(null);
    setBusyId(gateId);
    try {
      const res = await answerGate(gateId, option, fb || null);
      setGateFeedback((prev) => ({ ...prev, [gateId]: '' }));
      setWorkflows((prev) =>
        prev.map((run) => (run.id === res.run.id ? res.run : run)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to answer gate.');
    } finally {
      setBusyId(null);
    }
  };

  const handleCancelRun = async (runId: string) => {
    setError(null);
    setBusyId(runId);
    try {
      const updated = await cancelWorkflow(runId);
      setWorkflows((prev) => prev.map((run) => (run.id === updated.id ? updated : run)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel workflow.');
    } finally {
      setBusyId(null);
    }
  };

  const handleCheckText = async () => {
    setError(null);
    setBusyId('check-text');
    try {
      const res = await checkText(
        checkInput,
        checkPlatform || undefined,
        checkType || undefined,
      );
      setCheckWarnings(Array.isArray(res.warnings) ? res.warnings : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check text.');
    } finally {
      setBusyId(null);
    }
  };

  const isWorkflowKind =
    selectedKind !== 'card' && selectedKind !== 'pr_reply';

  return (
    <div className="studio-stack">
      <SheetHeader
        page={5}
        total={9}
        subtitle="Production"
        title="Create"
        lead="Pick what to make, start workflows with inline approval gates, render cards, draft PR replies, or run quick rule checks."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? <div className="mg-notice">{notice}</div> : null}

      <section className="studio-section" aria-labelledby="create-pick-heading">
        <h2 id="create-pick-heading" className="studio-section-title">
          What to make
        </h2>
        <RowList aria-label="Content types">
          {CREATE_OPTIONS.map((opt) => (
            <Row
              key={opt.id}
              selected={selectedKind === opt.id}
              title={opt.title}
              description={opt.description}
              meta={opt.meta}
              onClick={() => handleSelectKind(opt.id)}
            />
          ))}
        </RowList>
      </section>

      <section className="studio-section" aria-labelledby="create-form-heading">
        <h2 id="create-form-heading" className="studio-section-title">
          Brief &amp; setup
        </h2>

        {isWorkflowKind ? (
          <Card>
            <div className="studio-stack-sm">
              <div className="studio-grid-2">
                <Field label="Product" htmlFor="create-product">
                  <Select
                    id="create-product"
                    value={productId}
                    onChange={(e) => setProductId(e.target.value)}
                  >
                    <option value="">General (no product filter)</option>
                    {products.map((prod) => (
                      <option key={prod.id} value={prod.id}>
                        {prod.name} ({prod.status.replace(/_/g, ' ')})
                      </option>
                    ))}
                  </Select>
                </Field>

                {(selectedKind === 'post' || selectedKind === 'thread') && (
                  <Field label="Optional source asset" htmlFor="create-asset">
                    <Select
                      id="create-asset"
                      value={selectedAssetId}
                      onChange={(e) => setSelectedAssetId(e.target.value)}
                    >
                      <option value="">None</option>
                      {assets.map((asset) => (
                        <option key={asset.id} value={asset.id}>
                          {asset.name} ({asset.file_group || 'file'})
                        </option>
                      ))}
                    </Select>
                  </Field>
                )}

                {selectedKind === 'weekly_plan' && (
                  <Field label="Posts per week" htmlFor="create-posts-per-week">
                    <TextInput
                      id="create-posts-per-week"
                      type="number"
                      min={1}
                      max={14}
                      value={postsPerWeek}
                      onChange={(e) => setPostsPerWeek(e.target.value)}
                    />
                  </Field>
                )}

                {selectedKind === 'video_script' && (
                  <div className="studio-grid-2">
                    <Field label="Duration" htmlFor="create-video-duration">
                      <Select
                        id="create-video-duration"
                        value={videoDuration}
                        onChange={(e) => setVideoDuration(e.target.value)}
                      >
                        <option value="15">15 seconds</option>
                        <option value="30">30 seconds</option>
                        <option value="60">60 seconds</option>
                      </Select>
                    </Field>
                    <Field label="Aspect ratio" htmlFor="create-video-aspect">
                      <Select
                        id="create-video-aspect"
                        value={videoAspect}
                        onChange={(e) => setVideoAspect(e.target.value)}
                      >
                        <option value="9:16">9:16 (Vertical)</option>
                        <option value="16:9">16:9 (Landscape)</option>
                      </Select>
                    </Field>
                  </div>
                )}

                {selectedKind === 'blog' && (
                  <Field label="Word target" htmlFor="create-blog-words">
                    <TextInput
                      id="create-blog-words"
                      value={blogWords}
                      onChange={(e) => setBlogWords(e.target.value)}
                      placeholder="1200-2000"
                    />
                  </Field>
                )}
              </div>

              {selectedProduct?.confidential ? (
                <div className="studio-chip-row">
                  <span className="mg-chip">CONFIDENTIAL</span>
                  <span className="studio-hint">
                    Runs on local AI only. Never sent to cloud models.
                  </span>
                </div>
              ) : null}

              {(selectedKind === 'post' ||
                selectedKind === 'thread' ||
                selectedKind === 'repurpose' ||
                selectedKind === 'weekly_plan') && (
                <div className="studio-stack-xs">
                  <span className="mg-label">
                    {selectedKind === 'repurpose' ? 'Target platforms' : 'Platforms'}
                  </span>
                  <div className="studio-chip-row">
                    {PLATFORMS.map((plat) => (
                      <Checkbox
                        key={plat}
                        label={plat}
                        checked={selectedPlatforms.includes(plat)}
                        onChange={() => togglePlatform(plat)}
                      />
                    ))}
                  </div>
                </div>
              )}

              {selectedKind === 'repurpose' ? (
                <div className="studio-grid-2">
                  <Field label="Existing draft" htmlFor="create-source-select">
                    <Select
                      id="create-source-select"
                      value={sourceContentId}
                      onChange={(e) => setSourceContentId(e.target.value)}
                    >
                      <option value="">Choose content item...</option>
                      {contentList
                        .filter((c) => c.type !== 'review')
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.title || item.id} ({item.type})
                          </option>
                        ))}
                    </Select>
                  </Field>
                  <Field label="Or source content ID" htmlFor="create-source-id">
                    <TextInput
                      id="create-source-id"
                      value={sourceContentId}
                      onChange={(e) => setSourceContentId(e.target.value)}
                      placeholder="content id"
                    />
                  </Field>
                </div>
              ) : null}

              {selectedKind === 'blog' ? (
                <Field label="Target audience" htmlFor="create-blog-audience">
                  <TextInput
                    id="create-blog-audience"
                    value={blogAudience}
                    onChange={(e) => setBlogAudience(e.target.value)}
                  />
                </Field>
              ) : null}

              {selectedKind !== 'weekly_plan' && selectedKind !== 'repurpose' ? (
                <Field
                  label={selectedKind === 'post' || selectedKind === 'thread' ? 'Brief' : 'Topic'}
                  htmlFor="create-brief"
                >
                  <TextArea
                    id="create-brief"
                    rows={3}
                    value={briefOrTopic}
                    onChange={(e) => setBriefOrTopic(e.target.value)}
                    placeholder={
                      selectedKind === 'thread'
                        ? 'Describe the X thread angle and verified facts to highlight...'
                        : 'Describe what you want the team to create...'
                    }
                  />
                </Field>
              ) : null}

              {(selectedKind === 'post' || selectedKind === 'thread') && (
                <Checkbox
                  label="Include visual card / illustration"
                  checked={withImage}
                  onChange={(e) => setWithImage(e.target.checked)}
                />
              )}

              <div className="studio-btn-group">
                <PillButton
                  disabled={busyId === 'start-workflow'}
                  onClick={() => void handleStartWorkflow()}
                >
                  {busyId === 'start-workflow' ? 'Starting...' : 'Start workflow'}
                </PillButton>
              </div>
            </div>
          </Card>
        ) : null}

        {selectedKind === 'card' ? (
          <Card>
            <div className="studio-stack-sm">
              <div className="studio-grid-2">
                <Field label="Campaign / slug ID" htmlFor="card-post-id">
                  <TextInput
                    id="card-post-id"
                    value={cardPostId}
                    onChange={(e) => setCardPostId(e.target.value)}
                  />
                </Field>
                <Field label="Palette field" htmlFor="card-field">
                  <Select
                    id="card-field"
                    value={cardField}
                    onChange={(e) => setCardField(e.target.value)}
                  >
                    {CARD_FIELDS.map((f) => (
                      <option key={f.value} value={f.value}>
                        {f.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Top kicker" htmlFor="card-kicker">
                  <TextInput
                    id="card-kicker"
                    value={cardKicker}
                    onChange={(e) => setCardKicker(e.target.value)}
                  />
                </Field>
                <Field label="Optional image asset" htmlFor="card-asset">
                  <Select
                    id="card-asset"
                    value={cardAssetId}
                    onChange={(e) => setCardAssetId(e.target.value)}
                  >
                    <option value="">Typography only</option>
                    {imageAssets.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

              <Field label="Display headline (up to 3 lines)" htmlFor="card-big">
                <TextArea
                  id="card-big"
                  rows={2}
                  value={cardBig}
                  onChange={(e) => setCardBig(e.target.value)}
                />
              </Field>

              <div className="studio-grid-2">
                <Field label="Italic serif line" htmlFor="card-serif">
                  <TextInput
                    id="card-serif"
                    value={cardSerif}
                    onChange={(e) => setCardSerif(e.target.value)}
                  />
                </Field>
                <Field label="Footer URL" htmlFor="card-foot">
                  <TextInput
                    id="card-foot"
                    value={cardFoot}
                    onChange={(e) => setCardFoot(e.target.value)}
                  />
                </Field>
              </div>

              <div className="studio-btn-group">
                <PillButton
                  disabled={busyId === 'render-card'}
                  onClick={() => void handleRenderCard()}
                >
                  {busyId === 'render-card' ? 'Rendering...' : 'Render cards'}
                </PillButton>
              </div>

              {cardResult?.success ? (
                <div className="studio-stack-sm">
                  <div className="studio-section-bar">
                    <span className="studio-mono">
                      Rendered {(cardResult.rendered_files || []).length} files for{' '}
                      {cardResult.post_id}
                    </span>
                    <a
                      className="studio-inline-link"
                      href={`#/review/${encodeURIComponent(cardResult.post_id)}`}
                    >
                      Open in Review
                    </a>
                  </div>
                  <div className="studio-card-gallery">
                    {(cardResult.rendered_files || []).map((file) => (
                      <div key={file} className="studio-card-thumb">
                        <img
                          src={`/api/asset/${encodeURIComponent(cardResult.post_id)}/${encodeURIComponent(file)}`}
                          alt={`${cardResult.post_id} ${file}`}
                        />
                        <span className="studio-mono">{file}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </Card>
        ) : null}

        {selectedKind === 'pr_reply' ? (
          <Card>
            <div className="studio-stack-sm">
              <div className="studio-grid-2">
                <Field label="Scenario" htmlFor="pr-scenario">
                  <Select
                    id="pr-scenario"
                    value={prScenario}
                    onChange={(e) => setPrScenario(e.target.value)}
                  >
                    <option value="freelance_inquiry">Freelance client / inquiry</option>
                    <option value="misunderstanding">Technical misunderstanding</option>
                    <option value="valid_bug">Valid bug report</option>
                    <option value="fair_criticism">Fair criticism</option>
                    <option value="unfair">Unfair / bad-faith comment</option>
                  </Select>
                </Field>
                <Field label="Severity" htmlFor="pr-severity">
                  <Select
                    id="pr-severity"
                    value={prSeverity}
                    onChange={(e) => setPrSeverity(e.target.value)}
                  >
                    <option value="L1">L1 Minor</option>
                    <option value="L2">L2 Notable (CEO approves)</option>
                    <option value="L3">L3 Serious (Pause promos, CEO decides)</option>
                  </Select>
                </Field>
              </div>

              <Field label="Incoming comment or inquiry" htmlFor="pr-context">
                <TextArea
                  id="pr-context"
                  rows={4}
                  value={prContext}
                  onChange={(e) => setPrContext(e.target.value)}
                  placeholder="Paste the comment, thread, press question, or client inquiry..."
                />
              </Field>

              <div className="studio-section-bar">
                <span className="studio-hint">
                  Never posts automatically. Copy and review before sending.
                </span>
                <PillButton
                  disabled={busyId === 'pr-reply'}
                  onClick={() => void handleDraftPrReply()}
                >
                  {busyId === 'pr-reply' ? 'Drafting...' : 'Draft reply'}
                </PillButton>
              </div>

              {prResult?.draft ? (
                <div className="studio-stack-sm">
                  <div className="studio-section-bar">
                    <span className="studio-mono">
                      {prResult.provider || 'gateway'} · {prResult.model_used || 'unknown'}
                    </span>
                    <PillButton
                      variant="outline"
                      size="sm"
                      onClick={() => void handleCopyPrDraft()}
                    >
                      {prCopied ? 'Copied' : 'Copy reply'}
                    </PillButton>
                  </div>
                  <pre className="studio-pre">{prResult.draft}</pre>
                </div>
              ) : null}
            </div>
          </Card>
        ) : null}
      </section>

      <section className="studio-section" aria-labelledby="create-runs-heading">
        <div className="studio-section-bar">
          <h2 id="create-runs-heading" className="studio-section-title">
            Workflow runs
          </h2>
          <PillButton variant="quiet" size="sm" onClick={() => void loadData()}>
            Refresh
          </PillButton>
        </div>

        {loading && workflows.length === 0 ? (
          <div className="mg-empty">Loading workflow runs.</div>
        ) : workflows.length === 0 ? (
          <div className="mg-empty">
            No workflow runs yet. Pick a format above to start creating.
          </div>
        ) : (
          <div className="studio-stack-sm">
            {workflows.map((run) => {
              const pendingGates = (run.gates || []).filter((g) => g.status === 'pending');
              const active = isRunActive(run.status);
              const summaryText = String(
                run.inputs?.brief || run.inputs?.topic || run.inputs?.content_id || run.kind,
              );
              return (
                <Card key={run.id} outline>
                  <div className="studio-stack-sm">
                    <div className="studio-card-head">
                      <div>
                        <div className="mg-label">
                          {run.kind}
                          {run.created_at ? ` · ${formatTimestamp(run.created_at)}` : ''}
                        </div>
                        <div className="studio-card-title">{summaryText}</div>
                      </div>
                      <div className="studio-row-actions">
                        <StatusDot
                          state={mapRunState(run.status)}
                          label={run.status}
                          live={active}
                        />
                        {active ? (
                          <PillButton
                            variant="outline"
                            size="sm"
                            disabled={busyId === run.id}
                            onClick={() => void handleCancelRun(run.id)}
                          >
                            Cancel
                          </PillButton>
                        ) : null}
                      </div>
                    </div>

                    {run.steps && run.steps.length > 0 ? (
                      <div className="studio-step-list" aria-label={`Steps for ${run.id}`}>
                        {run.steps.map((st) => (
                          <StatusDot
                            key={`${run.id}-step-${st.index}`}
                            state={mapStepState(st.status)}
                            label={`${st.index + 1}. ${st.name}`}
                            live={st.status === 'current'}
                          />
                        ))}
                      </div>
                    ) : null}

                    {run.error ? (
                      <div className="mg-notice" role="alert">
                        {run.error}
                      </div>
                    ) : null}

                    {pendingGates.map((gate) => (
                      <div key={gate.id} className="studio-gate-box">
                        <div className="mg-label">Approval gate</div>
                        <div className="studio-card-title">{gate.question}</div>
                        <TextInput
                          aria-label={`Gate note for ${gate.id}`}
                          placeholder="Revision note (required for Revise or Change)"
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
                    ))}

                    {run.content_ids && run.content_ids.length > 0 ? (
                      <div className="studio-chip-row">
                        <span className="mg-label">Drafts</span>
                        {run.content_ids.map((cid) => (
                          <a
                            key={cid}
                            className="mg-chip"
                            href={`#/review/${encodeURIComponent(cid)}`}
                          >
                            Review {cid}
                          </a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <section className="studio-section" aria-labelledby="create-check-heading">
        <h2 id="create-check-heading" className="studio-section-title">
          Quick text check
        </h2>
        <Card>
          <div className="studio-stack-sm">
            <div className="studio-grid-2">
              <Field label="Platform limits (optional)" htmlFor="check-platform">
                <Select
                  id="check-platform"
                  value={checkPlatform}
                  onChange={(e) => setCheckPlatform(e.target.value)}
                >
                  <option value="">Product rules only</option>
                  <option value="x">X (280 chars, max 2 hashtags)</option>
                  <option value="linkedin">LinkedIn (3,000 chars)</option>
                  <option value="instagram">Instagram (2,200 chars)</option>
                  <option value="youtube">YouTube</option>
                  <option value="blog">Blog (1,200-2,000 words)</option>
                </Select>
              </Field>
              <Field label="Format" htmlFor="check-type">
                <Select
                  id="check-type"
                  value={checkType}
                  onChange={(e) => setCheckType(e.target.value)}
                >
                  <option value="post">Post</option>
                  <option value="thread">Thread</option>
                </Select>
              </Field>
            </div>

            <Field label="Draft text to check" htmlFor="check-text-input">
              <TextArea
                id="check-text-input"
                rows={4}
                value={checkInput}
                onChange={(e) => setCheckInput(e.target.value)}
                placeholder="Paste copy here to check product rules, blocked words, and platform limits..."
              />
            </Field>

            <div className="studio-btn-group">
              <PillButton
                variant="outline"
                disabled={busyId === 'check-text' || !checkInput.trim()}
                onClick={() => void handleCheckText()}
              >
                {busyId === 'check-text' ? 'Checking...' : 'Check text'}
              </PillButton>
            </div>

            {checkWarnings !== null ? (
              checkWarnings.length === 0 ? (
                <div className="mg-notice">
                  No product rule or platform limit warnings found.
                </div>
              ) : (
                <RowList aria-label="Reviewer warnings">
                  {checkWarnings.map((w, idx) => (
                    <Row
                      key={`${w.product || 'warn'}-${idx}`}
                      title={w.message}
                      meta={
                        <StatusDot
                          state="idle"
                          label={w.product ? `${w.product} · ${w.severity}` : w.severity}
                        />
                      }
                    />
                  ))}
                </RowList>
              )
            ) : null}
          </div>
        </Card>
      </section>
    </div>
  );
}
