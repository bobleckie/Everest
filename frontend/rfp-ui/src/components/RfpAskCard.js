/**
 * RfpAskCard — chat-style Q&A surface that lets a user ask any question
 * about the current proposal's RFP. The agent vector-searches the RFP
 * documents and replies with an answer grounded in inline citations.
 *
 * Wired to: POST /api/parsons-response/proposals/{id}/ask
 *
 * Drops onto the RfpWorkspaceDashboard.
 */
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Card, CardContent, Stack, Typography, Chip, IconButton, Tooltip,
  CircularProgress, TextField, Button, Divider, Collapse,
  Dialog, DialogTitle, DialogContent, DialogActions, Alert, MenuItem,
  Select, FormControl, InputLabel,
} from '@mui/material';
import {
  QuestionAnswer as AskIcon,
  Send as SendIcon,
  Article as ExcerptIcon,
  RestartAlt as ClearIcon,
  ExpandMore as ExpandIcon,
  ExpandLess as CollapseIcon,
  AddTask as PromoteIcon,
} from '@mui/icons-material';
import axios from 'axios';
// Markdown rendering for the agent's answers. Without this the user
// sees literal "**bold**", "##" headers, and "|" table syntax instead
// of formatted output. remark-gfm enables tables, strikethrough,
// task-lists, autolinks.
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const SUGGESTED_QUESTIONS = [
  'What is the proposal due date?',
  'What forms must be submitted with the quote?',
  'How are vendors evaluated?',
  'What is the bid bond requirement?',
  'What is the contract duration including option years?',
];

function CitationChip({ c, onClick }) {
  return (
    <Tooltip title={`${c.document_name}${c.page ? ' p.' + c.page : ''} (sim ${(c.similarity * 100).toFixed(0)}%)`}>
      <Chip
        size="small"
        icon={<ExcerptIcon />}
        label={`doc=${c.document_id} p=${c.page || '?'}`}
        onClick={() => onClick && onClick(c)}
        sx={{
          height: 20, fontSize: '0.65rem', cursor: 'pointer',
          bgcolor: 'rgba(0,174,230,0.08)',
          '&:hover': { bgcolor: 'rgba(0,174,230,0.18)' },
        }}
      />
    </Tooltip>
  );
}

function CitationDetail({ c, expanded, onToggle }) {
  return (
    <Box sx={{
      mt: 0.5, p: 1, borderRadius: 1.5,
      border: '1px solid rgba(0,0,0,0.08)',
      bgcolor: 'rgba(0,0,0,0.015)',
    }}>
      <Stack direction="row" alignItems="center" spacing={1}
             onClick={onToggle}
             sx={{ cursor: 'pointer' }}>
        <Chip size="small" label={`sim ${(c.similarity * 100).toFixed(0)}%`}
              sx={{ height: 18, fontSize: '0.62rem' }} />
        <Typography variant="caption" sx={{ flex: 1 }} noWrap>
          {c.document_name}{c.page ? ` · p.${c.page}` : ''}
        </Typography>
        {expanded ? <CollapseIcon fontSize="small" /> : <ExpandIcon fontSize="small" />}
      </Stack>
      <Collapse in={expanded}>
        <Typography variant="body2" sx={{
          mt: 1, whiteSpace: 'pre-wrap', fontSize: '0.85rem',
          fontFamily: 'inherit', lineHeight: 1.45,
        }}>
          {c.snippet}
        </Typography>
      </Collapse>
    </Box>
  );
}

// One Q+A turn
// Replace any "[doc=N p=P]" tokens in a plain-text string with styled
// pill spans. Used by the markdown renderer's text node override below
// so citations stay visually distinct even when the agent embeds them
// inside paragraphs, headers, or table cells.
function linkifyCitations(value) {
  if (typeof value !== 'string' || !value) return value;
  if (!value.includes('[doc=')) return value;
  const parts = value.split(/(\[doc=\d+\s+p=[^\]]+\])/g);
  return parts.map((part, i) => {
    const m = /^\[doc=(\d+)\s+p=([^\]]+)\]$/.exec(part);
    if (m) {
      return (
        <Box key={i} component="span" sx={{
          px: 0.4, py: 0, mx: 0.25, borderRadius: 0.5,
          fontSize: '0.7rem', fontFamily: 'monospace',
          bgcolor: 'rgba(0,174,230,0.12)', color: '#00667e',
          whiteSpace: 'nowrap',
        }}>
          doc={m[1]} p={m[2]}
        </Box>
      );
    }
    return part;
  });
}

