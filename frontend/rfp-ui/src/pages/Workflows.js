import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stepper,
  Step,
  StepLabel,
  Chip,
  Alert,
  CircularProgress,
  Divider,
  IconButton,
  Tooltip,
  Snackbar,
  Collapse,
} from '@mui/material';
import {
  PlayArrow as PlayIcon,
  CheckCircle as CheckIcon,
  Refresh as RefreshIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Verified as ApproveIcon,
} from '@mui/icons-material';
import axios from 'axios';

const STEP_LABELS = {
  extract_requirements: 'Extract Requirements',
  competitive_analysis: 'Competitive Research',
  competitor_response: 'Competitor Response Draft',
  parsons_sme: 'Parsons SME Input',
  parsons_response: 'Parsons Response',
  conflict_check: 'Conflict Check',
};

const statusColor = (s) => (
  s === 'completed' || s === 'approved' ? 'success'
    : s === 'in_progress' ? 'primary'
    : 'default'
);

const Workflows = () => {
  const [workflows, setWorkflows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [openDialog, setOpenDialog] = useState(false);
  const [newWorkflow, setNewWorkflow] = useState({ rfpContent: '', competitorName: '' });
  const [expanded, setExpanded] = useState({});
  const [approving, setApproving] = useState(null);
  const [snack, setSnack] = useState(null);

  const fetchWorkflows = useCallback(async () => {
    try {
      const res = await axios.get('/api/orchestrator/workflows', { timeout: 10000 });
      setWorkflows(res.data.workflows || []);
    } catch (err) {
      console.error('Workflows fetch failed', err);
      setSnack({ severity: 'error', message: err?.response?.data?.detail || 'Failed to load workflows.' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchWorkflows(); }, [fetchWorkflows]);

  const handleCreateWorkflow = async () => {
    if (!newWorkflow.rfpContent.trim() || !newWorkflow.competitorName.trim()) {
      setSnack({ severity: 'warning', message: 'Please provide both competitor name and RFP content.' });
      return;
    }
    setStarting(true);
    try {
      await axios.post(
        '/api/orchestrator/start-rfp-workflow',
        {
          rfp_content: newWorkflow.rfpContent,
          competitor_name: newWorkflow.competitorName,
        },
        { timeout: 120000 },
      );
      setOpenDialog(false);
      setNewWorkflow({ rfpContent: '', competitorName: '' });
      setSnack({ severity: 'success', message: 'Workflow started. Refreshing…' });
      await fetchWorkflows();
    } catch (err) {
      console.error(err);
      setSnack({ severity: 'error', message: err?.response?.data?.detail || 'Failed to start workflow.' });
    } finally {
      setStarting(false);
    }
  };

  const approveStep = async (stepId) => {
    setApproving(stepId);
    try {
      await axios.post('/api/orchestrator/approve-step', { step_id: stepId, approval_level: 2 });
      setSnack({ severity: 'success', message: 'Step approved.' });
      await fetchWorkflows();
    } catch (err) {
      setSnack({ severity: 'error', message: err?.response?.data?.detail || 'Approval failed.' });
    } finally {
      setApproving(null);
    }
  };

  const toggleExpanded = (id) => setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h4">Workflows</Typography>
          <Typography variant="body2" color="text.secondary">
            End-to-end RFP response runs: requirement extraction → competitor analysis → Parsons
            response → conflict check. Approve each step to advance toward release.
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Tooltip title="Refresh">
            <IconButton onClick={fetchWorkflows}><RefreshIcon /></IconButton>
          </Tooltip>
          <Button variant="contained" startIcon={<PlayIcon />} onClick={() => setOpenDialog(true)} sx={{ borderRadius: 2 }}>
            Create New Workflow
          </Button>
        </Box>
      </Box>

      {workflows.length === 0 && (
        <Alert severity="info">No workflows yet. Click "Create New Workflow" to run your first one.</Alert>
      )}

      {workflows.map((w) => {
        const isOpen = !!expanded[w.workflow_id];
        return (
          <Card key={w.workflow_id} sx={{ mb: 2 }}>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Box>
                  <Typography variant="h6">Workflow #{w.workflow_id}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {w.steps.length} step{w.steps.length === 1 ? '' : 's'} · {w.current_step}/{w.steps.length} complete
                  </Typography>
                </Box>
                <Chip label={(w.status || '').replace('_', ' ')} color={statusColor(w.status)} />
              </Box>

              <Stepper activeStep={w.current_step} alternativeLabel>
                {w.steps.map((step) => (
                  <Step key={step.id} completed={step.status === 'completed' || step.status === 'approved'}>
                    <StepLabel
                      StepIconComponent={() => (
                        <Box sx={{
                          width: 28, height: 28, borderRadius: '50%',
                          bgcolor: (step.status === 'completed' || step.status === 'approved') ? 'success.main'
                            : step.status === 'pending' ? 'warning.main' : 'grey.400',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                        }}>
                          {(step.status === 'completed' || step.status === 'approved') && <CheckIcon sx={{ fontSize: 18, color: 'white' }} />}
                        </Box>
                      )}
                    >
                      {STEP_LABELS[step.name] || step.name}
                    </StepLabel>
                  </Step>
                ))}
              </Stepper>

              <Box sx={{ mt: 2, display: 'flex', gap: 1 }}>
                <Button
                  size="small"
                  startIcon={isOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                  onClick={() => toggleExpanded(w.workflow_id)}
                >
                  {isOpen ? 'Hide details' : 'View details'}
                </Button>
              </Box>

              <Collapse in={isOpen}>
                <Divider sx={{ my: 2 }} />
                {w.steps.map((step) => (
                  <Box key={step.id} sx={{ mb: 2 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                      <Typography variant="subtitle2" fontWeight={700}>
                        {STEP_LABELS[step.name] || step.name}
                      </Typography>
                      <Chip size="small" label={step.status} color={statusColor(step.status)} />
                      <Chip size="small" variant="outlined" label={`approvals: ${step.approval_level}`} />
                      <Box sx={{ flex: 1 }} />
                      {step.status !== 'approved' && (
                        <Button
                          size="small"
                          variant="outlined"
                          startIcon={approving === step.id ? <CircularProgress size={14} /> : <ApproveIcon />}
                          disabled={approving === step.id}
                          onClick={() => approveStep(step.id)}
                        >
                          Approve
                        </Button>
                      )}
                    </Box>
                    <TextField
                      fullWidth multiline minRows={2} maxRows={10}
                      value={step.preview || ''}
                      InputProps={{ readOnly: true, sx: { fontFamily: 'monospace', fontSize: 12 } }}
                    />
                  </Box>
                ))}
              </Collapse>
            </CardContent>
          </Card>
        );
      })}

      <Dialog open={openDialog} onClose={() => !starting && setOpenDialog(false)} maxWidth="md" fullWidth>
        <DialogTitle>Create New RFP Workflow</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Competitor Name"
            value={newWorkflow.competitorName}
            onChange={(e) => setNewWorkflow({ ...newWorkflow, competitorName: e.target.value })}
            sx={{ mb: 2, mt: 1 }}
            disabled={starting}
          />
          <TextField
            fullWidth
            multiline
            rows={8}
            label="RFP Content"
            value={newWorkflow.rfpContent}
            onChange={(e) => setNewWorkflow({ ...newWorkflow, rfpContent: e.target.value })}
            placeholder="Paste the RFP content here..."
            disabled={starting}
          />
          <Alert severity="info" sx={{ mt: 2 }}>
            This runs the full pipeline: requirement extraction, competitive analysis, a simulated
            competitor draft, Parsons SME consultation, Parsons response generation, and conflict
            detection. The call may take 30–90 seconds depending on model availability.
          </Alert>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenDialog(false)} disabled={starting}>Cancel</Button>
          <Button
            onClick={handleCreateWorkflow}
            variant="contained"
            disabled={starting}
            startIcon={starting ? <CircularProgress size={16} sx={{ color: 'white' }} /> : <PlayIcon />}
          >
            {starting ? 'Running…' : 'Start Workflow'}
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

export default Workflows;
