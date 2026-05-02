/**
 * SubmissionReadinessPanel
 *
 * Sits on the RFP Workspace dashboard. Shows two stacked surfaces:
 *
 *   1. Agent readiness — traffic light + actionable issue list. Wired to
 *      GET /api/parsons-response/proposals/{id}/readiness.
 *
 *   2. Per-section preparedness — one row per RFP-native section root
 *      (the RFP's OWN terminology, not invented). Each row shows two
 *      animated progress bars (% drafted, % evidence covered) plus a
 *      green/amber/red readiness chip. Wired to GET /api/parsons-response/
 *      proposals/{id}/submission.
 *
 *   Click a section row → drills through to the response-writing page
 *   filtered to that section root.
 */
import React, { useEffect, useState, useMemo } from 'react';
import {
  Box, Card, CardContent, Stack, Typography, Chip, LinearProgress,
  IconButton, Tooltip, Alert, Button, CircularProgress, Divider, Grid,
  Menu, MenuItem, Dialog, DialogTitle, DialogContent, DialogActions,
  FormControlLabel, Checkbox,
} from '@mui/material';
import {
  Refresh as RefreshIcon,
  CheckCircle as CheckIcon,
  Warning as WarningIcon,
  ErrorOutline as ErrorIcon,
  Bolt as BoltIcon,
  AutoAwesome as AssistantIcon,
  ArrowForward as ArrowIcon,
  Build as RepairIcon,
  FileDownload as ExportIcon,
  CompareArrows as ConflictIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';

// ── Helpers ────────────────────────────────────────────────────────
const VERDICT_VISUAL = {
  green: { color: '#2e7d32', label: 'Ready', icon: <CheckIcon /> },
  amber: { color: '#ed6c02', label: 'Caveats', icon: <WarningIcon /> },
  red:   { color: '#c62828', label: 'Not Ready', icon: <ErrorIcon /> },
  unknown: { color: '#9e9e9e', label: 'Unknown', icon: <BoltIcon /> },
};

const READINESS_VISUAL = {
  green: { bg: '#2e7d32', label: 'Ready' },
  amber: { bg: '#ed6c02', label: 'In progress' },
  red:   { bg: '#c62828', label: 'Behind' },
  unknown: { bg: '#9e9e9e', label: '—' },
};

function pctBar(value, color) {
  return (
    <LinearProgress
      variant="determinate"
      value={Math.max(0, Math.min(100, value))}
      sx={{
        height: 6, borderRadius: 3,
        bgcolor: 'rgba(0,0,0,0.05)',
        '& .MuiLinearProgress-bar': { background: color },
      }}
    />
  );
}

// ── Component ──────────────────────────────────────────────────────
export default function SubmissionReadinessPanel({ proposalId }) {
  const [readiness, setReadiness] = useState(null);
  const [submission, setSubmission] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [repairBusy, setRepairBusy] = useState(false);
  const [exportMenu, setExportMenu] = useState(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportDialog, setExportDialog] = useState(null); // {format, preview}
  const [exportOpts, setExportOpts] = useState({
    format: 'docx', only_approved: false, mark_exported: false,
  });
  const [conflictBusy, setConflictBusy] = useState(false);
  const [conflicts, setConflicts] = useState([]);
  const [conflictsOpen, setConflictsOpen] = useState(false);
  const [conflictJobMsg, setConflictJobMsg] = useState(null);
  const navigate = useNavigate();

  const load = async () => {
    if (!proposalId) return;
    setLoading(true);
    setError(null);
    try {
      const [r1, r2] = await Promise.all([
        axios.get(`/api/parsons-response/proposals/${proposalId}/readiness`),
        axios.get(`/api/parsons-response/proposals/${proposalId}/submission`),
      ]);
      setReadiness(r1.data);
      setSubmission(r2.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load readiness');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [proposalId]);

  // Submission-structure extraction state
  const [structureBusy, setStructureBusy] = useState(null); // 'extract' | 'map' | null

  const detectStructure = async () => {
    if (!proposalId) return;
    if (!window.confirm(
      "Detect the RFP's vendor submission structure?\n\n"
      + "This reads the RFP text to find the section that defines how the "
      + "vendor must submit (e.g. 'Forms / Technical Quote / Price Sheet'), "
      + "then maps every requirement into one of those buckets."
    )) return;
    setStructureBusy('extract');
    try {
      const ext = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/structure/extract`,
        { replace_existing: false });
      setStructureBusy('map');
      const map = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/structure/map-requirements`,
        { only_unmapped: true });
      const ext_msg = ext.data.already_extracted
        ? `Structure already extracted (${ext.data.top_level_count} top-level sections).`
        : `Detected ${ext.data.top_level_count} top-level sections from RFP §${ext.data.rfp_section_ref}.`;
      const map_msg = map.data.mode === 'background'
        ? `Mapping ${map.data.pending_count} requirements in the background…`
        : `Mapped ${(map.data.mapped_via_heuristic || 0) + (map.data.mapped_via_llm || 0)} requirements to buckets.`;
      alert(`${ext_msg}\n${map_msg}`);
      await load();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Structure detection failed.');
    } finally {
      setStructureBusy(null);
    }
  };

  // ── Coverage assessment + competitive positioning state ──
  const [coverageJob, setCoverageJob] = useState(null);
  const [positioningJob, setPositioningJob] = useState(null);
  const [competitiveSummary, setCompetitiveSummary] = useState(null);

  const loadCompetitiveSummary = async () => {
    if (!proposalId) return;
    try {
      const { data } = await axios.get(
        `/api/parsons-response/proposals/${proposalId}/competitive/summary`);
      setCompetitiveSummary(data);
    } catch {
      setCompetitiveSummary(null);
    }
  };

  useEffect(() => { if (proposalId) loadCompetitiveSummary();
                    /* eslint-disable-next-line */ }, [proposalId]);

  // Poll an arbitrary background job
  const pollJob = (urlBase, jobId, setter, onDone) => {
    const t = setInterval(async () => {
      try {
        const { data } = await axios.get(`${urlBase}/${jobId}`);
        setter(data);
        if (data.status === 'complete' || data.status === 'error') {
          clearInterval(t);
          onDone && onDone(data);
        }
      } catch (e) {
        clearInterval(t);
      }
    }, 3000);
    return t;
  };

  const startCoverageAssessment = async () => {
    if (!window.confirm(
      "Run Parsons-coverage assessment on every requirement?\n\n"
      + "This is the assessment that decides which requirements the AI "
      + "CAN answer (covered/partial) vs CANNOT (gap). Runs in the "
      + "background; can take several minutes for large RFPs."
    )) return;
    try {
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/coverage/assess`);
      setCoverageJob({ ...data, progress_done: 0, progress_total: 0 });
      pollJob('/api/parsons-response/coverage/jobs', data.job_id, setCoverageJob,
              () => { load(); });
    } catch (e) {
      alert(e?.response?.data?.detail || 'Coverage assessment failed to start');
    }
  };

  const startCompetitivePositioning = async (rerun = false) => {
    if (!window.confirm(
      "Run competitive positioning on every requirement?\n\n"
      + "Tags each requirement as strong / parity / weak / neutral based "
      + "on Parsons evidence + tracked competitor strengths and weaknesses."
    )) return;
    try {
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/competitive/assess`,
        { rerun });
      setPositioningJob({ ...data, progress_done: 0, progress_total: 0 });
      pollJob('/api/parsons-response/competitive/jobs', data.job_id, setPositioningJob,
              () => { loadCompetitiveSummary(); load(); });
    } catch (e) {
      alert(e?.response?.data?.detail || 'Positioning failed to start');
    }
  };

  const repairEmbeddings = async () => {
    setRepairBusy(true);
    try {
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/repair-embeddings`);
      const fixed = (data.repaired || []).filter(r => !r.error).length;
      const failed = (data.repaired || []).filter(r => r.error).length;
      alert(`Embeddings repair: ${fixed} fixed, ${failed} failed.`);
      await load();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Repair failed');
    } finally {
      setRepairBusy(false);
    }
  };

  // Open the export confirmation dialog with a cheap preview first.
  const openExportDialog = async (format) => {
    setExportMenu(null);
    setExportOpts((o) => ({ ...o, format }));
    setExportBusy(true);
    try {
      const { data } = await axios.get(
        `/api/parsons-response/proposals/${proposalId}/export/preview`);
      setExportDialog({ preview: data });
    } catch (e) {
      setError(e?.response?.data?.detail || 'Export preview failed');
    } finally {
      setExportBusy(false);
    }
  };

  const runExport = async () => {
    setExportBusy(true);
    try {
      const fmt = exportOpts.format;
      if (fmt === 'json') {
        const { data } = await axios.post(
          `/api/parsons-response/proposals/${proposalId}/export`,
          { format: 'json',
            only_approved: exportOpts.only_approved,
            mark_exported: exportOpts.mark_exported });
        const blob = new Blob([JSON.stringify(data, null, 2)],
          { type: 'application/json' });
        triggerDownload(blob, `proposal_${proposalId}_draft.json`);
      } else {
        const resp = await axios.post(
          `/api/parsons-response/proposals/${proposalId}/export`,
          { format: fmt,
            only_approved: exportOpts.only_approved,
            mark_exported: exportOpts.mark_exported },
          { responseType: 'blob' });
        const cd = resp.headers['content-disposition'] || '';
        const m = /filename="?([^"]+)"?/.exec(cd);
        const fname = m ? m[1] :
          `proposal_${proposalId}_draft.${fmt === 'docx' ? 'docx' : 'md'}`;
        triggerDownload(resp.data, fname);
      }
      setExportDialog(null);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Export failed');
    } finally {
      setExportBusy(false);
    }
  };

  const triggerDownload = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  };

  const loadConflicts = async () => {
    try {
      const { data } = await axios.get(
        `/api/parsons-response/proposals/${proposalId}/conflicts?status=open`);
      setConflicts(data.conflicts || []);
    } catch (e) {
      setConflicts([]);
    }
  };

  useEffect(() => { if (proposalId) loadConflicts(); /* eslint-disable-next-line */ }, [proposalId]);

  const startConflictScan = async () => {
    setConflictBusy(true);
    setConflictJobMsg('Scanning…');
    try {
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/conflicts/scan`);
      const jobId = data.job_id;
      const poll = setInterval(async () => {
        try {
          const { data: job } = await axios.get(
            `/api/parsons-response/conflicts/jobs/${jobId}`);
          if (job.status === 'done') {
            clearInterval(poll);
            const found = job.result?.conflicts_found || 0;
            const pairs = job.result?.pairs_scanned || 0;
            setConflictJobMsg(
              `Scan complete — ${pairs} section pair(s) checked, ${found} conflict(s) found.`
            );
            await loadConflicts();
            setConflictBusy(false);
            setConflictsOpen(true);
          } else if (job.status === 'error') {
            clearInterval(poll);
            setConflictJobMsg(`Scan failed: ${job.error}`);
            setConflictBusy(false);
          } else {
            setConflictJobMsg(`Scanning (${job.status})…`);
          }
        } catch (e) {
          clearInterval(poll);
          setConflictBusy(false);
          setConflictJobMsg('Polling failed.');
        }
      }, 2500);
    } catch (e) {
      setConflictBusy(false);
      setConflictJobMsg(e?.response?.data?.detail || 'Scan kickoff failed.');
    }
  };

  const dismissConflict = async (id) => {
    try {
      await axios.patch(`/api/parsons-response/conflicts/${id}`, { status: 'dismissed' });
      setConflicts((cs) => cs.filter((c) => c.id !== id));
    } catch (e) {
      alert(e?.response?.data?.detail || 'Dismiss failed');
    }
  };

  const verdict = readiness?.verdict || 'unknown';
  const v = VERDICT_VISUAL[verdict] || VERDICT_VISUAL.unknown;
  const overall = submission?.overall || {};

  if (loading) {
    return (
      <Card sx={{ borderRadius: 3, mb: 3 }}>
        <CardContent sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', py: 5 }}>
          <CircularProgress size={28} />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card sx={{ borderRadius: 3, mb: 3,
                background: `linear-gradient(135deg, #fff, ${v.color}12)`,
                border: `1px solid ${v.color}40` }}>
      <CardContent>
        {/* ── Header: agent-readiness verdict ── */}
        <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
          <Box sx={{
            width: 48, height: 48, borderRadius: 2,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: v.color, color: '#fff',
          }}>{v.icon}</Box>
          <Box sx={{ flex: 1 }}>
            <Typography variant="overline" color="text.secondary"
                        sx={{ display: 'block', lineHeight: 1.1 }}>
              Submission readiness
            </Typography>
            <Typography variant="h6" fontWeight={800} sx={{ color: v.color }}>
              {v.label}{readiness?.verdict_label ? ` — ${readiness.verdict_label}` : ''}
            </Typography>
          </Box>
          <IconButton onClick={load} title="Refresh"><RefreshIcon /></IconButton>
        </Stack>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

        {/* ── Overall stats ── */}
        {submission && (
          <Grid container spacing={1.5} sx={{ mb: 2 }}>
            <Grid item xs={6} sm={3}>
              <Tooltip title="Total RFP requirements extracted">
                <Box sx={{ p: 1.25, borderRadius: 2, bgcolor: 'rgba(0,174,230,0.05)',
                           border: '1px solid rgba(0,174,230,0.18)' }}>
                  <Typography variant="caption" color="text.secondary">Requirements</Typography>
                  <Typography variant="h6" fontWeight={800}>{overall.total || 0}</Typography>
                </Box>
              </Tooltip>
            </Grid>
            <Grid item xs={6} sm={3}>
              <Tooltip title="Drafted (AI or user)">
                <Box sx={{ p: 1.25, borderRadius: 2, bgcolor: 'rgba(46,125,50,0.05)',
                           border: '1px solid rgba(46,125,50,0.18)' }}>
                  <Typography variant="caption" color="text.secondary">Drafted</Typography>
                  <Typography variant="h6" fontWeight={800}>
                    {overall.drafted || 0} <Typography component="span" variant="caption">({overall.pct_drafted || 0}%)</Typography>
                  </Typography>
                </Box>
              </Tooltip>
            </Grid>
            <Grid item xs={6} sm={3}>
              <Tooltip title="Parsons evidence available (covered + partial)">
                <Box sx={{ p: 1.25, borderRadius: 2, bgcolor: 'rgba(2,136,209,0.05)',
                           border: '1px solid rgba(2,136,209,0.18)' }}>
                  <Typography variant="caption" color="text.secondary">Evidence covered</Typography>
                  <Typography variant="h6" fontWeight={800}>
                    {overall.covered || 0} <Typography component="span" variant="caption">({overall.pct_evidence_covered || 0}%)</Typography>
                  </Typography>
                </Box>
              </Tooltip>
            </Grid>
            <Grid item xs={6} sm={3}>
              <Tooltip title="Requirements with no Parsons evidence found">
                <Box sx={{ p: 1.25, borderRadius: 2,
                           bgcolor: (overall.gap || 0) > 0 ? 'rgba(198,40,40,0.05)' : 'rgba(46,125,50,0.05)',
                           border: `1px solid ${(overall.gap || 0) > 0 ? 'rgba(198,40,40,0.18)' : 'rgba(46,125,50,0.18)'}` }}>
                  <Typography variant="caption" color="text.secondary">Evidence gaps</Typography>
                  <Typography variant="h6" fontWeight={800}
                              sx={{ color: (overall.gap || 0) > 0 ? '#c62828' : '#2e7d32' }}>
                    {overall.gap || 0} <Typography component="span" variant="caption">({overall.pct_gap || 0}%)</Typography>
                  </Typography>
                </Box>
              </Tooltip>
            </Grid>
          </Grid>
        )}

        {/* ── Submission structure source banner ── */}
        {submission && (
          <Alert
            severity={submission.structure_source === 'rfp_defined' ? 'success' : 'info'}
            sx={{ mb: 2 }}
            action={
              submission.structure_source !== 'rfp_defined' && (
                <Button size="small" color="inherit"
                        onClick={detectStructure}
                        disabled={!!structureBusy}>
                  {structureBusy === 'extract' ? 'Detecting…'
                    : structureBusy === 'map' ? 'Mapping requirements…'
                    : 'Detect submission structure'}
                </Button>
              )
            }>
            {submission.structure_source === 'rfp_defined' ? (
              <>
                <strong>RFP-defined structure</strong> — the dashboard
                groups requirements using the {submission.structure_top_level_count} sections
                the RFP itself defines for vendor submission.
                {submission.mapped_count < (overall.total || 0) && (
                  <> {(overall.total || 0) - submission.mapped_count} requirement(s)
                  not yet mapped.{' '}
                  <Button size="small" sx={{ ml: 1 }}
                          onClick={detectStructure} disabled={!!structureBusy}>
                    Map remaining
                  </Button></>
                )}
              </>
            ) : (
              <>
                <strong>Auto-derived sections</strong> — currently rolling up
                requirements by section ID prefix. Click <em>Detect submission
                structure</em> to use the RFP's own vendor-submission
                terminology (e.g. "Forms", "Technical Quote", "Price Sheet")
                instead.
              </>
            )}
          </Alert>
        )}

        {/* ── Parsons knowledge by category ── */}
        {readiness?.parsons_categories?.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="overline" color="text.secondary"
                        sx={{ display: 'block', mb: 0.5 }}>
              Parsons knowledge contributing to responses
            </Typography>
            <Grid container spacing={1}>
              {readiness.parsons_categories.map((c) => {
                const isPriority = ['past_proposal', 'capability_statement'].includes(c.category);
                const accent = isPriority ? '#2e7d32' : '#0288d1';
                const embeddedPct = c.chunk_count > 0
                  ? Math.round(c.chunks_with_embedding / c.chunk_count * 100)
                  : 0;
                return (
                  <Grid item xs={6} sm={4} md={3} key={c.category}>
                    <Box sx={{ p: 1.25, borderRadius: 2,
                               border: `1px solid ${accent}40`,
                               bgcolor: `${accent}08` }}>
                      <Typography variant="caption" color="text.secondary"
                                  sx={{ display: 'block', textTransform: 'uppercase',
                                        letterSpacing: 0.4, fontSize: '0.6rem' }}>
                        {c.category.replace(/_/g, ' ')}
                      </Typography>
                      <Stack direction="row" spacing={1} alignItems="baseline">
                        <Typography variant="h6" fontWeight={800} sx={{ color: accent }}>
                          {c.doc_count}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          doc{c.doc_count === 1 ? '' : 's'}
                        </Typography>
                      </Stack>
                      <Typography variant="caption" color="text.secondary"
                                  sx={{ display: 'block' }}>
                        {c.chunk_count.toLocaleString()} chunks · {embeddedPct}% embedded
                      </Typography>
                      {(c.scope_global > 0 || c.scope_proposal > 0) && (
                        <Typography variant="caption" color="text.secondary"
                                    sx={{ display: 'block', fontSize: '0.65rem' }}>
                          {c.scope_global > 0 && `${c.scope_global} global`}
                          {c.scope_global > 0 && c.scope_proposal > 0 && ' · '}
                          {c.scope_proposal > 0 && `${c.scope_proposal} this RFP`}
                        </Typography>
                      )}
                    </Box>
                  </Grid>
                );
              })}
            </Grid>
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', mt: 0.75, fontStyle: 'italic' }}>
              These are the documents the AI vector-searches when drafting
              every response. Past proposals and capability statements are
              weighted highest.
            </Typography>
          </Box>
        )}

        {/* ── AI assessment (coverage) status + trigger ── */}
        {readiness?.summary && (() => {
          const total = readiness.summary.requirements_total || 0;
          const unrun = readiness.summary.requirements_coverage_unrun || 0;
          const gap = readiness.summary.requirements_coverage_gap || 0;
          const assessed = total - unrun;
          const pctAssessed = total > 0 ? Math.round((assessed / total) * 100) : 0;
          const isRunning = coverageJob && (coverageJob.status === 'running'
                                            || coverageJob.status === 'queued');
          return (
            <Box sx={{ mb: 2 }}>
              <Typography variant="overline" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5 }}>
                AI assessment — what can / cannot be answered
              </Typography>
              {total === 0 ? (
                <Alert severity="info">
                  No requirements have been extracted yet.
                </Alert>
              ) : unrun > 0 && !isRunning ? (
                <Alert severity="warning"
                       action={
                         <Button color="inherit" size="small"
                                 onClick={startCoverageAssessment}>
                           Run assessment ({unrun} pending)
                         </Button>
                       }>
                  <strong>{unrun} of {total} requirements ({100 - pctAssessed}%)</strong>{' '}
                  have not yet been assessed for AI answerability.
                  Click <em>Run assessment</em> to grade each requirement
                  as <strong>covered</strong>, <strong>partial</strong>,{' '}
                  <strong>gap</strong>, or <strong>uncertain</strong>{' '}
                  using your Parsons knowledge corpus.
                </Alert>
              ) : isRunning ? (
                <Alert severity="info">
                  <Typography variant="body2" sx={{ mb: 1 }}>
                    Coverage assessment running ({coverageJob.progress_done || 0} of {coverageJob.progress_total || '?'} requirements)…
                  </Typography>
                  <LinearProgress
                    variant="determinate"
                    value={coverageJob.progress_total
                      ? (coverageJob.progress_done / coverageJob.progress_total) * 100
                      : 0}
                    sx={{ height: 6, borderRadius: 3 }}
                  />
                </Alert>
              ) : (
                <Grid container spacing={1}>
                  {[
                    { key: 'covered', label: 'Covered',  color: '#2e7d32',
                      v: (overall.covered || 0), tip: 'Parsons evidence directly substantiates these — AI can draft with confidence.' },
                    { key: 'partial', label: 'Partial',  color: '#0288d1',
                      v: 0, tip: 'Some Parsons evidence but key specifics missing — AI needs SME input.' },
                    { key: 'gap',     label: 'Gap',      color: '#c62828',
                      v: gap, tip: 'No Parsons evidence — AI cannot draft a defensible response.' },
                    { key: 'rerun',   label: 'Re-run',   color: '#7b1fa2',
                      v: '↻', tip: 'Re-run assessment after uploading new Parsons knowledge.', isAction: true },
                  ].map((tile) => (
                    <Grid item xs={6} sm={3} key={tile.key}>
                      <Tooltip title={tile.tip}>
                        <Box
                          onClick={tile.isAction ? startCoverageAssessment : undefined}
                          sx={{ p: 1.25, borderRadius: 2,
                                 border: `1px solid ${tile.color}40`,
                                 bgcolor: `${tile.color}08`,
                                 cursor: tile.isAction ? 'pointer' : 'default',
                                 '&:hover': tile.isAction ? { bgcolor: `${tile.color}18` } : {} }}>
                          <Typography variant="caption" color="text.secondary"
                                      sx={{ display: 'block', textTransform: 'uppercase',
                                            letterSpacing: 0.4, fontSize: '0.6rem' }}>
                            {tile.label}
                          </Typography>
                          <Typography variant="h6" fontWeight={800}
                                      sx={{ color: tile.color }}>
                            {tile.v}
                          </Typography>
                        </Box>
                      </Tooltip>
                    </Grid>
                  ))}
                </Grid>
              )}
            </Box>
          );
        })()}

        {/* ── Competitive positioning ── */}
        <Box sx={{ mb: 2 }}>
          <Stack direction="row" alignItems="center" sx={{ mb: 0.5 }}>
            <Typography variant="overline" color="text.secondary" sx={{ flex: 1 }}>
              Competitive positioning
            </Typography>
            {competitiveSummary && (competitiveSummary.counts?.not_assessed > 0
                                      || competitiveSummary.total === 0) && (
              <Button size="small" variant="outlined"
                      onClick={() => startCompetitivePositioning(false)}
                      disabled={positioningJob && positioningJob.status === 'running'}>
                {positioningJob && positioningJob.status === 'running'
                  ? `Assessing ${positioningJob.progress_done || 0}/${positioningJob.progress_total || '?'}…`
                  : 'Assess where we win/lose'}
              </Button>
            )}
            {competitiveSummary && competitiveSummary.counts?.not_assessed === 0
              && competitiveSummary.total > 0 && (
              <Button size="small" onClick={() => startCompetitivePositioning(true)}>
                Re-run
              </Button>
            )}
          </Stack>
          {!competitiveSummary || competitiveSummary.total === 0 ? (
            <Typography variant="body2" color="text.secondary">
              Not yet computed. Click <em>Assess where we win/lose</em> to
              tag every requirement as strong / parity / weak / neutral
              based on Parsons evidence + competitor dossiers.
            </Typography>
          ) : competitiveSummary.counts?.not_assessed === competitiveSummary.total ? (
            <Alert severity="info">
              {competitiveSummary.total} requirements not yet assessed for
              competitive position. Click <em>Assess where we win/lose</em>.
            </Alert>
          ) : (
            <>
              <Grid container spacing={1} sx={{ mb: 1 }}>
                {[
                  { k: 'strong',  label: 'We win',     color: '#2e7d32' },
                  { k: 'parity',  label: 'At parity',  color: '#0288d1' },
                  { k: 'weak',    label: 'They win',   color: '#c62828' },
                  { k: 'neutral', label: 'No angle',   color: '#9e9e9e' },
                ].map((tile) => {
                  const v = competitiveSummary.counts[tile.k] || 0;
                  return (
                    <Grid item xs={6} sm={3} key={tile.k}>
                      <Box sx={{ p: 1.25, borderRadius: 2,
                                 border: `1px solid ${tile.color}40`,
                                 bgcolor: `${tile.color}08` }}>
                        <Typography variant="caption" color="text.secondary"
                                    sx={{ display: 'block', textTransform: 'uppercase',
                                          letterSpacing: 0.4, fontSize: '0.6rem' }}>
                          {tile.label}
                        </Typography>
                        <Typography variant="h6" fontWeight={800}
                                    sx={{ color: tile.color }}>
                          {v}
                        </Typography>
                      </Box>
                    </Grid>
                  );
                })}
              </Grid>
              {competitiveSummary.competitors?.length > 0 && (
                <Box>
                  <Typography variant="caption" color="text.secondary"
                              sx={{ display: 'block', mb: 0.5 }}>
                    Per-competitor — where we have advantage / risk
                  </Typography>
                  <Stack spacing={0.5}>
                    {competitiveSummary.competitors.map((c) => (
                      <Box key={c.competitor_id}
                           sx={{ p: 1, borderRadius: 1.5,
                                 bgcolor: 'rgba(0,0,0,0.02)' }}>
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Typography variant="body2" fontWeight={600}
                                      sx={{ flex: 1 }} noWrap>
                            {c.name}
                          </Typography>
                          <Tooltip title="Requirements where we look stronger than this competitor">
                            <Chip size="small"
                                  label={`Win ${c.advantage_hits}`}
                                  sx={{ height: 20, bgcolor: 'rgba(46,125,50,0.15)',
                                        color: '#2e7d32', fontWeight: 700 }} />
                          </Tooltip>
                          <Tooltip title="Requirements where this competitor may look stronger">
                            <Chip size="small"
                                  label={`Risk ${c.risk_hits}`}
                                  sx={{ height: 20,
                                        bgcolor: c.risk_hits > 0
                                          ? 'rgba(198,40,40,0.15)' : 'rgba(0,0,0,0.05)',
                                        color: c.risk_hits > 0 ? '#c62828' : 'text.secondary',
                                        fontWeight: 700 }} />
                          </Tooltip>
                        </Stack>
                      </Box>
                    ))}
                  </Stack>
                </Box>
              )}
              {competitiveSummary.competitors?.length === 0 && (
                <Typography variant="caption" color="text.secondary">
                  No competitors tracked on this proposal yet — positioning
                  is using Parsons-coverage as a proxy. Add competitors to
                  the proposal to enable per-competitor advantage/risk
                  analysis.
                </Typography>
              )}
            </>
          )}
        </Box>

        {/* ── Issue list (agent self-check findings) ── */}
        {readiness?.issues?.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="overline" color="text.secondary"
                        sx={{ display: 'block', mb: 0.5 }}>
              Pre-flight findings
            </Typography>
            <Stack spacing={0.75}>
              {readiness.issues.map((iss, i) => {
                const sev = iss.severity === 'high' ? '#c62828'
                          : iss.severity === 'medium' ? '#ed6c02' : '#0288d1';
                return (
                  <Box key={i} sx={{
                    p: 1.25, borderRadius: 1.5,
                    borderLeft: `4px solid ${sev}`,
                    bgcolor: `${sev}10`,
                  }}>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <Typography variant="body2" fontWeight={700} sx={{ flex: 1 }}>
                        {iss.label}
                      </Typography>
                      <Chip size="small" label={iss.severity}
                            sx={{ height: 18, fontSize: '0.65rem',
                                  bgcolor: sev, color: '#fff' }} />
                      {iss.key === 'missing_embeddings' && (
                        <Button
                          size="small" variant="outlined" startIcon={<RepairIcon />}
                          onClick={repairEmbeddings} disabled={repairBusy}
                        >
                          {repairBusy ? 'Repairing…' : 'Repair embeddings'}
                        </Button>
                      )}
                    </Stack>
                    {iss.fix && (
                      <Typography variant="caption" color="text.secondary"
                                  sx={{ display: 'block', mt: 0.25 }}>
                        Fix: {iss.fix}
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </Stack>
          </Box>
        )}

        <Divider sx={{ my: 2 }} />

        {/* ── Conflict-scan banner (open conflicts found) ── */}
        {conflicts.length > 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}
                 action={
                   <Button color="inherit" size="small"
                           onClick={() => setConflictsOpen(true)}>
                     Review {conflicts.length}
                   </Button>
                 }>
            <strong>{conflicts.length}</strong> open cross-section conflict
            {conflicts.length === 1 ? '' : 's'} flagged in this proposal.
          </Alert>
        )}
        {conflictJobMsg && (
          <Alert severity={conflictBusy ? 'info' : 'success'} sx={{ mb: 2 }}
                 onClose={() => setConflictJobMsg(null)}>
            {conflictJobMsg}
          </Alert>
        )}

        {/* ── Per-section preparedness ── */}
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }} flexWrap="wrap" useFlexGap>
          <Typography variant="overline" color="text.secondary" sx={{ flex: 1, minWidth: 200 }}>
            Preparedness by RFP section
          </Typography>
          <Button size="small" variant="outlined" startIcon={<ConflictIcon />}
                  onClick={startConflictScan} disabled={conflictBusy}>
            {conflictBusy ? 'Scanning…' : 'Scan for conflicts'}
          </Button>
          <Button size="small" variant="outlined" startIcon={<ExportIcon />}
                  onClick={(e) => setExportMenu(e.currentTarget)}
                  disabled={exportBusy}>
            {exportBusy ? 'Working…' : 'Export draft'}
          </Button>
          <Menu anchorEl={exportMenu} open={Boolean(exportMenu)}
                onClose={() => setExportMenu(null)}>
            <MenuItem onClick={() => openExportDialog('docx')}>Microsoft Word (.docx)</MenuItem>
            <MenuItem onClick={() => openExportDialog('markdown')}>Markdown (.md)</MenuItem>
            <MenuItem onClick={() => openExportDialog('json')}>Structured JSON (.json)</MenuItem>
          </Menu>
          <Button size="small" variant="contained"
                  endIcon={<ArrowIcon />}
                  onClick={() => navigate(`/p/${proposalId}/parsons-response`)}
                  startIcon={<AssistantIcon />}>
            Open Response Writer
          </Button>
        </Stack>
        {!submission?.sections?.length ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            No requirements extracted yet — once the RFP is processed, the
            section-by-section breakdown lands here.
          </Typography>
        ) : (
          <Stack spacing={0.75} sx={{ maxHeight: 360, overflowY: 'auto', pr: 1 }}>
            {submission.sections.map((s) => {
              const rv = READINESS_VISUAL[s.readiness] || READINESS_VISUAL.unknown;
              return (
                <Box key={s.section_root}
                     onClick={() => navigate(
                       `/p/${proposalId}/parsons-response?section=${encodeURIComponent(s.section_root)}`
                     )}
                     sx={{
                       p: 1.25, borderRadius: 1.5, cursor: 'pointer',
                       border: '1px solid rgba(0,0,0,0.08)',
                       transition: 'all 0.15s ease',
                       '&:hover': { borderColor: '#00AEE6', bgcolor: 'rgba(0,174,230,0.04)' },
                     }}>
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                    <Chip size="small" label={rv.label}
                          sx={{ height: 18, fontSize: '0.62rem',
                                bgcolor: rv.bg, color: '#fff' }} />
                    <Typography variant="body2" fontWeight={700} sx={{ flex: 1 }} noWrap>
                      {s.label}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {s.total} req{s.total === 1 ? '' : 's'}
                    </Typography>
                  </Stack>
                  <Stack direction="row" spacing={1.5} alignItems="center">
                    <Box sx={{ flex: 1 }}>
                      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.15 }}>
                        <Typography variant="caption" color="text.secondary">Drafted</Typography>
                        <Typography variant="caption" fontWeight={700}>{s.pct_drafted}%</Typography>
                      </Stack>
                      {pctBar(s.pct_drafted, '#00AEE6')}
                    </Box>
                    <Box sx={{ flex: 1 }}>
                      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.15 }}>
                        <Typography variant="caption" color="text.secondary">Evidence</Typography>
                        <Typography variant="caption" fontWeight={700}>{s.pct_evidence_covered}%</Typography>
                      </Stack>
                      {pctBar(s.pct_evidence_covered, '#2e7d32')}
                    </Box>
                    {(s.gap_count || 0) > 0 && (
                      <Tooltip title={`${s.gap_count} requirement(s) with no Parsons evidence`}>
                        <Chip size="small" color="error"
                              label={`${s.gap_count} gap${s.gap_count === 1 ? '' : 's'}`}
                              sx={{ height: 20, fontSize: '0.65rem' }} />
                      </Tooltip>
                    )}
                  </Stack>
                </Box>
              );
            })}
          </Stack>
        )}
      </CardContent>

      {/* ── Export confirmation dialog with preview ── */}
      <Dialog open={Boolean(exportDialog)} onClose={() => setExportDialog(null)}
              maxWidth="sm" fullWidth>
        <DialogTitle>Export proposal draft ({exportOpts.format.toUpperCase()})</DialogTitle>
        <DialogContent dividers>
          {exportDialog?.preview ? (
            <Box>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {exportDialog.preview.section_count} sections will be rendered.
              </Typography>
              <Grid container spacing={1.5} sx={{ mb: 2 }}>
                <Grid item xs={6}>
                  <Box sx={{ p: 1.25, borderRadius: 2, bgcolor: 'rgba(0,174,230,0.06)' }}>
                    <Typography variant="caption" color="text.secondary">Total requirements</Typography>
                    <Typography variant="h6" fontWeight={800}>{exportDialog.preview.totals?.requirements || 0}</Typography>
                  </Box>
                </Grid>
                <Grid item xs={6}>
                  <Box sx={{ p: 1.25, borderRadius: 2, bgcolor: 'rgba(46,125,50,0.06)' }}>
                    <Typography variant="caption" color="text.secondary">Will be included</Typography>
                    <Typography variant="h6" fontWeight={800}>{exportDialog.preview.totals?.included_in_export || 0}</Typography>
                  </Box>
                </Grid>
              </Grid>
              <FormControlLabel
                control={<Checkbox checked={exportOpts.only_approved}
                  onChange={(e) => setExportOpts((o) => ({ ...o, only_approved: e.target.checked }))} />}
                label="Only include approved responses"
              />
              <FormControlLabel
                control={<Checkbox checked={exportOpts.mark_exported}
                  onChange={(e) => setExportOpts((o) => ({ ...o, mark_exported: e.target.checked }))} />}
                label="Mark included responses as exported (locks subsequent re-drafting)"
              />
              <Alert severity="info" sx={{ mt: 1 }}>
                Stale section narratives will be re-assembled on the fly. This can take a minute or two for large proposals.
              </Alert>
            </Box>
          ) : (
            <CircularProgress size={20} />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setExportDialog(null)}>Cancel</Button>
          <Button variant="contained" onClick={runExport}
                  disabled={exportBusy} startIcon={<ExportIcon />}>
            {exportBusy ? 'Exporting…' : `Download ${exportOpts.format}`}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Conflicts drawer dialog ── */}
      <Dialog open={conflictsOpen} onClose={() => setConflictsOpen(false)}
              maxWidth="md" fullWidth>
        <DialogTitle>Cross-section conflicts ({conflicts.length} open)</DialogTitle>
        <DialogContent dividers>
          {!conflicts.length ? (
            <Typography variant="body2" color="text.secondary">
              No open conflicts. Run a fresh scan to check again.
            </Typography>
          ) : (
            <Stack spacing={1.5}>
              {conflicts.map((c) => {
                const sevColor = c.severity === 'high' ? '#c62828'
                  : c.severity === 'medium' ? '#ed6c02' : '#0288d1';
                return (
                  <Box key={c.id} sx={{
                    p: 1.5, borderRadius: 1.5,
                    borderLeft: `4px solid ${sevColor}`,
                    bgcolor: `${sevColor}10`,
                  }}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip size="small" label={c.severity}
                            sx={{ height: 20, bgcolor: sevColor, color: '#fff', fontSize: '0.7rem' }} />
                      <Typography variant="caption" color="text.secondary">
                        {c.section_label_a} <ConflictIcon sx={{ fontSize: 12, mx: 0.5 }} /> {c.section_label_b}
                      </Typography>
                      <Box sx={{ flex: 1 }} />
                      <Button size="small" onClick={() => dismissConflict(c.id)}>Dismiss</Button>
                    </Stack>
                    <Typography variant="body2" fontWeight={700}>{c.summary}</Typography>
                    {c.detail && (
                      <Typography variant="body2" color="text.secondary"
                                  sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                        {c.detail}
                      </Typography>
                    )}
                    {(c.requirement_ids_a?.length > 0 || c.requirement_ids_b?.length > 0) && (
                      <Typography variant="caption" color="text.secondary"
                                  sx={{ display: 'block', mt: 0.5 }}>
                        Requirement IDs cited: A=[{c.requirement_ids_a?.join(', ')}] · B=[{c.requirement_ids_b?.join(', ')}]
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={startConflictScan} disabled={conflictBusy} startIcon={<ConflictIcon />}>
            {conflictBusy ? 'Scanning…' : 'Re-scan now'}
          </Button>
          <Button onClick={() => setConflictsOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}
