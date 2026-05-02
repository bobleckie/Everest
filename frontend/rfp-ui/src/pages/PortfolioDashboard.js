/**
 * PortfolioDashboard
 *
 * The new top-of-app view. Lists every Parsons RFP as an animated card.
 * Click a card to enter that RFP's workspace at /p/{id}/dashboard.
 *
 * Visualizations (every one is clickable / drill-throughs):
 *   • Header KPI strip  — total proposals, in-flight count, due-this-week count, total target value $.
 *   • Card grid         — one per proposal with countdown ring + win-prob donut + 4-stat strip.
 *   • Funnel bar        — proposal status distribution (draft/in_flight/submitted/awarded/lost).
 * Animations are MUI Grow + a tiny CSS countup for the headline numbers
 * (no extra deps — framer-motion isn't installed here).
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Chip, Button, Stack, Tooltip,
  LinearProgress, IconButton, Grow, Fade, Alert, CircularProgress,
} from '@mui/material';
import {
  Add as AddIcon,
  Refresh as RefreshIcon,
  Schedule as ScheduleIcon,
  EmojiEvents as TrophyIcon,
  Warning as WarningIcon,
  CheckCircle as CheckIcon,
  Description as DocIcon,
  HelpOutline as QuestionIcon,
  Insights as InsightsIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import AddRfpWizard from '../components/AddRfpWizard';
import { useProposal } from '../proposal/ProposalContext';

// ── Status visuals ─────────────────────────────────────────────────
const STATUS_VISUAL = {
  draft:        { color: 'default', label: 'Draft' },
  in_flight:    { color: 'info',    label: 'In Flight' },
  submitted:    { color: 'primary', label: 'Submitted' },
  under_review: { color: 'warning', label: 'Under Review' },
  awarded:      { color: 'success', label: 'Awarded' },
  lost:         { color: 'error',   label: 'Lost' },
  approved:     { color: 'success', label: 'Approved' },
  rejected:     { color: 'error',   label: 'Rejected' },
};

// Color a due-date countdown by urgency.
function dueColor(days) {
  if (days == null) return 'text.secondary';
  if (days < 0) return 'error.main';
  if (days <= 7) return 'error.main';
  if (days <= 30) return 'warning.main';
  return 'success.main';
}

function formatUSD(n) {
  if (!n && n !== 0) return null;
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}K`;
  return `$${Math.round(n)}`;
}

// Tiny inline donut/dial that fills to a percentage, styled for use as
// a win-probability badge. Pure SVG so no extra deps.
function ProbabilityDial({ value }) {
  const pct = Math.max(0, Math.min(100, value || 0));
  const r = 18;
  const c = 2 * Math.PI * r;
  const dash = (pct / 100) * c;
  const color = pct >= 70 ? '#2e7d32' : pct >= 40 ? '#ed6c02' : '#c62828';
  return (
    <Box sx={{ position: 'relative', width: 48, height: 48 }}>
      <svg width="48" height="48" viewBox="0 0 48 48">
        <circle cx="24" cy="24" r={r} fill="none" stroke="rgba(0,0,0,0.08)" strokeWidth="5" />
        <circle
          cx="24" cy="24" r={r}
          fill="none" stroke={color} strokeWidth="5"
          strokeDasharray={`${dash} ${c}`}
          strokeLinecap="round"
          transform="rotate(-90 24 24)"
          style={{ transition: 'stroke-dasharray 0.7s ease' }}
        />
      </svg>
      <Box sx={{
        position: 'absolute', inset: 0, display: 'flex',
        alignItems: 'center', justifyContent: 'center',
      }}>
        <Typography variant="caption" fontWeight={800} sx={{ color }}>
          {value != null ? `${pct}%` : '—'}
        </Typography>
      </Box>
    </Box>
  );
}

// Animated number — counts up from 0 to `value` on mount. Pure CSS-friendly.
function CountUp({ value = 0, duration = 800, format = (n) => n.toLocaleString() }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    let raf;
    const start = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setN(Math.round(value * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return <span>{format(n)}</span>;
}

// One card per proposal. Click → enter the workspace.
function ProposalCard({ p, index, onOpen }) {
  const sv = STATUS_VISUAL[p.status] || STATUS_VISUAL.draft;
  const days = p.days_until_due;
  const dueLabel = days == null ? 'No due date'
    : days < 0 ? `${Math.abs(days)} days overdue`
    : days === 0 ? 'Due today'
    : `${days} day${days === 1 ? '' : 's'} until due`;

  const reqPct = p.stats?.requirement_verified_pct || 0;
  const stats = p.stats || {};

  return (
    <Grow in timeout={500 + index * 80}>
      <Card
        onClick={() => onOpen(p)}
        sx={{
          cursor: 'pointer',
          height: '100%',
          borderRadius: 3,
          transition: 'transform 0.2s ease, box-shadow 0.2s ease',
          border: '1px solid rgba(0,0,0,0.08)',
          background: 'linear-gradient(135deg, #ffffff, #fafbfc)',
          '&:hover': {
            transform: 'translateY(-4px)',
            boxShadow: 6,
            borderColor: '#00AEE6',
          },
        }}
      >
        <CardContent sx={{ p: 2.5 }}>
          <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{ mb: 1.5 }}>
            <ProbabilityDial value={p.win_probability} />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                <Chip size="small" label={sv.label} color={sv.color}
                      variant={p.status === 'awarded' ? 'filled' : 'outlined'} />
                {p.solicitation_number && (
                  <Typography variant="caption" color="text.secondary"
                              sx={{ fontFamily: 'monospace' }} noWrap>
                    {p.solicitation_number}
                  </Typography>
                )}
              </Stack>
              <Typography variant="subtitle1" fontWeight={700} sx={{
                lineHeight: 1.25, display: '-webkit-box',
                WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
              }}>
                {p.title}
              </Typography>
              {p.issuing_agency && (
                <Typography variant="caption" color="text.secondary" noWrap>
                  {p.issuing_agency}
                </Typography>
              )}
            </Box>
          </Stack>

          {/* Due date strip */}
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.25 }}>
            <ScheduleIcon sx={{ fontSize: 16, color: dueColor(days) }} />
            <Typography variant="body2" fontWeight={700} sx={{ color: dueColor(days) }}>
              {dueLabel}
            </Typography>
            <Box sx={{ flex: 1 }} />
            {p.target_value_usd != null && (
              <Tooltip title="Target contract value">
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <TrophyIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                  <Typography variant="caption" fontWeight={700} color="text.secondary">
                    {formatUSD(p.target_value_usd)}
                  </Typography>
                </Stack>
              </Tooltip>
            )}
          </Stack>

          {/* Compliance progress bar */}
          <Box sx={{ mb: 1.25 }}>
            <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.25 }}>
              <Typography variant="caption" color="text.secondary">
                Requirements verified
              </Typography>
              <Typography variant="caption" fontWeight={700}>
                {reqPct}%
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={reqPct}
              sx={{
                height: 6, borderRadius: 3,
                bgcolor: 'rgba(0,0,0,0.06)',
                '& .MuiLinearProgress-bar': {
                  background: reqPct >= 70 ? 'linear-gradient(90deg,#43a047,#2e7d32)'
                            : reqPct >= 40 ? 'linear-gradient(90deg,#ffa726,#ed6c02)'
                            : 'linear-gradient(90deg,#ef5350,#c62828)',
                },
              }}
            />
          </Box>

          {/* 4-stat strip */}
          <Grid container spacing={1}>
            {[
              { icon: <DocIcon sx={{ fontSize: 14 }} />, label: 'RFP docs', v: stats.rfp_doc_count || 0 },
              { icon: <CheckIcon sx={{ fontSize: 14 }} />, label: 'Reqs', v: stats.requirement_count || 0 },
              { icon: <QuestionIcon sx={{ fontSize: 14 }} />, label: 'Open Qs', v: stats.open_question_count || 0 },
              { icon: <InsightsIcon sx={{ fontSize: 14 }} />, label: 'Lead', v: p.capture_lead || '—', isStr: true },
            ].map((s, i) => (
              <Grid item xs={3} key={i}>
                <Box sx={{
                  textAlign: 'center', p: 0.75, borderRadius: 1.5,
                  bgcolor: 'rgba(0,174,230,0.04)',
                  border: '1px solid rgba(0,174,230,0.12)',
                }}>
                  <Stack direction="row" justifyContent="center" alignItems="center" spacing={0.5}>
                    {s.icon}
                    <Typography variant="caption" color="text.secondary"
                                sx={{ fontSize: '0.65rem' }} noWrap>
                      {s.label}
                    </Typography>
                  </Stack>
                  <Typography variant="body2" fontWeight={800} noWrap
                              sx={{ fontSize: s.isStr ? '0.75rem' : '0.95rem' }}>
                    {s.isStr ? s.v : <CountUp value={s.v} duration={500 + index * 60} />}
                  </Typography>
                </Box>
              </Grid>
            ))}
          </Grid>
        </CardContent>
      </Card>
    </Grow>
  );
}

