/**
 * QuestionTriage
 *
 * The bridge between "thousands of candidate questions" and "100-200
 * curated questions ready to send to the agency."
 *
 * Layout (top → bottom):
 *   1. Funnel header — total / active / curator-recommended / approved
 *   2. Three-column workflow:
 *      a. Bulk filter + bulk-action panel
 *      b. Strategic curator launcher + progress
 *      c. Submission-ready panel (approve / export)
 *   3. Triage stats grid — counts by source/priority/status/curator-reason
 *
 * Power-user shortcuts (one-click bulk actions):
 *   - "Reject all low-priority inferences"      (~hundreds removed)
 *   - "Approve all curator-recommended >=85"    (push high-confidence into final list)
 *   - "Reject all answer_in_rfp"
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Card, CardContent, Stack, Typography, Chip, Button, Alert,
  CircularProgress, LinearProgress, Divider, MenuItem, Select,
  FormControl, InputLabel, FormControlLabel, Checkbox, TextField,
  Tooltip, IconButton, Snackbar, Dialog, DialogTitle, DialogContent,
  DialogActions, ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import {
  AutoAwesome as CuratorIcon,
  PlayArrow as RunIcon,
  Refresh as RefreshIcon,
  CheckCircle as ApproveIcon,
  Cancel as RejectIcon,
  Download as ExportIcon,
  FilterAlt as FilterIcon,
  BarChart as StatsIcon,
  Psychology as InferenceIcon,
} from '@mui/icons-material';
import axios from 'axios';
import { useProposal } from '../proposal/ProposalContext';

const SOURCE_KINDS = [
  { value: 'diff_addition',  label: 'Added (new RFP text)' },
  { value: 'diff_change',    label: 'Changed' },
  { value: 'diff_removal',   label: 'Removed' },
  { value: 'diff_inference', label: 'Inference (LLM extrapolation)' },
  { value: 'manual',         label: 'Manual' },
  { value: 'drafter',        label: 'Drafter (per-document)' },
  { value: 'ask_agent',      label: 'Ask-the-Agent' },
];

const PRIORITIES = ['critical', 'high', 'medium', 'low'];
const STATUSES = ['draft', 'reviewed', 'approved', 'rejected', 'submitted', 'answered'];
const RECONCILIATIONS = ['added', 'replaces', 'updates', 'duplicate'];
const CURATOR_REASONS = [
  { value: 'recommended',     label: 'Recommended ✓',       color: 'success' },
  { value: 'answer_in_rfp',   label: 'Answer in RFP',       color: 'warning' },
  { value: 'reveals_strategy',label: 'Reveals strategy',    color: 'error' },
  { value: 'trick_removal',   label: 'Trick removal',       color: 'warning' },
  { value: 'vague',           label: 'Vague',               color: 'default' },
  { value: 'redundant',       label: 'Redundant',           color: 'default' },
  { value: 'not_actionable',  label: 'Not actionable',      color: 'default' },
];

export default function QuestionTriage() {
  const { proposalId: ctxProposalId } = useProposal();
  const proposalId = ctxProposalId || 1; // fall back to proposal 1 in legacy view

  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(null); // {filters, newStatus, label}
  const [actingBusy, setActingBusy] = useState(false);

  // Curator job state
  const [curateJobId, setCurateJobId] = useState(null);
  const [curateJob, setCurateJob] = useState(null);
  const [curatorBusy, setCuratorBusy] = useState(false);

  // Custom filter state
  const [filterSource, setFilterSource] = useState('');
  const [filterPriority, setFilterPriority] = useState('');
  const [filterStatus, setFilterStatus] = useState(['draft', 'reviewed']);
  const [filterInference, setFilterInference] = useState('');  // '' | 'true' | 'false'
  const [filterCuratorReason, setFilterCuratorReason] = useState('');
  const [filterScoreLT, setFilterScoreLT] = useState('');
  const [filterScoreGTE, setFilterScoreGTE] = useState('');
  const [customNewStatus, setCustomNewStatus] = useState('rejected');

  const loadStats = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get('/api/questions/triage-stats',
                                       { params: { proposal_id: proposalId } });
      setStats(data);
    } catch (e) {
      setToast({ severity: 'error',
        msg: 'Stats load failed: ' + (e?.response?.data?.detail || e.message) });
    } finally {
      setLoading(false);
    }
  }, [proposalId]);

  useEffect(() => { loadStats(); }, [loadStats]);

  // ── Curator job polling ────────────────────────────────────────────
  useEffect(() => {
    if (!curateJobId) return;
    if (curateJob && (curateJob.status === 'complete' || curateJob.status === 'failed')) return;
    const t = setInterval(async () => {
      try {
        const { data } = await axios.get(`/api/questions/curate/${curateJobId}`);
        setCurateJob(data);
        if (data.status === 'complete' || data.status === 'failed') {
          loadStats(); // refresh counts after curator finishes
          clearInterval(t);
        }
      } catch { /* ignore one-off polling failures */ }
    }, 3000);
    return () => clearInterval(t);
  }, [curateJobId, curateJob, loadStats]);

  // On mount, see if there's already a curate job in-flight for this proposal
  useEffect(() => {
    (async () => {
      try {
        const { data } = await axios.get('/api/questions/curate');
        const live = (Array.isArray(data) ? data : [])
          .filter(j => j.proposal_id === proposalId &&
                  (j.status === 'queued' || j.status === 'running'))
          .pop();
        if (live) {
          setCurateJobId(live.job_id);
          setCurateJob(live);
        }
      } catch { /* nbd */ }
    })();
  }, [proposalId]);

  const startCurate = async () => {
    setCuratorBusy(true);
    try {
      const { data } = await axios.post('/api/questions/curate', {
        proposal_id: proposalId,
        only_statuses: ['draft', 'reviewed'],
      });
      setCurateJobId(data.job_id);
      setCurateJob({ ...data, processed: 0, total: 0, recommended: 0 });
      setToast({ severity: 'info',
        msg: 'Curator started. This may take 20-60 minutes for thousands of questions.' });
    } catch (e) {
      setToast({ severity: 'error',
        msg: 'Curator start failed: ' + (e?.response?.data?.detail || e.message) });
    } finally {
      setCuratorBusy(false);
    }
  };

  const runBulkAction = async (filters, newStatus, reviewNotes, label) => {
    setActingBusy(true);
    try {
      // First dry-run to get the count and confirm
      const dry = await axios.post('/api/questions/bulk-status', {
        filters: { proposal_id: proposalId, ...filters },
        new_status: newStatus,
        review_notes: reviewNotes,
        dry_run: true,
      });
      if (!dry.data.matched) {
        setToast({ severity: 'info', msg: `${label}: nothing matched.` });
        return;
      }
      if (!window.confirm(
        `${label}\n\nThis will set ${dry.data.matched} questions to "${newStatus}". Proceed?`
      )) {
        return;
      }
      const { data } = await axios.post('/api/questions/bulk-status', {
        filters: { proposal_id: proposalId, ...filters },
        new_status: newStatus,
        review_notes: reviewNotes,
        dry_run: false,
      });
      setToast({ severity: 'success',
        msg: `${label}: updated ${data.updated} questions.` });
      loadStats();
    } catch (e) {
      setToast({ severity: 'error',
        msg: `${label} failed: ${e?.response?.data?.detail || e.message}` });
    } finally {
      setActingBusy(false);
    }
  };

  const runCustomBulk = () => {
    const filters = {};
    if (filterSource)    filters.source_kind = [filterSource];
    if (filterPriority)  filters.priority    = [filterPriority];
    if (filterStatus.length) filters.status  = filterStatus;
    if (filterInference !== '') filters.inference_flag = filterInference === 'true';
    if (filterCuratorReason) filters.curator_reason = [filterCuratorReason];
    if (filterScoreLT !== '') filters.curator_score_lt = parseInt(filterScoreLT, 10);
    if (filterScoreGTE !== '') filters.curator_score_gte = parseInt(filterScoreGTE, 10);
    runBulkAction(filters, customNewStatus, 'Bulk action via Triage filter', 'Custom bulk');
  };

  // ── Funnel numbers ────────────────────────────────────────────────
  const funnel = useMemo(() => {
    if (!stats) return null;
    const total = stats.total || 0;
    const rejected = (stats.by_status?.rejected) || 0;
    const active = total - rejected;
    return {
      total,
      active,
      curator_run: stats.curator_run_total || 0,
      curator_recommended: stats.curator_recommended || 0,
      submission_ready: stats.submission_ready || 0,
    };
  }, [stats]);

  if (loading && !stats) {
    return (
      <Box sx={{ p: 4, textAlign: 'center' }}>
        <CircularProgress />
        <Typography sx={{ mt: 2 }}>Loading triage stats…</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3, maxWidth: 1400, margin: '0 auto' }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h5">Question Triage</Typography>
          <Typography variant="body2" color="text.secondary">
            Narrow the candidate pool down to the questions you'll actually submit.
            Use the Strategic Curator to score every question, then bulk-reject
            obvious skips and approve the must-asks.
          </Typography>
        </Box>
        <IconButton onClick={loadStats} title="Refresh stats">
          <RefreshIcon />
        </IconButton>
      </Stack>

      {/* ── Funnel ───────────────────────────────────────────────── */}
      {funnel && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={3} flexWrap="wrap">
              <FunnelStat label="Total" value={funnel.total} />
              <FunnelArrow />
              <FunnelStat label="Active" value={funnel.active}
                          tip="Questions not yet rejected (draft + reviewed + approved + submitted)" />
              <FunnelArrow />
              <FunnelStat label="Curator-scored" value={funnel.curator_run}
                          color={funnel.curator_run > 0 ? 'info' : 'default'}
                          tip="Questions the strategic curator has reviewed" />
              <FunnelArrow />
              <FunnelStat label="Recommended" value={funnel.curator_recommended}
                          color="success"
                          tip="Curator score ≥70 with reason='recommended'" />
              <FunnelArrow />
              <FunnelStat label="Approved" value={funnel.submission_ready}
                          color="primary"
                          tip="status='approved' — what /api/questions/export ships" />
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* ── Three-column workflow ───────────────────────────────── */}
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ mb: 2 }}>
        {/* (1) One-click bulk actions */}
        <Card variant="outlined" sx={{ flex: 1 }}>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <FilterIcon color="action" />
              <Typography variant="h6">Quick bulk actions</Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary">
              Each click previews the count, then asks for confirmation.
            </Typography>
            <Stack spacing={1} sx={{ mt: 1.5 }}>
              <BulkButton
                label="Reject all low-priority inferences"
                disabled={actingBusy}
                onClick={() => runBulkAction(
                  { source_kind: ['diff_inference'], priority: ['low'],
                    status: ['draft', 'reviewed'] },
                  'rejected',
                  'Auto: low-priority inferences are typically wild extrapolations.',
                  'Reject low-priority inferences')} />
              <BulkButton
                label="Reject curator: answer_in_rfp"
                disabled={actingBusy || !funnel?.curator_run}
                onClick={() => runBulkAction(
                  { curator_reason: ['answer_in_rfp'], status: ['draft', 'reviewed'] },
                  'rejected',
                  'Auto: curator determined the answer is in the RFP.',
                  'Reject answer_in_rfp')} />
              <BulkButton
                label="Reject curator: reveals_strategy"
                disabled={actingBusy || !funnel?.curator_run}
                color="error"
                onClick={() => runBulkAction(
                  { curator_reason: ['reveals_strategy'], status: ['draft', 'reviewed'] },
                  'rejected',
                  'Auto: would telegraph our solution approach.',
                  'Reject reveals_strategy')} />
              <BulkButton
                label="Reject curator score < 50"
                disabled={actingBusy || !funnel?.curator_run}
                onClick={() => runBulkAction(
                  { curator_score_lt: 50, status: ['draft', 'reviewed'] },
                  'rejected',
                  'Auto: curator scored < 50 (low value).',
                  'Reject score < 50')} />
              <Divider sx={{ my: 0.5 }} />
              <BulkButton
                label="Approve curator-recommended ≥85"
                color="success"
                disabled={actingBusy || !funnel?.curator_run}
                onClick={() => runBulkAction(
                  { curator_score_gte: 85, curator_recommended: true,
                    status: ['draft', 'reviewed'] },
                  'approved',
                  'Auto: curator strongly recommends.',
                  'Approve high-confidence recommended')} />
            </Stack>
          </CardContent>
        </Card>

        {/* (2) Curator launcher */}
        <Card variant="outlined" sx={{ flex: 1 }}>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <CuratorIcon color="primary" />
              <Typography variant="h6">Strategic Curator</Typography>
            </Stack>
            <Typography variant="caption" color="text.secondary" component="div">
              For every draft/reviewed question, the curator pulls the most-relevant
              2026 RFP text and asks the LLM:<br />
              • Is the answer already in the RFP?<br />
              • Would this reveal our strategy?<br />
              • Is it a trick removal question?<br />
              • Is the wording too vague to elicit a useful answer?<br />
              Each Q gets a 0-100 score and a reason.
            </Typography>

            {curateJob && curateJob.status === 'running' && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="body2">
                  Processing {curateJob.processed}/{curateJob.total}
                  {curateJob.total ? ` (${curateJob.recommended} recommended so far)` : ''}
                </Typography>
                <LinearProgress
                  variant={curateJob.total ? 'determinate' : 'indeterminate'}
                  value={curateJob.total ?
                    Math.round(100 * (curateJob.processed || 0) / curateJob.total) : 0}
                  sx={{ mt: 0.5 }} />
              </Box>
            )}
            {curateJob && curateJob.status === 'complete' && (
              <Alert severity="success" sx={{ mt: 2 }}>
                Curator complete: {curateJob.recommended} recommended,{' '}
                {curateJob.rejected} not recommended,{' '}
                {curateJob.errors} errors out of {curateJob.processed}.
              </Alert>
            )}
            {curateJob && curateJob.status === 'failed' && (
              <Alert severity="error" sx={{ mt: 2 }}>
                Curator failed: {curateJob.error}
              </Alert>
            )}

            <Button
              variant="contained"
              startIcon={curatorBusy ? <CircularProgress size={16} /> : <RunIcon />}
              fullWidth
              sx={{ mt: 2 }}
              disabled={curatorBusy ||
                       (curateJob && (curateJob.status === 'queued' ||
                                      curateJob.status === 'running'))}
              onClick={startCurate}
            >
              {curateJob && curateJob.status === 'running' ?
                'Curator running…' : 'Run Strategic Curator'}
            </Button>
          </CardContent>
        </Card>

        {/* (3) Submission ready */}
        <Card variant="outlined" sx={{ flex: 1 }}>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <ApproveIcon color="success" />
              <Typography variant="h6">Submission ready</Typography>
            </Stack>
            <Typography variant="h3" sx={{ my: 1 }}>
              {funnel?.submission_ready || 0}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Questions with status='approved'. Use the export buttons to
              produce the file you'll submit to the agency.
            </Typography>
            <Stack direction="row" spacing={1} sx={{ mt: 2 }} flexWrap="wrap">
              <Button size="small" variant="outlined" startIcon={<ExportIcon />}
                href={`/api/questions/export/${proposalId}?format=njstart&status=approved`}
                target="_blank" rel="noopener">
                NJSTART (txt)
              </Button>
              <Button size="small" variant="outlined" startIcon={<ExportIcon />}
                href={`/api/questions/export/${proposalId}?format=docx&status=approved`}
                target="_blank" rel="noopener">
                Word
              </Button>
            </Stack>
          </CardContent>
        </Card>
      </Stack>

      {/* ── Custom filter ───────────────────────────────────────── */}
      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Custom bulk action</Typography>
          <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
            <FormControl size="small" sx={{ minWidth: 180 }}>
              <InputLabel>Source</InputLabel>
              <Select label="Source" value={filterSource}
                onChange={e => setFilterSource(e.target.value)}>
                <MenuItem value="">(any)</MenuItem>
                {SOURCE_KINDS.map(k =>
                  <MenuItem key={k.value} value={k.value}>{k.label}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 130 }}>
              <InputLabel>Priority</InputLabel>
              <Select label="Priority" value={filterPriority}
                onChange={e => setFilterPriority(e.target.value)}>
                <MenuItem value="">(any)</MenuItem>
                {PRIORITIES.map(p =>
                  <MenuItem key={p} value={p}>{p}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>Inference</InputLabel>
              <Select label="Inference" value={filterInference}
                onChange={e => setFilterInference(e.target.value)}>
                <MenuItem value="">(any)</MenuItem>
                <MenuItem value="true">Yes</MenuItem>
                <MenuItem value="false">No</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 200 }}>
              <InputLabel>Curator reason</InputLabel>
              <Select label="Curator reason" value={filterCuratorReason}
                onChange={e => setFilterCuratorReason(e.target.value)}>
                <MenuItem value="">(any)</MenuItem>
                {CURATOR_REASONS.map(r =>
                  <MenuItem key={r.value} value={r.value}>{r.label}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField size="small" label="Score < " type="number"
              value={filterScoreLT} onChange={e => setFilterScoreLT(e.target.value)}
              sx={{ width: 100 }} />
            <TextField size="small" label="Score ≥" type="number"
              value={filterScoreGTE} onChange={e => setFilterScoreGTE(e.target.value)}
              sx={{ width: 100 }} />
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>New status</InputLabel>
              <Select label="New status" value={customNewStatus}
                onChange={e => setCustomNewStatus(e.target.value)}>
                {STATUSES.map(s =>
                  <MenuItem key={s} value={s}>{s}</MenuItem>)}
              </Select>
            </FormControl>
            <Button variant="contained" disabled={actingBusy}
                    onClick={runCustomBulk}>Apply</Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            Default status filter is draft+reviewed. Filters AND together. Click
            Apply to preview the count and confirm the change.
          </Typography>
        </CardContent>
      </Card>

      {/* ── Stats grid ──────────────────────────────────────────── */}
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
        <StatCard title="By source" data={stats?.by_source_kind} />
        <StatCard title="By priority" data={stats?.by_priority} order={PRIORITIES} />
        <StatCard title="By status" data={stats?.by_status} order={STATUSES} />
        <StatCard title="By reconciliation" data={stats?.by_reconciliation}
                  order={RECONCILIATIONS} />
        <StatCard title="By curator reason"
                  data={stats?.by_curator_reason}
                  order={CURATOR_REASONS.map(r => r.value)} />
      </Stack>

      <Snackbar open={!!toast} autoHideDuration={5000} onClose={() => setToast(null)}>
        {toast && <Alert severity={toast.severity}>{toast.msg}</Alert>}
      </Snackbar>
    </Box>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────

function FunnelStat({ label, value, color = 'default', tip }) {
  const node = (
    <Box sx={{ textAlign: 'center', minWidth: 100 }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h4" sx={{
        fontWeight: 700,
        color: color === 'success' ? '#2e7d32' :
               color === 'primary' ? '#1976d2' :
               color === 'info' ? '#0288d1' :
               color === 'warning' ? '#ed6c02' :
               'text.primary',
      }}>{value.toLocaleString()}</Typography>
    </Box>
  );
  return tip ? <Tooltip title={tip}>{node}</Tooltip> : node;
}

function FunnelArrow() {
  return <Typography variant="h5" color="text.disabled">→</Typography>;
}

function BulkButton({ label, color = 'inherit', disabled, onClick }) {
  return (
    <Button variant="outlined" size="small" color={color}
            disabled={disabled} onClick={onClick}
            sx={{ justifyContent: 'flex-start' }}>
      {label}
    </Button>
  );
}

function StatCard({ title, data, order }) {
  if (!data) return null;
  let entries = Object.entries(data);
  if (order) {
    entries = order
      .filter(k => data[k] !== undefined)
      .map(k => [k, data[k]])
      .concat(entries.filter(([k]) => !order.includes(k)));
  }
  if (entries.length === 0) return null;
  return (
    <Card variant="outlined" sx={{ flex: 1, minWidth: 200 }}>
      <CardContent>
        <Typography variant="overline" color="text.secondary">{title}</Typography>
        <Box sx={{ mt: 1 }}>
          {entries.map(([k, v]) => (
            <Stack key={k} direction="row" justifyContent="space-between"
                   sx={{ py: 0.25, fontSize: '0.85rem' }}>
              <Typography variant="body2" sx={{ flex: 1 }}>{k}</Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {v.toLocaleString()}
              </Typography>
            </Stack>
          ))}
        </Box>
      </CardContent>
    </Card>
  );
}
