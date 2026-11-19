import {
  Card,
  Row,
  RowList,
  SheetHeader,
  StatusDot,
  type StatusState,
} from '@magnm/design/react';
import type { EngineStatusInfo, HealthInfo, JobRecord } from '../types';

export interface TodayScreenProps {
  health: HealthInfo | null;
  status: EngineStatusInfo | null;
  jobs: JobRecord[];
  loading: boolean;
  error: string | null;
}

function mapJobState(jobStatus: string): StatusState {
  switch (jobStatus) {
    case 'running':
      return 'working';
    case 'queued':
      return 'idle';
    case 'done':
      return 'online';
    case 'failed':
      return 'error';
    default:
      return 'off';
  }
}

function formatJobLabel(job: JobRecord): string {
  if (job.status === 'running') {
    return `Running ${job.progress}%`;
  }
  switch (job.status) {
    case 'done':
      return 'Done';
    case 'failed':
      return 'Failed';
    case 'cancelled':
      return 'Cancelled';
    case 'queued':
      return 'Queued';
    default:
      return job.status;
  }
}

function formatProviderSummary(status: EngineStatusInfo | null): string {
  const env = status?.env_status;
  if (!env) return 'Unknown';
  const configured = Boolean(
    env.nvidia_nim_global || env.openai || env.anthropic || env.gemini || env.groq,
  );
  return configured ? 'Yes' : 'No';
}

function formatRolesReady(status: EngineStatusInfo | null): string {
  const env = status?.env_status;
  if (!env || typeof env.nvidia_nim_roles_configured !== 'number') {
    return 'Unknown';
  }
  const total = Array.isArray(status?.models_matrix) ? status.models_matrix.length : 0;
  if (total > 0) {
    return `${env.nvidia_nim_roles_configured} of ${total}`;
  }
  return String(env.nvidia_nim_roles_configured);
}

/** Today home screen showing safe engine status and live recent jobs. */
export function TodayScreen({ health, status, jobs, loading, error }: TodayScreenProps) {
  const env = status?.env_status;
  const providerFlags: Array<{ name: string; ready: boolean }> = env
    ? [
        { name: 'NVIDIA NIM', ready: Boolean(env.nvidia_nim_global) },
        { name: 'OpenAI', ready: Boolean(env.openai) },
        { name: 'Anthropic', ready: Boolean(env.anthropic) },
        { name: 'Gemini', ready: Boolean(env.gemini) },
        { name: 'Groq', ready: Boolean(env.groq) },
      ]
    : [];

  return (
    <div className="studio-stack">
      <SheetHeader
        page={1}
        total={9}
        subtitle="Magnm Studio"
        title="today"
        lead="Local engine health, configured providers, and recent background jobs."
      />

      {error ? (
        <div className="mg-notice" role="alert">
          {error}
        </div>
      ) : null}

      <section className="studio-section" aria-labelledby="today-engine-heading">
        <h2 id="today-engine-heading" className="studio-section-title">
          Engine
        </h2>
        <Card>
          <div className="studio-facts">
            <div className="studio-fact">
              <span className="studio-fact-label">Engine</span>
              <span className="studio-fact-value">
                {health ? (health.ok ? 'Online' : 'Unavailable') : loading ? 'Checking' : 'Unavailable'}
              </span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Providers configured</span>
              <span className="studio-fact-value">
                {loading && !status ? 'Checking' : formatProviderSummary(status)}
              </span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Roles ready</span>
              <span className="studio-fact-value">
                {loading && !status ? 'Checking' : formatRolesReady(status)}
              </span>
            </div>
            <div className="studio-fact">
              <span className="studio-fact-label">Default provider</span>
              <span className="studio-fact-value">
                {env?.default_provider || (loading && !status ? 'Checking' : 'Not set')}
              </span>
            </div>
          </div>

          {providerFlags.length > 0 ? (
            <div className="studio-provider-list" aria-label="Configured providers">
              {providerFlags.map((provider) => (
                <span key={provider.name} className="mg-chip">
                  {provider.name}: {provider.ready ? 'yes' : 'no'}
                </span>
              ))}
            </div>
          ) : null}
        </Card>
      </section>

      <section className="studio-section" aria-labelledby="today-jobs-heading">
        <h2 id="today-jobs-heading" className="studio-section-title">
          Recent jobs
        </h2>
        {loading && jobs.length === 0 ? (
          <div className="mg-empty">Loading recent jobs.</div>
        ) : jobs.length === 0 ? (
          <div className="mg-empty">No recent jobs yet. Jobs you start will appear here.</div>
        ) : (
          <RowList aria-label="Recent jobs">
            {jobs.map((job) => (
              <Row
                key={job.id}
                title={job.name}
                description={job.error || job.message || 'Waiting in queue'}
                meta={<StatusDot state={mapJobState(job.status)} label={formatJobLabel(job)} />}
              />
            ))}
          </RowList>
        )}
      </section>
    </div>
  );
}
