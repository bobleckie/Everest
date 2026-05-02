import React, { useState, useEffect, useCallback, useMemo } from 'react';import { exportToExcel } from '../utils/exportExcel';import {
  Box, Typography, Card, CardContent, Button, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  TablePagination,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert,
  IconButton, Tooltip, MenuItem, Select, FormControl, InputLabel,
  Stack, Divider, Snackbar, CircularProgress, List, ListItem, ListItemText, Accordion,
  AccordionSummary, AccordionDetails,
} from '@mui/material';
import {
  QuestionAnswer as QIcon,
  Refresh as RefreshIcon,
  AutoAwesome as DraftIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  Add as AddIcon,
  Check as ApproveIcon,
  Close as RejectIcon,
  Download as DownloadIcon,
  Settings as SettingsIcon,
  Search as SearchIcon,
  Psychology as AskIcon,
  Compress as ConsolidateIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import axios from 'axios';

// Backend errors come in two shapes depending on the handler that fired:
//   1. Plain FastAPI: { detail: "..." }
//   2. Our central handler: { error: { message: "...", code: "...", status_code: N } }
// This helper extracts the user-facing message from either, falling back to
// the axios message ("Request failed with status code 403"). Without this
// the user just sees the generic axios message and has no idea what's wrong.
const errMsg = (e) => (
  e?.response?.data?.detail ||
  e?.response?.data?.error?.message ||
  e?.message ||
  'Unknown error'
);

const CATEGORIES = ['clarification', 'risk', 'pricing', 'scope', 'competitive', 'form'];
const PRIORITIES = ['critical', 'high', 'medium', 'low'];
const STATUSES = ['draft', 'reviewed', 'approved', 'rejected', 'submitted', 'answered'];

const PRIORITY_COLOR = { critical: 'error', high: 'warning', medium: 'info', low: 'default' };
const STATUS_COLOR = {
  draft: 'default', reviewed: 'info', approved: 'success',
  rejected: 'error', submitted: 'primary', answered: 'secondary',
};

export default function Questions() {
  const [rows, setRows] = useState([]);
  const [docs, setDocs] = useState([]);
  const [proposals, setProposals] = useState([]);
  const [loading, setLoading] = useState(false);
  const [proposalId, setProposalId] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');
  const [draftDoc, setDraftDoc] = useState('');
  const [jobs, setJobs] = useState({});   // job_id -> { status, saved, error }
  const [editRow, setEditRow] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [approvalRoles, setApprovalRoles] = useState([]);
  const [rolesEditOpen, setRolesEditOpen] = useState(false);
  const [rolesDraft, setRolesDraft] = useState('');
  const [toast, setToast] = useState(null);
  // Search & Ask-the-Agent state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchHits, setSearchHits] = useState([]);      // [{question, similarity}]
  const [searchBusy, setSearchBusy] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [askQuery, setAskQuery] = useState('');
  const [askResult, setAskResult] = useState(null);       // AskAgentOut
  const [askBusy, setAskBusy] = useState(false);
  // Consolidate state
  const [consolidateJobId, setConsolidateJobId] = useState(null);
  const [consolidateStatus, setConsolidateStatus] = useState(null); // queued|running|done|error
  const [consolidatePlan, setConsolidatePlan] = useState(null);
  const [consolidateOpen, setConsolidateOpen] = useState(false);
  const [consolidateApplying, setConsolidateApplying] = useState(false);
  // Pagination
  const [page, setPage] = useState(0);          // zero-based page index
  const [pageSize, setPageSize] = useState(50); // rows per page
  const [total, setTotal] = useState(0);        // total matching rows (server-reported)

  // Reset to page 0 whenever filters change.
  useEffect(() => { setPage(0); }, [proposalId, statusFilter, categoryFilter, priorityFilter, pageSize]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit: pageSize, offset: page * pageSize };
      if (proposalId) params.proposal_id = proposalId;
      if (statusFilter) params.status = statusFilter;
      if (categoryFilter) params.category = categoryFilter;
      if (priorityFilter) params.priority = priorityFilter;
      const [rRes, dRes, pRes, arRes] = await Promise.all([
        axios.get('/api/questions/', { params }),
        axios.get('/api/documents'),
        axios.get('/api/proposals/').catch(() => ({ data: [] })),
        axios.get('/api/questions/approval-roles').catch(() => ({ data: { roles: [] } })),
      ]);
      // Backend now returns { total, limit, offset, items }; fall back to raw array for safety.
      const payload = rRes.data;
      if (payload && Array.isArray(payload.items)) {
        setRows(payload.items);
        setTotal(payload.total || 0);
      } else {
        const arr = Array.isArray(payload) ? payload : [];
        setRows(arr);
        setTotal(arr.length);
      }
      // /api/documents returns { documents: [...] }; other callers might return an array.
      const docsPayload = dRes.data;
      setDocs(Array.isArray(docsPayload) ? docsPayload : (docsPayload?.documents || []));
      setProposals(pRes.data || []);
      setApprovalRoles((arRes.data && arRes.data.roles) || []);
    } catch (e) {
      setToast({ severity: 'error', msg: 'Failed to load: ' + errMsg(e) });
    } finally {
      setLoading(false);
    }
  }, [proposalId, statusFilter, categoryFilter, priorityFilter, page, pageSize]);

  // Poll draft jobs
  useEffect(() => {
    const ids = Object.keys(jobs).filter(id => ['queued', 'running'].includes(jobs[id].status));
    if (!ids.length) return;
    const t = setInterval(async () => {
      for (const id of ids) {
        try {
          const { data } = await axios.get(`/api/questions/draft-job/${id}`);
          setJobs(prev => ({ ...prev, [id]: data }));
          if (data.status === 'complete' || data.status === 'failed') {
            loadData();
          }
        } catch { /* ignore */ }
      }
    }, 3000);
    return () => clearInterval(t);
  }, [jobs, loadData]);

  const startDraft = async () => {
    if (!draftDoc) return;
    try {
      const { data } = await axios.post(`/api/questions/draft/${draftDoc}`);
      setJobs(prev => ({ ...prev, [data.job_id]: { status: 'queued', saved: 0 } }));
      setToast({ severity: 'info', msg: `Drafting started (job ${data.job_id}).` });
    } catch (e) {
      setToast({ severity: 'error', msg: 'Draft failed: ' + errMsg(e) });
    }
  };

  const startConsolidate = async () => {
    if (!proposalId) {
      setToast({ severity: 'warning', msg: 'Select a proposal first.' });
      return;
    }
    setConsolidatePlan(null);
    try {
      const { data } = await axios.post('/api/questions/consolidate/plan-llm', {
        proposal_id: parseInt(proposalId, 10),
        only_statuses: ['draft'],
      });
      setConsolidateJobId(data.job_id);
      setConsolidateStatus(data.status || 'queued');
      setConsolidateOpen(true);
      setToast({ severity: 'info', msg: 'Consolidation started — this may take 2-5 minutes.' });
    } catch (e) {
      setToast({ severity: 'error', msg: 'Consolidate failed: ' + errMsg(e) });
    }
  };

  // Poll the consolidation job.
  useEffect(() => {
    if (!consolidateJobId) return;
    if (consolidateStatus === 'done' || consolidateStatus === 'error') return;
    const t = setInterval(async () => {
      try {
        const { data } = await axios.get(`/api/questions/consolidate/plan-llm/${consolidateJobId}`);
        setConsolidateStatus(data.status);
        if (data.status === 'done') {
          setConsolidatePlan(data.plan);
          clearInterval(t);
        } else if (data.status === 'error') {
          setToast({ severity: 'error', msg: 'Consolidation failed: ' + (data.error || 'unknown') });
          clearInterval(t);
        }
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(t);
  }, [consolidateJobId, consolidateStatus]);

  const applyMerges = async () => {
    if (!consolidatePlan) return;
    setConsolidateApplying(true);
    try {
      const { data } = await axios.post('/api/questions/consolidate/apply', { plan: consolidatePlan });
      setToast({ severity: 'success',
        msg: `Merges applied: created ${data.created_count}, superseded ${data.superseded_count}.` });
      loadData();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Apply failed: ' + errMsg(e) });
    } finally {
      setConsolidateApplying(false);
    }
  };

  const applyDrops = async () => {
    if (!consolidatePlan?.dropped_ids?.length) return;
    if (!window.confirm(`Drop ${consolidatePlan.dropped_ids.length} questions as superseded?`)) return;
    setConsolidateApplying(true);
    try {
      const { data } = await axios.post('/api/questions/consolidate/apply-dropped', {
        dropped_ids: consolidatePlan.dropped_ids,
      });
      setToast({ severity: 'success', msg: `Dropped ${data.dropped_count} questions.` });
      loadData();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Drop failed: ' + errMsg(e) });
    } finally {
      setConsolidateApplying(false);
    }
  };

  const closeConsolidate = () => {
    setConsolidateOpen(false);
    // Keep job+plan in state so dialog re-opens cleanly; clear on next start.
  };

  const saveEdit = async () => {
    if (!editRow) return;
    try {
      const payload = {
        question_text: editRow.question_text,
        rationale: editRow.rationale,
        source_quote: editRow.source_quote,
        source_section: editRow.source_section,
        source_page: editRow.source_page ? parseInt(editRow.source_page, 10) : null,
        category: editRow.category,
        priority: editRow.priority,
        status: editRow.status,
        review_notes: editRow.review_notes,
        answer_text: editRow.answer_text,
      };
      await axios.patch(`/api/questions/${editRow.id}`, payload);
      setEditRow(null);
      loadData();
      setToast({ severity: 'success', msg: 'Question updated.' });
    } catch (e) {
      setToast({ severity: 'error', msg: 'Save failed: ' + errMsg(e) });
    }
  };

  const changeStatus = async (row, newStatus) => {
    try {
      await axios.patch(`/api/questions/${row.id}`, { status: newStatus });
      loadData();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Status change failed: ' + errMsg(e) });
    }
  };

  const deleteRow = async (row) => {
    if (!window.confirm(`Delete question #${row.id}?`)) return;
    try {
      await axios.delete(`/api/questions/${row.id}`);
      loadData();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Delete failed: ' + errMsg(e) });
    }
  };

  const createQuestion = async (row) => {
    try {
      await axios.post('/api/questions/', {
        proposal_id: proposalId ? parseInt(proposalId, 10) : null,
        category: row.category || 'clarification',
        priority: row.priority || 'medium',
        question_text: row.question_text || '',
        rationale: row.rationale,
        source_section: row.source_section,
        source_page: row.source_page ? parseInt(row.source_page, 10) : null,
      });
      setCreateOpen(false);
      loadData();
    } catch (e) {
      setToast({ severity: 'error', msg: 'Create failed: ' + errMsg(e) });
    }
  };

  const exportText = async (format) => {
    if (!proposalId) {
      setToast({ severity: 'warning', msg: 'Choose a proposal before exporting.' });
      return;
    }
    try {
      if (format === 'docx') {
        const res = await axios.get(
          `/api/questions/export/${proposalId}?format=docx&status=approved`,
          { responseType: 'blob' },
        );
        const url = URL.createObjectURL(new Blob([res.data]));
        const a = document.createElement('a');
        a.href = url;
        a.download = `questions_proposal_${proposalId}.docx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      } else {
        const res = await axios.get(
          `/api/questions/export/${proposalId}?format=${format}&status=approved`,
        );
        const blob = new Blob([res.data.text || ''], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `questions_proposal_${proposalId}.txt`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    } catch (e) {
      setToast({ severity: 'error', msg: 'Export failed: ' + errMsg(e) });
    }
  };

  const exportExcel = () => {
    if (!rows.length) return;
    exportToExcel({
      filename: `rfp-questions${proposalId ? `-proposal-${proposalId}` : ''}`,
      sheetName: 'Questions',
      columns: [
        { header: 'ID', key: 'id', width: 8 },
        { header: 'Priority', key: 'priority', width: 12 },
        { header: 'Category', key: 'category', width: 14 },
        { header: 'Status', key: 'status', width: 12 },
        { header: 'Section', key: 'source_section', width: 12 },
        { header: 'Page', key: 'source_page', width: 8 },
        { header: 'Question', key: 'question_text', width: 70 },
        { header: 'Rationale (internal)', key: 'rationale', width: 50 },
        { header: 'Source Quote', key: 'source_quote', width: 50 },
        { header: 'Review Notes', key: 'review_notes', width: 30 },
        { header: 'Answer', key: 'answer_text', width: 50 },
        { header: 'AI Generated', key: 'created_by_ai', width: 12, transform: (v) => v ? 'yes' : 'no' },
      ],
      rows,
    });
  };

  const saveApprovalRoles = async () => {
    try {
      const list = rolesDraft.split(',').map(s => s.trim()).filter(Boolean);
      await axios.put('/api/questions/approval-roles', { roles: list });
      setApprovalRoles(list);
      setRolesEditOpen(false);
      setToast({ severity: 'success', msg: 'Approval roles updated.' });
    } catch (e) {
      setToast({ severity: 'error', msg: 'Save failed: ' + errMsg(e) });
    }
  };

  const runSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchHits([]);
      return;
    }
    setSearchBusy(true);
    try {
      const payload = {
        query: searchQuery,
        proposal_id: proposalId ? parseInt(proposalId, 10) : null,
        top_k: 10,
        min_similarity: 0.2,
      };
      const { data } = await axios.post('/api/questions/search', payload);
      setSearchHits(data.hits || []);
      if ((data.hits || []).length === 0) {
        setToast({ severity: 'info', msg: 'No similar questions found. Try "Ask the Agent" to draft a new one.' });
      }
    } catch (e) {
      setToast({ severity: 'error', msg: 'Search failed: ' + errMsg(e) });
    } finally {
      setSearchBusy(false);
    }
  };

  const clearSearch = () => {
    setSearchQuery('');
    setSearchHits([]);
  };

  const openAsk = () => {
    setAskQuery(searchQuery);   // prefill with current search if any
    setAskResult(null);
    setAskOpen(true);
  };

  const runAsk = async () => {
    if (!askQuery.trim()) return;
    setAskBusy(true);
    try {
      const payload = {
        query: askQuery,
        proposal_id: proposalId ? parseInt(proposalId, 10) : null,
        match_threshold: 0.72,
        save_if_new: true,
        context_chunks: 6,
      };
      const { data } = await axios.post('/api/questions/ask', payload);
      setAskResult(data);
      if (data.mode === 'drafted' && data.drafted_question) {
        loadData();   // show the new row in the table
      }
    } catch (e) {
      setToast({ severity: 'error', msg: 'Ask failed: ' + errMsg(e) });
    } finally {
      setAskBusy(false);
    }
  };

  const keepDraftedOrDiscard = async (keep) => {
    if (!askResult?.drafted_question) { setAskOpen(false); return; }
    if (!keep) {
      // Delete the auto-saved draft
      try { await axios.delete(`/api/questions/${askResult.drafted_question.id}`); } catch { /* ignore */ }
      loadData();
    }
    setAskOpen(false);
    setAskResult(null);
    setAskQuery('');
  };

  const docsForProposal = useMemo(
    () => (proposalId ? docs.filter(d => d.proposal_id === parseInt(proposalId, 10)) : docs),
    [docs, proposalId],
  );

  const activeJobs = Object.entries(jobs).filter(([, j]) => ['queued', 'running'].includes(j.status));

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <QIcon color="primary" />
        <Typography variant="h5">RFP Questions</Typography>
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Approval roles">
          <IconButton onClick={() => { setRolesDraft(approvalRoles.join(', ')); setRolesEditOpen(true); }}>
            <SettingsIcon />
          </IconButton>
        </Tooltip>
        <Button startIcon={<RefreshIcon />} onClick={loadData}>Refresh</Button>
      </Stack>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems="center">
            <FormControl size="small" sx={{ minWidth: 220 }}>
              <InputLabel>Proposal</InputLabel>
              <Select value={proposalId} label="Proposal" onChange={(e) => setProposalId(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                {proposals.map(p => (
                  <MenuItem key={p.id} value={p.id}>
                    #{p.id} {p.name || p.solicitation_number || ''}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>Status</InputLabel>
              <Select value={statusFilter} label="Status" onChange={(e) => setStatusFilter(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                {STATUSES.map(s => <MenuItem key={s} value={s}>{s}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Category</InputLabel>
              <Select value={categoryFilter} label="Category" onChange={(e) => setCategoryFilter(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                {CATEGORIES.map(c => <MenuItem key={c} value={c}>{c}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel>Priority</InputLabel>
              <Select value={priorityFilter} label="Priority" onChange={(e) => setPriorityFilter(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                {PRIORITIES.map(p => <MenuItem key={p} value={p}>{p}</MenuItem>)}
              </Select>
            </FormControl>
            <Box sx={{ flex: 1 }} />
            <Button startIcon={<AddIcon />} variant="outlined" onClick={() => setCreateOpen(true)}>
              Add Manual
            </Button>
          </Stack>

          <Divider sx={{ my: 2 }} />

          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems="center">
            <FormControl size="small" sx={{ minWidth: 320 }}>
              <InputLabel>Document to draft from</InputLabel>
              <Select value={draftDoc} label="Document to draft from" onChange={(e) => setDraftDoc(e.target.value)}>
                <MenuItem value=""><em>Select document…</em></MenuItem>
                {docsForProposal.map(d => (
                  <MenuItem key={d.id} value={d.id}>
                    #{d.id} {d.filename}{d.proposal_id ? ` (prop ${d.proposal_id})` : ''}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Button
              startIcon={<DraftIcon />}
              variant="contained"
              onClick={startDraft}
              disabled={!draftDoc}
            >
              Draft with AI
            </Button>
            <Button
              startIcon={<ConsolidateIcon />}
              variant="outlined"
              color="secondary"
              onClick={startConsolidate}
              disabled={!proposalId || (consolidateStatus === 'queued' || consolidateStatus === 'running')}
            >
              {consolidateStatus === 'queued' || consolidateStatus === 'running' ? 'Consolidating…' : 'Consolidate Questions'}
            </Button>
            <Box sx={{ flex: 1 }} />
            <Button startIcon={<DownloadIcon />} onClick={() => exportText('njstart')} disabled={!proposalId}>
              Export NJSTART
            </Button>
            <Button startIcon={<DownloadIcon />} onClick={() => exportText('text')} disabled={!proposalId}>
              Export Text
            </Button>
            <Button startIcon={<DownloadIcon />} onClick={() => exportText('docx')} disabled={!proposalId}>
              Export Word
            </Button>
            <Button variant="contained" startIcon={<DownloadIcon />} onClick={exportExcel} disabled={rows.length === 0}>
              Export Excel
            </Button>
          </Stack>

          {activeJobs.length > 0 && (
            <Alert severity="info" sx={{ mt: 2 }}>
              {activeJobs.length} drafting job(s) in progress — table refreshes automatically when complete.
              <LinearProgress sx={{ mt: 1 }} />
            </Alert>
          )}

          {approvalRoles.length > 0 && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              Approval requires role(s): {approvalRoles.join(', ')}
            </Typography>
          )}
        </CardContent>
      </Card>

      {/* Search existing questions + Ask the Agent */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems="center">
            <TextField
              size="small"
              fullWidth
              placeholder="Search existing questions (e.g. liquidated damages, retention period, SBE set-aside)…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') runSearch(); }}
              InputProps={{ startAdornment: <SearchIcon fontSize="small" sx={{ mr: 1, color: 'text.secondary' }} /> }}
            />
            <Button
              variant="contained"
              startIcon={<SearchIcon />}
              onClick={runSearch}
              disabled={searchBusy || !searchQuery.trim()}
            >
              Search
            </Button>
            <Button onClick={clearSearch} disabled={!searchQuery && searchHits.length === 0}>Clear</Button>
            <Button
              variant="outlined"
              color="secondary"
              startIcon={<AskIcon />}
              onClick={openAsk}
            >
              Ask the Agent
            </Button>
          </Stack>

          {searchBusy && <LinearProgress sx={{ mt: 2 }} />}

          {searchHits.length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>
                Top {searchHits.length} matches
              </Typography>
              <Stack spacing={1}>
                {searchHits.map(({ question: h, similarity }) => (
                  <Paper key={h.id} variant="outlined" sx={{ p: 1.5 }}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip size="small" label={`#${h.id}`} />
                      <Chip size="small" label={h.priority || '—'} color={PRIORITY_COLOR[h.priority] || 'default'} />
                      <Chip size="small" label={h.category || '—'} />
                      <Chip size="small" label={h.status} color={STATUS_COLOR[h.status] || 'default'} />
                      <Typography variant="caption" color="text.secondary">
                        {h.source_section ? `§ ${h.source_section}` : ''}{h.source_page ? `  ·  p. ${h.source_page}` : ''}
                      </Typography>
                      <Box sx={{ flex: 1 }} />
                      <Chip size="small" color="info" variant="outlined"
                            label={`similarity ${(similarity * 100).toFixed(0)}%`} />
                    </Stack>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{h.question_text}</Typography>
                    {h.rationale && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                        Why it matters: {h.rationale}
                      </Typography>
                    )}
                  </Paper>
                ))}
              </Stack>
            </Box>
          )}
        </CardContent>
      </Card>

      {loading && <LinearProgress sx={{ mb: 1 }} />}

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>#</TableCell>
              <TableCell>Priority</TableCell>
              <TableCell>Category</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Source</TableCell>
              <TableCell>Question</TableCell>
              <TableCell>Rationale</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map(r => (
              <TableRow key={r.id} hover>
                <TableCell>{r.id}</TableCell>
                <TableCell>
                  <Chip size="small" label={r.priority || '—'} color={PRIORITY_COLOR[r.priority] || 'default'} />
                </TableCell>
                <TableCell><Chip size="small" label={r.category || '—'} /></TableCell>
                <TableCell>
                  <Chip size="small" label={r.status} color={STATUS_COLOR[r.status] || 'default'} />
                </TableCell>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>
                  {r.source_section ? <div>§ {r.source_section}</div> : null}
                  {r.source_page ? <div>p. {r.source_page}</div> : null}
                </TableCell>
                <TableCell sx={{ maxWidth: 500, overflow: 'hidden' }}>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{r.question_text}</Typography>
                  {r.source_quote && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5, fontStyle: 'italic', wordBreak: 'break-word' }}>
                      "{r.source_quote}"
                    </Typography>
                  )}
                </TableCell>
                <TableCell sx={{ maxWidth: 320, overflow: 'hidden' }}>
                  <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-word' }}>{r.rationale || ''}</Typography>
                </TableCell>
                <TableCell align="right">
                  <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                    {r.status === 'draft' && (
                      <Tooltip title="Mark reviewed">
                        <IconButton size="small" onClick={() => changeStatus(r, 'reviewed')}>
                          <ApproveIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    {r.status !== 'approved' && r.status !== 'rejected' && (
                      <Tooltip title="Approve">
                        <IconButton size="small" color="success" onClick={() => changeStatus(r, 'approved')}>
                          <ApproveIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    {r.status !== 'rejected' && (
                      <Tooltip title="Reject">
                        <IconButton size="small" color="error" onClick={() => changeStatus(r, 'rejected')}>
                          <RejectIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    )}
                    <Tooltip title="Edit">
                      <IconButton size="small" onClick={() => setEditRow({ ...r })}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Delete">
                      <IconButton size="small" onClick={() => deleteRow(r)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                </TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && !loading && (
              <TableRow><TableCell colSpan={8} align="center">
                <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
                  No questions yet. Pick a document above and click "Draft with AI".
                </Typography>
              </TableCell></TableRow>
            )}
          </TableBody>
        </Table>
        <TablePagination
          component="div"
          count={total}
          page={page}
          onPageChange={(_e, newPage) => setPage(newPage)}
          rowsPerPage={pageSize}
          onRowsPerPageChange={(e) => { setPageSize(parseInt(e.target.value, 10)); setPage(0); }}
          rowsPerPageOptions={[25, 50, 100, 200]}
          labelRowsPerPage="Rows per page:"
        />
      </TableContainer>

      {/* Ask the Agent dialog */}
      <Dialog open={askOpen} onClose={() => setAskOpen(false)} fullWidth maxWidth="md">
        <DialogTitle>
          <Stack direction="row" spacing={1} alignItems="center">
            <AskIcon color="secondary" />
            <span>Ask the Agent</span>
          </Stack>
        </DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Describe what you want to ask the State. The agent will first search existing questions for a close match.
            If none is found, it will research the RFP documents and draft a new, properly-formed question grounded in source text.
          </Typography>
          <TextField
            autoFocus fullWidth multiline minRows={3}
            placeholder="e.g. Is the 10% retention held until final acceptance or released progressively?"
            value={askQuery}
            onChange={(e) => setAskQuery(e.target.value)}
          />

          {askBusy && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 2 }}>
              <CircularProgress size={18} />
              <Typography variant="body2" color="text.secondary">
                Searching existing questions and researching source documents…
              </Typography>
            </Stack>
          )}

          {askResult && !askBusy && (
            <Box sx={{ mt: 2 }}>
              <Divider sx={{ mb: 2 }} />
              {askResult.mode === 'matched' && askResult.best_match && (
                <>
                  <Alert severity="success" sx={{ mb: 2 }}>
                    A similar question already exists (similarity {(askResult.best_match.similarity * 100).toFixed(0)}%).
                    Use the existing one instead of duplicating.
                  </Alert>
                  <Paper variant="outlined" sx={{ p: 1.5 }}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip size="small" label={`#${askResult.best_match.question.id}`} />
                      <Chip size="small" label={askResult.best_match.question.priority || '—'}
                            color={PRIORITY_COLOR[askResult.best_match.question.priority] || 'default'} />
                      <Chip size="small" label={askResult.best_match.question.category || '—'} />
                      <Chip size="small" label={askResult.best_match.question.status}
                            color={STATUS_COLOR[askResult.best_match.question.status] || 'default'} />
                    </Stack>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                      {askResult.best_match.question.question_text}
                    </Typography>
                    {askResult.best_match.question.rationale && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                        Why: {askResult.best_match.question.rationale}
                      </Typography>
                    )}
                  </Paper>
                </>
              )}

              {askResult.mode === 'drafted' && askResult.drafted_question && (
                <>
                  <Alert severity="info" sx={{ mb: 2 }}>
                    No close match was found. The agent drafted a new question grounded in the RFP source text. It has been saved as a draft.
                  </Alert>
                  <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip size="small" label={`#${askResult.drafted_question.id}`} />
                      <Chip size="small" label={askResult.drafted_question.priority || '—'}
                            color={PRIORITY_COLOR[askResult.drafted_question.priority] || 'default'} />
                      <Chip size="small" label={askResult.drafted_question.category || '—'} />
                      <Chip size="small" label="draft" />
                      <Typography variant="caption" color="text.secondary">
                        {askResult.drafted_question.source_section ? `§ ${askResult.drafted_question.source_section}` : ''}
                        {askResult.drafted_question.source_page ? `  ·  p. ${askResult.drafted_question.source_page}` : ''}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                      {askResult.drafted_question.question_text}
                    </Typography>
                    {askResult.drafted_question.rationale && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                        Why it matters: {askResult.drafted_question.rationale}
                      </Typography>
                    )}
                  </Paper>

                  {Array.isArray(askResult.context_used) && askResult.context_used.length > 0 && (
                    <Box>
                      <Typography variant="subtitle2" sx={{ mb: 1 }}>Source passages used</Typography>
                      <Stack spacing={1}>
                        {askResult.context_used.map((c, i) => (
                          <Paper key={i} variant="outlined" sx={{ p: 1, bgcolor: 'action.hover' }}>
                            <Typography variant="caption" color="text.secondary">
                              doc #{c.document_id}{c.page ? `  ·  p. ${c.page}` : ''}
                              {typeof c.similarity === 'number' ? `  ·  sim ${(c.similarity * 100).toFixed(0)}%` : ''}
                            </Typography>
                            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mt: 0.5 }}>
                              {c.snippet}
                            </Typography>
                          </Paper>
                        ))}
                      </Stack>
                    </Box>
                  )}
                </>
              )}

              {askResult.mode === 'drafted' && !askResult.drafted_question && (
                <Alert severity="warning">
                  The agent could not draft a useful question. {askResult.rationale || ''}
                </Alert>
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          {askResult?.mode === 'drafted' && askResult?.drafted_question ? (
            <>
              <Button color="error" onClick={() => keepDraftedOrDiscard(false)}>Discard draft</Button>
              <Button variant="contained" onClick={() => keepDraftedOrDiscard(true)}>Keep draft</Button>
            </>
          ) : (
            <>
              <Button onClick={() => setAskOpen(false)}>Close</Button>
              <Button
                variant="contained"
                onClick={runAsk}
                disabled={askBusy || !askQuery.trim()}
                startIcon={<AskIcon />}
              >
                Ask
              </Button>
            </>
          )}
        </DialogActions>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={!!editRow} onClose={() => setEditRow(null)} fullWidth maxWidth="md">
        <DialogTitle>Edit question #{editRow?.id}</DialogTitle>
        <DialogContent dividers>
          {editRow && (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <TextField
                label="Question" multiline minRows={3} fullWidth value={editRow.question_text || ''}
                onChange={(e) => setEditRow({ ...editRow, question_text: e.target.value })}
              />
              <TextField
                label="Rationale" multiline minRows={2} fullWidth value={editRow.rationale || ''}
                onChange={(e) => setEditRow({ ...editRow, rationale: e.target.value })}
              />
              <TextField
                label="Source quote" multiline minRows={2} fullWidth value={editRow.source_quote || ''}
                onChange={(e) => setEditRow({ ...editRow, source_quote: e.target.value })}
              />
              <Stack direction="row" spacing={2}>
                <TextField
                  label="Section" value={editRow.source_section || ''}
                  onChange={(e) => setEditRow({ ...editRow, source_section: e.target.value })}
                />
                <TextField
                  label="Page" type="number" value={editRow.source_page || ''}
                  onChange={(e) => setEditRow({ ...editRow, source_page: e.target.value })}
                />
                <FormControl fullWidth>
                  <InputLabel>Category</InputLabel>
                  <Select label="Category" value={editRow.category || 'clarification'}
                    onChange={(e) => setEditRow({ ...editRow, category: e.target.value })}>
                    {CATEGORIES.map(c => <MenuItem key={c} value={c}>{c}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl fullWidth>
                  <InputLabel>Priority</InputLabel>
                  <Select label="Priority" value={editRow.priority || 'medium'}
                    onChange={(e) => setEditRow({ ...editRow, priority: e.target.value })}>
                    {PRIORITIES.map(p => <MenuItem key={p} value={p}>{p}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl fullWidth>
                  <InputLabel>Status</InputLabel>
                  <Select label="Status" value={editRow.status || 'draft'}
                    onChange={(e) => setEditRow({ ...editRow, status: e.target.value })}>
                    {STATUSES.map(s => <MenuItem key={s} value={s}>{s}</MenuItem>)}
                  </Select>
                </FormControl>
              </Stack>
              <TextField
                label="Review notes" multiline minRows={2} fullWidth value={editRow.review_notes || ''}
                onChange={(e) => setEditRow({ ...editRow, review_notes: e.target.value })}
              />
              <TextField
                label="Answer (after submission)" multiline minRows={2} fullWidth value={editRow.answer_text || ''}
                onChange={(e) => setEditRow({ ...editRow, answer_text: e.target.value })}
              />
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditRow(null)}>Cancel</Button>
          <Button variant="contained" onClick={saveEdit}>Save</Button>
        </DialogActions>
      </Dialog>

      {/* Create dialog */}
      <CreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={createQuestion}
      />

      {/* Approval roles dialog */}
      <Dialog open={rolesEditOpen} onClose={() => setRolesEditOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Approval roles</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Comma-separated list of role names that are allowed to set a question to "approved".
            Use <code>any</code> to allow any authenticated user.
          </Typography>
          <TextField
            fullWidth label="Roles" value={rolesDraft}
            placeholder="e.g. capture_manager, principal_engineer"
            onChange={(e) => setRolesDraft(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRolesEditOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={saveApprovalRoles}>Save</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={consolidateOpen} onClose={closeConsolidate} fullWidth maxWidth="lg">
        <DialogTitle>
          Consolidate Questions (AI Review)
          {consolidatePlan && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              {consolidatePlan.summary?.total_in || 0} input → {consolidatePlan.summary?.total_out || 0} output
              {' · '}
              {consolidatePlan.summary?.merged_away || 0} merged away
              {' · '}
              {consolidatePlan.dropped_ids?.length || 0} flagged to drop
              {' · '}
              {consolidatePlan.summary?.reduction_pct != null
                ? `${consolidatePlan.summary.reduction_pct}% reduction`
                : ''}
            </Typography>
          )}
        </DialogTitle>
        <DialogContent dividers>
          {(consolidateStatus === 'queued' || consolidateStatus === 'running') && (
            <Box sx={{ textAlign: 'center', py: 4 }}>
              <CircularProgress />
              <Typography sx={{ mt: 2 }} color="text.secondary">
                Claude is reviewing each section bucket in parallel. This usually takes 2-5 minutes
                depending on how many questions need consolidation.
              </Typography>
              <LinearProgress sx={{ mt: 2 }} />
            </Box>
          )}

          {consolidateStatus === 'error' && (
            <Alert severity="error">Consolidation failed. Check server logs.</Alert>
          )}

          {consolidateStatus === 'done' && consolidatePlan && (
            <Box>
              <Alert severity="info" sx={{ mb: 2 }}>
                Review the proposed merges and drops below. Nothing is changed until you click
                <strong> Apply Merges</strong> or <strong>Apply Drops</strong>. Merges are reversible
                via the Revert endpoint; drops mark originals as <code>superseded</code>.
              </Alert>

              <Accordion defaultExpanded>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle1">
                    Merges ({consolidatePlan.merges?.length || 0})
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  {(consolidatePlan.merges || []).length === 0 && (
                    <Typography color="text.secondary">No merges proposed.</Typography>
                  )}
                  {(consolidatePlan.merges || []).map((m, i) => (
                    <Card key={i} variant="outlined" sx={{ mb: 1.5 }}>
                      <CardContent>
                        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                          <Chip size="small" label={m.bucket_key || 'bucket'} />
                          <Chip size="small" color="info"
                            label={`${m.member_ids?.length || 0} → 1`} />
                          {m.new_question?.priority && (
                            <Chip size="small" color={PRIORITY_COLOR[m.new_question.priority] || 'default'}
                              label={m.new_question.priority} />
                          )}
                        </Stack>
                        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                          Proposed merged question:
                        </Typography>
                        <Typography variant="body2" sx={{ mb: 1 }}>
                          {m.new_question?.question_text}
                        </Typography>
                        {m.new_question?.rationale && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                            Why: {m.new_question.rationale}
                          </Typography>
                        )}
                        <Divider sx={{ my: 1 }} />
                        <Typography variant="caption" color="text.secondary">
                          Replaces these drafts:
                        </Typography>
                        <List dense disablePadding>
                          {(m.members || []).map((mem) => (
                            <ListItem key={mem.id} sx={{ py: 0.25 }}>
                              <ListItemText
                                primary={`#${mem.id}: ${mem.question_text}`}
                                primaryTypographyProps={{ variant: 'caption' }}
                              />
                            </ListItem>
                          ))}
                        </List>
                      </CardContent>
                    </Card>
                  ))}
                </AccordionDetails>
              </Accordion>

              <Accordion>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle1">
                    Drops ({consolidatePlan.dropped_ids?.length || 0})
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  {(consolidatePlan.dropped_ids || []).length === 0 && (
                    <Typography color="text.secondary">No drops proposed.</Typography>
                  )}
                  {(consolidatePlan.dropped_ids || []).length > 0 && (
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                      Question IDs Claude recommends cutting entirely
                      (redundant / too speculative / not answerable from the RFP):
                    </Typography>
                  )}
                  <List dense>
                    {(consolidatePlan.dropped_ids || []).map((id) => {
                      const q = rows.find(r => r.id === id);
                      return (
                        <ListItem key={id} sx={{ py: 0.25 }}>
                          <ListItemText
                            primary={`#${id}${q ? `: ${q.question_text}` : ''}`}
                            primaryTypographyProps={{ variant: 'caption' }}
                          />
                        </ListItem>
                      );
                    })}
                  </List>
                </AccordionDetails>
              </Accordion>

              <Accordion>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle1">
                    Kept as-is ({consolidatePlan.keeps?.length || 0})
                  </Typography>
                </AccordionSummary>
                <AccordionDetails>
                  <List dense>
                    {(consolidatePlan.keeps || []).map((k) => (
                      <ListItem key={k.id} sx={{ py: 0.25 }}>
                        <ListItemText
                          primary={`#${k.id}: ${k.question_text}`}
                          primaryTypographyProps={{ variant: 'caption' }}
                        />
                      </ListItem>
                    ))}
                  </List>
                </AccordionDetails>
              </Accordion>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={closeConsolidate}>Close</Button>
          {consolidateStatus === 'done' && consolidatePlan && (
            <>
              <Button
                onClick={applyDrops}
                disabled={consolidateApplying || !consolidatePlan.dropped_ids?.length}
                color="warning"
              >
                Apply Drops ({consolidatePlan.dropped_ids?.length || 0})
              </Button>
              <Button
                variant="contained"
                onClick={applyMerges}
                disabled={consolidateApplying || !consolidatePlan.merges?.length}
              >
                Apply Merges ({consolidatePlan.merges?.length || 0})
              </Button>
            </>
          )}
        </DialogActions>
      </Dialog>

      <Snackbar
        open={!!toast} autoHideDuration={4000} onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        {toast && <Alert severity={toast.severity} onClose={() => setToast(null)}>{toast.msg}</Alert>}
      </Snackbar>
    </Box>
  );
}

function CreateDialog({ open, onClose, onCreate }) {
  const [row, setRow] = useState({ category: 'clarification', priority: 'medium', question_text: '' });
  useEffect(() => { if (open) setRow({ category: 'clarification', priority: 'medium', question_text: '' }); }, [open]);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Add question manually</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="Question" multiline minRows={3} fullWidth value={row.question_text}
            onChange={(e) => setRow({ ...row, question_text: e.target.value })}
          />
          <TextField label="Rationale" multiline minRows={2} fullWidth value={row.rationale || ''}
            onChange={(e) => setRow({ ...row, rationale: e.target.value })}
          />
          <Stack direction="row" spacing={2}>
            <TextField label="Section" value={row.source_section || ''}
              onChange={(e) => setRow({ ...row, source_section: e.target.value })}
            />
            <TextField label="Page" type="number" value={row.source_page || ''}
              onChange={(e) => setRow({ ...row, source_page: e.target.value })}
            />
            <FormControl fullWidth>
              <InputLabel>Category</InputLabel>
              <Select label="Category" value={row.category}
                onChange={(e) => setRow({ ...row, category: e.target.value })}>
                {CATEGORIES.map(c => <MenuItem key={c} value={c}>{c}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl fullWidth>
              <InputLabel>Priority</InputLabel>
              <Select label="Priority" value={row.priority}
                onChange={(e) => setRow({ ...row, priority: e.target.value })}>
                {PRIORITIES.map(p => <MenuItem key={p} value={p}>{p}</MenuItem>)}
              </Select>
            </FormControl>
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => onCreate(row)} disabled={!row.question_text}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
}
