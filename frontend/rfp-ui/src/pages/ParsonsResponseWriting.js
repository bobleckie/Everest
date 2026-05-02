/**
 * ParsonsResponseWriting
 *
 * Per-requirement response surface — the user's "respond to compliance
 * items first, then assemble section narratives from those" workflow.
 *
 * Layout:
 *   • Sidebar: list of RFP-native section roots (the RFP's OWN
 *     terminology, e.g. "Section 3", "Appendix 3 - Inspection") with
 *     drafted/total counts and readiness chips.
 *   • Main: requirements in the selected section, each as an expandable
 *     accordion containing the requirement detail, AI-suggest button,
 *     compliance disposition picker, free-text response editor,
 *     approve button, and a footer showing Parsons coverage status.
 *   • Section toolbar (when a section is selected): "Bulk-draft this
 *     section" + "Assemble section narrative" buttons.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Card, CardContent, Stack, Typography, Chip, IconButton, Tooltip,
  CircularProgress, Alert, Button, Divider, TextField, MenuItem, Select,
  Accordion, AccordionSummary, AccordionDetails, LinearProgress, Snackbar,
  Dialog, DialogTitle, DialogContent, DialogActions, Tabs, Tab,
} from '@mui/material';
import {
  ExpandMore as ExpandIcon,
  AutoAwesome as DraftIcon,
  CheckCircle as ApproveIcon,
  Refresh as RefreshIcon,
  Save as SaveIcon,
  Bolt as BulkIcon,
  AutoStories as AssembleIcon,
  ArrowBack as BackIcon,
  Article as EvidenceIcon,
  ThumbDown as RejectIcon,
  Comment as CommentIcon,
  Compare as DiffIcon,
  Send as SendIcon,
  Save as SaveNarrativeIcon,
  Chat as ChatIcon,
  Groups as CompetitorIcon,
} from '@mui/icons-material';
import { useNavigate, useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { useProposal } from '../proposal/ProposalContext';

const DISPOSITIONS = [
  'Comply', 'Comply-with-exception', 'Take-exception',
  'Not-Applicable', 'Needs-Clarification',
];

const STATUS_VISUAL = {
  not_started:  { color: '#9e9e9e', label: 'Not started' },
  ai_drafted:   { color: '#0288d1', label: 'AI drafted' },
  user_edited:  { color: '#7b1fa2', label: 'Edited' },
  approved:     { color: '#2e7d32', label: 'Approved' },
  exported:     { color: '#1565c0', label: 'Exported' },
  rejected:     { color: '#c62828', label: 'Rejected' },
};

// Tiny line-based diff for the AI-vs-user comparator. Not LCS — just a
// "changed lines" summary good enough to highlight what the user edited.
function lineDiff(oldText, newText) {
  const oldLines = (oldText || '').split(/\n/);
  const newLines = (newText || '').split(/\n/);
  const oldSet = new Set(oldLines.map(s => s.trim()).filter(Boolean));
  const newSet = new Set(newLines.map(s => s.trim()).filter(Boolean));
  const added = newLines.filter(l => l.trim() && !oldSet.has(l.trim()));
  const removed = oldLines.filter(l => l.trim() && !newSet.has(l.trim()));
  return { added, removed };
}

const COVERAGE_VISUAL = {
  covered:      { color: '#2e7d32', label: 'Covered' },
  partial:      { color: '#ed6c02', label: 'Partial' },
  gap:          { color: '#c62828', label: 'Gap' },
  uncertain:    { color: '#7b1fa2', label: 'Uncertain' },
  not_assessed: { color: '#bdbdbd', label: 'Not assessed' },
};

const READINESS_BG = {
  green: '#2e7d32', amber: '#ed6c02', red: '#c62828', unknown: '#9e9e9e',
};


// ── One requirement card ──────────────────────────────────────────
// Wrapped in React.memo at the bottom of this declaration so the parent's
// state changes don't re-render every card (typical case: 100+ rows).
function _RequirementCardInner({ req, onChanged }) {
  const [expanded, setExpanded] = useState(false);
  const [response, setResponse] = useState(req.parsons_response || '');
  const [disposition, setDisposition] = useState(req.compliance_disposition || '');
  const [status, setStatus] = useState(req.parsons_response_status || 'not_started');
  const [busy, setBusy] = useState(null); // 'draft' | 'save' | 'approve' | 'reject'
  const [toast, setToast] = useState(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidence, setEvidence] = useState(null);
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [comments, setComments] = useState([]);
  const [newComment, setNewComment] = useState('');
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [rejectRedraft, setRejectRedraft] = useState(true);
  const [diffOpen, setDiffOpen] = useState(false);
  // Per-requirement chat with the agent (Parsons KB + req + draft).
  const [chatOpen, setChatOpen] = useState(false);
  const [chatTurns, setChatTurns] = useState([]); // [{role, content, citations?, suggested_rewrite?}]
  const [chatInput, setChatInput] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  // Competitor predictions for this requirement's section (silent if empty).
  const [competitorPreds, setCompetitorPreds] = useState(null); // null = unloaded, [] = none, [...] = some
  const [competitorOpen, setCompetitorOpen] = useState(false);
  const initialResponseRef = useRef(req.parsons_response || '');

  useEffect(() => {
    setResponse(req.parsons_response || '');
    setDisposition(req.compliance_disposition || '');
    setStatus(req.parsons_response_status || 'not_started');
    initialResponseRef.current = req.parsons_response || '';
  }, [req.id, req.parsons_response, req.compliance_disposition, req.parsons_response_status]);

  const dirty = response !== initialResponseRef.current
             || disposition !== (req.compliance_disposition || '');
  const evidenceCount = req.parsons_response_cited_evidence_count || 0;
  const hasUserEdits = req.has_user_edits;

  const sv = STATUS_VISUAL[status] || STATUS_VISUAL.not_started;
  const cv = COVERAGE_VISUAL[req.parsons_coverage_status] || COVERAGE_VISUAL.not_assessed;

  const aiDraft = async () => {
    setBusy('draft');
    try {
      const { data } = await axios.post(`/api/parsons-response/requirements/${req.id}/draft`);
      if (data.skipped) {
        setToast(`Skipped: ${data.reason}`);
      } else {
        setResponse(data.parsons_response || '');
        setDisposition(data.compliance_disposition || '');
        setStatus(data.status || 'ai_drafted');
        initialResponseRef.current = data.parsons_response || '';
        setToast('Draft generated.');
        onChanged && onChanged();
      }
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Draft failed');
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy('save');
    try {
      const { data } = await axios.patch(
        `/api/parsons-response/requirements/${req.id}`,
        { parsons_response: response, compliance_disposition: disposition || null });
      setStatus(data.parsons_response_status);
      initialResponseRef.current = data.parsons_response || '';
      setToast('Saved.');
      onChanged && onChanged();
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Save failed');
    } finally {
      setBusy(null);
    }
  };

  const approve = async () => {
    if (dirty) {
      // Save first
      await save();
    }
    setBusy('approve');
    try {
      await axios.post(`/api/parsons-response/requirements/${req.id}/approve`);
      setStatus('approved');
      setToast('Approved.');
      onChanged && onChanged();
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Approve failed');
    } finally {
      setBusy(null);
    }
  };

  const openEvidence = async () => {
    setEvidenceOpen(true);
    if (evidence) return;
    try {
      const { data } = await axios.get(
        `/api/parsons-response/requirements/${req.id}/evidence`);
      setEvidence(data);
    } catch (e) {
      setEvidence({ error: e?.response?.data?.detail || 'Failed to load evidence' });
    }
  };

  const openComments = async () => {
    setCommentsOpen(true);
    try {
      const { data } = await axios.get(
        `/api/parsons-response/requirements/${req.id}/comments`);
      setComments(data?.comments || []);
    } catch (e) {
      setComments([]);
    }
  };

  const postComment = async () => {
    if (!newComment.trim()) return;
    try {
      const { data } = await axios.post(
        `/api/parsons-response/requirements/${req.id}/comments`,
        { body: newComment, kind: 'comment' });
      setComments((cs) => [...cs, data]);
      setNewComment('');
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Comment failed');
    }
  };

  const submitReject = async () => {
    if (!rejectFeedback.trim()) return;
    setBusy('reject');
    try {
      const { data } = await axios.post(
        `/api/parsons-response/requirements/${req.id}/reject`,
        { feedback: rejectFeedback, redraft: rejectRedraft });
      const r2 = data.requirement;
      if (r2) {
        setResponse(r2.parsons_response || '');
        setDisposition(r2.compliance_disposition || '');
        setStatus(r2.parsons_response_status);
        initialResponseRef.current = r2.parsons_response || '';
      }
      if (data.redraft && !data.redraft.error) {
        setToast('Rejected and re-drafted.');
      } else {
        setToast('Rejection feedback saved.');
      }
      setRejectOpen(false);
      setRejectFeedback('');
      onChanged && onChanged();
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Reject failed');
    } finally {
      setBusy(null);
    }
  };

  // ── Chat with the agent about THIS requirement ────────────────
  const sendChat = async () => {
    const msg = (chatInput || '').trim();
    if (!msg) return;
    setChatBusy(true);
    const history = chatTurns
      .filter(t => t.role === 'user' || t.role === 'agent')
      .map(t => ({ role: t.role, content: t.content }));
    // Optimistically add the user turn
    setChatTurns(ts => [...ts, { role: 'user', content: msg }]);
    setChatInput('');
    try {
      const { data } = await axios.post(
        `/api/parsons-response/requirements/${req.id}/chat`,
        { message: msg, history });
      setChatTurns(ts => [...ts, {
        role: 'agent',
        content: data.answer || '(no answer)',
        citations: data.citations || [],
        suggested_rewrite: data.suggested_rewrite || null,
      }]);
    } catch (e) {
      setChatTurns(ts => [...ts, {
        role: 'agent',
        content: 'Error: ' + (e?.response?.data?.detail || e.message || 'chat failed'),
        error: true,
      }]);
    } finally {
      setChatBusy(false);
    }
  };

  const applyRewrite = (rewrite) => {
    if (!rewrite) return;
    setResponse(rewrite);
    setToast('Rewrite applied to draft. Save when ready.');
  };

  // ── Competitor predictions (lazy-load on expand) ──────────────
  const loadCompetitorPreds = async () => {
    if (competitorPreds !== null) return; // already loaded (possibly empty)
    try {
      const { data } = await axios.get(
        `/api/parsons-response/requirements/${req.id}/competitor-predictions`);
      setCompetitorPreds(data?.predictions || []);
    } catch (e) {
      // Silent on failure — this is an optional, independent feature.
      setCompetitorPreds([]);
    }
  };

  return (
    <>
      <Accordion expanded={expanded} onChange={(_, v) => { setExpanded(v); if (v) loadCompetitorPreds(); }}
                 variant="outlined"
                 // unmountOnExit: don't keep the heavy body in the DOM for
                 // collapsed cards. With 100+ requirements per section
                 // this is the difference between butter-smooth scrolling
                 // and the OS-beep-on-every-tick lag the user reported.
                 TransitionProps={{ unmountOnExit: true }}
                 sx={{ mb: 1, borderRadius: 2,
                       '&:before': { display: 'none' } }}>
        <AccordionSummary expandIcon={<ExpandIcon />}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ width: '100%' }}>
            <Typography variant="caption" sx={{ fontFamily: 'monospace',
                                                  color: 'text.secondary',
                                                  minWidth: 60 }}>
              {req.section_id || '—'}
            </Typography>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="body2" fontWeight={600} noWrap>
                {req.title || '(untitled)'}
              </Typography>
            </Box>
            <Chip size="small" label={sv.label}
                  sx={{ height: 20, fontSize: '0.65rem',
                        bgcolor: sv.color, color: '#fff' }} />
            <Tooltip title={`Parsons evidence coverage: ${cv.label}`}>
              <Chip size="small" label={cv.label}
                    sx={{ height: 20, fontSize: '0.65rem',
                          bgcolor: `${cv.color}20`, color: cv.color,
                          border: `1px solid ${cv.color}40` }} />
            </Tooltip>
            {req.priority && (
              <Chip size="small" variant="outlined" label={req.priority}
                    sx={{ height: 20, fontSize: '0.65rem' }} />
            )}
          </Stack>
        </AccordionSummary>
        <AccordionDetails>
          <Divider sx={{ mb: 1.5 }} />

          {/* Requirement detail */}
          {req.description && (
            <Typography variant="body2" sx={{ mb: 1 }}>
              {req.description}
            </Typography>
          )}
          {req.source_text && (
            <Box sx={{ p: 1.25, mb: 2, borderRadius: 1, bgcolor: 'rgba(0,0,0,0.03)',
                       borderLeft: '3px solid rgba(0,174,230,0.4)',
                       fontSize: '0.85rem', whiteSpace: 'pre-wrap',
                       maxHeight: 200, overflowY: 'auto' }}>
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5 }}>
                Source text {req.source_page ? `(p. ${req.source_page})` : ''}
              </Typography>
              {req.source_text}
            </Box>
          )}
          {req.parsons_coverage_notes && (
            <Alert severity="info" sx={{ mb: 2, py: 0.5 }}>
              <Typography variant="caption" fontWeight={700}>
                Coverage rationale:
              </Typography>{' '}
              {req.parsons_coverage_notes}
            </Alert>
          )}

          {/* Reviewer-rejected feedback callout */}
          {req.parsons_response_review_feedback && status === 'rejected' && (
            <Alert severity="warning" sx={{ mb: 2 }}
                   action={<Button size="small" onClick={aiDraft} disabled={!!busy}>
                     Re-draft
                   </Button>}>
              <Typography variant="caption" fontWeight={700}>Reviewer feedback:</Typography>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                {req.parsons_response_review_feedback}
              </Typography>
            </Alert>
          )}

          {/* Response editor toolbar */}
          <Stack direction="row" spacing={1} sx={{ mb: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Select
              size="small"
              value={disposition || ''}
              onChange={(e) => setDisposition(e.target.value)}
              displayEmpty
              sx={{ minWidth: 220, height: 36 }}
            >
              <MenuItem value=""><em>Set compliance disposition…</em></MenuItem>
              {DISPOSITIONS.map(d => (
                <MenuItem key={d} value={d}>{d}</MenuItem>
              ))}
            </Select>
            <Box sx={{ flex: 1 }} />
            <Tooltip title={evidenceCount > 0
              ? `View the ${evidenceCount} Parsons chunks the AI cited`
              : 'No evidence captured yet — click Generate Response first'}>
              <span>
                <Button size="small" variant="outlined" startIcon={<EvidenceIcon />}
                        onClick={openEvidence} disabled={evidenceCount === 0}>
                  Evidence ({evidenceCount})
                </Button>
              </span>
            </Tooltip>
            {hasUserEdits && (
              <Tooltip title="Compare AI-original to current text">
                <Button size="small" variant="outlined" startIcon={<DiffIcon />}
                        onClick={() => setDiffOpen(true)}>
                  Diff
                </Button>
              </Tooltip>
            )}
            <Button size="small" variant="outlined" startIcon={<CommentIcon />}
                    onClick={openComments}>
              Comments
            </Button>
            <Button size="small" variant="outlined" color="error"
                    startIcon={<RejectIcon />}
                    onClick={() => setRejectOpen(true)}
                    disabled={!!busy || !response.trim()}>
              Reject
            </Button>
            <Tooltip title="Open a focused chat with the agent about this requirement (Parsons KB + this draft + this requirement)">
              <Button size="small" variant="outlined" startIcon={<ChatIcon />}
                      onClick={() => setChatOpen(true)} disabled={!!busy}>
                Chat
              </Button>
            </Tooltip>
            <Tooltip title="Have the agent search the Parsons documents and propose the best possible answer for this requirement based on what we know we can do.">
              <Button size="small" variant="contained" color="primary"
                      startIcon={busy === 'draft' ? <CircularProgress size={14} color="inherit" /> : <DraftIcon />}
                      onClick={aiDraft} disabled={!!busy}
                      sx={{ fontWeight: 700 }}>
                {busy === 'draft' ? 'Generating…' : (response ? 'Re-generate' : 'Generate Response')}
              </Button>
            </Tooltip>
            <Button size="small" variant="outlined" startIcon={<SaveIcon />}
                    onClick={save} disabled={!!busy || !dirty}>
              {busy === 'save' ? 'Saving…' : 'Save'}
            </Button>
            <Button size="small" variant="contained" color="success"
                    startIcon={<ApproveIcon />}
                    onClick={approve}
                    disabled={!!busy || !response.trim()}>
              {busy === 'approve' ? 'Approving…' : 'Approve'}
            </Button>
          </Stack>
          <TextField
            multiline minRows={4} fullWidth
            placeholder="Write Parsons' response to this requirement, or click Generate Response to have the agent draft it from the Parsons knowledge base…"
            value={response}
            onChange={(e) => setResponse(e.target.value)}
            sx={{
              '& .MuiInputBase-root': { fontSize: '0.9rem', lineHeight: 1.45 },
            }}
          />

          {/* Evidence-doc footnote */}
          {(req.parsons_evidence_doc_ids || []).length > 0 && (
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', mt: 1 }}>
              Cited evidence document ids: {req.parsons_evidence_doc_ids.join(', ')}
            </Typography>
          )}

          {/* Competitor predictions for this section — silent if none.
              The competitor-persona pipeline runs separately on the Competitive
              Intel page; we just surface its output here so the user can
              compare side-by-side without leaving the requirement. */}
          {Array.isArray(competitorPreds) && competitorPreds.length > 0 && (
            <Accordion variant="outlined" expanded={competitorOpen}
                       onChange={(_, v) => setCompetitorOpen(v)}
                       sx={{ mt: 2, borderRadius: 1.5,
                             '&:before': { display: 'none' } }}>
              <AccordionSummary expandIcon={<ExpandIcon />}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <CompetitorIcon fontSize="small" sx={{ color: '#7b1fa2' }} />
                  <Typography variant="body2" fontWeight={600}>
                    Competitor predicted responses ({competitorPreds.length})
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    — for comparison only, scored separately
                  </Typography>
                </Stack>
              </AccordionSummary>
              <AccordionDetails>
                <Stack spacing={1.5}>
                  {competitorPreds.map((p) => (
                    <Box key={p.id} sx={{
                      p: 1.5, borderRadius: 1.5,
                      border: '1px solid rgba(123,31,162,0.25)',
                      bgcolor: 'rgba(123,31,162,0.04)',
                    }}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                        <Typography variant="body2" fontWeight={700}
                                    sx={{ color: '#6a1b9a' }}>
                          {p.competitor_name}
                        </Typography>
                        {p.confidence_score != null && (
                          <Chip size="small" variant="outlined"
                                label={`confidence ${p.confidence_score}%`}
                                sx={{ height: 20, fontSize: '0.65rem' }} />
                        )}
                        {p.model_used && (
                          <Typography variant="caption" color="text.secondary">
                            {p.model_used}
                          </Typography>
                        )}
                      </Stack>
                      <Typography variant="body2"
                                  sx={{ whiteSpace: 'pre-wrap',
                                        fontSize: '0.85rem' }}>
                        {p.predicted_response}
                      </Typography>
                      {p.reasoning && (
                        <>
                          <Divider sx={{ my: 1 }} />
                          <Typography variant="caption" color="text.secondary"
                                      sx={{ whiteSpace: 'pre-wrap' }}>
                            <strong>Reasoning:</strong> {p.reasoning}
                          </Typography>
                        </>
                      )}
                    </Box>
                  ))}
                </Stack>
                <Typography variant="caption" color="text.secondary"
                            sx={{ display: 'block', mt: 1.5 }}>
                  Tip: generate or refresh competitor predictions from the
                  Competitive Intel page. Scoring (Parsons vs. competitor)
                  runs there too.
                </Typography>
              </AccordionDetails>
            </Accordion>
          )}
        </AccordionDetails>
      </Accordion>

      {/* Evidence drawer dialog */}
      <Dialog open={evidenceOpen} onClose={() => setEvidenceOpen(false)}
              maxWidth="md" fullWidth>
        <DialogTitle>
          Evidence used by AI — {req.section_id || `req ${req.id}`}
        </DialogTitle>
        <DialogContent dividers>
          {!evidence ? (
            <CircularProgress size={20} />
          ) : evidence.error ? (
            <Alert severity="error">{evidence.error}</Alert>
          ) : !evidence.evidence?.length ? (
            <Typography variant="body2" color="text.secondary">
              No evidence captured. Click Generate Response to populate.
            </Typography>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mb: 1.5 }}>
                {evidence.evidence_count} Parsons chunk{evidence.evidence_count === 1 ? '' : 's'} were
                shown to the drafter. Snippets in <strong>bold</strong> are the ones the
                model name-checked in its citation list.
              </Typography>
              <Stack spacing={1.5}>
                {evidence.evidence.map((ev, i) => (
                  <Box key={i} sx={{
                    p: 1.5, borderRadius: 1.5,
                    border: ev.model_named
                      ? '2px solid rgba(46,125,50,0.4)'
                      : '1px solid rgba(0,0,0,0.08)',
                    bgcolor: ev.model_named
                      ? 'rgba(46,125,50,0.05)'
                      : 'rgba(0,0,0,0.02)',
                  }}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                      <Chip size="small" label={`sim ${(ev.similarity * 100).toFixed(0)}%`}
                            sx={{ height: 18, fontSize: '0.65rem' }} />
                      {ev.model_named && (
                        <Chip size="small" color="success" label="cited"
                              sx={{ height: 18, fontSize: '0.65rem' }} />
                      )}
                      <Typography variant="caption" sx={{ flex: 1 }} noWrap>
                        {ev.document_name}{ev.page ? ` · p.${ev.page}` : ''}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        chunk #{ev.chunk_id}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap',
                                                       fontFamily: 'inherit',
                                                       fontWeight: ev.model_named ? 600 : 400 }}>
                      {ev.snippet}
                    </Typography>
                  </Box>
                ))}
              </Stack>
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEvidenceOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      {/* Comments thread dialog */}
      <Dialog open={commentsOpen} onClose={() => setCommentsOpen(false)}
              maxWidth="sm" fullWidth>
        <DialogTitle>Comments — {req.section_id || `req ${req.id}`}</DialogTitle>
        <DialogContent dividers>
          {comments.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No comments yet.
            </Typography>
          ) : (
            <Stack spacing={1.5} sx={{ mb: 2 }}>
              {comments.map((c) => (
                <Box key={c.id} sx={{
                  p: 1.25, borderRadius: 1.5,
                  borderLeft: `3px solid ${c.kind === 'rejection' ? '#c62828'
                                          : c.kind === 'system' ? '#9e9e9e'
                                          : '#0288d1'}`,
                  bgcolor: 'rgba(0,0,0,0.02)',
                }}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="caption" fontWeight={700}>
                      {c.author_label || 'user'}
                    </Typography>
                    {c.kind && c.kind !== 'comment' && (
                      <Chip size="small" label={c.kind}
                            sx={{ height: 16, fontSize: '0.6rem' }} />
                    )}
                    <Typography variant="caption" color="text.secondary"
                                sx={{ ml: 'auto' }}>
                      {c.created_at && new Date(c.created_at).toLocaleString()}
                    </Typography>
                  </Stack>
                  <Typography variant="body2" sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                    {c.body}
                  </Typography>
                </Box>
              ))}
            </Stack>
          )}
          <TextField
            multiline minRows={2} fullWidth
            placeholder="Add a comment…"
            value={newComment}
            onChange={(e) => setNewComment(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCommentsOpen(false)}>Close</Button>
          <Button onClick={postComment} variant="contained"
                  startIcon={<SendIcon />}
                  disabled={!newComment.trim()}>
            Post
          </Button>
        </DialogActions>
      </Dialog>

      {/* Reject + feedback dialog */}
      <Dialog open={rejectOpen} onClose={() => setRejectOpen(false)}
              maxWidth="sm" fullWidth>
        <DialogTitle>Reject draft & request changes</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" sx={{ mb: 1 }}>
            What's wrong with this draft? Your feedback is fed back to the AI
            on the next draft so it can address your concerns.
          </Typography>
          <TextField
            multiline minRows={3} fullWidth autoFocus
            placeholder="e.g. The response cites Method A but the requirement asks for Method B…"
            value={rejectFeedback}
            onChange={(e) => setRejectFeedback(e.target.value)}
          />
          <Box sx={{ mt: 1.5 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={rejectRedraft}
                onChange={(e) => setRejectRedraft(e.target.checked)}
              />
              <Typography variant="body2">
                Immediately re-draft with this feedback
              </Typography>
            </label>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRejectOpen(false)}>Cancel</Button>
          <Button onClick={submitReject} color="error" variant="contained"
                  startIcon={<RejectIcon />}
                  disabled={!rejectFeedback.trim() || busy === 'reject'}>
            {busy === 'reject' ? 'Submitting…' : 'Reject & save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Diff dialog */}
      <Dialog open={diffOpen} onClose={() => setDiffOpen(false)}
              maxWidth="md" fullWidth>
        <DialogTitle>AI draft vs current — {req.section_id || `req ${req.id}`}</DialogTitle>
        <DialogContent dividers>
          {(() => {
            const orig = req.parsons_response_ai_original || '';
            const cur = response;
            const { added, removed } = lineDiff(orig, cur);
            return (
              <>
                <Typography variant="overline" color="text.secondary"
                            sx={{ display: 'block' }}>
                  AI original
                </Typography>
                <Box sx={{ p: 1.25, mb: 2, bgcolor: 'rgba(0,0,0,0.03)',
                            borderRadius: 1, fontSize: '0.85rem',
                            whiteSpace: 'pre-wrap', maxHeight: 200,
                            overflowY: 'auto' }}>
                  {orig || <em>—</em>}
                </Box>
                <Typography variant="overline" color="text.secondary"
                            sx={{ display: 'block' }}>
                  Current
                </Typography>
                <Box sx={{ p: 1.25, mb: 2, bgcolor: 'rgba(0,174,230,0.05)',
                            borderRadius: 1, fontSize: '0.85rem',
                            whiteSpace: 'pre-wrap', maxHeight: 200,
                            overflowY: 'auto' }}>
                  {cur || <em>—</em>}
                </Box>
                {removed.length > 0 && (
                  <>
                    <Typography variant="overline" color="error"
                                sx={{ display: 'block' }}>
                      Removed
                    </Typography>
                    <Box sx={{ p: 1.25, mb: 1, bgcolor: 'rgba(198,40,40,0.06)',
                                borderRadius: 1, fontSize: '0.85rem' }}>
                      {removed.map((l, i) => <div key={i}>− {l}</div>)}
                    </Box>
                  </>
                )}
                {added.length > 0 && (
                  <>
                    <Typography variant="overline"
                                sx={{ display: 'block', color: '#2e7d32' }}>
                      Added
                    </Typography>
                    <Box sx={{ p: 1.25, bgcolor: 'rgba(46,125,50,0.06)',
                                borderRadius: 1, fontSize: '0.85rem' }}>
                      {added.map((l, i) => <div key={i}>+ {l}</div>)}
                    </Box>
                  </>
                )}
              </>
            );
          })()}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDiffOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      {/* Chat-with-agent dialog (per-requirement) */}
      <Dialog open={chatOpen} onClose={() => setChatOpen(false)}
              maxWidth="md" fullWidth>
        <DialogTitle sx={{ pb: 0.5 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <ChatIcon fontSize="small" />
            <Box sx={{ flex: 1 }}>
              <Typography variant="h6" fontWeight={700}>
                Chat with the agent
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {req.section_id || `Requirement ${req.id}`} ·{' '}
                {(req.title || '').slice(0, 80)}
                {(req.title || '').length > 80 ? '…' : ''}
              </Typography>
            </Box>
          </Stack>
        </DialogTitle>
        <DialogContent dividers sx={{ p: 0 }}>
          <Box sx={{
            maxHeight: 480, minHeight: 280, overflowY: 'auto', p: 2,
            bgcolor: 'rgba(0,0,0,0.015)',
          }}>
            {chatTurns.length === 0 && (
              <Box sx={{ textAlign: 'center', color: 'text.secondary', py: 4 }}>
                <ChatIcon sx={{ fontSize: 32, opacity: 0.4 }} />
                <Typography variant="body2" sx={{ mt: 1 }}>
                  Ask the agent how to strengthen this response.
                </Typography>
                <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
                  Examples: "What evidence in the Parsons KB supports this?",
                  "How would you tighten this paragraph?", "Rewrite this for
                  a state evaluator."
                </Typography>
              </Box>
            )}
            <Stack spacing={1.5}>
              {chatTurns.map((t, i) => (
                <Box key={i} sx={{
                  p: 1.5, borderRadius: 1.5,
                  bgcolor: t.role === 'user' ? '#e3f2fd' : '#fff',
                  border: '1px solid',
                  borderColor: t.role === 'user' ? '#90caf9' : 'rgba(0,0,0,0.08)',
                  alignSelf: t.role === 'user' ? 'flex-end' : 'flex-start',
                  maxWidth: '92%',
                  ml: t.role === 'user' ? 'auto' : 0,
                }}>
                  <Typography variant="caption" fontWeight={700}
                              color={t.error ? 'error' : 'text.secondary'}>
                    {t.role === 'user' ? 'You' : 'Agent'}
                  </Typography>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mt: 0.5 }}>
                    {t.content}
                  </Typography>
                  {t.suggested_rewrite && (
                    <Box sx={{ mt: 1.5, p: 1.25, borderRadius: 1,
                                bgcolor: 'rgba(46,125,50,0.06)',
                                border: '1px solid rgba(46,125,50,0.3)' }}>
                      <Typography variant="caption" fontWeight={700}
                                  color="success.dark">
                        SUGGESTED REWRITE
                      </Typography>
                      <Typography variant="body2"
                                  sx={{ whiteSpace: 'pre-wrap', mt: 0.5,
                                        fontFamily: 'inherit', fontSize: '0.85rem' }}>
                        {t.suggested_rewrite}
                      </Typography>
                      <Button size="small" variant="contained" color="success"
                              sx={{ mt: 1 }}
                              onClick={() => applyRewrite(t.suggested_rewrite)}>
                        Apply rewrite to draft
                      </Button>
                    </Box>
                  )}
                  {Array.isArray(t.citations) && t.citations.length > 0 && (
                    <Box sx={{ mt: 1.25 }}>
                      <Typography variant="caption" color="text.secondary"
                                  sx={{ display: 'block', mb: 0.5 }}>
                        Citations ({t.citations.length}):
                      </Typography>
                      <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
                        {t.citations.slice(0, 8).map((c, ci) => (
                          <Tooltip key={ci}
                                   title={(c.snippet || '').slice(0, 280)}>
                            <Chip size="small" variant="outlined"
                                  label={`${c.document_name || 'doc'}${c.page ? ' p.' + c.page : ''}`}
                                  sx={{ height: 20, fontSize: '0.65rem' }} />
                          </Tooltip>
                        ))}
                      </Stack>
                    </Box>
                  )}
                </Box>
              ))}
              {chatBusy && (
                <Box sx={{ p: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}>
                  <CircularProgress size={16} />
                  <Typography variant="caption" color="text.secondary">
                    Agent is thinking…
                  </Typography>
                </Box>
              )}
            </Stack>
          </Box>
        </DialogContent>
        <DialogActions sx={{ p: 1.5 }}>
          <TextField
            placeholder="Ask the agent…"
            fullWidth size="small" multiline maxRows={4}
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!chatBusy) sendChat();
              }
            }}
            disabled={chatBusy}
          />
          <Button onClick={() => setChatOpen(false)}>Close</Button>
          <Button variant="contained" startIcon={<SendIcon />}
                  onClick={sendChat}
                  disabled={chatBusy || !chatInput.trim()}>
            Send
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar open={!!toast} autoHideDuration={3500} onClose={() => setToast(null)}
                message={toast || ''} />
    </>
  );
}

