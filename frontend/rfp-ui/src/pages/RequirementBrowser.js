import React, { useEffect, useMemo, useState } from 'react';
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
  IconButton,
  Tooltip,
  Divider,
  Button,
  ToggleButton,
  ToggleButtonGroup,
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

export default function RequirementBrowser() {
  const { proposalId } = useProposal();
  const [tree, setTree] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [openThemes, setOpenThemes] = useState({});
  const [openDocs, setOpenDocs] = useState({});
  const [openSections, setOpenSections] = useState({});

  useEffect(() => {
    setLoading(true);
    axios
      .get('/api/knowledge/requirements/tree', {
        params: proposalId ? { proposal_id: proposalId } : {},
      })
      .then((res) => {
        setTree(res.data);
        setError(null);
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || 'Failed to load tree');
      })
      .finally(() => setLoading(false));
  }, [proposalId]);

  const filtered = useMemo(() => {
    if (!tree || !search.trim()) return tree;
    const q = search.toLowerCase();
    const themes = tree.themes
      .map((t) => {
        const docs = t.documents
          .map((d) => {
            const sections = d.sections
              .map((s) => {
                const reqs = s.requirements.filter((r) =>
                  (r.title || '').toLowerCase().includes(q),
                );
                return reqs.length ? { ...s, requirements: reqs } : null;
              })
              .filter(Boolean);
            return sections.length ? { ...d, sections } : null;
          })
          .filter(Boolean);
        return docs.length ? { ...t, documents: docs } : null;
      })
      .filter(Boolean);
    return { themes, total_requirements: themes.reduce(
      (acc, t) => acc + t.documents.reduce(
        (a, d) => a + d.sections.reduce((b, s) => b + s.requirements.length, 0), 0), 0) };
  }, [tree, search]);

  const toggleTheme = (id) => setOpenThemes((s) => ({ ...s, [id]: !s[id] }));
  const toggleDoc = (id) => setOpenDocs((s) => ({ ...s, [id]: !s[id] }));
  const toggleSection = (k) => setOpenSections((s) => ({ ...s, [k]: !s[k] }));

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

  const data = filtered || tree;

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h5">Requirement Browser</Typography>
        <Chip label={`${data.total_requirements.toLocaleString()} requirements`} color="primary" variant="outlined" />
      </Stack>

      <TextField
        fullWidth
        size="small"
        placeholder="Filter by title…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        sx={{ mb: 2 }}
      />

      {data.themes.map((theme) => {
        const themeOpen = openThemes[theme.theme_id] !== false; // default open
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
              <Chip size="small" label={`${theme.n_requirements} reqs`} sx={{ mr: 1, bgcolor: 'background.paper' }} />
              {themeOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </Box>
            <Collapse in={themeOpen}>
              <Box sx={{ pl: 1.5, pr: 1, pt: 1, pb: 1 }}>
                {theme.documents.map((doc) => {
                  const docKey = `${theme.theme_id}::${doc.document_id}`;
                  const docOpen = openDocs[docKey] !== false;
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
                        <Chip size="small" variant="outlined" label={`${doc.n_requirements}`} />
                        {docOpen ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
                      </Stack>
                      <Collapse in={docOpen}>
                        <Box sx={{ pl: 3 }}>
                          {doc.sections.map((sec) => {
                            const secKey = `${docKey}::${sec.section_id}`;
                            const secOpen = !!openSections[secKey];
                            return (
                              <Box key={secKey} sx={{ mb: 0.5 }}>
                                <Stack
                                  direction="row"
                                  alignItems="center"
                                  spacing={1}
                                  onClick={() => toggleSection(secKey)}
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
                                  <Chip size="small" variant="outlined" label={sec.requirements.length} />
                                </Stack>
                                <Collapse in={secOpen}>
                                  <Box sx={{ pl: 4, py: 0.5 }}>
                                    {sec.requirements.map((req) => (
                                      <RequirementRow key={req.requirement_id} req={req} proposalId={proposalId} />
                                    ))}
                                  </Box>
                                </Collapse>
                              </Box>
                            );
                          })}
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
