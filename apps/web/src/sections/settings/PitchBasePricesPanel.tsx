import { useCallback, useEffect, useState } from 'react';
import { Card, PillButton, TextInput } from '@magnm/design/react';
import { getAccount, type AccountState } from '../../api/account';
import {
  formatRupees,
  getPitchCatalog,
  getPitchOutbox,
  resetBasePrice,
  setBasePrice,
  type PitchCatalog,
} from '../../api/pitch';
import { useStudioEvents } from '../../events';
import { PITCH_SHEETS } from '../pitch/pitchUtils';

/** Settings sub-section: Pitch catalog base prices (admin overrides) and pending outbox count. */
export function PitchBasePricesPanel() {
  const [account, setAccount] = useState<AccountState | null>(null);
  const [catalog, setCatalog] = useState<PitchCatalog | null>(null);
  const [pendingCount, setPendingCount] = useState<number>(0);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadAccount = useCallback(async () => {
    try {
      const acct = await getAccount();
      setAccount(acct);
    } catch {
      setAccount(null);
    }
  }, []);

  const loadData = useCallback(async () => {
    const [catRes, outboxRes] = await Promise.allSettled([
      getPitchCatalog(),
      getPitchOutbox(true),
    ]);

    if (catRes.status === 'fulfilled' && catRes.value && typeof catRes.value === 'object') {
      const cat = catRes.value;
      setCatalog(cat);
      if (cat.items && typeof cat.items === 'object') {
        const nextInputs: Record<string, string> = {};
        for (const [id, item] of Object.entries(cat.items)) {
          nextInputs[id] = item.overridden ? String(item.price) : '';
        }
        setInputs(nextInputs);
      }
    }

    if (outboxRes.status === 'fulfilled' && outboxRes.value && typeof outboxRes.value === 'object') {
      setPendingCount(Number(outboxRes.value.pending_count ?? 0));
    }
  }, []);

  useEffect(() => {
    void loadData();
    void loadAccount();
    const onAccountChanged = () => {
      void loadAccount();
    };
    window.addEventListener('magnm:account-changed', onAccountChanged);
    return () => {
      window.removeEventListener('magnm:account-changed', onAccountChanged);
    };
  }, [loadAccount, loadData]);

  useStudioEvents(
    useCallback(
      (eventName) => {
        if (eventName === 'pitch.updated' || eventName === 'team_knowledge.updated') {
          void loadData();
        }
      },
      [loadData],
    ),
  );

  const isMember = Boolean(
    account?.configured &&
      account?.signed_in &&
      !account?.deactivated &&
      account?.user?.role === 'member',
  );

  const handleSaveItem = async (itemId: string) => {
    const raw = (inputs[itemId] ?? '').trim();
    const parsed = Number(raw);
    if (!raw || !Number.isInteger(parsed) || parsed < 0 || parsed > 10_000_000) {
      setError('Enter an integer price between 0 and 10,000,000.');
      return;
    }
    setError(null);
    setNotice(null);
    setBusyItem(itemId);
    try {
      await setBasePrice(itemId, parsed);
      setNotice(`Updated admin base price for ${itemId}.`);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save base price.');
    } finally {
      setBusyItem(null);
    }
  };

  const handleResetItem = async (itemId: string) => {
    setError(null);
    setNotice(null);
    setBusyItem(itemId);
    try {
      await resetBasePrice(itemId);
      setNotice(`Reset ${itemId} back to Pitch catalog price.`);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset base price.');
    } finally {
      setBusyItem(null);
    }
  };

  const items = catalog?.items && typeof catalog.items === 'object' ? catalog.items : {};
  const hasItems = Object.keys(items).length > 0;

  return (
    <section className="studio-section" aria-labelledby="settings-pitch-prices-heading">
      <div className="studio-section-bar">
        <div className="studio-settings-head">
          <span className="studio-settings-num">09</span>
          <h2 id="settings-pitch-prices-heading" className="studio-section-title">
            Pitch base prices (admin)
          </h2>
        </div>
        <span className="studio-mono">
          Queued admin notifications: {pendingCount}
        </span>
      </div>

      {isMember ? (
        <div className="mg-notice" role="note">
          Managed by your admin
        </div>
      ) : (
        <div className="mg-notice" role="note">
          Only the admin will be able to edit Pitch base prices once team roles arrive in Phase 4. Changes are queued locally until then.
        </div>
      )}

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      {notice ? (
        <div className="mg-notice" role="status">
          {notice}
        </div>
      ) : null}

      {!hasItems ? (
        <div className="mg-empty">No Pitch catalog items loaded.</div>
      ) : (
        <div className="studio-stack">
          {PITCH_SHEETS.map((sheet) => {
            const sheetEntries = Object.entries(items).filter(([, item]) =>
              sheet.groups.includes(item.g),
            );
            if (sheetEntries.length === 0) return null;

            return (
              <Card key={sheet.key}>
                <div className="studio-stack-sm">
                  <div className="studio-section-bar">
                    <span className="mg-label">{sheet.label}</span>
                    <span className="studio-mono">{sheetEntries.length} items</span>
                  </div>

                  <div className="studio-table-wrap">
                    <table className="studio-table">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th>Pitch base</th>
                          <th>Admin override</th>
                          {!isMember ? <th>Actions</th> : null}
                        </tr>
                      </thead>
                      <tbody>
                        {sheetEntries.map(([itemId, item]) => (
                          <tr key={itemId}>
                            <td>
                              <div className="studio-stack-xs">
                                <span>{item.n}</span>
                                <span className="studio-mono">{itemId} · {item.g}</span>
                              </div>
                            </td>
                            <td className="mg-num">{formatRupees(item.base_price)}</td>
                            <td>
                              <TextInput
                                type="number"
                                min={0}
                                max={10000000}
                                step={1}
                                disabled={isMember}
                                readOnly={isMember}
                                className="studio-pitch-price-input"
                                aria-label={`Admin override for ${item.n}`}
                                placeholder={String(item.base_price)}
                                value={inputs[itemId] ?? ''}
                                onChange={(e) =>
                                  setInputs((prev) => ({
                                    ...prev,
                                    [itemId]: e.target.value,
                                  }))
                                }
                              />
                            </td>
                            {!isMember ? (
                              <td>
                                <div className="studio-row-actions">
                                  <PillButton
                                    size="sm"
                                    disabled={busyItem === itemId}
                                    aria-label={`Save base price for ${item.n}`}
                                    onClick={() => void handleSaveItem(itemId)}
                                  >
                                    Save
                                  </PillButton>
                                  <PillButton
                                    variant="quiet"
                                    size="sm"
                                    disabled={!item.overridden || busyItem === itemId}
                                    aria-label={`Reset base price for ${item.n}`}
                                    onClick={() => void handleResetItem(itemId)}
                                  >
                                    Reset
                                  </PillButton>
                                </div>
                              </td>
                            ) : null}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </section>
  );
}
