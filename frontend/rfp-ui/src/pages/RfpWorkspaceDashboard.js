/**
 * RfpWorkspaceDashboard
 *
 * The per-RFP dashboard you land on after selecting a proposal from the
 * portfolio. Pulls every signal we can derive from the new
 * GET /api/proposals/{id}/dashboard-summary endpoint and lays them out
 * as a visually-stunning, drill-throughable home page for that RFP.
 *
 * Sections (top → bottom):
 *   1. Header strip — title, status, due-date countdown, capture lead, win %, target $
 *   2. KPI tile row (animated counters): requirements, % verified,
 *      open critical questions, risk factor count
 *   3. Horizontal RFP timeline with mandatory markers and legend
 *   4. Risk panel + Compliance status donut + Questions priority donut
 *   5. Competitor watch strip — latest 5 synthesized threads
 *
 * Every card / chart / chip is a click-through to the relevant page,
 * filtered to the appropriate scope.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Chip, Stack, Tooltip,
  LinearProgress, IconButton, Grow, Fade, Alert, CircularProgress, Button,
  Collapse,
} from '@mui/material';
import {
  ArrowBack as BackIcon,
  Refresh as RefreshIcon,
  Schedule as ScheduleIcon,
  EmojiEvents as TrophyIcon,
  Warning as WarningIcon,
  CheckCircle as CheckIcon,
  HelpOutline as QuestionIcon,
  Insights as InsightsIcon,
  TrendingUp as TrendIcon,
  Person as PersonIcon,
  ArrowForward as ArrowIcon,
  ExpandMore as ExpandIcon,
  OpenInNew as DrillIcon,
} from '@mui/icons-material';
import { useNavigate, useParams } from 'react-router-dom';
import axios from 'axios';
import { useProposal } from '../proposal/ProposalContext';
import RfpTimeline from '../components/RfpTimeline';
import ProposalNewsFeed from '../components/ProposalNewsFeed';
import SubmissionReadinessPanel from '../components/SubmissionReadinessPanel';
import RfpAskCard from '../components/RfpAskCard';
import TargetedCompetitorsDialog from '../components/TargetedCompetitorsDialog';

// ── Risk item — clickable, expandable detail + drill-through ────────

function RiskItem({ risk: r, goto }) {
  const [open, setOpen] = useState(false);
  const sevColor = r.severity === 'high' ? '#c62828'
                 : r.severity === 'medium' ? '#ed6c02' : '#0288d1';
  return (
    <Box sx={{
      borderRadius: 1.5,
      borderLeft: `4px solid ${sevColor}`,
      bgcolor: `${sevColor}11`,
      cursor: 'pointer',
      transition: 'background 0.15s',
      '&:hover': { bgcolor: `${sevColor}18` },
    }} onClick={() => setOpen(v => !v)}>
      <Box sx={{ p: 1.25, display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" fontWeight={700} sx={{ wordBreak: 'break-word' }}>
            {r.label}
          </Typography>
          <Chip size="small" label={r.severity}
                sx={{ mt: 0.5, height: 18, fontSize: '0.65rem',
                      bgcolor: sevColor, color: '#fff' }} />
        </Box>
        <ExpandIcon sx={{
          fontSize: 18, color: 'text.secondary',
          transform: open ? 'rotate(180deg)' : 'none',
          transition: 'transform 0.2s',
        }} />
      </Box>
      <Collapse in={open}>
        <Box sx={{ px: 1.25, pb: 1.25 }}>
          {r.detail && (
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', whiteSpace: 'pre-line', mb: 1 }}>
              {r.detail}
            </Typography>
          )}
          {r.action && (
            <Button size="small" variant="outlined"
                    endIcon={<DrillIcon sx={{ fontSize: 14 }} />}
                    onClick={(e) => { e.stopPropagation(); goto(r.action.path); }}
                    sx={{ height: 26, fontSize: '0.72rem' }}>
              {r.action.label}
            </Button>
          )}
        </Box>
      </Collapse>
    </Box>
  );
}

// ── Tiny shared helpers (kept here to avoid a one-off helpers file) ──

function dueColor(days) {
  if (days == null) return 'text.secondary';
  if (days < 0 || days <= 7) return 'error.main';
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

function CountUp({ value = 0, duration = 800, suffix = '' }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    let raf;
    const start = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setN(Math.round((value || 0) * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return <span>{n.toLocaleString()}{suffix}</span>;
}

// Donut from a {label: count} dict — clickable slices that drill through
// via the supplied onSliceClick(key) handler.
function Donut({ data, palette, size = 160, thickness = 28, onSliceClick, title }) {
  const entries = Object.entries(data || {}).filter(([, v]) => v > 0);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  if (!total) {
    return (
      <Box sx={{
        height: size, display: 'flex', alignItems: 'center',
        justifyContent: 'center', color: 'text.secondary',
      }}>
        <Typography variant="caption">No data yet</Typography>
      </Box>
    );
  }
  const r = (size - thickness) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const c = 2 * Math.PI * r;
  let acc = 0;

  return (
    <Box sx={{ position: 'relative', width: size, height: size, mx: 'auto' }}>
      <svg width={size} height={size}>
        <circle cx={cx} cy={cy} r={r} fill="none"
                stroke="rgba(0,0,0,0.06)" strokeWidth={thickness} />
        {entries.map(([key, v]) => {
          const fraction = v / total;
          const dash = fraction * c;
          const offset = -acc * c;
          acc += fraction;
          const colour = palette[key] || '#9e9e9e';
          return (
            <Tooltip key={key} title={`${key}: ${v}`} arrow>
              <circle
                cx={cx} cy={cy} r={r}
                fill="none" stroke={colour} strokeWidth={thickness}
                strokeDasharray={`${dash} ${c}`}
                strokeDashoffset={offset}
                transform={`rotate(-90 ${cx} ${cy})`}
                style={{
                  cursor: onSliceClick ? 'pointer' : 'default',
                  transition: 'stroke-width 0.2s ease',
                }}
                onClick={() => onSliceClick && onSliceClick(key)}
              />
            </Tooltip>
          );
        })}
      </svg>
      <Box sx={{
        position: 'absolute', inset: 0, display: 'flex',
        flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        pointerEvents: 'none',
      }}>
        <Typography variant="h4" fontWeight={800}>{total}</Typography>
        {title && (
          <Typography variant="caption" color="text.secondary"
                      sx={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>
            {title}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

function DonutLegend({ data, palette, onClickKey }) {
  const entries = Object.entries(data || {}).filter(([, v]) => v > 0);
  return (
    <Stack spacing={0.5} sx={{ pl: 1 }}>
      {entries.map(([k, v]) => (
        <Stack key={k} direction="row" spacing={1} alignItems="center"
               onClick={onClickKey ? () => onClickKey(k) : undefined}
               sx={{ cursor: onClickKey ? 'pointer' : 'default',
                     '&:hover': onClickKey ? { color: 'primary.main' } : {} }}>
          <Box sx={{ width: 10, height: 10, borderRadius: '50%',
                     bgcolor: palette[k] || '#9e9e9e' }} />
          <Typography variant="caption" sx={{ flex: 1 }}>
            {(k || '').replace(/_/g, ' ')}
          </Typography>
          <Typography variant="caption" fontWeight={700}>{v}</Typography>
        </Stack>
      ))}
    </Stack>
  );
}

// ── Page ─────────────────────────────────────────────────────────────
export default function RfpWorkspaceDashboard() {
  const { proposalId: routeProposalId } = useParams();
  const { setActiveProposal } = useProposal();
  const navigate = useNavigate();

  const proposalId = useMemo(() => parseInt(routeProposalId, 10), [routeProposalId]);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [competitorsDialogOpen, setCompetitorsDialogOpen] = useState(false);
  // AI Readiness panel collapse — preference persisted per browser so the
  // user's choice survives page reloads.
  const [readinessOpen, setReadinessOpen] = useState(() => {
    try { return window.localStorage.getItem('rfp.dashboard.readinessOpen') !== 'false'; }
    catch { return true; }
  });
  const toggleReadiness = () => {
    setReadinessOpen((v) => {
      const next = !v;
      try { window.localStorage.setItem('rfp.dashboard.readinessOpen', String(next)); } catch { /* ignore */ }
      return next;
    });
  };

  // Sync the URL-scoped id into ProposalContext so other pages know what we're on.
  useEffect(() => { if (proposalId) setActiveProposal(proposalId); },
    [proposalId, setActiveProposal]);

  const load = async () => {
    if (!proposalId) return;
    setLoading(true);
    setError(null);
    try {
      const { data: payload } = await axios.get(`/api/proposals/${proposalId}/dashboard-summary`);
      setData(payload);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [proposalId]);

  if (!proposalId) {
    return <Alert severity="error" sx={{ m: 2 }}>Invalid proposal id in URL.</Alert>;
  }
  if (loading) {
    return <Box sx={{ p: 6, textAlign: 'center' }}><CircularProgress /></Box>;
  }
  if (error) {
    return <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>;
  }
  if (!data) return null;

  const p = data.proposal || {};
  const reqs = data.requirements || {};
  const qs = data.questions || {};
  const events = data.schedule_events || [];
  const risks = data.risk_factors || [];
  const docs = data.documents || {};

  // Drill-through helpers — every chart/tile/chip routes via these.
  // We keep the legacy paths working by passing ?proposal=<id> so the
  // existing pages auto-scope without a refactor.
  const goto = (path) => navigate(`${path}?proposal=${proposalId}`);

  const PALETTE = {
    priority: { critical: '#c62828', high: '#ef6c00', medium: '#0288d1',
                low: '#9e9e9e', unspecified: '#bdbdbd' },
    compliance: { compliant: '#2e7d32', partial: '#ed6c02',
                  non_compliant: '#c62828', not_applicable: '#9e9e9e',
                  not_assessed: '#bdbdbd', not_reviewed: '#bdbdbd' },
    qStatus: { draft: '#9e9e9e', reviewed: '#0288d1', approved: '#2e7d32',
               rejected: '#c62828', submitted: '#7b1fa2', answered: '#3949ab' },
    // Parsons coverage palette — green for covered, amber for partial,
    // red for gap, purple for uncertain, grey for not-yet-assessed.
    parsons: { covered: '#2e7d32', partial: '#ed6c02', gap: '#c62828',
               uncertain: '#7b1fa2', not_assessed: '#bdbdbd' },
  };

  const verifiedPct = reqs.total ? Math.round((reqs.verified / reqs.total) * 100) : 0;
  const criticalOpenQ = (qs.by_priority?.critical || 0)
                      - (qs.by_status?.submitted || 0)
                      - (qs.by_status?.answered || 0);
  // Parsons-coverage gap count drives the new KPI tile + risk surface.
  const parsonsCoverage = reqs.by_parsons_coverage || {};
  const parsonsGapCount = parsonsCoverage.gap || 0;

  return (
    <Fade in timeout={400}>
      <Box>
        {/* ── Header strip ─────────────────────────────────────────── */}
        <Card sx={{
          mb: 3, borderRadius: 3,
          background: 'linear-gradient(135deg, #fff, rgba(0,174,230,0.05))',
        }}>
          <CardContent sx={{ p: 3 }}>
            <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 1 }}>
              <Tooltip title="Back to Portfolio">
                <IconButton onClick={() => navigate('/')}><BackIcon /></IconButton>
              </Tooltip>
              <Box sx={{ flex: 1 }}>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5, flexWrap: 'wrap' }}>
                  {p.solicitation_number && (
                    <Typography variant="caption"
                                sx={{ fontFamily: 'monospace', color: 'text.secondary' }}>
                      {p.solicitation_number}
                    </Typography>
                  )}
                  {p.issuing_agency && (
                    <Typography variant="caption" color="text.secondary">
                      · {p.issuing_agency}
                    </Typography>
                  )}
                  <Chip size="small"
                        label={(p.status || 'draft').replace('_', ' ')}
                        color={p.status === 'awarded' ? 'success'
                             : p.status === 'lost' ? 'error'
                             : p.status === 'submitted' ? 'primary' : 'default'} />
                </Stack>
                <Typography variant="h4" fontWeight={800}>{p.title}</Typography>
              </Box>
              <IconButton onClick={load} title="Refresh"><RefreshIcon /></IconButton>
            </Stack>
            <Stack direction="row" spacing={3} sx={{ mt: 1.5, flexWrap: 'wrap' }}>
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <ScheduleIcon sx={{ fontSize: 18, color: dueColor(p.days_until_due) }} />
                <Typography variant="body2" fontWeight={700}
                            sx={{ color: dueColor(p.days_until_due) }}>
                  {p.days_until_due == null ? 'No due date'
                    : p.days_until_due < 0 ? `${Math.abs(p.days_until_due)} days overdue`
                    : p.days_until_due === 0 ? 'Due today'
                    : `${p.days_until_due} days until due`}
                </Typography>
              </Stack>
              {p.capture_lead && (
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <PersonIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                  <Typography variant="body2">
                    Lead: <strong>{p.capture_lead}</strong>
                  </Typography>
                </Stack>
              )}
              {p.win_probability != null && (
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <TrendIcon sx={{ fontSize: 18, color: 'success.main' }} />
                  <Typography variant="body2">
                    Win prob: <strong>{p.win_probability}%</strong>
                  </Typography>
                </Stack>
              )}
              {p.target_value_usd != null && (
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <TrophyIcon sx={{ fontSize: 18, color: 'warning.main' }} />
                  <Typography variant="body2">
                    Target: <strong>{formatUSD(p.target_value_usd)}</strong>
                  </Typography>
                </Stack>
              )}
            </Stack>
          </CardContent>
        </Card>

        {/* ── KPI tile row ─────────────────────────────────────────── */}
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {[
            { label: 'Requirements', v: reqs.total || 0, c: '#00AEE6',
              icon: <InsightsIcon />, onClick: () => goto('/compliance-matrix') },
            { label: '% Verified',   v: verifiedPct, c: verifiedPct >= 70 ? '#2e7d32' : '#ed6c02',
              icon: <CheckIcon />, suffix: '%', onClick: () => goto('/compliance-matrix') },
            { label: 'Parsons gaps', v: parsonsGapCount,
              c: parsonsGapCount > 0 ? '#c62828' : '#2e7d32',
              icon: <WarningIcon />,
              onClick: () => goto('/compliance-matrix') },
            { label: 'Open critical Qs', v: Math.max(0, criticalOpenQ), c: criticalOpenQ > 0 ? '#c62828' : '#2e7d32',
              icon: <QuestionIcon />, onClick: () => goto('/questions') },
          ].map((k, i) => (
            <Grid item xs={6} md={3} key={k.label}>
              <Grow in timeout={300 + i * 100}>
                <Card
                  onClick={k.onClick || undefined}
                  sx={{
                    cursor: k.onClick ? 'pointer' : 'default',
                    borderRadius: 3,
                    background: `linear-gradient(135deg, #fff, ${k.c}10)`,
                    border: `1px solid ${k.c}33`,
                    transition: 'transform 0.2s ease',
                    '&:hover': k.onClick ? { transform: 'translateY(-2px)', boxShadow: 3 } : {},
                  }}
                >
                  <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 2.5 }}>
                    <Box sx={{
                      width: 48, height: 48, borderRadius: 2,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: k.c, color: '#fff',
                    }}>{k.icon}</Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary"
                                  sx={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>
                        {k.label}
                      </Typography>
                      <Typography variant="h5" fontWeight={800}>
                        <CountUp value={k.v} suffix={k.suffix || ''} />
                      </Typography>
                    </Box>
                  </CardContent>
                </Card>
              </Grow>
            </Grid>
          ))}
        </Grid>

        {/* ── Horizontal timeline ─────────────────────────────────── */}
        <Box sx={{ mb: 3 }}>
          <RfpTimeline
            events={events}
            dueDate={p.due_date}
            onEventClick={() => goto('/schedule')}
          />
        </Box>

        {/* ── Ask the RFP — chat-style vector-grounded Q&A.
             Sits right under the timeline (and its "Unscheduled events"
             strip) so the search box is reachable without scrolling
             through the readiness panel first. */}
        <RfpAskCard proposalId={proposalId} />

        {/* ── Submission Readiness panel — agent-readiness traffic light
             + per-section preparedness derived from the RFP's own
             section taxonomy. Collapsible (preference persisted per
             browser); the heavy panel is mounted lazily on first open. */}
        <Card sx={{ borderRadius: 3, mb: 3 }}>
          <Box
            onClick={toggleReadiness}
            sx={{
              display: 'flex', alignItems: 'center', gap: 1.5,
              px: 2, py: 1.5, cursor: 'pointer',
              userSelect: 'none',
              borderBottom: readinessOpen ? '1px solid' : 'none',
              borderColor: 'divider',
              '&:hover': { bgcolor: 'rgba(0,0,0,0.02)' },
            }}
          >
            <ExpandIcon
              sx={{
                transition: 'transform 0.2s ease',
                transform: readinessOpen ? 'rotate(0deg)' : 'rotate(-90deg)',
                color: 'text.secondary',
              }}
            />
            <InsightsIcon sx={{ color: '#00AEE6' }} />
            <Box sx={{ flex: 1 }}>
              <Typography variant="overline" color="text.secondary"
                          sx={{ display: 'block', lineHeight: 1.1 }}>
                Submission readiness
              </Typography>
              <Typography variant="h6" fontWeight={800}>
                AI Readiness & Section Preparedness
              </Typography>
            </Box>
            <Typography variant="caption" color="text.secondary"
                        sx={{ fontStyle: 'italic' }}>
              {readinessOpen ? 'Click to collapse' : 'Click to expand'}
            </Typography>
          </Box>
          <Collapse in={readinessOpen} timeout="auto" mountOnEnter unmountOnExit>
            <Box sx={{ p: 0 }}>
              <SubmissionReadinessPanel proposalId={proposalId} />
            </Box>
          </Collapse>
        </Card>

        {/* ── Risks + Donuts row (4-up to fit Parsons coverage) ──────── */}
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {/* Risk factors panel */}
          <Grid item xs={12} md={3}>
            <Card sx={{ height: '100%', borderRadius: 3 }}>
              <CardContent>
                <Typography variant="overline" color="text.secondary">Active Risks</Typography>
                {risks.length === 0 ? (
                  <Box sx={{ textAlign: 'center', py: 4 }}>
                    <CheckIcon color="success" sx={{ fontSize: 48, mb: 1 }} />
                    <Typography variant="body2" color="success.main" fontWeight={700}>
                      No active risks
                    </Typography>
                  </Box>
                ) : (
                  <Stack spacing={1} sx={{ mt: 1 }}>
                    {risks.map((r, i) => (
                      <RiskItem key={i} risk={r} goto={goto} />
                    ))}
                  </Stack>
                )}
              </CardContent>
            </Card>
          </Grid>

          {/* Compliance donut */}
          <Grid item xs={12} md={3}>
            <Card sx={{ height: '100%', borderRadius: 3 }}>
              <CardContent>
                <Typography variant="overline" color="text.secondary">
                  Compliance status
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 1 }}>
                  <Donut data={reqs.by_compliance} palette={PALETTE.compliance}
                         title="reqs"
                         onSliceClick={() => goto('/compliance-matrix')} />
                  <Box sx={{ flex: 1 }}>
                    <DonutLegend data={reqs.by_compliance} palette={PALETTE.compliance}
                                 onClickKey={() => goto('/compliance-matrix')} />
                  </Box>
                </Box>
                <Button size="small" sx={{ mt: 1 }} endIcon={<ArrowIcon />}
                        onClick={() => goto('/compliance-matrix')}>
                  Open Compliance Matrix
                </Button>
              </CardContent>
            </Card>
          </Grid>

          {/* Questions donut */}
          <Grid item xs={12} md={3}>
            <Card sx={{ height: '100%', borderRadius: 3 }}>
              <CardContent>
                <Typography variant="overline" color="text.secondary">
                  Questions by priority
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 1 }}>
                  <Donut data={qs.by_priority} palette={PALETTE.priority}
                         title="questions"
                         onSliceClick={() => goto('/questions')} />
                  <Box sx={{ flex: 1 }}>
                    <DonutLegend data={qs.by_priority} palette={PALETTE.priority}
                                 onClickKey={() => goto('/questions')} />
                  </Box>
                </Box>
                <Button size="small" sx={{ mt: 1 }} endIcon={<ArrowIcon />}
                        onClick={() => goto('/questions')}>
                  Open Questions
                </Button>
              </CardContent>
            </Card>
          </Grid>

          {/* Parsons coverage donut — brand-new for this build */}
          <Grid item xs={12} md={3}>
            <Card sx={{ height: '100%', borderRadius: 3,
                        background: 'linear-gradient(135deg, #fff, rgba(46,125,50,0.04))' }}>
              <CardContent>
                <Typography variant="overline" color="text.secondary">
                  Parsons coverage
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 1 }}>
                  <Donut data={parsonsCoverage} palette={PALETTE.parsons}
                         title="reqs"
                         onSliceClick={() => goto('/compliance-matrix')} />
                  <Box sx={{ flex: 1 }}>
                    <DonutLegend data={parsonsCoverage} palette={PALETTE.parsons}
                                 onClickKey={() => goto('/compliance-matrix')} />
                  </Box>
                </Box>
                <Button size="small" sx={{ mt: 1 }} endIcon={<ArrowIcon />}
                        onClick={() => navigate(`/p/${proposalId}/parsons-knowledge`)}>
                  Open Parsons Knowledge
                </Button>
              </CardContent>
            </Card>
          </Grid>
        </Grid>

        {/* ── Bottom row: requirement-priority bar + competitor watch ─ */}
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <Card sx={{ borderRadius: 3 }}>
              <CardContent>
                <Typography variant="overline" color="text.secondary">
                  Requirements by priority
                </Typography>
                <Stack spacing={1} sx={{ mt: 1.5 }}>
                  {Object.entries(reqs.by_priority || {})
                    .sort(([, a], [, b]) => b - a)
                    .map(([k, v]) => {
                      const max = Math.max(...Object.values(reqs.by_priority || { _: 1 }));
                      const pct = (v / (max || 1)) * 100;
                      const c = PALETTE.priority[k] || '#9e9e9e';
                      return (
                        <Box key={k}
                             onClick={() => goto('/compliance-matrix')}
                             sx={{ cursor: 'pointer' }}>
                          <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.25 }}>
                            <Typography variant="caption">{k}</Typography>
                            <Typography variant="caption" fontWeight={700}>{v}</Typography>
                          </Stack>
                          <LinearProgress
                            variant="determinate" value={pct}
                            sx={{
                              height: 8, borderRadius: 4,
                              bgcolor: 'rgba(0,0,0,0.05)',
                              '& .MuiLinearProgress-bar': { background: c },
                            }}
                          />
                        </Box>
                      );
                    })}
                </Stack>
                <Stack direction="row" spacing={2} sx={{ mt: 2, color: 'text.secondary' }}>
                  <Typography variant="caption">
                    Docs: <strong>{docs.total || 0}</strong>
                  </Typography>
                  <Typography variant="caption">
                    RFP files: <strong>{docs.rfp || 0}</strong>
                  </Typography>
                </Stack>
              </CardContent>
            </Card>
          </Grid>

          {/* News feed — RFP-specific + tracked competitors. Replaced the
              former "competitor watch threads" card; threads are still
              accessible via the Parsons coverage donut → Intel page link. */}
          <Grid item xs={12} md={6}>
            <ProposalNewsFeed
              proposalId={proposalId}
              onManageCompetitors={() => setCompetitorsDialogOpen(true)}
            />
          </Grid>
        </Grid>

        <TargetedCompetitorsDialog
          open={competitorsDialogOpen}
          onClose={() => setCompetitorsDialogOpen(false)}
          proposalId={proposalId}
          onChanged={load}
        />
      </Box>
    </Fade>
  );
}
