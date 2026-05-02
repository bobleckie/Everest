import React, { useEffect, useMemo, useState, useCallback } from 'react';
import axios from 'axios';
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  TextField,
  MenuItem,
  Alert,
  LinearProgress,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Divider,
  IconButton,
  Tooltip,
  Button,
  TablePagination,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  CheckCircle as CheckIcon,
  Cancel as CancelIcon,
  HelpOutline as QuestionIcon,
  Warning as WarningIcon,
  UnfoldMore as UnfoldMoreIcon,
  UnfoldLess as UnfoldLessIcon,
} from '@mui/icons-material';
import CitationChips from './CitationChips';

/* --------------------------------------------------------------------------
 * RequirementsBySection
 *
 * Read-only accordion view of extracted RFP requirements grouped hierarchically
 * by section (and sub-grouped by category inside each section).
 *
 * Backed by GET /api/knowledge/requirements/by-section.
 *
 * Used by: ComplianceMatrix, RfpSetup, Review pages as an alternative view to
 * the flat table. No DB writes from this component (status / evidence edits
 * still happen from the flat table surface).
 * ------------------------------------------------------------------------ */

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
  unknown: '#9e9e9e',
};

const PRIORITY_COLORS = {
  critical: '#b71c1c',
  high: '#e65100',
  medium: '#0277bd',
  low: '#2e7d32',
};

