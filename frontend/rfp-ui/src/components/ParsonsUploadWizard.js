/**
 * ParsonsUploadWizard
 *
 * 4-step guided upload flow for Parsons knowledge documents:
 *
 *   Step 1 — File + Metadata     (pick file, category, scope, etc.)
 *   Step 2 — Supersession        (review predecessor candidates,
 *                                  pick any that this upload replaces)
 *   Step 3 — Pipeline Options    (choose: re-run coverage? classify?)
 *   Step 4 — Live Progress       (timeline showing each pipeline stage
 *                                  status in real time, with summary)
 *
 * On completion the user can review what changed (counts of reqs
 * promoted out of "gap", suggestions generated, etc.) and either close
 * the wizard or upload another doc. Cancelling mid-wizard does NOT
 * delete the partially-uploaded doc — it's left in the DB with whatever
 * stages completed (the user can re-trigger from the doc detail page).
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  Box, Typography, Button, Stack, TextField, MenuItem,
  Stepper, Step, StepLabel, StepContent,
  Alert, LinearProgress, CircularProgress,
  Card, CardContent, Chip, Checkbox, IconButton, Tooltip,
  Switch, FormControlLabel, Divider, List, ListItem, ListItemText,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  CheckCircle as CheckIcon,
  Error as ErrorIcon,
  HourglassEmpty as PendingIcon,
  Refresh as RefreshIcon,
  History as HistoryIcon,
  AutoAwesome as MagicIcon,
  Description as DocIcon,
  Layers as StageIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import axios from 'axios';

// ── Stage metadata for the live timeline ────────────────────────────
const STAGES = [
  { key: 'parsing',       label: 'Parsing & chunking',
    desc: 'Extracting text from the file and splitting into chunks for retrieval.' },
  { key: 'embedding',     label: 'Generating embeddings',
    desc: 'Building vector embeddings + section tags so the AI can find this content during response writing.' },
  { key: 'quality',       label: 'Quality assessment',
    desc: 'Scoring how well this document substantiates Parsons capabilities and suggesting improvements.' },
  { key: 'supersession',  label: 'Supersession',
    desc: 'Marking any predecessor documents as replaced so the AI cites only the current version.' },
  { key: 'coverage',      label: 'Coverage re-assessment',
    desc: 'Re-evaluating open-gap requirements — does this new document close any of them?' },
  { key: 'classify',      label: 'Disposition classification',
    desc: 'Tagging newly-affected requirements as evidence-required vs commitment-only vs acknowledgment-only.' },
];

function stageStatus(stage) {
  if (!stage) return 'pending';
  if (stage.completed_at) return 'complete';
  if (stage.started_at) return 'running';
  return 'pending';
}

function stageColor(status) {
  return status === 'complete' ? '#2e7d32'
       : status === 'running' ? '#0288d1'
       : '#bdbdbd';
}

function StageIconFor({ status }) {
  if (status === 'complete') return <CheckIcon sx={{ color: '#2e7d32', fontSize: 22 }} />;
  if (status === 'running') return <CircularProgress size={18} />;
  return <PendingIcon sx={{ color: '#bdbdbd', fontSize: 22 }} />;
}

function durationStr(start, end) {
  if (!start) return '';
  const a = new Date(start).getTime();
  const b = end ? new Date(end).getTime() : Date.now();
  const ms = b - a;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}


// ─────────────────────────────────────────────────────────────────────
// Step 4 — Live progress timeline
// ─────────────────────────────────────────────────────────────────────

function ProgressTimeline({ job, error }) {
  if (!job) {
    return (
      <Stack alignItems="center" spacing={1} sx={{ py: 4 }}>
        <CircularProgress />
        <Typography variant="caption" color="text.secondary">
          Initializing pipeline...
        </Typography>
      </Stack>
    );
  }
  return (
    <Box sx={{ width: '100%' }}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {job.error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Pipeline failed at <strong>{job.current_stage}</strong>: {job.error}
        </Alert>
      )}
      {job.progress_note && job.status === 'running' && (
        <Alert severity="info" sx={{ mb: 2 }} icon={<MagicIcon />}>
          {job.progress_note}
        </Alert>
      )}
      <Box sx={{ position: 'relative', pl: 0 }}>
        {STAGES.map((s, idx) => {
          const stage = job.stages?.[s.key];
          const status = stageStatus(stage);
          const isLast = idx === STAGES.length - 1;
          const color = stageColor(status);
          return (
            <Box key={s.key} sx={{ display: 'flex', mb: isLast ? 0 : 2 }}>
              {/* Vertical line + icon */}
              <Box sx={{ display: 'flex', flexDirection: 'column',
                          alignItems: 'center', mr: 2 }}>
                <Box sx={{
                  width: 32, height: 32, borderRadius: '50%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  bgcolor: status === 'complete' ? 'rgba(46,125,50,0.1)'
                         : status === 'running' ? 'rgba(2,136,209,0.1)'
                         : 'rgba(0,0,0,0.04)',
                  border: `2px solid ${color}`,
                  flexShrink: 0,
                }}>
                  <StageIconFor status={status} />
                </Box>
                {!isLast && <Box sx={{ flex: 1, width: 2,
                                        bgcolor: status === 'complete' ? '#2e7d32' : '#e0e0e0',
                                        my: 0.5, minHeight: 30 }} />}
              </Box>
              {/* Content */}
              <Box sx={{ flex: 1, pb: 1.5 }}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.25 }}>
                  <Typography variant="body2" fontWeight={700} sx={{ color }}>
                    {s.label}
                  </Typography>
                  {status === 'complete' && stage?.started_at && (
                    <Chip size="small" label={durationStr(stage.started_at, stage.completed_at)}
                          sx={{ height: 18, fontSize: '0.65rem' }} />
                  )}
                  {status === 'running' && (
                    <Chip size="small" label="running" color="info"
                          sx={{ height: 18, fontSize: '0.65rem' }} />
                  )}
                </Stack>
                <Typography variant="caption" color="text.secondary"
                            sx={{ display: 'block', lineHeight: 1.4 }}>
                  {s.desc}
                </Typography>
              </Box>
            </Box>
          );
        })}
      </Box>

      {/* Summary card on completion */}
      {job.status === 'complete' && (
        <Card variant="outlined" sx={{ mt: 3, borderColor: '#2e7d32', bgcolor: 'rgba(46,125,50,0.04)' }}>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <CheckIcon sx={{ color: '#2e7d32' }} />
              <Typography variant="h6" fontWeight={700} sx={{ color: '#2e7d32' }}>
                All done!
              </Typography>
            </Stack>
            <Stack spacing={0.5}>
              {job.summary?.coverage_reqs_reassessed > 0 && (
                <Typography variant="body2">
                  • Re-assessed <strong>{job.summary.coverage_reqs_reassessed}</strong> open-gap
                  requirement{job.summary.coverage_reqs_reassessed === 1 ? '' : 's'}.
                </Typography>
              )}
              {job.summary?.coverage_reqs_promoted > 0 && (
                <Typography variant="body2" sx={{ color: '#2e7d32' }}>
                  • <strong>{job.summary.coverage_reqs_promoted}</strong>
                  {' '}requirement{job.summary.coverage_reqs_promoted === 1 ? '' : 's'} moved
                  out of "gap" thanks to this document.
                </Typography>
              )}
              {job.summary?.classify_reqs_classified > 0 && (
                <Typography variant="body2">
                  • Classified <strong>{job.summary.classify_reqs_classified}</strong>
                  {' '}requirement{job.summary.classify_reqs_classified === 1 ? '' : 's'} by response disposition.
                </Typography>
              )}
              {job.summary?.superseded_doc_ids?.length > 0 && (
                <Typography variant="body2">
                  • Superseded <strong>{job.summary.superseded_doc_ids.length}</strong>
                  {' '}predecessor document{job.summary.superseded_doc_ids.length === 1 ? '' : 's'}.
                </Typography>
              )}
              {!job.summary?.coverage_reqs_reassessed && !job.summary?.classify_reqs_classified
                && !job.summary?.superseded_doc_ids?.length && (
                <Typography variant="body2" color="text.secondary">
                  Document is now indexed and available to the response writer.
                </Typography>
              )}
            </Stack>
          </CardContent>
        </Card>
      )}
    </Box>
  );
}


