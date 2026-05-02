// ParsonsScorePanel.js
// Right-side slide-out drawer for the Parsons response page (ParsonsServices).
// Shows the targeted competitor, that competitor's predicted RFP response for
// the currently active section, and side-by-side LLM scores (Parsons vs target)
// against the active scoring rubric. Aggregate "winning bid" indicator at top.
//
// Triggered by a floating toggle button mounted from the parent page. The parent
// also calls `panelRef.current.rescoreSection(sectionId)` after a section save
// so the panel stays current with whatever the user just submitted.

import React, {
  forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState,
} from 'react';
import {
  Drawer, Box, Typography, IconButton, Button, Chip, Divider, MenuItem,
  Select, FormControl, InputLabel, LinearProgress, Tooltip, Alert,
  Accordion, AccordionSummary, AccordionDetails,
} from '@mui/material';
import {
  Close as CloseIcon,
  Refresh as RefreshIcon,
  EmojiEvents as TrophyIcon,
  TrendingDown as TrendingDownIcon,
  TrendingFlat as TrendingFlatIcon,
  TrendingUp as TrendingUpIcon,
  ExpandMore as ExpandIcon,
  Block as BlockIcon,
  Psychology as PsychologyIcon,
} from '@mui/icons-material';
import axios from 'axios';

const PANEL_WIDTH = 460;

const VERDICT_META = {
  winning:      { label: 'Winning Bid',     color: 'success', icon: <TrophyIcon /> },
  losing:       { label: 'Losing Bid',      color: 'error',   icon: <TrendingDownIcon /> },
  tossup:       { label: 'Toss-up',         color: 'warning', icon: <TrendingFlatIcon /> },
  disqualified: { label: 'Disqualified',    color: 'error',   icon: <BlockIcon /> },
  no_competitor:{ label: 'No Target Set',   color: 'default', icon: <PsychologyIcon /> },
  incomplete:   { label: 'Scoring Pending', color: 'default', icon: <RefreshIcon /> },
};

// MUI Chip color values that work for both score chips and verdict chips.
const scoreColor = (score) => {
  if (score == null) return 'default';
  if (score >= 80) return 'success';
  if (score >= 60) return 'primary';
  if (score >= 40) return 'warning';
  return 'error';
};

