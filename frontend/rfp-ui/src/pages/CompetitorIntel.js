import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Button, Chip, LinearProgress,
  Paper,
  Tabs, Tab, Fade, IconButton, Tooltip, Alert, Accordion, AccordionSummary,
  AccordionDetails,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
} from '@mui/material';
import {
  Refresh as RefreshIcon, ExpandMore as ExpandIcon, CheckCircle as VerifiedIcon,
  Warning as UnverifiedIcon, Psychology as PersonaIcon, Article as PredictionIcon,
  Delete as DeleteIcon, DeleteSweep as DeleteSweepIcon,
} from '@mui/icons-material';
import axios from 'axios';
import MarkdownView from '../components/MarkdownView';
import DossierExecutiveSummary from '../components/DossierExecutiveSummary';
import IntelligenceRunTimeline from '../components/IntelligenceRunTimeline';
import IntelligenceThreadsTab from '../components/IntelligenceThreadsTab';
import IntelligenceTimelineTab from '../components/IntelligenceTimelineTab';
import CompetitorDocumentsPanel from '../components/CompetitorDocumentsPanel';
import CompetitorResearchFocus from '../components/CompetitorResearchFocus';

const CONFIDENCE_COLORS = { high: 'success', medium: 'warning', low: 'error', unverified: 'default' };

const CATEGORY_LABELS = {
  leadership: 'Leadership',
  technology: 'Technology',
  customer_service: 'Customer Service',
  contracts: 'Contracts & Wins',
  financials: 'Financials',
  strengths: 'Strengths',
  weaknesses: 'Weaknesses',
  strategy: 'Strategy',
};
const prettyCategory = (c) =>
  CATEGORY_LABELS[c] || (c || '').replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());

