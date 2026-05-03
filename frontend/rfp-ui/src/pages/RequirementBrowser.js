import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import axios from 'axios';
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  TextField,
  Alert,
  CircularProgress,
  Collapse,
  Tooltip,
  Button,
  TablePagination,
  ToggleButton,
  ToggleButtonGroup,
  Autocomplete,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Description as DocIcon,
  Folder as ThemeIcon,
  ChevronRight as ChevronRightIcon,
  Link as LinkIcon,
  TableRows as TableIcon,
  HelpOutline as RefIcon,
  MenuBook as GlossaryIcon,
} from '@mui/icons-material';
import { useProposal } from '../proposal/ProposalContext';

const PRIORITY_COLOR = {
  critical: 'error',
  high: 'warning',
  medium: 'info',
  low: 'default',
};

const COMPLIANCE_COLOR = {
  compliant: 'success',
  non_compliant: 'error',
  partial: 'warning',
  not_assessed: 'default',
};

const PAGE_SIZE_DEFAULT = 25;

export default function RequirementBrowser() {
  const { proposalId } = useProposal();
  const [tree, setTree] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [openThemes, setOpenThemes] = useState({});
  const [openDocs, setOpenDocs] = useState({});
  const [openSections, setOpenSections] = useState({});
  const [docPages, setDocPages] = useState({});      // docKey -> page index
  const [docPageSize, setDocPageSize] = useState(25); // sections per page per doc

  // Filters
  const [categoryFilter, setCategoryFilter] = useState('');           // '' = all
  const [selectedDocs, setSelectedDocs] = useState([]);                // [] = all
  const [requirementKind, setRequirementKind] = useState('obligation');
  const [responseEffort, setResponseEffort] = useState('writeup');
  const [procurementScope, setProcurementScope] = useState('current_2026');
  const [filterOptions, setFilterOptions] = useState({ categories: [], documents: [], kinds: [], efforts: [], scopes: [] });

  // Per-section lazy-loaded requirement caches:
  // sectionDataByKey[`${doc_id}::${section_id}`] = { rows, total, offset, limit, loading, error }
  const [sectionDataByKey, setSectionDataByKey] = useState({});

  // Pull filter option lists once per proposal / scope change
  useEffect(() => {
    const params = { procurement_scope: procurementScope };
    if (proposalId) params.proposal_id = proposalId;
    axios
      .get('/api/knowledge/requirements/filters', { params })
      .then((res) => setFilterOptions(res.data))
      .catch(() => {});
  }, [proposalId, procurementScope]);

  // Reload tree when any filter changes.
  useEffect(() => {
    setLoading(true);
    const params = {
      summary: true,
      requirement_kind: requirementKind,
      response_effort: responseEffort,
      procurement_scope: procurementScope,
    };
    if (proposalId) params.proposal_id = proposalId;
    if (categoryFilter) params.category = categoryFilter;
    if (selectedDocs.length) params.document_ids = selectedDocs.map((d) => d.document_id).join(',');
    axios
      .get('/api/knowledge/requirements/tree', { params })
      .then((res) => {
        setTree(res.data);
        setError(null);
        // Reset open-state when filters change (otherwise old tree's keys leak)
        setOpenThemes({}); setOpenDocs({}); setOpenSections({}); setDocPages({});
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || 'Failed to load tree');
      })
      .finally(() => setLoading(false));
  }, [proposalId, categoryFilter, selectedDocs, requirementKind, responseEffort, procurementScope]);

  const sectionKey = (themeId, docId, sectionId) => `${themeId}::${docId}::${sectionId}`;

  const fetchSectionRequirements = useCallback(
    async (themeId, docId, sectionId, offset, limit) => {
      const key = sectionKey(themeId, docId, sectionId);
      setSectionDataByKey((prev) => ({
        ...prev,
        [key]: { ...(prev[key] || {}), loading: true, error: null },
      }));
      try {
        const res = await axios.get('/api/knowledge/requirements/in-section', {
          params: {
            ...(proposalId ? { proposal_id: proposalId } : {}),
            theme_id: themeId,
            document_id: docId,
            section_id: sectionId,
            offset,
            limit,
            ...(search.trim() ? { search: search.trim() } : {}),
          },
        });
        setSectionDataByKey((prev) => ({
          ...prev,
          [key]: {
            rows: res.data.requirements,
            total: res.data.total,
            offset: res.data.offset,
            limit: res.data.limit,
            loading: false,
            error: null,
          },
        }));
      } catch (err) {
        setSectionDataByKey((prev) => ({
          ...prev,
          [key]: {
            ...(prev[key] || {}),
            loading: false,
            error: err?.response?.data?.detail || err.message || 'Failed to load',
          },
        }));
      }
    },
    [proposalId, search],
  );

  const toggleTheme = (id) => setOpenThemes((s) => ({ ...s, [id]: !s[id] }));
  const toggleDoc = (id) => setOpenDocs((s) => ({ ...s, [id]: !s[id] }));
  const toggleSection = (themeId, docId, sectionId) => {
    const key = sectionKey(themeId, docId, sectionId);
    setOpenSections((s) => {
      const newOpen = !s[key];
      if (newOpen && !sectionDataByKey[key]) {
        fetchSectionRequirements(themeId, docId, sectionId, 0, PAGE_SIZE_DEFAULT);
      }
      return { ...s, [key]: newOpen };
    });
  };

  // Re-fetch open sections whenever search changes (debounced)
  useEffect(() => {
    const handle = setTimeout(() => {
      Object.keys(openSections).forEach((key) => {
        if (!openSections[key]) return;
        const [themeId, docId, sectionId] = key.split('::');
        fetchSectionRequirements(Number(themeId), Number(docId), sectionId, 0, PAGE_SIZE_DEFAULT);
      });
    }, 350);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  if (loading) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (error) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{error}</Alert>
      </Box>
    );
  }

  const data = tree;
  if (!data) return null;

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h5">Requirement Browser</Typography>
        <Chip label={`${data.total_requirements.toLocaleString()} requirements`} color="primary" variant="outlined" />
      </Stack>

      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ mb: 2 }}>
        {/* Procurement-scope toggle — primary scoping (live RFP vs prior cycle) */}
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>RFP Scope</Typography>
          <ToggleButtonGroup
            size="small"
            value={procurementScope}
            exclusive
            onChange={(_, v) => v && setProcurementScope(v)}
          >
            <ToggleButton value="current_2026">
              Live 2026 RFP
              {filterOptions.scopes?.find((s) => s.scope === 'current_2026') ? (
                <Chip size="small" label={filterOptions.scopes.find((s) => s.scope === 'current_2026').n_writeups} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="archive_2021_compare">
              2021 Archive
              {filterOptions.scopes?.find((s) => s.scope === 'archive_2021_compare') ? (
                <Chip size="small" label={filterOptions.scopes.find((s) => s.scope === 'archive_2021_compare').n_writeups} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="all">All</ToggleButton>
          </ToggleButtonGroup>
        </Box>

        {/* Response-effort toggle (writer-facing) */}
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>Response Effort</Typography>
          <ToggleButtonGroup
            size="small"
            value={responseEffort}
            exclusive
            onChange={(_, v) => v && setResponseEffort(v)}
          >
            <ToggleButton value="writeup">
              Writeup
              {filterOptions.efforts?.find((e) => e.effort === 'writeup') ? (
                <Chip size="small" label={filterOptions.efforts.find((e) => e.effort === 'writeup').n_requirements} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="attestation">
              Attest
              {filterOptions.efforts?.find((e) => e.effort === 'attestation') ? (
                <Chip size="small" label={filterOptions.efforts.find((e) => e.effort === 'attestation').n_requirements} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="info">
              Info
              {filterOptions.efforts?.find((e) => e.effort === 'info') ? (
                <Chip size="small" label={filterOptions.efforts.find((e) => e.effort === 'info').n_requirements} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="all">All</ToggleButton>
          </ToggleButtonGroup>
        </Box>

        {/* Row-kind toggle */}
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>View</Typography>
          <ToggleButtonGroup
            size="small"
            value={requirementKind}
            exclusive
            onChange={(_, v) => v && setRequirementKind(v)}
          >
            <ToggleButton value="obligation">
              Obligations
              {filterOptions.kinds.find((k) => k.kind === 'obligation') ? (
                <Chip size="small" label={filterOptions.kinds.find((k) => k.kind === 'obligation').n_requirements} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="checklist_item">
              Checklist
              {filterOptions.kinds.find((k) => k.kind === 'checklist_item') ? (
                <Chip size="small" label={filterOptions.kinds.find((k) => k.kind === 'checklist_item').n_requirements} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="data_element_spec">
              Specs
              {filterOptions.kinds.find((k) => k.kind === 'data_element_spec') ? (
                <Chip size="small" label={filterOptions.kinds.find((k) => k.kind === 'data_element_spec').n_requirements} sx={{ ml: 0.5 }} />
              ) : null}
            </ToggleButton>
            <ToggleButton value="all">All</ToggleButton>
          </ToggleButtonGroup>
        </Box>

        {/* Category toggle */}
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>Category</Typography>
          <ToggleButtonGroup
            size="small"
            value={categoryFilter}
            exclusive
            onChange={(_, v) => setCategoryFilter(v || '')}
            sx={{ display: 'flex', flexWrap: 'wrap' }}
          >
            <ToggleButton value="">All</ToggleButton>
            {filterOptions.categories.map((c) => (
              <ToggleButton key={c.category} value={c.category}>
                {c.category} <Chip size="small" label={c.n_requirements} sx={{ ml: 0.5 }} />
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        {/* Document multi-select */}
        <Box sx={{ flex: 1, minWidth: 280 }}>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>Documents</Typography>
          <Autocomplete
            multiple
            size="small"
            options={filterOptions.documents}
            getOptionLabel={(opt) =>
              `${opt.document_name || `Doc #${opt.document_id}`}  (${opt.n_requirements})`
            }
            isOptionEqualToValue={(a, b) => a.document_id === b.document_id}
            value={selectedDocs}
            onChange={(_, newValue) => setSelectedDocs(newValue)}
            renderInput={(params) => (
              <TextField {...params} placeholder="All documents" />
            )}
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip
                  size="small"
                  variant="outlined"
                  label={(option.document_name || `Doc #${option.document_id}`).slice(0, 32)}
                  {...getTagProps({ index })}
                  key={option.document_id}
                />
              ))
            }
          />
        </Box>
      </Stack>

      <TextField
        fullWidth
        size="small"
        placeholder="Filter by title (re-applies to open sections)…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        sx={{ mb: 2 }}
      />

      {data.themes.map((theme) => {
        const themeOpen = !!openThemes[theme.theme_id]; // default closed
        return (
          <Paper key={theme.theme_id} elevation={1} sx={{ mb: 1.5 }}>
            <Box
              onClick={() => toggleTheme(theme.theme_id)}
              sx={{
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                p: 1.5,
                bgcolor: themeOpen ? 'primary.main' : 'grey.100',
                color: themeOpen ? 'primary.contrastText' : 'text.primary',
                borderRadius: 1,
              }}
            >
              <ThemeIcon sx={{ mr: 1 }} />
              <Typography variant="subtitle1" sx={{ fontWeight: 700, flex: 1 }}>
                {theme.theme_label}
              </Typography>
              {theme.category ? (
                <Chip
                  size="small"
                  label={theme.category}
                  variant="outlined"
                  sx={{ mr: 1, bgcolor: 'background.paper', borderColor: 'background.paper' }}
                />
              ) : null}
              <Chip size="small" label={`${theme.n_requirements} reqs`} sx={{ mr: 1, bgcolor: 'background.paper' }} />
              {themeOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </Box>
            <Collapse in={themeOpen} unmountOnExit>
              <Box sx={{ pl: 1.5, pr: 1, pt: 1, pb: 1 }}>
                {theme.documents.map((doc) => {
                  const docKey = `${theme.theme_id}::${doc.document_id}`;
                  const docOpen = !!openDocs[docKey]; // default closed
                  const docPage = docPages[docKey] || 0;
                  const totalSecs = doc.sections.length;
                  const pageStart = docPage * docPageSize;
                  const visibleSections = doc.sections.slice(pageStart, pageStart + docPageSize);
                  return (
                    <Box key={docKey} sx={{ mb: 1 }}>
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={1}
                        onClick={() => toggleDoc(docKey)}
                        sx={{ cursor: 'pointer', py: 0.5 }}
                      >
                        <DocIcon fontSize="small" />
                        <Typography variant="body2" sx={{ fontWeight: 600, flex: 1 }}>
                          {doc.document_name || `Document #${doc.document_id}`}
                        </Typography>
                        <Chip size="small" variant="outlined" label={`${doc.n_requirements} reqs · ${totalSecs} sections`} />
                        {docOpen ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
                      </Stack>
                      <Collapse in={docOpen} unmountOnExit>
                        <Box sx={{ pl: 3 }}>
                          {visibleSections.map((sec) => {
                            const secKey = sectionKey(theme.theme_id, doc.document_id, sec.section_id);
                            const secOpen = !!openSections[secKey];
                            const secData = sectionDataByKey[secKey];
                            return (
                              <Box key={secKey} sx={{ mb: 0.5 }}>
                                <Stack
                                  direction="row"
                                  alignItems="center"
                                  spacing={1}
                                  onClick={() => toggleSection(theme.theme_id, doc.document_id, sec.section_id)}
                                  sx={{
                                    cursor: 'pointer',
                                    py: 0.4,
                                    px: 0.5,
                                    bgcolor: secOpen ? 'action.selected' : 'transparent',
                                    borderRadius: 1,
                                  }}
                                >
                                  <ChevronRightIcon
                                    fontSize="small"
                                    sx={{
                                      transform: secOpen ? 'rotate(90deg)' : 'none',
                                      transition: 'transform 120ms',
                                    }}
                                  />
                                  <Typography variant="body2" sx={{ flex: 1 }}>
                                    <strong>{sec.section_id}</strong>
                                    {sec.breadcrumb ? (
                                      <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                                        {sec.breadcrumb}
                                      </Typography>
                                    ) : null}
                                  </Typography>
                                  <Chip size="small" variant="outlined" label={sec.n_requirements} />
                                </Stack>
                                <Collapse in={secOpen} unmountOnExit>
                                  <Box sx={{ pl: 4, py: 0.5 }}>
                                    {!secData || secData.loading ? (
                                      <Box sx={{ p: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
                                        <CircularProgress size={14} />
                                        <Typography variant="caption" color="text.secondary">Loading…</Typography>
                                      </Box>
                                    ) : secData.error ? (
                                      <Alert severity="error" sx={{ my: 0.5 }}>{secData.error}</Alert>
                                    ) : secData.rows.length === 0 ? (
                                      <Typography variant="caption" color="text.secondary">
                                        No matching requirements (filter active).
                                      </Typography>
                                    ) : (
                                      <>
                                        {secData.rows.map((req) => (
                                          <RequirementRow key={req.requirement_id} req={req} proposalId={proposalId} />
                                        ))}
                                        {secData.total > secData.limit ? (
                                          <TablePagination
                                            component="div"
                                            count={secData.total}
                                            page={Math.floor(secData.offset / secData.limit)}
                                            onPageChange={(_, newPage) =>
                                              fetchSectionRequirements(
                                                theme.theme_id, doc.document_id, sec.section_id,
                                                newPage * secData.limit, secData.limit,
                                              )
                                            }
                                            rowsPerPage={secData.limit}
                                            onRowsPerPageChange={(e) =>
                                              fetchSectionRequirements(
                                                theme.theme_id, doc.document_id, sec.section_id,
                                                0, parseInt(e.target.value, 10),
                                              )
                                            }
                                            rowsPerPageOptions={[10, 25, 50, 100]}
                                            sx={{ '.MuiTablePagination-toolbar': { minHeight: 32, py: 0 } }}
                                          />
                                        ) : null}
                                      </>
                                    )}
                                  </Box>
                                </Collapse>
                              </Box>
                            );
                          })}
                          {totalSecs > docPageSize ? (
                            <TablePagination
                              component="div"
                              count={totalSecs}
                              page={docPage}
                              onPageChange={(_, newPage) =>
                                setDocPages((s) => ({ ...s, [docKey]: newPage }))
                              }
                              rowsPerPage={docPageSize}
                              onRowsPerPageChange={(e) => {
                                setDocPageSize(parseInt(e.target.value, 10));
                                setDocPages((s) => ({ ...s, [docKey]: 0 }));
                              }}
                              rowsPerPageOptions={[10, 25, 50, 100]}
                              labelRowsPerPage="Sections per page:"
                              sx={{ '.MuiTablePagination-toolbar': { minHeight: 36, py: 0 } }}
                            />
                          ) : null}
                        </Box>
                      </Collapse>
                    </Box>
                  );
                })}
              </Box>
            </Collapse>
          </Paper>
        );
      })}
    </Box>
  );
}

function RequirementRow({ req, proposalId }) {
  const detailUrl = proposalId
    ? `/p/${proposalId}/requirement/${req.requirement_id}`
    : `/requirement/${req.requirement_id}`;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        py: 0.6,
        px: 1,
        borderBottom: '1px solid',
        borderColor: 'divider',
        '&:hover': { bgcolor: 'action.hover' },
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          component={RouterLink}
          to={detailUrl}
          variant="body2"
          sx={{
            color: 'primary.main',
            textDecoration: 'none',
            fontWeight: 500,
            '&:hover': { textDecoration: 'underline' },
            display: 'block',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {req.title}
        </Typography>
      </Box>
      {req.priority ? (
        <Chip size="small" label={req.priority} color={PRIORITY_COLOR[req.priority] || 'default'} />
      ) : null}
      {req.compliance_status && req.compliance_status !== 'not_assessed' ? (
        <Chip size="small" label={req.compliance_status} color={COMPLIANCE_COLOR[req.compliance_status] || 'default'} />
      ) : null}
      <Stack direction="row" spacing={0.5}>
        {req.n_children ? (
          <Tooltip title={`${req.n_children} sub-parts rolled up under this obligation`}>
            <Chip size="small" color="secondary" label={`+${req.n_children}`} />
          </Tooltip>
        ) : null}
        {req.n_resolved_refs ? (
          <Tooltip title={`${req.n_resolved_refs} resolved cross-references`}>
            <Chip size="small" icon={<LinkIcon />} label={req.n_resolved_refs} variant="outlined" />
          </Tooltip>
        ) : null}
        {req.n_glossary ? (
          <Tooltip title={`${req.n_glossary} glossary terms appear here`}>
            <Chip size="small" icon={<GlossaryIcon />} label={req.n_glossary} variant="outlined" />
          </Tooltip>
        ) : null}
        {req.n_tables ? (
          <Tooltip title={`${req.n_tables} tables in this section`}>
            <Chip size="small" icon={<TableIcon />} label={req.n_tables} variant="outlined" />
          </Tooltip>
        ) : null}
        {req.n_reverse_refs ? (
          <Tooltip title={`${req.n_reverse_refs} other requirements point at this one`}>
            <Chip size="small" icon={<RefIcon />} label={req.n_reverse_refs} variant="outlined" />
          </Tooltip>
        ) : null}
      </Stack>
    </Stack>
  );
}