// ─────────────────────────────────────────────────────────────────────
// Step 2 — Supersession picker
// ─────────────────────────────────────────────────────────────────────

function SupersessionPicker({ category, filename, practiceArea,
                                selectedIds, setSelectedIds,
                                supersessionReason, setSupersessionReason }) {
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!category) return;
    setLoading(true);
    axios.get('/api/parsons-knowledge/upload-wizard/predecessors', {
      params: { parsons_category: category, new_doc_filename: filename || '',
                 practice_area: practiceArea || undefined },
    }).then(r => setCandidates(r.data.candidates || []))
      .catch(() => setCandidates([]))
      .finally(() => setLoading(false));
  }, [category, filename, practiceArea]);

  const toggle = (id) => {
    setSelectedIds(ids => ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id]);
  };

  if (loading) return <LinearProgress />;
  if (candidates.length === 0) {
    return (
      <Alert severity="info" icon={<HistoryIcon />}>
        No existing documents in this category. Nothing to supersede.
      </Alert>
    );
  }

  const likely = candidates.filter(c => c.is_likely_predecessor);
  const others = candidates.filter(c => !c.is_likely_predecessor);

  return (
    <Stack spacing={2}>
      <Alert severity="info" icon={<HistoryIcon />}>
        <strong>Does this new document replace an existing one?</strong> Check
        any predecessors below. Superseded documents stay in the system for
        audit, but the AI will cite the current version when writing
        responses.
      </Alert>

      {likely.length > 0 && (
        <>
          <Typography variant="overline" color="text.secondary">
            Likely predecessors ({likely.length})
          </Typography>
          {likely.map(c => (
            <CandidateRow key={c.id} c={c}
                          checked={selectedIds.includes(c.id)}
                          onToggle={() => toggle(c.id)} />
          ))}
        </>
      )}
      {others.length > 0 && (
        <>
          <Divider sx={{ my: 1 }} />
          <Typography variant="overline" color="text.secondary">
            Other docs in this category ({others.length})
          </Typography>
          {others.map(c => (
            <CandidateRow key={c.id} c={c}
                          checked={selectedIds.includes(c.id)}
                          onToggle={() => toggle(c.id)} />
          ))}
        </>
      )}

      {selectedIds.length > 0 && (
        <TextField
          label={`Supersession reason (${selectedIds.length} doc${selectedIds.length === 1 ? '' : 's'})`}
          placeholder="Optional — e.g. 'New version with updated 2026 staffing rates'"
          multiline minRows={2} fullWidth
          value={supersessionReason}
          onChange={e => setSupersessionReason(e.target.value)}
        />
      )}
    </Stack>
  );
}

