import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Button, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, IconButton, Drawer, MenuItem, Select, FormControlLabel,
  Switch, Alert, Tooltip,
} from '@mui/material';
import {
  Edit as EditIcon,
  Add as AddIcon,
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  Warning as WarningIcon,
  TrendingUp as TrendingUpIcon,
  Assignment as AssignmentIcon,
  Score as ScoreIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import {
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, Legend,
  ResponsiveContainer,
} from 'recharts';
import axios from 'axios';
import { useAuth } from '../auth/AuthContext';

// ── Color palette for competitors ──────────────────────────────────
const COMP_COLORS = ['#FF6B6B', '#4ECDC4', '#FFE66D', '#A06CD5', '#6BCB77'];

export default function ProposalCompletionScoreboard({ proposalId, serviceData, rfpSections }) {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [rubric, setRubric] = useState(null);
  const [scores, setScores] = useState([]);
  const [competitors, setCompetitors] = useState([]);
  const [editRubricOpen, setEditRubricOpen] = useState(false);
  const [scoreDrawerOpen, setScoreDrawerOpen] = useState(false);
  const [editSections, setEditSections] = useState([]);
  const [editError, setEditError] = useState('');
  const [scoreForm, setScoreForm] = useState({ section_id: '', scorer_type: 'parsons', scorer_name: '', score: 0, rationale: '' });

  // ── Data fetching ──────────────────────────────────────────────────

  const fetchRubric = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/scoring/rubric');
      setRubric(data);
    } catch (e) { console.error('Failed to load rubric', e); }
  }, []);

  const fetchScores = useCallback(async () => {
    if (!proposalId) return;
    try {
      const { data } = await axios.get(`/api/scoring/proposals/${proposalId}/scores`);
      setScores(data.scores || []);
    } catch (e) { console.error('Failed to load scores', e); }
  }, [proposalId]);

  const fetchCompetitors = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/competitors');
      setCompetitors(data.competitors || []);
    } catch (e) { console.error('Failed to load competitors', e); }
  }, []);

  useEffect(() => { fetchRubric(); fetchCompetitors(); }, [fetchRubric, fetchCompetitors]);
  useEffect(() => { fetchScores(); }, [fetchScores]);

  // ── Derived data ───────────────────────────────────────────────────

  const rubricSections = rubric?.sections || [];
  const competitorNames = [...new Set(scores.filter(s => s.scorer_type === 'competitor').map(s => s.scorer_name))];

  const getSectionScore = (sectionId, scorerType, scorerName = null) => {
    const match = scores.find(s =>
      s.section_id === sectionId && s.scorer_type === scorerType &&
      (scorerType === 'parsons' ? true : s.scorer_name === scorerName)
    );
    return match?.score ?? null;
  };

  const hasContent = (sectionId) => {
    if (!serviceData?.[sectionId]) return false;
    return Object.values(serviceData[sectionId]).some(v =>
      typeof v === 'string' ? v.trim().length > 0 : !!v
    );
  };

  const getSectionStatus = (sectionId) => {
    const rfp = rfpSections.find(s => s.id === sectionId);
    if (!rfp) return 'empty';
    if (hasContent(sectionId)) return 'has_content';
    return 'empty';
  };

  // ── KPI calculations ──────────────────────────────────────────────

  const totalWeighted = rubricSections.filter(s => !s.pass_fail).reduce((sum, s) => sum + s.weight_points, 0);

  const parsonsWeightedScore = rubricSections
    .filter(s => !s.pass_fail)
    .reduce((sum, s) => {
      const raw = getSectionScore(s.section_id, 'parsons');
      if (raw === null) return sum;
      return sum + (raw * s.weight_points / 100);
    }, 0);

  const sectionsWithContent = rfpSections.filter(s => hasContent(s.id)).length;
  const sectionsScored = rubricSections.filter(s => getSectionScore(s.section_id, 'parsons') !== null).length;
  const passFail = rubricSections.filter(s => s.pass_fail);
  const passFailPassed = passFail.filter(s => hasContent(s.section_id)).length;

  // Best competitor composite
  const bestCompetitorScore = competitorNames.reduce((best, name) => {
    const cs = rubricSections
      .filter(s => !s.pass_fail)
      .reduce((sum, s) => {
        const raw = getSectionScore(s.section_id, 'competitor', name);
        if (raw === null) return sum;
        return sum + (raw * s.weight_points / 100);
      }, 0);
    return cs > best ? cs : best;
  }, 0);

  // ── Radar chart data ──────────────────────────────────────────────

  const radarData = rubricSections.filter(s => !s.pass_fail).map(s => {
    const d = { section: s.title.length > 20 ? s.title.substring(0, 18) + '…' : s.title };
    d['Parsons'] = getSectionScore(s.section_id, 'parsons') ?? 0;
    competitorNames.forEach(name => {
      d[name] = getSectionScore(s.section_id, 'competitor', name) ?? 0;
    });
    return d;
  });

  // ── Edit rubric handlers ──────────────────────────────────────────

  const openEditRubric = () => {
    setEditSections(rubricSections.map(s => ({ ...s })));
    setEditError('');
    setEditRubricOpen(true);
  };

  const weightedSum = editSections.filter(s => !s.pass_fail).reduce((sum, s) => sum + (parseInt(s.weight_points) || 0), 0);

  const handleSaveRubric = async () => {
    if (weightedSum !== 100) {
      setEditError(`Weighted sections must sum to 100 (currently ${weightedSum})`);
      return;
    }
    try {
      await axios.put('/api/scoring/rubric', {
        sections: editSections.map((s, i) => ({
          section_id: s.section_id,
          title: s.title,
          weight_points: parseInt(s.weight_points) || 0,
          pass_fail: s.pass_fail,
          sort_order: i,
        })),
      });
      setEditRubricOpen(false);
      fetchRubric();
    } catch (e) {
      setEditError(e.response?.data?.detail || 'Failed to save');
    }
  };

  // ── Score entry handlers ──────────────────────────────────────────

  const handleSaveScore = async () => {
    if (!proposalId) return;
    try {
      await axios.put(`/api/scoring/proposals/${proposalId}/scores`, {
        scores: [{
          section_id: scoreForm.section_id,
          scorer_type: scoreForm.scorer_type,
          scorer_name: scoreForm.scorer_type === 'competitor' ? scoreForm.scorer_name : null,
          score: parseInt(scoreForm.score) || 0,
          rationale: scoreForm.rationale,
        }],
      });
      setScoreDrawerOpen(false);
      setScoreForm({ section_id: '', scorer_type: 'parsons', scorer_name: '', score: 0, rationale: '' });
      fetchScores();
    } catch (e) {
      console.error('Failed to save score', e);
    }
  };

  const openScoreDrawer = (sectionId, scorerType = 'parsons', scorerName = '') => {
    const existing = getSectionScore(sectionId, scorerType, scorerName);
    setScoreForm({
      section_id: sectionId,
      scorer_type: scorerType,
      scorer_name: scorerName,
      score: existing ?? 0,
      rationale: '',
    });
    setScoreDrawerOpen(true);
  };

  // ── Render ─────────────────────────────────────────────────────────

  return (
    <Card className="card" sx={{ mb: 3 }}>
      <CardContent>
        {/* Header + Admin button */}
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>Proposal Completion Status</Typography>
          <Box>
            <Button size="small" startIcon={<AddIcon />} onClick={() => { setScoreForm({ section_id: rfpSections[0]?.id || '', scorer_type: 'parsons', scorer_name: '', score: 0, rationale: '' }); setScoreDrawerOpen(true); }} sx={{ mr: 1 }}>
              Add Score
            </Button>
            {isAdmin && (
              <Button size="small" variant="outlined" startIcon={<EditIcon />} onClick={openEditRubric}>
                Edit Rubric
              </Button>
            )}
          </Box>
        </Box>

        {/* KPI Strip */}
        <Grid container spacing={2} sx={{ mb: 3 }}>
          <Grid item xs={6} sm={3}>
            <Box sx={{ textAlign: 'center', p: 2, borderRadius: 2, bgcolor: 'rgba(0, 174, 230, 0.08)' }}>
              <TrendingUpIcon sx={{ color: '#00AEE6', fontSize: 28, mb: 0.5 }} />
              <Typography variant="h5" sx={{ fontWeight: 700, color: '#1B3349' }}>
                {parsonsWeightedScore.toFixed(1)}<Typography component="span" variant="body2" color="text.secondary"> / {totalWeighted}</Typography>
              </Typography>
              <Typography variant="caption" color="text.secondary">Parsons Weighted Score</Typography>
              {bestCompetitorScore > 0 && (
                <Typography variant="caption" display="block" color={parsonsWeightedScore >= bestCompetitorScore ? 'success.main' : 'error.main'}>
                  vs Best Competitor: {bestCompetitorScore.toFixed(1)}
                </Typography>
              )}
            </Box>
          </Grid>
          <Grid item xs={6} sm={3}>
            <Box sx={{ textAlign: 'center', p: 2, borderRadius: 2, bgcolor: 'rgba(80, 191, 52, 0.08)' }}>
              <AssignmentIcon sx={{ color: '#50BF34', fontSize: 28, mb: 0.5 }} />
              <Typography variant="h5" sx={{ fontWeight: 700, color: '#1B3349' }}>
                {sectionsWithContent}<Typography component="span" variant="body2" color="text.secondary"> / {rfpSections.length}</Typography>
              </Typography>
              <Typography variant="caption" color="text.secondary">Sections with Content</Typography>
            </Box>
          </Grid>
          <Grid item xs={6} sm={3}>
            <Box sx={{ textAlign: 'center', p: 2, borderRadius: 2, bgcolor: 'rgba(160, 108, 213, 0.08)' }}>
              <ScoreIcon sx={{ color: '#A06CD5', fontSize: 28, mb: 0.5 }} />
              <Typography variant="h5" sx={{ fontWeight: 700, color: '#1B3349' }}>
                {sectionsScored}<Typography component="span" variant="body2" color="text.secondary"> / {rubricSections.filter(s => !s.pass_fail).length}</Typography>
              </Typography>
              <Typography variant="caption" color="text.secondary">Sections Scored</Typography>
            </Box>
          </Grid>
          <Grid item xs={6} sm={3}>
            <Box sx={{ textAlign: 'center', p: 2, borderRadius: 2, bgcolor: 'rgba(255, 107, 107, 0.08)' }}>
              {passFailPassed === passFail.length
                ? <CheckCircleIcon sx={{ color: '#50BF34', fontSize: 28, mb: 0.5 }} />
                : <WarningIcon sx={{ color: '#FF6B6B', fontSize: 28, mb: 0.5 }} />
              }
              <Typography variant="h5" sx={{ fontWeight: 700, color: '#1B3349' }}>
                {passFailPassed}<Typography component="span" variant="body2" color="text.secondary"> / {passFail.length}</Typography>
              </Typography>
              <Typography variant="caption" color="text.secondary">Pass/Fail Gates</Typography>
            </Box>
          </Grid>
        </Grid>

        {/* Section Scoreboard Table */}
        <TableContainer component={Paper} variant="outlined" sx={{ mb: 3, borderRadius: 2 }}>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                <TableCell sx={{ fontWeight: 700 }}>Section</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700, width: 80 }}>Weight</TableCell>
                <TableCell sx={{ fontWeight: 700, minWidth: 180 }}>Parsons</TableCell>
                {competitorNames.map((name, i) => (
                  <TableCell key={name} sx={{ fontWeight: 700, minWidth: 150 }}>{name}</TableCell>
                ))}
                <TableCell align="center" sx={{ fontWeight: 700, width: 90 }}>Status</TableCell>
                <TableCell align="center" sx={{ width: 50 }} />
              </TableRow>
            </TableHead>
            <TableBody>
              {rubricSections.map((section) => {
                const parsonsScore = getSectionScore(section.section_id, 'parsons');
                const status = getSectionStatus(section.section_id);
                return (
                  <TableRow key={section.section_id} hover>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{section.title}</Typography>
                    </TableCell>
                    <TableCell align="center">
                      <Chip
                        label={section.pass_fail ? 'P/F' : `${section.weight_points}`}
                        size="small"
                        color={section.pass_fail ? 'error' : 'primary'}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      {section.pass_fail ? (
                        status === 'has_content'
                          ? <Chip label="PASS" size="small" color="success" />
                          : <Chip label="INCOMPLETE" size="small" color="warning" />
                      ) : (
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Box sx={{ flexGrow: 1 }}>
                            <LinearProgress
                              variant="determinate"
                              value={parsonsScore ?? 0}
                              sx={{
                                height: 10, borderRadius: 5,
                                bgcolor: 'rgba(0,174,230,0.15)',
                                '& .MuiLinearProgress-bar': {
                                  bgcolor: '#00AEE6',
                                  borderRadius: 5,
                                },
                              }}
                            />
                          </Box>
                          <Typography variant="body2" sx={{ minWidth: 32, fontWeight: 600 }}>
                            {parsonsScore !== null ? parsonsScore : '—'}
                          </Typography>
                        </Box>
                      )}
                    </TableCell>
                    {competitorNames.map((name, idx) => {
                      const cs = getSectionScore(section.section_id, 'competitor', name);
                      return (
                        <TableCell key={name}>
                          {section.pass_fail ? (
                            <Chip label="N/A" size="small" variant="outlined" />
                          ) : (
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <Box sx={{ flexGrow: 1 }}>
                                <LinearProgress
                                  variant="determinate"
                                  value={cs ?? 0}
                                  sx={{
                                    height: 10, borderRadius: 5,
                                    bgcolor: `${COMP_COLORS[idx % COMP_COLORS.length]}22`,
                                    '& .MuiLinearProgress-bar': {
                                      bgcolor: COMP_COLORS[idx % COMP_COLORS.length],
                                      borderRadius: 5,
                                    },
                                  }}
                                />
                              </Box>
                              <Typography variant="body2" sx={{ minWidth: 32, fontWeight: 600 }}>
                                {cs !== null ? cs : '—'}
                              </Typography>
                            </Box>
                          )}
                        </TableCell>
                      );
                    })}
                    <TableCell align="center">
                      {status === 'has_content'
                        ? <Chip label="Content" size="small" color="success" variant="outlined" />
                        : <Chip label="Empty" size="small" color="default" variant="outlined" />
                      }
                    </TableCell>
                    <TableCell align="center">
                      <Tooltip title="Add/Edit Score">
                        <IconButton size="small" onClick={() => openScoreDrawer(section.section_id)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>

        {/* Radar Chart */}
        {radarData.length > 0 && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 700 }}>Score Comparison Radar</Typography>
            <ResponsiveContainer width="100%" height={350}>
              <RadarChart data={radarData}>
                <PolarGrid />
                <PolarAngleAxis dataKey="section" tick={{ fontSize: 11 }} />
                <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
                <Radar name="Parsons" dataKey="Parsons" stroke="#00AEE6" fill="#00AEE6" fillOpacity={0.25} strokeWidth={2} />
                {competitorNames.map((name, idx) => (
                  <Radar
                    key={name}
                    name={name}
                    dataKey={name}
                    stroke={COMP_COLORS[idx % COMP_COLORS.length]}
                    fill={COMP_COLORS[idx % COMP_COLORS.length]}
                    fillOpacity={0.1}
                    strokeWidth={2}
                  />
                ))}
                <Legend />
              </RadarChart>
            </ResponsiveContainer>
          </Box>
        )}
      </CardContent>

      {/* ── Edit Rubric Dialog (admin only) ─────────────────────────── */}
      <Dialog open={editRubricOpen} onClose={() => setEditRubricOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Edit Scoring Rubric</DialogTitle>
        <DialogContent>
          {editError && <Alert severity="error" sx={{ mb: 2 }}>{editError}</Alert>}
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Weighted sections must sum to 100. Currently: <strong>{weightedSum}</strong>
            {weightedSum !== 100 && <span style={{ color: '#d32f2f' }}> (needs adjustment)</span>}
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Section</TableCell>
                  <TableCell align="center">Pass/Fail</TableCell>
                  <TableCell align="center">Weight</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {editSections.map((s, idx) => (
                  <TableRow key={s.section_id}>
                    <TableCell>{s.title}</TableCell>
                    <TableCell align="center">
                      <FormControlLabel
                        control={
                          <Switch
                            checked={s.pass_fail}
                            onChange={(e) => {
                              const updated = [...editSections];
                              updated[idx] = { ...updated[idx], pass_fail: e.target.checked, weight_points: e.target.checked ? 0 : updated[idx].weight_points };
                              setEditSections(updated);
                            }}
                            size="small"
                          />
                        }
                        label=""
                      />
                    </TableCell>
                    <TableCell align="center">
                      <TextField
                        type="number"
                        size="small"
                        value={s.weight_points}
                        disabled={s.pass_fail}
                        onChange={(e) => {
                          const updated = [...editSections];
                          updated[idx] = { ...updated[idx], weight_points: parseInt(e.target.value) || 0 };
                          setEditSections(updated);
                        }}
                        inputProps={{ min: 0, max: 100, style: { width: 60, textAlign: 'center' } }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditRubricOpen(false)}>Cancel</Button>
          <Button onClick={handleSaveRubric} variant="contained" disabled={weightedSum !== 100}>Save Rubric</Button>
        </DialogActions>
      </Dialog>

      {/* ── Score Entry Drawer ───────────────────────────────────────── */}
      <Drawer anchor="right" open={scoreDrawerOpen} onClose={() => setScoreDrawerOpen(false)}>
        <Box sx={{ width: 380, p: 3 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
            <Typography variant="h6">Enter Score</Typography>
            <IconButton onClick={() => setScoreDrawerOpen(false)}><CloseIcon /></IconButton>
          </Box>

          <Select
            fullWidth
            size="small"
            value={scoreForm.section_id}
            onChange={(e) => setScoreForm({ ...scoreForm, section_id: e.target.value })}
            sx={{ mb: 2 }}
            displayEmpty
          >
            <MenuItem value="" disabled>Select section…</MenuItem>
            {rubricSections.map(s => (
              <MenuItem key={s.section_id} value={s.section_id}>{s.title}</MenuItem>
            ))}
          </Select>

          <Select
            fullWidth
            size="small"
            value={scoreForm.scorer_type}
            onChange={(e) => setScoreForm({ ...scoreForm, scorer_type: e.target.value, scorer_name: '' })}
            sx={{ mb: 2 }}
          >
            <MenuItem value="parsons">Parsons (Our Score)</MenuItem>
            <MenuItem value="competitor">Competitor</MenuItem>
          </Select>

          {scoreForm.scorer_type === 'competitor' && (
            <Select
              fullWidth
              size="small"
              value={scoreForm.scorer_name}
              onChange={(e) => setScoreForm({ ...scoreForm, scorer_name: e.target.value })}
              sx={{ mb: 2 }}
              displayEmpty
            >
              <MenuItem value="" disabled>Select competitor…</MenuItem>
              {competitors.map(c => (
                <MenuItem key={c.id} value={c.name}>{c.name}</MenuItem>
              ))}
            </Select>
          )}

          <TextField
            fullWidth
            label="Score (0-100)"
            type="number"
            size="small"
            value={scoreForm.score}
            onChange={(e) => setScoreForm({ ...scoreForm, score: Math.min(100, Math.max(0, parseInt(e.target.value) || 0)) })}
            inputProps={{ min: 0, max: 100 }}
            sx={{ mb: 2 }}
          />

          <TextField
            fullWidth
            label="Rationale"
            multiline
            rows={3}
            size="small"
            value={scoreForm.rationale}
            onChange={(e) => setScoreForm({ ...scoreForm, rationale: e.target.value })}
            sx={{ mb: 3 }}
          />

          <Button
            fullWidth
            variant="contained"
            onClick={handleSaveScore}
            disabled={!scoreForm.section_id || (scoreForm.scorer_type === 'competitor' && !scoreForm.scorer_name)}
          >
            Save Score
          </Button>
        </Box>
      </Drawer>
    </Card>
  );
}
