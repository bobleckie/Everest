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
  Divider,
  Button,
  IconButton,
  Tooltip,
  Tabs,
  Tab,
  List,
  ListItemButton,
  ListItemText,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  FormControlLabel,
  Switch,
  CircularProgress,
} from '@mui/material';
import {
  Refresh as RefreshIcon,
  AutoAwesome as AiIcon,
  PlayArrow as RunIcon,
  Save as SaveIcon,
  CheckCircle as CheckIcon,
  Cancel as CancelIcon,
  Edit as EditIcon,
  Gavel as RubricIcon,
  FileDownload as DownloadIcon,
} from '@mui/icons-material';
import { Menu } from '@mui/material';

/* --------------------------------------------------------------------------
 * ResponseWorkbench
 *
 * Single consolidated surface for authoring RFP responses at the EXTRACTED-
 * SECTION granularity. One conceptual "section of work" = one extracted RFP
 * section_id (e.g. "7.1", "3.13.8.1").
 *
 * Left pane: section tree (GET /api/workbench/sections).
 * Right pane: selected section detail with tabs:
 *   - Parsons draft (editable)
 *   - Competitor responses (generate / score)
 *   - Scoring (Parsons + each competitor against the rubric rollup)
 *   - Cure suggestions (generate / approve / deny / apply)
 *
 * Feature-flagged via localStorage key 'responseWorkbench.enabled' — set it
 * to 'true' in the browser devtools to enable the nav link.
 * ------------------------------------------------------------------------ */

const DEFAULT_PROPOSAL_ID = 1;

const COMPLIANCE_TAGS = [
  'Comply',
  'Comply-with-exception',
  'Take-exception',
  'Not-Applicable',
  'Needs-Clarification',
];

const STATUS_COLORS = {
  draft: 'default',
  in_review: 'warning',
  approved: 'success',
  exported: 'info',
};

function fmtPct(n, total) {
  if (!total) return '—';
  return `${Math.round((n / total) * 100)}%`;
}

