/**
 * Flashcards page — RFP comprehension testing.
 *
 * Two views:
 *   1) DECK PICKER  — table of sections with card counts, plus the
 *      "Generate cards from Section 4" button that fires the LLM job.
 *   2) STUDY MODE   — full-card flip view with Again/Good/Easy SM-2.
 *
 * Backed by /api/flashcards (added in step #500).
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Typography, Card, CardContent, Button, Chip, LinearProgress,
  Stack, Alert, Fade, IconButton, Tooltip, TextField, MenuItem,
  Dialog, DialogTitle, DialogContent, DialogActions, Divider,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
} from '@mui/material';
import {
  AutoAwesome as GenerateIcon,
  Refresh as RefreshIcon,
  PlayArrow as StudyIcon,
  Close as CloseIcon,
  Replay as RestartIcon,
  School as SchoolIcon,
} from '@mui/icons-material';
import axios from 'axios';
import { useProposal } from '../proposal/ProposalContext';

const RATING_BUTTONS = [
  { rating: 'again', label: 'Again',  color: 'error',   help: 'Got it wrong / forgot. Show again soon.' },
  { rating: 'good',  label: 'Good',   color: 'primary', help: 'Got it. Standard interval.' },
  { rating: 'easy',  label: 'Easy',   color: 'success', help: 'Trivially easy. Push the interval out further.' },
];

const CARD_TYPE_LABEL = {
  recall: 'Recall',
  concept: 'Concept',
  numeric: 'Numeric',
  cross_reference: 'Cross-ref',
};

export default function Flashcards() {
  const { proposalId, proposal } = useProposal();

  const [sections, setSections] = useState([]);     // [{section_id, total, due}]
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

  // Generation
  const [genDialog, setGenDialog] = useState(false);
  const [genMaxReqs, setGenMaxReqs] = useState(50);  // safe default — small first batch
  const [genJobId, setGenJobId] = useState(null);
  const [genJob, setGenJob] = useState(null);

  // Study mode
  const [studyOpen, setStudyOpen] = useState(false);
  const [studyDeck, setStudyDeck] = useState([]);    // array of cards
  const [studyIndex, setStudyIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [studyFilter, setStudyFilter] = useState({ section_prefix: '4', due_only: false });

  const fetchSections = useCallback(async () => {
    if (!proposalId) { setSections([]); setLoading(false); return; }
    try {
      const { data } = await axios.get(`/api/flashcards/sections?proposal_id=${proposalId}`);
      setSections(data.sections || []);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load deck summary');
    } finally {
      setLoading(false);
    }
  }, [proposalId]);

  useEffect(() => { fetchSections(); }, [fetchSections]);

  // Poll the generation job while it's running.
  useEffect(() => {
    if (!genJobId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const { data } = await axios.get(`/api/jobs/${genJobId}`);
        if (cancelled) return;
        setGenJob(data);
        if (data.status === 'done' || data.status === 'error') {
          // Refresh the section list and stop polling.
          await fetchSections();
          if (data.status === 'done') {
            const created = data.result?.cards_created ?? 0;
            const errs = data.result?.errors_count ?? 0;
            setInfo(`Generated ${created} flashcard(s)${errs ? ` (${errs} errors)` : ''}.`);
          } else {
            setError(`Generation failed: ${data.error || 'unknown error'}`);
          }
          setGenJobId(null);
        } else {
          setTimeout(tick, 2000);
        }
      } catch (e) {
        if (!cancelled) {
          setError(`Job poll failed: ${e.response?.data?.detail || e.message}`);
          setGenJobId(null);
        }
      }
    };
    tick();
    return () => { cancelled = true; };
  }, [genJobId, fetchSections]);

  const handleGenerate = async () => {
    setWorking(true); setError(''); setInfo('');
    try {
      const { data } = await axios.post('/api/flashcards/generate', {
        proposal_id: proposalId,
        section_prefix: '4',
        max_requirements: genMaxReqs > 0 ? genMaxReqs : null,
      });
      setGenJobId(data.job_id);
      setGenJob({ status: 'queued' });
      setGenDialog(false);
      setInfo('Generation started. This page will update when the job finishes.');
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start generation');
    } finally {
      setWorking(false);
    }
  };

  const startStudy = async (sectionPrefix, dueOnly = false) => {
    setWorking(true); setError(''); setInfo('');
    try {
      const params = new URLSearchParams();
      if (proposalId) params.set('proposal_id', String(proposalId));
      if (sectionPrefix) params.set('section_prefix', sectionPrefix);
      if (dueOnly) params.set('due_only', 'true');
      params.set('limit', '500');
      const { data } = await axios.get(`/api/flashcards?${params.toString()}`);
      const items = data.items || [];
      if (items.length === 0) {
        setInfo(dueOnly ? 'No cards due for review right now.' : 'No cards in this deck yet.');
        return;
      }
      // Shuffle so review order varies session-to-session.
      const shuffled = [...items].sort(() => Math.random() - 0.5);
      setStudyDeck(shuffled);
      setStudyIndex(0);
      setFlipped(false);
      setStudyFilter({ section_prefix: sectionPrefix || '4', due_only: dueOnly });
      setStudyOpen(true);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load study deck');
    } finally {
      setWorking(false);
    }
  };

  const rateCurrent = async (rating) => {
    const card = studyDeck[studyIndex];
    if (!card) return;
    try {
      await axios.post(`/api/flashcards/${card.id}/review`, { rating });
      // Advance to next card.
      const next = studyIndex + 1;
      if (next >= studyDeck.length) {
        setStudyOpen(false);
        await fetchSections();
        setInfo(`Session complete — reviewed ${studyDeck.length} card(s).`);
      } else {
        setStudyIndex(next);
        setFlipped(false);
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to record review');
    }
  };

  // Aggregate stats for the header strip.
  const totals = useMemo(() => {
    const t = sections.reduce((m, s) => m + s.total, 0);
    const d = sections.reduce((m, s) => m + s.due, 0);
    return { total: t, due: d };
  }, [sections]);

  const studyCard = studyDeck[studyIndex];

  return (
    <Fade in timeout={400}>
      <Box sx={{ p: 3, maxWidth: 1200, mx: 'auto' }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 3 }}>
          <Box>
            <Typography variant="h4" fontWeight={700}>
              <SchoolIcon sx={{ verticalAlign: 'middle', mr: 1 }} />
              RFP Flashcards
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Comprehension testing on the Statement of Work (Section 4 only — bid mechanics excluded).
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button startIcon={<RefreshIcon />} onClick={fetchSections} disabled={working}>
              Refresh
            </Button>
            <Button
              variant="outlined"
              startIcon={<StudyIcon />}
              disabled={working || totals.due === 0}
              onClick={() => startStudy('4', true)}
            >
              Study due ({totals.due})
            </Button>
            <Button
              variant="contained"
              startIcon={<GenerateIcon />}
              disabled={working || !proposalId || !!genJobId}
              onClick={() => setGenDialog(true)}
            >
              Generate cards
            </Button>
          </Stack>
        </Stack>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo('')}>{info}</Alert>}
        {(loading || working) && <LinearProgress sx={{ mb: 2 }} />}

        {/* Active proposal banner */}
        {proposalId ? (
          <Alert severity="info" variant="outlined" sx={{ mb: 2 }}>
            Active proposal: <strong>{proposal?.title || `#${proposalId}`}</strong>.
            Cards are scoped to this proposal's RFP.
          </Alert>
        ) : (
          <Alert severity="warning" variant="outlined" sx={{ mb: 2 }}>
            Select an active proposal to generate or study flashcards.
          </Alert>
        )}

        {/* Generation progress strip */}
        {genJob && (genJob.status === 'queued' || genJob.status === 'running') && (
          <Card variant="outlined" sx={{ mb: 2, borderColor: 'primary.main' }}>
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={2}>
                <GenerateIcon color="primary" />
                <Box sx={{ flex: 1 }}>
                  <Typography variant="body2" fontWeight={600}>
                    Generating flashcards…
                    {genJob.meta?.progress && (
                      <span> ({genJob.meta.progress.processed} / {genJob.meta.progress.total} requirements,
                      {' '}{genJob.meta.progress.cards_created} cards so far)</span>
                    )}
                  </Typography>
                  <LinearProgress
                    sx={{ mt: 1 }}
                    variant={genJob.meta?.progress?.total ? 'determinate' : 'indeterminate'}
                    value={genJob.meta?.progress?.total
                      ? (100 * genJob.meta.progress.processed) / genJob.meta.progress.total
                      : 0}
                  />
                </Box>
              </Stack>
            </CardContent>
          </Card>
        )}

        {/* Section table */}
        <Card variant="outlined">
          <CardContent sx={{ p: 0 }}>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow sx={{ '& th': { fontWeight: 700 } }}>
                    <TableCell>Section</TableCell>
                    <TableCell align="right" sx={{ width: 110 }}>Total cards</TableCell>
                    <TableCell align="right" sx={{ width: 110 }}>Due now</TableCell>
                    <TableCell align="right" sx={{ width: 220 }}>Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {sections.length === 0 && !loading && (
                    <TableRow>
                      <TableCell colSpan={4} align="center">
                        <Box sx={{ py: 4 }}>
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                            No flashcards yet. Generate a batch from the Statement of Work.
                          </Typography>
                          <Button
                            variant="contained"
                            startIcon={<GenerateIcon />}
                            disabled={!proposalId}
                            onClick={() => setGenDialog(true)}
                          >
                            Generate cards
                          </Button>
                        </Box>
                      </TableCell>
                    </TableRow>
                  )}
                  {sections.map(s => (
                    <TableRow key={s.section_id} hover>
                      <TableCell>
                        <Typography variant="body2" fontWeight={600}>§ {s.section_id}</Typography>
                      </TableCell>
                      <TableCell align="right">{s.total}</TableCell>
                      <TableCell align="right">
                        {s.due > 0 ? (
                          <Chip size="small" color="warning" label={s.due} />
                        ) : (
                          <Typography variant="caption" color="text.secondary">—</Typography>
                        )}
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        <Button
                          size="small"
                          startIcon={<StudyIcon />}
                          onClick={() => startStudy(s.section_id, false)}
                        >
                          Study all
                        </Button>
                        {s.due > 0 && (
                          <Button
                            size="small"
                            color="warning"
                            startIcon={<StudyIcon />}
                            onClick={() => startStudy(s.section_id, true)}
                            sx={{ ml: 1 }}
                          >
                            Study due
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>

        {/* Generate dialog */}
        <Dialog open={genDialog} onClose={() => setGenDialog(false)} maxWidth="xs" fullWidth>
          <DialogTitle>Generate flashcards</DialogTitle>
          <DialogContent>
            <Typography variant="body2" sx={{ mb: 2 }}>
              Walks every Section-4 requirement on this proposal and asks the AI
              to write 1-3 flashcards per requirement. Skips requirements that
              already have cards (idempotent).
            </Typography>
            <TextField
              fullWidth
              size="small"
              type="number"
              label="Max requirements (this run)"
              value={genMaxReqs}
              onChange={(e) => setGenMaxReqs(Number(e.target.value) || 0)}
              helperText="Set to 0 to generate cards for ALL Section-4 reqs (≈1,800). Start small."
              sx={{ mt: 1 }}
            />
            <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>
              Generation runs in the background — you can leave this page and come back.
              Uses gpt-4o-mini by default (~$0.50 for the full corpus).
            </Alert>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setGenDialog(false)}>Cancel</Button>
            <Button
              variant="contained"
              startIcon={<GenerateIcon />}
              onClick={handleGenerate}
              disabled={working || !proposalId}
            >
              Start generation
            </Button>
          </DialogActions>
        </Dialog>

        {/* Study mode dialog (full-screen flip card) */}
        <Dialog
          open={studyOpen}
          onClose={() => setStudyOpen(false)}
          fullWidth
          maxWidth="md"
        >
          <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Box>
              <Typography variant="h6">
                Studying §{studyFilter.section_prefix}{studyFilter.due_only ? ' (due now)' : ''}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Card {studyIndex + 1} of {studyDeck.length}
              </Typography>
            </Box>
            <IconButton onClick={() => setStudyOpen(false)}><CloseIcon /></IconButton>
          </DialogTitle>
          <LinearProgress
            variant="determinate"
            value={studyDeck.length ? (100 * studyIndex) / studyDeck.length : 0}
          />
          <DialogContent sx={{ minHeight: 360, display: 'flex', flexDirection: 'column' }}>
            {studyCard && (
              <Box
                onClick={() => setFlipped(f => !f)}
                sx={{
                  cursor: 'pointer',
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                  alignItems: 'stretch',
                  p: 4,
                  textAlign: 'center',
                  bgcolor: flipped ? 'background.paper' : '#f7f9fc',
                  border: '1px solid',
                  borderColor: 'divider',
                  borderRadius: 2,
                  minHeight: 240,
                  transition: 'background-color 0.2s ease',
                  userSelect: 'none',
                }}
              >
                <Stack direction="row" justifyContent="center" spacing={1} sx={{ mb: 2 }}>
                  <Chip size="small" label={`§ ${studyCard.section_id}`} variant="outlined" />
                  <Chip
                    size="small"
                    label={CARD_TYPE_LABEL[studyCard.card_type] || studyCard.card_type}
                    color={studyCard.card_type === 'numeric' ? 'warning' : 'default'}
                    variant="outlined"
                  />
                  <Chip
                    size="small"
                    label={studyCard.difficulty}
                    variant="outlined"
                  />
                </Stack>

                <Typography
                  variant={flipped ? 'body1' : 'h6'}
                  sx={{ whiteSpace: 'pre-wrap', mb: flipped ? 2 : 0 }}
                >
                  {flipped ? studyCard.answer : studyCard.question}
                </Typography>

                {flipped && studyCard.source_text && (
                  <>
                    <Divider sx={{ my: 2 }} />
                    <Typography variant="caption" color="text.secondary">
                      Source excerpt{studyCard.source_page ? ` (p. ${studyCard.source_page})` : ''}
                    </Typography>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{
                        fontStyle: 'italic',
                        whiteSpace: 'pre-wrap',
                        mt: 1,
                        textAlign: 'left',
                      }}
                    >
                      "{studyCard.source_text}"
                    </Typography>
                  </>
                )}

                {!flipped && (
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 3 }}>
                    Click anywhere to reveal the answer
                  </Typography>
                )}
              </Box>
            )}

            {flipped && (
              <Stack direction="row" spacing={2} sx={{ mt: 3, justifyContent: 'center' }}>
                {RATING_BUTTONS.map(b => (
                  <Tooltip key={b.rating} title={b.help}>
                    <Button
                      variant="contained"
                      color={b.color}
                      size="large"
                      sx={{ minWidth: 130 }}
                      onClick={() => rateCurrent(b.rating)}
                    >
                      {b.label}
                    </Button>
                  </Tooltip>
                ))}
              </Stack>
            )}
          </DialogContent>
          <DialogActions>
            <Button startIcon={<RestartIcon />} onClick={() => { setStudyIndex(0); setFlipped(false); }}>
              Restart deck
            </Button>
            <Button onClick={() => setStudyOpen(false)}>Close</Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Fade>
  );
}
