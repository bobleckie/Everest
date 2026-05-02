import React, { useMemo } from 'react';
import {
  Box, Paper, Typography, Stack, Chip, Tooltip, keyframes, Divider,
} from '@mui/material';
import {
  CheckCircle as CheckIcon,
  Autorenew as SpinnerIcon,
  RadioButtonUnchecked as PendingIcon,
  ErrorOutline as ErrorIcon,
  Bolt as BoltIcon,
  Search as SearchIcon,
  AutoStories as StoriesIcon,
  EditNote as EditIcon,
  PlayArrow as PlayIcon,
} from '@mui/icons-material';

// Animations
const spin = keyframes`from {transform:rotate(0deg);} to {transform:rotate(360deg);}`;
const pulseGlow = keyframes`
  0%   { box-shadow: 0 0 0 0 rgba(0,174,230,0.55); }
  70%  { box-shadow: 0 0 0 10px rgba(0,174,230,0); }
  100% { box-shadow: 0 0 0 0 rgba(0,174,230,0); }
`;
const shimmer = keyframes`
  0%   { background-position: -200% 0; }
  100% { background-position:  200% 0; }
`;

const GROUP_ICONS = {
  Setup: <PlayIcon fontSize="small" />,
  Research: <SearchIcon fontSize="small" />,
  Synthesis: <StoriesIcon fontSize="small" />,
  Drafting: <EditIcon fontSize="small" />,
};