export default function ResponseWorkbench() {
  const [proposalId] = useState(DEFAULT_PROPOSAL_ID);
  const [sectionsData, setSectionsData] = useState(null);
  const [loadingSections, setLoadingSections] = useState(false);
  const [sectionsError, setSectionsError] = useState(null);
  const [includeInformational, setIncludeInformational] = useState(false);
  const [filter, setFilter] = useState('');
  const [selectedSectionId, setSelectedSectionId] = useState(null);

  // Detail pane state
  const [detail, setDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState(null);
  const [tab, setTab] = useState(0);

  // Editors
  const [parsonsDraft, setParsonsDraft] = useState('');
  const [parsonsStatus, setParsonsStatus] = useState('draft');
  const [complianceTagsDraft, setComplianceTagsDraft] = useState({}); // {reqId: tag}
  const [savingParsons, setSavingParsons] = useState(false);

  // Competitors
  const [competitors, setCompetitors] = useState([]);
  const [selectedCompetitorId, setSelectedCompetitorId] = useState('');
  const [generatingCompetitor, setGeneratingCompetitor] = useState(false);

  // Scoring
  const [scoring, setScoring] = useState(false);

  // Cure
  const [cureDialogOpen, setCureDialogOpen] = useState(false);
  const [cureCompetitorId, setCureCompetitorId] = useState('');
  const [cureIncludeRewrite, setCureIncludeRewrite] = useState(false);
  const [cureRunning, setCureRunning] = useState(false);
  const [rewriteDialogOpen, setRewriteDialogOpen] = useState(false);
  const [rewriteCompetitorId, setRewriteCompetitorId] = useState('');
  const [rewriteInstructions, setRewriteInstructions] = useState('');
  const [rewriteRunning, setRewriteRunning] = useState(false);

  const [snackbar, setSnackbar] = useState(null); // {severity, message}
  const [exportAnchor, setExportAnchor] = useState(null);
  const [exporting, setExporting] = useState(false);

  // ── Loaders ───────────────────────────────────────────────────────
  const loadSections = useCallback(async () => {
    setLoadingSections(true);
    setSectionsError(null);
    try {
      const res = await axios.get('/api/workbench/sections', {
        params: { proposal_id: proposalId, include_informational: includeInformational },
      });
      setSectionsData(res.data);
    } catch (e) {
      setSectionsError(e?.response?.data?.detail || e.message);
    } finally {
      setLoadingSections(false);
    }
  }, [proposalId, includeInformational]);

  const loadDetail = useCallback(async (sid) => {
    if (!sid) return;
    setLoadingDetail(true);
    setDetailError(null);
    try {
      const res = await axios.get(
        `/api/workbench/sections/${encodeURIComponent(sid)}`,
        { params: { proposal_id: proposalId } }
      );
      setDetail(res.data);
      setParsonsDraft(res.data?.parsons_response?.content || '');
      setParsonsStatus(res.data?.parsons_response?.status || 'draft');
      setComplianceTagsDraft(res.data?.parsons_response?.compliance_tags || {});
    } catch (e) {
      setDetailError(e?.response?.data?.detail || e.message);
    } finally {
      setLoadingDetail(false);
    }
  }, [proposalId]);

  const loadCompetitors = useCallback(async () => {
    try {
      const res = await axios.get('/api/competitors');
      setCompetitors(Array.isArray(res.data) ? res.data : res.data?.competitors || []);
    } catch {
      /* non-fatal */
    }
  }, []);

  useEffect(() => { loadSections(); }, [loadSections]);
  useEffect(() => { loadCompetitors(); }, [loadCompetitors]);
  useEffect(() => { if (selectedSectionId) loadDetail(selectedSectionId); }, [selectedSectionId, loadDetail]);

  // ── Filtered section list ─────────────────────────────────────────
  const filteredSections = useMemo(() => {
    const all = sectionsData?.sections || [];
    if (!filter.trim()) return all;
    const q = filter.trim().toLowerCase();
    return all.filter((s) => s.section_id.toLowerCase().includes(q));
  }, [sectionsData, filter]);

  // ── Actions ───────────────────────────────────────────────────────
  const saveParsons = async () => {
    if (!selectedSectionId) return;
    setSavingParsons(true);
    try {
      await axios.put(
        `/api/workbench/sections/${encodeURIComponent(selectedSectionId)}/parsons-response`,
        { content: parsonsDraft, status: parsonsStatus, compliance_tags: complianceTagsDraft },
        { params: { proposal_id: proposalId } }
      );
      setSnackbar({ severity: 'success', message: 'Parsons draft saved.' });
      await loadDetail(selectedSectionId);
      await loadSections();
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    } finally {
      setSavingParsons(false);
    }
  };

  const generateCompetitor = async () => {
    if (!selectedSectionId || !selectedCompetitorId) return;
    setGeneratingCompetitor(true);
    try {
      await axios.post(
        `/api/workbench/sections/${encodeURIComponent(selectedSectionId)}/generate-competitor-response`,
        null,
        { params: { proposal_id: proposalId, competitor_id: selectedCompetitorId, persist_to_workbench: true } }
      );
      setSnackbar({ severity: 'success', message: 'Competitor response generated.' });
      await loadDetail(selectedSectionId);
      await loadSections();
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    } finally {
      setGeneratingCompetitor(false);
    }
  };

  const runScore = async (competitorId) => {
    if (!selectedSectionId) return;
    setScoring(true);
    try {
      const params = { proposal_id: proposalId, score_parsons: true };
      if (competitorId) params.competitor_id = competitorId;
      await axios.post(
        `/api/workbench/sections/${encodeURIComponent(selectedSectionId)}/score`,
        null,
        { params }
      );
      setSnackbar({ severity: 'success', message: 'Scoring complete.' });
      await loadDetail(selectedSectionId);
      await loadSections();
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    } finally {
      setScoring(false);
    }
  };

  const runCure = async () => {
    if (!selectedSectionId) return;
    setCureRunning(true);
    try {
      await axios.post(
        `/api/workbench/sections/${encodeURIComponent(selectedSectionId)}/cure-suggestion`,
        {
          competitor_id: cureCompetitorId || null,
          include_rewrite: cureIncludeRewrite,
        },
        { params: { proposal_id: proposalId } }
      );
      setSnackbar({ severity: 'success', message: 'Cure suggestion generated.' });
      setCureDialogOpen(false);
      await loadDetail(selectedSectionId);
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    } finally {
      setCureRunning(false);
    }
  };

  const runRewrite = async () => {
    if (!selectedSectionId) return;
    setRewriteRunning(true);
    try {
      await axios.post(
        `/api/workbench/sections/${encodeURIComponent(selectedSectionId)}/rewrite`,
        {
          competitor_id: rewriteCompetitorId || null,
          instructions: rewriteInstructions || null,
        },
        { params: { proposal_id: proposalId } }
      );
      setSnackbar({ severity: 'success', message: 'Rewrite generated. Review and apply from the Cure tab.' });
      setRewriteDialogOpen(false);
      setRewriteInstructions('');
      await loadDetail(selectedSectionId);
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    } finally {
      setRewriteRunning(false);
    }
  };

  const decideCure = async (id, decision) => {
    try {
      await axios.put(`/api/workbench/cure-suggestions/${id}/decision`, { decision });
      await loadDetail(selectedSectionId);
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    }
  };

  const runExport = async (fmt) => {
    setExportAnchor(null);
    setExporting(true);
    try {
      const res = await axios.get(`/api/workbench/export.${fmt}`, {
        params: { proposal_id: proposalId, include_informational: includeInformational },
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = `response_workbench_proposal_${proposalId}.${fmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      setSnackbar({ severity: 'success', message: `${fmt.toUpperCase()} export downloaded.` });
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    } finally {
      setExporting(false);
    }
  };

  const applyCure = async (id, useRewrite) => {
    try {
      const res = await axios.post(
        `/api/workbench/cure-suggestions/${id}/apply`,
        { use_rewrite: useRewrite, rescore: true }
      );
      const delta = res.data?.rescore_delta;
      const newScore = res.data?.new_score;
      setSnackbar({
        severity: 'success',
        message: `Cure applied. New Parsons score: ${newScore ?? '—'}${delta != null ? ` (Δ ${delta >= 0 ? '+' : ''}${delta})` : ''}`,
      });
      await loadDetail(selectedSectionId);
      await loadSections();
    } catch (e) {
      setSnackbar({ severity: 'error', message: e?.response?.data?.detail || e.message });
    }
  };

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <Box sx={{ p: 2, maxWidth: 1600, mx: 'auto' }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Box>
          <Typography variant="h4">Response Workbench</Typography>
          <Typography variant="body2" color="text.secondary">
            Write each extracted RFP section once. Parsons + competitor responses, scoring, and cure advice — all keyed to the section.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <FormControlLabel
            control={<Switch size="small" checked={includeInformational} onChange={(e) => setIncludeInformational(e.target.checked)} />}
            label={<Typography variant="caption">Include informational sections</Typography>}
          />
          <Button
            variant="outlined"
            size="small"
            startIcon={exporting ? <CircularProgress size={16} /> : <DownloadIcon />}
            onClick={(e) => setExportAnchor(e.currentTarget)}
            disabled={exporting}
          >
            Export
          </Button>
          <Menu
            anchorEl={exportAnchor}
            open={Boolean(exportAnchor)}
            onClose={() => setExportAnchor(null)}
          >
            <MenuItem onClick={() => runExport('xlsx')}>Excel (.xlsx) — compliance matrix</MenuItem>
            <MenuItem onClick={() => runExport('docx')}>Word (.docx) — volume narrative</MenuItem>
            <MenuItem onClick={() => runExport('pdf')}>PDF (.pdf) — volume narrative</MenuItem>
          </Menu>
          <Tooltip title="Reload sections">
            <IconButton onClick={loadSections} size="small"><RefreshIcon /></IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      {sectionsData && (
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <Chip size="small" label={`${sectionsData.total_sections?.toLocaleString()} sections`} />
          <Chip size="small" label={`${sectionsData.total_requirements?.toLocaleString()} requirements`} />
          <Chip size="small" color="warning" label={`${sectionsData.sections_needing_response?.toLocaleString()} need a response`} />
          <Chip size="small" color="success" label={`${sectionsData.sections_with_parsons_draft?.toLocaleString()} drafts started (${fmtPct(sectionsData.sections_with_parsons_draft, sectionsData.sections_needing_response)})`} />
        </Stack>
      )}

      {sectionsError && <Alert severity="error" sx={{ mb: 2 }}>{sectionsError}</Alert>}
      {loadingSections && <LinearProgress sx={{ mb: 1 }} />}

      <Box sx={{ display: 'grid', gridTemplateColumns: '380px 1fr', gap: 2, minHeight: '70vh' }}>
        {/* LEFT: section list */}
        <Paper variant="outlined" sx={{ p: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <TextField
            size="small"
            placeholder="Filter sections…"
            fullWidth
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            sx={{ mb: 1 }}
          />
          <Box sx={{ overflow: 'auto', flex: 1 }}>
            <List dense disablePadding>
              {filteredSections.map((s) => {
                const selected = s.section_id === selectedSectionId;
                const hasDraft = (s.parsons_response_char_count || 0) > 0;
                const hasScore = s.parsons_score != null;
                return (
                  <ListItemButton
                    key={s.section_id}
                    selected={selected}
                    onClick={() => setSelectedSectionId(s.section_id)}
                    sx={{ borderLeft: selected ? '3px solid' : '3px solid transparent', borderLeftColor: 'primary.main' }}
                  >
                    <ListItemText
                      primary={
                        <Stack direction="row" spacing={0.5} alignItems="center">
                          <Typography variant="body2" fontWeight={600}>{s.section_id}</Typography>
                          {s.needs_response && <Chip size="small" color="warning" label="needs response" sx={{ height: 18, fontSize: 10 }} />}
                          {hasDraft && <Chip size="small" color="success" label="draft" sx={{ height: 18, fontSize: 10 }} />}
                          {hasScore && <Chip size="small" label={`${s.parsons_score}/100`} sx={{ height: 18, fontSize: 10 }} />}
                        </Stack>
                      }
                      secondary={
                        <Typography variant="caption" color="text.secondary" noWrap
                                    sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {s.total_requirements} req · {s.rubric_section_id} ({s.rubric_weight_points} pts{s.rubric_pass_fail ? ', P/F' : ''})
                        </Typography>
                      }
                    />
                  </ListItemButton>
                );
              })}
              {filteredSections.length === 0 && !loadingSections && (
                <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: 'center' }}>
                  No sections match.
                </Typography>
              )}
            </List>
          </Box>
        </Paper>

        {/* RIGHT: selected section detail */}
        <Paper variant="outlined" sx={{ p: 2, overflow: 'auto' }}>
          {!selectedSectionId && (
            <Typography variant="body2" color="text.secondary">
              Select a section from the left to begin.
            </Typography>
          )}
          {selectedSectionId && loadingDetail && <LinearProgress />}
          {detailError && <Alert severity="error">{detailError}</Alert>}

          {selectedSectionId && detail && !loadingDetail && (
            <>
              {/* Header */}
              <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
                <Box>
                  <Typography variant="h5">Section {detail.rfp_section_id}</Typography>
                  <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
                    <Chip
                      size="small"
                      icon={<RubricIcon />}
                      label={`Rubric: ${detail.rubric_section_id} (${detail.rubric_weight_points} pts${detail.rubric_pass_fail ? ', pass/fail' : ''})`}
                    />
                    <Chip size="small" label={`${detail.requirements?.length || 0} requirements`} />
                    {detail.needs_response && <Chip size="small" color="warning" label="needs response" />}
                  </Stack>
                </Box>
              </Stack>

              <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: 'divider' }}>
                <Tab label="Requirements" />
                <Tab label={`Parsons draft${detail.parsons_response ? ' ✓' : ''}`} />
                <Tab label={`Competitors (${(detail.workbench_competitor_responses?.length || 0) + (detail.competitor_predictions?.length || 0)})`} />
                <Tab label={`Scoring (${(detail.parsons_scores?.length || 0) + (detail.competitor_scores?.length || 0)})`} />
                <Tab label={`Cure (${detail.cure_suggestions?.length || 0})`} />
              </Tabs>

              {/* Tab 0: Requirements (read-only, with inline compliance tag editor) */}
              {tab === 0 && (
                <Box sx={{ mt: 2 }}>
                  {(detail.requirements || []).map((r) => (
                    <Paper key={r.id} variant="outlined" sx={{ p: 1.5, mb: 1 }}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                        <Typography variant="body2" fontWeight={600}>[{r.requirement_id}]</Typography>
                        {r.category && <Chip size="small" label={r.category} />}
                        {r.priority && <Chip size="small" variant="outlined" label={r.priority} />}
                        {r.document_name && <Typography variant="caption" color="text.secondary">{r.document_name}{r.source_page ? ` · p.${r.source_page}` : ''}</Typography>}
                      </Stack>
                      {r.title && <Typography variant="body2" fontWeight={500}>{r.title}</Typography>}
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }} color="text.secondary">
                        {r.description || r.source_text}
                      </Typography>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
                        <Typography variant="caption" color="text.secondary">Parsons compliance:</Typography>
                        <TextField
                          select
                          size="small"
                          value={complianceTagsDraft[r.requirement_id] || ''}
                          onChange={(e) => setComplianceTagsDraft((prev) => ({ ...prev, [r.requirement_id]: e.target.value }))}
                          sx={{ minWidth: 220 }}
                        >
                          <MenuItem value="">(not set)</MenuItem>
                          {COMPLIANCE_TAGS.map((t) => (<MenuItem key={t} value={t}>{t}</MenuItem>))}
                        </TextField>
                      </Stack>
                    </Paper>
                  ))}
                  {(detail.requirements || []).length === 0 && (
                    <Typography variant="body2" color="text.secondary">No extracted requirements in this section.</Typography>
                  )}
                </Box>
              )}

              {/* Tab 1: Parsons draft */}
              {tab === 1 && (
                <Box sx={{ mt: 2 }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                    <TextField
                      select
                      size="small"
                      label="Status"
                      value={parsonsStatus}
                      onChange={(e) => setParsonsStatus(e.target.value)}
                      sx={{ minWidth: 180 }}
                    >
                      {['draft', 'in_review', 'approved', 'exported'].map((s) => (<MenuItem key={s} value={s}>{s}</MenuItem>))}
                    </TextField>
                    {detail.parsons_response?.version && (
                      <Chip size="small" label={`v${detail.parsons_response.version}`} />
                    )}
                    <Box sx={{ flex: 1 }} />
                    <Button
                      variant="outlined"
                      startIcon={<AiIcon />}
                      onClick={() => { setRewriteCompetitorId(''); setRewriteDialogOpen(true); }}
                    >
                      AI Rewrite
                    </Button>
                    <Button
                      variant="contained"
                      startIcon={savingParsons ? <CircularProgress size={16} /> : <SaveIcon />}
                      disabled={savingParsons}
                      onClick={saveParsons}
                    >
                      Save draft
                    </Button>
                  </Stack>
                  <TextField
                    multiline
                    fullWidth
                    minRows={14}
                    value={parsonsDraft}
                    onChange={(e) => setParsonsDraft(e.target.value)}
                    placeholder="Write Parsons' response to this section. Reference each requirement by ID. Cite specific evidence, named personnel, and concrete metrics."
                  />
                </Box>
              )}

              {/* Tab 2: Competitors */}
              {tab === 2 && (
                <Box sx={{ mt: 2 }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                    <TextField
                      select
                      size="small"
                      label="Competitor"
                      value={selectedCompetitorId}
                      onChange={(e) => setSelectedCompetitorId(e.target.value)}
                      sx={{ minWidth: 220 }}
                    >
                      <MenuItem value="">(choose)</MenuItem>
                      {competitors.map((c) => (<MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>))}
                    </TextField>
                    <Button
                      variant="contained"
                      startIcon={generatingCompetitor ? <CircularProgress size={16} /> : <AiIcon />}
                      disabled={!selectedCompetitorId || generatingCompetitor}
                      onClick={generateCompetitor}
                    >
                      Generate competitor response
                    </Button>
                  </Stack>

                  {(detail.workbench_competitor_responses || []).map((r) => (
                    <Paper key={`wb-${r.id}`} variant="outlined" sx={{ p: 1.5, mb: 1 }}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                        <Typography variant="body2" fontWeight={600}>{r.competitor_name}</Typography>
                        <Chip size="small" label="workbench" color="primary" />
                        <Chip size="small" label={`v${r.version}`} variant="outlined" />
                        <Chip size="small" label={r.status} color={STATUS_COLORS[r.status] || 'default'} />
                      </Stack>
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{r.content}</Typography>
                    </Paper>
                  ))}
                  {(detail.competitor_predictions || []).map((p) => (
                    <Paper key={`pred-${p.id}`} variant="outlined" sx={{ p: 1.5, mb: 1, bgcolor: 'action.hover' }}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                        <Typography variant="body2" fontWeight={600}>{p.competitor_name}</Typography>
                        <Chip size="small" label="prediction" variant="outlined" />
                        {p.confidence_score != null && <Chip size="small" label={`confidence ${Math.round(p.confidence_score * 100)}%`} />}
                        {p.model_used && <Typography variant="caption" color="text.secondary">{p.model_used}</Typography>}
                      </Stack>
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{p.predicted_response}</Typography>
                      {p.reasoning && (
                        <>
                          <Divider sx={{ my: 1 }} />
                          <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'pre-wrap' }}>
                            <b>Reasoning:</b> {p.reasoning}
                          </Typography>
                        </>
                      )}
                    </Paper>
                  ))}
                  {(detail.workbench_competitor_responses || []).length === 0 && (detail.competitor_predictions || []).length === 0 && (
                    <Typography variant="body2" color="text.secondary">No competitor responses yet.</Typography>
                  )}
                </Box>
              )}

              {/* Tab 3: Scoring */}
              {tab === 3 && (
                <Box sx={{ mt: 2 }}>
                  <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
                    <Button
                      variant="contained"
                      startIcon={scoring ? <CircularProgress size={16} /> : <RunIcon />}
                      onClick={() => runScore(null)}
                      disabled={scoring}
                    >
                      Score Parsons
                    </Button>
                    <TextField
                      select
                      size="small"
                      label="Competitor"
                      value={selectedCompetitorId}
                      onChange={(e) => setSelectedCompetitorId(e.target.value)}
                      sx={{ minWidth: 200 }}
                    >
                      <MenuItem value="">(none)</MenuItem>
                      {competitors.map((c) => (<MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>))}
                    </TextField>
                    <Button
                      variant="outlined"
                      startIcon={<RunIcon />}
                      disabled={!selectedCompetitorId || scoring}
                      onClick={() => runScore(selectedCompetitorId)}
                    >
                      Score selected competitor
                    </Button>
                  </Stack>

                  {(detail.parsons_scores || []).map((s) => (
                    <Paper key={`ps-${s.id}`} variant="outlined" sx={{ p: 1.5, mb: 1, borderLeft: '4px solid', borderLeftColor: 'primary.main' }}>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography variant="body2" fontWeight={600}>Parsons</Typography>
                        <Chip size="small" label={`${s.score}/100`} color={s.score >= 70 ? 'success' : s.score >= 50 ? 'warning' : 'error'} />
                        <Typography variant="caption" color="text.secondary">{s.scored_at}</Typography>
                      </Stack>
                      {s.rationale && <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mt: 1 }}>{s.rationale}</Typography>}
                    </Paper>
                  ))}
                  {(detail.competitor_scores || []).map((s) => (
                    <Paper key={`cs-${s.id}`} variant="outlined" sx={{ p: 1.5, mb: 1, borderLeft: '4px solid', borderLeftColor: 'secondary.main' }}>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography variant="body2" fontWeight={600}>{s.scorer_name}</Typography>
                        <Chip size="small" label={`${s.score}/100`} color={s.score >= 70 ? 'success' : s.score >= 50 ? 'warning' : 'error'} />
                        <Typography variant="caption" color="text.secondary">{s.scored_at}</Typography>
                      </Stack>
                      {s.rationale && <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mt: 1 }}>{s.rationale}</Typography>}
                    </Paper>
                  ))}
                  {(detail.parsons_scores || []).length === 0 && (detail.competitor_scores || []).length === 0 && (
                    <Typography variant="body2" color="text.secondary">No scores yet.</Typography>
                  )}
                </Box>
              )}

              {/* Tab 4: Cure */}
              {tab === 4 && (
                <Box sx={{ mt: 2 }}>
                  <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
                    <Button
                      variant="contained"
                      startIcon={<AiIcon />}
                      onClick={() => { setCureCompetitorId(''); setCureIncludeRewrite(false); setCureDialogOpen(true); }}
                    >
                      Generate cure suggestion
                    </Button>
                    <Button
                      variant="outlined"
                      startIcon={<EditIcon />}
                      onClick={() => { setRewriteCompetitorId(''); setRewriteInstructions(''); setRewriteDialogOpen(true); }}
                    >
                      Full rewrite
                    </Button>
                  </Stack>

                  {(detail.cure_suggestions || []).map((c) => (
                    <Paper key={c.id} variant="outlined" sx={{ p: 1.5, mb: 1 }}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                        <Chip
                          size="small"
                          label={c.status}
                          color={c.status === 'applied' ? 'success' : c.status === 'approved' ? 'info' : c.status === 'denied' ? 'error' : 'default'}
                        />
                        {c.competitor_name && <Chip size="small" variant="outlined" label={`vs ${c.competitor_name}`} />}
                        {c.has_rewrite && <Chip size="small" color="primary" label="has rewrite" />}
                        {c.rescore_delta != null && (
                          <Chip size="small" label={`Δ ${c.rescore_delta >= 0 ? '+' : ''}${c.rescore_delta} pts`} color={c.rescore_delta >= 0 ? 'success' : 'error'} />
                        )}
                        <Typography variant="caption" color="text.secondary">{c.created_at}</Typography>
                      </Stack>
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{c.suggestion_text}</Typography>
                      <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                        {c.status === 'proposed' && (
                          <>
                            <Button size="small" startIcon={<CheckIcon />} color="success" variant="outlined" onClick={() => decideCure(c.id, 'approve')}>Approve</Button>
                            <Button size="small" startIcon={<CancelIcon />} color="error" variant="outlined" onClick={() => decideCure(c.id, 'deny')}>Deny</Button>
                          </>
                        )}
                        {c.status === 'approved' && (
                          <>
                            <Button size="small" variant="contained" onClick={() => applyCure(c.id, false)}>
                              Apply as addendum & rescore
                            </Button>
                            {c.has_rewrite && (
                              <Button size="small" variant="contained" color="secondary" onClick={() => applyCure(c.id, true)}>
                                Apply rewrite & rescore
                              </Button>
                            )}
                          </>
                        )}
                      </Stack>
                    </Paper>
                  ))}
                  {(detail.cure_suggestions || []).length === 0 && (
                    <Typography variant="body2" color="text.secondary">No cure suggestions yet. Score first, then generate a cure.</Typography>
                  )}
                </Box>
              )}
            </>
          )}
        </Paper>
      </Box>

      {/* Cure dialog */}
      <Dialog open={cureDialogOpen} onClose={() => setCureDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Generate cure suggestion</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              select
              label="Close gap versus (optional)"
              value={cureCompetitorId}
              onChange={(e) => setCureCompetitorId(e.target.value)}
              fullWidth
            >
              <MenuItem value="">(overall — no specific competitor)</MenuItem>
              {competitors.map((c) => (<MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>))}
            </TextField>
            <FormControlLabel
              control={<Switch checked={cureIncludeRewrite} onChange={(e) => setCureIncludeRewrite(e.target.checked)} />}
              label="Also generate a full suggested rewrite"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCureDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={runCure} disabled={cureRunning} startIcon={cureRunning ? <CircularProgress size={16} /> : <AiIcon />}>Generate</Button>
        </DialogActions>
      </Dialog>

      {/* Rewrite dialog */}
      <Dialog open={rewriteDialogOpen} onClose={() => setRewriteDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>AI full rewrite</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              select
              label="Benchmark against competitor (optional)"
              value={rewriteCompetitorId}
              onChange={(e) => setRewriteCompetitorId(e.target.value)}
              fullWidth
            >
              <MenuItem value="">(none)</MenuItem>
              {competitors.map((c) => (<MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>))}
            </TextField>
            <TextField
              label="User guidance (optional)"
              value={rewriteInstructions}
              onChange={(e) => setRewriteInstructions(e.target.value)}
              multiline
              minRows={3}
              fullWidth
              placeholder="E.g. 'Emphasize Parsons incumbency. Include specific past-performance metrics from the NJ MVC contract.'"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRewriteDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={runRewrite} disabled={rewriteRunning} startIcon={rewriteRunning ? <CircularProgress size={16} /> : <AiIcon />}>Generate</Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar-ish alert at bottom */}
      {snackbar && (
        <Alert
          severity={snackbar.severity}
          onClose={() => setSnackbar(null)}
          sx={{ position: 'fixed', bottom: 16, right: 16, zIndex: 1400, minWidth: 320 }}
        >
          {snackbar.message}
        </Alert>
      )}
    </Box>
  );
}
