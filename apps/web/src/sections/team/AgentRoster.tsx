import { Row, RowList, StatusDot, type StatusState } from '@magnm/design/react';
import type { RosterAgent } from '../../api/team';

export interface AgentRosterProps {
  agents: RosterAgent[];
  selectedTarget: 'room' | string;
  onSelect: (target: 'room' | string) => void;
  loading: boolean;
}

function mapAgentStatus(agent: RosterAgent): { state: StatusState; label: string } {
  switch (agent.status) {
    case 'working':
      return { state: 'working', label: 'Working' };
    case 'waiting_user':
      return { state: 'online', label: 'Waiting' };
    case 'online':
      return { state: 'online', label: 'Online' };
    case 'off':
      return { state: 'off', label: 'Off' };
    default:
      if (agent.model_info && agent.model_info.has_key === false) {
        return { state: 'off', label: 'Off' };
      }
      return { state: 'idle', label: 'Idle' };
  }
}

/** Left roster list: shared Team Room plus the 7 agents with status dot and model label. */
export function AgentRoster({ agents, selectedTarget, onSelect, loading }: AgentRosterProps) {
  return (
    <div className="studio-team-roster">
      <div className="studio-section-title">Channels &amp; agents</div>
      {loading && agents.length === 0 ? (
        <div className="mg-empty">Loading team roster.</div>
      ) : (
        <RowList aria-label="Team roster">
          <Row
            selected={selectedTarget === 'room'}
            onClick={() => onSelect('room')}
            title="Team room"
            description="Shared thread with @mentions"
            meta="ROOM"
          />
          {agents.map((agent) => {
            const st = mapAgentStatus(agent);
            const modelName = agent.model_info?.model || 'default';
            return (
              <Row
                key={agent.id}
                selected={selectedTarget === agent.id}
                onClick={() => onSelect(agent.id)}
                title={agent.name}
                description={
                  <span className="studio-roster-meta">
                    <StatusDot state={st.state} label={st.label} />
                    <span className="studio-mono studio-model-label" title={modelName}>
                      {modelName}
                    </span>
                  </span>
                }
                meta={agent.code}
              />
            );
          })}
        </RowList>
      )}
    </div>
  );
}