// ── Funnel/distribution bar at the top of the page ─────────────────
const FUNNEL_ORDER = ['draft', 'in_flight', 'submitted', 'under_review', 'awarded', 'lost'];

function StatusFunnel({ proposals }) {
  const counts = useMemo(() => {
    const m = {};
    for (const k of FUNNEL_ORDER) m[k] = 0;
    for (const p of proposals) m[p.status] = (m[p.status] || 0) + 1;
    return m;
  }, [proposals]);
  const total = proposals.length || 1;

  return (
    <Card sx={{ mb: 3, borderRadius: 3 }}>
      <CardContent sx={{ pb: 2 }}>
        <Typography variant="overline" color="text.secondary">
          Status distribution
        </Typography>
        <Box sx={{ display: 'flex', height: 28, borderRadius: 2, overflow: 'hidden', mt: 1 }}>
          {FUNNEL_ORDER.map((k, i) => {
            const n = counts[k];
            if (!n) return null;
            const pct = (n / total) * 100;
            const sv = STATUS_VISUAL[k] || {};
            const palette = {
              draft: '#9e9e9e', in_flight: '#0288d1', submitted: '#00AEE6',
              under_review: '#ed6c02', awarded: '#2e7d32', lost: '#c62828',
            };
            return (
              <Tooltip key={k} title={`${sv.label}: ${n}`} arrow>
                <Box sx={{
                  width: `${pct}%`,
                  background: palette[k] || '#9e9e9e',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: '#fff', fontWeight: 700, fontSize: '0.75rem',
                  transition: 'width 0.6s ease',
                  borderRight: i < FUNNEL_ORDER.length - 1 ? '1px solid rgba(255,255,255,0.4)' : 'none',
                }}>
                  {pct >= 8 ? n : ''}
                </Box>
              </Tooltip>
            );
          })}
        </Box>
        <Stack direction="row" spacing={2} sx={{ mt: 1.5, flexWrap: 'wrap' }}>
          {FUNNEL_ORDER.map(k => {
            const palette = {
              draft: '#9e9e9e', in_flight: '#0288d1', submitted: '#00AEE6',
              under_review: '#ed6c02', awarded: '#2e7d32', lost: '#c62828',
            };
            return (
              <Stack key={k} direction="row" alignItems="center" spacing={0.5}>
                <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: palette[k] }} />
                <Typography variant="caption" color="text.secondary">
                  {STATUS_VISUAL[k]?.label}: <strong>{counts[k]}</strong>
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      </CardContent>
    </Card>
  );
}

// ── Top-of-page KPI tiles ──────────────────────────────────────────
function HeaderKpis({ proposals }) {
  const total = proposals.length;
  const inFlight = proposals.filter(p => ['in_flight', 'submitted', 'under_review'].includes(p.status)).length;
  const dueWithin7 = proposals.filter(p =>
    p.days_until_due != null && p.days_until_due >= 0 && p.days_until_due <= 7
  ).length;
  const totalValue = proposals.reduce((acc, p) => acc + (p.target_value_usd || 0), 0);

  const kpis = [
    { label: 'Total RFPs', value: total, color: '#00AEE6', icon: <DocIcon /> },
    { label: 'In Flight', value: inFlight, color: '#0288d1', icon: <InsightsIcon /> },
    { label: 'Due ≤ 7 days', value: dueWithin7, color: dueWithin7 > 0 ? '#c62828' : '#2e7d32',
      icon: <WarningIcon /> },
    { label: 'Pipeline value', value: totalValue, color: '#7b1fa2', icon: <TrophyIcon />,
      isMoney: true },
  ];

  return (
    <Grid container spacing={2} sx={{ mb: 3 }}>
      {kpis.map((k, i) => (
        <Grid item xs={6} md={3} key={k.label}>
          <Grow in timeout={400 + i * 100}>
            <Card sx={{
              borderRadius: 3,
              background: `linear-gradient(135deg, #fff, ${k.color}11)`,
              border: `1px solid ${k.color}33`,
            }}>
              <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 2.5 }}>
                <Box sx={{
                  width: 48, height: 48, borderRadius: 2,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: k.color, color: '#fff',
                }}>
                  {k.icon}
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary"
                              sx={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    {k.label}
                  </Typography>
                  <Typography variant="h5" fontWeight={800}>
                    {k.isMoney
                      ? <span>{formatUSD(k.value) || '$0'}</span>
                      : <CountUp value={k.value} duration={900} />}
                  </Typography>
                </Box>
              </CardContent>
            </Card>
          </Grow>
        </Grid>
      ))}
    </Grid>
  );
}

// ── Page ───────────────────────────────────────────────────────────
export default function PortfolioDashboard() {
  const [portfolio, setPortfolio] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const navigate = useNavigate();
  const { setActiveProposal, refresh: refreshProposalsCtx } = useProposal();

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await axios.get('/api/proposals/portfolio');
      setPortfolio(data?.proposals || []);
    } catch (e) {
      // Surface 401 explicitly so the user sees a real error instead of
      // an empty portfolio (which would imply they have no RFPs at all).
      const status = e?.response?.status;
      if (status === 401) {
        setError('Your session has expired. Click Log out (top right) and sign in again to view your RFPs.');
      } else {
        setError(e?.response?.data?.detail || e.message || 'Failed to load portfolio');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onOpen = (p) => {
    setActiveProposal(p.id);
    navigate(`/p/${p.id}/dashboard`);
  };

  const onCreated = (newP) => {
    setWizardOpen(false);
    refreshProposalsCtx();
    load();
    if (newP?.id) onOpen(newP);
  };

  return (
    <Fade in timeout={400}>
      <Box>
        <Stack direction="row" alignItems="center" sx={{ mb: 3 }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h4" fontWeight={800}>RFP Portfolio</Typography>
            <Typography variant="body2" color="text.secondary">
              Every Parsons proposal at a glance — click any card to enter the workspace.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <IconButton onClick={load} title="Refresh"><RefreshIcon /></IconButton>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => setWizardOpen(true)}
              sx={{
                borderRadius: 3,
                background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)',
              }}
            >
              New RFP
            </Button>
          </Stack>
        </Stack>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {loading ? (
          <Box sx={{ p: 6, textAlign: 'center' }}><CircularProgress /></Box>
        ) : (error && portfolio.length === 0) ? (
          // When the load failed AND we have no cached data, suppress the
          // misleading "No proposals yet" CTA — the alert above already
          // explains the problem.
          null
        ) : portfolio.length === 0 ? (
          <Card sx={{ p: 4, textAlign: 'center', borderRadius: 3 }}>
            <Typography variant="h6" gutterBottom>No proposals yet</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Click "New RFP" above to set up your first proposal — the wizard will guide you through it.
            </Typography>
            <Button
              variant="contained" startIcon={<AddIcon />}
              onClick={() => setWizardOpen(true)}
            >Create your first RFP</Button>
          </Card>
        ) : (
          <>
            <HeaderKpis proposals={portfolio} />
            <StatusFunnel proposals={portfolio} />
            <Grid container spacing={2}>
              {portfolio.map((p, i) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={p.id}>
                  <ProposalCard p={p} index={i} onOpen={onOpen} />
                </Grid>
              ))}
            </Grid>
          </>
        )}

        <AddRfpWizard
          open={wizardOpen}
          onClose={() => setWizardOpen(false)}
          onCreated={onCreated}
        />
      </Box>
    </Fade>
  );
}
