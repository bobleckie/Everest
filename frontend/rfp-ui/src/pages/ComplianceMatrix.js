import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { useProposal } from '../proposal/ProposalContext';
import axios from 'axios';
import { exportToExcel } from '../utils/exportExcel';
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  TextField,
  MenuItem,
  Button,
  Alert,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  TablePagination,
  IconButton,
  Collapse,
  Tooltip,
  Divider,
  LinearProgress,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  FileDownload as DownloadIcon,
  Refresh as RefreshIcon,
  CheckCircle as CheckIcon,
  Cancel as CancelIcon,
  HelpOutline as QuestionIcon,
  Warning as WarningIcon,
  TableRows as TableRowsIcon,
  ViewAgenda as ViewAgendaIcon,
  PictureAsPdf as PdfIcon,
  Description as DocIcon,
  TableChart as XlsxIcon,
  Article as TxtIcon,
  InsertDriveFile as GenericFileIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import RequirementsBySection from '../components/RequirementsBySection';

// --- palette helpers ------------------------------------------------

const CAT_COLORS = {
  mandatory: '#d32f2f',
  scored: '#7b1fa2',
  certification: '#00796b',
  form: '#1976d2',
  signature: '#455a64',
  deadline: '#e65100',
  informational: '#757575',
  information: '#757575',
  legal_reference: '#5d4037',
  legal: '#5d4037',
  limitation: '#ad1457',
  restriction: '#ad1457',
  exclusion: '#bf360c',
  consequence: '#bf360c',
  approval: '#00695c',
  definition: '#0277bd',
};
const PRIORITY_COLORS = {
  critical: '#b71c1c',
  high: '#e65100',
  medium: '#0277bd',
  low: '#2e7d32',
};
const COMPLIANCE_OPTIONS = [
  { value: 'not_reviewed', label: 'Not Reviewed' },
  { value: 'compliant', label: 'Compliant' },
  { value: 'partial', label: 'Partially Compliant' },
  { value: 'non_compliant', label: 'Non-Compliant' },
  { value: 'not_applicable', label: 'Not Applicable' },
];
const COMPLIANCE_ICON = {
  compliant: <CheckIcon sx={{ color: '#2e7d32', fontSize: 18 }} />,
  partial: <WarningIcon sx={{ color: '#f57c00', fontSize: 18 }} />,
  non_compliant: <CancelIcon sx={{ color: '#c62828', fontSize: 18 }} />,
  not_applicable: <QuestionIcon sx={{ color: '#9e9e9e', fontSize: 18 }} />,
  not_reviewed: <QuestionIcon sx={{ color: '#bdbdbd', fontSize: 18 }} />,
};

// Highlight shall/must/will/required tokens in source text.
const MANDATORY_RE = /\b(shall|must|will|required to|is required|no later than|at a minimum|mandatory)\b/gi;
function highlightMandatory(text) {
  if (!text) return '';
  const parts = [];
  let lastIndex = 0;
  let match;
  const re = new RegExp(MANDATORY_RE.source, 'gi');
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <Box
        component="span"
        key={`m-${match.index}`}
        sx={{
          backgroundColor: '#fff3c4',
          color: '#8a6d00',
          fontWeight: 700,
          px: 0.25,
          borderRadius: 0.5,
        }}
      >
        {match[0]}
      </Box>
    );
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

// Fallback for the case where the user lands on this page without ever
// touching the portfolio. ProposalContext will normally have resolved
// proposalId by the time we render — the constant is just the last-resort.
const DEFAULT_PROPOSAL_ID = 1;

// Pick a sensible Material icon + tint by file extension. Keeps the
// "By Document" filter row compact and visually scannable instead of the
// old variable-width card soup.
function docIconFor(name) {
  const ext = (name || '').split('.').pop().toLowerCase();
  if (ext === 'pdf') return { Icon: PdfIcon, color: '#c62828' };
  if (ext === 'doc' || ext === 'docx') return { Icon: DocIcon, color: '#1565c0' };
  if (ext === 'xls' || ext === 'xlsx' || ext === 'csv') return { Icon: XlsxIcon, color: '#2e7d32' };
  if (ext === 'txt' || ext === 'md') return { Icon: TxtIcon, color: '#616161' };
  return { Icon: GenericFileIcon, color: '#546e7a' };
}

