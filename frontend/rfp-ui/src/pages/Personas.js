import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Grid,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Chip,
  Avatar,
  CircularProgress,
  Alert,
  IconButton,
  Tooltip,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  List,
  ListItemButton,
  ListItemText,
  Divider,
  Paper,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Snackbar,
} from '@mui/material';
import {
  Psychology as AIIcon,
  Edit as EditIcon,
  Add as AddIcon,
  Delete as DeleteIcon,
  AutoAwesome as RefineIcon,
  ExpandMore as ExpandMoreIcon,
  ContentCopy as CopyIcon,
  Save as SaveIcon,
} from '@mui/icons-material';

// ---------------------------------------------------------------------------
// Personas page — per-persona prompt templates with AI-refinement.
// Layout:
//   [Left column]  list of personas (selectable, CRUD actions)
//   [Right column] details of selected persona + its prompt templates
//                  each template shows:  Prompt | AI-Refined Prompt | Refine button
// ---------------------------------------------------------------------------

const DEFAULT_PERSONA = {
  name: '',
  description: '',
  role: '',
  expertise_areas: '',
  writing_style: '',
  tone: 'professional',
  audience: 'external',
  is_active: true,
};

const DEFAULT_PROMPT = {
  name: '',
  description: '',
  template_type: 'section_refinement',
  template_content: '',
  variables: '',
  model_provider: 'anthropic',
  model_name: 'claude-sonnet-4-5-20250929',
  temperature: 0.7,
  max_tokens: 2000,
  is_active: true,
};

const MODEL_PRESETS = [
  { provider: 'anthropic', model: 'claude-sonnet-4-5-20250929', label: 'Claude Sonnet 4.5' },
  { provider: 'anthropic', model: 'claude-opus-4-20250514', label: 'Claude Opus 4' },
  { provider: 'anthropic', model: 'claude-3-5-sonnet-20241022', label: 'Claude 3.5 Sonnet' },
  { provider: 'openai', model: 'gpt-4o', label: 'GPT-4o' },
  { provider: 'openai', model: 'gpt-4o-mini', label: 'GPT-4o mini' },
  { provider: 'openai', model: 'gpt-4', label: 'GPT-4' },
];