// MUI-styled overrides for the markdown elements. Keeps tables readable
// (the agent loves tables for SLA / LD enumeration), and bumps headers
// down a notch so an "## H2" doesn't dominate inside a card.
const MARKDOWN_COMPONENTS = {
  h1: ({ node, ...props }) => (
    <Typography variant="h6" component="h3" sx={{ mt: 1.5, mb: 0.75, fontWeight: 700 }} {...props} />
  ),
  h2: ({ node, ...props }) => (
    <Typography variant="subtitle1" component="h4" sx={{ mt: 1.5, mb: 0.5, fontWeight: 700 }} {...props} />
  ),
  h3: ({ node, ...props }) => (
    <Typography variant="subtitle2" component="h5" sx={{ mt: 1.25, mb: 0.5, fontWeight: 700 }} {...props} />
  ),
  h4: ({ node, ...props }) => (
    <Typography variant="body2" component="h6" sx={{ mt: 1, mb: 0.5, fontWeight: 700 }} {...props} />
  ),
  p: ({ node, ...props }) => (
    <Typography variant="body2" component="p" sx={{ my: 0.75, lineHeight: 1.55 }} {...props} />
  ),
  ul: ({ node, ...props }) => (
    <Box component="ul" sx={{ pl: 3, my: 0.5 }} {...props} />
  ),
  ol: ({ node, ...props }) => (
    <Box component="ol" sx={{ pl: 3, my: 0.5 }} {...props} />
  ),
  li: ({ node, ...props }) => (
    <Box component="li" sx={{ mb: 0.25, fontSize: '0.875rem', lineHeight: 1.55 }} {...props} />
  ),
  strong: ({ node, ...props }) => (
    <Box component="strong" sx={{ fontWeight: 700 }} {...props} />
  ),
  em: ({ node, ...props }) => (
    <Box component="em" sx={{ fontStyle: 'italic' }} {...props} />
  ),
  hr: () => <Divider sx={{ my: 1.5 }} />,
  blockquote: ({ node, ...props }) => (
    <Box component="blockquote" sx={{
      borderLeft: '3px solid', borderColor: 'rgba(0,0,0,0.18)',
      pl: 1.5, my: 1, color: 'text.secondary', fontStyle: 'italic',
    }} {...props} />
  ),
  code: ({ node, inline, ...props }) => (
    inline ? (
      <Box component="code" sx={{
        px: 0.5, py: 0.1, borderRadius: 0.5,
        bgcolor: 'rgba(0,0,0,0.06)',
        fontFamily: 'monospace', fontSize: '0.82em',
      }} {...props} />
    ) : (
      <Box component="pre" sx={{
        p: 1, my: 1, borderRadius: 1,
        bgcolor: 'rgba(0,0,0,0.06)', overflowX: 'auto',
        fontFamily: 'monospace', fontSize: '0.82rem',
      }}><code {...props} /></Box>
    )
  ),
  table: ({ node, ...props }) => (
    <Box sx={{ overflowX: 'auto', my: 1 }}>
      <Box component="table" sx={{
        borderCollapse: 'collapse', width: '100%',
        '& th, & td': {
          border: '1px solid rgba(0,0,0,0.15)',
          padding: '6px 8px',
          fontSize: '0.82rem',
          textAlign: 'left',
          verticalAlign: 'top',
        },
        '& th': {
          bgcolor: 'rgba(0,174,230,0.08)',
          fontWeight: 700,
        },
      }} {...props} />
    </Box>
  ),
  // Custom text node — runs every textual leaf through the citation
  // linkifier so [doc=N p=P] tokens render as styled pills no matter
  // where they appear (paragraph, header, table cell, list item).
  text: ({ value }) => linkifyCitations(value || ''),
};

