import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';import { exportToExcel } from '../utils/exportExcel';import {
  Box, Typography, Card, CardContent, Grid, Button, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert,
  IconButton, Tooltip, MenuItem, Select, FormControl, InputLabel,
  Stack, Divider, Snackbar, Tabs, Tab, Checkbox, FormControlLabel,
  Accordion, AccordionSummary, AccordionDetails,
} from '@mui/material';
import {
  AccountTree as CoverageIcon,
  Refresh as RefreshIcon,
  PlayArrow as RunIcon,
  Check as AcceptIcon,
  Close as RejectIcon,
  ExpandMore as ExpandMoreIcon,
  Flag as FlagIcon,
} from '@mui/icons-material';
import axios from 'axios';

// ── Visual tokens ───────────────────────────────────────────────────
const COVERAGE_COLOR = {
  gap: 'error',
  partial: 'warning',
  covered: 'success',
};
const COVERAGE_LABEL = {
  gap: 'Gap',
  partial: 'Partial',
  covered: 'Covered',
};
const SEVERITY_COLOR = {
  critical: 'error', high: 'warning', medium: 'info', low: 'default', informational: 'default',
};
const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3, informational: 4, '': 5 };
const STATUS_COLOR = {
  pending: 'default', accepted: 'info', applied: 'success', rejected: 'default',
};