// React.memo with a custom equality check: parent re-renders shouldn't
// re-render every card. The card's own state (response text, busy flag,
// dialog opens) is internal — the only external props are `req` and the
// `onChanged` callback. We compare `req` shallowly on the fields the card
// actually displays so that an unrelated dashboard refresh doesn't churn
// every row in the list.
const RequirementCard = React.memo(_RequirementCardInner, (prev, next) => {
  if (prev.onChanged !== next.onChanged) return false;
  const a = prev.req, b = next.req;
  if (!a || !b) return a === b;
  if (a.id !== b.id) return false;
  // Compare the fields that actually render. Anything else can change
  // without triggering a re-render.
  return (
    a.parsons_response === b.parsons_response &&
    a.parsons_response_status === b.parsons_response_status &&
    a.parsons_response_review_feedback === b.parsons_response_review_feedback &&
    a.compliance_disposition === b.compliance_disposition &&
    a.parsons_coverage_status === b.parsons_coverage_status &&
    a.parsons_coverage_notes === b.parsons_coverage_notes &&
    a.title === b.title &&
    a.priority === b.priority &&
    a.section_id === b.section_id &&
    a.parsons_response_cited_evidence_count === b.parsons_response_cited_evidence_count &&
    a.has_user_edits === b.has_user_edits
  );
});