const COMPLIANCE_ICON = {
  compliant: <CheckIcon sx={{ color: '#2e7d32', fontSize: 16 }} />,
  partial: <WarningIcon sx={{ color: '#f57c00', fontSize: 16 }} />,
  non_compliant: <CancelIcon sx={{ color: '#c62828', fontSize: 16 }} />,
  not_applicable: <QuestionIcon sx={{ color: '#9e9e9e', fontSize: 16 }} />,
  not_reviewed: <QuestionIcon sx={{ color: '#bdbdbd', fontSize: 16 }} />,
  not_assessed: <QuestionIcon sx={{ color: '#bdbdbd', fontSize: 16 }} />,
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
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
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

export default function RequirementsBySection({
  proposalId,
  documentId, // optional filter; undefined = all docs in the proposal
  initialSearch = '',
  emptyMessage = 'No requirements extracted for this proposal yet.',
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);

  const [filterCategory, setFilterCategory] = useState('');
  const [filterPriority, setFilterPriority] = useState('');
  const [filterCompliance, setFilterCompliance] = useState('');
  const [searchText, setSearchText] = useState(initialSearch);
  const [debouncedSearch, setDebouncedSearch] = useState(initialSearch);

  // Which section accordions are open. Default: closed to keep the initial
  // render fast for RFPs with thousands of requirements.
  const [expandedSections, setExpandedSections] = useState(() => new Set());
  const [expandAllToken, setExpandAllToken] = useState(0); // bump to force-open
  const [collapseAllToken, setCollapseAllToken] = useState(0);

  // Section-level pagination: with 60+ sections each containing 20+ reqs,
  // rendering all accordions at once can freeze the tab even when collapsed
  // because MUI still mounts their summary contents. Page through the
  // sections themselves.
  const [sectionPage, setSectionPage] = useState(0);
  const [sectionsPerPage, setSectionsPerPage] = useState(10);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchText.trim()), 300);
    return () => clearTimeout(t);
  }, [searchText]);

  // Whenever the underlying filter set changes, reset to page 0 so we always
  // show the top matching sections first.
  useEffect(() => {
    setSectionPage(0);
  }, [debouncedSearch, filterCategory, filterPriority, filterCompliance, documentId, proposalId]);

  const load = useCallback(async () => {
    if (proposalId == null) return;
    setLoading(true);
    setError(null);
    try {
      const params = { proposal_id: proposalId };
      if (documentId != null && documentId !== '') params.document_id = documentId;
      if (filterCategory) params.category = filterCategory;
      if (filterPriority) params.priority = filterPriority;
      if (filterCompliance) params.compliance_status = filterCompliance;
      if (debouncedSearch) params.search = debouncedSearch;
      const res = await axios.get('/api/knowledge/requirements/by-section', { params });
      setData(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load requirements');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [proposalId, documentId, filterCategory, filterPriority, filterCompliance, debouncedSearch]);

  useEffect(() => { load(); }, [load]);

  // Derive available filter options from the returned data so the
  // dropdowns only show categories/priorities that actually appear.
  const categoryOptions = useMemo(() => {
    if (!data?.sections) return [];
    const s = new Set();
    data.sections.forEach((sec) => Object.keys(sec.by_category || {}).forEach((c) => c && c !== 'unknown' && s.add(c)));
    return Array.from(s).sort();
  }, [data]);

  const priorityOptions = useMemo(() => {
    if (!data?.sections) return [];
    const s = new Set();
    data.sections.forEach((sec) => Object.keys(sec.by_priority || {}).forEach((p) => p && p !== 'unknown' && s.add(p)));
    return Array.from(s).sort();
  }, [data]);

  const toggleSection = (sid) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid); else next.add(sid);
      return next;
    });
  };

  const collapseAll = () => {
    setExpandedSections(new Set());
    setCollapseAllToken((x) => x + 1);
  };

  const sections = data?.sections || [];
  const totalSections = data?.total_sections || 0;
  const totalReqs = data?.total_requirements || 0;

  // Slice to the current page of sections.
  const pagedSections = useMemo(() => {
    if (!sections.length) return [];
    const start = sectionPage * sectionsPerPage;
    return sections.slice(start, start + sectionsPerPage);
  }, [sections, sectionPage, sectionsPerPage]);

  // Clamp sectionPage if the section set shrinks (e.g. after applying a filter).
  useEffect(() => {
    if (!sections.length) return;
    const maxPage = Math.max(0, Math.ceil(sections.length / sectionsPerPage) - 1);
    if (sectionPage > maxPage) setSectionPage(maxPage);
  }, [sections.length, sectionsPerPage, sectionPage]);

  // Only expand/collapse-all on the currently-visible page.
  const expandAllOnPage = () => {
    if (!pagedSections.length) return;
    setExpandedSections(new Set(pagedSections.map((s) => s.section_id)));
    setExpandAllToken((x) => x + 1);
  };

  return (
    <Box>
      {/* Filter bar */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
          <TextField
            label="Search"
            size="small"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            sx={{ minWidth: 220, flex: 1 }}
            placeholder="Search titles, sections, source text…"
          />
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
            {priorityOptions.map((p) => (
              <MenuItem key={p} value={p}>{p}</MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Compliance"
            size="small"
            value={filterCompliance}
            onChange={(e) => setFilterCompliance(e.target.value)}
            sx={{ minWidth: 170 }}
          >
            <MenuItem value="">All</MenuItem>
            <MenuItem value="not_reviewed">Not Reviewed</MenuItem>
            <MenuItem value="not_assessed">Not Assessed</MenuItem>
            <MenuItem value="compliant">Compliant</MenuItem>
            <MenuItem value="partial">Partial</MenuItem>
            <MenuItem value="non_compliant">Non-Compliant</MenuItem>
            <MenuItem value="not_applicable">N/A</MenuItem>
          </TextField>
        </Stack>
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary">
            {totalSections.toLocaleString()} sections · {totalReqs.toLocaleString()} requirements
            {totalSections > sectionsPerPage && (
              <> · showing {pagedSections.length} on this page</>
            )}
          </Typography>
          <Box sx={{ flex: 1 }} />
          <Button size="small" startIcon={<UnfoldMoreIcon />} onClick={expandAllOnPage} disabled={!pagedSections.length}>
            Expand page
          </Button>
          <Button size="small" startIcon={<UnfoldLessIcon />} onClick={collapseAll} disabled={!totalSections}>
            Collapse all
          </Button>
        </Stack>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && <LinearProgress sx={{ mb: 2 }} />}

      {!loading && totalReqs === 0 && (
        <Alert severity="info">{emptyMessage}</Alert>
      )}

      {/* Section accordions (paginated) */}
      {pagedSections.map((sec) => {
        const isOpen = expandedSections.has(sec.section_id);
        const docSummary = sec.document_names?.length === 1
          ? sec.document_names[0]
          : `${sec.document_names?.length || 0} documents`;
        return (
          <Accordion
            key={`${sec.section_id}-${expandAllToken}-${collapseAllToken}`}
            expanded={isOpen}
            onChange={() => toggleSection(sec.section_id)}
            TransitionProps={{ unmountOnExit: true }}
            sx={{ '&:before': { display: 'none' }, mb: 0.5 }}
          >
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Stack direction="row" spacing={2} alignItems="center" sx={{ width: '100%' }}>
                <Typography variant="subtitle1" fontWeight={700} sx={{ minWidth: 140 }}>
                  {sec.section_id}
                </Typography>
                <Chip
                  label={`${sec.total} req${sec.total === 1 ? '' : 's'}`}
                  size="small"
                  sx={{ fontWeight: 600 }}
                />
                {Object.entries(sec.by_category || {})
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 4)
                  .map(([cat, count]) => (
                    <Chip
                      key={cat}
                      label={`${cat}: ${count}`}
                      size="small"
                      variant="outlined"
                      sx={{
                        borderColor: CAT_COLORS[cat] || '#9e9e9e',
                        color: CAT_COLORS[cat] || '#9e9e9e',
                        fontWeight: 600,
                      }}
                    />
                  ))}
                <Box sx={{ flex: 1 }} />
                {sec.page_range && (
                  <Typography variant="caption" color="text.secondary">
                    p. {sec.page_range}
                  </Typography>
                )}
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  title={(sec.document_names || []).join(' · ')}
                >
                  {docSummary}
                </Typography>
              </Stack>
            </AccordionSummary>
            <AccordionDetails sx={{ backgroundColor: '#fafafa', pt: 0 }}>
              {(sec.categories || []).map((grp, gi) => (
                <Box key={grp.category} sx={{ mt: gi === 0 ? 1 : 2 }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                    <Chip
                      label={grp.category}
                      size="small"
                      sx={{
                        backgroundColor: CAT_COLORS[grp.category] || '#9e9e9e',
                        color: 'white',
                        fontWeight: 700,
                        textTransform: 'capitalize',
                      }}
                    />
                    <Typography variant="caption" color="text.secondary">
                      {grp.count} in this category
                    </Typography>
                  </Stack>
                  <Stack spacing={1}>
                    {grp.requirements.map((r) => <RequirementRow key={r.id} req={r} />)}
                  </Stack>
                  {gi < (sec.categories || []).length - 1 && <Divider sx={{ mt: 2 }} />}
                </Box>
              ))}
            </AccordionDetails>
          </Accordion>
        );
      })}

      {/* Section pagination footer */}
      {totalSections > 0 && (
        <Paper sx={{ mt: 1 }}>
          <TablePagination
            component="div"
            count={sections.length}
            page={sectionPage}
            onPageChange={(_, p) => {
              setSectionPage(p);
              // Scroll back to the top of the list so the user sees the new page.
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
            rowsPerPage={sectionsPerPage}
            onRowsPerPageChange={(e) => {
              setSectionsPerPage(parseInt(e.target.value, 10));
              setSectionPage(0);
            }}
            rowsPerPageOptions={[5, 10, 20, 50]}
            labelRowsPerPage="Sections per page:"
            labelDisplayedRows={({ from, to, count }) =>
              `Sections ${from}–${to} of ${count}`
            }
          />
        </Paper>
      )}
    </Box>
  );
}

function RequirementRow({ req }) {
  const cs = req.compliance_status || 'not_reviewed';
  return (
    <Paper variant="outlined" sx={{ p: 1.5, backgroundColor: '#fff' }}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Box sx={{ minWidth: 96 }}>
          <Typography variant="caption" fontWeight={700} sx={{ fontFamily: 'monospace' }}>
            {req.requirement_id}
          </Typography>
          {req.source_page && (
            <Typography variant="caption" display="block" color="text.secondary">
              p. {req.source_page}
            </Typography>
          )}
          {req.priority && (
            <Chip
              label={req.priority}
              size="small"
              variant="outlined"
              sx={{
                mt: 0.5,
                height: 18,
                fontSize: 10,
                borderColor: PRIORITY_COLORS[req.priority] || '#9e9e9e',
                color: PRIORITY_COLORS[req.priority] || '#9e9e9e',
                fontWeight: 600,
                textTransform: 'capitalize',
              }}
            />
          )}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" fontWeight={600}>
            {req.title}
          </Typography>
          {req.description && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>
              {req.description}
            </Typography>
          )}
          {req.source_text && (
            <Box
              sx={{
                mt: 0.75,
                p: 1,
                backgroundColor: '#f5f7fa',
                borderLeft: '3px solid #00AEE6',
                fontFamily: 'Georgia, serif',
                fontSize: '0.85rem',
                whiteSpace: 'pre-wrap',
              }}
            >
              {highlightMandatory(req.source_text)}
            </Box>
          )}
          <CitationChips
            requirementId={req.id}
            text={[req.description, req.source_text]}
          />
          {req.document_name && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
              {req.document_name}
            </Typography>
          )}
        </Box>
        <Stack alignItems="center" spacing={0.5} sx={{ minWidth: 90 }}>
          <Tooltip title={cs.replace(/_/g, ' ')}>
            <Box>{COMPLIANCE_ICON[cs] || COMPLIANCE_ICON.not_reviewed}</Box>
          </Tooltip>
          {req.verified && (
            <Tooltip title={`Verified by ${req.verified_by || 'reviewer'}`}>
              <IconButton size="small" disabled>
                <CheckIcon sx={{ color: '#2e7d32', fontSize: 18 }} />
              </IconButton>
            </Tooltip>
          )}
        </Stack>
      </Stack>
    </Paper>
  );
}
