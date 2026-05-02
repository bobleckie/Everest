/**
 * DiffQuestionAnalysis
 *
 * The post-extraction pipeline review surface.
 *
 * Workflow:
 *   1. Click "Run pipeline" → start the chained Stage 3 diff →
 *      question generation → reconciliation. Live progress polled
 *      every 2.5s until status='complete'.
 *   2. Each candidate question shows:
 *        - Reconciliation badge (added / replaces / updates / duplicate)
 *        - Inference flag + confidence, when applicable
 *        - The diff row(s) that triggered it (status, severity, blurb)
 *        - The existing question it replaces / updates, when applicable
 *        - Accept / Edit / Reject actions
 *   3. Filters across the candidate list: action / inferences-only.
 *   4. Run history sidebar so the user can re-open a prior run.
 *
 * Accuracy-of-output transparency: every candidate makes its provenance
 * explicit (source diff row + change summary + ops_change_summary +
 * rationale + inference flag) so reviewers can audit any question
 * before it's submitted to the agency.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Card, CardContent, Stack, Typography, Chip, IconButton, Tooltip,
  CircularProgress, Alert, Button, Divider, TextField, MenuItem, Select,
  Accordion, AccordionSummary, AccordionDetails, LinearProgress,
  Dialog, DialogTitle, DialogContent, DialogActions, FormControlLabel,
  Switch, Checkbox, ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import {
  ExpandMore as ExpandIcon,
  AutoAwesome as RunIcon,
  CheckCircle as AcceptIcon,
  Cancel as RejectIcon,
  Edit as EditIcon,
  Refresh as RefreshIcon,
  Warning as InferenceIcon,
  ArrowBack as BackIcon,
  Compare as DiffIcon,
  History as HistoryIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { useProposal } from '../proposal/ProposalContext';

const ACTION_VISUAL = {
  added:     { color: '#2e7d32', label: 'Add' },
  replaces:  { color: '#7b1fa2', label: 'Replaces' },
  updates:   { color: '#0288d1', label: 'Updates' },
  duplicate: { color: '#9e9e9e', label: 'Duplicate' },
};

const SEVERITY_COLORS = {
  critical: '#c62828', high: '#ed6c02', medium: '#0288d1',
  low: '#9e9e9e', informational: '#bdbdbd',
};

const STATUS_COLORS = {
  pending: '#9e9e9e',
  diff_running: '#0288d1',
  generating_questions: '#7b1fa2',
  reconciling: '#ed6c02',
  complete: '#2e7d32',
  failed: '#c62828',
};

function StagePill({ run, stageKey, label }) {
  const startedKey = `${stageKey}_started_at`;
  const completedKey = `${stageKey}_completed_at`;
  const started = run?.[startedKey];
  const completed = run?.[completedKey];
  let state = 'idle';
  if (completed) state = 'done';
  else if (started) state = 'running';
  const bg = state === 'done' ? '#2e7d32'
           : state === 'running' ? '#0288d1' : '#bdbdbd';
  return (
    <Box sx={{
      px: 1.25, py: 0.5, borderRadius: 1, bgcolor: `${bg}15`,
      border: `1px solid ${bg}40`,
      display: 'inline-flex', alignItems: 'center', gap: 0.75,
    }}>
      {state === 'running' && <CircularProgress size={10} />}
      <Typography variant="caption" sx={{ color: bg, fontWeight: 700 }}>
        {label}
      </Typography>
    </Box>
  );
}

// ── Candidate card ────────────────────────────────────────────────
function CandidateCard({ cand, onChanged }) {
  const [editOpen, setEditOpen] = useState(false);
  const [draft, setDraft] = useState({
    question_text: cand.question_text || '',
    rationale: cand.rationale || '',
    category: cand.category || 'clarification',
    priority: cand.priority || 'medium',
  });
  const [busy, setBusy] = useState(null);
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [rejectOpen, setRejectOpen] = useState(false);

  const av = ACTION_VISUAL[cand.reconciliation_action] || ACTION_VISUAL.added;
  const sev = (cand.related_diff_rows?.[0]?.impact_severity) || 'medium';
  const sevColor = SEVERITY_COLORS[sev] || SEVERITY_COLORS.medium;

  const accept = async () => {
    setBusy('accept');
    try {
      await axios.post(`/api/diff-question-analysis/candidates/${cand.id}/accept`);
      onChanged && onChanged();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Accept failed');
    } finally { setBusy(null); }
  };

  const submitReject = async () => {
    setBusy('reject');
    try {
      await axios.post(`/api/diff-question-analysis/candidates/${cand.id}/reject`,
        { review_notes: rejectFeedback || null });
      setRejectOpen(false);
      setRejectFeedback('');
      onChanged && onChanged();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Reject failed');
    } finally { setBusy(null); }
  };

  const saveEdit = async () => {
    setBusy('edit');
    try {
      await axios.patch(`/api/diff-question-analysis/candidates/${cand.id}`, draft);
      setEditOpen(false);
      onChanged && onChanged();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Edit failed');
    } finally { setBusy(null); }
  };

  return (
    <>
      <Accordion variant="outlined" sx={{ mb: 1, borderRadius: 2,
                                           overflow: 'hidden',
                                           '&:before': { display: 'none' } }}>
        <AccordionSummary expandIcon={<ExpandIcon />}>
          <Stack direction="row" alignItems="center" spacing={1}
                 sx={{ width: '100%', overflow: 'hidden', minWidth: 0 }}>
            <Chip size="small" label={av.label}
                  sx={{ height: 22, fontWeight: 700,
                        bgcolor: av.color, color: '#fff', minWidth: 80, flexShrink: 0 }} />
            {cand.inference_flag && (
              <Tooltip title={`Inference (${cand.inference_confidence} confidence) — verify before submitting`}>
                <Chip size="small" icon={<InferenceIcon />} label="inference"
                      color="warning"
                      sx={{ height: 22, fontWeight: 700, flexShrink: 0 }} />
              </Tooltip>
            )}
            <Chip size="small" label={cand.priority || 'medium'}
                  variant="outlined"
                  sx={{ height: 20, fontSize: '0.65rem', flexShrink: 0 }} />
            <Box sx={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
              <Typography variant="body2" fontWeight={600} noWrap
                          sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {cand.question_text}
              </Typography>
              {cand.source_section && (
                <Typography variant="caption" color="text.secondary">
                  {cand.source_section}{cand.source_page ? ` · p.${cand.source_page}` : ''}
                </Typography>
              )}
            </Box>
            <Chip size="small" label={cand.status}
                  sx={{ height: 18, fontSize: '0.62rem',
                        bgcolor: cand.status === 'rejected' ? '#c62828'
                               : cand.status === 'reviewed' ? '#2e7d32'
                               : '#9e9e9e',
                        color: '#fff' }} />
          </Stack>
        </AccordionSummary>
        <AccordionDetails>
          <Divider sx={{ mb: 1.5 }} />

          {/* Rationale + operation_change_summary */}
          {cand.rationale && (
            <Box sx={{ mb: 1.5 }}>
              <Typography variant="overline" color="text.secondary">Why we're asking</Typography>
              <Typography variant="body2">{cand.rationale}</Typography>
            </Box>
          )}
          {cand.operation_change_summary && (
            <Alert severity="info" sx={{ mb: 1.5 }}>
              <Typography variant="caption" fontWeight={700}>Operational impact: </Typography>
              {cand.operation_change_summary}
            </Alert>
          )}
          {cand.source_quote && (
            <Box sx={{ p: 1.25, mb: 1.5, borderRadius: 1, bgcolor: 'rgba(0,0,0,0.03)',
                       borderLeft: `3px solid ${sevColor}`,
                       fontSize: '0.85rem', whiteSpace: 'pre-wrap' }}>
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5 }}>
                Source quote
              </Typography>
              "{cand.source_quote}"
            </Box>
          )}

          {/* Reconciliation context */}
          {cand.replaces_question && (
            <Box sx={{ p: 1.25, mb: 1.5, borderRadius: 1.5,
                       border: `1px dashed ${ACTION_VISUAL.replaces.color}` }}>
              <Typography variant="overline" color="text.secondary">
                Replaces existing question #{cand.replaces_question.id}
              </Typography>
              <Typography variant="body2" sx={{ textDecoration: 'line-through',
                                                  color: 'text.secondary' }}>
                {cand.replaces_question.question_text}
              </Typography>
            </Box>
          )}
          {cand.reconciliation_action === 'updates' && cand.replaces_question_id && (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              <Typography variant="caption" fontWeight={700}>
                Existing question #{cand.replaces_question_id} was edited in place.
              </Typography>{' '}
              The candidate is recorded for audit but the user-facing list
              already reflects the rewritten version.
            </Alert>
          )}

          {/* Diff context */}
          {cand.related_diff_rows && cand.related_diff_rows.length > 0 && (
            <Box sx={{ mb: 1.5 }}>
              <Typography variant="overline" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5 }}>
                Triggered by diff change
              </Typography>
              {cand.related_diff_rows.map((d) => {
                const dsev = SEVERITY_COLORS[d.impact_severity] || SEVERITY_COLORS.medium;
                return (
                  <Box key={d.id} sx={{
                    p: 1.25, borderRadius: 1.5,
                    borderLeft: `4px solid ${dsev}`,
                    bgcolor: `${dsev}10`, mb: 0.5,
                  }}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip size="small" label={d.status}
                            sx={{ height: 18, fontSize: '0.62rem',
                                  bgcolor: dsev, color: '#fff' }} />
                      <Chip size="small" label={d.impact_severity || 'medium'}
                            variant="outlined" sx={{ height: 18, fontSize: '0.62rem' }} />
                    </Stack>
                    {d.change_summary && (
                      <Typography variant="body2" fontWeight={600}>{d.change_summary}</Typography>
                    )}
                    {d.impact_blurb && (
                      <Typography variant="body2" color="text.secondary"
                                  sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                        {d.impact_blurb}
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </Box>
          )}

          {/* Coupling findings — second-order effects on unchanged rows */}
          {cand.coupling_findings && cand.coupling_findings.length > 0 && (
            <Box sx={{ mb: 1.5 }}>
              <Typography variant="overline" color="warning.main"
                          sx={{ display: 'block', mb: 0.5 }}>
                ⚠ Second-order effects on unchanged requirements
              </Typography>
              {cand.coupling_findings.map((cf) => {
                const sev = SEVERITY_COLORS[cf.severity] || SEVERITY_COLORS.medium;
                return (
                  <Box key={cf.id} sx={{
                    p: 1.25, borderRadius: 1.5,
                    borderLeft: `4px dashed ${sev}`,
                    bgcolor: `${sev}08`, mb: 0.5,
                  }}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip size="small" label={cf.coupling_kind?.replace('_', ' ') || 'coupling'}
                            sx={{ height: 18, fontSize: '0.62rem',
                                  bgcolor: sev, color: '#fff' }} />
                      <Chip size="small" label={cf.severity || 'medium'}
                            variant="outlined" sx={{ height: 18, fontSize: '0.62rem' }} />
                      {cf.affected_requirement && (
                        <Typography variant="caption" color="text.secondary">
                          → {cf.affected_requirement.section_id || `req #${cf.affected_requirement.id}`}
                          {cf.affected_requirement.source_page ? ` p.${cf.affected_requirement.source_page}` : ''}
                        </Typography>
                      )}
                    </Stack>
                    <Typography variant="body2" fontWeight={600}>{cf.summary}</Typography>
                    {cf.detail && (
                      <Typography variant="body2" color="text.secondary"
                                  sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                        {cf.detail}
                      </Typography>
                    )}
                    {cf.suggested_question && (
                      <Box sx={{ mt: 0.75, p: 0.75, borderRadius: 1,
                                 bgcolor: 'rgba(255,193,7,0.08)',
                                 borderLeft: '3px solid #ffa000' }}>
                        <Typography variant="caption" fontWeight={700}
                                    color="warning.main"
                                    sx={{ display: 'block', mb: 0.25 }}>
                          Suggested follow-up question
                        </Typography>
                        <Typography variant="body2"
                                    sx={{ fontStyle: 'italic' }}>
                          {cf.suggested_question}
                        </Typography>
                      </Box>
                    )}
                  </Box>
                );
              })}
            </Box>
          )}

          {/* Action row */}
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            <Button size="small" variant="outlined" startIcon={<EditIcon />}
                    onClick={() => setEditOpen(true)} disabled={!!busy}>
              Edit
            </Button>
            <Button size="small" variant="outlined" color="error"
                    startIcon={<RejectIcon />}
                    onClick={() => setRejectOpen(true)}
                    disabled={!!busy || cand.status === 'rejected'}>
              Reject
            </Button>
            <Box sx={{ flex: 1 }} />
            <Button size="small" variant="contained" color="success"
                    startIcon={<AcceptIcon />}
                    onClick={accept}
                    disabled={!!busy || cand.status === 'reviewed'
                              || cand.reconciliation_action === 'duplicate'
                              || cand.reconciliation_action === 'updates'}>
              {cand.status === 'reviewed' ? 'Accepted' : (busy === 'accept' ? 'Accepting…' : 'Accept')}
            </Button>
          </Stack>

          {cand.review_notes && (
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', mt: 1, whiteSpace: 'pre-wrap' }}>
              <strong>Review notes:</strong> {cand.review_notes}
            </Typography>
          )}
        </AccordionDetails>
      </Accordion>

      {/* Edit dialog */}
      <Dialog open={editOpen} onClose={() => setEditOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit candidate question</DialogTitle>
        <DialogContent dividers>
          <TextField label="Question text" multiline minRows={3} fullWidth
                     value={draft.question_text}
                     onChange={(e) => setDraft({ ...draft, question_text: e.target.value })}
                     sx={{ mb: 2 }} />
          <TextField label="Rationale (internal)" multiline minRows={2} fullWidth
                     value={draft.rationale}
                     onChange={(e) => setDraft({ ...draft, rationale: e.target.value })}
                     sx={{ mb: 2 }} />
          <Stack direction="row" spacing={2}>
            <Select size="small" value={draft.category}
                    onChange={(e) => setDraft({ ...draft, category: e.target.value })}
                    sx={{ minWidth: 160 }}>
              {['clarification','risk','pricing','scope','competitive','form'].map(c =>
                <MenuItem key={c} value={c}>{c}</MenuItem>)}
            </Select>
            <Select size="small" value={draft.priority}
                    onChange={(e) => setDraft({ ...draft, priority: e.target.value })}
                    sx={{ minWidth: 140 }}>
              {['critical','high','medium','low'].map(p =>
                <MenuItem key={p} value={p}>{p}</MenuItem>)}
            </Select>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={saveEdit} disabled={busy === 'edit'}>
            {busy === 'edit' ? 'Saving…' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Reject dialog */}
      <Dialog open={rejectOpen} onClose={() => setRejectOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Reject candidate</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" sx={{ mb: 1 }}>
            Optionally explain why so the team has a record.
          </Typography>
          <TextField multiline minRows={2} fullWidth
                     placeholder="e.g. Already covered by Q#42, or out of scope…"
                     value={rejectFeedback}
                     onChange={(e) => setRejectFeedback(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRejectOpen(false)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={submitReject}
                  disabled={busy === 'reject'}>
            {busy === 'reject' ? 'Rejecting…' : 'Reject'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

// ── Page ──────────────────────────────────────────────────────────
export default function DiffQuestionAnalysis() {
  const { proposalId } = useProposal();
  const navigate = useNavigate();
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);
  const [activeRun, setActiveRun] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [filterAction, setFilterAction] = useState(null);
  const [onlyInferences, setOnlyInferences] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [startOpen, setStartOpen] = useState(false);
  const [startForm, setStartForm] = useState({
    baseline_consolidation_run_id: '', // preferred: as-amended baseline
    baseline_proposal_id: '',          // fallback: another proposal
    baseline_document_ids: '',         // fallback: raw doc ids
    label: '',
  });
  const [startBusy, setStartBusy] = useState(false);
  const [consolRuns, setConsolRuns] = useState([]);
  const [consolBusy, setConsolBusy] = useState(false);
  const [consolForm, setConsolForm] = useState({
    master_document_id: '',
    amendment_document_ids: '',
    label: '',
  });
  const [consolStartOpen, setConsolStartOpen] = useState(false);
  const [activeConsolJob, setActiveConsolJob] = useState(null);

  const loadConsolRuns = async () => {
    try {
      const { data } = await axios.get('/api/baseline-consolidation/runs',
        { params: { limit: 50 } });
      setConsolRuns(data.runs || []);
    } catch (e) {
      setConsolRuns([]);
    }
  };
  useEffect(() => { loadConsolRuns(); }, []);
  // Poll an active consolidation job until complete
  useEffect(() => {
    if (!activeConsolJob || activeConsolJob.status === 'complete'
        || activeConsolJob.status === 'failed') return;
    const t = setInterval(async () => {
      try {
        const { data } = await axios.get(
          `/api/baseline-consolidation/runs/${activeConsolJob.id}`);
        setActiveConsolJob(data);
        if (data.status === 'complete' || data.status === 'failed') {
          clearInterval(t);
          loadConsolRuns();
        }
      } catch {
        clearInterval(t);
      }
    }, 3000);
    return () => clearInterval(t);
  }, [activeConsolJob?.id, activeConsolJob?.status]);

  // Render a backend error nicely. FastAPI returns `detail` either as a
  // plain string OR (for Pydantic validation failures) as an array of
  // {loc, msg, type, input} dicts. Naive alert() on that array prints
  // "[object Object]". This stringifies both shapes.
  const formatApiError = (e, fallback) => {
    const detail = e?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          const loc = Array.isArray(d.loc) ? d.loc.slice(1).join('.') : '';
          return `${loc ? loc + ': ' : ''}${d.msg || JSON.stringify(d)}`;
        })
        .join('\n');
    }
    if (detail && typeof detail === 'object') {
      return JSON.stringify(detail);
    }
    return e?.message || fallback || 'Request failed';
  };

  const [suggestBusy, setSuggestBusy] = useState(false);
  const [suggestDetails, setSuggestDetails] = useState(null);

  const suggestOrder = async () => {
    const ids = consolForm.amendment_document_ids
      .split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
    if (!ids.length) {
      alert('Paste amendment document ids (comma-separated integers) first; the suggester will then return them sorted by their actual issuance dates.');
      return;
    }
    setSuggestBusy(true);
    setSuggestDetails(null);
    try {
      const { data } = await axios.post(
        '/api/baseline-consolidation/suggest-order',
        { document_ids: ids, use_llm_fallback: true });
      const orderStr = (data.suggested_order || []).join(', ');
      setConsolForm((f) => ({ ...f, amendment_document_ids: orderStr }));
      setSuggestDetails(data);
    } catch (e) {
      alert(formatApiError(e, 'Suggest-order failed'));
    } finally {
      setSuggestBusy(false);
    }
  };

  const startConsolidation = async () => {
    const masterId = parseInt(consolForm.master_document_id, 10);
    if (isNaN(masterId)) {
      alert('Master document id must be a number (e.g. 4 — the integer ID of the master RFP document, not its name).');
      return;
    }
    const amendIds = consolForm.amendment_document_ids
      .split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
    if (!amendIds.length) {
      alert('Provide at least one amendment document id (comma-separated integers).');
      return;
    }
    setConsolBusy(true);
    try {
      const body = {
        master_document_id: masterId,
        amendment_document_ids: amendIds,
        label: consolForm.label || null,
      };
      const { data } = await axios.post('/api/baseline-consolidation/runs', body);
      setActiveConsolJob(data);
      setConsolStartOpen(false);
      await loadConsolRuns();
    } catch (e) {
      alert(formatApiError(e, 'Consolidation failed to start'));
    } finally {
      setConsolBusy(false);
    }
  };

  const loadRuns = async () => {
    if (!proposalId) return;
    try {
      const { data } = await axios.get('/api/diff-question-analysis/runs',
        { params: { proposal_id: proposalId, limit: 50 } });
      setRuns(data.runs || []);
      if (!activeRunId && data.runs?.length) {
        setActiveRunId(data.runs[0].id);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to list runs');
    }
  };

  const loadActiveRun = async () => {
    if (!activeRunId) { setActiveRun(null); setCandidates([]); setLoading(false); return; }
    setLoading(true);
    try {
      const [r1, r2] = await Promise.all([
        axios.get(`/api/diff-question-analysis/runs/${activeRunId}`),
        axios.get(`/api/diff-question-analysis/runs/${activeRunId}/candidates`,
          { params: { action: filterAction || undefined,
                      only_inferences: onlyInferences } }),
      ]);
      setActiveRun(r1.data);
      setCandidates(r2.data?.candidates || []);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load run');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (proposalId) loadRuns(); /* eslint-disable-next-line */ }, [proposalId]);
  useEffect(() => { loadActiveRun();
                    /* eslint-disable-next-line */ }, [activeRunId, filterAction, onlyInferences]);

  // Poll while running
  useEffect(() => {
    if (!activeRun) return;
    const isRunning = ['pending','diff_running','generating_questions','reconciling']
      .includes(activeRun.status);
    if (!isRunning) return;
    const t = setInterval(loadActiveRun, 2500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRun?.status]);

  const startPipeline = async () => {
    setStartBusy(true);
    try {
      const body = {
        proposal_id: proposalId,
        label: startForm.label || null,
      };
      if (startForm.baseline_consolidation_run_id) {
        body.baseline_consolidation_run_id =
          parseInt(startForm.baseline_consolidation_run_id, 10);
      } else if (startForm.baseline_document_ids.trim()) {
        body.baseline_document_ids = startForm.baseline_document_ids
          .split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
      } else if (startForm.baseline_proposal_id) {
        body.baseline_proposal_id = parseInt(startForm.baseline_proposal_id, 10);
      } else {
        alert('Pick a consolidation run, a baseline proposal id, or paste comma-separated document ids');
        setStartBusy(false);
        return;
      }
      const { data } = await axios.post('/api/diff-question-analysis/runs', body);
      setStartOpen(false);
      setActiveRunId(data.id);
      await loadRuns();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Failed to start pipeline');
    } finally {
      setStartBusy(false);
    }
  };

  const counts = useMemo(() => {
    const c = { added: 0, replaces: 0, updates: 0, duplicate: 0,
                pending: 0, accepted: 0, rejected: 0, inferences: 0 };
    for (const cand of candidates) {
      c[cand.reconciliation_action] = (c[cand.reconciliation_action] || 0) + 1;
      if (cand.inference_flag) c.inferences++;
      if (cand.status === 'reviewed') c.accepted++;
      else if (cand.status === 'rejected') c.rejected++;
      else c.pending++;
    }
    return c;
  }, [candidates]);

  if (!proposalId) return <Alert severity="info" sx={{ m: 3 }}>Pick a proposal first.</Alert>;

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 3 }}>
        <IconButton onClick={() => navigate(`/p/${proposalId}/dashboard`)}>
          <BackIcon />
        </IconButton>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h4" fontWeight={800}>Diff → Question Analysis</Typography>
          <Typography variant="body2" color="text.secondary">
            After re-extraction: re-runs the requirement diff, generates questions
            grounded in current operations, and reconciles them with existing
            questions (add / replace / update / duplicate).
          </Typography>
        </Box>
        <Tooltip title="Refresh">
          <IconButton onClick={() => { loadRuns(); loadActiveRun(); }}><RefreshIcon /></IconButton>
        </Tooltip>
        <Button variant="contained" startIcon={<RunIcon />}
                onClick={() => setStartOpen(true)}>
          Run pipeline
        </Button>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      <Box sx={{ display: 'flex', gap: 2 }}>
        {/* Run history sidebar */}
        <Box sx={{ width: 280, flexShrink: 0 }}>
          <Card variant="outlined" sx={{ borderRadius: 2 }}>
            <CardContent sx={{ pb: 1 }}>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
                <HistoryIcon fontSize="small" />
                <Typography variant="overline" color="text.secondary">
                  Run history
                </Typography>
              </Stack>
              {!runs.length ? (
                <Typography variant="body2" color="text.secondary"
                            sx={{ textAlign: 'center', py: 2 }}>
                  No runs yet.<br />Click <strong>Run pipeline</strong> above.
                </Typography>
              ) : (
                <Stack spacing={0.75} sx={{ maxHeight: 'calc(100vh - 240px)',
                                            overflowY: 'auto', pr: 0.5 }}>
                  {runs.map((r) => {
                    const sc = STATUS_COLORS[r.status] || '#9e9e9e';
                    const isActive = r.id === activeRunId;
                    return (
                      <Box key={r.id}
                           onClick={() => setActiveRunId(r.id)}
                           sx={{
                             p: 1.25, borderRadius: 1.5, cursor: 'pointer',
                             border: '1px solid',
                             borderColor: isActive ? '#00AEE6' : 'rgba(0,0,0,0.08)',
                             bgcolor: isActive ? 'rgba(0,174,230,0.06)' : 'transparent',
                             '&:hover': { borderColor: '#00AEE6' },
                           }}>
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: sc }} />
                          <Typography variant="body2" fontWeight={isActive ? 700 : 500}
                                      noWrap
                                      sx={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            #{r.id} {r.label || ''}
                          </Typography>
                        </Stack>
                        <Typography variant="caption" color="text.secondary"
                                    sx={{ display: 'block', mt: 0.25 }}>
                          {r.status} · {r.candidates_generated} candidate{r.candidates_generated === 1 ? '' : 's'}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {r.created_at && new Date(r.created_at).toLocaleString()}
                        </Typography>
                      </Box>
                    );
                  })}
                </Stack>
              )}
            </CardContent>
          </Card>
        </Box>

        {/* Main: active run + candidates */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {!activeRun ? (
            <Alert severity="info">
              Select a run on the left, or click <strong>Run pipeline</strong> to start one.
            </Alert>
          ) : (
            <>
              {/* Run header card */}
              <Card sx={{ mb: 2, borderRadius: 2,
                          background: 'linear-gradient(135deg, #fff, rgba(0,174,230,0.04))',
                          border: '1px solid rgba(0,174,230,0.2)' }}>
                <CardContent>
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
                    <Typography variant="h6" fontWeight={800} sx={{ flex: 1 }}>
                      Run #{activeRun.id} — {activeRun.label || 'untitled'}
                    </Typography>
                    <Chip size="small" label={activeRun.status}
                          sx={{ bgcolor: STATUS_COLORS[activeRun.status] || '#9e9e9e',
                                color: '#fff', fontWeight: 700 }} />
                  </Stack>
                  {activeRun.error && (
                    <Alert severity="error" sx={{ mb: 1.5 }}>
                      Pipeline error: {activeRun.error}
                    </Alert>
                  )}
                  <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: 'wrap' }} useFlexGap>
                    <StagePill run={activeRun} stageKey="diff" label="1. Diff" />
                    <Box sx={{ alignSelf: 'center' }}>→</Box>
                    <StagePill run={activeRun} stageKey="generation" label="2. Generate questions" />
                    <Box sx={{ alignSelf: 'center' }}>→</Box>
                    <StagePill run={activeRun} stageKey="reconcile" label="3. Reconcile with existing" />
                  </Stack>
                  {activeRun.progress_note && (
                    <Typography variant="caption" color="text.secondary"
                                sx={{ display: 'block', mb: 1 }}>
                      {activeRun.progress_note}
                    </Typography>
                  )}
                  {['pending','diff_running','generating_questions','reconciling']
                    .includes(activeRun.status) && (
                    <LinearProgress sx={{ height: 4, borderRadius: 2, mb: 1 }} />
                  )}

                  {/* Counts */}
                  <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: 'wrap' }} useFlexGap>
                    <Chip size="small" label={`${activeRun.candidates_generated || 0} candidates`} />
                    <Chip size="small" label={`${activeRun.inference_count || 0} inferences`}
                          color={activeRun.inference_count > 0 ? 'warning' : 'default'}
                          icon={activeRun.inference_count > 0 ? <InferenceIcon /> : undefined} />
                    <Chip size="small" label={`add: ${activeRun.reconciled_added || 0}`}
                          sx={{ bgcolor: ACTION_VISUAL.added.color, color: '#fff' }} />
                    <Chip size="small" label={`replaces: ${activeRun.reconciled_replaces || 0}`}
                          sx={{ bgcolor: ACTION_VISUAL.replaces.color, color: '#fff' }} />
                    <Chip size="small" label={`updates: ${activeRun.reconciled_updates || 0}`}
                          sx={{ bgcolor: ACTION_VISUAL.updates.color, color: '#fff' }} />
                    <Chip size="small" label={`duplicates: ${activeRun.reconciled_duplicates || 0}`}
                          sx={{ bgcolor: ACTION_VISUAL.duplicate.color, color: '#fff' }} />
                  </Stack>
                </CardContent>
              </Card>

              {/* Filters */}
              {activeRun.status === 'complete' && (
                <Stack direction="row" spacing={2} sx={{ mb: 2 }} alignItems="center">
                  <Typography variant="overline" color="text.secondary">Filter</Typography>
                  <ToggleButtonGroup
                    size="small"
                    value={filterAction}
                    exclusive
                    onChange={(_, v) => setFilterAction(v)}
                  >
                    <ToggleButton value={null}>All</ToggleButton>
                    <ToggleButton value="added">Added ({counts.added})</ToggleButton>
                    <ToggleButton value="replaces">Replaces ({counts.replaces})</ToggleButton>
                    <ToggleButton value="updates">Updates ({counts.updates})</ToggleButton>
                    <ToggleButton value="duplicate">Duplicates ({counts.duplicate})</ToggleButton>
                  </ToggleButtonGroup>
                  <FormControlLabel
                    control={<Switch size="small" checked={onlyInferences}
                                     onChange={(e) => setOnlyInferences(e.target.checked)} />}
                    label="Only inferences"
                  />
                  <Box sx={{ flex: 1 }} />
                  <Typography variant="caption" color="text.secondary">
                    {counts.accepted} accepted · {counts.pending} pending · {counts.rejected} rejected
                  </Typography>
                </Stack>
              )}

              {/* Candidates */}
              {loading ? (
                <Box sx={{ p: 5, textAlign: 'center' }}><CircularProgress /></Box>
              ) : candidates.length === 0 ? (
                <Alert severity="info">
                  {activeRun.status === 'complete'
                    ? 'No candidate questions match the current filter.'
                    : 'Pipeline is running — candidates will appear as they are generated.'}
                </Alert>
              ) : (
                <Box>
                  {candidates.map(cand => (
                    <CandidateCard key={cand.id} cand={cand} onChanged={loadActiveRun} />
                  ))}
                </Box>
              )}
            </>
          )}
        </Box>
      </Box>

      {/* Start pipeline dialog */}
      <Dialog open={startOpen} onClose={() => setStartOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Run diff → question analysis pipeline</DialogTitle>
        <DialogContent dividers>
          <Alert severity="success" sx={{ mb: 2 }}
                 action={
                   <Button size="small" color="inherit"
                           onClick={() => setConsolStartOpen(true)}>
                     New consolidation
                   </Button>
                 }>
            <strong>Recommended:</strong> use a consolidated baseline so the
            diff compares the new RFP against the prior solicitation
            <em> as it was actually amended</em> (master + every amendment
            applied). This is the apples-to-apples option.
          </Alert>

          <Typography variant="overline" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Baseline source
          </Typography>
          <Select
            size="small" fullWidth displayEmpty
            value={startForm.baseline_consolidation_run_id}
            onChange={(e) => setStartForm({ ...startForm,
              baseline_consolidation_run_id: e.target.value,
              baseline_document_ids: '',
              baseline_proposal_id: '',
            })}
            sx={{ mb: 2, mt: 0.5 }}
          >
            <MenuItem value=""><em>— pick a consolidation run, or fall back below —</em></MenuItem>
            {consolRuns
              .filter(r => r.status === 'complete')
              .map(r => (
                <MenuItem key={r.id} value={r.id}>
                  #{r.id} {r.label || `master=${r.master_document_id} + ${r.amendment_document_ids.length} amendment(s)`}
                  {' '}— {r.actions_modified} mod, {r.actions_added} add, {r.actions_removed} rm
                </MenuItem>
              ))
            }
          </Select>

          {!startForm.baseline_consolidation_run_id && (
            <>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                Or fall back to a raw baseline source:
              </Typography>
              <TextField label="Baseline proposal id" fullWidth size="small"
                         value={startForm.baseline_proposal_id}
                         onChange={(e) => setStartForm({ ...startForm, baseline_proposal_id: e.target.value })}
                         sx={{ mb: 2 }} />
              <TextField label="OR baseline document ids (comma-separated)" fullWidth size="small"
                         placeholder="4, 14, 15, 16, …"
                         value={startForm.baseline_document_ids}
                         onChange={(e) => setStartForm({ ...startForm, baseline_document_ids: e.target.value })}
                         sx={{ mb: 2 }} />
            </>
          )}
          <TextField label="Label (optional)" fullWidth size="small"
                     value={startForm.label}
                     onChange={(e) => setStartForm({ ...startForm, label: e.target.value })} />
          <Alert severity="info" sx={{ mt: 2 }}>
            Runs three stages: requirement diff → LLM question generation
            grounded in current Parsons operations → reconciliation against
            existing questions. Expect a few minutes per 100 changed rows.
          </Alert>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setStartOpen(false)}>Cancel</Button>
          <Button variant="contained" startIcon={<RunIcon />}
                  onClick={startPipeline} disabled={startBusy}>
            {startBusy ? 'Starting…' : 'Start'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Consolidation start dialog */}
      <Dialog open={consolStartOpen} onClose={() => setConsolStartOpen(false)}
              maxWidth="sm" fullWidth>
        <DialogTitle>Build a consolidated as-amended baseline</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" sx={{ mb: 2 }}>
            Pick the master RFP document and the chronologically-ordered
            amendments. The agent will: (1) snapshot every requirement from
            the master; (2) for each amendment, decide modify / add / remove /
            skip per item; (3) produce an as-amended baseline that the diff
            can use apples-to-apples.
          </Typography>
          <TextField label="Master document id" fullWidth size="small"
                     placeholder="e.g. 4 (the original 2021 Bid Solicitation)"
                     value={consolForm.master_document_id}
                     onChange={(e) => setConsolForm({ ...consolForm, master_document_id: e.target.value })}
                     sx={{ mb: 2 }} />
          <TextField label="Amendment document ids (comma-separated, in date order)" fullWidth size="small"
                     placeholder="14, 20, 21, 22, 17, 23, 24, 25, 26, 27, 18, 19, 28, 31, 30, 32, 33, 34, 35, 36, 37, 15, 16, 29"
                     value={consolForm.amendment_document_ids}
                     onChange={(e) => setConsolForm({ ...consolForm, amendment_document_ids: e.target.value })}
                     sx={{ mb: 1 }} />
          <Stack direction="row" spacing={1} sx={{ mb: 2 }} alignItems="center">
            <Button size="small" variant="outlined"
                    onClick={suggestOrder} disabled={suggestBusy || !consolForm.amendment_document_ids.trim()}>
              {suggestBusy ? 'Reading dates from each document…' : 'Auto-detect chronological order'}
            </Button>
            <Typography variant="caption" color="text.secondary">
              Reads each doc's actual issuance date (body text + filename + LLM fallback).
            </Typography>
          </Stack>
          {suggestDetails && (
            <Alert severity={suggestDetails.undated_count > 0 ? 'warning' : 'success'}
                   sx={{ mb: 2 }} onClose={() => setSuggestDetails(null)}>
              <Typography variant="caption" fontWeight={700}>
                {suggestDetails.dated_count} of {suggestDetails.input_count} amendments dated{' '}
                {suggestDetails.undated_count > 0 && `(${suggestDetails.undated_count} undated — kept at end of input order)`}
              </Typography>
              <Box sx={{ maxHeight: 220, overflowY: 'auto', mt: 0.75,
                          fontSize: '0.75rem', fontFamily: 'monospace' }}>
                {suggestDetails.details.map((d, i) => (
                  <Box key={d.document_id} sx={{
                    display: 'flex', gap: 1, py: 0.25,
                    color: d.date ? 'text.primary' : 'warning.main',
                  }}>
                    <span>{String(i + 1).padStart(2, ' ')}.</span>
                    <span>doc {String(d.document_id).padStart(3, ' ')}</span>
                    <span>{d.date || '(no date)'}</span>
                    <span style={{ color: '#888' }}>{d.source}</span>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(d.filename || '').slice(0, 50)}
                    </span>
                  </Box>
                ))}
              </Box>
            </Alert>
          )}
          <TextField label="Label (optional)" fullWidth size="small"
                     value={consolForm.label}
                     onChange={(e) => setConsolForm({ ...consolForm, label: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConsolStartOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={startConsolidation}
                  disabled={consolBusy} startIcon={<RunIcon />}>
            {consolBusy ? 'Starting…' : 'Start consolidation'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Consolidation progress banner */}
      {activeConsolJob && (
        <Box sx={{
          position: 'fixed', bottom: 16, right: 16, zIndex: 2000,
          minWidth: 380, maxWidth: 480, p: 2, borderRadius: 2,
          bgcolor: '#fff', boxShadow: 4,
          border: `1px solid ${
            activeConsolJob.status === 'complete' ? '#2e7d32'
              : activeConsolJob.status === 'failed' ? '#c62828'
              : '#0288d1'}40`,
        }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
            <Typography variant="overline" color="text.secondary" sx={{ flex: 1 }}>
              Consolidation #{activeConsolJob.id}
            </Typography>
            <Chip size="small" label={activeConsolJob.status}
                  sx={{ height: 20,
                        bgcolor: activeConsolJob.status === 'complete' ? '#2e7d32'
                          : activeConsolJob.status === 'failed' ? '#c62828' : '#0288d1',
                        color: '#fff' }} />
            <Button size="small" onClick={() => setActiveConsolJob(null)}>×</Button>
          </Stack>
          <Typography variant="caption" color="text.secondary"
                      sx={{ display: 'block', mb: 1 }}>
            {activeConsolJob.progress_note || '—'}
          </Typography>
          {(activeConsolJob.status === 'running' || activeConsolJob.status === 'pending') && (
            <LinearProgress sx={{ height: 4, borderRadius: 2 }} />
          )}
          {activeConsolJob.status === 'complete' && (
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              <Chip size="small" label={`${activeConsolJob.total_master_requirements} master`} />
              <Chip size="small" label={`${activeConsolJob.actions_modified} mod`} />
              <Chip size="small" label={`${activeConsolJob.actions_added} add`} />
              <Chip size="small" label={`${activeConsolJob.actions_removed} rm`} />
              <Chip size="small" label={`${activeConsolJob.actions_skipped} skip`} />
            </Stack>
          )}
        </Box>
      )}
    </Box>
  );
}
