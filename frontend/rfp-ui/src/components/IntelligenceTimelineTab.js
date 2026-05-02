import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Chip, Stack, CircularProgress, Alert, Tooltip,
  ToggleButton, ToggleButtonGroup, Paper,
} from '@mui/material';
import {
  AccountTree as MAIcon,
  Gavel as LawsuitIcon,
  EmojiEvents as AwardIcon,
  Cancel as LossIcon,
  Person as LeadershipIcon,
  Security as BreachIcon,
  Description as FilingIcon,
  Warning as RegulatoryIcon,
  Article as PressIcon,
  AttachMoney as FinancialIcon,
  HelpOutline as OtherIcon,
} from '@mui/icons-material';
import axios from 'axios';

// Per event_type icon + colour. Keep this in sync with the backend canonical
// list in the migration / models.
const EVENT_VISUAL = {
  m_and_a:           { icon: <MAIcon fontSize="small" />,           color: '#7b1fa2', label: 'M&A' },
  contract_award:    { icon: <AwardIcon fontSize="small" />,        color: '#2e7d32', label: 'Award' },
  contract_loss:     { icon: <LossIcon fontSize="small" />,         color: '#c62828', label: 'Loss' },
  lawsuit_filed:     { icon: <LawsuitIcon fontSize="small" />,      color: '#ef6c00', label: 'Lawsuit filed' },
  lawsuit_resolved:  { icon: <LawsuitIcon fontSize="small" />,      color: '#5d4037', label: 'Lawsuit resolved' },
  regulatory_action: { icon: <RegulatoryIcon fontSize="small" />,   color: '#d84315', label: 'Regulatory' },
  leadership_change: { icon: <LeadershipIcon fontSize="small" />,   color: '#1565c0', label: 'Leadership' },
  breach:            { icon: <BreachIcon fontSize="small" />,       color: '#b71c1c', label: 'Breach' },
  financial:         { icon: <FinancialIcon fontSize="small" />,    color: '#00695c', label: 'Financial' },
  press:             { icon: <PressIcon fontSize="small" />,        color: '#455a64', label: 'Press' },
  other:             { icon: <OtherIcon fontSize="small" />,        color: '#616161', label: 'Other' },
};

const FILTERS = [
  ['all', 'All'],
  ['m_and_a', 'M&A'],
  ['contract_award', 'Awards'],
  ['contract_loss', 'Losses'],
  ['lawsuit_filed', 'Lawsuits'],
  ['breach', 'Breaches'],
  ['regulatory_action', 'Regulatory'],
  ['leadership_change', 'Leadership'],
];

export default function IntelligenceTimelineTab({ competitorId }) {
  const [events, setEvents] = useState([]);
  const [evidence, setEvidence] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    if (!competitorId) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      axios.get(`/api/intelligence/competitors/${competitorId}/timeline`),
      axios.get(`/api/intelligence/competitors/${competitorId}/evidence`),
    ]).then(([t, e]) => {
      if (cancelled) return;
      setEvents(t.data.events || []);
      setEvidence(e.data.evidence || []);
    }).catch(() => { if (!cancelled) { setEvents([]); setEvidence([]); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [competitorId]);

  const evidenceById = useMemo(() => {
    const m = {};
    for (const e of evidence) m[e.id] = e;
    return m;
  }, [evidence]);

  const filtered = useMemo(() => {
    if (filter === 'all') return events;
    return events.filter(e => e.event_type === filter);
  }, [events, filter]);

  if (loading) return <Box sx={{ p: 2, textAlign: 'center' }}><CircularProgress size={24} /></Box>;
  if (!events.length) {
    return (
      <Alert severity="info">
        No timeline events yet. Run "Generate Intelligence" to build the
        chronological event timeline from the evidence pool.
      </Alert>
    );
  }

  return (
    <Box>
      <ToggleButtonGroup
        size="small"
        value={filter}
        exclusive
        onChange={(_, v) => v && setFilter(v)}
        sx={{ mb: 2, flexWrap: 'wrap' }}
      >
        {FILTERS.map(([v, label]) => (
          <ToggleButton key={v} value={v} sx={{ textTransform: 'none' }}>
            {label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      <Box sx={{ position: 'relative', pl: 4 }}>
        {/* Vertical spine */}
        <Box sx={{
          position: 'absolute', left: 11, top: 0, bottom: 0, width: 2,
          bgcolor: 'rgba(0,0,0,0.12)',
        }} />

        <Stack spacing={1.5}>
          {filtered.map((ev) => {
            const v = EVENT_VISUAL[ev.event_type] || EVENT_VISUAL.other;
            return (
              <Box key={ev.id} sx={{ position: 'relative' }}>
                {/* Marker on the spine */}
                <Box sx={{
                  position: 'absolute', left: -24, top: 8,
                  width: 24, height: 24, borderRadius: '50%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: '#fff', border: `2px solid ${v.color}`,
                  color: v.color, zIndex: 1,
                }}>
                  {v.icon}
                </Box>
                <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5, flexWrap: 'wrap' }}>
                    <Typography variant="caption" fontWeight={700} sx={{ color: v.color }}>
                      {ev.event_date}
                    </Typography>
                    <Chip size="small" label={v.label}
                          sx={{ height: 20, fontSize: '0.7rem',
                                bgcolor: v.color, color: '#fff' }} />
                    {ev.jurisdiction && (
                      <Chip size="small" variant="outlined" label={ev.jurisdiction}
                            sx={{ height: 20, fontSize: '0.7rem' }} />
                    )}
                    {ev.amount_usd != null && (
                      <Chip size="small" variant="outlined"
                            label={`$${(ev.amount_usd / 1_000_000).toFixed(1)}M`}
                            sx={{ height: 20, fontSize: '0.7rem' }} />
                    )}
                    <Box sx={{ flex: 1 }} />
                    <Typography variant="caption" color="text.secondary">
                      {ev.confidence}
                    </Typography>
                  </Stack>
                  <Typography variant="body2" fontWeight={700} sx={{ mb: 0.5 }}>
                    {ev.title}
                  </Typography>
                  {ev.description && (
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                      {ev.description}
                    </Typography>
                  )}
                  {(ev.evidence_ids || []).length > 0 && (
                    <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ gap: 0.5 }}>
                      {ev.evidence_ids.map((id) => {
                        const e = evidenceById[id];
                        const url = e?.citation_url;
                        const label = e?.title?.slice(0, 50) || `ev ${id}`;
                        return (
                          <Tooltip key={id}
                                   title={e ? `${e.source_connector} · ${e.claim_class}` : ''}
                                   arrow>
                            <Chip
                              size="small"
                              variant="outlined"
                              label={label}
                              clickable={!!url}
                              component={url ? 'a' : 'div'}
                              href={url || undefined}
                              target={url ? '_blank' : undefined}
                              rel={url ? 'noopener noreferrer' : undefined}
                              sx={{ height: 20, fontSize: '0.68rem', maxWidth: 280 }}
                            />
                          </Tooltip>
                        );
                      })}
                    </Stack>
                  )}
                </Paper>
              </Box>
            );
          })}
        </Stack>
      </Box>
    </Box>
  );
}