function CandidateRow({ c, checked, onToggle }) {
  return (
    <Card variant="outlined" sx={{
      borderColor: checked ? '#7b1fa2' : 'rgba(0,0,0,0.12)',
      bgcolor: checked ? 'rgba(123,31,162,0.04)' : 'background.paper',
      cursor: 'pointer',
      '&:hover': { borderColor: checked ? '#7b1fa2' : 'rgba(0,174,230,0.4)' },
    }} onClick={onToggle}>
      <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.5}>
          <Checkbox checked={checked} onChange={onToggle} sx={{ p: 0.5, mt: -0.5 }} />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <DocIcon fontSize="small" sx={{ color: 'text.secondary' }} />
              <Typography variant="body2" fontWeight={600} sx={{ wordBreak: 'break-word' }}>
                {c.filename}
              </Typography>
              {c.is_likely_predecessor && (
                <Chip size="small" label="likely match" color="warning"
                      sx={{ height: 18, fontSize: '0.65rem' }} />
              )}
            </Stack>
            <Stack direction="row" spacing={1} sx={{ mt: 0.5 }} flexWrap="wrap" useFlexGap>
              {c.practice_area && (
                <Chip size="small" variant="outlined" label={c.practice_area}
                      sx={{ height: 18, fontSize: '0.62rem' }} />
              )}
              {c.quality_score != null && (
                <Chip size="small" label={`Quality: ${c.quality_score}/100`}
                      sx={{ height: 18, fontSize: '0.62rem',
                            bgcolor: c.quality_score >= 70 ? 'rgba(46,125,50,0.1)'
                                   : c.quality_score >= 40 ? 'rgba(237,108,2,0.1)'
                                   : 'rgba(198,40,40,0.1)' }} />
              )}
              {c.total_chunks > 0 && (
                <Chip size="small" variant="outlined" label={`${c.total_chunks} chunks`}
                      sx={{ height: 18, fontSize: '0.62rem' }} />
              )}
              {c.created_at && (
                <Typography variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
                  uploaded {new Date(c.created_at).toLocaleDateString()}
                </Typography>
              )}
            </Stack>
          </Box>
        </Stack>
      </CardContent>
    </Card>
  );
}


