import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  Alert,
  CircularProgress,
  Button,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  Tooltip,
  LinearProgress,
} from '@mui/material';
import {
  TrendingUp as WinIcon,
  TrendingDown as LossIcon,
  Warning as GapIcon,
  AutoFixHigh as CureIcon,
  PlayArrow as ScoreIcon,
} from '@mui/icons-material';
import { useProposal } from '../proposal/ProposalContext';

/**
 * Adversarial Scorecard: the single screen for the 8-phase gap-flagging
 * loop. For each rubric section it shows Parsons score, the modeled
 * competitor score, and the delta. Sections where Parsons is losing or
 * unscored are flagged with concrete next-step actions:
 *   - Score (if no Parsons score yet)
 *   - Generate competitor draft (if no competitor draft for the target)
 *   - Generate cure (if Parsons < competitor)
 */
export default function AdversarialScorecard() {
  const { proposalId } = useProposal();
  const [aggregate, setAggregate] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busyKey, setBusyKey] = useState(null);

  const reload = () => {
    if (!proposalId) return;
    setLoading(true);
    axios
      .get(`/api/response-scoring/proposals/${proposalId}/aggregate`)
      .then((res) => {
        setAggregate(res.data);
        setError(null);
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || 'Failed to load');
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { reload(); }, [proposalId]);

  const rows = useMemo(() => {
    if (!aggregate) return [];
    return aggregate.sections || [];
  }, [aggregate]);

  const counts = useMemo(() => {
    let win = 0, loss = 0, tie = 0, unscored = 0;
    rows.forEach((s) => {
      if (s.parsons_score == null) {
        unscored += 1;
        return;
      }
      if (s.delta == null) {
        unscored += 1;
        return;
      }
      if (s.delta > 1) win += 1;
      else if (s.delta < -1) loss += 1;
      else tie += 1;
    });
    return { win, loss, tie, unscored };
  }, [rows]);

  const scoreSection = async (sectionId) => {
    setBusyKey(`score:${sectionId}`);
    try {
      await axios.post(
        `/api/response-scoring/proposals/${proposalId}/sections/${encodeURIComponent(sectionId)}/score`,
        {},
      );
      await reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Score failed');
    } finally { setBusyKey(null); }
  };

  const generateCompetitor = async (sectionId) => {
    setBusyKey(`comp:${sectionId}`);
    try {
      await axios.post(
        `/api/workbench/sections/${encodeURIComponent(sectionId)}/generate-competitor-response`,
        {},
        { params: { proposal_id: proposalId } },
      );
      await reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Competitor generation failed');
    } finally { setBusyKey(null); }
  };

  const generateCure = async (sectionId) => {
    setBusyKey(`cure:${sectionId}`);
    try {
      await axios.post(
        `/api/workbench/sections/${encodeURIComponent(sectionId)}/cure-suggestion`,
        { include_rewrite: true, competitor_id: aggregate?.target_competitor?.id || null },
        { params: { proposal_id: proposalId } },
      );
      await reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Cure generation failed');
    } finally { setBusyKey(null); }
  };

  if (loading) {
    return <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={28} /></Box>;
  }
  if (error) {
    return <Box sx={{ p: 3 }}><Alert severity="error">{error}</Alert></Box>;
  }
  if (!aggregate) {
    return <Box sx={{ p: 3 }}><Alert severity="info">No scoring data yet.</Alert></Box>;
  }

  const targetName = aggregate.target_competitor?.name || '(no target competitor set)';

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h5">Adversarial Scorecard</Typography>
        <Chip label={`vs ${targetName}`} color="primary" variant="outlined" />
      </Stack>

      <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
        <Paper sx={{ p: 2, flex: 1, bgcolor: 'success.light' }}>
          <Typography variant="overline">Sections winning</Typography>
          <Typography variant="h4">{counts.win}</Typography>
        </Paper>
        <Paper sx={{ p: 2, flex: 1, bgcolor: 'error.light' }}>
          <Typography variant="overline">Sections losing</Typography>
          <Typography variant="h4">{counts.loss}</Typography>
        </Paper>
        <Paper sx={{ p: 2, flex: 1, bgcolor: 'warning.light' }}>
          <Typography variant="overline">Tied / close</Typography>
          <Typography variant="h4">{counts.tie}</Typography>
        </Paper>
        <Paper sx={{ p: 2, flex: 1, bgcolor: 'grey.200' }}>
          <Typography variant="overline">Not yet scored</Typography>
          <Typography variant="h4">{counts.unscored}</Typography>
        </Paper>
      </Stack>

      {aggregate.parsons_total != null && aggregate.competitor_total != null ? (
        <Paper sx={{ p: 2, mb: 3 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Total: Parsons {aggregate.parsons_total.toFixed(1)} vs {targetName} {aggregate.competitor_total.toFixed(1)} {' '}
            <Chip
              size="small"
              label={`Δ ${aggregate.delta_total ?? '?'}`}
              color={aggregate.delta_total > 0 ? 'success' : aggregate.delta_total < 0 ? 'error' : 'default'}
            />
          </Typography>
          <LinearProgress
            variant="determinate"
            value={Math.min(100, Math.max(0, aggregate.parsons_total))}
            sx={{ height: 8, borderRadius: 1 }}
          />
        </Paper>
      ) : null}

      <Paper>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Section</TableCell>
              <TableCell align="right">Weight</TableCell>
              <TableCell align="right">Parsons</TableCell>
              <TableCell align="right">{targetName}</TableCell>
              <TableCell align="right">Δ</TableCell>
              <TableCell align="center">Status</TableCell>
              <TableCell>Next action</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((s) => {
              const winning = s.delta != null && s.delta > 1;
              const losing = s.delta != null && s.delta < -1;
              const close = s.delta != null && Math.abs(s.delta) <= 1;
              const noP = s.parsons_score == null;
              const noC = s.competitor_score == null;
              const sid = s.section_id;
              return (
                <TableRow key={sid} hover>
                  <TableCell sx={{ maxWidth: 320 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.title || sid}</Typography>
                    <Typography variant="caption" color="text.secondary">{sid}</Typography>
                  </TableCell>
                  <TableCell align="right">{s.weight_points || ''}</TableCell>
                  <TableCell align="right">
                    {s.parsons_score != null ? s.parsons_score : <Typography variant="caption" color="text.secondary">—</Typography>}
                  </TableCell>
                  <TableCell align="right">
                    {s.competitor_score != null ? s.competitor_score : <Typography variant="caption" color="text.secondary">—</Typography>}
                  </TableCell>
                  <TableCell align="right">
                    {s.delta != null ? (
                      <Chip
                        size="small"
                        label={s.delta > 0 ? `+${s.delta}` : s.delta}
                        color={winning ? 'success' : losing ? 'error' : 'default'}
                      />
                    ) : null}
                  </TableCell>
                  <TableCell align="center">
                    {winning ? <Tooltip title="Parsons winning"><WinIcon color="success" fontSize="small" /></Tooltip>
                      : losing ? <Tooltip title="Parsons losing — needs cure"><LossIcon color="error" fontSize="small" /></Tooltip>
                      : close ? <Tooltip title="Tied / close"><GapIcon color="warning" fontSize="small" /></Tooltip>
                      : <Typography variant="caption" color="text.secondary">unscored</Typography>}
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.5}>
                      {noP ? (
                        <Button size="small" startIcon={<ScoreIcon />} variant="outlined"
                                disabled={busyKey === `score:${sid}`}
                                onClick={() => scoreSection(sid)}>
                          Score
                        </Button>
                      ) : null}
                      {noC ? (
                        <Button size="small" variant="outlined" color="warning"
                                disabled={busyKey === `comp:${sid}`}
                                onClick={() => generateCompetitor(sid)}>
                          Gen competitor
                        </Button>
                      ) : null}
                      {losing || (close && !noP) ? (
                        <Button size="small" startIcon={<CureIcon />} variant="contained" color="primary"
                                disabled={busyKey === `cure:${sid}`}
                                onClick={() => generateCure(sid)}>
                          Cure
                        </Button>
                      ) : null}
                    </Stack>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
}