function TurnImpl({ turn, expandedCitations, toggleCitation, onPromote }) {

  return (
    <Box sx={{ mb: 2 }}>
      {/* User question */}
      <Box sx={{
        p: 1.25, borderRadius: 1.5, mb: 1,
        bgcolor: 'rgba(0,174,230,0.06)',
        border: '1px solid rgba(0,174,230,0.2)',
      }}>
        <Typography variant="caption" color="text.secondary"
                    sx={{ display: 'block', mb: 0.25, textTransform: 'uppercase',
                          letterSpacing: 0.4, fontSize: '0.6rem' }}>
          You asked
        </Typography>
        <Typography variant="body2" fontWeight={600}>{turn.question}</Typography>
      </Box>

      {/* Answer */}
      <Box sx={{
        p: 1.25, borderRadius: 1.5,
        bgcolor: 'rgba(46,125,50,0.04)',
        border: '1px solid rgba(46,125,50,0.18)',
      }}>
        <Typography variant="caption" color="text.secondary"
                    sx={{ display: 'block', mb: 0.5, textTransform: 'uppercase',
                          letterSpacing: 0.4, fontSize: '0.6rem' }}>
          Agent answer
        </Typography>
        {turn.error ? (
          <Typography variant="body2" color="error">
            {turn.error}
          </Typography>
        ) : (
          <Box sx={{
            // Tighten the first/last child margins so the rendered
            // markdown sits flush with the answer card padding.
            '& > *:first-of-type': { mt: 0 },
            '& > *:last-child': { mb: 0 },
          }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={MARKDOWN_COMPONENTS}
            >
              {turn.answer || ''}
            </ReactMarkdown>
          </Box>
        )}
        {/* Search details hidden by default — internal diagnostics only */}
        {turn.citations && turn.citations.length > 0 && (
          <Box sx={{ mt: 1.25 }}>
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', mb: 0.5 }}>
              {turn.citations.length} RFP excerpt{turn.citations.length === 1 ? '' : 's'} cited:
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
              {turn.citations.map((c, i) => (
                <CitationChip
                  key={`${c.chunk_id}-${i}`}
                  c={c}
                  onClick={(c) => toggleCitation(turn.id, c.chunk_id)}
                />
              ))}
            </Stack>
            {turn.citations.map((c, i) => {
              const key = `${turn.id}::${c.chunk_id}`;
              if (!expandedCitations[key]) return null;
              return (
                <CitationDetail
                  key={`detail-${key}`}
                  c={c}
                  expanded
                  onToggle={() => toggleCitation(turn.id, c.chunk_id)}
                />
              );
            })}
          </Box>
        )}

        {/* Promote-to-question affordance */}
        {!turn.error && turn.answer && onPromote && (
          <Box sx={{ mt: 1.25, display: 'flex', justifyContent: 'flex-end' }}>
            {turn.promotedQuestionId ? (
              <Chip
                size="small"
                color="success"
                icon={<PromoteIcon />}
                label={`Saved as agency question #${turn.promotedQuestionId}`}
                sx={{ height: 24 }}
              />
            ) : (
              <Tooltip title="Save this finding as an approved agency-submission question">
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<PromoteIcon />}
                  onClick={() => onPromote(turn)}
                  sx={{
                    borderRadius: 2, textTransform: 'none', fontSize: '0.78rem',
                    borderColor: 'rgba(46,125,50,0.45)', color: '#2e7d32',
                    '&:hover': { borderColor: '#2e7d32', bgcolor: 'rgba(46,125,50,0.06)' },
                  }}
                >
                  Save as agency question
                </Button>
              </Tooltip>
            )}
          </Box>
        )}
      </Box>
    </Box>
  );
}

// React.memo so the conversation history doesn't re-render on every keystroke
// in the question input. Custom comparator: only re-render this turn when its
// own data changed OR when the expansion state for ITS citations changed.
const Turn = React.memo(TurnImpl, (prev, next) => {
  if (prev.turn !== next.turn) return false;
  if (prev.toggleCitation !== next.toggleCitation) return false;
  if (prev.onPromote !== next.onPromote) return false;
  // Only the keys belonging to THIS turn matter for re-render.
  const turnId = next.turn.id;
  const prevExp = prev.expandedCitations || {};
  const nextExp = next.expandedCitations || {};
  for (const k of Object.keys(prevExp)) {
    if (k.startsWith(`${turnId}::`) && prevExp[k] !== nextExp[k]) return false;
  }
  for (const k of Object.keys(nextExp)) {
    if (k.startsWith(`${turnId}::`) && prevExp[k] !== nextExp[k]) return false;
  }
  return true; // skip render
});

