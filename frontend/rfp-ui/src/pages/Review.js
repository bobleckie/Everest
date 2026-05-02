import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Tabs, Tab, Fade, Alert, TextField, MenuItem, TablePagination, InputAdornment,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import {
  CheckCircle as CheckIcon, Warning as WarningIcon,
  Timeline as WorkflowIcon, Score as ScoreIcon,
  Search as SearchIcon,
  TableRows as TableRowsIcon,
  ViewAgenda as ViewAgendaIcon,
} from '@mui/icons-material';
import axios from 'axios';
import RequirementsBySection from '../components/RequirementsBySection';
import CitationChips from '../components/CitationChips';

// Single-proposal app — same default used by RfpSetup / ComplianceMatrix.
const DEFAULT_PROPOSAL_ID = 1;

// Debounce helper — pause search requests until the user stops typing.
function useDebounced(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

const Review = () => {
  const [activeTab, setActiveTab] = useState(0);

  // Conversations (small table today but paginated so it can't blow up later)
  const [conversations, setConversations] = useState([]);
  const [convLoading, setConvLoading] = useState(true);
  const [convPage, setConvPage] = useState(0);
  const [convRowsPerPage, setConvRowsPerPage] = useState(25);

  // Requirements — ALWAYS paginated server-side (there can be 7k+ rows).
  // Previously this page fetched every row and rendered it unpaginated, which
  // froze the browser.
  const [requirements, setRequirements] = useState([]);
  const [reqLoading, setReqLoading] = useState(true);
  const [reqTotal, setReqTotal] = useState(0);
  const [reqPage, setReqPage] = useState(0);
  const [reqRowsPerPage, setReqRowsPerPage] = useState(50);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const debouncedSearch = useDebounced(search, 300);

  // View mode for the requirements tab: section accordions (default) vs. flat table.
  const [reqViewMode, setReqViewMode] = useState('section');

  // Summary counts (the big cards at the top) — computed via cheap count
  // queries so the numbers reflect the FULL dataset, not just the current page.
  const [summary, setSummary] = useState({
    total: 0, compliant: 0, non_compliant: 0, not_assessed: 0,
  });

  // ── Fetch conversations (one-shot, small list) ───────────────────
  const fetchConversations = useCallback(async () => {
    setConvLoading(true);
    try {
      const r = await axios.get('/api/knowledge/conversations', { params: { limit: 200 } });
      setConversations(r.data.conversations || []);
    } catch (e) { console.error(e); }
    finally { setConvLoading(false); }
  }, []);

  // ── Fetch summary counts (unfiltered + per-status) ───────────────
  const fetchSummary = useCallback(async () => {
    try {
      // 4 cheap count-only calls (limit=1 returns the full total + 1 row)
      const base = { limit: 1 };
      const [all, compliant, nonCompliant, notAssessed] = await Promise.all([
        axios.get('/api/knowledge/requirements', { params: base }),
        axios.get('/api/knowledge/requirements', { params: { ...base, compliance_status: 'compliant' } }),
        axios.get('/api/knowledge/requirements', { params: { ...base, compliance_status: 'non_compliant' } }),
        axios.get('/api/knowledge/requirements', { params: { ...base, compliance_status: 'not_assessed' } }),
      ]);
      setSummary({
        total: all.data?.total ?? 0,
        compliant: compliant.data?.total ?? 0,
        non_compliant: nonCompliant.data?.total ?? 0,
        not_assessed: notAssessed.data?.total ?? 0,
      });
    } catch (e) {
      console.error('summary fetch failed', e);
    }
  }, []);

  // ── Fetch requirements for the current page ──────────────────────
  const fetchRequirements = useCallback(async () => {
    setReqLoading(true);
    try {
      const params = {
        limit: reqRowsPerPage,
        offset: reqPage * reqRowsPerPage,
      };
      if (debouncedSearch.trim()) params.search = debouncedSearch.trim();
      if (statusFilter) params.compliance_status = statusFilter;

      const r = await axios.get('/api/knowledge/requirements', { params });
      setRequirements(r.data?.requirements || []);
      setReqTotal(r.data?.total ?? 0);
    } catch (e) {
      console.error(e);
      setRequirements([]);
      setReqTotal(0);
    } finally {
      setReqLoading(false);
    }
  }, [reqPage, reqRowsPerPage, debouncedSearch, statusFilter]);

  useEffect(() => { fetchConversations(); fetchSummary(); }, [fetchConversations, fetchSummary]);
  useEffect(() => { fetchRequirements(); }, [fetchRequirements]);

  // Reset to first page whenever filters change
  useEffect(() => { setReqPage(0); }, [debouncedSearch, statusFilter, reqRowsPerPage]);

  const pagedConversations = useMemo(
    () => conversations.slice(
      convPage * convRowsPerPage,
      convPage * convRowsPerPage + convRowsPerPage,
    ),
    [conversations, convPage, convRowsPerPage],
  );

  return (
    <Fade in timeout={600}>
      <Box>
        {/* Compliance Summary (whole-dataset totals) */}
        <Grid container spacing={2} sx={{ mb: 3 }}>
          <Grid item xs={6} sm={3}>
            <Card sx={{ textAlign: 'center', py: 2 }}>
              <CardContent sx={{ p: '12px !important' }}>
                <Typography variant="h4" fontWeight={700} color="primary">
                  {summary.total.toLocaleString()}
                </Typography>
                <Typography variant="caption" color="text.secondary">Total Requirements</Typography>
              </CardContent>
            </Card>
          </Grid>
          <Grid item xs={6} sm={3}>
            <Card sx={{ textAlign: 'center', py: 2 }}>
              <CardContent sx={{ p: '12px !important' }}>
                <Typography variant="h4" fontWeight={700} color="success.main">
                  {summary.compliant.toLocaleString()}
                </Typography>
                <Typography variant="caption" color="text.secondary">Compliant</Typography>
              </CardContent>
            </Card>
          </Grid>
          <Grid item xs={6} sm={3}>
            <Card sx={{ textAlign: 'center', py: 2 }}>
              <CardContent sx={{ p: '12px !important' }}>
                <Typography variant="h4" fontWeight={700} color="error.main">
                  {summary.non_compliant.toLocaleString()}
                </Typography>
                <Typography variant="caption" color="text.secondary">Non-Compliant</Typography>
              </CardContent>
            </Card>
          </Grid>
          <Grid item xs={6} sm={3}>
            <Card sx={{ textAlign: 'center', py: 2 }}>
              <CardContent sx={{ p: '12px !important' }}>
                <Typography variant="h4" fontWeight={700} color="warning.main">
                  {summary.not_assessed.toLocaleString()}
                </Typography>
                <Typography variant="caption" color="text.secondary">Not Assessed</Typography>
              </CardContent>
            </Card>
          </Grid>
        </Grid>

        <Tabs value={activeTab} onChange={(_, v) => setActiveTab(v)} sx={{ mb: 3 }}>
          <Tab label="Agent Conversations" icon={<WorkflowIcon />} iconPosition="start" />
          <Tab label="Compliance Matrix" icon={<ScoreIcon />} iconPosition="start" />
        </Tabs>

        {/* Agent Conversations */}
        {activeTab === 0 && (
          <Card className="card">
            <CardContent>
              <Typography variant="h6" fontWeight={700} gutterBottom>Agent Conversations</Typography>
              {convLoading ? (
                <LinearProgress />
              ) : conversations.length === 0 ? (
                <Alert severity="info">
                  No agent conversations yet. Extract RFP requirements or run analysis tasks to generate conversations.
                </Alert>
              ) : (
                <>
                  <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
                    <Table size="small">
                      <TableHead>
                        <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                          <TableCell sx={{ fontWeight: 700 }}>ID</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Type</TableCell>
                          <TableCell align="center" sx={{ fontWeight: 700 }}>Loops</TableCell>
                          <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Summary</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Date</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {pagedConversations.map(c => (
                          <TableRow key={c.id} hover>
                            <TableCell>#{c.id}</TableCell>
                            <TableCell>
                              <Chip label={c.type?.replace('_', ' ')} size="small" variant="outlined" />
                            </TableCell>
                            <TableCell align="center">{c.current_loop}/{c.max_loops}</TableCell>
                            <TableCell align="center">
                              <Chip
                                label={c.status}
                                size="small"
                                color={
                                  c.status === 'completed' || c.status === 'consensus_reached' ? 'success'
                                    : c.status === 'active' ? 'primary'
                                      : 'default'
                                }
                              />
                            </TableCell>
                            <TableCell>
                              <Typography
                                variant="body2"
                                sx={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                              >
                                {c.summary || '—'}
                              </Typography>
                            </TableCell>
                            <TableCell>
                              <Typography variant="caption">
                                {c.created_at ? new Date(c.created_at).toLocaleString() : '—'}
                              </Typography>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                  <TablePagination
                    component="div"
                    count={conversations.length}
                    page={convPage}
                    onPageChange={(_, p) => setConvPage(p)}
                    rowsPerPage={convRowsPerPage}
                    onRowsPerPageChange={(e) => { setConvRowsPerPage(parseInt(e.target.value, 10)); setConvPage(0); }}
                    rowsPerPageOptions={[10, 25, 50, 100]}
                  />
                </>
              )}
            </CardContent>
          </Card>
        )}

        {/* Compliance Matrix */}
        {activeTab === 1 && (
          <Card className="card">
            <CardContent>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', mb: 2 }}>
                <Typography variant="h6" fontWeight={700} sx={{ flex: '0 0 auto' }}>
                  Requirement Compliance
                </Typography>
                <Box sx={{ flex: 1 }} />
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={reqViewMode}
                  onChange={(_, v) => v && setReqViewMode(v)}
                >
                  <ToggleButton value="table">
                    <TableRowsIcon fontSize="small" sx={{ mr: 0.5 }} />
                    Table
                  </ToggleButton>
                  <ToggleButton value="section">
                    <ViewAgendaIcon fontSize="small" sx={{ mr: 0.5 }} />
                    By Section
                  </ToggleButton>
                </ToggleButtonGroup>
              </Box>

              {reqViewMode === 'section' ? (
                <RequirementsBySection
                  proposalId={DEFAULT_PROPOSAL_ID}
                  emptyMessage="No requirements extracted yet. Upload and process the RFP in RFP Setup first."
                />
              ) : (
              <>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', mb: 2 }}>
                <TextField
                  size="small"
                  placeholder="Search title, text, section, ID…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <SearchIcon fontSize="small" />
                      </InputAdornment>
                    ),
                  }}
                  sx={{ flex: '1 1 320px', maxWidth: 420 }}
                />
                <TextField
                  size="small"
                  select
                  label="Status"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  sx={{ minWidth: 180 }}
                >
                  <MenuItem value="">All statuses</MenuItem>
                  <MenuItem value="compliant">Compliant</MenuItem>
                  <MenuItem value="non_compliant">Non-Compliant</MenuItem>
                  <MenuItem value="not_assessed">Not Assessed</MenuItem>
                </TextField>
                <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
                  {reqLoading
                    ? 'Loading…'
                    : `${reqTotal.toLocaleString()} matching requirement${reqTotal === 1 ? '' : 's'}`}
                </Typography>
              </Box>

              {reqLoading && <LinearProgress sx={{ mb: 1 }} />}

              {!reqLoading && reqTotal === 0 ? (
                <Alert severity="info">
                  {search || statusFilter
                    ? 'No requirements match the current filter.'
                    : 'No requirements extracted yet. Upload and process the RFP in RFP Setup first.'}
                </Alert>
              ) : (
                <>
                  <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2, maxHeight: 600 }}>
                    <Table size="small" stickyHeader>
                      <TableHead>
                        <TableRow>
                          <TableCell sx={{ fontWeight: 700 }}>ID</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Requirement</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Section</TableCell>
                          <TableCell sx={{ fontWeight: 700 }}>Category</TableCell>
                          <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                          <TableCell align="center" sx={{ fontWeight: 700 }}>Verified</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {requirements.map(r => (
                          <TableRow key={r.id} hover>
                            <TableCell>
                              <Chip label={r.requirement_id} size="small" variant="outlined" />
                            </TableCell>
                            <TableCell sx={{ maxWidth: 480, overflow: 'hidden' }}>
                              <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>{r.title}</Typography>
                              <CitationChips
                                requirementId={r.id}
                                text={[r.description, r.source_text]}
                              />
                            </TableCell>
                            <TableCell>
                              <Chip label={r.section_id} size="small" variant="outlined" />
                            </TableCell>
                            <TableCell>
                              <Chip
                                label={r.category}
                                size="small"
                                color={r.category === 'mandatory' ? 'error' : 'default'}
                                variant="outlined"
                              />
                            </TableCell>
                            <TableCell align="center">
                              <Chip
                                label={r.compliance_status?.replace('_', ' ')}
                                size="small"
                                color={
                                  r.compliance_status === 'compliant' ? 'success'
                                    : r.compliance_status === 'non_compliant' ? 'error'
                                      : 'default'
                                }
                              />
                            </TableCell>
                            <TableCell align="center">
                              {r.verified
                                ? <CheckIcon color="success" fontSize="small" />
                                : <WarningIcon color="disabled" fontSize="small" />}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                  <TablePagination
                    component="div"
                    count={reqTotal}
                    page={reqPage}
                    onPageChange={(_, p) => setReqPage(p)}
                    rowsPerPage={reqRowsPerPage}
                    onRowsPerPageChange={(e) => setReqRowsPerPage(parseInt(e.target.value, 10))}
                    rowsPerPageOptions={[25, 50, 100, 250]}
                  />
                </>
              )}
              </>
              )}
            </CardContent>
          </Card>
        )}
      </Box>
    </Fade>
  );
};

export default Review;
