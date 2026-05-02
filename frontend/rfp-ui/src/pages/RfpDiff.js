import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';import { exportToExcel } from '../utils/exportExcel';import {
  Box, Typography, Card, CardContent, Grid, Button, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert,
  IconButton, Tooltip, MenuItem, Select, FormControl, InputLabel,
  Stack, Divider, Snackbar, ToggleButtonGroup, ToggleButton, Drawer,
  TablePagination, InputAdornment,
} from '@mui/material';
import {
  CompareArrows as CompareIcon,
  Refresh as RefreshIcon,
  PlayArrow as RunIcon,
  Add as AddIcon,
  Flag as FlagIcon,
  Delete as DeleteIcon,
  NavigateNext as DetailIcon,
  Search as SearchIcon,
} from '@mui/icons-material';
import axios from 'axios';

const STATUS_COLOR = {
  added: 'success', removed: 'error', changed: 'warning', unchanged: 'default',
};
const SEVERITY_COLOR = {
  critical: 'error', high: 'warning', medium: 'info', low: 'default', informational: 'default',
};

export default function RfpDiff() {
  const [params] = useSearchParams();
  const proposalFromUrl = params.get('proposal');

  const [proposals, setProposals] = useState([]);
  const [docs, setDocs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [rows, setRows] = useState([]);
  const [totalRows, setTotalRows] = useState(0);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [reviewerFilter, setReviewerFilter] = useState('');
  const [searchText, setSearchText] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);
  const [newRunOpen, setNewRunOpen] = useState(false);
  const [detailRow, setDetailRow] = useState(null);
  const [toast, setToast] = useState(null);

  // Debounce the search input so we don't hammer the backend while typing.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchText.trim()), 350);
    return () => clearTimeout(t);
  }, [searchText]);

  // Reset to first page whenever the filter set changes.
  useEffect(() => {
    setPage(0);
  }, [activeRunId, statusFilter, severityFilter, reviewerFilter, debouncedSearch, rowsPerPage]);

  const targetProposalId = proposalFromUrl || (proposals[0] && proposals[0].id);

  const loadRuns = useCallback(async () => {
    if (!targetProposalId) return;
    try {
      const { data } = await axios.get('/api/rfp-diff/runs', {
        params: { target_proposal_id: targetProposalId },
      });
      setRuns(data || []);
      if (!activeRunId && data && data.length) {
        // Prefer a complete run
        const complete = data.find(r => r.status === 'complete') || data[0];
        setActiveRunId(complete.id);
      }
    } catch (e) {
      setToast({ severity: 'error', msg: 'Failed to load runs: ' + (e.response?.data?.detail || e.message) });
    }
  }, [targetProposalId, activeRunId]);

  const loadRows = useCallback(async () => {
    if (!activeRunId) { setRows([]); setTotalRows(0); return; }
    setLoading(true);
    try {
      const params = { limit: rowsPerPage, offset: page * rowsPerPage };
      if (statusFilter) params.status = statusFilter;
      if (severityFilter) params.severity = severityFilter;
      if (reviewerFilter) params.reviewer_status = reviewerFilter;
      if (debouncedSearch) params.search = debouncedSearch;
      const { data } = await axios.get(`/api/rfp-diff/runs/${activeRunId}/rows`, { params });
      // New envelope: { total, offset, limit, returned, rows }
      // Old shape was a plain array — fall back to it just in case.
      if (Array.isArray(data)) {
        setRows(data);
        setTotalRows(data.length);
      } else {
        setRows(data.rows || []);
        setTotalRows(data.total || 0);
      }
    } catch (e) {
      setToast({ severity: 'error', msg: 'Failed to load rows: ' + (e.response?.data?.detail || e.message) });
    } finally {
      setLoading(false);
    }
  }, [activeRunId, statusFilter, severityFilter, reviewerFilter, debouncedSearch, page, rowsPerPage]);

  useEffect(() => {
    (async () => {
      try {
        const [pRes, dRes] = await Promise.all([
          axios.get('/api/proposals/').catch(() => ({ data: [] })),
          axios.get('/api/documents').catch(() => ({ data: [] })),
        ]);
        setProposals(pRes.data || []);
        // /api/documents returns { documents: [...] }
        const docsPayload = dRes.data;
        setDocs(Array.isArray(docsPayload) ? docsPayload : (docsPayload?.documents || []));
      } catch { /* ignore */ }
    })();
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);
  useEffect(() => { loadRows(); }, [loadRows]);

  // Auto-refresh while a run is in progress
  useEffect(() => {
    const busy = runs.some(r => ['queued', 'running', 'pending'].includes(r.status));
    if (!busy) return;
    const t = setInterval(() => { loadRuns(); loadRows(); }, 5000);
    return () => clearInterval(t);
  }, [runs, loadRuns, loadRows]);

  const activeRun = useMemo(() => runs.find(r => r.id === activeRunId), [runs, activeRunId]);

  // Backend already orders by severity→status→id, so no client sort needed.
  const sortedRows = rows;

  const updateReviewer = async (row, reviewer_status) => {
    try {
      await axios.patch(`/api/rfp-diff/rows/${row.id}`, { reviewer_status });
      loadRows();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Save failed: ' + (e.response?.data?.detail || e.message) });
    }
  };

  const deleteRun = async (run) => {
    if (!window.confirm(`Delete diff run #${run.id} and all its rows?`)) return;
    try {
      await axios.delete(`/api/rfp-diff/runs/${run.id}`);
      if (activeRunId === run.id) setActiveRunId(null);
      loadRuns();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Delete failed: ' + (e.response?.data?.detail || e.message) });
    }
  };

  const exportExcel = async () => {
    if (!activeRunId) return;
    try {
      // Fetch ALL rows for this run (no pagination)
      const { data } = await axios.get(`/api/rfp-diff/runs/${activeRunId}/rows`, { params: { limit: 5000 } });
      const allRows = Array.isArray(data) ? data : (data.rows || []);
      exportToExcel({
        filename: `diff-run-${activeRunId}`,
        sheetName: 'Requirement Diff',
        columns: [
          { header: 'Status', key: 'status', width: 12 },
          { header: 'Severity', key: 'impact_severity', width: 14 },
          { header: 'Similarity', key: 'similarity', width: 12, transform: (v) => v != null ? Number(v.toFixed(3)) : '' },
          { header: 'Baseline Section', key: 'baseline', width: 14, transform: (_, r) => r.baseline?.section_id || '' },
          { header: 'Baseline Title', key: 'baseline', width: 45, transform: (_, r) => r.baseline?.title || '' },
          { header: 'Baseline Description', key: 'baseline', width: 60, transform: (_, r) => r.baseline?.description || '' },
          { header: 'Baseline Page', key: 'baseline', width: 10, transform: (_, r) => r.baseline?.source_page || '' },
          { header: 'Target Section', key: 'target', width: 14, transform: (_, r) => r.target?.section_id || '' },
          { header: 'Target Title', key: 'target', width: 45, transform: (_, r) => r.target?.title || '' },
          { header: 'Target Description', key: 'target', width: 60, transform: (_, r) => r.target?.description || '' },
          { header: 'Target Page', key: 'target', width: 10, transform: (_, r) => r.target?.source_page || '' },
          { header: 'Change Summary', key: 'change_summary', width: 50 },
          { header: 'Impact / Why It Matters', key: 'impact_blurb', width: 60 },
          { header: 'Reviewer Status', key: 'reviewer_status', width: 14 },
          { header: 'Reviewer Notes', key: 'reviewer_notes', width: 30 },
        ],
        rows: allRows,
      });
    } catch (e) {
      setToast({ severity: 'error', msg: 'Excel export failed: ' + (e.response?.data?.detail || e.message) });
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <CompareIcon color="primary" />
        <Typography variant="h5">Solicitation Diff</Typography>
        <Box sx={{ flex: 1 }} />
        <Button startIcon={<RefreshIcon />} onClick={() => { loadRuns(); loadRows(); }}>Refresh</Button>
        <Button startIcon={<AddIcon />} variant="contained" onClick={() => setNewRunOpen(true)}>
          New Diff Run
        </Button>
        <Button variant="outlined" onClick={exportExcel} disabled={!activeRunId || rows.length === 0}>
          Export Excel
        </Button>
        {/* Direct hop into the diff→question pipeline for the active run */}
        <Button variant="outlined" color="primary"
                onClick={() => {
                  // route depending on whether we're in /p/:id/* or legacy
                  const m = window.location.pathname.match(/^\/p\/(\d+)\//);
                  if (m) window.location.href = `/p/${m[1]}/diff-questions`;
                  else window.location.href = '/diff-questions';
                }}>
          Generate Questions →
        </Button>
      </Stack>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Compare a baseline solicitation (prior revision or predecessor contract) against the current
        solicitation. The system pairs requirements by embedding similarity, asks the LLM to adjudicate
        borderline matches, and writes an impact blurb explaining what changed and why it matters.
      </Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Grid container spacing={2}>
            <Grid item xs={12} md={6}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Diff Runs</Typography>
              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>#</TableCell>
                      <TableCell>Label</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>+/-/~/=</TableCell>
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {runs.map(r => (
                      <TableRow
                        key={r.id}
                        hover
                        selected={r.id === activeRunId}
                        onClick={() => setActiveRunId(r.id)}
                        sx={{ cursor: 'pointer' }}
                      >
                        <TableCell>{r.id}</TableCell>
                        <TableCell>{r.label || '(unnamed)'}</TableCell>
                        <TableCell>
                          <Chip size="small" label={r.status}
                            color={r.status === 'complete' ? 'success' : r.status === 'failed' ? 'error' : 'info'} />
                        </TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          <span style={{ color: 'green' }}>+{r.counts_added}</span>
                          {' / '}
                          <span style={{ color: 'red' }}>-{r.counts_removed}</span>
                          {' / '}
                          <span style={{ color: 'orange' }}>~{r.counts_changed}</span>
                          {' / '}
                          <span style={{ color: 'gray' }}>={r.counts_unchanged}</span>
                        </TableCell>
                        <TableCell align="right">
                          <IconButton size="small" onClick={(e) => { e.stopPropagation(); deleteRun(r); }}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                    {runs.length === 0 && (
                      <TableRow><TableCell colSpan={5}>
                        <Typography variant="body2" color="text.secondary">
                          No diff runs yet for this proposal. Click "New Diff Run" to start one.
                        </Typography>
                      </TableCell></TableRow>
                    )}
                  </TableBody>
                </Table>
              </TableContainer>
            </Grid>
            <Grid item xs={12} md={6}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Active Run</Typography>
              {activeRun ? (
                <Paper variant="outlined" sx={{ p: 2, overflow: 'hidden' }}>
                  <Typography variant="body2" sx={{ wordBreak: 'break-word' }}><b>Label:</b> {activeRun.label || '—'}</Typography>
                  <Typography variant="body2" sx={{ wordBreak: 'break-word' }}><b>Status:</b> {activeRun.status}{activeRun.error ? ` — ${activeRun.error}` : ''}</Typography>
                  <Typography variant="body2"><b>Baseline:</b> {
                    activeRun.baseline_document_ids
                      ? `docs ${activeRun.baseline_document_ids.join(', ')}`
                      : `proposal ${activeRun.baseline_proposal_id}`
                  }</Typography>
                  <Typography variant="body2"><b>Target:</b> {
                    activeRun.target_document_ids
                      ? `docs ${activeRun.target_document_ids.join(', ')}`
                      : `proposal ${activeRun.target_proposal_id}`
                  }</Typography>
                  <Typography variant="body2">
                    <b>Thresholds:</b> high ≥ {activeRun.high_match_threshold}, low ≥ {activeRun.low_match_threshold}
                  </Typography>
                </Paper>
              ) : (
                <Typography variant="body2" color="text.secondary">Pick a run on the left.</Typography>
              )}
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={statusFilter}
              onChange={(_, v) => setStatusFilter(v || '')}
            >
              <ToggleButton value="">All</ToggleButton>
              <ToggleButton value="added">Added</ToggleButton>
              <ToggleButton value="removed">Removed</ToggleButton>
              <ToggleButton value="changed">Changed</ToggleButton>
              <ToggleButton value="unchanged">Unchanged</ToggleButton>
            </ToggleButtonGroup>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Severity</InputLabel>
              <Select value={severityFilter} label="Severity" onChange={(e) => setSeverityFilter(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                <MenuItem value="critical">critical</MenuItem>
                <MenuItem value="high">high</MenuItem>
                <MenuItem value="medium">medium</MenuItem>
                <MenuItem value="low">low</MenuItem>
                <MenuItem value="informational">informational</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Reviewer</InputLabel>
              <Select value={reviewerFilter} label="Reviewer" onChange={(e) => setReviewerFilter(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                <MenuItem value="new">new</MenuItem>
                <MenuItem value="reviewed">reviewed</MenuItem>
                <MenuItem value="flagged">flagged</MenuItem>
                <MenuItem value="dismissed">dismissed</MenuItem>
              </Select>
            </FormControl>
            <TextField
              size="small"
              placeholder="Search title / description / blurb…"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              sx={{ flex: 1, minWidth: 260 }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />
            <Typography variant="caption" color="text.secondary">
              {totalRows.toLocaleString()} rows
            </Typography>
          </Stack>
        </CardContent>
      </Card>

      {loading && <LinearProgress sx={{ mb: 1 }} />}

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Status</TableCell>
              <TableCell>Severity</TableCell>
              <TableCell>Baseline (prior)</TableCell>
              <TableCell>Target (new)</TableCell>
              <TableCell>What changed & why it matters</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {sortedRows.map(r => (
              <TableRow key={r.id} hover>
                <TableCell>
                  <Chip size="small" label={r.status} color={STATUS_COLOR[r.status] || 'default'} />
                  {r.similarity != null && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      sim {r.similarity.toFixed(2)}
                    </Typography>
                  )}
                </TableCell>
                <TableCell>
                  {r.impact_severity && (
                    <Chip size="small" label={r.impact_severity} color={SEVERITY_COLOR[r.impact_severity] || 'default'} />
                  )}
                </TableCell>
                <TableCell sx={{ maxWidth: 360, verticalAlign: 'top', overflow: 'hidden' }}>
                  <ReqSide side={r.baseline} />
                </TableCell>
                <TableCell sx={{ maxWidth: 360, verticalAlign: 'top', overflow: 'hidden' }}>
                  <ReqSide side={r.target} />
                </TableCell>
                <TableCell sx={{ maxWidth: 420, verticalAlign: 'top', overflow: 'hidden' }}>
                  {r.change_summary && (
                    <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5, wordBreak: 'break-word' }}>
                      {r.change_summary}
                    </Typography>
                  )}
                  {r.impact_blurb && (
                    <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-word' }}>
                      {r.impact_blurb}
                    </Typography>
                  )}
                </TableCell>
                <TableCell align="right">
                  <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                    <Tooltip title="View full detail">
                      <IconButton size="small" onClick={() => setDetailRow(r)}>
                        <DetailIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Flag for follow-up">
                      <IconButton size="small"
                        color={r.reviewer_status === 'flagged' ? 'warning' : 'default'}
                        onClick={() => updateReviewer(r, r.reviewer_status === 'flagged' ? 'new' : 'flagged')}>
                        <FlagIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <FormControl size="small" sx={{ minWidth: 110 }}>
                      <Select
                        value={r.reviewer_status || 'new'}
                        onChange={(e) => updateReviewer(r, e.target.value)}
                      >
                        <MenuItem value="new">new</MenuItem>
                        <MenuItem value="reviewed">reviewed</MenuItem>
                        <MenuItem value="dismissed">dismissed</MenuItem>
                        <MenuItem value="flagged">flagged</MenuItem>
                      </Select>
                    </FormControl>
                  </Stack>
                </TableCell>
              </TableRow>
            ))}
            {sortedRows.length === 0 && !loading && (
              <TableRow><TableCell colSpan={6} align="center">
                <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
                  {activeRunId
                    ? 'No rows match these filters.'
                    : 'Pick a diff run above to see requirement-level differences.'}
                </Typography>
              </TableCell></TableRow>
            )}
          </TableBody>
        </Table>
        <TablePagination
          component="div"
          count={totalRows}
          page={page}
          onPageChange={(_, newPage) => setPage(newPage)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(e) => {
            setRowsPerPage(parseInt(e.target.value, 10));
            setPage(0);
          }}
          rowsPerPageOptions={[25, 50, 100, 250]}
        />
      </TableContainer>

      <NewRunDialog
        open={newRunOpen}
        onClose={() => setNewRunOpen(false)}
        proposals={proposals}
        docs={docs}
        targetProposalId={targetProposalId}
        onStarted={() => { setNewRunOpen(false); loadRuns(); }}
      />

      <Drawer anchor="right" open={!!detailRow} onClose={() => setDetailRow(null)}>
        {detailRow && (
          <Box sx={{ width: 560, p: 3 }}>
            <Typography variant="h6">Row #{detailRow.id} — {detailRow.status}</Typography>
            {detailRow.impact_severity && (
              <Chip size="small" sx={{ mt: 1 }}
                label={detailRow.impact_severity}
                color={SEVERITY_COLOR[detailRow.impact_severity] || 'default'} />
            )}
            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Change summary</Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>{detailRow.change_summary || '—'}</Typography>
            <Typography variant="subtitle2" gutterBottom>Impact / why it matters</Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>{detailRow.impact_blurb || '—'}</Typography>
            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Baseline (prior)</Typography>
            <ReqSideDetailed side={detailRow.baseline} />
            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>Target (new)</Typography>
            <ReqSideDetailed side={detailRow.target} />
          </Box>
        )}
      </Drawer>

      <Snackbar
        open={!!toast} autoHideDuration={4000} onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        {toast && <Alert severity={toast.severity} onClose={() => setToast(null)}>{toast.msg}</Alert>}
      </Snackbar>
    </Box>
  );
}

function ReqSide({ side }) {
  if (!side) return <Typography variant="body2" color="text.disabled">(none)</Typography>;
  return (
    <Box sx={{ overflow: 'hidden' }}>
      <Typography variant="body2" sx={{ fontWeight: 600, wordBreak: 'break-word' }}>
        {side.section_id ? `§ ${side.section_id} — ` : ''}{side.title || '(untitled)'}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        {[side.category, side.priority, side.source_page ? `p.${side.source_page}` : null].filter(Boolean).join(' · ')}
      </Typography>
      {side.description && (
        <Typography variant="body2" sx={{ mt: 0.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {side.description.length > 300 ? side.description.slice(0, 300) + '…' : side.description}
        </Typography>
      )}
    </Box>
  );
}

function ReqSideDetailed({ side }) {
  if (!side) return <Typography variant="body2" color="text.disabled">(none)</Typography>;
  return (
    <Box sx={{ overflow: 'hidden' }}>
      <Typography variant="body2" sx={{ fontWeight: 600, wordBreak: 'break-word' }}>
        {side.section_id ? `§ ${side.section_id} — ` : ''}{side.title || '(untitled)'}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        {[side.category, side.priority, side.source_page ? `p.${side.source_page}` : null].filter(Boolean).join(' · ')}
      </Typography>
      {side.description && (
        <Box sx={{ mt: 1 }}>
          <Typography variant="caption" color="text.secondary">Description</Typography>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{side.description}</Typography>
        </Box>
      )}
      {side.source_text && (
        <Box sx={{ mt: 1 }}>
          <Typography variant="caption" color="text.secondary">Source text</Typography>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontStyle: 'italic' }}>
            "{side.source_text}"
          </Typography>
        </Box>
      )}
    </Box>
  );
}

function NewRunDialog({ open, onClose, proposals, docs, targetProposalId, onStarted }) {
  const [baselineMode, setBaselineMode] = useState('docs');   // 'docs' or 'proposal'
  const [baselineProposal, setBaselineProposal] = useState('');
  const [baselineDocIds, setBaselineDocIds] = useState([]);
  const [targetMode, setTargetMode] = useState('proposal');
  const [targetProposal, setTargetProposal] = useState(targetProposalId || '');
  const [targetDocIds, setTargetDocIds] = useState([]);
  const [label, setLabel] = useState('');
  const [high, setHigh] = useState(0.92);
  const [low, setLow] = useState(0.70);
  const [writeBlurbs, setWriteBlurbs] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (open) {
      setTargetProposal(targetProposalId || '');
      setErr('');
    }
  }, [open, targetProposalId]);

  const submit = async () => {
    setErr('');
    const body = {
      label: label || null,
      high_threshold: parseFloat(high),
      low_threshold: parseFloat(low),
      write_blurbs: writeBlurbs,
    };
    if (baselineMode === 'proposal' && baselineProposal) {
      body.baseline_proposal_id = parseInt(baselineProposal, 10);
    } else if (baselineMode === 'docs' && baselineDocIds.length) {
      body.baseline_document_ids = baselineDocIds.map(x => parseInt(x, 10));
    } else {
      setErr('Pick a baseline.'); return;
    }
    if (targetMode === 'proposal' && targetProposal) {
      body.target_proposal_id = parseInt(targetProposal, 10);
    } else if (targetMode === 'docs' && targetDocIds.length) {
      body.target_document_ids = targetDocIds.map(x => parseInt(x, 10));
    } else {
      setErr('Pick a target.'); return;
    }
    try {
      setSubmitting(true);
      await axios.post('/api/rfp-diff/runs', body);
      onStarted();
    } catch (e) {
      setErr(e.response?.data?.detail || e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>New diff run</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} fullWidth />

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle2">Baseline (prior solicitation / revision)</Typography>
            <ToggleButtonGroup
              exclusive size="small" value={baselineMode}
              onChange={(_, v) => v && setBaselineMode(v)}
              sx={{ my: 1 }}
            >
              <ToggleButton value="docs">By documents</ToggleButton>
              <ToggleButton value="proposal">By proposal</ToggleButton>
            </ToggleButtonGroup>
            {baselineMode === 'proposal' ? (
              <FormControl fullWidth size="small">
                <InputLabel>Baseline proposal</InputLabel>
                <Select value={baselineProposal} label="Baseline proposal"
                  onChange={(e) => setBaselineProposal(e.target.value)}>
                  {proposals.map(p => (
                    <MenuItem key={p.id} value={p.id}>#{p.id} {p.name || p.solicitation_number}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            ) : (
              <FormControl fullWidth size="small">
                <InputLabel>Baseline documents</InputLabel>
                <Select multiple value={baselineDocIds} label="Baseline documents"
                  onChange={(e) => setBaselineDocIds(e.target.value)}>
                  {docs.map(d => (
                    <MenuItem key={d.id} value={d.id}>#{d.id} {d.filename}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle2">Target (current solicitation)</Typography>
            <ToggleButtonGroup
              exclusive size="small" value={targetMode}
              onChange={(_, v) => v && setTargetMode(v)}
              sx={{ my: 1 }}
            >
              <ToggleButton value="proposal">By proposal</ToggleButton>
              <ToggleButton value="docs">By documents</ToggleButton>
            </ToggleButtonGroup>
            {targetMode === 'proposal' ? (
              <FormControl fullWidth size="small">
                <InputLabel>Target proposal</InputLabel>
                <Select value={targetProposal} label="Target proposal"
                  onChange={(e) => setTargetProposal(e.target.value)}>
                  {proposals.map(p => (
                    <MenuItem key={p.id} value={p.id}>#{p.id} {p.name || p.solicitation_number}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            ) : (
              <FormControl fullWidth size="small">
                <InputLabel>Target documents</InputLabel>
                <Select multiple value={targetDocIds} label="Target documents"
                  onChange={(e) => setTargetDocIds(e.target.value)}>
                  {docs.map(d => (
                    <MenuItem key={d.id} value={d.id}>#{d.id} {d.filename}</MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
          </Paper>

          <Stack direction="row" spacing={2}>
            <TextField label="High match threshold" type="number" size="small" value={high}
              onChange={(e) => setHigh(e.target.value)} />
            <TextField label="Low match threshold" type="number" size="small" value={low}
              onChange={(e) => setLow(e.target.value)} />
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Impact blurbs</InputLabel>
              <Select label="Impact blurbs" value={writeBlurbs ? 'yes' : 'no'}
                onChange={(e) => setWriteBlurbs(e.target.value === 'yes')}>
                <MenuItem value="yes">Write (slow, recommended)</MenuItem>
                <MenuItem value="no">Skip (fast)</MenuItem>
              </Select>
            </FormControl>
          </Stack>

          {err && <Alert severity="error">{err}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          startIcon={<RunIcon />}
          onClick={submit}
          disabled={submitting}
        >
          {submitting ? 'Starting…' : 'Start diff'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