const Personas = () => {
  const [personas, setPersonas] = useState([]);
  const [prompts, setPrompts] = useState([]);
  const [selectedPersonaId, setSelectedPersonaId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [snack, setSnack] = useState(null); // { severity, message }
  const [refiningId, setRefiningId] = useState(null);

  const [personaDialog, setPersonaDialog] = useState(false);
  const [editingPersona, setEditingPersona] = useState(null);
  const [personaForm, setPersonaForm] = useState(DEFAULT_PERSONA);

  const [promptDialog, setPromptDialog] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(null);
  const [promptForm, setPromptForm] = useState(DEFAULT_PROMPT);

  // -----------------------------------------------------------------------
  // Data loading
  // -----------------------------------------------------------------------

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [pRes, tRes] = await Promise.all([
        axios.get('/api/personas/', { timeout: 8000 }),
        axios.get('/api/prompts/', { timeout: 8000 }),
      ]);
      const pData = pRes.data;
      const tData = tRes.data;
      const personaList = Array.isArray(pData) ? pData : (pData?.personas || []);
      const promptList = Array.isArray(tData) ? tData : (tData?.templates || []);
      setPersonas(personaList);
      setPrompts(promptList);
      setSelectedPersonaId((prev) => {
        if (prev && personaList.some((p) => p.id === prev)) return prev;
        return personaList[0]?.id || null;
      });
    } catch (err) {
      console.error(err);
      setSnack({ severity: 'error', message: 'Failed to load personas or prompts.' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const selectedPersona = personas.find((p) => p.id === selectedPersonaId) || null;
  const personaPrompts = prompts.filter((pt) => pt.persona?.id === selectedPersonaId);

  // -----------------------------------------------------------------------
  // Persona CRUD
  // -----------------------------------------------------------------------

  const openCreatePersona = () => {
    setEditingPersona(null);
    setPersonaForm(DEFAULT_PERSONA);
    setPersonaDialog(true);
  };

  const openEditPersona = (persona) => {
    setEditingPersona(persona);
    setPersonaForm({
      name: persona.name || '',
      description: persona.description || '',
      role: persona.role || '',
      expertise_areas: (persona.expertise_areas || []).map((v) => JSON.stringify(v)).join(', '),
      writing_style: JSON.stringify(persona.writing_style || {}, null, 2),
      tone: persona.tone || 'professional',
      audience: persona.audience || 'external',
      is_active: persona.is_active !== false,
    });
    setPersonaDialog(true);
  };

  const savePersona = async () => {
    try {
      const payload = {
        ...personaForm,
        expertise_areas: personaForm.expertise_areas
          ? JSON.parse(`[${personaForm.expertise_areas}]`)
          : [],
        writing_style: personaForm.writing_style
          ? JSON.parse(personaForm.writing_style)
          : {},
      };
      if (editingPersona) {
        await axios.put(`/api/personas/${editingPersona.id}`, payload);
        setSnack({ severity: 'success', message: 'Persona updated.' });
      } else {
        await axios.post('/api/personas/create', payload);
        setSnack({ severity: 'success', message: 'Persona created.' });
      }
      setPersonaDialog(false);
      await loadAll();
    } catch (err) {
      setSnack({
        severity: 'error',
        message: err?.response?.data?.detail || 'Failed to save persona. Check expertise/writing-style JSON.',
      });
    }
  };

  const deletePersona = async (persona) => {
    if (!window.confirm(`Delete persona "${persona.name}"? This does not delete its prompt templates.`)) return;
    try {
      await axios.delete(`/api/personas/${persona.id}`);
      setSnack({ severity: 'success', message: 'Persona deleted.' });
      await loadAll();
    } catch (err) {
      setSnack({
        severity: 'error',
        message: err?.response?.data?.detail || 'Failed to delete persona.',
      });
    }
  };

  // -----------------------------------------------------------------------
  // Prompt template CRUD
  // -----------------------------------------------------------------------

  const openCreatePrompt = () => {
    if (!selectedPersona) return;
    setEditingPrompt(null);
    setPromptForm({ ...DEFAULT_PROMPT });
    setPromptDialog(true);
  };

  const openEditPrompt = (pt) => {
    setEditingPrompt(pt);
    setPromptForm({
      name: pt.name || '',
      description: pt.description || '',
      template_type: pt.template_type || 'section_refinement',
      template_content: pt.template_content || '',
      variables: (pt.variables || []).map((v) => JSON.stringify(v)).join(', '),
      model_provider: pt.model_provider || 'anthropic',
      model_name: pt.model_name || 'claude-sonnet-4-5-20250929',
      temperature: pt.temperature ?? 0.7,
      max_tokens: pt.max_tokens ?? 2000,
      is_active: pt.is_active !== false,
    });
    setPromptDialog(true);
  };

  const savePrompt = async () => {
    if (!selectedPersona) return;
    try {
      const payload = {
        ...promptForm,
        variables: promptForm.variables
          ? JSON.parse(`[${promptForm.variables}]`)
          : [],
        persona_id: selectedPersona.id,
      };
      if (editingPrompt) {
        await axios.put(`/api/prompts/${editingPrompt.id}`, payload);
        setSnack({ severity: 'success', message: 'Prompt template updated.' });
      } else {
        await axios.post('/api/prompts/create', payload);
        setSnack({ severity: 'success', message: 'Prompt template created.' });
      }
      setPromptDialog(false);
      await loadAll();
    } catch (err) {
      setSnack({
        severity: 'error',
        message: err?.response?.data?.detail || 'Failed to save prompt template.',
      });
    }
  };

  const deletePrompt = async (pt) => {
    if (!window.confirm(`Delete prompt template "${pt.name}"?`)) return;
    try {
      await axios.delete(`/api/prompts/${pt.id}`);
      setSnack({ severity: 'success', message: 'Prompt template deleted.' });
      await loadAll();
    } catch (err) {
      setSnack({
        severity: 'error',
        message: err?.response?.data?.detail || 'Failed to delete prompt template.',
      });
    }
  };

  // -----------------------------------------------------------------------
  // AI refinement (Claude 4.5 -> target model)
  // -----------------------------------------------------------------------

  const refinePrompt = async (pt) => {
    setRefiningId(pt.id);
    try {
      const res = await axios.post(`/api/prompts/${pt.id}/refine`);
      // Merge refined content into local state without a full reload.
      setPrompts((prev) => prev.map((x) => (
        x.id === pt.id
          ? {
              ...x,
              refined_content: res.data.refined_content,
              refined_at: res.data.refined_at,
              refined_by_model: res.data.refined_by_model,
            }
          : x
      )));
      setSnack({
        severity: 'success',
        message: `Refined for ${pt.model_provider}/${pt.model_name} using ${res.data.refined_by_model}.${res.data.notes ? ' ' + res.data.notes : ''}`,
      });
    } catch (err) {
      setSnack({
        severity: 'error',
        message: err?.response?.data?.detail || 'Refinement failed.',
      });
    } finally {
      setRefiningId(null);
    }
  };

  const copyToClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text || '');
      setSnack({ severity: 'info', message: 'Copied to clipboard.' });
    } catch {
      setSnack({ severity: 'warning', message: 'Clipboard copy not permitted.' });
    }
  };

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Box>
          <Typography variant="h4" component="h1">Personas & Prompts</Typography>
          <Typography variant="body2" color="text.secondary">
            Each persona owns one or more prompt templates. Use the AI-refine
            button to have Claude rewrite the prompt for its target model.
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openCreatePersona}>
          New Persona
        </Button>
      </Box>

      <Grid container spacing={3}>
        {/* Persona list */}
        <Grid item xs={12} md={4} lg={3}>
          <Paper sx={{ p: 1 }}>
            {personas.length === 0 && (
              <Box p={2}>
                <Alert severity="info">No personas yet. Create one to get started.</Alert>
              </Box>
            )}
            <List disablePadding>
              {personas.map((p, idx) => {
                const count = prompts.filter((x) => x.persona?.id === p.id).length;
                const active = p.id === selectedPersonaId;
                return (
                  <React.Fragment key={p.id}>
                    {idx > 0 && <Divider component="li" />}
                    <ListItemButton
                      selected={active}
                      onClick={() => setSelectedPersonaId(p.id)}
                      sx={{ alignItems: 'flex-start', py: 1.5 }}
                    >
                      <Avatar sx={{ bgcolor: active ? 'primary.main' : 'grey.300', mr: 1.5, mt: 0.5 }}>
                        <AIIcon fontSize="small" />
                      </Avatar>
                      <ListItemText
                        primary={
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Typography variant="subtitle2" fontWeight={700}>{p.name}</Typography>
                            <Chip label={count} size="small" />
                          </Box>
                        }
                        secondary={
                          <Box component="span" sx={{ display: 'block' }}>
                            <Typography variant="caption" color="text.secondary">{p.role}</Typography>
                          </Box>
                        }
                      />
                    </ListItemButton>
                  </React.Fragment>
                );
              })}
            </List>
          </Paper>
        </Grid>

        {/* Right side: selected persona + its prompts */}
        <Grid item xs={12} md={8} lg={9}>
          {!selectedPersona ? (
            <Alert severity="info">Select a persona on the left (or create one) to manage its prompt templates.</Alert>
          ) : (
            <>
              <Card sx={{ mb: 2 }}>
                <CardContent>
                  <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                    <Box>
                      <Typography variant="h5" fontWeight={700}>{selectedPersona.name}</Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        {selectedPersona.role} • Tone: {selectedPersona.tone} • Audience: {selectedPersona.audience}
                      </Typography>
                      <Typography variant="body2" sx={{ mb: 1 }}>
                        {selectedPersona.description}
                      </Typography>
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        {(selectedPersona.expertise_areas || []).map((area, i) => (
                          <Chip key={i} label={area} size="small" variant="outlined" />
                        ))}
                      </Box>
                    </Box>
                    <Box>
                      <Tooltip title="Edit persona"><IconButton onClick={() => openEditPersona(selectedPersona)}><EditIcon /></IconButton></Tooltip>
                      <Tooltip title="Delete persona"><IconButton color="error" onClick={() => deletePersona(selectedPersona)}><DeleteIcon /></IconButton></Tooltip>
                    </Box>
                  </Box>
                </CardContent>
              </Card>

              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1.5 }}>
                <Typography variant="h6">Prompt Templates ({personaPrompts.length})</Typography>
                <Button variant="outlined" startIcon={<AddIcon />} onClick={openCreatePrompt}>Add Prompt</Button>
              </Box>

              {personaPrompts.length === 0 && (
                <Alert severity="info">This persona has no prompt templates yet.</Alert>
              )}

              {personaPrompts.map((pt) => {
                const modelLabel = `${pt.model_provider}/${pt.model_name}`;
                const isRefining = refiningId === pt.id;
                return (
                  <Accordion key={pt.id} sx={{ mb: 1 }} defaultExpanded={personaPrompts.length === 1}>
                    <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                        <Typography variant="subtitle1" fontWeight={700} sx={{ flex: 1 }}>{pt.name}</Typography>
                        <Chip label={pt.template_type} size="small" />
                        <Chip label={modelLabel} size="small" variant="outlined" />
                        {pt.refined_at && <Chip label="Refined" size="small" color="success" />}
                      </Box>
                    </AccordionSummary>
                    <AccordionDetails>
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                        {pt.description}
                      </Typography>

                      <Grid container spacing={2}>
                        <Grid item xs={12} md={6}>
                          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                            <Typography variant="subtitle2" fontWeight={700}>Prompt</Typography>
                            <Tooltip title="Copy prompt"><IconButton size="small" onClick={() => copyToClipboard(pt.template_content)}><CopyIcon fontSize="small" /></IconButton></Tooltip>
                          </Box>
                          <TextField
                            fullWidth multiline minRows={10} maxRows={18}
                            value={pt.template_content || ''}
                            InputProps={{ readOnly: true, sx: { fontFamily: 'monospace', fontSize: 13 } }}
                          />
                        </Grid>
                        <Grid item xs={12} md={6}>
                          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                            <Typography variant="subtitle2" fontWeight={700}>
                              AI-Refined Prompt {pt.refined_by_model ? <Typography component="span" variant="caption" color="text.secondary">({pt.refined_by_model})</Typography> : null}
                            </Typography>
                            <Box>
                              {pt.refined_content && (
                                <Tooltip title="Copy refined prompt">
                                  <IconButton size="small" onClick={() => copyToClipboard(pt.refined_content)}><CopyIcon fontSize="small" /></IconButton>
                                </Tooltip>
                              )}
                              <Tooltip title={`Refine with Claude for ${modelLabel}`}>
                                <span>
                                  <Button
                                    size="small"
                                    variant="contained"
                                    startIcon={isRefining ? <CircularProgress size={16} sx={{ color: 'white' }} /> : <RefineIcon />}
                                    onClick={() => refinePrompt(pt)}
                                    disabled={isRefining}
                                    sx={{ ml: 1 }}
                                  >
                                    {isRefining ? 'Refining…' : 'Refine with Claude'}
                                  </Button>
                                </span>
                              </Tooltip>
                            </Box>
                          </Box>
                          <TextField
                            fullWidth multiline minRows={10} maxRows={18}
                            value={pt.refined_content || ''}
                            placeholder={`Click "Refine with Claude" to generate a refined version tuned for ${modelLabel}.`}
                            InputProps={{ readOnly: true, sx: { fontFamily: 'monospace', fontSize: 13 } }}
                          />
                          {pt.refined_at && (
                            <Typography variant="caption" color="text.secondary">
                              Refined {new Date(pt.refined_at).toLocaleString()}
                            </Typography>
                          )}
                        </Grid>
                      </Grid>

                      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2, gap: 1 }}>
                        <Button size="small" startIcon={<EditIcon />} onClick={() => openEditPrompt(pt)}>Edit</Button>
                        <Button size="small" color="error" startIcon={<DeleteIcon />} onClick={() => deletePrompt(pt)}>Delete</Button>
                      </Box>
                    </AccordionDetails>
                  </Accordion>
                );
              })}
            </>
          )}
        </Grid>
      </Grid>

      {/* ---------- Persona dialog ---------- */}
      <Dialog open={personaDialog} onClose={() => setPersonaDialog(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editingPersona ? 'Edit Persona' : 'Create Persona'}</DialogTitle>
        <DialogContent>
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12} sm={6}>
              <TextField fullWidth required label="Name" value={personaForm.name}
                onChange={(e) => setPersonaForm({ ...personaForm, name: e.target.value })} />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField fullWidth required label="Role" value={personaForm.role}
                onChange={(e) => setPersonaForm({ ...personaForm, role: e.target.value })} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth required multiline rows={3} label="Description"
                value={personaForm.description}
                onChange={(e) => setPersonaForm({ ...personaForm, description: e.target.value })} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth label='Expertise areas (JSON, comma-separated)'
                placeholder='"government RFPs", "technical writing"'
                value={personaForm.expertise_areas}
                onChange={(e) => setPersonaForm({ ...personaForm, expertise_areas: e.target.value })} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth multiline rows={3} label="Writing style (JSON object)"
                placeholder='{ "voice": "formal", "sentence_length": "medium" }'
                value={personaForm.writing_style}
                onChange={(e) => setPersonaForm({ ...personaForm, writing_style: e.target.value })} />
            </Grid>
            <Grid item xs={12} sm={6}>
              <FormControl fullWidth>
                <InputLabel>Tone</InputLabel>
                <Select value={personaForm.tone} label="Tone"
                  onChange={(e) => setPersonaForm({ ...personaForm, tone: e.target.value })}>
                  <MenuItem value="formal">Formal</MenuItem>
                  <MenuItem value="professional">Professional</MenuItem>
                  <MenuItem value="conversational">Conversational</MenuItem>
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12} sm={6}>
              <FormControl fullWidth>
                <InputLabel>Audience</InputLabel>
                <Select value={personaForm.audience} label="Audience"
                  onChange={(e) => setPersonaForm({ ...personaForm, audience: e.target.value })}>
                  <MenuItem value="internal">Internal</MenuItem>
                  <MenuItem value="external">External</MenuItem>
                  <MenuItem value="technical">Technical</MenuItem>
                  <MenuItem value="executive">Executive</MenuItem>
                </Select>
              </FormControl>
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPersonaDialog(false)}>Cancel</Button>
          <Button variant="contained" startIcon={<SaveIcon />} onClick={savePersona}
            disabled={!personaForm.name || !personaForm.description || !personaForm.role}>
            {editingPersona ? 'Update' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ---------- Prompt template dialog ---------- */}
      <Dialog open={promptDialog} onClose={() => setPromptDialog(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editingPrompt ? 'Edit Prompt Template' : 'Create Prompt Template'}</DialogTitle>
        <DialogContent>
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12} sm={6}>
              <TextField fullWidth required label="Name" value={promptForm.name}
                onChange={(e) => setPromptForm({ ...promptForm, name: e.target.value })} />
            </Grid>
            <Grid item xs={12} sm={6}>
              <TextField fullWidth required label="Type (e.g. section_refinement)"
                value={promptForm.template_type}
                onChange={(e) => setPromptForm({ ...promptForm, template_type: e.target.value })} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth required multiline rows={2} label="Description"
                value={promptForm.description}
                onChange={(e) => setPromptForm({ ...promptForm, description: e.target.value })} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth required multiline rows={8} label="Template content"
                helperText="Use {variable_name} placeholders."
                value={promptForm.template_content}
                InputProps={{ sx: { fontFamily: 'monospace', fontSize: 13 } }}
                onChange={(e) => setPromptForm({ ...promptForm, template_content: e.target.value })} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth label='Variables (JSON, comma-separated)'
                placeholder='"section_name", "rfp_context"'
                value={promptForm.variables}
                onChange={(e) => setPromptForm({ ...promptForm, variables: e.target.value })} />
            </Grid>
            <Grid item xs={12} sm={6}>
              <FormControl fullWidth>
                <InputLabel>Target model</InputLabel>
                <Select
                  value={`${promptForm.model_provider}|${promptForm.model_name}`}
                  label="Target model"
                  onChange={(e) => {
                    const [provider, model] = e.target.value.split('|');
                    setPromptForm({ ...promptForm, model_provider: provider, model_name: model });
                  }}
                >
                  {MODEL_PRESETS.map((m) => (
                    <MenuItem key={`${m.provider}|${m.model}`} value={`${m.provider}|${m.model}`}>
                      {m.label} <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>({m.provider})</Typography>
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={6} sm={3}>
              <TextField fullWidth type="number" label="Temperature"
                inputProps={{ step: 0.1, min: 0, max: 2 }}
                value={promptForm.temperature}
                onChange={(e) => setPromptForm({ ...promptForm, temperature: parseFloat(e.target.value) })} />
            </Grid>
            <Grid item xs={6} sm={3}>
              <TextField fullWidth type="number" label="Max tokens"
                inputProps={{ step: 100, min: 100 }}
                value={promptForm.max_tokens}
                onChange={(e) => setPromptForm({ ...promptForm, max_tokens: parseInt(e.target.value, 10) })} />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPromptDialog(false)}>Cancel</Button>
          <Button variant="contained" startIcon={<SaveIcon />} onClick={savePrompt}
            disabled={!promptForm.name || !promptForm.description || !promptForm.template_content}>
            {editingPrompt ? 'Update' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={!!snack}
        autoHideDuration={6000}
        onClose={() => setSnack(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {snack ? (
          <Alert severity={snack.severity} onClose={() => setSnack(null)} sx={{ width: '100%' }}>
            {snack.message}
          </Alert>
        ) : <div />}
      </Snackbar>
    </Box>
  );
};

export default Personas;
