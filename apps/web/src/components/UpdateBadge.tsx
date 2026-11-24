import { useEffect, useState } from 'react';
import { getUpdateStatus, listenUpdateAvailable, updatesAvailable } from '../desktopUpdates';

/**
 * A small "Update available" link to Settings, in the desktop app only. It shows what the once-a-day quiet check
 * found; it never installs anything.
 */
export function UpdateBadge() {
  const [version, setVersion] = useState<string | null>(null);
  const present = updatesAvailable();

  useEffect(() => {
    if (!present) return undefined;
    let alive = true;
    void getUpdateStatus()
      .then((status) => {
        if (alive && status && status.configured && status.available) setVersion(status.available.version);
      })
      .catch(() => {});
    const off = listenUpdateAvailable((info) => {
      if (alive) setVersion(info.version);
    });
    return () => {
      alive = false;
      off();
    };
  }, [present]);

  if (!present || !version) return null;
  return (
    <a className="studio-update-badge" href="#/settings" title={`Version ${version} is available`}>
      Update available
    </a>
  );
}