const ParsonsScorePanel = forwardRef(function ParsonsScorePanel(
  { open, onClose, proposalId, currentSectionId, currentSectionTitle },
  ref,
) {
  const [competitors, setCompetitors] = useState([]);
  const [targetId, setTargetId] = useState('');
  const [aggregate, setAggregate] = useState(null);
  const [prediction, setPrediction] = useState(null); // for current section
  const [predictionCompetitor, setPredictionCompetitor] = useState(null);
  const [loadingAggregate, setLoadingAggregate] = useState(false);
  const [scoring, setScoring] = useState(false);
  const [error, setError] = useState('');
  // Track which sections are currently being rescored so the indicator doesn't
  // race with stale responses when the user is actively editing.
  const inFlightRef = useRef(new Set());

  // ── Load competitors with persona generated (eligible targets) ───
  const loadCompetitors = useCallback(async () => {
    try {
      const r = await axios.get('/api/intelligence/summary');
      // Anyone with a writer persona is a valid target. If none, fall back to
      // every watchlisted competitor — the backend will auto-generate predictions
      // on demand.
      const list = r.data?.competitors || [];
      setCompetitors(list);
    } catch {
      setCompetitors([]);
    }
  }, []);

  // ── Load target + aggregate for proposal ─────────────────────────
  const loadAggregate = useCallback(async () => {
    if (!proposalId) return;
    setLoadingAggregate(true);
    try {
      const [tRes, aRes] = await Promise.all([
        axios.get(`/api/response-scoring/proposals/${proposalId}/target-competitor`),
        axios.get(`/api/response-scoring/proposals/${proposalId}/aggregate`),
      ]);
      setTargetId(tRes.data?.competitor?.id || '');
      setAggregate(aRes.data || null);
    } catch (e) {
      setError('Could not load scoring data.');
    } finally {
      setLoadingAggregate(false);
    }
  }, [proposalId]);

  // ── Load competitor prediction for the current section ───────────
  const loadPrediction = useCallback(async () => {
    if (!proposalId || !currentSectionId) {
      setPrediction(null);
      setPredictionCompetitor(null);
      return;
    }
    try {
      const r = await axios.get(
        `/api/response-scoring/proposals/${proposalId}/sections/${currentSectionId}/competitor-prediction`,
      );
      setPredictionCompetitor(r.data?.competitor || null);
      setPrediction(r.data?.prediction || null);
    } catch {
      setPrediction(null);
    }
  }, [proposalId, currentSectionId]);

  useEffect(() => {
    if (open) {
      loadCompetitors();
      loadAggregate();
      loadPrediction();
    }
  }, [open, loadCompetitors, loadAggregate, loadPrediction]);

  // ── Handle target competitor change ──────────────────────────────
  const handleTargetChange = async (e) => {
    const newId = e.target.value || null;
    setTargetId(newId || '');
    if (!proposalId) return;
    try {
      await axios.put(
        `/api/response-scoring/proposals/${proposalId}/target-competitor`,
        { competitor_id: newId ? Number(newId) : null },
      );
      await loadAggregate();
      await loadPrediction();
    } catch {
      setError('Failed to set target competitor.');
    }
  };

  // ── Score one section (called from parent on save) ───────────────
  // Exposed through the ref so the parent can fire it from its save flow.
  const rescoreSection = useCallback(async (sectionId) => {
    if (!proposalId || !sectionId) return null;
    if (inFlightRef.current.has(sectionId)) return null;
    inFlightRef.current.add(sectionId);
    setScoring(true);
    setError('');
    try {
      const r = await axios.post(
        `/api/response-scoring/proposals/${proposalId}/sections/${sectionId}/score`,
        { score_parsons: true, auto_predict_if_missing: true },
      );
      setAggregate(r.data?.aggregate || null);
      // Refresh prediction text in case auto_predict_if_missing created one
      if (sectionId === currentSectionId) {
        await loadPrediction();
      }
      return r.data;
    } catch (e) {
      setError('Section scoring failed.');
      return null;
    } finally {
      inFlightRef.current.delete(sectionId);
      setScoring(false);
    }
  }, [proposalId, currentSectionId, loadPrediction]);

  useImperativeHandle(ref, () => ({
    rescoreSection,
    refresh: loadAggregate,
  }), [rescoreSection, loadAggregate]);

  // ── Score every rubric section in one shot ───────────────────────
  const scoreAll = async () => {
    if (!proposalId) return;
    setScoring(true);
    setError('');
    try {
      const r = await axios.post(`/api/response-scoring/proposals/${proposalId}/score-all`);
      setAggregate(r.data?.aggregate || null);
    } catch {
      setError('Score-all failed.');
    } finally {
      setScoring(false);
    }
  };

  // ── Render helpers ───────────────────────────────────────────────
  const verdict = VERDICT_META[aggregate?.verdict] || VERDICT_META.no_competitor;
  const currentSectionRow = aggregate?.sections?.find((s) => s.section_id === currentSectionId);
  const parsonsScoreRow = currentSectionRow?.parsons_score;
  const competitorScoreRow = currentSectionRow?.competitor_score;

  const renderAggregate = () => (
    <Box sx={{ p: 2, bgcolor: 'rgba(0, 174, 230, 0.04)', borderRadius: 2, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
        <Typography variant="overline" sx={{ color: 'text.secondary', letterSpacing: 1.2 }}>
          Aggregate Position
        </Typography>
        <Chip
          icon={verdict.icon}
          label={verdict.label}
          color={verdict.color}
          size="small"
          sx={{ fontWeight: 600 }}
        />
      </Box>
      {loadingAggregate && <LinearProgress sx={{ mb: 1 }} />}
      {aggregate && (
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1, mb: 1 }}>
          <Box>
            <Typography variant="caption" color="text.secondary">Parsons</Typography>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              {aggregate.parsons_weighted_total ?? 0}
              <Typography component="span" variant="caption" color="text.secondary">
                {' / 100'}
              </Typography>
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              {aggregate.target_competitor_name || 'Competitor'}
            </Typography>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              {aggregate.competitor_weighted_total ?? '—'}
              {aggregate.competitor_weighted_total != null && (
                <Typography component="span" variant="caption" color="text.secondary">
                  {' / 100'}
                </Typography>
              )}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">Δ</Typography>
            <Typography
              variant="h5"
              sx={{
                fontWeight: 700,
                color:
                  aggregate.delta == null ? 'text.disabled'
                    : aggregate.delta > 0 ? 'success.main'
                      : aggregate.delta < 0 ? 'error.main' : 'warning.main',
              }}
            >
              {aggregate.delta == null
                ? '—'
                : `${aggregate.delta > 0 ? '+' : ''}${aggregate.delta}`}
            </Typography>
          </Box>
        </Box>
      )}
      {aggregate?.parsons_pf_failures?.length > 0 && (
        <Alert severity="error" sx={{ mt: 1, py: 0.5 }}>
          Parsons fails gate(s): {aggregate.parsons_pf_failures.join(', ')}
        </Alert>
      )}
      <Button
        size="small"
        variant="outlined"
        startIcon={<RefreshIcon />}
        onClick={scoreAll}
        disabled={scoring}
        sx={{ mt: 1 }}
        fullWidth
      >
        {scoring ? 'Scoring…' : 'Score All Sections'}
      </Button>
    </Box>
  );

  const renderSectionScores = () => (
    <Box sx={{ mb: 2 }}>
      <Typography variant="overline" sx={{ color: 'text.secondary', letterSpacing: 1.2 }}>
        Current Section
      </Typography>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
        {currentSectionTitle || currentSectionId || '— no section selected —'}
      </Typography>
      <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
        <Tooltip title={currentSectionRow?.parsons_score != null ? 'Parsons score 0-100' : 'Not scored yet'}>
          <Chip
            label={`Parsons ${parsonsScoreRow ?? '—'}`}
            color={scoreColor(parsonsScoreRow)}
            sx={{ fontWeight: 600 }}
          />
        </Tooltip>
        <Tooltip title={competitorScoreRow != null ? 'Competitor score 0-100' : 'No competitor prediction yet'}>
          <Chip
            label={`${aggregate?.target_competitor_name || 'Competitor'} ${competitorScoreRow ?? '—'}`}
            color={scoreColor(competitorScoreRow)}
            variant="outlined"
            sx={{ fontWeight: 600 }}
          />
        </Tooltip>
        {parsonsScoreRow != null && competitorScoreRow != null && (
          <Chip
            icon={
              parsonsScoreRow > competitorScoreRow ? <TrendingUpIcon /> :
              parsonsScoreRow < competitorScoreRow ? <TrendingDownIcon /> : <TrendingFlatIcon />
            }
            label={`Δ ${parsonsScoreRow - competitorScoreRow > 0 ? '+' : ''}${parsonsScoreRow - competitorScoreRow}`}
            color={
              parsonsScoreRow > competitorScoreRow ? 'success' :
              parsonsScoreRow < competitorScoreRow ? 'error' : 'warning'
            }
            size="small"
          />
        )}
      </Box>
      <Button
        size="small"
        variant="contained"
        startIcon={<RefreshIcon />}
        onClick={() => rescoreSection(currentSectionId)}
        disabled={scoring || !currentSectionId}
      >
        {scoring ? 'Scoring…' : 'Re-score this section'}
      </Button>
    </Box>
  );

  const renderPrediction = () => {
    if (!targetId) {
      return (
        <Alert severity="info" sx={{ mt: 2 }}>
          Pick a target competitor above to see how they would write this section.
        </Alert>
      );
    }
    if (!prediction) {
      return (
        <Alert severity="warning" sx={{ mt: 2 }}>
          No predicted response yet for {predictionCompetitor?.name || 'this competitor'} on this
          section. Re-scoring this section will auto-generate one.
        </Alert>
      );
    }
    return (
      <Accordion defaultExpanded sx={{ mt: 2 }}>
        <AccordionSummary expandIcon={<ExpandIcon />}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            {predictionCompetitor?.name || 'Competitor'} — predicted response
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Typography
            variant="body2"
            sx={{ whiteSpace: 'pre-wrap', maxHeight: 320, overflowY: 'auto', lineHeight: 1.55 }}
          >
            {prediction.predicted_response}
          </Typography>
          {prediction.reasoning && (
            <>
              <Divider sx={{ my: 1.5 }} />
              <Typography variant="caption" color="text.secondary">Why they'd write it this way</Typography>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mt: 0.5 }}>
                {prediction.reasoning}
              </Typography>
            </>
          )}
        </AccordionDetails>
      </Accordion>
    );
  };

  const renderSectionRollup = () => {
    if (!aggregate?.sections?.length) return null;
    return (
      <Accordion sx={{ mt: 2 }}>
        <AccordionSummary expandIcon={<ExpandIcon />}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            All Sections — Side-by-side
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {aggregate.sections.map((s) => (
              <Box
                key={s.section_id}
                sx={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto auto auto',
                  gap: 1,
                  alignItems: 'center',
                  py: 0.5,
                  borderBottom: '1px solid rgba(0,0,0,0.06)',
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: s.section_id === currentSectionId ? 700 : 400 }}>
                  {s.title.length > 60 ? `${s.title.slice(0, 60)}…` : s.title}
                  {s.pass_fail && (
                    <Chip label="P/F" size="small" color="error" sx={{ ml: 0.5, height: 16, fontSize: 10 }} />
                  )}
                </Typography>
                <Chip label={s.parsons_score ?? '—'} size="small" color={scoreColor(s.parsons_score)} />
                <Chip label={s.competitor_score ?? '—'} size="small" color={scoreColor(s.competitor_score)} variant="outlined" />
                <Typography
                  variant="caption"
                  sx={{
                    minWidth: 32, textAlign: 'right', fontWeight: 600,
                    color: s.delta == null ? 'text.disabled'
                      : s.delta > 0 ? 'success.main'
                        : s.delta < 0 ? 'error.main' : 'warning.main',
                  }}
                >
                  {s.delta == null ? '—' : (s.delta > 0 ? `+${s.delta}` : s.delta)}
                </Typography>
              </Box>
            ))}
          </Box>
        </AccordionDetails>
      </Accordion>
    );
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      ModalProps={{ keepMounted: true }}
      PaperProps={{ sx: { width: PANEL_WIDTH, maxWidth: '100vw' } }}
    >
      <Box sx={{ p: 2, borderBottom: '1px solid rgba(0,0,0,0.08)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="h6" sx={{ fontWeight: 700, color: '#1B3349' }}>
            Bid Position
          </Typography>
          <IconButton size="small" onClick={onClose}><CloseIcon /></IconButton>
        </Box>
        <FormControl fullWidth size="small">
          <InputLabel>Target Competitor</InputLabel>
          <Select
            value={targetId || ''}
            label="Target Competitor"
            onChange={handleTargetChange}
          >
            <MenuItem value=""><em>None — score Parsons only</em></MenuItem>
            {competitors.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name}{c.has_writer_persona ? '' : ' (no persona — will auto-generate)'}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>

      <Box sx={{ p: 2, overflowY: 'auto' }}>
        {error && <Alert severity="error" onClose={() => setError('')} sx={{ mb: 1 }}>{error}</Alert>}
        {renderAggregate()}
        {renderSectionScores()}
        {renderPrediction()}
        {renderSectionRollup()}
      </Box>
    </Drawer>
  );
});

export default ParsonsScorePanel;