// ── Section sidebar entry ─────────────────────────────────────────
function SectionListItem({ section, active, onClick }) {
  const bg = READINESS_BG[section.readiness] || READINESS_BG.unknown;
  return (
    <Box onClick={onClick}
         sx={{
           p: 1.25, borderRadius: 1.5, cursor: 'pointer',
           border: '1px solid',
           borderColor: active ? '#00AEE6' : 'rgba(0,0,0,0.08)',
           bgcolor: active ? 'rgba(0,174,230,0.06)' : 'transparent',
           '&:hover': { borderColor: '#00AEE6' },
         }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: bg }} />
        <Typography variant="body2" fontWeight={active ? 700 : 600}
                    sx={{ flex: 1 }} noWrap>
          {section.label}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {section.drafted_count}/{section.total}
        </Typography>
      </Stack>
      <Box sx={{ mt: 0.5 }}>
        <LinearProgress
          variant="determinate"
          value={section.pct_drafted}
          sx={{ height: 4, borderRadius: 2, bgcolor: 'rgba(0,0,0,0.06)',
                '& .MuiLinearProgress-bar': { background: '#00AEE6' } }}
        />
      </Box>
    </Box>
  );
}


// ── Page ─────────────────────────────────────────────────────────
export default function ParsonsResponseWriting() {
  const { proposalId } = useProposal();
  const [searchParams, setSearchParams] = useSearchParams();
  const [submission, setSubmission] = useState(null);
  const [loadingSubmission, setLoadingSubmission] = useState(true);
  const [requirements, setRequirements] = useState([]);
  const [loadingReqs, setLoadingReqs] = useState(false);
  const [activeSection, setActiveSection] = useState(searchParams.get('section') || null);
  const [bulkJob, setBulkJob] = useState(null);
  const [assemblyResult, setAssemblyResult] = useState(null);
  const [assembleBusy, setAssembleBusy] = useState(false);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  // Stable callbacks so React.memo on RequirementCard can actually skip work.
  // Without useCallback, every parent render produced a fresh `loadSubmission`,
  // which forced every card in the list to re-render on any state change.
  const loadSubmission = useCallback(async () => {
    setLoadingSubmission(true);
    try {
      const { data } = await axios.get(
        `/api/parsons-response/proposals/${proposalId}/submission`);
      setSubmission(data);
      // Auto-pick first section if nothing selected
      if (!activeSection && data?.sections?.length) {
        setActiveSection(data.sections[0].section_root);
        setSearchParams({ section: data.sections[0].section_root });
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load sections');
    } finally {
      setLoadingSubmission(false);
    }
    // We intentionally omit activeSection from deps — re-creating this fn
    // on every section pick would defeat the React.memo on RequirementCard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposalId]);

  const loadRequirements = useCallback(async () => {
    if (!activeSection) return;
    setLoadingReqs(true);
    try {
      const { data } = await axios.get('/api/parsons-response/requirements', {
        params: { proposal_id: proposalId, section_root: activeSection, limit: 500 },
      });
      setRequirements(data?.requirements || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load requirements');
    } finally {
      setLoadingReqs(false);
    }
  }, [proposalId, activeSection]);

  useEffect(() => { if (proposalId) loadSubmission(); /* eslint-disable-next-line */ }, [proposalId]);
  useEffect(() => { if (proposalId && activeSection) loadRequirements();
                    /* eslint-disable-next-line */ }, [proposalId, activeSection]);

  // Poll bulk-draft job
  useEffect(() => {
    if (!bulkJob?.job_id) return;
    if (bulkJob.status === 'done' || bulkJob.status === 'error') {
      // Refresh once when finished
      loadRequirements();
      loadSubmission();
      return;
    }
    const t = setInterval(async () => {
      try {
        const { data } = await axios.get(
          `/api/parsons-response/draft-jobs/${bulkJob.job_id}`);
        setBulkJob(data);
      } catch { /* ignore */ }
    }, 2500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkJob?.job_id, bulkJob?.status]);

  const startBulkDraft = async () => {
    if (!activeSection) return;
    if (!window.confirm(
      `Draft ALL not-yet-started requirements in "${activeSection}"? `
      + `This may take several minutes.`)) return;
    try {
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/draft-batch`,
        { section_root: activeSection });
      setBulkJob({ ...data, total: 0, completed: 0 });
    } catch (e) {
      setError(e?.response?.data?.detail || 'Bulk draft failed to start');
    }
  };

  const assembleSection = async (persist = false) => {
    if (!activeSection) return;
    setAssembleBusy(true);
    if (!persist) setAssemblyResult(null);
    try {
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/sections/${encodeURIComponent(activeSection)}/assemble`,
        null,
        { params: { persist } });
      setAssemblyResult(data);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Assemble failed');
    } finally {
      setAssembleBusy(false);
    }
  };

  const activeSectionMeta = useMemo(
    () => (submission?.sections || []).find(s => s.section_root === activeSection),
    [submission, activeSection]
  );

  // Persisted narrative metadata for the active section, if any.
  const [activeNarrativeMeta, setActiveNarrativeMeta] = useState(null);
  useEffect(() => {
    if (!proposalId || !activeSection) { setActiveNarrativeMeta(null); return; }
    let alive = true;
    axios.get(`/api/parsons-response/proposals/${proposalId}/narratives/${encodeURIComponent(activeSection)}`)
      .then(({ data }) => alive && setActiveNarrativeMeta(data))
      .catch(() => alive && setActiveNarrativeMeta(null));
    return () => { alive = false; };
  }, [proposalId, activeSection, assemblyResult]);

  if (!proposalId) {
    return <Alert severity="info" sx={{ m: 3 }}>Pick a proposal first.</Alert>;
  }

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 3 }}>
        <IconButton onClick={() => navigate(`/p/${proposalId}/dashboard`)}>
          <BackIcon />
        </IconButton>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h4" fontWeight={800}>Response Writer</Typography>
          <Typography variant="body2" color="text.secondary">
            Respond to every RFP requirement, then assemble section narratives
            from those responses for a single-voice submission.
          </Typography>
        </Box>
        <IconButton onClick={() => { loadSubmission(); loadRequirements(); }}>
          <RefreshIcon />
        </IconButton>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      <Box sx={{ display: 'flex', gap: 2 }}>
        {/* Sidebar */}
        <Box sx={{ width: 320, flexShrink: 0 }}>
          <Card variant="outlined" sx={{ borderRadius: 2 }}>
            <CardContent sx={{ pb: 1 }}>
              <Typography variant="overline" color="text.secondary"
                          sx={{ display: 'block', mb: 1 }}>
                RFP sections
              </Typography>
              {loadingSubmission ? (
                <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={20} /></Box>
              ) : !submission?.sections?.length ? (
                <Typography variant="body2" color="text.secondary">No sections yet.</Typography>
              ) : (
                <Stack spacing={0.75} sx={{ maxHeight: 'calc(100vh - 280px)', overflowY: 'auto', pr: 0.5 }}>
                  {submission.sections.map(s => (
                    <SectionListItem
                      key={s.section_root}
                      section={s}
                      active={s.section_root === activeSection}
                      onClick={() => {
                        setActiveSection(s.section_root);
                        setSearchParams({ section: s.section_root });
                      }}
                    />
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>
        </Box>

        {/* Main */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {!activeSection ? (
            <Alert severity="info">Select a section on the left to begin.</Alert>
          ) : (
            <>
              {/* Section toolbar */}
              <Card sx={{ mb: 2, borderRadius: 2,
                          background: 'linear-gradient(135deg, #fff, rgba(0,174,230,0.04))',
                          border: '1px solid rgba(0,174,230,0.2)' }}>
                <CardContent sx={{ pb: '12px !important' }}>
                  <Stack direction="row" alignItems="center" spacing={2} sx={{ flexWrap: 'wrap' }}>
                    <Box sx={{ flex: 1, minWidth: 200 }}>
                      <Typography variant="overline" color="text.secondary"
                                  sx={{ display: 'block', lineHeight: 1 }}>
                        Section
                      </Typography>
                      <Typography variant="h6" fontWeight={800}>
                        {activeSectionMeta?.label || activeSection}
                      </Typography>
                      {activeSectionMeta && (
                        <Typography variant="caption" color="text.secondary">
                          {activeSectionMeta.drafted_count}/{activeSectionMeta.total} drafted
                          · {activeSectionMeta.evidence_covered_count}/{activeSectionMeta.total} evidence-covered
                          {activeSectionMeta.gap_count > 0 && ` · ${activeSectionMeta.gap_count} gap(s)`}
                        </Typography>
                      )}
                    </Box>
                    <Button variant="outlined" startIcon={<BulkIcon />}
                            onClick={startBulkDraft}
                            disabled={bulkJob && bulkJob.status === 'running'}>
                      {bulkJob && bulkJob.status === 'running'
                        ? `Drafting ${bulkJob.completed}/${bulkJob.total}…`
                        : 'Bulk-draft section'}
                    </Button>
                    <Button variant="contained" startIcon={<AssembleIcon />}
                            onClick={() => assembleSection(false)} disabled={assembleBusy}>
                      {assembleBusy ? 'Assembling…' : 'Assemble narrative'}
                    </Button>
                  </Stack>
                  {bulkJob && bulkJob.status === 'running' && (
                    <Box sx={{ mt: 1.5 }}>
                      <LinearProgress variant="determinate"
                        value={bulkJob.total ? (bulkJob.completed / bulkJob.total) * 100 : 0}
                        sx={{ height: 6, borderRadius: 3 }} />
                    </Box>
                  )}
                  {activeNarrativeMeta?.is_stale && (
                    <Alert severity="warning" sx={{ mt: 1.5 }}
                           action={
                             <Button size="small" color="inherit"
                                     onClick={() => assembleSection(true)}
                                     disabled={assembleBusy}>
                               {assembleBusy ? 'Re-assembling…' : 'Re-assemble now'}
                             </Button>
                           }>
                      The saved narrative for this section is <strong>stale</strong>—
                      a contributing requirement has changed since it was last
                      assembled.
                    </Alert>
                  )}
                  {activeNarrativeMeta && !activeNarrativeMeta.is_stale && activeNarrativeMeta.narrative_md && (
                    <Typography variant="caption" color="success.main" sx={{ mt: 1.5, display: 'block' }}>
                      Saved narrative present (last assembled {activeNarrativeMeta.last_assembled_at
                        ? new Date(activeNarrativeMeta.last_assembled_at).toLocaleString()
                        : '—'}).
                    </Typography>
                  )}
                </CardContent>
              </Card>

              {/* Requirements list */}
              {loadingReqs ? (
                <Box sx={{ p: 5, textAlign: 'center' }}><CircularProgress /></Box>
              ) : requirements.length === 0 ? (
                <Alert severity="info">No requirements in this section.</Alert>
              ) : (
                <Box>
                  {requirements.map(r => (
                    <RequirementCard key={r.id} req={r} onChanged={loadSubmission} />
                  ))}
                </Box>
              )}
            </>
          )}
        </Box>
      </Box>

      {/* Assembled narrative preview dialog */}
      <Dialog open={!!assemblyResult} onClose={() => setAssemblyResult(null)}
              maxWidth="md" fullWidth>
        <DialogTitle>Assembled section narrative — {assemblyResult?.section_root}</DialogTitle>
        <DialogContent dividers>
          {!assemblyResult?.narrative ? (
            <Typography variant="body2" color="text.secondary">
              {assemblyResult?.warning || 'No content.'}
            </Typography>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
                Built from {assemblyResult.drafted_count} drafted requirement response(s).
                Review and copy into your proposal section.
              </Typography>
              <Box sx={{ p: 2, bgcolor: 'rgba(0,0,0,0.02)', borderRadius: 1,
                         maxHeight: 500, overflowY: 'auto',
                         whiteSpace: 'pre-wrap', fontSize: '0.9rem',
                         fontFamily: 'inherit', lineHeight: 1.55 }}>
                {assemblyResult.narrative}
              </Box>
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => {
            navigator.clipboard?.writeText(assemblyResult?.narrative || '');
          }}>Copy</Button>
          {assemblyResult?.narrative && !assemblyResult?.persisted && (
            <Button onClick={() => assembleSection(true)} variant="outlined"
                    startIcon={<SaveNarrativeIcon />} disabled={assembleBusy}>
              {assembleBusy ? 'Saving…' : 'Save narrative'}
            </Button>
          )}
          {assemblyResult?.persisted && (
            <Chip label="Saved to proposal" color="success" size="small"
                  sx={{ mr: 1 }} />
          )}
          <Button onClick={() => setAssemblyResult(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
