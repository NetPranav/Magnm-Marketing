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
  TextArea,
  TextInput,
} from '@magnm/design/react';
import {
  addRepo,
  createFact,
  decideFact,
  getProductRules,
  listFacts,
  listRepos,
  listShipped,
  removeRepo,
  saveProductRule,
  syncRepo,
} from '../../api/repos';
import { useStudioEvents } from '../../events';
import type {
  ProductFact,
  ProductRule,
  ProductRulesConfig,
  RepoRecord,
  ShippedItem,
} from '../../types';
import { formatTimestamp, isSafeHttpUrl } from '../../ui/format';

const STATUS_LABELS: Record<string, string> = {
  released: 'Released',
  in_development: 'In development',
  secret: 'Secret',
};

const SHOW_LABELS: Record<string, string> = {
  everything: 'Everything',
  text_only: 'Text only',
  teasers_only: 'Teasers only',
  nothing: 'Nothing',
};

function isLazyLayoutRule(rule: ProductRule | null | undefined): boolean {
  if (!rule) return false;
  return rule.id.toLowerCase() === 'lazylayout' || rule.name.toLowerCase() === 'lazylayout';
}

/** Repos section: connected repos, shipped this week per repo, fact approvals, and product rules. */
export function ReposSection() {
  const [repos, setRepos] = useState<RepoRecord[]>([]);
  const [shipped, setShipped] = useState<ShippedItem[]>([]);
  const [pendingFacts, setPendingFacts] = useState<ProductFact[]>([]);
  const [rulesConfig, setRulesConfig] = useState<ProductRulesConfig | null>(null);

  const [factEdits, setFactEdits] = useState<Record<string, string>>({});
  const [manualProductId, setManualProductId] = useState('cero');
  const [manualFactText, setManualFactText] = useState('');
  const [manualFactUrl, setManualFactUrl] = useState('');

  // Add repo dialog state
  const [addOpen, setAddOpen] = useState(false);
  const [repoKind, setRepoKind] = useState<'local' | 'github'>('local');
  const [repoName, setRepoName] = useState('');
  const [repoLocalPath, setRepoLocalPath] = useState('');
  const [repoGithub, setRepoGithub] = useState('');
  const [repoProductId, setRepoProductId] = useState('');
  const [repoBranch, setRepoBranch] = useState('');
  const [repoStatus, setRepoStatus] = useState('in_development');
  const [repoConfidential, setRepoConfidential] = useState(false);
  const [repoAngle, setRepoAngle] = useState('');
  const [addError, setAddError] = useState<string | null>(null);

  // Confirm remove dialog state
  const [confirmRemove, setConfirmRemove] = useState<RepoRecord | null>(null);

  // Edit product rule dialog state
  const [editingRule, setEditingRule] = useState<ProductRule | null>(null);
  const [ruleStatus, setRuleStatus] = useState('released');
  const [ruleShow, setRuleShow] = useState('everything');
  const [ruleBlocked, setRuleBlocked] = useState('');
  const [ruleConfidential, setRuleConfidential] = useState(false);
  const [ruleNotes, setRuleNotes] = useState('');
  const [confirmLazyOverride, setConfirmLazyOverride] = useState(false);

  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setError(null);
    const results = await Promise.allSettled([
      listRepos(),
      listShipped(7),
      listFacts('pending'),
      getProductRules(),
    ]);

    let firstErr: string | null = null;
    const [reposRes, shippedRes, factsRes, rulesRes] = results;

    if (reposRes.status === 'fulfilled') {
      setRepos(Array.isArray(reposRes.value) ? reposRes.value : []);
    } else if (reposRes.reason instanceof Error) {
      firstErr = firstErr ?? reposRes.reason.message;
    }

    if (shippedRes.status === 'fulfilled') {
      setShipped(Array.isArray(shippedRes.value) ? shippedRes.value : []);
    } else if (shippedRes.reason instanceof Error) {
      firstErr = firstErr ?? shippedRes.reason.message;
    }

    if (factsRes.status === 'fulfilled') {
      const nextFacts = Array.isArray(factsRes.value) ? factsRes.value : [];
      setPendingFacts(nextFacts);
      setFactEdits((prev) => {
        const next: Record<string, string> = {};
        for (const f of nextFacts) {
          next[f.id] = prev[f.id] !== undefined ? prev[f.id] : f.text;
        }
        return next;
      });
    } else if (factsRes.reason instanceof Error) {
      firstErr = firstErr ?? factsRes.reason.message;
    }

    if (rulesRes.status === 'fulfilled') {
      setRulesConfig(rulesRes.value ?? null);
    } else if (rulesRes.reason instanceof Error) {
      firstErr = firstErr ?? rulesRes.reason.message;
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
        if (eventName === 'repo_synced') {
          void loadAll();
        }
      },
      [loadAll],
    ),
  );

  const shippedByRepo = useMemo(() => {
    const groups = new Map<string, ShippedItem[]>();
    for (const r of repos) {
      groups.set(r.id, []);
    }
    for (const item of shipped) {
      const list = groups.get(item.repo_id) ?? [];
      list.push(item);
      groups.set(item.repo_id, list);
    }
    return Array.from(groups.entries()).map(([repoId, items]) => {
      const repo = repos.find((r) => r.id === repoId);
      return {
        repoId,
        repoName: repo?.name || repoId,
        items,
      };
    });
  }, [repos, shipped]);

  const handleSyncRepo = async (repoId: string) => {
    setError(null);
    setNotice(null);
    setBusyId(`sync-${repoId}`);
    try {
      const res = await syncRepo(repoId);
      await loadAll();
      setNotice(
        res.success
          ? `Synced ${repoId}.`
          : `Sync finished for ${repoId} with warnings.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to sync repository.');
    } finally {
      setBusyId(null);
    }
  };

  const handleConfirmRemove = async () => {
    if (!confirmRemove) return;
    const target = confirmRemove;
    setBusyId(`remove-${target.id}`);
    setError(null);
    try {
      await removeRepo(target.id);
      setRepos((prev) => prev.filter((r) => r.id !== target.id));
      setConfirmRemove(null);
      setNotice(`Removed ${target.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove repository.');
    } finally {
      setBusyId(null);
    }
  };

  const resetAddForm = () => {
    setRepoKind('local');
    setRepoName('');
    setRepoLocalPath('');
    setRepoGithub('');
    setRepoProductId('');
    setRepoBranch('');
    setRepoStatus('in_development');
    setRepoConfidential(false);
    setRepoAngle('');
    setAddError(null);
  };

  const handleAddRepo = async () => {
    setAddError(null);
    setBusyId('add-repo');
    try {
      const payload: Parameters<typeof addRepo>[0] = {
        kind: repoKind,
        name: repoName.trim(),
        status: repoStatus,
        confidential: repoConfidential,
      };
      if (repoKind === 'local') {
        payload.local_path = repoLocalPath.trim();
      } else {
        payload.github = repoGithub.trim();
      }
      if (repoProductId.trim()) payload.product_id = repoProductId.trim();
      if (repoBranch.trim()) payload.branch = repoBranch.trim();
      if (repoAngle.trim()) payload.marketing_angle = repoAngle.trim();

      const res = await addRepo(payload);
      setAddOpen(false);
      resetAddForm();
      await loadAll();
      setNotice(`Connected ${res.repo?.name || 'repository'}.`);
    } catch (err) {
      setAddError(err instanceof Error ? err.message : 'Failed to add repository.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDecideFact = async (factId: string, status: 'approved' | 'rejected') => {
    setError(null);
    setBusyId(factId);
    try {
      const editedText = (factEdits[factId] ?? '').trim();
      await decideFact(
        factId,
        status,
        status === 'approved' && editedText ? editedText : undefined,
      );
      setPendingFacts((prev) => prev.filter((f) => f.id !== factId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update product fact.');
    } finally {
      setBusyId(null);
    }
  };

  const handleCreateFact = async () => {
    const pid = manualProductId.trim();
    const text = manualFactText.trim();
    if (!pid || !text) {
      setError('Product ID and fact text are required.');
      return;
    }
    setError(null);
    setBusyId('create-fact');
    try {
      await createFact({
        product_id: pid,
        text,
        source_url: manualFactUrl.trim() || null,
      });
      setManualFactText('');
      setManualFactUrl('');
      setNotice(`Added approved fact for ${pid}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add fact.');
    } finally {
      setBusyId(null);
    }
  };

  const openEditRule = (rule: ProductRule) => {
    setEditingRule(rule);
    setRuleStatus(rule.status);
    setRuleShow(rule.show);
    setRuleBlocked((rule.blocked_terms || []).join(', '));
    setRuleConfidential(Boolean(rule.confidential));
    setRuleNotes(rule.notes || '');
    setConfirmLazyOverride(false);
  };

  const lazyRuleChanged =
    isLazyLayoutRule(editingRule) &&
    (ruleStatus !== 'in_development' || ruleShow !== 'teasers_only');

  const commitSaveRule = async () => {
    if (!editingRule) return;
    setError(null);
    setBusyId(`rule-${editingRule.id}`);
    try {
      const blockedTerms = ruleBlocked
        .split(/[,;\n]+/)
        .map((t) => t.trim())
        .filter(Boolean);
      const res = await saveProductRule({
        product_id: editingRule.id,
        status: ruleStatus,
        show: ruleShow,
        blocked_terms: blockedTerms,
        confidential: ruleConfidential,
        notes: ruleNotes.trim(),
      });
      if (res.rules) {
        setRulesConfig(res.rules);
      } else {
        await loadAll();
      }
      setNotice(`Saved product rules for ${editingRule.name}.`);
      setConfirmLazyOverride(false);
      setEditingRule(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save product rules.');
    } finally {
      setBusyId(null);
    }
  };

  const handleRequestSaveRule = () => {
    if (lazyRuleChanged && !confirmLazyOverride) {
      setConfirmLazyOverride(true);
      return;
    }
    void commitSaveRule();
  };

  const products = rulesConfig?.products || [];
  const showLevels = rulesConfig?.show_levels || {};

  return (
    <div className="studio-stack">
      <SheetHeader
        page={9}
        total={10}
        subtitle="Code & Ground Truth"
        title="Repos"
        lead="Connected repositories, what shipped this week, product facts waiting for sign-off, and per-product rules."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? <div className="mg-notice">{notice}</div> : null}

      <section className="studio-section" aria-labelledby="repos-connected-heading">
        <div className="studio-section-bar">
          <h2 id="repos-connected-heading" className="studio-section-title">
            Connected repos ({repos.length})
          </h2>
          <PillButton
            size="sm"
            onClick={() => {
              resetAddForm();
              setAddOpen(true);
            }}
          >
            Add repo
          </PillButton>
        </div>

        {loading && repos.length === 0 ? (
          <div className="mg-empty">Loading connected repositories.</div>
        ) : repos.length === 0 ? (
          <div className="mg-empty">
            No repositories connected yet. Add a local folder or GitHub repository.
          </div>
        ) : (
          <div className="studio-stack-sm">
            {repos.map((repo) => {
              const target =
                repo.kind === 'github' ? repo.github || '' : repo.local_path || '';
              return (
                <Card key={repo.id} outline>
                  <div className="studio-stack-sm">
                    <div className="studio-card-head">
                      <div className="studio-stack-xs">
                        <div className="studio-chip-row">
                          <span className="studio-card-title">{repo.name}</span>
                          <span className="mg-chip">{repo.kind}</span>
                          {repo.status ? (
                            <span className="mg-chip">
                              {STATUS_LABELS[repo.status] || repo.status}
                            </span>
                          ) : null}
                          {repo.version ? (
                            <span className="mg-chip">v{repo.version}</span>
                          ) : null}
                          {repo.confidential ? (
                            <span className="mg-chip">CONFIDENTIAL</span>
                          ) : null}
                        </div>
                        <span className="studio-mono-wrap">
                          {target || repo.id} ·{' '}
                          {repo.last_sync
                            ? `Synced ${formatTimestamp(repo.last_sync)}`
                            : 'Never synced'}
                        </span>
                      </div>

                      <div className="studio-row-actions">
                        <PillButton
                          variant="outline"
                          size="sm"
                          disabled={busyId === `sync-${repo.id}`}
                          onClick={() => void handleSyncRepo(repo.id)}
                        >
                          {busyId === `sync-${repo.id}` ? 'Syncing...' : 'Sync now'}
                        </PillButton>
                        <PillButton
                          variant="danger"
                          size="sm"
                          onClick={() => setConfirmRemove(repo)}
                        >
                          Remove
                        </PillButton>
                      </div>
                    </div>

                    {repo.confidential ? (
                      <div className="studio-hint">
                        Confidential repository: uses local AI only, never sent to cloud models.
                      </div>
                    ) : null}

                    {repo.rate_limited && repo.rate_limit_message ? (
                      <div className="mg-notice">{repo.rate_limit_message}</div>
                    ) : null}

                    {repo.recent_commits && repo.recent_commits.length > 0 ? (
                      <div className="studio-stack-xs">
                        {repo.recent_commits.slice(0, 3).map((c, idx) => (
                          <div key={`${repo.id}-c-${idx}`} className="studio-mono-wrap">
                            {c.hash || 'commit'} · {c.message}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="studio-hint">No recent commits synced.</div>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <section className="studio-section" aria-labelledby="repos-shipped-heading">
        <h2 id="repos-shipped-heading" className="studio-section-title">
          Shipped this week
        </h2>

        {loading && shipped.length === 0 && repos.length === 0 ? (
          <div className="mg-empty">Loading shipped items.</div>
        ) : shippedByRepo.length === 0 ? (
          <div className="mg-empty">Nothing shipped in the last 7 days.</div>
        ) : (
          <div className="studio-stack-sm">
            {shippedByRepo.map((group) => (
              <Card key={group.repoId}>
                <div className="studio-stack-sm">
                  <div className="studio-section-bar">
                    <span className="studio-card-title">{group.repoName}</span>
                    <span className="studio-mono">{group.items.length} shipped</span>
                  </div>
                  {group.items.length === 0 ? (
                    <div className="studio-hint">Nothing shipped in the last 7 days.</div>
                  ) : (
                    <RowList aria-label={`Shipped for ${group.repoName}`}>
                      {group.items.slice(0, 10).map((item, idx) => (
                        <Row
                          key={`${group.repoId}-ship-${idx}`}
                          title={
                            isSafeHttpUrl(item.url) ? (
                              <a
                                href={item.url || undefined}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {item.title}
                              </a>
                            ) : (
                              item.title
                            )
                          }
                          description={item.kind}
                          meta={formatTimestamp(item.date)}
                        />
                      ))}
                    </RowList>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section className="studio-section" aria-labelledby="repos-facts-heading">
        <h2 id="repos-facts-heading" className="studio-section-title">
          Product facts waiting for approval ({pendingFacts.length})
        </h2>

        {loading && pendingFacts.length === 0 ? (
          <div className="mg-empty">Loading pending product facts.</div>
        ) : pendingFacts.length === 0 ? (
          <div className="mg-empty">No pending product facts awaiting approval.</div>
        ) : (
          <div className="studio-stack-sm">
            {pendingFacts.map((fact) => (
              <Card key={fact.id} outline>
                <div className="studio-stack-sm">
                  <div className="studio-section-bar">
                    <div className="studio-chip-row">
                      <span className="mg-chip">{fact.product_id}</span>
                      <span className="studio-mono">{fact.source_kind}</span>
                      {isSafeHttpUrl(fact.source_url) ? (
                        <a
                          className="studio-inline-link"
                          href={fact.source_url || undefined}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Source
                        </a>
                      ) : null}
                    </div>
                    {fact.created_by ? (
                      <span className="studio-mono">{fact.created_by}</span>
                    ) : null}
                  </div>

                  <Field label="Fact text (edit before approving if needed)" htmlFor={`fact-${fact.id}`}>
                    <TextInput
                      id={`fact-${fact.id}`}
                      value={factEdits[fact.id] ?? fact.text}
                      onChange={(e) =>
                        setFactEdits((prev) => ({ ...prev, [fact.id]: e.target.value }))
                      }
                    />
                  </Field>

                  <div className="studio-btn-group">
                    <PillButton
                      size="sm"
                      disabled={busyId === fact.id}
                      onClick={() => void handleDecideFact(fact.id, 'approved')}
                    >
                      Approve
                    </PillButton>
                    <PillButton
                      variant="outline"
                      size="sm"
                      disabled={busyId === fact.id}
                      onClick={() => void handleDecideFact(fact.id, 'rejected')}
                    >
                      Reject
                    </PillButton>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}

        <Card>
          <div className="studio-stack-sm">
            <div className="mg-label">Add verified fact</div>
            <div className="studio-grid-2">
              <Field label="Product ID" htmlFor="manual-fact-product">
                <TextInput
                  id="manual-fact-product"
                  value={manualProductId}
                  onChange={(e) => setManualProductId(e.target.value)}
                  placeholder="cero"
                />
              </Field>
              <Field label="Source URL (optional)" htmlFor="manual-fact-url">
                <TextInput
                  id="manual-fact-url"
                  value={manualFactUrl}
                  onChange={(e) => setManualFactUrl(e.target.value)}
                  placeholder="https://github.com/..."
                />
              </Field>
            </div>
            <div className="studio-inline-form">
              <TextInput
                aria-label="New verified product fact"
                placeholder="Verified product fact..."
                value={manualFactText}
                onChange={(e) => setManualFactText(e.target.value)}
              />
              <PillButton
                size="sm"
                disabled={busyId === 'create-fact'}
                onClick={() => void handleCreateFact()}
              >
                Add fact
              </PillButton>
            </div>
          </div>
        </Card>
      </section>

      <section className="studio-section" aria-labelledby="repos-rules-heading">
        <h2 id="repos-rules-heading" className="studio-section-title">
          Product rules
        </h2>

        {loading && products.length === 0 ? (
          <div className="mg-empty">Loading product rules.</div>
        ) : products.length === 0 ? (
          <div className="mg-empty">No product rules configured.</div>
        ) : (
          <div className="studio-stack-sm">
            {products.map((rule) => (
              <Card key={rule.id} outline>
                <div className="studio-stack-sm">
                  <div className="studio-card-head">
                    <div className="studio-stack-xs">
                      <div className="studio-chip-row">
                        <span className="studio-card-title">{rule.name}</span>
                        <span className="mg-chip">
                          {STATUS_LABELS[rule.status] || rule.status}
                        </span>
                        <span className="mg-chip">
                          {SHOW_LABELS[rule.show] || rule.show}
                        </span>
                        {rule.confidential ? (
                          <span className="mg-chip">CONFIDENTIAL</span>
                        ) : null}
                      </div>
                      {showLevels[rule.show] ? (
                        <span className="studio-hint">{showLevels[rule.show]}</span>
                      ) : null}
                    </div>
                    <PillButton
                      variant="outline"
                      size="sm"
                      onClick={() => openEditRule(rule)}
                    >
                      Edit rules
                    </PillButton>
                  </div>

                  <div className="studio-mono-wrap">
                    Blocked words:{' '}
                    {rule.blocked_terms && rule.blocked_terms.length > 0
                      ? rule.blocked_terms.join(', ')
                      : 'None'}
                  </div>

                  {rule.confidential ? (
                    <div className="studio-hint">
                      Confidential product: uses local AI only, never sent to cloud models.
                    </div>
                  ) : null}

                  {rule.notes ? <p className="mg-sub">{rule.notes}</p> : null}
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>

      <Dialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add repository"
        subtitle="Repos"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setAddOpen(false)}>
              Cancel
            </PillButton>
            <PillButton
              size="sm"
              disabled={busyId === 'add-repo'}
              onClick={() => void handleAddRepo()}
            >
              {busyId === 'add-repo' ? 'Connecting...' : 'Add repository'}
            </PillButton>
          </>
        }
      >
        <div className="studio-stack-sm">
          {addError ? (
            <div className="mg-notice" role="alert">
              {addError}
            </div>
          ) : null}
          <div className="studio-grid-2">
            <Field label="Source" htmlFor="repo-kind">
              <Select
                id="repo-kind"
                value={repoKind}
                onChange={(e) => setRepoKind(e.target.value as 'local' | 'github')}
              >
                <option value="local">Local folder</option>
                <option value="github">GitHub (public or token)</option>
              </Select>
            </Field>
            <Field label="Project name" htmlFor="repo-name">
              <TextInput
                id="repo-name"
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                placeholder="Cero Terminal"
              />
            </Field>
          </div>

          {repoKind === 'local' ? (
            <Field label="Local folder path" htmlFor="repo-local-path">
              <TextInput
                id="repo-local-path"
                value={repoLocalPath}
                onChange={(e) => setRepoLocalPath(e.target.value)}
                placeholder="/path/to/repo"
              />
            </Field>
          ) : (
            <Field
              label="GitHub repository (owner/repo or URL)"
              htmlFor="repo-github"
              hint="Private repos use GITHUB_TOKEN configured in Settings > Keys."
            >
              <TextInput
                id="repo-github"
                value={repoGithub}
                onChange={(e) => setRepoGithub(e.target.value)}
                placeholder="owner/repo"
              />
            </Field>
          )}

          <div className="studio-grid-2">
            <Field label="Product ID (optional)" htmlFor="repo-product-id">
              <TextInput
                id="repo-product-id"
                value={repoProductId}
                onChange={(e) => setRepoProductId(e.target.value)}
                placeholder="cero"
              />
            </Field>
            <Field label="Branch (optional)" htmlFor="repo-branch">
              <TextInput
                id="repo-branch"
                value={repoBranch}
                onChange={(e) => setRepoBranch(e.target.value)}
                placeholder="main"
              />
            </Field>
          </div>

          <Field label="Lifecycle status" htmlFor="repo-status">
            <Select
              id="repo-status"
              value={repoStatus}
              onChange={(e) => setRepoStatus(e.target.value)}
            >
              <option value="in_development">In development</option>
              <option value="released">Released</option>
            </Select>
          </Field>

          <Checkbox
            label="Confidential (local AI only, never sent to cloud models)"
            checked={repoConfidential}
            onChange={(e) => setRepoConfidential(e.target.checked)}
          />

          <Field label="Marketing angle (optional)" htmlFor="repo-angle">
            <TextArea
              id="repo-angle"
              rows={2}
              value={repoAngle}
              onChange={(e) => setRepoAngle(e.target.value)}
            />
          </Field>
        </div>
      </Dialog>

      <Dialog
        open={Boolean(confirmRemove)}
        onClose={() => setConfirmRemove(null)}
        title="Remove repository"
        subtitle="Repos"
        actions={
          <>
            <PillButton variant="quiet" size="sm" onClick={() => setConfirmRemove(null)}>
              Cancel
            </PillButton>
            <PillButton
              variant="danger"
              size="sm"
              disabled={Boolean(confirmRemove && busyId === `remove-${confirmRemove.id}`)}
              onClick={() => void handleConfirmRemove()}
            >
              Remove repo
            </PillButton>
          </>
        }
      >
        {confirmRemove ? (
          <p className="mg-sub">
            Disconnect <span className="studio-mono">{confirmRemove.name}</span> from
            Magnm Studio? Local files on disk are not deleted.
          </p>
        ) : null}
      </Dialog>

      <Dialog
        open={Boolean(editingRule)}
        onClose={() => {
          setConfirmLazyOverride(false);
          setEditingRule(null);
        }}
        title={editingRule ? `Rules for ${editingRule.name}` : 'Edit product rules'}
        subtitle="Product rules"
        actions={
          confirmLazyOverride ? (
            <>
              <PillButton
                variant="quiet"
                size="sm"
                onClick={() => setConfirmLazyOverride(false)}
              >
                Back
              </PillButton>
              <PillButton
                size="sm"
                disabled={Boolean(editingRule && busyId === `rule-${editingRule.id}`)}
                onClick={() => void commitSaveRule()}
              >
                Confirm override &amp; save
              </PillButton>
            </>
          ) : (
            <>
              <PillButton
                variant="quiet"
                size="sm"
                onClick={() => setEditingRule(null)}
              >
                Cancel
              </PillButton>
              <PillButton
                size="sm"
                disabled={Boolean(editingRule && busyId === `rule-${editingRule.id}`)}
                onClick={handleRequestSaveRule}
              >
                Save rules
              </PillButton>
            </>
          )
        }
      >
        {editingRule ? (
          <div className="studio-stack-sm">
            {lazyRuleChanged ? (
              <div className="mg-notice" role="alert">
                Warning: LazyLayout is unreleased and should remain In development with
                Teasers only. Changing this allows agents to mention or show more than
                teasers.
              </div>
            ) : null}

            <div className="studio-grid-2">
              <Field label="Status" htmlFor="rule-status">
                <Select
                  id="rule-status"
                  value={ruleStatus}
                  onChange={(e) => {
                    setConfirmLazyOverride(false);
                    setRuleStatus(e.target.value);
                  }}
                >
                  <option value="released">Released</option>
                  <option value="in_development">In development</option>
                  <option value="secret">Secret</option>
                </Select>
              </Field>
              <Field label="What may be shown" htmlFor="rule-show">
                <Select
                  id="rule-show"
                  value={ruleShow}
                  onChange={(e) => {
                    setConfirmLazyOverride(false);
                    setRuleShow(e.target.value);
                  }}
                >
                  <option value="everything">Everything</option>
                  <option value="text_only">Text only</option>
                  <option value="teasers_only">Teasers only</option>
                  <option value="nothing">Nothing</option>
                </Select>
              </Field>
            </div>

            <Field
              label="Blocked words (comma-separated)"
              htmlFor="rule-blocked-words"
            >
              <TextInput
                id="rule-blocked-words"
                value={ruleBlocked}
                onChange={(e) => setRuleBlocked(e.target.value)}
                placeholder="ROS, robotics"
              />
            </Field>

            <Checkbox
              label="Confidential (local AI only, never sent to cloud models)"
              checked={ruleConfidential}
              onChange={(e) => setRuleConfidential(e.target.checked)}
            />

            <Field label="Notes" htmlFor="rule-notes">
              <TextArea
                id="rule-notes"
                rows={3}
                value={ruleNotes}
                onChange={(e) => setRuleNotes(e.target.value)}
              />
            </Field>
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
