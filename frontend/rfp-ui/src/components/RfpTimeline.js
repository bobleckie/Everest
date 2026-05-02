/**
 * RfpTimeline — horizontal timeline visualization for an RFP's
 * schedule events.
 *
 * Renders a horizontal axis from earliest event to latest event (or to
 * proposal due_date if no events exist). Plots one dot per event,
 * color-coded by status:
 *   • complete       → green
 *   • in_progress    → blue
 *   • upcoming ≤ 7d  → amber
 *   • upcoming > 7d  → grey
 *   • missed / past  → red
 *   • cancelled      → strikethrough grey
 *
 * Mandatory events get a thicker outline ring. The "today" marker is a
 * vertical dashed line on the axis. A legend sits below the axis.
 *
 * Hover any dot for full tooltip (label + date + assignee + confidence).
 * Click any dot to drill through to /p/{id}/schedule.
 */
import React, { useMemo } from 'react';
import {
  Box, Typography, Tooltip, Stack, Chip, Paper, keyframes,
} from '@mui/material';

// Pulse animation for "upcoming-soon" events to draw the eye.
const pulse = keyframes`
  0%   { box-shadow: 0 0 0 0 rgba(237,108,2,0.55); }
  70%  { box-shadow: 0 0 0 10px rgba(237,108,2,0); }
  100% { box-shadow: 0 0 0 0 rgba(237,108,2,0); }
`;

// Color logic — single source of truth, mirrored in the legend.
function eventStatusColor(ev) {
  const status = ev.status || 'upcoming';
  if (status === 'complete') return { c: '#2e7d32', name: 'Complete' };
  if (status === 'in_progress') return { c: '#0288d1', name: 'In progress' };
  if (status === 'cancelled') return { c: '#9e9e9e', name: 'Cancelled' };
  if (status === 'missed') return { c: '#c62828', name: 'Missed' };

  // Upcoming — branch on days-until.
  if (!ev.event_date) return { c: '#9e9e9e', name: 'Unscheduled' };
  const days = Math.floor((new Date(ev.event_date) - new Date()) / 86400000);
  if (days < 0) return { c: '#c62828', name: 'Overdue' };
  if (days <= 7) return { c: '#ed6c02', name: 'Upcoming (≤7d)' };
  return { c: '#9e9e9e', name: 'Upcoming' };
}

const LEGEND = [
  { c: '#2e7d32', label: 'Complete' },
  { c: '#0288d1', label: 'In progress' },
  { c: '#ed6c02', label: 'Upcoming (≤7d)' },
  { c: '#9e9e9e', label: 'Upcoming' },
  { c: '#c62828', label: 'Missed / overdue' },
];

// Format event_date → "Apr 25"
const fmtShort = (d) => d
  ? new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  : '—';
const fmtFull = (d) => d
  ? new Date(d).toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit',
    })
  : 'No date';