export default function PricingCoverage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

  const [proposals, setProposals] = useState([]);
  const [models, setModels] = useState([]);
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const [newRunOpen, setNewRunOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(null); // suggestion object
  const [reviewNotes, setReviewNotes] = useState('');
  const [targetCategoryId, setTargetCategoryId] = useState('');

  const [tab, setTab] = useState(0); // 0=gap, 1=partial, 2=covered
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState(''); // pending / applied / rejected

  // URL params drive default model + proposal
  const proposalFromUrl = params.get('proposal_id');
  const modelFromUrl = params.get('model_id');

  const selectedProposalId = useMemo(
    () => (proposalFromUrl ? Number(proposalFromUrl) : (proposals[0]?.id ?? null)),
    [proposalFromUrl, proposals]
  );
  const selectedModelId = useMemo(
    () => (modelFromUrl ? Number(modelFromUrl) : (models[0]?.id ?? null)),
    [modelFromUrl, models]
  );

  // ── Loaders ───────────────────────────────────────────────────────
  const loadRuns = useCallback(async () => {
    if (!selectedModelId) return;
    try {
      const { data } = await axios.get('/api/pricing/coverage/runs', {
        params: {
          pricing_model_id: selectedModelId,
          ...(selectedProposalId ? { proposal_id: selectedProposalId } : {}),
        },
      });
      setRuns(data || []);
      if (!activeRunId && data && data.length) {
        const completed = data.find(r => r.status === 'completed') || data[0];
        setActiveRunId(completed.id);
      }
    } catch (e) {
      setToast({ severity: 'error', msg: 'Failed to load runs: ' + (e.response?.data?.detail || e.message) });
    }
  }, [selectedModelId, selectedProposalId, activeRunId]);

  const loadSuggestions = useCallback(async () => {
    if (!activeRunId) { setSuggestions([]); return; }
    setLoading(true);
    try {
      const q = { run_id: activeRunId, include_requirement: true, limit: 1000 };
      const coverage_status = ['gap', 'partial', 'covered'][tab];
      if (coverage_status) q.coverage_status = coverage_status;
      if (severityFilter) q.severity = severityFilter;
      if (statusFilter) q.status = statusFilter;
      const { data } = await axios.get('/api/pricing/coverage/suggestions', { params: q });
      setSuggestions(data || []);
    } catch (e) {
      setToast({ severity: 'error', msg: 'Failed to load suggestions: ' + (e.response?.data?.detail || e.message) });
    } finally {
      setLoading(false);
    }
  }, [activeRunId, tab, severityFilter, statusFilter]);

  // Initial data — proposals + models
  useEffect(() => {
    (async () => {
      try {
        const [pRes, mRes] = await Promise.all([
          axios.get('/api/proposals/').catch(() => ({ data: [] })),
          axios.get('/api/pricing/models').catch(() => ({ data: [] })),
        ]);
        setProposals(pRes.data || []);
        setModels(mRes.data || []);
      } catch { /* ignore */ }
    })();
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);
  useEffect(() => { loadSuggestions(); }, [loadSuggestions]);

  // Auto-refresh while a run is in progress
  useEffect(() => {
    const busy = runs.some(r => ['queued', 'running'].includes(r.status));
    if (!busy) return;
    const t = setInterval(() => { loadRuns(); loadSuggestions(); }, 5000);
    return () => clearInterval(t);
  }, [runs, loadRuns, loadSuggestions]);

  const activeRun = useMemo(() => runs.find(r => r.id === activeRunId), [runs, activeRunId]);
  const activeModel = useMemo(() => models.find(m => m.id === selectedModelId), [models, selectedModelId]);

  // Full category list for the active model (to let user pick a target category on accept)
  const [fullModel, setFullModel] = useState(null);
  useEffect(() => {
    if (!selectedModelId) { setFullModel(null); return; }
    axios.get(`/api/pricing/models/${selectedModelId}`)
      .then(r => setFullModel(r.data))
      .catch(() => setFullModel(null));
  }, [selectedModelId]);

  // ── Actions ───────────────────────────────────────────────────────
  const handleRun = async (body) => {
    try {
      await axios.post('/api/pricing/coverage/run', body);
      setToast({ severity: 'success', msg: 'Coverage analysis started. Refreshing every few seconds.' });
      setNewRunOpen(false);
      setTimeout(() => { loadRuns(); }, 1500);
    } catch (e) {
      setToast({ severity: 'error', msg: 'Failed to start run: ' + (e.response?.data?.detail || e.message) });
    }
  };

  const openReview = (sugg) => {
    setReviewOpen(sugg);
    setReviewNotes('');
    setTargetCategoryId('');
  };

  const closeReview = () => {
    setReviewOpen(null);
    setReviewNotes('');
    setTargetCategoryId('');
  };

  const submitAccept = async () => {
    if (!reviewOpen) return;
    try {
      await axios.post(`/api/pricing/coverage/suggestions/${reviewOpen.id}/accept`, {
        review_notes: reviewNotes || null,
        target_category_id: targetCategoryId ? Number(targetCategoryId) : null,
      });
      setToast({ severity: 'success', msg: 'Accepted — line item created.' });
      closeReview();
      loadSuggestions();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Accept failed: ' + (e.response?.data?.detail || e.message) });
    }
  };

  const submitReject = async () => {
    if (!reviewOpen) return;
    try {
      await axios.post(`/api/pricing/coverage/suggestions/${reviewOpen.id}/reject`, {
        review_notes: reviewNotes || null,
      });
      setToast({ severity: 'info', msg: 'Rejected.' });
      closeReview();
      loadSuggestions();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Reject failed: ' + (e.response?.data?.detail || e.message) });
    }
  };

  // Sorted view: severity asc, then pending first
  const sortedSuggestions = useMemo(() => {
    const statusOrder = { pending: 0, accepted: 1, applied: 2, rejected: 3 };
    return [...suggestions].sort((a, b) => {
      const sa = SEVERITY_ORDER[a.severity || ''] ?? 5;
      const sb = SEVERITY_ORDER[b.severity || ''] ?? 5;
      if (sa !== sb) return sa - sb;
      const oa = statusOrder[a.status] ?? 9;
      const ob = statusOrder[b.status] ?? 9;
      return oa - ob;
    });
  }, [suggestions]);

  const exportExcel = () => {
    if (!sortedSuggestions.length) return;
    exportToExcel({
      filename: `pricing-coverage-run-${activeRunId || 'all'}`,
      sheetName: 'Coverage',
      columns: [
        { header: 'Coverage', key: 'coverage_status', width: 12 },
        { header: 'Severity', key: 'severity', width: 12 },
        { header: 'Confidence', key: 'confidence', width: 12 },
        { header: 'Review Status', key: 'status', width: 14 },
        { header: 'Requirement Title', key: 'requirement', width: 45, transform: (v) => v?.title || '' },
        { header: 'Req Category', key: 'requirement', width: 16, transform: (v) => v?.category || '' },
        { header: 'Req Section', key: 'requirement', width: 12, transform: (v) => v?.section_id || '' },
        { header: 'Req Page', key: 'requirement', width: 8, transform: (v) => v?.source_page || '' },
        { header: 'Req Description', key: 'requirement', width: 60, transform: (v) => v?.description || '' },
        { header: 'Matched Line IDs', key: 'matched_line_item_ids', width: 18, transform: (v) => (v || []).join(', ') },
        { header: 'Proposed Tab', key: 'proposed_tab', width: 14 },
        { header: 'Proposed Category', key: 'proposed_category_name', width: 20 },
        { header: 'Proposed Line', key: 'proposed_line_name', width: 35 },
        { header: 'Proposed Unit', key: 'proposed_unit', width: 12 },
        { header: 'Proposed Allocation', key: 'proposed_allocation_basis', width: 16 },
        { header: 'Proposed Qty', key: 'proposed_qty', width: 10 },
        { header: 'Rationale', key: 'rationale', width: 50 },
        { header: 'Review Notes', key: 'review_notes', width: 30 },
      ],
      rows: sortedSuggestions,
    });
  };

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <CoverageIcon color="primary" />
        <Typography variant="h5">Pricing Coverage</Typography>
        <Box sx={{ flex: 1 }} />
        <Button startIcon={<RefreshIcon />} onClick={() => { loadRuns(); loadSuggestions(); }}>
          Refresh
        </Button>
        <Button
          startIcon={<RunIcon />}
          variant="contained"
          onClick={() => setNewRunOpen(true)}
          disabled={!selectedModelId}
        >
          New Coverage Run
        </Button>
        <Button variant="outlined" onClick={exportExcel} disabled={sortedSuggestions.length === 0}>
          Export Excel
        </Button>
      </Stack>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Cross-checks every extracted RFP requirement against the selected pricing model's line items.
        Gap items become proposed new line items with a <strong>$0 starting cost</strong> (you review &amp;
        fill in the number). Partial items flag what already exists but needs expansion. Covered items
        let you link a requirement to one or more existing lines for audit traceability.
      </Typography>

      {/* Scope selector */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Grid container spacing={2}>
            <Grid item xs={12} md={4}>
              <FormControl fullWidth size="small">
                <InputLabel>Pricing Model</InputLabel>
                <Select
                  label="Pricing Model"
                  value={selectedModelId || ''}
                  onChange={(e) => {
                    const v = e.target.value;
                    const next = new URLSearchParams(params);
                    if (v) next.set('model_id', String(v)); else next.delete('model_id');
                    setParams(next);
                    setActiveRunId(null);
                  }}
                >
                  {models.map(m => (
                    <MenuItem key={m.id} value={m.id}>
                      {m.name} (#{m.id})
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12} md={4}>
              <FormControl fullWidth size="small">
                <InputLabel>Proposal</InputLabel>
                <Select
                  label="Proposal"
                  value={selectedProposalId || ''}
                  onChange={(e) => {
                    const v = e.target.value;
                    const next = new URLSearchParams(params);
                    if (v) next.set('proposal_id', String(v)); else next.delete('proposal_id');
                    setParams(next);
                    setActiveRunId(null);
                  }}
                >
                  {proposals.map(p => (
                    <MenuItem key={p.id} value={p.id}>
                      #{p.id} — {p.title || p.solicitation_number || 'Proposal ' + p.id}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12} md={4}>
              <Button
                variant="outlined"
                onClick={() => navigate(`/pricing?model=${selectedModelId || ''}`)}
                disabled={!selectedModelId}
                fullWidth
                sx={{ height: 40 }}
              >
                Open this model in Pricing Editor
              </Button>
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      {/* Runs list */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>Coverage Runs</Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>#</TableCell>
                  <TableCell>Label</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Scanned</TableCell>
                  <TableCell>Cost-bearing</TableCell>
                  <TableCell>Gaps</TableCell>
                  <TableCell>Partial</TableCell>
                  <TableCell>Covered</TableCell>
                  <TableCell>Started</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {runs.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={9} align="center">
                      <Typography variant="body2" color="text.secondary">
                        No coverage runs yet. Click "New Coverage Run" above.
                      </Typography>
                    </TableCell>
                  </TableRow>
                )}
                {runs.map(r => (
                  <TableRow
                    key={r.id}
                    hover
                    selected={r.id === activeRunId}
                    onClick={() => setActiveRunId(r.id)}
                    sx={{ cursor: 'pointer' }}
                  >
                    <TableCell>{r.id}</TableCell>
                    <TableCell>{r.label || '(unlabeled)'}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={r.status}
                        color={
                          r.status === 'completed' ? 'success' :
                          r.status === 'failed' ? 'error' :
                          r.status === 'running' ? 'info' : 'default'
                        }
                      />
                    </TableCell>
                    <TableCell>{r.requirements_scanned}</TableCell>
                    <TableCell>{r.cost_bearing_count}</TableCell>
                    <TableCell>
                      <Chip size="small" label={r.gap_count} color="error" variant="outlined" />
                    </TableCell>
                    <TableCell>
                      <Chip size="small" label={r.partial_count} color="warning" variant="outlined" />
                    </TableCell>
                    <TableCell>
                      <Chip size="small" label={r.covered_count} color="success" variant="outlined" />
                    </TableCell>
                    <TableCell>
                      {r.started_at ? new Date(r.started_at).toLocaleString() : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {loading && <LinearProgress sx={{ mb: 2 }} />}
      {activeRun && activeRun.status === 'running' && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Run in progress — {activeRun.requirements_scanned} requirements scanned so far.
          This page will refresh automatically every few seconds.
        </Alert>
      )}
      {activeRun && activeRun.status === 'failed' && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Run failed: {activeRun.error_message || '(no error message)'}
        </Alert>
      )}

      {/* Tabs + filters */}
      {activeRunId && (
        <Card>
          <CardContent>
            <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
              <Tabs value={tab} onChange={(_, v) => setTab(v)}>
                <Tab label={`Gaps (${activeRun?.gap_count ?? 0})`} />
                <Tab label={`Partial (${activeRun?.partial_count ?? 0})`} />
                <Tab label={`Covered (${activeRun?.covered_count ?? 0})`} />
              </Tabs>
              <Box sx={{ flex: 1 }} />
              <FormControl size="small" sx={{ minWidth: 140 }}>
                <InputLabel>Severity</InputLabel>
                <Select
                  label="Severity"
                  value={severityFilter}
                  onChange={(e) => setSeverityFilter(e.target.value)}
                >
                  <MenuItem value="">All</MenuItem>
                  <MenuItem value="critical">Critical</MenuItem>
                  <MenuItem value="high">High</MenuItem>
                  <MenuItem value="medium">Medium</MenuItem>
                  <MenuItem value="low">Low</MenuItem>
                  <MenuItem value="informational">Informational</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 140 }}>
                <InputLabel>Status</InputLabel>
                <Select
                  label="Status"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  <MenuItem value="">All</MenuItem>
                  <MenuItem value="pending">Pending</MenuItem>
                  <MenuItem value="accepted">Accepted</MenuItem>
                  <MenuItem value="applied">Applied</MenuItem>
                  <MenuItem value="rejected">Rejected</MenuItem>
                </Select>
              </FormControl>
            </Stack>

            <Divider sx={{ mb: 2 }} />

            {sortedSuggestions.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
                No items in this view.
              </Typography>
            ) : (
              sortedSuggestions.map(s => (
                <SuggestionRow
                  key={s.id}
                  s={s}
                  onReview={() => openReview(s)}
                />
              ))
            )}
          </CardContent>
        </Card>
      )}

      {/* New run dialog */}
      <NewRunDialog
        open={newRunOpen}
        onClose={() => setNewRunOpen(false)}
        onRun={handleRun}
        modelId={selectedModelId}
        proposalId={selectedProposalId}
        modelName={activeModel?.name}
      />

      {/* Review dialog */}
      <ReviewDialog
        open={!!reviewOpen}
        suggestion={reviewOpen}
        categories={fullModel?.categories || []}
        reviewNotes={reviewNotes}
        setReviewNotes={setReviewNotes}
        targetCategoryId={targetCategoryId}
        setTargetCategoryId={setTargetCategoryId}
        onAccept={submitAccept}
        onReject={submitReject}
        onClose={closeReview}
      />

      <Snackbar
        open={!!toast}
        autoHideDuration={5000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        {toast && (
          <Alert severity={toast.severity} onClose={() => setToast(null)} sx={{ width: '100%' }}>
            {toast.msg}
          </Alert>
        )}
      </Snackbar>
    </Box>
  );
}

// ── Sub-components ────────────────────────────────────────────────────

function SuggestionRow({ s, onReview }) {
  const isGap = s.coverage_status === 'gap';
  const isPartial = s.coverage_status === 'partial';
  const req = s.requirement || {};
  return (
    <Accordion variant="outlined" sx={{ mb: 1 }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ width: '100%' }}>
          <Chip
            size="small"
            label={COVERAGE_LABEL[s.coverage_status] || s.coverage_status}
            color={COVERAGE_COLOR[s.coverage_status] || 'default'}
          />
          <Chip
            size="small"
            label={s.severity || 'informational'}
            color={SEVERITY_COLOR[s.severity] || 'default'}
            variant="outlined"
          />
          {s.status !== 'pending' && (
            <Chip
              size="small"
              label={s.status}
              color={STATUS_COLOR[s.status] || 'default'}
            />
          )}
          <Typography variant="body2" sx={{ flex: 1, fontWeight: 500 }}>
            {req.title || '(no title)'}
          </Typography>
          {req.source_page && (
            <Typography variant="caption" color="text.secondary">
              p. {req.source_page}
            </Typography>
          )}
        </Stack>
      </AccordionSummary>
      <AccordionDetails>
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <Typography variant="overline" color="text.secondary">Requirement</Typography>
            <Typography variant="body2" sx={{ mb: 1 }}>
              {req.description || '(no description)'}
            </Typography>
            {req.source_text && (
              <Paper variant="outlined" sx={{ p: 1, bgcolor: 'grey.50', maxHeight: 160, overflow: 'auto' }}>
                <Typography variant="caption" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                  {req.source_text}
                </Typography>
              </Paper>
            )}
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              {req.category && <Chip size="small" label={req.category} variant="outlined" />}
              {req.priority && <Chip size="small" label={req.priority} variant="outlined" />}
              {req.section_id && (
                <Chip size="small" label={`§ ${req.section_id}`} variant="outlined" />
              )}
            </Stack>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="overline" color="text.secondary">
              {isGap ? 'Proposed New Line' : isPartial ? 'Expand Coverage' : 'Matched Lines'}
            </Typography>
            {s.coverage_status !== 'covered' && (
              <Box sx={{ mb: 1 }}>
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  {s.proposed_line_name || '(no proposed name)'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {s.proposed_tab || '—'} / {s.proposed_category_name || '—'} ·{' '}
                  {s.proposed_allocation_basis || 'SHARED'} ·{' '}
                  qty {s.proposed_qty ?? 'TBD'} {s.proposed_unit || ''}
                </Typography>
                {s.proposed_description && (
                  <Typography variant="body2" sx={{ mt: 0.5 }}>
                    {s.proposed_description}
                  </Typography>
                )}
              </Box>
            )}
            {s.matched_line_item_ids && s.matched_line_item_ids.length > 0 && (
              <Box sx={{ mb: 1 }}>
                <Typography variant="caption" color="text.secondary">
                  Matches line items: {s.matched_line_item_ids.join(', ')}
                </Typography>
              </Box>
            )}
            {s.rationale && (
              <Typography variant="body2" sx={{ mt: 1, fontStyle: 'italic' }}>
                <FlagIcon fontSize="inherit" sx={{ mr: 0.5, verticalAlign: 'middle' }} />
                {s.rationale}
              </Typography>
            )}
            {s.confidence && (
              <Chip
                size="small"
                label={`confidence: ${s.confidence}`}
                sx={{ mt: 1 }}
                variant="outlined"
              />
            )}
            {s.status === 'pending' && (
              <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
                <Button
                  size="small"
                  variant="contained"
                  color="success"
                  startIcon={<AcceptIcon />}
                  onClick={onReview}
                >
                  Review &amp; Accept
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  color="inherit"
                  startIcon={<RejectIcon />}
                  onClick={onReview}
                >
                  Review &amp; Reject
                </Button>
              </Stack>
            )}
            {s.review_notes && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                Notes: {s.review_notes}
              </Typography>
            )}
          </Grid>
        </Grid>
      </AccordionDetails>
    </Accordion>
  );
}

function NewRunDialog({ open, onClose, onRun, modelId, proposalId, modelName }) {
  const [label, setLabel] = useState('');
  const [limit, setLimit] = useState('');

  useEffect(() => {
    if (open) {
      const ts = new Date().toISOString().slice(0, 16).replace('T', ' ');
      setLabel(`${modelName || 'Coverage'} — ${ts}`);
      setLimit('');
    }
  }, [open, modelName]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New Coverage Run</DialogTitle>
      <DialogContent>
        <Alert severity="info" sx={{ mb: 2 }}>
          Scans every <strong>cost-bearing</strong> requirement on proposal #{proposalId}
          &nbsp;against pricing model #{modelId}. Runs in the background —
          safe to close this dialog and come back later. Uses Claude Opus 4.7
          on each cost-bearing requirement.
        </Alert>
        <TextField
          fullWidth
          label="Label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          margin="normal"
          size="small"
        />
        <TextField
          fullWidth
          label="Smoke-test limit (optional)"
          helperText="Leave blank to scan all requirements. Enter a number (e.g. 25) to cap for testing."
          value={limit}
          onChange={(e) => setLimit(e.target.value.replace(/[^0-9]/g, ''))}
          margin="normal"
          size="small"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => onRun({
            pricing_model_id: modelId,
            proposal_id: proposalId,
            label: label || null,
            max_requirements: limit ? Number(limit) : null,
          })}
          disabled={!modelId || !proposalId}
        >
          Start Run
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function ReviewDialog({
  open, suggestion, categories,
  reviewNotes, setReviewNotes,
  targetCategoryId, setTargetCategoryId,
  onAccept, onReject, onClose,
}) {
  if (!suggestion) return null;
  const isCovered = suggestion.coverage_status === 'covered';
  const req = suggestion.requirement || {};
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        Review — {COVERAGE_LABEL[suggestion.coverage_status]} · {req.title || ''}
      </DialogTitle>
      <DialogContent>
        <Typography variant="overline" color="text.secondary">Requirement</Typography>
        <Typography variant="body2" sx={{ mb: 2 }}>
          {req.description || '(no description)'}
        </Typography>
        {!isCovered && (
          <>
            <Typography variant="overline" color="text.secondary">Proposed new line item</Typography>
            <TableContainer component={Paper} variant="outlined" sx={{ mb: 2 }}>
              <Table size="small">
                <TableBody>
                  <TableRow><TableCell>Name</TableCell><TableCell>{suggestion.proposed_line_name}</TableCell></TableRow>
                  <TableRow><TableCell>Tab</TableCell><TableCell>{suggestion.proposed_tab}</TableCell></TableRow>
                  <TableRow><TableCell>Category (default)</TableCell><TableCell>{suggestion.proposed_category_name}</TableCell></TableRow>
                  <TableRow><TableCell>Unit</TableCell><TableCell>{suggestion.proposed_unit}</TableCell></TableRow>
                  <TableRow><TableCell>Allocation</TableCell><TableCell>{suggestion.proposed_allocation_basis}</TableCell></TableRow>
                  <TableRow><TableCell>Qty</TableCell><TableCell>{suggestion.proposed_qty}</TableCell></TableRow>
                  {suggestion.proposed_description && (
                    <TableRow><TableCell>Description</TableCell><TableCell>{suggestion.proposed_description}</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
            <FormControl fullWidth size="small" sx={{ mb: 2 }}>
              <InputLabel>Target Category (override)</InputLabel>
              <Select
                label="Target Category (override)"
                value={targetCategoryId}
                onChange={(e) => setTargetCategoryId(e.target.value)}
              >
                <MenuItem value="">
                  <em>Use proposed: {suggestion.proposed_category_name || '(create new)'}</em>
                </MenuItem>
                {categories.map(c => (
                  <MenuItem key={c.id} value={c.id}>
                    {c.tab} › {c.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Alert severity="warning" sx={{ mb: 2 }}>
              New line item will start with <strong>$0 current cost, $0 future cost</strong>.
              Fill in real numbers in the Pricing Editor after accepting.
            </Alert>
          </>
        )}
        <TextField
          fullWidth
          label="Review Notes (optional)"
          value={reviewNotes}
          onChange={(e) => setReviewNotes(e.target.value)}
          multiline
          rows={2}
          size="small"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={onReject} color="inherit">Reject</Button>
        <Button onClick={onAccept} variant="contained" color="success">
          {isCovered ? 'Accept (no new line)' : 'Accept &amp; Create Line'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
