import { useCallback, useEffect, useState } from 'react';
import { Card, Checkbox, PillButton, Row, RowList, StatusDot } from '@magnm/design/react';
import {
  getNotificationSettings,
  sendTestNotification,
  updateNotificationSetting,
  type NotificationSettingItem,
} from '../../api/notifications';
import {
  getSystemNotificationPermission,
  requestSystemNotificationPermission,
  type SystemNotificationPermission,
} from '../../notifications';

/** Settings card: desktop/browser notification permission and per-event toggles. */
export function NotificationsPanel() {
  const [permission, setPermission] = useState<SystemNotificationPermission>('default');
  const [settings, setSettings] = useState<NotificationSettingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKind, setBusyKind] = useState<string | null>(null);
  const [permBusy, setPermBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testSent, setTestSent] = useState(false);

  const loadSettings = useCallback(async () => {
    try {
      const res = await getNotificationSettings();
      if (res && Array.isArray(res.settings)) {
        setSettings(res.settings);
      } else {
        setSettings([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load notification settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void getSystemNotificationPermission()
      .then((perm) => setPermission(perm))
      .catch(() => setPermission('unsupported'));
    void loadSettings();

    const onAccountChanged = () => {
      void loadSettings();
    };
    window.addEventListener('magnm:account-changed', onAccountChanged);
    return () => {
      window.removeEventListener('magnm:account-changed', onAccountChanged);
    };
  }, [loadSettings]);

  const handleRequestPermission = async () => {
    setPermBusy(true);
    setError(null);
    try {
      const next = await requestSystemNotificationPermission();
      setPermission(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not request notification permission.');
    } finally {
      setPermBusy(false);
    }
  };

  const handleSendTest = async () => {
    setError(null);
    setTestSent(false);
    try {
      await sendTestNotification();
      setTestSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send a test notification.');
    }
  };

  const handleToggle = async (kind: string, nextEnabled: boolean) => {
    setBusyKind(kind);
    setError(null);
    setSettings((prev) =>
      prev.map((item) => (item.kind === kind ? { ...item, enabled: nextEnabled } : item)),
    );
    try {
      const res = await updateNotificationSetting(kind, nextEnabled);
      if (res && Array.isArray(res.settings)) {
        setSettings(res.settings);
      }
    } catch (err) {
      setSettings((prev) =>
        prev.map((item) => (item.kind === kind ? { ...item, enabled: !nextEnabled } : item)),
      );
      setError(err instanceof Error ? err.message : 'Failed to update notification setting.');
    } finally {
      setBusyKind(null);
    }
  };

  const renderPermissionBlock = () => {
    if (permission === 'granted') {
      return (
        <div className="studio-notif-perm-row">
          <div>
            <div className="studio-card-title">Desktop notifications</div>
            <div className="studio-hint">
              System notifications are turned on when the window is in the background.
            </div>
          </div>
          <StatusDot state="online" label="Allowed" />
        </div>
      );
    }
    if (permission === 'denied') {
      return (
        <div className="studio-notif-perm-row">
          <div>
            <div className="studio-card-title">Desktop notifications</div>
            <div className="studio-hint">
              Permission is denied in your browser. Showing in-app notifications only.
            </div>
          </div>
          <StatusDot state="error" label="Denied" />
        </div>
      );
    }
    if (permission === 'unsupported') {
      return (
        <div className="studio-notif-perm-row">
          <div>
            <div className="studio-card-title">Desktop notifications</div>
            <div className="studio-hint">
              System notifications are unavailable in this browser. Showing in-app notifications only.
            </div>
          </div>
          <StatusDot state="off" label="Unavailable" />
        </div>
      );
    }
    return (
      <div className="studio-notif-perm-row">
        <div>
          <div className="studio-card-title">Desktop notifications</div>
          <div className="studio-hint">
            Show desktop alerts when Magnm Studio is in the background.
          </div>
        </div>
        <PillButton
          size="sm"
          disabled={permBusy}
          onClick={() => void handleRequestPermission()}
        >
          {permBusy ? 'Checking...' : 'Turn on desktop notifications'}
        </PillButton>
      </div>
    );
  };

  return (
    <section className="studio-section" aria-labelledby="settings-notifications-heading">
      <div className="studio-section-bar">
        <h2 id="settings-notifications-heading" className="studio-section-title">
          Notifications
        </h2>
      </div>

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <Card>
        <div className="studio-stack-sm">
          {renderPermissionBlock()}

          <div className="studio-btn-group">
            <PillButton variant="outline" size="sm" onClick={() => void handleSendTest()}>
              Send a test notification
            </PillButton>
            {testSent ? (
              <span className="studio-hint">Sent. Check the bell and your desktop notifications.</span>
            ) : null}
          </div>

          {loading && settings.length === 0 ? (
            <div className="studio-hint">Loading notification preferences...</div>
          ) : settings.length === 0 ? null : (
            <RowList aria-label="Notification preferences">
              {settings.map((item) => (
                <Row
                  key={item.kind}
                  title={item.label}
                  description={item.description}
                  meta={
                    <Checkbox
                      label={item.enabled ? 'On' : 'Off'}
                      aria-label={item.label}
                      checked={item.enabled}
                      disabled={busyKind === item.kind}
                      onChange={(e) => void handleToggle(item.kind, e.target.checked)}
                    />
                  }
                />
              ))}
            </RowList>
          )}
        </div>
      </Card>
    </section>
  );
}