// ── Promote-to-Question dialog ──────────────────────────────────────
function PromoteDialog({ open, onClose, proposalId, sourceTurn, onSaved }) {
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState('idle'); // idle | duplicate | saved | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [category, setCategory] = useState('clarification');
  const [priority, setPriority] = useState('medium');

  // Reset when opened with a new turn
  useEffect(() => {
    if (open) {
      setStage('idle');
      setResult(null);
      setError(null);
      setCategory('clarification');
      setPriority('medium');
    }
  }, [open, sourceTurn?.id]);

  const submit = async (force) => {
    if (!sourceTurn) return;
    setBusy(true);
    setError(null);
    try {
      const payload = {
        user_question: sourceTurn.question,
        agent_answer: sourceTurn.answer,
        citations: (sourceTurn.citations || []).map((c) => ({
          document_id: c.document_id,
          page: c.page,
          snippet: c.snippet,
          similarity: c.similarity,
        })),
        category,
        priority,
        force: !!force,
        polish: true,
      };
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/promote-question`,
        payload,
      );
      setResult(data);
      if (data.mode === 'duplicate') {
        setStage('duplicate');
      } else {
        setStage('saved');
        if (onSaved && data.saved_question?.id) {
          onSaved(sourceTurn.id, data.saved_question.id);
        }
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Save failed');
      setStage('error');
    } finally {
      setBusy(false);
    }
  };

  const handleClose = () => {
    if (busy) return;
    onClose();
  };

  const polished = result?.polished_question_text || sourceTurn?.question || '';

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <PromoteIcon color="success" />
          <Typography variant="h6" fontWeight={700}>
            Save as Agency Question
          </Typography>
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        {stage === 'idle' && (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              We'll polish your finding into a submission-ready clarifying
              question, check it doesn't duplicate an existing question, and
              save it to the question bank as <b>approved</b>.
            </Typography>
            <Box sx={{
              p: 1.25, borderRadius: 1.5, mb: 2,
              bgcolor: 'rgba(0,174,230,0.06)',
              border: '1px solid rgba(0,174,230,0.2)',
            }}>
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5,
                                textTransform: 'uppercase', letterSpacing: 0.4,
                                fontSize: '0.6rem' }}>
                Your original ask
              </Typography>
              <Typography variant="body2">{sourceTurn?.question}</Typography>
            </Box>
            <Stack direction="row" spacing={1.5}>
              <FormControl size="small" sx={{ minWidth: 160 }}>
                <InputLabel>Category</InputLabel>
                <Select
                  label="Category"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                >
                  <MenuItem value="clarification">Clarification</MenuItem>
                  <MenuItem value="risk">Risk</MenuItem>
                  <MenuItem value="pricing">Pricing</MenuItem>
                  <MenuItem value="scope">Scope</MenuItem>
                  <MenuItem value="competitive">Competitive</MenuItem>
                  <MenuItem value="form">Form</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 140 }}>
                <InputLabel>Priority</InputLabel>
                <Select
                  label="Priority"
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                >
                  <MenuItem value="critical">Critical</MenuItem>
                  <MenuItem value="high">High</MenuItem>
                  <MenuItem value="medium">Medium</MenuItem>
                  <MenuItem value="low">Low</MenuItem>
                </Select>
              </FormControl>
            </Stack>
            {(sourceTurn?.citations?.length || 0) > 0 && (
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mt: 1.5 }}>
                {sourceTurn.citations.length} citation
                {sourceTurn.citations.length === 1 ? '' : 's'} from the
                agent's answer will be carried over as the source anchor.
              </Typography>
            )}
          </>
        )}

        {stage === 'duplicate' && result && (
          <>
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              An existing question on this proposal already covers this topic
              ({(result.duplicate.similarity * 100).toFixed(0)}% match).
            </Alert>
            <Box sx={{
              p: 1.25, borderRadius: 1.5, mb: 1.5,
              bgcolor: 'rgba(46,125,50,0.04)',
              border: '1px solid rgba(46,125,50,0.18)',
            }}>
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5,
                                textTransform: 'uppercase', letterSpacing: 0.4,
                                fontSize: '0.6rem' }}>
                Polished version of your ask
              </Typography>
              <Typography variant="body2">{polished}</Typography>
            </Box>
            <Box sx={{
              p: 1.25, borderRadius: 1.5,
              bgcolor: 'rgba(255,193,7,0.07)',
              border: '1px solid rgba(255,193,7,0.35)',
            }}>
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5,
                                textTransform: 'uppercase', letterSpacing: 0.4,
                                fontSize: '0.6rem' }}>
                Existing question #{result.duplicate.id} ({result.duplicate.status})
              </Typography>
              <Typography variant="body2">{result.duplicate.question_text}</Typography>
            </Box>
          </>
        )}

        {stage === 'saved' && result && (
          <>
            <Alert severity="success" sx={{ mb: 1.5 }}>
              Saved as question #{result.saved_question?.id} at status
              <b> {result.status}</b>.
            </Alert>
            <Box sx={{
              p: 1.25, borderRadius: 1.5,
              bgcolor: 'rgba(46,125,50,0.04)',
              border: '1px solid rgba(46,125,50,0.18)',
            }}>
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mb: 0.5,
                                textTransform: 'uppercase', letterSpacing: 0.4,
                                fontSize: '0.6rem' }}>
                Final question text
              </Typography>
              <Typography variant="body2">
                {result.saved_question?.question_text}
              </Typography>
            </Box>
            {result.notes && (
              <Typography variant="caption" color="text.secondary"
                          sx={{ display: 'block', mt: 1 }}>
                {result.notes}
              </Typography>
            )}
          </>
        )}

        {stage === 'error' && (
          <Alert severity="error">{error || 'Save failed'}</Alert>
        )}

        {busy && (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1.5 }}>
            <CircularProgress size={16} />
            <Typography variant="caption" color="text.secondary">
              Polishing, deduping, and saving…
            </Typography>
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {stage === 'idle' && (
          <>
            <Button onClick={handleClose} disabled={busy}>Cancel</Button>
            <Button variant="contained" color="success"
                    disabled={busy} onClick={() => submit(false)}>
              Save as Approved
            </Button>
          </>
        )}
        {stage === 'duplicate' && (
          <>
            <Button onClick={handleClose} disabled={busy}>
              Use existing
            </Button>
            <Button variant="contained" color="warning"
                    disabled={busy} onClick={() => submit(true)}>
              Save anyway
            </Button>
          </>
        )}
        {(stage === 'saved' || stage === 'error') && (
          <Button onClick={handleClose}>Close</Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

export default function RfpAskCard({ proposalId }) {
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [expandedCitations, setExpandedCitations] = useState({});
  const [promoteTurn, setPromoteTurn] = useState(null);
  const turnsEndRef = useRef(null);

  useEffect(() => {
    turnsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns.length]);

  const ask = async (q) => {
    const text = (q || question).trim();
    if (!text) return;
    setBusy(true);
    const turnId = `t-${Date.now()}`;
    // Build history from completed prior turns (alternating user/agent)
    const history = [];
    for (const t of turns) {
      if (t.pending || t.error) continue;
      history.push({ role: 'user', content: t.question });
      if (t.answer) history.push({ role: 'agent', content: t.answer });
    }
    setTurns((ts) => [...ts, {
      id: turnId, question: text, answer: '', citations: [], pending: true,
    }]);
    setQuestion('');
    try {
      const { data } = await axios.post(
        `/api/parsons-response/proposals/${proposalId}/ask`,
        { question: text, top_k: 10, history });
      setTurns((ts) => ts.map((t) => t.id === turnId ? {
        ...t, answer: data.answer, citations: data.citations,
        queries_used: data.queries_used,
        per_query_counts: data.per_query_counts,
        pending: false,
      } : t));
    } catch (e) {
      setTurns((ts) => ts.map((t) => t.id === turnId ? {
        ...t, error: e?.response?.data?.detail || 'Ask failed',
        pending: false,
      } : t));
    } finally {
      setBusy(false);
    }
  };

  // Stable callback identities so the memoized Turn component below
  // doesn't re-render on every parent keystroke.
  const toggleCitation = useCallback((turnId, chunkId) => {
    const key = `${turnId}::${chunkId}`;
    setExpandedCitations((m) => ({ ...m, [key]: !m[key] }));
  }, []);

  const handlePromote = useCallback((turn) => {
    setPromoteTurn(turn);
  }, []);

  const handlePromoteSaved = useCallback((turnId, savedQuestionId) => {
    setTurns((ts) => ts.map((t) => t.id === turnId
      ? { ...t, promotedQuestionId: savedQuestionId }
      : t));
  }, []);

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  };

  return (
    <Card sx={{ borderRadius: 3, mb: 3,
                background: 'linear-gradient(135deg, #fff, rgba(0,174,230,0.04))',
                border: '1px solid rgba(0,174,230,0.18)' }}>
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
          <Box sx={{
            width: 40, height: 40, borderRadius: 2,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: '#00AEE6', color: '#fff',
          }}><AskIcon /></Box>
          <Box sx={{ flex: 1 }}>
            <Typography variant="overline" color="text.secondary"
                        sx={{ display: 'block', lineHeight: 1.1 }}>
              Ask the RFP
            </Typography>
            <Typography variant="h6" fontWeight={800}>
              Vector-grounded Q&A on this RFP
            </Typography>
          </Box>
          {turns.length > 0 && (
            <Tooltip title="Clear conversation">
              <IconButton onClick={() => { setTurns([]); setExpandedCitations({}); }}>
                <ClearIcon />
              </IconButton>
            </Tooltip>
          )}
        </Stack>

        {turns.length === 0 ? (
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              Ask anything about the current RFP — the agent vector-searches
              every ingested RFP document and replies with answers grounded
              in inline citations to the exact RFP text.
            </Typography>
            <Typography variant="caption" color="text.secondary"
                        sx={{ display: 'block', mb: 0.75 }}>
              Try one of these:
            </Typography>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              {SUGGESTED_QUESTIONS.map((q, i) => (
                <Chip
                  key={i}
                  label={q}
                  size="small"
                  onClick={() => ask(q)}
                  disabled={busy}
                  sx={{ cursor: 'pointer',
                        '&:hover': { bgcolor: 'rgba(0,174,230,0.12)' } }}
                />
              ))}
            </Stack>
          </Box>
        ) : (
          <Box sx={{ maxHeight: 500, overflowY: 'auto', pr: 1, mb: 1.5 }}>
            {turns.map((turn) => (
              turn.pending ? (
                <Box key={turn.id} sx={{ mb: 2 }}>
                  <Box sx={{
                    p: 1.25, borderRadius: 1.5, mb: 1,
                    bgcolor: 'rgba(0,174,230,0.06)',
                    border: '1px solid rgba(0,174,230,0.2)',
                  }}>
                    <Typography variant="body2" fontWeight={600}>{turn.question}</Typography>
                  </Box>
                  <Stack direction="row" spacing={1} alignItems="center"
                         sx={{ p: 1.25, color: 'text.secondary' }}>
                    <CircularProgress size={16} />
                    <Typography variant="caption">Searching RFP and synthesizing answer…</Typography>
                  </Stack>
                </Box>
              ) : (
                <Turn key={turn.id} turn={turn}
                      expandedCitations={expandedCitations}
                      toggleCitation={toggleCitation}
                      onPromote={handlePromote} />
              )
            ))}
            <div ref={turnsEndRef} />
          </Box>
        )}

        <Divider sx={{ mb: 1.5 }} />

        <Stack direction="row" spacing={1} alignItems="flex-end">
          <TextField
            multiline maxRows={4} fullWidth size="small"
            placeholder="Ask the RFP… (Enter to send, Shift+Enter for newline)"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKey}
            disabled={busy}
            sx={{ '& .MuiInputBase-root': { borderRadius: 2 } }}
          />
          <Button variant="contained"
                  endIcon={<SendIcon />}
                  onClick={() => ask()}
                  disabled={busy || !question.trim()}
                  sx={{ borderRadius: 2, height: 40 }}>
            Ask
          </Button>
        </Stack>
      </CardContent>

      <PromoteDialog
        open={!!promoteTurn}
        onClose={() => setPromoteTurn(null)}
        proposalId={proposalId}
        sourceTurn={promoteTurn}
        onSaved={handlePromoteSaved}
      />
    </Card>
  );
}
