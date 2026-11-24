import { useCallback, useEffect, useState } from 'react';
import { NumberedTabs, SheetHeader, type TabItem } from '@magnm/design/react';
import { getAdminTeam, type AdminMember } from '../../api/admin';
import { AdminActivityPanel } from './AdminActivityPanel';
import { AdminAiKeysPanel } from './AdminAiKeysPanel';
import { AdminAuditPanel } from './AdminAuditPanel';
import { AdminPitchPanel } from './AdminPitchPanel';
import { AdminStoragePanel } from './AdminStoragePanel';
import { AdminTeamPanel } from './AdminTeamPanel';
import { AdminWorkspacePanel } from './AdminWorkspacePanel';

export type AdminTabId = 'team' | 'activity' | 'workspace' | 'pitch' | 'storage' | 'ai' | 'audit';

const ADMIN_TABS: TabItem[] = [
  { id: 'team', label: 'Team' },
  { id: 'activity', label: 'Activity' },
  { id: 'workspace', label: 'Workspace' },
  { id: 'pitch', label: 'Pitch' },
  { id: 'storage', label: 'Storage' },
  { id: 'ai', label: 'AI keys' },
  { id: 'audit', label: 'Audit log' },
];

export interface AdminSectionProps {
  param?: string | null;
  onNavigate?: (param: string | null) => void;
  onOpenSettings: () => void;
}

/** Admin dashboard section: team status, activity, read-only workspaces, pitch, storage, and audit log. */
export function AdminSection({ onOpenSettings }: AdminSectionProps) {
  const [activeTab, setActiveTab] = useState<AdminTabId>('team');
  const [members, setMembers] = useState<AdminMember[]>([]);
  const [currentUserId, setCurrentUserId] = useState('');
  const [selectedMemberId, setSelectedMemberId] = useState('');
  const [loadingTeam, setLoadingTeam] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTeam = useCallback(async () => {
    setError(null);
    try {
      const res = await getAdminTeam();
      const list = Array.isArray(res.members) ? res.members : [];
      setMembers(list);
      setCurrentUserId(res.current_user_id || '');
      setSelectedMemberId((prev) => {
        if (prev && list.some((m) => m.id === prev)) return prev;
        return list[0]?.id || '';
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load team.');
    } finally {
      setLoadingTeam(false);
    }
  }, []);

  useEffect(() => {
    void loadTeam();
  }, [loadTeam]);

  const handleOpenWorkspace = (memberId: string) => {
    setSelectedMemberId(memberId);
    setActiveTab('workspace');
  };

  const handleOpenPitch = (memberId: string) => {
    setSelectedMemberId(memberId);
    setActiveTab('pitch');
  };

  return (
    <div className="studio-stack">
      <SheetHeader
        page={12}
        total={12}
        subtitle="Team oversight"
        title="Admin"
        lead="Live member status, team activity, read-only workspaces, Pitch links, storage usage, and audit log."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <NumberedTabs
        items={ADMIN_TABS}
        current={activeTab}
        onSelect={(id) => setActiveTab(id as AdminTabId)}
        ariaLabel="Admin sections"
      />

      {activeTab === 'team' ? (
        <AdminTeamPanel
          members={members}
          currentUserId={currentUserId}
          loading={loadingTeam}
          onRefresh={loadTeam}
          onOpenWorkspace={handleOpenWorkspace}
          onOpenPitch={handleOpenPitch}
          onOpenSettings={onOpenSettings}
        />
      ) : null}

      {activeTab === 'activity' ? <AdminActivityPanel members={members} /> : null}

      {activeTab === 'workspace' ? (
        <AdminWorkspacePanel
          members={members}
          memberId={selectedMemberId}
          onSelectMember={setSelectedMemberId}
          onBackToTeam={() => setActiveTab('team')}
        />
      ) : null}

      {activeTab === 'pitch' ? (
        <AdminPitchPanel
          members={members}
          memberId={selectedMemberId}
          onSelectMember={setSelectedMemberId}
        />
      ) : null}

      {activeTab === 'storage' ? <AdminStoragePanel /> : null}

      {activeTab === 'ai' ? <AdminAiKeysPanel members={members} /> : null}

      {activeTab === 'audit' ? <AdminAuditPanel /> : null}
    </div>
  );
}
