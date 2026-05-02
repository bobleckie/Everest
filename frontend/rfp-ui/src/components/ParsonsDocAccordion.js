/**
 * ParsonsDocAccordion
 *
 * Expandable row representing one Parsons knowledge document. Collapsed
 * state mirrors the old table row (filename + category chip + chunks +
 * status). Expanding reveals:
 *
 *   1. A semantic-search bar that queries this document's chunks via
 *      GET /api/parsons-knowledge/documents/:id/content?q=...
 *   2. A quality badge (animated 0-100 dial) with the LLM headline +
 *      a "Reassess" button that calls POST /assess-quality.
 *   3. A "Suggestions" sub-accordion. Each suggestion has accept/edit/
 *      reject/ignore actions wired to POST /suggestions/:sid/disposition.
 *      Accepted suggestions get applied to a chunk + re-embedded.
 *   4. The matching chunks below (top 20).
 *
 * All state changes go through the audit log (server-side); the UI
 * doesn't need to know.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Accordion, AccordionSummary, AccordionDetails, Box, Stack, Typography,
  Chip, IconButton, Tooltip, TextField, InputAdornment, Button,
  CircularProgress, Alert, Paper, Divider, Snackbar,
  Dialog, DialogTitle, DialogContent, DialogActions,
} from '@mui/material';
import {
  ExpandMore as ExpandIcon,
  Search as SearchIcon,
  Refresh as RefreshIcon,
  CheckCircle as AcceptIcon,
  Cancel as RejectIcon,
  VisibilityOff as IgnoreIcon,
  AutoFixHigh as SuggestionsIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  History as HistoryIcon,
} from '@mui/icons-material';
import axios from 'axios';

// ── Helpers ─────────────────────────────────────────────────────────
const SEVERITY_COLOR = {
  critical: '#c62828',
  high:     '#ed6c02',
  medium:   '#0288d1',
  low:      '#616161',
};

function qualityColor(score) {
  if (score == null) return '#9e9e9e';
  if (score >= 80) return '#2e7d32';
  if (score >= 60) return '#43a047';
  if (score >= 40) return '#ed6c02';
  return '#c62828';
}

function qualityBand(score) {
  if (score == null) return 'Not assessed';
  if (score >= 80) return 'Excellent';
  if (score >= 60) return 'Good';
  if (score >= 40) return 'Needs work';
  return 'Poor';
}

// SVG quality dial — 0-100 with animated fill.
function QualityDial({ score }) {
  const pct = Math.max(0, Math.min(100, score || 0));
  const r = 22;
  const c = 2 * Math.PI * r;
  const dash = (pct / 100) * c;
  const color = qualityColor(score);
  return (
    <Box sx={{ position: 'relative', width: 60, height: 60, flexShrink: 0 }}>
      <svg width="60" height="60" viewBox="0 0 60 60">
        <circle cx="30" cy="30" r={r} fill="none" stroke="rgba(0,0,0,0.08)" strokeWidth="6" />
        {score != null && (
          <circle
            cx="30" cy="30" r={r}
            fill="none" stroke={color} strokeWidth="6"
            strokeDasharray={`${dash} ${c}`}
            strokeLinecap="round"
            transform="rotate(-90 30 30)"
            style={{ transition: 'stroke-dasharray 0.7s ease' }}
          />
        )}
      </svg>
      <Box sx={{
        position: 'absolute', inset: 0, display: 'flex',
        alignItems: 'center', justifyContent: 'center',
      }}>
        <Typography variant="caption" fontWeight={800} sx={{ color }}>
          {score != null ? score : '—'}
        </Typography>
      </Box>
    </Box>
  );
}

// ── Edit dialog (used when user clicks "Edit" on a suggestion) ───────
function SuggestionEditDialog({ open, suggestion, onClose, onAccept, busy }) {
  const [text, setText] = useState('');
  useEffect(() => {
    if (suggestion) setText(suggestion.suggested_text || '');
  }, [suggestion]);
  if (!suggestion) return null;
  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>Edit & accept: {suggestion.title}</DialogTitle>
      <DialogContent>
        {suggestion.rationale && (
          <Alert severity="info" sx={{ mb: 2 }}>{suggestion.rationale}</Alert>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          Edit the suggested text below — this is what gets applied to the document.
          A new chunk (or replacement) is created and re-embedded so the AI can cite it.
        </Typography>
        <TextField
          multiline minRows={6} fullWidth autoFocus
          value={text} onChange={(e) => setText(e.target.value)}
          placeholder="Type the content you want appended/replaced…"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained" startIcon={<AcceptIcon />}
          onClick={() => onAccept(text)}
          disabled={busy || !text.trim()}
        >
          {busy ? 'Applying…' : 'Apply edit'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── One suggestion row ──────────────────────────────────────────────
function SuggestionRow({ suggestion, onDispose, busyId }) {
  const sev = SEVERITY_COLOR[suggestion.severity] || SEVERITY_COLOR.medium;
  const busy = busyId === suggestion.id;
  return (
    <Paper variant="outlined" sx={{
      p: 1.5, borderRadius: 2,
      borderLeft: `4px solid ${sev}`,
      opacity: busy ? 0.6 : 1,
    }}>
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} sx={{ mb: 0.5, flexWrap: 'wrap' }}>
            <Chip size="small"
                  label={suggestion.severity}
                  sx={{ height: 20, fontSize: '0.65rem', bgcolor: sev, color: '#fff' }} />
            <Chip size="small" variant="outlined"
                  label={suggestion.edit_kind}
                  sx={{ height: 20, fontSize: '0.65rem' }} />
          </Stack>
          <Typography variant="body2" fontWeight={700}>{suggestion.title}</Typography>
          {suggestion.rationale && (
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', mt: 0.5 }}>
              {suggestion.rationale}
            </Typography>
          )}
          {suggestion.suggested_text && (
            <Box sx={{
              mt: 1, p: 1, borderRadius: 1, bgcolor: 'rgba(0,0,0,0.03)',
              borderLeft: '3px solid rgba(0,174,230,0.5)',
              fontSize: '0.8rem', whiteSpace: 'pre-wrap',
              maxHeight: 120, overflowY: 'auto',
            }}>
              {suggestion.suggested_text}
            </Box>
          )}
        </Box>
        <Stack direction="row" spacing={0.5}>
          <Tooltip title="Accept exactly as suggested">
            <span>
              <IconButton size="small" color="success"
                          disabled={busy}
                          onClick={() => onDispose(suggestion, 'accept')}>
                <AcceptIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Edit then accept">
            <span>
              <IconButton size="small" color="primary"
                          disabled={busy}
                          onClick={() => onDispose(suggestion, 'edit')}>
                <EditIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Reject permanently">
            <span>
              <IconButton size="small" color="error"
                          disabled={busy}
                          onClick={() => onDispose(suggestion, 'reject')}>
                <RejectIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Ignore for now">
            <span>
              <IconButton size="small" disabled={busy}
                          onClick={() => onDispose(suggestion, 'ignore')}>
                <IgnoreIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>
      </Stack>
    </Paper>
  );
}

// ── Main accordion ──────────────────────────────────────────────────
export default function ParsonsDocAccordion({
  doc, categoryLabel, onChanged, onDelete,
}) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState(null);
  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [suggestionsOpen, setSuggestionsOpen] = useState(true);
  const [reassessBusy, setReassessBusy] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [editTarget, setEditTarget] = useState(null);
  const [toast, setToast] = useState(null);

  // Load content (and suggestions) lazily on first expand.
  const loadContent = useCallback(async (q) => {
    setContentLoading(true);
    setContentError(null);
    try {
      const params = q ? { q, limit: 20 } : { limit: 20 };
      const { data } = await axios.get(
        `/api/parsons-knowledge/documents/${doc.id}/content`,
        { params },
      );
      setContent(data);
    } catch (e) {
      setContentError(e?.response?.data?.detail || e.message || 'Failed to load content');
    } finally {
      setContentLoading(false);
    }
  }, [doc.id]);

  const loadSuggestions = useCallback(async () => {
    try {
      const { data } = await axios.get(
        `/api/parsons-knowledge/documents/${doc.id}/suggestions`,
        { params: { status: 'pending' } });
      setSuggestions(data?.suggestions || []);
    } catch { /* non-fatal */ }
  }, [doc.id]);

  useEffect(() => {
    if (open && content === null) {
      loadContent('');
      loadSuggestions();
    }
  }, [open, content, loadContent, loadSuggestions]);

  // Debounced search — query the backend with `?q=` after 350ms idle.
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => setSearchQuery(searchInput.trim()), 350);
    return () => clearTimeout(id);
  }, [searchInput, open]);
  useEffect(() => {
    if (!open) return;
    loadContent(searchQuery);
  }, [searchQuery, open, loadContent]);

  const reassess = async () => {
    setReassessBusy(true);
    try {
      await axios.post(`/api/parsons-knowledge/documents/${doc.id}/assess-quality`);
      setToast('Quality re-assessed.');
      loadSuggestions();
      onChanged && onChanged();
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Reassess failed');
    } finally {
      setReassessBusy(false);
    }
  };

  const dispose = async (suggestion, action, applied_text) => {
    if (action === 'edit') {
      // Open the editor; the edit dialog calls dispose('accept', text) on apply.
      setEditTarget(suggestion);
      return;
    }
    setBusyId(suggestion.id);
    try {
      const body = { action };
      if (action === 'accept') body.applied_text = applied_text || suggestion.suggested_text || '';
      await axios.post(
        `/api/parsons-knowledge/documents/${doc.id}/suggestions/${suggestion.id}/disposition`,
        body);
      setSuggestions(suggestions.filter(s => s.id !== suggestion.id));
      setToast(
        action === 'accept' ? 'Suggestion accepted and applied.'
        : action === 'reject' ? 'Suggestion rejected.'
        : 'Suggestion ignored.'
      );
      onChanged && onChanged();
      // If the doc text changed, refresh the content panel too.
      if (action === 'accept') loadContent(searchQuery);
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Action failed');
    } finally {
      setBusyId(null);
      setEditTarget(null);
    }
  };

  const onAcceptEdited = (text) => {
    dispose(editTarget, 'accept', text);
  };

  const pendingCount = suggestions.length;

  return (
    <>
      <Accordion
        expanded={open}
        onChange={(_, v) => setOpen(v)}
        variant="outlined"
        sx={{ mb: 1, borderRadius: 2, '&:before': { display: 'none' } }}
      >
        <AccordionSummary expandIcon={<ExpandIcon />}>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ width: '100%' }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="body2" fontWeight={700} noWrap>
                {doc.original_filename}
              </Typography>
              {doc.description && (
                <Typography variant="caption" color="text.secondary" noWrap>
                  {doc.description}
                </Typography>
              )}
            </Box>
            <Chip size="small" label={categoryLabel || doc.parsons_category} />
            {doc.practice_area && (
              <Chip size="small" variant="outlined" label={doc.practice_area} />
            )}
            {(doc.jurisdictions || []).slice(0, 2).map(j => (
              <Chip key={j} size="small" variant="outlined" label={j} />
            ))}
            <Tooltip title={qualityBand(doc.quality_score)}>
              <Box sx={{
                display: 'flex', alignItems: 'center', gap: 0.5,
                px: 1, py: 0.25, borderRadius: 1,
                bgcolor: `${qualityColor(doc.quality_score)}15`,
                color: qualityColor(doc.quality_score),
                fontWeight: 700, fontSize: '0.85rem',
                minWidth: 56, justifyContent: 'center',
              }}>
                {doc.quality_score != null ? doc.quality_score : '—'}
              </Box>
            </Tooltip>
            {doc.pending_suggestion_count > 0 && (
              <Chip size="small" color="warning" icon={<SuggestionsIcon sx={{ fontSize: 14 }} />}
                    label={`${doc.pending_suggestion_count} suggestion${doc.pending_suggestion_count === 1 ? '' : 's'}`} />
            )}
            <Tooltip title="Delete document">
              <IconButton size="small"
                          onClick={(e) => { e.stopPropagation(); onDelete && onDelete(doc); }}>
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        </AccordionSummary>
        <AccordionDetails sx={{ pt: 0 }}>
          <Divider sx={{ mb: 2 }} />

          {/* Quality summary */}
          <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
            <QualityDial score={doc.quality_score} />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary"
                          sx={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>
                Quality — {qualityBand(doc.quality_score)}
              </Typography>
              <Typography variant="body2" fontWeight={700}>
                {doc.quality_headline
                  || (doc.quality_score == null
                      ? 'Not yet assessed — click Reassess to score this document.'
                      : '(no headline)')}
              </Typography>
              {doc.quality_assessed_at && (
                <Typography variant="caption" color="text.secondary">
                  Assessed {new Date(doc.quality_assessed_at).toLocaleString()}
                </Typography>
              )}
            </Box>
            <Button
              size="small" variant="outlined" startIcon={<RefreshIcon />}
              onClick={reassess} disabled={reassessBusy}
            >
              {reassessBusy ? 'Assessing…' : 'Reassess'}
            </Button>
          </Stack>

          {/* Search bar */}
          <TextField
            fullWidth size="small" placeholder="Search this document semantically…"
            value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
            InputProps={{
              startAdornment: <InputAdornment position="start"><SearchIcon /></InputAdornment>,
              endAdornment: searchInput && (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={() => setSearchInput('')}>×</IconButton>
                </InputAdornment>
              ),
            }}
            sx={{ mb: 2 }}
          />

          {/* Suggestions sub-accordion */}
          <Accordion expanded={suggestionsOpen}
                     onChange={(_, v) => setSuggestionsOpen(v)}
                     variant="outlined"
                     sx={{ mb: 2, '&:before': { display: 'none' } }}>
            <AccordionSummary expandIcon={<ExpandIcon />}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <SuggestionsIcon color="warning" fontSize="small" />
                <Typography variant="body2" fontWeight={700}>
                  Suggestions to better inform the AI
                </Typography>
                {pendingCount > 0 && (
                  <Chip size="small" color="warning" label={pendingCount} />
                )}
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              {pendingCount === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                  No pending suggestions. Click Reassess above to get a fresh set.
                </Typography>
              ) : (
                <Stack spacing={1}>
                  {suggestions.map(s => (
                    <SuggestionRow key={s.id} suggestion={s}
                                   onDispose={(s, action) => dispose(s, action)}
                                   busyId={busyId} />
                  ))}
                </Stack>
              )}
            </AccordionDetails>
          </Accordion>

          {/* Content / search results */}
          <Box>
            <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="overline" color="text.secondary" sx={{ flex: 1 }}>
                {searchQuery
                  ? `Top matches for "${searchQuery}"`
                  : 'Document content'}
              </Typography>
              {content?.total_chunks != null && (
                <Typography variant="caption" color="text.secondary">
                  {content.chunk_count} of {content.total_chunks} chunks shown
                </Typography>
              )}
            </Stack>
            {contentLoading ? (
              <Box sx={{ p: 2, textAlign: 'center' }}><CircularProgress size={20} /></Box>
            ) : contentError ? (
              <Alert severity="error">{contentError}</Alert>
            ) : !content?.chunks?.length ? (
              <Typography variant="body2" color="text.secondary">
                No content yet — wait for ingestion to finish, or try a different search.
              </Typography>
            ) : (
              <Stack spacing={1} sx={{ maxHeight: 360, overflowY: 'auto', pr: 1 }}>
                {content.chunks.map(ch => (
                  <Paper key={ch.id} variant="outlined" sx={{ p: 1.25, borderRadius: 1.5 }}>
                    <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">
                        chunk #{ch.chunk_index}{ch.page_number ? ` · page ${ch.page_number}` : ''}
                      </Typography>
                      {ch.similarity != null && (
                        <Chip size="small" variant="outlined"
                              label={`sim ${ch.similarity.toFixed(2)}`}
                              sx={{ height: 18, fontSize: '0.65rem' }} />
                      )}
                    </Stack>
                    <Typography variant="body2" sx={{
                      whiteSpace: 'pre-wrap', fontSize: '0.85rem', lineHeight: 1.45,
                    }}>
                      {ch.content}
                    </Typography>
                  </Paper>
                ))}
              </Stack>
            )}
          </Box>
        </AccordionDetails>
      </Accordion>

      <SuggestionEditDialog
        open={!!editTarget}
        suggestion={editTarget}
        onClose={() => setEditTarget(null)}
        onAccept={onAcceptEdited}
        busy={busyId === editTarget?.id}
      />
      <Snackbar open={!!toast} autoHideDuration={3500} onClose={() => setToast(null)}
                message={toast || ''} />
    </>
  );
}