export default function RfpTimeline({ events, dueDate, onEventClick }) {
  // Filter out events without a date for plotting (we render them in a
  // small "no date" pile at the right edge).
  const dated = useMemo(
    () => (events || []).filter(e => e.event_date),
    [events]
  );
  const undated = useMemo(
    () => (events || []).filter(e => !e.event_date),
    [events]
  );

  // Compute axis bounds: earliest event ↔ max(latest event, due_date, today + 30d).
  const { tMin, tMax } = useMemo(() => {
    const dates = dated.map(e => new Date(e.event_date).getTime());
    if (dueDate) dates.push(new Date(dueDate).getTime());
    if (!dates.length) {
      const now = Date.now();
      return { tMin: now - 30 * 86400000, tMax: now + 60 * 86400000 };
    }
    const min = Math.min(...dates);
    const max = Math.max(...dates);
    // Pad so dots near the edges aren't clipped.
    const pad = Math.max(2 * 86400000, (max - min) * 0.05);
    return { tMin: min - pad, tMax: max + pad };
  }, [dated, dueDate]);

  const span = Math.max(tMax - tMin, 86400000);
  const xPct = (ts) => Math.max(0, Math.min(100, ((ts - tMin) / span) * 100));

  const todayPct = xPct(Date.now());
  const duePct = dueDate ? xPct(new Date(dueDate).getTime()) : null;

  // Group dots that are very close together to avoid overlap (within 1.5%).
  // For the simple pass we just rely on slight z-offset + tooltip to disambiguate.

  return (
    <Paper variant="outlined" sx={{
      p: 3, borderRadius: 3,
      background: 'linear-gradient(135deg, #ffffff, #f8fafc)',
    }}>
      <Stack direction="row" alignItems="baseline" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="subtitle1" fontWeight={700}>RFP Timeline</Typography>
        <Typography variant="caption" color="text.secondary">
          {dated.length} dated event{dated.length === 1 ? '' : 's'}
          {undated.length ? ` · ${undated.length} unscheduled` : ''}
          {dueDate && ` · due ${fmtShort(dueDate)}`}
        </Typography>
      </Stack>

      {/* The plot area. Generous vertical padding keeps tooltips and labels
          from clipping the parent. */}
      <Box sx={{ position: 'relative', height: 130, mx: 1 }}>
        {/* Axis line */}
        <Box sx={{
          position: 'absolute', left: 0, right: 0, top: 65, height: 4,
          borderRadius: 2,
          background: 'linear-gradient(90deg, #00AEE6 0%, #50BF34 100%)',
          opacity: 0.85,
        }} />

        {/* Axis ends — bookend chips for tMin / tMax */}
        <Box sx={{ position: 'absolute', left: 0, top: 76 }}>
          <Typography variant="caption" color="text.secondary">
            {fmtShort(new Date(tMin))}
          </Typography>
        </Box>
        <Box sx={{ position: 'absolute', right: 0, top: 76, textAlign: 'right' }}>
          <Typography variant="caption" color="text.secondary">
            {fmtShort(new Date(tMax))}
          </Typography>
        </Box>

        {/* "Today" marker — vertical dashed line through the axis */}
        {todayPct >= 0 && todayPct <= 100 && (
          <Tooltip title={`Today — ${fmtFull(Date.now())}`} arrow>
            <Box sx={{
              position: 'absolute', top: 18, bottom: 36,
              left: `${todayPct}%`,
              borderLeft: '2px dashed #607d8b',
              transform: 'translateX(-1px)',
            }}>
              <Box sx={{
                position: 'absolute', top: -16, left: -22, width: 44,
                textAlign: 'center', color: '#607d8b',
              }}>
                <Typography variant="caption" fontWeight={800}>TODAY</Typography>
              </Box>
            </Box>
          </Tooltip>
        )}

        {/* Due-date marker — vertical solid red line if proposal has due_date */}
        {duePct !== null && duePct >= 0 && duePct <= 100 && (
          <Tooltip title={`Proposal due — ${fmtFull(dueDate)}`} arrow>
            <Box sx={{
              position: 'absolute', top: 18, bottom: 36,
              left: `${duePct}%`,
              borderLeft: '3px solid #c62828',
              transform: 'translateX(-1.5px)',
            }}>
              <Box sx={{
                position: 'absolute', top: -16, left: -28, width: 56,
                textAlign: 'center', color: '#c62828',
              }}>
                <Typography variant="caption" fontWeight={800}>DUE</Typography>
              </Box>
            </Box>
          </Tooltip>
        )}

        {/* Event dots */}
        {dated.map((ev) => {
          const ts = new Date(ev.event_date).getTime();
          const left = xPct(ts);
          const { c, name } = eventStatusColor(ev);
          const isUpcomingSoon = name === 'Upcoming (≤7d)';
          const isCancelled = ev.status === 'cancelled';
          const tooltip = (
            <span>
              <strong>{ev.label || ev.event_type}</strong><br />
              {fmtFull(ev.event_date)}<br />
              Status: {ev.status || 'upcoming'}
              {ev.is_mandatory ? ' · MANDATORY' : ''}
              {ev.assignee ? ` · ${ev.assignee}` : ''}
            </span>
          );
          return (
            <Tooltip key={ev.id} title={tooltip} arrow>
              <Box
                onClick={() => onEventClick && onEventClick(ev)}
                sx={{
                  position: 'absolute', top: 55, left: `${left}%`,
                  width: 22, height: 22, ml: '-11px',
                  borderRadius: '50%',
                  background: isCancelled ? 'transparent' : c,
                  border: ev.is_mandatory ? `3px solid ${c}` : `2px solid #fff`,
                  outline: ev.is_mandatory ? '2px solid rgba(0,0,0,0.15)' : 'none',
                  cursor: onEventClick ? 'pointer' : 'default',
                  textDecoration: isCancelled ? 'line-through' : 'none',
                  animation: isUpcomingSoon ? `${pulse} 2s ease-out infinite` : 'none',
                  transition: 'transform 0.2s ease',
                  '&:hover': { transform: 'scale(1.25)' },
                  zIndex: 2,
                }}
              />
            </Tooltip>
          );
        })}
      </Box>

      {/* Legend */}
      <Stack direction="row" spacing={2} sx={{ mt: 2, flexWrap: 'wrap', gap: 1 }}>
        {LEGEND.map(l => (
          <Stack key={l.label} direction="row" alignItems="center" spacing={0.75}>
            <Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: l.c }} />
            <Typography variant="caption" color="text.secondary">{l.label}</Typography>
          </Stack>
        ))}
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box sx={{ width: 12, height: 12, borderRadius: '50%',
                     border: '3px solid #607d8b', bgcolor: '#fff' }} />
          <Typography variant="caption" color="text.secondary">Mandatory</Typography>
        </Stack>
      </Stack>

      {/* Unscheduled events pile */}
      {undated.length > 0 && (
        <Box sx={{ mt: 2, pt: 1.5, borderTop: '1px dashed rgba(0,0,0,0.1)' }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
            Unscheduled events:
          </Typography>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" sx={{ gap: 0.75 }}>
            {undated.map(ev => (
              <Chip
                key={ev.id}
                size="small"
                label={ev.label || ev.event_type}
                variant="outlined"
                onClick={onEventClick ? () => onEventClick(ev) : undefined}
              />
            ))}
          </Stack>
        </Box>
      )}
    </Paper>
  );
}