// ─────────────────────────────────────────────────────────────────────
// Main wizard component
// ─────────────────────────────────────────────────────────────────────

export default function ParsonsUploadWizard({
  open, onClose, categories, scopeProposalId, onComplete,
}) {
  const [activeStep, setActiveStep] = useState(0);

  // Step 1 form
  const [file, setFile] = useState(null);
  const [parsonsCategory, setParsonsCategory] = useState('past_proposal');
  const [description, setDescription] = useState('');
  const [practiceArea, setPracticeArea] = useState('');
  const [jurisdictions, setJurisdictions] = useState('');

  // Step 2
  const [supersededIds, setSupersededIds] = useState([]);
  const [supersessionReason, setSupersessionReason] = useState('');

  // Step 3
  const [rerunCoverage, setRerunCoverage] = useState(true);
  const [runClassifier, setRunClassifier] = useState(true);

  // Step 4 (live progress)
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [job, setJob] = useState(null);

  const reset = () => {
    setActiveStep(0);
    setFile(null); setParsonsCategory('past_proposal');
    setDescription(''); setPracticeArea(''); setJurisdictions('');
    setSupersededIds([]); setSupersessionReason('');
    setRerunCoverage(true); setRunClassifier(true);
    setSubmitting(false); setError(null); setJob(null);
  };

  const close = () => {
    if (submitting && job?.status === 'running') {
      // Don't allow closing mid-run; pipeline keeps going either way
      // but the user might think cancel reverted things.
      if (!window.confirm(
        'Pipeline is still running in the background. Close anyway? '
        + 'It will continue and you can check the Recent Uploads list later.')) {
        return;
      }
    }
    reset();
    onClose && onClose();
  };

  // Poll job status while running
  useEffect(() => {
    if (!job?.id) return;
    if (job.status === 'complete' || job.status === 'failed') return;
    const t = setInterval(async () => {
      try {
        const { data } = await axios.get(
          `/api/parsons-knowledge/upload-wizard/jobs/${job.id}`);
        setJob(data);
        if (data.status === 'complete' || data.status === 'failed') {
          clearInterval(t);
          if (data.status === 'complete') {
            onComplete && onComplete(data);
          }
        }
      } catch (e) {
        console.warn('poll failed:', e);
      }
    }, 2000);
    return () => clearInterval(t);
  }, [job?.id, job?.status, onComplete]);

  const submit = async () => {
    if (!file) return;
    setSubmitting(true); setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('parsons_category', parsonsCategory);
      if (description.trim()) fd.append('description', description.trim());
      if (practiceArea.trim()) fd.append('practice_area', practiceArea.trim());
      const jurList = jurisdictions.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
      if (jurList.length) fd.append('jurisdictions', JSON.stringify(jurList));
      if (scopeProposalId) fd.append('scope_proposal_id', String(scopeProposalId));
      if (supersededIds.length) {
        fd.append('superseded_doc_ids', JSON.stringify(supersededIds));
      }
      if (supersessionReason.trim()) fd.append('supersession_reason', supersessionReason.trim());
      fd.append('rerun_coverage', String(rerunCoverage));
      fd.append('run_classifier', String(runClassifier));

      const { data } = await axios.post(
        '/api/parsons-knowledge/upload-wizard/start', fd,
        { headers: { 'Content-Type': 'multipart/form-data' } });
      setJob({ id: data.job_id, document_id: data.document_id,
                status: 'running', stages: {} });
      setActiveStep(3);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Upload failed');
      setSubmitting(false);
    }
  };

  // Step navigation
  const canAdvanceFromStep1 = !!file && !!parsonsCategory;
  const canAdvanceFromStep2 = true; // supersession is optional
  const canAdvanceFromStep3 = true;

  return (
    <Dialog open={open} onClose={close} maxWidth="md" fullWidth
            PaperProps={{ sx: { minHeight: 540 } }}>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <UploadIcon color="primary" />
        Upload Parsons Knowledge Document
        {scopeProposalId ? (
          <Chip size="small" label="scoped to this RFP" color="info" sx={{ ml: 1 }} />
        ) : (
          <Chip size="small" label="global library" sx={{ ml: 1 }} />
        )}
        <Box sx={{ flex: 1 }} />
        <IconButton onClick={close} size="small"><CloseIcon /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        <Stepper activeStep={activeStep} orientation="vertical">
          {/* Step 1 — File + metadata */}
          <Step>
            <StepLabel>File & metadata</StepLabel>
            <StepContent>
              <Stack spacing={2} sx={{ pt: 1 }}>
                {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
                <Box sx={{
                  border: '2px dashed rgba(0,174,230,0.4)', borderRadius: 2,
                  p: 3, textAlign: 'center',
                  bgcolor: file ? 'rgba(46,125,50,0.04)' : 'rgba(0,174,230,0.04)',
                }}>
                  {file ? (
                    <Stack spacing={0.5} alignItems="center">
                      <CheckIcon color="success" />
                      <Typography variant="body2" fontWeight={700}>{file.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {(file.size / 1024 / 1024).toFixed(2)} MB
                      </Typography>
                      <Button size="small" onClick={() => setFile(null)}>Replace</Button>
                    </Stack>
                  ) : (
                    <>
                      <UploadIcon sx={{ fontSize: 36, color: 'rgba(0,174,230,0.6)', mb: 1 }} />
                      <Typography variant="body2" sx={{ mb: 1 }}>
                        Drop file or click to browse
                      </Typography>
                      <Button variant="outlined" component="label" startIcon={<UploadIcon />}>
                        Choose file
                        <input
                          type="file" hidden
                          accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx,.xls,.pptx,.ppt"
                          onChange={(e) => setFile(e.target.files?.[0] || null)}
                        />
                      </Button>
                    </>
                  )}
                </Box>
                <TextField
                  select fullWidth label="Category" required
                  value={parsonsCategory}
                  onChange={(e) => setParsonsCategory(e.target.value)}
                >
                  {(categories || []).map(c => (
                    <MenuItem key={c.slug} value={c.slug}>{c.label}</MenuItem>
                  ))}
                </TextField>
                <TextField
                  fullWidth label="Description"
                  value={description} onChange={(e) => setDescription(e.target.value)}
                  placeholder="Short description — why this doc matters for capture"
                  multiline rows={2}
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                  <TextField
                    fullWidth label="Practice area"
                    value={practiceArea} onChange={(e) => setPracticeArea(e.target.value)}
                    placeholder="e.g. vehicle inspection"
                  />
                  <TextField
                    fullWidth label="Jurisdictions"
                    value={jurisdictions} onChange={(e) => setJurisdictions(e.target.value)}
                    placeholder="NJ, MD, AZ"
                    helperText="Comma-separated"
                  />
                </Stack>
                <Box>
                  <Button variant="contained" onClick={() => setActiveStep(1)}
                          disabled={!canAdvanceFromStep1}>
                    Next
                  </Button>
                </Box>
              </Stack>
            </StepContent>
          </Step>

          {/* Step 2 — Supersession */}
          <Step>
            <StepLabel>Replaces existing document?</StepLabel>
            <StepContent>
              <Box sx={{ pt: 1 }}>
                <SupersessionPicker
                  category={parsonsCategory}
                  filename={file?.name || ''}
                  practiceArea={practiceArea}
                  selectedIds={supersededIds}
                  setSelectedIds={setSupersededIds}
                  supersessionReason={supersessionReason}
                  setSupersessionReason={setSupersessionReason}
                />
                <Box sx={{ mt: 2 }}>
                  <Button onClick={() => setActiveStep(0)} sx={{ mr: 1 }}>
                    Back
                  </Button>
                  <Button variant="contained" onClick={() => setActiveStep(2)}
                          disabled={!canAdvanceFromStep2}>
                    Next
                  </Button>
                </Box>
              </Box>
            </StepContent>
          </Step>

          {/* Step 3 — Pipeline options */}
          <Step>
            <StepLabel>Pipeline options</StepLabel>
            <StepContent>
              <Stack spacing={2} sx={{ pt: 1 }}>
                <Alert severity="info" icon={<MagicIcon />}>
                  After this document is indexed, we can automatically:
                </Alert>
                <FormControlLabel
                  control={<Switch checked={rerunCoverage}
                                    onChange={e => setRerunCoverage(e.target.checked)} />}
                  label={<>
                    <Typography variant="body2" fontWeight={600}>
                      Re-assess open coverage gaps
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      For every requirement currently marked gap/partial/uncertain,
                      the AI will re-check whether this new document closes the gap.
                      Cost: ~$0.50–$2 depending on how many open gaps remain.
                    </Typography>
                  </>}
                />
                <FormControlLabel
                  control={<Switch checked={runClassifier}
                                    onChange={e => setRunClassifier(e.target.checked)} />}
                  label={<>
                    <Typography variant="body2" fontWeight={600}>
                      Classify newly-affected requirements
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Tag each requirement by HOW it should be responded to
                      (evidence-required vs commitment-only vs acknowledgment-only,
                      etc.). Skips anything you've manually classified.
                    </Typography>
                  </>}
                />
                <Box>
                  <Button onClick={() => setActiveStep(1)} sx={{ mr: 1 }}>
                    Back
                  </Button>
                  <Button variant="contained" onClick={submit}
                          disabled={!canAdvanceFromStep3 || submitting}
                          startIcon={submitting ? <CircularProgress size={16} /> : <UploadIcon />}>
                    {submitting ? 'Starting...' : 'Upload & Run Pipeline'}
                  </Button>
                </Box>
              </Stack>
            </StepContent>
          </Step>

          {/* Step 4 — Live progress */}
          <Step>
            <StepLabel>Pipeline progress</StepLabel>
            <StepContent>
              <Box sx={{ pt: 1 }}>
                <ProgressTimeline job={job} error={error} />
              </Box>
            </StepContent>
          </Step>
        </Stepper>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        {activeStep === 3 && job?.status === 'complete' && (
          <Button onClick={() => { reset(); }} startIcon={<RefreshIcon />}>
            Upload another
          </Button>
        )}
        <Button onClick={close} variant={activeStep === 3 && job?.status === 'complete'
                                          ? 'contained' : 'text'}>
          {activeStep === 3 && job?.status === 'complete' ? 'Done' : 'Close'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