function formatDuration(ms) {
  if (ms == null || ms < 0) return '—';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

function statusVisuals(status) {
  switch (status) {
    case 'complete':
      return {
        icon: <CheckIcon sx={{ fontSize: 18, color: '#fff' }} />,
        bg: 'linear-gradient(135deg,#2e7d32,#43a047)',
        border: '#2e7d32',
        labelColor: 'success.main',
      };
    case 'in_progress':
      return {
        icon: <SpinnerIcon sx={{ fontSize: 18, color: '#fff', animation: `${spin} 1.4s linear infinite` }} />,
        bg: 'linear-gradient(135deg,#00AEE6,#0277bd)',
        border: '#00AEE6',
        labelColor: 'primary.main',
      };
    case 'failed':
      return {
        icon: <ErrorIcon sx={{ fontSize: 18, color: '#fff' }} />,
        bg: 'linear-gradient(135deg,#c62828,#ef5350)',
        border: '#c62828',
        labelColor: 'error.main',
      };
    default:
      return {
        icon: <PendingIcon sx={{ fontSize: 18, color: '#9e9e9e' }} />,
        bg: '#f5f5f5',
        border: '#bdbdbd',
        labelColor: 'text.secondary',
      };
  }
}

/**
 * IntelligenceRunTimeline
 *
 * Fully data-driven from the job payload. Phases are grouped by `group`
 * (Setup / Research / Synthesis / Drafting). The component does not
 * hard-code phase ids — adding a connector on the backend means the new
 * tile shows up automatically.
 *
 * Props:
 *   job: {
 *     job_id, status, started_at, completed_at, error,
 *     phases: [{id, label, group, status, started_at, completed_at, detail}],
 *     summary: {evidence_count?, thread_count?, timeline_count?},
 *   }
 *   competitorName: string
 */
export default function IntelligenceRunTimeline({ job, competitorName }) {
  const { phases = [], status, started_at, completed_at, summary, error } = job || {};

  const completedCount = useMemo(
    () => phases.filter(p => p.status === 'complete').length,
    [phases]
  );
  const failedCount = useMemo(
    () => phases.filter(p => p.status === 'failed').length,
    [phases]
  );
  const total = phases.length;
  const pct = total > 0 ? Math.round(((completedCount + failedCount) / total) * 100) : 0;

  const elapsedMs = useMemo(() => {
    if (!started_at) return 0;
    const end = completed_at ? new Date(completed_at) : new Date();
    return end.getTime() - new Date(started_at).getTime();
  }, [started_at, completed_at]);

  // Group phases preserving order.
  const groups = useMemo(() => {
    const map = new Map();
    phases.forEach(p => {
      if (!map.has(p.group)) map.set(p.group, []);
      map.get(p.group).push(p);
    });
    return Array.from(map.entries()); // [[group, phases], ...]
  }, [phases]);

  const currentPhase = useMemo(
    () => phases.find(p => p.status === 'in_progress') || null,
    [phases]
  );

  if (!job) return null;

  const overallChip = (() => {
    switch (status) {
      case 'done':    return <Chip size="small" color="success" icon={<CheckIcon />} label="Complete" />;
      case 'error':   return <Chip size="small" color="error"   icon={<ErrorIcon />} label="Failed" />;
      case 'running': return <Chip size="small" color="primary" icon={<BoltIcon />} label="Running" />;
      default:        return <Chip size="small" label="Queued" />;
    }
  })();

  return (
    <Paper variant="outlined" sx={{
      p: 2, mb: 2, borderRadius: 2,
      background: 'linear-gradient(135deg, rgba(0,174,230,0.04), rgba(2,119,189,0.04))',
      borderColor: status === 'done' ? 'success.light'
                : status === 'error' ? 'error.light'
                : '#00AEE6',
    }}>
      {/* Header */}
      <Stack direction="row" alignItems="center" justifyContent="space-between"
             sx={{ mb: 1, flexWrap: 'wrap', gap: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <BoltIcon color="primary" />
          <Typography variant="subtitle1" fontWeight={700}>
            Intelligence Run{competitorName ? ` — ${competitorName}` : ''}
          </Typography>
          {overallChip}
        </Stack>
        <Stack direction="row" alignItems="center" spacing={2}>
          <Typography variant="caption" color="text.secondary">
            {completedCount} / {total} phases · {pct}%
            {failedCount > 0 && (
              <span style={{ color: '#c62828' }}> · {failedCount} failed</span>
            )}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Elapsed: <strong>{formatDuration(elapsedMs)}</strong>
          </Typography>
        </Stack>
      </Stack>

      {/* Overall shimmer bar */}
      <Box sx={{
        position: 'relative', height: 6, borderRadius: 3, overflow: 'hidden',
        mb: 2, bgcolor: 'rgba(0,0,0,0.06)',
      }}>
        <Box sx={{
          width: `${pct}%`, height: '100%', transition: 'width 0.5s ease',
          background: status === 'error'
            ? 'linear-gradient(90deg,#c62828,#ef5350)'
            : 'linear-gradient(90deg,#00AEE6,#0277bd,#00AEE6)',
          backgroundSize: '200% 100%',
          animation: status === 'running' ? `${shimmer} 2.5s linear infinite` : 'none',
        }} />
      </Box>

      {/* Current step callout */}
      {currentPhase && (
        <Paper variant="outlined" sx={{
          p: 1.25, mb: 2, borderRadius: 2, display: 'flex',
          alignItems: 'center', gap: 1.5,
          borderColor: '#00AEE6', bgcolor: 'rgba(0,174,230,0.06)',
        }}>
          <Box sx={{
            width: 32, height: 32, borderRadius: '50%',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'linear-gradient(135deg,#00AEE6,#0277bd)',
            animation: `${pulseGlow} 1.8s ease-out infinite`,
          }}>
            <SpinnerIcon sx={{ color: '#fff', animation: `${spin} 1.4s linear infinite` }} />
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary">
              {currentPhase.group} · running now
            </Typography>
            <Typography variant="body2" fontWeight={700} noWrap>
              {currentPhase.label}
            </Typography>
          </Box>
        </Paper>
      )}

      {/* Grouped phase rows */}
      {groups.map(([groupName, list], gi) => (
        <Box key={groupName} sx={{ mb: gi < groups.length - 1 ? 1.5 : 0 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75 }}>
            {GROUP_ICONS[groupName] || null}
            <Typography variant="caption" fontWeight={700}
                        sx={{ textTransform: 'uppercase', letterSpacing: 0.5 }}
                        color="text.secondary">
              {groupName}
            </Typography>
            <Box sx={{ flex: 1 }}><Divider /></Box>
            <Typography variant="caption" color="text.secondary">
              {list.filter(p => p.status === 'complete').length}/{list.length}
            </Typography>
          </Stack>

          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
            {list.map((p) => {
              const v = statusVisuals(p.status);
              const dur = p.started_at && p.completed_at
                ? new Date(p.completed_at).getTime() - new Date(p.started_at).getTime()
                : null;
              const detailBits = [];
              if (p.detail?.card_count != null) detailBits.push(`${p.detail.card_count} cards`);
              if (p.detail?.evidence_count != null) detailBits.push(`${p.detail.evidence_count} evidence`);
              if (p.detail?.thread_count != null) detailBits.push(`${p.detail.thread_count} threads`);
              if (p.detail?.timeline_count != null) detailBits.push(`${p.detail.timeline_count} events`);
              if (p.detail?.confidence) detailBits.push(p.detail.confidence);
              if (p.detail?.error) detailBits.push(`error: ${p.detail.error}`);
              if (dur != null) detailBits.push(formatDuration(dur));

              const tooltip = (
                <span>
                  <strong>{p.label}</strong><br />
                  Status: {p.status}<br />
                  {detailBits.length > 0 && detailBits.join(' · ')}
                </span>
              );

              return (
                <Tooltip key={p.id} title={tooltip} arrow>
                  <Box sx={{
                    display: 'flex', alignItems: 'center', gap: 0.75,
                    px: 1, py: 0.5, borderRadius: 999,
                    border: `1px solid ${v.border}`,
                    background: p.status === 'in_progress' ? v.bg : 'transparent',
                    color: p.status === 'in_progress' ? '#fff' : v.labelColor,
                    fontSize: '0.78rem',
                    animation: p.status === 'in_progress' ? `${pulseGlow} 1.8s ease-out infinite` : 'none',
                    transition: 'all 0.2s ease',
                  }}>
                    <Box sx={{
                      width: 22, height: 22, borderRadius: '50%',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: v.bg, border: `1px solid ${v.border}`,
                    }}>
                      {v.icon}
                    </Box>
                    <Typography variant="caption" fontWeight={p.status === 'in_progress' ? 700 : 500}
                                sx={{ color: 'inherit' }} noWrap>
                      {p.label}
                    </Typography>
                    {p.detail?.card_count != null && (
                      <Chip size="small" variant="outlined"
                            label={p.detail.card_count}
                            sx={{ height: 18, fontSize: '0.7rem', '& .MuiChip-label': { px: 0.75 } }} />
                    )}
                  </Box>
                </Tooltip>
              );
            })}
          </Box>
        </Box>
      ))}

      {/* Summary footer */}
      {status === 'done' && summary && (
        <Stack direction="row" spacing={1} sx={{ mt: 2, flexWrap: 'wrap' }}>
          {summary.evidence_count != null && (
            <Chip size="small" color="primary" variant="outlined"
                  label={`${summary.evidence_count} evidence cards`} />
          )}
          {summary.thread_count != null && (
            <Chip size="small" color="secondary" variant="outlined"
                  label={`${summary.thread_count} threads`} />
          )}
          {summary.timeline_count != null && (
            <Chip size="small" variant="outlined"
                  label={`${summary.timeline_count} timeline events`} />
          )}
        </Stack>
      )}

      {status === 'error' && error && (
        <Box sx={{ mt: 2, p: 1.5, borderRadius: 1,
                   bgcolor: 'rgba(198,40,40,0.08)',
                   border: '1px solid', borderColor: 'error.light' }}>
          <Typography variant="caption" color="error.main" fontWeight={700}>Error</Typography>
          <Typography variant="body2" color="error.main">{error}</Typography>
        </Box>
      )}
    </Paper>
  );
}
