import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Button, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert,
  Fade, MenuItem, Select, FormControl, InputLabel,
  TablePagination, InputAdornment, Stack,
  ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  Refresh as RefreshIcon,
  Search as SearchIcon,
  TableRows as TableRowsIcon,
  ViewAgenda as ViewAgendaIcon,
  CheckCircle as CheckIcon,
  Warning as WarningIcon,
} from '@mui/icons-material';
import axios from 'axios';
import RequirementsBySection from '../components/RequirementsBySection';
import { useProposal } from '../proposal/ProposalContext';

const RfpSetup = () => {
  // Proposal we're editing. Pulled from ProposalContext (URL `/p/:id/...`,
  // ?proposal= query, localStorage, or most-recent fallback). The legacy
  // ?proposal=<id> query path also works because ProposalContext reads it.
  const { proposalId: ctxProposalId } = useProposal();
  const proposalId = ctxProposalId || 1;

  const [rfpDocs, setRfpDocs] = useState([]);
  const [summary, setSummary] = useState({ totals: {}, by_document: [] });
  const [requirements, setRequirements] = useState([]);
  const [totalReqs, setTotalReqs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [extracting, setExtracting] = useState(false);
  // Tracks which document_id is currently being (re-)extracted, so we can
  // disable just THAT row's button instead of every row's.
  const [extractingDocId, setExtractingDocId] = useState(null);
  // Confirmation dialog state for the "are you sure you want to re-extract?"
  // flow. Holds the document being re-extracted plus its ETA payload.
  const [reExtractConfirm, setReExtractConfirm] = useState(null);
  // ^ shape: { doc, summaryRow } where summaryRow is the entry from
  //   `summary.by_document` (carries last_extracted_at + last_duration_seconds).
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadDesc, setUploadDesc] = useState('');
  const [uploadDocType, setUploadDocType] = useState('rfp');
  const [uploading, setUploading] = useState(false);

  // Filters (server-side)
  const [filterSection, setFilterSection] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [filterCompliance, setFilterCompliance] = useState('');
  const [searchText, setSearchText] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');

  // Pagination
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);

  // View mode: hierarchical section accordions (default) vs. flat paginated table.
  const [viewMode, setViewMode] = useState('section');

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchText.trim()), 350);
    return () => clearTimeout(t);
  }, [searchText]);

  // Reset to first page when filters change
  useEffect(() => { setPage(0); }, [filterSection, filterCategory, filterCompliance, debouncedSearch, rowsPerPage]);

  // Load the docs list + summary once (cheap).
  const loadSummary = useCallback(async () => {
    try {
      const [docsRes, summaryRes] = await Promise.all([
        axios.get('/api/documents', { params: { source_type: 'rfp' } }),
        axios.get('/api/knowledge/requirements/summary', { params: { proposal_id: proposalId } })
          .catch(() => ({ data: { totals: {}, by_document: [] } })),
      ]);
      const docsPayload = docsRes.data;
      setRfpDocs(Array.isArray(docsPayload) ? docsPayload : (docsPayload?.documents || []));
      setSummary(summaryRes.data || { totals: {}, by_document: [] });
    } catch (e) { console.error('Failed to load RFP summary', e); }
  }, [proposalId]);

  // Load a single page of requirements (cheap, server-side filtered).
  const loadPage = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        proposal_id: proposalId,
        limit: rowsPerPage,
        offset: page * rowsPerPage,
      };
      if (filterSection) params.section_id = filterSection;
      if (filterCategory) params.category = filterCategory;
      if (filterCompliance) params.compliance_status = filterCompliance;
      if (debouncedSearch) params.search = debouncedSearch;
      const { data } = await axios.get('/api/knowledge/requirements', { params });
      setRequirements(data.requirements || []);
      setTotalReqs(data.total || 0);
    } catch (e) { console.error('Failed to load requirements', e); }
    finally { setLoading(false); }
  }, [proposalId, page, rowsPerPage, filterSection, filterCategory, filterCompliance, debouncedSearch]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => { loadPage(); }, [loadPage]);

  const handleUpload = async () => {
    if (!selectedFile) return;
    setUploading(true);
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('source_type', 'rfp');
    if (uploadDocType) fd.append('document_type', uploadDocType);
    if (uploadDesc) fd.append('description', uploadDesc);
    try {
      await axios.post('/api/documents/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      setUploadOpen(false); setSelectedFile(null); setUploadDesc(''); setUploadDocType('rfp');
      loadSummary(); loadPage();
    } catch (e) { alert(e.response?.data?.detail || 'Upload failed'); }
    finally { setUploading(false); }
  };

  // Run the actual extraction. Used both for first-time runs and (after
  // confirmation) for re-extractions. The backend's extract_rfp_requirements
  // already does replace_existing=True so the call is idempotent.
  const runExtraction = async (docId) => {
    setExtracting(true);
    setExtractingDocId(docId);
    try {
      const { data } = await axios.post(`/api/knowledge/extract-requirements/${docId}`);
      alert(`Extracted ${data.requirements_extracted} requirements via triple-check pipeline`);
      loadSummary(); loadPage();
    } catch (e) {
      alert(e.response?.data?.detail || 'Extraction failed');
    } finally {
      setExtracting(false);
      setExtractingDocId(null);
    }
  };

  // First-time extraction: run immediately, no confirmation dialog.
  const handleExtract = (docId) => runExtraction(docId);

  // Re-extraction: open the confirmation dialog showing the warning + ETA.
  // Confirmed re-runs delete the existing requirement set (handled server-side
  // via replace_existing=True in extract_rfp_requirements).
  const handleReExtract = (doc, summaryRow) => {
    setReExtractConfirm({ doc, summaryRow });
  };

  const confirmReExtract = async () => {
    const docId = reExtractConfirm?.doc?.id;
    setReExtractConfirm(null);
    if (docId) await runExtraction(docId);
  };

  // Format ETA in human terms (e.g. "~7 minutes" or "~45 seconds").
  const formatEta = (seconds) => {
    if (!seconds || seconds <= 0) return null;
    if (seconds < 90) return `~${Math.round(seconds)} seconds`;
    return `~${Math.round(seconds / 60)} minutes`;
  };

  const handleStatusChange = async (reqId, newStatus) => {
    try {
      await axios.put(`/api/knowledge/requirements/${reqId}`, { compliance_status: newStatus });
      setRequirements(prev => prev.map(r => r.id === reqId ? { ...r, compliance_status: newStatus } : r));
    } catch { /* ignore */ }
  };

  // Derive section/category option lists from the summary (covers ALL requirements, not just this page).
  const sectionOptions = Object.keys(summary?.totals?.by_section || {}).sort();
  const categoryOptions = Object.keys(summary?.totals?.by_category || {}).sort();
  // Fall back to scanning docs if `by_section` isn't populated (older summary shape).
  const fallbackCategories = [...new Set((summary?.by_document || []).flatMap(d => Object.keys(d.by_category || {})))].sort();
  const categoriesToShow = categoryOptions.length ? categoryOptions : fallbackCategories;

  const totals = summary?.totals || {};
  const compliantReqs = (totals.by_compliance && totals.by_compliance.compliant) || 0;
  const notAssessed = (totals.by_compliance && (totals.by_compliance.not_assessed || totals.by_compliance.not_reviewed)) || 0;
  const grandTotal = totals.total || totalReqs;

  return (
    <Fade in timeout={600}>
      <Box>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
          <Button variant="contained" startIcon={<UploadIcon />} onClick={() => setUploadOpen(true)}
            sx={{ borderRadius: 3, background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)' }}>
            Upload RFP
          </Button>
        </Box>

        {/* RFP Documents */}
        <Card className="card" sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" fontWeight={700} gutterBottom>RFP Documents</Typography>
            {rfpDocs.length === 0 ? (
              <Alert severity="info">No RFP documents uploaded yet. Upload the RFP to begin requirement extraction.</Alert>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                      <TableCell sx={{ fontWeight: 700 }}>Document</TableCell>
                      <TableCell align="center" sx={{ fontWeight: 700 }}>Pages</TableCell>
                      <TableCell align="center" sx={{ fontWeight: 700 }}>Chunks</TableCell>
                      <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                      <TableCell align="center" sx={{ fontWeight: 700 }}>Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {rfpDocs.map(doc => {
                      // Look up extraction state from the summary so we can
                      // pick the right action and surface count + ETA.
                      const summaryRow = (summary?.by_document || []).find(d => d.document_id === doc.id);
                      const reqCount = summaryRow?.total || 0;
                      const isAlreadyExtracted = reqCount > 0;
                      const busyHere = extracting && extractingDocId === doc.id;
                      const busyOther = extracting && extractingDocId !== doc.id;
                      return (
                        <TableRow key={doc.id}>
                          <TableCell>
                            <Typography variant="body2" fontWeight={600}>{doc.filename}</Typography>
                            {doc.description && <Typography variant="caption" color="text.secondary">{doc.description}</Typography>}
                          </TableCell>
                          <TableCell align="center">{doc.total_pages}</TableCell>
                          <TableCell align="center">{doc.total_chunks}</TableCell>
                          <TableCell align="center"><Chip label={doc.status} size="small" color={doc.status === 'completed' ? 'success' : 'warning'} /></TableCell>
                          <TableCell align="center">
                            {isAlreadyExtracted ? (
                              <Stack direction="row" spacing={1} justifyContent="center" alignItems="center">
                                <Chip
                                  size="small"
                                  color="success"
                                  icon={<CheckIcon />}
                                  label={`Extracted (${reqCount.toLocaleString()} reqs)`}
                                />
                                <Button
                                  size="small"
                                  variant="outlined"
                                  color="warning"
                                  startIcon={<RefreshIcon />}
                                  onClick={() => handleReExtract(doc, summaryRow)}
                                  disabled={extracting}
                                >
                                  {busyHere ? 'Re-running…' : 'Re-run extraction'}
                                </Button>
                              </Stack>
                            ) : (
                              <Button
                                size="small"
                                variant="outlined"
                                startIcon={<RefreshIcon />}
                                onClick={() => handleExtract(doc.id)}
                                disabled={busyOther}
                              >
                                {busyHere ? 'Extracting…' : 'Extract Requirements'}
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </CardContent>
        </Card>

        {/* Requirements Summary */}
        {grandTotal > 0 && (
          <Grid container spacing={2} sx={{ mb: 3 }}>
            <Grid item xs={6} sm={3}>
              <Card sx={{ textAlign: 'center', py: 2 }}>
                <CardContent sx={{ p: '12px !important' }}>
                  <Typography variant="h4" fontWeight={700} color="primary">{grandTotal.toLocaleString()}</Typography>
                  <Typography variant="caption" color="text.secondary">Total Requirements</Typography>
                </CardContent>
              </Card>
            </Grid>
            <Grid item xs={6} sm={3}>
              <Card sx={{ textAlign: 'center', py: 2 }}>
                <CardContent sx={{ p: '12px !important' }}>
                  <Typography variant="h4" fontWeight={700} color="success.main">{compliantReqs.toLocaleString()}</Typography>
                  <Typography variant="caption" color="text.secondary">Compliant</Typography>
                </CardContent>
              </Card>
            </Grid>
            <Grid item xs={6} sm={3}>
              <Card sx={{ textAlign: 'center', py: 2 }}>
                <CardContent sx={{ p: '12px !important' }}>
                  <Typography variant="h4" fontWeight={700} color="warning.main">{notAssessed.toLocaleString()}</Typography>
                  <Typography variant="caption" color="text.secondary">Not Assessed</Typography>
                </CardContent>
              </Card>
            </Grid>
            <Grid item xs={6} sm={3}>
              <Card sx={{ textAlign: 'center', py: 2 }}>
                <CardContent sx={{ p: '12px !important' }}>
                  <Typography variant="h4" fontWeight={700}>{grandTotal > 0 ? Math.round(compliantReqs / grandTotal * 100) : 0}%</Typography>
                  <Typography variant="caption" color="text.secondary">Compliance Rate</Typography>
                </CardContent>
              </Card>
            </Grid>
          </Grid>
        )}

        {/* Requirements Table */}
        {grandTotal > 0 && (
          <Card className="card">
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
                <Typography variant="h6" fontWeight={700}>Requirements Matrix</Typography>
                <Box sx={{ flex: 1 }} />
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={viewMode}
                  onChange={(_, v) => v && setViewMode(v)}
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
              </Stack>

              {viewMode === 'section' ? (
                <RequirementsBySection
                  proposalId={proposalId}
                  emptyMessage="No requirements match the current filters."
                />
              ) : (
              <>
              <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
                <TextField
                  size="small"
                  placeholder="Search title / description / source text…"
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  sx={{ minWidth: 260, flex: 1 }}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <SearchIcon fontSize="small" />
                      </InputAdornment>
                    ),
                  }}
                />
                <FormControl size="small" sx={{ minWidth: 150 }}>
                  <InputLabel>Section</InputLabel>
                  <Select value={filterSection} label="Section" onChange={(e) => setFilterSection(e.target.value)}>
                    <MenuItem value="">All</MenuItem>
                    {sectionOptions.map(s => <MenuItem key={s} value={s}>{s}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 150 }}>
                  <InputLabel>Category</InputLabel>
                  <Select value={filterCategory} label="Category" onChange={(e) => setFilterCategory(e.target.value)}>
                    <MenuItem value="">All</MenuItem>
                    {categoriesToShow.map(c => <MenuItem key={c} value={c}>{c}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" sx={{ minWidth: 170 }}>
                  <InputLabel>Compliance</InputLabel>
                  <Select value={filterCompliance} label="Compliance" onChange={(e) => setFilterCompliance(e.target.value)}>
                    <MenuItem value="">All</MenuItem>
                    <MenuItem value="not_assessed">Not Assessed</MenuItem>
                    <MenuItem value="compliant">Compliant</MenuItem>
                    <MenuItem value="partial">Partial</MenuItem>
                    <MenuItem value="non_compliant">Non-Compliant</MenuItem>
                    <MenuItem value="not_applicable">N/A</MenuItem>
                  </Select>
                </FormControl>
              </Stack>
              {loading && <LinearProgress sx={{ mb: 1 }} />}
              <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontWeight: 700, width: 100 }}>ID</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>Requirement</TableCell>
                      <TableCell sx={{ fontWeight: 700, width: 120 }}>Section</TableCell>
                      <TableCell sx={{ fontWeight: 700, width: 100 }}>Category</TableCell>
                      <TableCell align="center" sx={{ fontWeight: 700, width: 140 }}>Status</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {requirements.map(req => (
                      <TableRow key={req.id} hover>
                        <TableCell><Chip label={req.requirement_id} size="small" variant="outlined" /></TableCell>
                        <TableCell>
                          <Typography variant="body2" fontWeight={600}>{req.title}</Typography>
                          {req.description && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>{req.description.substring(0, 150)}{req.description.length > 150 ? '…' : ''}</Typography>}
                        </TableCell>
                        <TableCell><Chip label={req.section_id} size="small" variant="outlined" /></TableCell>
                        <TableCell><Chip label={req.category} size="small" color={req.category === 'mandatory' ? 'error' : 'default'} variant="outlined" /></TableCell>
                        <TableCell align="center">
                          <Select size="small" value={req.compliance_status || 'not_assessed'}
                            onChange={(e) => handleStatusChange(req.id, e.target.value)}
                            sx={{ fontSize: 12, minWidth: 120 }}>
                            <MenuItem value="not_assessed">Not Assessed</MenuItem>
                            <MenuItem value="compliant">Compliant</MenuItem>
                            <MenuItem value="partial">Partial</MenuItem>
                            <MenuItem value="non_compliant">Non-Compliant</MenuItem>
                            <MenuItem value="not_applicable">N/A</MenuItem>
                          </Select>
                        </TableCell>
                      </TableRow>
                    ))}
                    {requirements.length === 0 && !loading && (
                      <TableRow><TableCell colSpan={5} align="center">
                        <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
                          No requirements match these filters.
                        </Typography>
                      </TableCell></TableRow>
                    )}
                  </TableBody>
                </Table>
                <TablePagination
                  component="div"
                  count={totalReqs}
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
              </>
              )}
            </CardContent>
          </Card>
        )}

        {/* Upload Dialog */}
        <Dialog open={uploadOpen} onClose={() => setUploadOpen(false)} maxWidth="sm" fullWidth>
          <DialogTitle>Upload RFP Document</DialogTitle>
          <DialogContent>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
              <input accept=".pdf,.doc,.docx,.txt" style={{ display: 'none' }} id="rfp-upload" type="file"
                onChange={(e) => setSelectedFile(e.target.files[0])} />
              <label htmlFor="rfp-upload">
                <Box sx={{ cursor: 'pointer', border: '2px dashed rgba(0,174,230,0.3)', borderRadius: 3,
                  p: 3, textAlign: 'center', '&:hover': { borderColor: '#00AEE6' } }}>
                  <UploadIcon sx={{ fontSize: 40, color: '#00AEE6', mb: 1 }} />
                  <Typography variant="body1" fontWeight={600}>
                    {selectedFile ? selectedFile.name : 'Select the RFP document'}
                  </Typography>
                </Box>
              </label>
              <FormControl fullWidth size="small">
                <InputLabel>Document Type</InputLabel>
                <Select label="Document Type" value={uploadDocType}
                  onChange={(e) => setUploadDocType(e.target.value)}>
                  <MenuItem value="notice_of_solicitation">Notice of Bid Solicitation</MenuItem>
                  <MenuItem value="rfp">RFP / Full Solicitation</MenuItem>
                  <MenuItem value="addendum">Addendum / Amendment</MenuItem>
                  <MenuItem value="qa_response">Q&amp;A Response</MenuItem>
                  <MenuItem value="attachment">Attachment / Exhibit</MenuItem>
                  <MenuItem value="reference">Reference Material</MenuItem>
                </Select>
              </FormControl>
              <Alert severity="info" sx={{ fontSize: 13 }}>
                Uploading a <strong>Notice of Bid Solicitation</strong>, <strong>RFP</strong>,
                or <strong>Addendum</strong> will automatically extract the schedule of
                key events (proposal due date, pre-bid conference, Q&amp;A cutoff, etc.).
              </Alert>
              <TextField fullWidth size="small" label="Description" value={uploadDesc}
                onChange={(e) => setUploadDesc(e.target.value)} />
            </Box>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setUploadOpen(false)}>Cancel</Button>
            <Button variant="contained" onClick={handleUpload} disabled={!selectedFile || uploading}>
              {uploading ? 'Uploading…' : 'Upload RFP'}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Re-extraction confirmation dialog ─────────────────────────
            Appears only when the user clicks "Re-run extraction" on a
            document that already has requirements. Per the spec, this
            warns that confirming will delete all existing requirement
            data for the document and quotes the previous run's wall-clock
            time as an ETA so the user knows what they're committing to. */}
        <Dialog
          open={!!reExtractConfirm}
          onClose={() => setReExtractConfirm(null)}
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, color: 'warning.main' }}>
            <WarningIcon color="warning" />
            Re-run extraction for "{reExtractConfirm?.doc?.filename}"?
          </DialogTitle>
          <DialogContent>
            <Alert severity="warning" sx={{ mb: 2 }}>
              <Typography variant="body2" fontWeight={700} sx={{ mb: 0.5 }}>
                WARNING
              </Typography>
              <Typography variant="body2">
                By selecting yes you will delete all existing data collected for this
                document and restart the process.
              </Typography>
            </Alert>
            <Stack spacing={1} sx={{ mb: 2 }}>
              <Typography variant="body2">
                <strong>Existing requirements:</strong>{' '}
                {(reExtractConfirm?.summaryRow?.total || 0).toLocaleString()} rows will
                be removed before extraction starts.
              </Typography>
              {reExtractConfirm?.summaryRow?.last_extracted_at && (
                <Typography variant="body2">
                  <strong>Last extracted:</strong>{' '}
                  {new Date(reExtractConfirm.summaryRow.last_extracted_at).toLocaleString()}
                </Typography>
              )}
              <Typography variant="body2">
                <strong>ETA for completion:</strong>{' '}
                {formatEta(reExtractConfirm?.summaryRow?.last_duration_seconds)
                  || 'no prior duration recorded — estimate ~5–15 minutes per RFP'}
              </Typography>
            </Stack>
            <Typography variant="body2" fontWeight={600}>
              Do you wish to continue?
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setReExtractConfirm(null)}>Cancel</Button>
            <Button
              variant="contained"
              color="warning"
              onClick={confirmReExtract}
              startIcon={<RefreshIcon />}
            >
              Yes, re-run extraction
            </Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Fade>
  );
};

export default RfpSetup;