export default function ComplianceMatrix() {
  // The active proposal id flows from ProposalContext (URL `/p/:id/...`,
  // ?proposal=, localStorage, or most-recent fallback). When nothing has
  // resolved yet we fall back to the seed-data DEFAULT so existing test
  // workflows keep working until the user has picked a proposal.
  const { proposalId: ctxProposalId } = useProposal();
  const proposalId = ctxProposalId || DEFAULT_PROPOSAL_ID;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(null);
  const [requirements, setRequirements] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [expandedRows, setExpandedRows] = useState(() => new Set());

  const [filterDoc, setFilterDoc] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [filterPriority, setFilterPriority] = useState('');
  const [filterCompliance, setFilterCompliance] = useState('');
  // Parsons-evidence coverage filter — maps to the new
  // ?parsons_coverage_status= query param on /api/knowledge/requirements.
  const [filterParsonsCoverage, setFilterParsonsCoverage] = useState('');
  const [searchText, setSearchText] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');

  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);

  // View mode: hierarchical section accordions (default) vs. flat paginated table.
  const [viewMode, setViewMode] = useState('section');

  // Debounce search input so we don't hammer the backend while typing
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchText.trim()), 300);
    return () => clearTimeout(t);
  }, [searchText]);

  // Reset page whenever the filter criteria change
  useEffect(() => {
    setPage(0);
  }, [filterDoc, filterCategory, filterPriority, filterCompliance, filterParsonsCoverage, debouncedSearch]);

  // Load the summary (small, fast) only once / on explicit refresh
  const loadSummary = useCallback(async () => {
    try {
      const res = await axios.get('/api/knowledge/requirements/summary', {
        params: { proposal_id: proposalId },
      });
      setSummary(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load summary');
    }
  }, [proposalId]);

  // Load one page of requirements, server-side filtered / searched / paginated
  const loadPage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {
        proposal_id: proposalId,
        limit: rowsPerPage,
        offset: page * rowsPerPage,
      };
      if (filterDoc) params.document_id = filterDoc;
      if (filterCategory) params.category = filterCategory;
      if (filterPriority) params.priority = filterPriority;
      if (filterCompliance) params.compliance_status = filterCompliance;
      if (filterParsonsCoverage) params.parsons_coverage_status = filterParsonsCoverage;
      if (debouncedSearch) params.search = debouncedSearch;
      const res = await axios.get('/api/knowledge/requirements', { params });
      setRequirements(res.data.requirements || []);
      setTotalCount(res.data.total || 0);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load requirements');
    } finally {
      setLoading(false);
    }
  }, [proposalId, page, rowsPerPage, filterDoc, filterCategory,
      filterPriority, filterCompliance, filterParsonsCoverage, debouncedSearch]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => { loadPage(); }, [loadPage]);

  const loadData = useCallback(() => {
    loadSummary();
    loadPage();
  }, [loadSummary, loadPage]);

  // Server did the filtering; the current page is `requirements` directly.
  const filtered = requirements;

  const toggleRow = (id) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const updateRequirement = async (id, patch) => {
    // Optimistic UI
    setRequirements((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
    try {
      await axios.put(`/api/knowledge/requirements/${id}`, patch);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Update failed');
      await loadData();
    }
  };

  const handleComplianceChange = (id, value) => {
    updateRequirement(id, { compliance_status: value });
  };
  const handleVerifiedToggle = (id, currentlyVerified) => {
    updateRequirement(id, { verified: !currentlyVerified });
  };
  const handleEvidenceChange = (id, value) => {
    updateRequirement(id, { parsons_evidence: value });
  };

  const [exporting, setExporting] = useState(false);

  const exportCsv = async () => {
    setExporting(true);
    setError(null);
    try {
      // Re-query the backend with the current filters but without pagination,
      // so the CSV contains every matching row (not just the visible page).
      const params = { proposal_id: proposalId };
      if (filterDoc) params.document_id = filterDoc;
      if (filterCategory) params.category = filterCategory;
      if (filterPriority) params.priority = filterPriority;
      if (filterCompliance) params.compliance_status = filterCompliance;
      if (filterParsonsCoverage) params.parsons_coverage_status = filterParsonsCoverage;
      if (debouncedSearch) params.search = debouncedSearch;
      const res = await axios.get('/api/knowledge/requirements', { params });
      const all = res.data.requirements || [];

      const headers = [
        'Document', 'RequirementID', 'Section', 'Category', 'Priority', 'Title',
        'Description', 'SourceText', 'Page', 'ExtractionPass', 'Confidence',
        'ComplianceStatus', 'ParsonsEvidence', 'Verified',
      ];
      const escape = (v) => {
        if (v == null) return '';
        const s = String(v).replace(/"/g, '""');
        return `"${s}"`;
      };
      const rows = [headers.join(',')];
      all.forEach((r) => {
        rows.push([
          escape(r.document_name),
          escape(r.requirement_id),
          escape(r.section_id),
          escape(r.category),
          escape(r.priority),
          escape(r.title),
          escape(r.description),
          escape(r.source_text),
          escape(r.source_page),
          escape(r.extraction_pass),
          escape(r.reviewer_confidence),
          escape(r.compliance_status || 'not_reviewed'),
          escape(r.parsons_evidence),
          escape(r.verified ? 'yes' : 'no'),
        ].join(','));
      });
      const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `compliance-matrix-proposal-${proposalId}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'CSV export failed');
    } finally {
      setExporting(false);
    }
  };

  const exportExcel = async () => {
    setExporting(true);
    setError(null);
    try {
      const params = { proposal_id: proposalId };
      if (filterDoc) params.document_id = filterDoc;
      if (filterCategory) params.category = filterCategory;
      if (filterPriority) params.priority = filterPriority;
      if (filterCompliance) params.compliance_status = filterCompliance;
      if (filterParsonsCoverage) params.parsons_coverage_status = filterParsonsCoverage;
      if (debouncedSearch) params.search = debouncedSearch;
      const res = await axios.get('/api/knowledge/requirements', { params });
      const all = res.data.requirements || [];
      exportToExcel({
        filename: `compliance-matrix-proposal-${proposalId}`,
        sheetName: 'Compliance Matrix',
        columns: [
          { header: 'Document', key: 'document_name', width: 30 },
          { header: 'Requirement ID', key: 'requirement_id', width: 18 },
          { header: 'Section', key: 'section_id', width: 12 },
          { header: 'Category', key: 'category', width: 16 },
          { header: 'Priority', key: 'priority', width: 12 },
          { header: 'Title', key: 'title', width: 50 },
          { header: 'Description', key: 'description', width: 60 },
          { header: 'Source Text', key: 'source_text', width: 70 },
          { header: 'Page', key: 'source_page', width: 8 },
          { header: 'Extraction Pass', key: 'extraction_pass', width: 16 },
          { header: 'Confidence', key: 'reviewer_confidence', width: 14 },
          { header: 'Compliance Status', key: 'compliance_status', width: 20, transform: (v) => v || 'not_reviewed' },
          { header: 'Parsons Evidence', key: 'parsons_evidence', width: 40 },
          { header: 'Verified', key: 'verified', width: 10, transform: (v) => v ? 'yes' : 'no' },
        ],
        rows: all,
      });
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Excel export failed');
    } finally {
      setExporting(false);
    }
  };

  // Build filter-dropdown options from the full summary (not just current page)
  const docOptions = useMemo(() => {
    if (!summary?.by_document) return [];
    return summary.by_document
      .filter((d) => d.document_id)
      .map((d) => ({ id: d.document_id, name: d.document_name }));
  }, [summary]);

  const categoryOptions = useMemo(() => {
    if (!summary?.totals?.by_category) return [];
    return Object.keys(summary.totals.by_category)
      .filter((c) => c && c !== 'unknown')
      .sort();
  }, [summary]);

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2}>
        <Box>
          <Typography variant="h4">Compliance Matrix</Typography>
          <Typography variant="body2" color="text.secondary">
            NJ MVC T1628 — every requirement extracted from the solicitation documents.
            Triple-checked extraction with page citations.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={loadData}
            disabled={loading}
          >
            Refresh
          </Button>
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={exportCsv}
            disabled={exporting || totalCount === 0}
          >
            CSV
          </Button>
          <Button
            variant="contained"
            startIcon={<DownloadIcon />}
            onClick={exportExcel}
            disabled={exporting || totalCount === 0}
          >
            {exporting ? 'Exporting…' : 'Export Excel'}
          </Button>
        </Stack>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && <LinearProgress sx={{ mb: 2 }} />}

      {/* Summary cards */}
      {summary && (
        <Paper sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" spacing={3} flexWrap="wrap">
            <SummaryChip label="Total Requirements" value={summary.totals.total} color="#00AEE6" />
            <SummaryChip
              label="Critical"
              value={summary.totals.by_priority?.critical || 0}
              color={PRIORITY_COLORS.critical}
            />
            <SummaryChip
              label="High"
              value={summary.totals.by_priority?.high || 0}
              color={PRIORITY_COLORS.high}
            />
            <SummaryChip
              label="Mandatory"
              value={summary.totals.by_category?.mandatory || 0}
              color={CAT_COLORS.mandatory}
            />
            <SummaryChip
              label="Deadlines"
              value={summary.totals.by_category?.deadline || 0}
              color={CAT_COLORS.deadline}
            />
            <SummaryChip label="Verified" value={summary.totals.verified || 0} color="#2e7d32" />
          </Stack>

          <Divider sx={{ my: 2 }} />
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ mb: 1 }}
          >
            <Typography variant="subtitle2">Filter by Document</Typography>
            <Box sx={{ flex: 1 }} />
            {filterDoc && (
              <Button
                size="small"
                startIcon={<CloseIcon />}
                onClick={() => setFilterDoc('')}
              >
                Clear
              </Button>
            )}
          </Stack>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {summary.by_document.map((d) => {
              const active = filterDoc === String(d.document_id);
              const { Icon, color } = docIconFor(d.document_name);
              return (
                <Tooltip
                  key={d.document_id}
                  title={`${d.document_name} — ${d.total} requirements · ${d.by_priority?.critical || 0} critical · ${d.verified} verified`}
                  arrow
                >
                  <Chip
                    icon={<Icon sx={{ color: active ? 'inherit' : color }} />}
                    label={
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 600,
                            maxWidth: 260,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {d.document_name}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{
                            opacity: 0.8,
                            fontVariantNumeric: 'tabular-nums',
                          }}
                        >
                          {d.total}
                        </Typography>
                      </Stack>
                    }
                    onClick={() => setFilterDoc(active ? '' : String(d.document_id))}
                    color={active ? 'primary' : 'default'}
                    variant={active ? 'filled' : 'outlined'}
                    sx={{
                      height: 32,
                      borderColor: active ? undefined : color,
                      '& .MuiChip-label': { px: 1 },
                    }}
                  />
                </Tooltip>
              );
            })}
          </Stack>
        </Paper>
      )}

      {/* View toggle */}
      <Stack direction="row" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="subtitle2" color="text.secondary">
          View
        </Typography>
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
          documentId={filterDoc || undefined}
          emptyMessage="No requirements match the current filters."
        />
      ) : (
      <>
      {/* Filters */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
          <TextField
            label="Search"
            size="small"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            sx={{ minWidth: 240, flex: 1 }}
            placeholder="Search titles, sections, source text…"
          />
          <TextField
            select
            label="Document"
            size="small"
            value={filterDoc}
            onChange={(e) => setFilterDoc(e.target.value)}
            sx={{ minWidth: 200 }}
          >
            <MenuItem value="">All</MenuItem>
            {docOptions.map((d) => (
              <MenuItem key={d.id} value={String(d.id)}>
                {d.name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Category"
            size="small"
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            sx={{ minWidth: 160 }}
          >
            <MenuItem value="">All</MenuItem>
            {categoryOptions.map((c) => (
              <MenuItem key={c} value={c}>{c}</MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Priority"
            size="small"
            value={filterPriority}
            onChange={(e) => setFilterPriority(e.target.value)}
            sx={{ minWidth: 140 }}
          >
            <MenuItem value="">All</MenuItem>
            <MenuItem value="critical">Critical</MenuItem>
            <MenuItem value="high">High</MenuItem>
            <MenuItem value="medium">Medium</MenuItem>
            <MenuItem value="low">Low</MenuItem>
          </TextField>
          <TextField
            select
            label="Compliance"
            size="small"
            value={filterCompliance}
            onChange={(e) => setFilterCompliance(e.target.value)}
            sx={{ minWidth: 180 }}
          >
            <MenuItem value="">All</MenuItem>
            {COMPLIANCE_OPTIONS.map((o) => (
              <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Parsons coverage"
            size="small"
            value={filterParsonsCoverage}
            onChange={(e) => setFilterParsonsCoverage(e.target.value)}
            sx={{ minWidth: 180 }}
          >
            <MenuItem value="">All</MenuItem>
            <MenuItem value="covered">Covered</MenuItem>
            <MenuItem value="partial">Partial</MenuItem>
            <MenuItem value="gap">Gap</MenuItem>
            <MenuItem value="uncertain">Uncertain</MenuItem>
            <MenuItem value="not_assessed">Not assessed</MenuItem>
          </TextField>
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          Showing {filtered.length} of {requirements.length} requirements
        </Typography>
      </Paper>

      {/* Table */}
      <Paper>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              <TableCell width={32} />
              <TableCell>Section</TableCell>
              <TableCell>Requirement</TableCell>
              <TableCell>Category</TableCell>
              <TableCell>Priority</TableCell>
              <TableCell>Page</TableCell>
              <TableCell>Compliance</TableCell>
              <TableCell width={80} align="center">Verified</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {filtered.map((r) => {
              const expanded = expandedRows.has(r.id);
              const cs = r.compliance_status || 'not_reviewed';
              return (
                <React.Fragment key={r.id}>
                  <TableRow hover sx={{ verticalAlign: 'top' }}>
                    <TableCell>
                      <IconButton size="small" onClick={() => toggleRow(r.id)}>
                        {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                      </IconButton>
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      <Typography variant="caption" fontWeight={600}>
                        {r.section_id || '—'}
                      </Typography>
                      <Typography variant="caption" display="block" color="text.secondary">
                        {r.requirement_id}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ maxWidth: 480, overflow: 'hidden' }}>
                      <Typography variant="body2" fontWeight={600} sx={{ wordBreak: 'break-word' }}>
                        {r.title}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" noWrap
                                  sx={{ maxWidth: 420, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {r.document_name}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={r.category}
                        size="small"
                        sx={{
                          backgroundColor: CAT_COLORS[r.category] || '#9e9e9e',
                          color: 'white',
                          fontWeight: 600,
                          textTransform: 'capitalize',
                        }}
                      />
                    </TableCell>
                    <TableCell>
                      {r.priority && (
                        <Chip
                          label={r.priority}
                          size="small"
                          variant="outlined"
                          sx={{
                            borderColor: PRIORITY_COLORS[r.priority] || '#9e9e9e',
                            color: PRIORITY_COLORS[r.priority] || '#9e9e9e',
                            fontWeight: 600,
                            textTransform: 'capitalize',
                          }}
                        />
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption">p. {r.source_page || '?'}</Typography>
                    </TableCell>
                    <TableCell>
                      <TextField
                        select
                        size="small"
                        value={cs}
                        onChange={(e) => handleComplianceChange(r.id, e.target.value)}
                        sx={{ minWidth: 170 }}
                        SelectProps={{
                          renderValue: (v) => (
                            <Stack direction="row" spacing={0.5} alignItems="center">
                              {COMPLIANCE_ICON[v]}
                              <Typography variant="caption">
                                {COMPLIANCE_OPTIONS.find((o) => o.value === v)?.label || v}
                              </Typography>
                            </Stack>
                          ),
                        }}
                      >
                        {COMPLIANCE_OPTIONS.map((o) => (
                          <MenuItem key={o.value} value={o.value}>
                            <Stack direction="row" spacing={1} alignItems="center">
                              {COMPLIANCE_ICON[o.value]}
                              <Typography variant="body2">{o.label}</Typography>
                            </Stack>
                          </MenuItem>
                        ))}
                      </TextField>
                    </TableCell>
                    <TableCell align="center">
                      <Tooltip title={r.verified ? `Verified by ${r.verified_by || 'reviewer'}` : 'Mark verified'}>
                        <IconButton
                          size="small"
                          onClick={() => handleVerifiedToggle(r.id, r.verified)}
                        >
                          <CheckIcon
                            sx={{ color: r.verified ? '#2e7d32' : '#bdbdbd' }}
                          />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell colSpan={8} sx={{ p: 0, borderBottom: expanded ? undefined : 'none' }}>
                      <Collapse in={expanded} timeout="auto" unmountOnExit>
                        <Box sx={{ p: 2, backgroundColor: '#fafafa' }}>
                          <Stack spacing={1.5}>
                            {r.description && (
                              <Box>
                                <Typography variant="caption" color="text.secondary" fontWeight={600}>
                                  DESCRIPTION
                                </Typography>
                                <Typography variant="body2">{r.description}</Typography>
                              </Box>
                            )}
                            {r.source_text && (
                              <Box>
                                <Typography variant="caption" color="text.secondary" fontWeight={600}>
                                  VERBATIM SOURCE (p. {r.source_page || '?'})
                                </Typography>
                                <Box
                                  sx={{
                                    p: 1.5,
                                    backgroundColor: '#fff',
                                    borderLeft: '3px solid #00AEE6',
                                    fontFamily: 'Georgia, serif',
                                    fontSize: '0.9rem',
                                    whiteSpace: 'pre-wrap',
                                  }}
                                >
                                  {highlightMandatory(r.source_text)}
                                </Box>
                              </Box>
                            )}
                            <Stack direction="row" spacing={2} flexWrap="wrap">
                              <Chip
                                label={`Pass: ${r.extraction_pass || 'n/a'}`}
                                size="small"
                                variant="outlined"
                              />
                              <Chip
                                label={`Confidence: ${r.reviewer_confidence || 'n/a'}`}
                                size="small"
                                variant="outlined"
                              />
                              {r.notes && (
                                <Chip
                                  label={r.notes}
                                  size="small"
                                  variant="outlined"
                                  sx={{ maxWidth: 360 }}
                                />
                              )}
                            </Stack>
                            <TextField
                              label="Parsons Evidence / Response"
                              size="small"
                              multiline
                              minRows={2}
                              fullWidth
                              defaultValue={r.parsons_evidence || ''}
                              onBlur={(e) => {
                                if (e.target.value !== (r.parsons_evidence || '')) {
                                  handleEvidenceChange(r.id, e.target.value);
                                }
                              }}
                              placeholder="Cite the Parsons proposal section, form, or attachment that satisfies this requirement."
                            />
                          </Stack>
                        </Box>
                      </Collapse>
                    </TableCell>
                  </TableRow>
                </React.Fragment>
              );
            })}
            {!loading && filtered.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} align="center" sx={{ py: 4 }}>
                  <Typography variant="body2" color="text.secondary">
                    No requirements match the current filters.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        <TablePagination
          component="div"
          count={totalCount}
          page={page}
          onPageChange={(_, newPage) => setPage(newPage)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(e) => {
            setRowsPerPage(parseInt(e.target.value, 10));
            setPage(0);
          }}
          rowsPerPageOptions={[25, 50, 100, 250]}
          labelRowsPerPage="Rows / page"
        />
      </Paper>
      </>
      )}
    </Box>
  );
}

function SummaryChip({ label, value, color }) {
  return (
    <Stack alignItems="center" spacing={0.25} sx={{ minWidth: 120 }}>
      <Typography variant="h4" fontWeight={700} sx={{ color }}>
        {value.toLocaleString()}
      </Typography>
      <Typography variant="caption" color="text.secondary" textTransform="uppercase" letterSpacing={1}>
        {label}
      </Typography>
    </Stack>
  );
}