const CompetitorIntel = () => {
  const [competitors, setCompetitors] = useState([]);
  const [summary, setSummary] = useState([]);
  const [selectedComp, setSelectedComp] = useState(null);
  const [dossier, setDossier] = useState([]);
  const [persona, setPersona] = useState(null);
  const [predictions, setPredictions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [dossierJob, setDossierJob] = useState(null); // live progress for async dossier generation
  const [activeTab, setActiveTab] = useState(0);
  // Confirmation dialog state: { kind: 'dossier-entry' | 'prediction' | 'persona' | 'all-analysis' | 'clear-dossier' | 'clear-predictions', targetId?, label? }
  const [confirm, setConfirm] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const fetchCompetitors = useCallback(async () => {
    try {
      const [compRes, sumRes] = await Promise.all([
        axios.get('/api/competitors'),
        axios.get('/api/intelligence/summary'),
      ]);
      setCompetitors(compRes.data.competitors || []);
      setSummary(sumRes.data.competitors || []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchCompetitors(); }, [fetchCompetitors]);

  const selectCompetitor = async (comp) => {
    setSelectedComp(comp);
    setActiveTab(0);
    try {
      const [dosRes, perRes, predRes] = await Promise.all([
        axios.get(`/api/intelligence/competitors/${comp.id}/dossier`),
        axios.get(`/api/intelligence/competitors/${comp.id}/persona`),
        axios.get(`/api/intelligence/competitors/${comp.id}/predictions`),
      ]);
      setDossier(dosRes.data.entries || []);
      setPersona(perRes.data.persona || null);
      setPredictions(predRes.data.predictions || []);
    } catch { /* ignore */ }
  };

  const generateDossier = async () => {
    if (!selectedComp) return;
    setGenerating(true);
    setDossierJob(null);
    try {
      // NEW: unified intelligence run — replaces the old /dossier/generate-async.
      // The new endpoint runs every connector + thread synthesis + drafting in
      // one shot and emits a 22-phase progress plan.
      const { data } = await axios.post(
        `/api/intelligence/competitors/${selectedComp.id}/intelligence-run`
      );
      setDossierJob({
        job_id: data.job_id,
        status: data.status || 'queued',
        started_at: null,
        completed_at: null,
        error: null,
        // Phases populated by the first poll — initial seed is empty so the
        // timeline component just shows a "queued" banner until the backend
        // returns the phase list.
        phases: [],
        summary: null,
      });
    } catch (e) {
      alert('Intelligence run failed: ' + (e.response?.data?.detail?.message
                                            || e.response?.data?.detail
                                            || e.message));
      setGenerating(false);
    }
  };

  // Poll the active intelligence-run job every 2s while it is queued / running.
  useEffect(() => {
    if (!dossierJob?.job_id) return undefined;
    if (dossierJob.status === 'done' || dossierJob.status === 'error') return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const { data } = await axios.get(`/api/intelligence/intelligence-run/${dossierJob.job_id}`);
        if (cancelled) return;
        setDossierJob(data);
        if (data.status === 'done') {
          if (selectedComp) await selectCompetitor(selectedComp);
          fetchCompetitors();
          setGenerating(false);
        } else if (data.status === 'error') {
          setGenerating(false);
        }
      } catch { /* ignore transient errors; keep polling */ }
    };
    const interval = setInterval(tick, 2000);
    tick();
    return () => { cancelled = true; clearInterval(interval); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dossierJob?.job_id, dossierJob?.status]);

  const generatePersona = async () => {
    if (!selectedComp) return;
    setGenerating(true);
    try {
      await axios.post(`/api/intelligence/competitors/${selectedComp.id}/persona/generate`);
      await selectCompetitor(selectedComp);
    } catch { alert('Persona generation failed'); }
    finally { setGenerating(false); }
  };

  const generatePredictions = async () => {
    if (!selectedComp) return;
    setGenerating(true);
    try {
      await axios.post(`/api/intelligence/competitors/${selectedComp.id}/predictions/generate`, {});
      await selectCompetitor(selectedComp);
    } catch { alert('Prediction generation failed'); }
    finally { setGenerating(false); }
  };

  const executeDelete = async () => {
    if (!confirm || !selectedComp) return;
    setDeleting(true);
    try {
      switch (confirm.kind) {
        case 'dossier-entry':
          await axios.delete(`/api/intelligence/dossier/${confirm.targetId}`);
          setDossier(prev => prev.filter(e => e.id !== confirm.targetId));
          break;
        case 'prediction':
          await axios.delete(`/api/intelligence/predictions/${confirm.targetId}`);
          setPredictions(prev => prev.filter(p => p.id !== confirm.targetId));
          break;
        case 'persona':
          await axios.delete(`/api/intelligence/competitors/${selectedComp.id}/persona`);
          setPersona(null);
          break;
        case 'clear-dossier':
          await axios.delete(`/api/intelligence/competitors/${selectedComp.id}/dossier`);
          setDossier([]);
          break;
        case 'clear-predictions':
          await axios.delete(`/api/intelligence/competitors/${selectedComp.id}/predictions`);
          setPredictions([]);
          break;
        case 'all-analysis':
          await axios.delete(`/api/intelligence/competitors/${selectedComp.id}/analysis`);
          setDossier([]); setPersona(null); setPredictions([]);
          break;
        default:
          break;
      }
      fetchCompetitors();
      setConfirm(null);
    } catch (e) {
      alert(e.response?.data?.detail || 'Delete failed');
    } finally {
      setDeleting(false);
    }
  };

  if (loading) return <Box sx={{ p: 4 }}><LinearProgress /></Box>;

  return (
    <Fade in timeout={600}>
      <Box>


        <Grid container spacing={3}>
          {/* Competitor List */}
          <Grid item xs={12} md={4}>
            <Card className="card">
              <CardContent>
                <Typography variant="h6" fontWeight={700} gutterBottom>Competitors</Typography>
                {competitors.map(comp => {
                  const s = summary.find(x => x.id === comp.id) || {};
                  const isSelected = selectedComp?.id === comp.id;
                  return (
                    <Paper key={comp.id} variant="outlined"
                      onClick={() => selectCompetitor(comp)}
                      sx={{
                        p: 2, mb: 1, borderRadius: 2, cursor: 'pointer',
                        borderColor: isSelected ? '#00AEE6' : 'divider',
                        bgcolor: isSelected ? 'rgba(0,174,230,0.05)' : 'transparent',
                        '&:hover': { borderColor: '#00AEE6' },
                      }}>
                      <Typography variant="body1" fontWeight={700}>{comp.name}</Typography>
                      <Box sx={{ display: 'flex', gap: 0.5, mt: 1, flexWrap: 'wrap' }}>
                        <Chip label={`${s.dossier_entries || 0}/8 intel`} size="small"
                          color={s.dossier_entries >= 8 ? 'success' : s.dossier_entries > 0 ? 'warning' : 'default'} variant="outlined" />
                        <Chip label={s.has_writer_persona ? 'Persona ✓' : 'No persona'} size="small"
                          color={s.has_writer_persona ? 'success' : 'default'} variant="outlined" />
                        <Chip label={`${s.predictions_generated || 0} predictions`} size="small" variant="outlined" />
                      </Box>
                    </Paper>
                  );
                })}
              </CardContent>
            </Card>
          </Grid>

          {/* Detail Panel */}
          <Grid item xs={12} md={8}>
            {!selectedComp ? (
              <Card className="card" sx={{ textAlign: 'center', py: 6 }}>
                <CardContent>
                  <Typography variant="h6" color="text.secondary">Select a competitor to view intelligence</Typography>
                </CardContent>
              </Card>
            ) : (
              <Card className="card">
                <CardContent>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2, flexWrap: 'wrap', gap: 1 }}>
                    <Typography variant="h5" fontWeight={700}>{selectedComp.name}</Typography>
                    <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                      <Button size="small" variant="outlined" startIcon={<RefreshIcon />} onClick={generateDossier} disabled={generating}>
                        {generating
                          ? `Running… ${dossierJob?.phases ? `${dossierJob.phases.filter(p => p.status === 'complete').length}/${dossierJob.phases.length}` : ''}`
                          : 'Generate Intelligence'}
                      </Button>
                      <Button size="small" variant="outlined" startIcon={<PersonaIcon />} onClick={generatePersona} disabled={generating}>
                        Build Persona
                      </Button>
                      <Button size="small" variant="outlined" startIcon={<PredictionIcon />} onClick={generatePredictions} disabled={generating}>
                        Predict Responses
                      </Button>
                      <Tooltip title="Delete all dossier entries, persona, and predictions for this competitor. Documents and the competitor record itself are preserved.">
                        <span>
                          <Button
                            size="small"
                            variant="outlined"
                            color="error"
                            startIcon={<DeleteSweepIcon />}
                            onClick={() => setConfirm({
                              kind: 'all-analysis',
                              label: `ALL analysis (dossier + persona + predictions) for ${selectedComp.name}`,
                            })}
                            disabled={generating || deleting}
                          >
                            Delete All Analysis
                          </Button>
                        </span>
                      </Tooltip>
                    </Box>
                  </Box>

                  {/* Live progress timeline for the dossier-generation job */}
                  {dossierJob && (
                    <IntelligenceRunTimeline
                      job={dossierJob}
                      competitorName={selectedComp.name}
                    />
                  )}

                  <Tabs value={activeTab} onChange={(_, v) => setActiveTab(v)} sx={{ mb: 2 }}>
                    <Tab label={`Dossier (${dossier.length})`} />
                    <Tab label="Threads" />
                    <Tab label="Timeline" />
                    <Tab label="Writer Persona" />
                    <Tab label={`Predictions (${predictions.length})`} />
                    <Tab label="Sources & Focus" />
                  </Tabs>

                  {/* Dossier Tab */}
                  {activeTab === 0 && (
                    dossier.length === 0 ? (
                      <Alert severity="info">No dossier entries yet. Click "Generate Dossier" to build intelligence.</Alert>
                    ) : (
                      <Box>
                        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
                          <Button
                            size="small"
                            color="error"
                            startIcon={<DeleteSweepIcon />}
                            onClick={() => setConfirm({
                              kind: 'clear-dossier',
                              label: `all ${dossier.length} dossier entries for ${selectedComp.name}`,
                            })}
                            disabled={deleting}
                          >
                            Clear all dossier entries
                          </Button>
                        </Box>
                        <DossierExecutiveSummary
                          competitorName={selectedComp.name}
                          entries={dossier}
                        />
                        {dossier.map(entry => (
                          <Accordion key={entry.id} variant="outlined" sx={{ mb: 1 }}>
                            <AccordionSummary expandIcon={<ExpandIcon />}>
                              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                                {entry.verified ? <VerifiedIcon color="success" fontSize="small" /> : <UnverifiedIcon color="warning" fontSize="small" />}
                                <Typography variant="body1" fontWeight={700}>{prettyCategory(entry.category)}</Typography>
                                <Chip label={entry.confidence} size="small" color={CONFIDENCE_COLORS[entry.confidence] || 'default'} sx={{ ml: 'auto' }} />
                                <Tooltip title="Delete this entry">
                                  <IconButton
                                    size="small"
                                    color="error"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setConfirm({
                                        kind: 'dossier-entry',
                                        targetId: entry.id,
                                        label: `dossier entry "${prettyCategory(entry.category)}"`,
                                      });
                                    }}
                                    sx={{ mr: 1 }}
                                  >
                                    <DeleteIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              </Box>
                            </AccordionSummary>
                            <AccordionDetails>
                              <MarkdownView dense>{entry.content}</MarkdownView>
                            </AccordionDetails>
                          </Accordion>
                        ))}
                      </Box>
                    )
                  )}

                  {/* Threads Tab — synthesized cross-category narratives */}
                  {activeTab === 1 && (
                    <IntelligenceThreadsTab competitorId={selectedComp.id} />
                  )}

                  {/* Timeline Tab — chronological event view */}
                  {activeTab === 2 && (
                    <IntelligenceTimelineTab competitorId={selectedComp.id} />
                  )}

                  {/* Persona Tab */}
                  {activeTab === 3 && (
                    persona ? (
                      <Box>
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 2 }}>
                          <Box>
                            <Typography variant="body1" fontWeight={700} gutterBottom>{persona.name}</Typography>
                            <Typography variant="body2" color="text.secondary" gutterBottom>{persona.description}</Typography>
                          </Box>
                          <Button
                            size="small"
                            color="error"
                            startIcon={<DeleteIcon />}
                            onClick={() => setConfirm({
                              kind: 'persona',
                              label: `the writer persona for ${selectedComp.name}`,
                            })}
                            disabled={deleting}
                          >
                            Delete persona
                          </Button>
                        </Box>
                        <Paper variant="outlined" sx={{ p: 2, mt: 2, borderRadius: 2, maxHeight: 500, overflow: 'auto' }}>
                          <Typography variant="caption" fontWeight={700} color="text.secondary">SYSTEM PROMPT</Typography>
                          <Box sx={{ mt: 1 }}>
                            <MarkdownView dense>{persona.system_prompt}</MarkdownView>
                          </Box>
                        </Paper>
                      </Box>
                    ) : (
                      <Alert severity="info">No writer persona generated yet. Build the dossier first, then click "Build Persona".</Alert>
                    )
                  )}

                  {/* Predictions Tab */}
                  {activeTab === 4 && (
                    predictions.length === 0 ? (
                      <Alert severity="info">No predictions generated yet. Build the persona first, then click "Predict Responses".</Alert>
                    ) : (
                      <Box>
                        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
                          <Button
                            size="small"
                            color="error"
                            startIcon={<DeleteSweepIcon />}
                            onClick={() => setConfirm({
                              kind: 'clear-predictions',
                              label: `all ${predictions.length} predicted responses for ${selectedComp.name}`,
                            })}
                            disabled={deleting}
                          >
                            Clear all predictions
                          </Button>
                        </Box>
                        {predictions.map(pred => (
                          <Accordion key={pred.id} variant="outlined" sx={{ mb: 1 }}>
                            <AccordionSummary expandIcon={<ExpandIcon />}>
                              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                                <Chip label={pred.section_id} size="small" variant="outlined" />
                                <Typography variant="body2" fontWeight={600}>Predicted Response</Typography>
                                <Chip label={`Confidence: ${pred.confidence_score}%`} size="small" color={pred.confidence_score >= 70 ? 'success' : 'warning'} sx={{ ml: 1 }} />
                                <Box sx={{ flex: 1 }} />
                                <Tooltip title="Delete this prediction">
                                  <IconButton
                                    size="small"
                                    color="error"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setConfirm({
                                        kind: 'prediction',
                                        targetId: pred.id,
                                        label: `prediction for section ${pred.section_id}`,
                                      });
                                    }}
                                    sx={{ mr: 1 }}
                                  >
                                    <DeleteIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              </Box>
                            </AccordionSummary>
                            <AccordionDetails>
                              <MarkdownView dense>{pred.predicted_response}</MarkdownView>
                              {pred.reasoning && (
                                <Box sx={{ mt: 2, p: 1.5, bgcolor: '#F5F7FA', borderRadius: 2 }}>
                                  <Typography variant="caption" fontWeight={700}>REASONING</Typography>
                                  <Box sx={{ mt: 0.5 }}>
                                    <MarkdownView dense>{pred.reasoning}</MarkdownView>
                                  </Box>
                                </Box>
                              )}
                            </AccordionDetails>
                          </Accordion>
                        ))}
                      </Box>
                    )
                  )}

                  {/* Sources & Focus Tab */}
                  {activeTab === 5 && (
                    <Box>
                      <CompetitorResearchFocus
                        competitor={selectedComp}
                        onSaved={(updated) => {
                          setSelectedComp(updated);
                          setCompetitors((prev) =>
                            prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c))
                          );
                        }}
                      />
                      <CompetitorDocumentsPanel
                        competitor={selectedComp}
                        onChange={fetchCompetitors}
                      />
                    </Box>
                  )}
                </CardContent>
              </Card>
            )}
          </Grid>
        </Grid>

        {/* Delete confirmation */}
        <Dialog open={Boolean(confirm)} onClose={() => !deleting && setConfirm(null)} maxWidth="sm" fullWidth>
          <DialogTitle>Confirm delete</DialogTitle>
          <DialogContent>
            <DialogContentText>
              This will permanently delete <strong>{confirm?.label}</strong>.
              Ingested documents and the competitor record itself are not affected.
              This cannot be undone.
            </DialogContentText>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setConfirm(null)} disabled={deleting}>Cancel</Button>
            <Button onClick={executeDelete} color="error" variant="contained" disabled={deleting} startIcon={<DeleteIcon />}>
              {deleting ? 'Deleting…' : 'Delete'}
            </Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Fade>
  );
};

export default CompetitorIntel;
