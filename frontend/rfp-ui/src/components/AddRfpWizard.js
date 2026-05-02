/**
 * AddRfpWizard
 *
 * Three-step Stepper-based modal that creates a new proposal via
 * POST /api/proposals/wizard, optionally uploads an RFP document to it,
 * then hands the new proposal back to the caller for navigation.
 *
 * Steps:
 *   1. Basics            — title, agency, solicitation #, due date
 *   2. Capture details   — capture lead, win % dial, target value, description
 *   3. (Optional) Upload — drop or pick an RFP PDF/DOCX/etc., source_type='rfp'
 *   4. Confirmation      — review and submit
 */
import React, { useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  Stepper, Step, StepLabel, Box, TextField, Button, Stack, Typography,
  Slider, MenuItem, Alert, LinearProgress, Chip,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  CheckCircle as CheckIcon,
  Description as DocIcon,
} from '@mui/icons-material';
import axios from 'axios';

const STEPS = ['Basics', 'Capture details', 'Upload RFP (optional)', 'Confirm'];

// Initial form scaffold — keep top-level so resets don't drift.
const blankForm = () => ({
  title: '',
  rfp_reference: '',
  solicitation_number: '',
  issuing_agency: '',
  due_date: '', // local date input "YYYY-MM-DD"
  capture_lead: '',
  win_probability: 50,
  target_value_usd: '',
  description: '',
  upload_file: null,
});

export default function AddRfpWizard({ open, onClose, onCreated }) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState(blankForm());
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  const reset = () => { setForm(blankForm()); setStep(0); setError(null); };
  const close = () => { if (!submitting && !uploading) { reset(); onClose && onClose(); } };

  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e?.target ? e.target.value : e }));

  const canNextFromBasics = !!form.title.trim();

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      // Step A: create the proposal.
      const payload = {
        title: form.title.trim(),
        rfp_reference: form.rfp_reference.trim() || null,
        solicitation_number: form.solicitation_number.trim() || null,
        issuing_agency: form.issuing_agency.trim() || null,
        due_date: form.due_date ? new Date(form.due_date + 'T17:00:00').toISOString() : null,
        capture_lead: form.capture_lead.trim() || null,
        win_probability: Number.isFinite(parseInt(form.win_probability, 10))
          ? parseInt(form.win_probability, 10) : null,
        target_value_usd: form.target_value_usd === '' ? null : Number(form.target_value_usd),
        description: form.description.trim() || null,
      };
      const { data: created } = await axios.post('/api/proposals/wizard', payload);

      // Step B: optional doc upload, if the user attached one.
      if (form.upload_file) {
        setUploading(true);
        const fd = new FormData();
        fd.append('file', form.upload_file);
        fd.append('source_type', 'rfp');
        fd.append('document_type', 'rfp');
        // The /api/documents/upload endpoint accepts proposal_id as a form field.
        fd.append('proposal_id', String(created.id));
        try {
          await axios.post('/api/documents/upload', fd, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });
        } catch (uploadErr) {
          // Surface as a non-blocking warning — proposal exists; user can
          // upload later from the workspace if needed.
          console.warn('Wizard upload failed', uploadErr);
        } finally {
          setUploading(false);
        }
      }

      onCreated && onCreated(created);
      reset();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to create proposal');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={close} maxWidth="md" fullWidth>
      <DialogTitle>
        <Typography variant="h6" fontWeight={700}>Create a new RFP</Typography>
        <Typography variant="caption" color="text.secondary">
          Walks you through the basics so the proposal is ready to use immediately.
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        <Stepper activeStep={step} sx={{ mb: 3 }}>
          {STEPS.map((s) => <Step key={s}><StepLabel>{s}</StepLabel></Step>)}
        </Stepper>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {(submitting || uploading) && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="caption" color="text.secondary">
              {submitting ? 'Creating proposal…' : 'Uploading document…'}
            </Typography>
            <LinearProgress />
          </Box>
        )}

        {step === 0 && (
          <Stack spacing={2}>
            <TextField
              label="Proposal title *"
              fullWidth required autoFocus
              value={form.title}
              onChange={set('title')}
              helperText="Short, descriptive — e.g. 'NJ MVC Vehicle Inspection 2026 Rebid'"
            />
            <TextField
              label="Issuing agency"
              fullWidth
              value={form.issuing_agency}
              onChange={set('issuing_agency')}
              helperText="e.g. NJ Treasury / MVC"
            />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <TextField
                label="Solicitation number"
                fullWidth
                value={form.solicitation_number}
                onChange={set('solicitation_number')}
                helperText="e.g. T1628"
              />
              <TextField
                label="Due date"
                type="date"
                fullWidth
                InputLabelProps={{ shrink: true }}
                value={form.due_date}
                onChange={set('due_date')}
              />
            </Stack>
            <TextField
              label="RFP reference (optional)"
              fullWidth
              value={form.rfp_reference}
              onChange={set('rfp_reference')}
              helperText="Internal capture-team reference; defaults to title if blank."
            />
          </Stack>
        )}

        {step === 1 && (
          <Stack spacing={3}>
            <TextField
              label="Capture lead"
              fullWidth
              value={form.capture_lead}
              onChange={set('capture_lead')}
              helperText="Person responsible for this proposal"
            />
            <Box>
              <Typography variant="body2" sx={{ mb: 1 }}>
                Win probability — capture-team estimate
              </Typography>
              <Stack direction="row" alignItems="center" spacing={2}>
                <Slider
                  value={form.win_probability}
                  onChange={(_, v) => setForm(f => ({ ...f, win_probability: v }))}
                  min={0} max={100} step={5}
                  marks={[{ value: 0, label: '0%' }, { value: 50, label: '50%' }, { value: 100, label: '100%' }]}
                  sx={{
                    flex: 1,
                    '& .MuiSlider-track': {
                      background: 'linear-gradient(90deg,#c62828,#ed6c02,#2e7d32)',
                      border: 'none',
                    },
                  }}
                />
                <Chip
                  label={`${form.win_probability}%`}
                  color={form.win_probability >= 70 ? 'success'
                       : form.win_probability >= 40 ? 'warning' : 'error'}
                  sx={{ fontWeight: 700, minWidth: 60 }}
                />
              </Stack>
            </Box>
            <TextField
              label="Target contract value (USD)"
              type="number"
              fullWidth
              value={form.target_value_usd}
              onChange={set('target_value_usd')}
              helperText="Total contract ceiling / target — used for portfolio aggregates"
            />
            <TextField
              label="Description"
              multiline rows={3} fullWidth
              value={form.description}
              onChange={set('description')}
              helperText="Short capture summary — shown on portfolio cards"
            />
          </Stack>
        )}

        {step === 2 && (
          <Stack spacing={2} alignItems="stretch">
            <Typography variant="body2" color="text.secondary">
              Optional: upload the RFP document now. You can also do this later from the
              RFP Setup page inside the proposal workspace.
            </Typography>
            <Box sx={{
              border: '2px dashed rgba(0,174,230,0.4)',
              borderRadius: 2, p: 4, textAlign: 'center',
              bgcolor: form.upload_file ? 'rgba(46,125,50,0.04)' : 'rgba(0,174,230,0.04)',
              transition: 'background 0.2s ease',
            }}>
              {form.upload_file ? (
                <Stack spacing={1} alignItems="center">
                  <CheckIcon color="success" sx={{ fontSize: 40 }} />
                  <Typography variant="body1" fontWeight={700}>{form.upload_file.name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {(form.upload_file.size / 1024 / 1024).toFixed(2)} MB
                  </Typography>
                  <Button size="small"
                          onClick={() => setForm(f => ({ ...f, upload_file: null }))}>
                    Remove
                  </Button>
                </Stack>
              ) : (
                <>
                  <UploadIcon sx={{ fontSize: 40, color: 'rgba(0,174,230,0.6)', mb: 1 }} />
                  <Typography variant="body2" sx={{ mb: 1.5 }}>
                    Drop your RFP file here or click to browse
                  </Typography>
                  <Button variant="outlined" component="label" startIcon={<UploadIcon />}>
                    Choose file
                    <input
                      type="file" hidden
                      accept=".pdf,.docx,.doc,.txt,.xlsx,.xls"
                      onChange={(e) => setForm(f => ({ ...f, upload_file: e.target.files?.[0] || null }))}
                    />
                  </Button>
                </>
              )}
            </Box>
          </Stack>
        )}

        {step === 3 && (
          <Stack spacing={2}>
            <Typography variant="body2" color="text.secondary">
              Review and submit. The wizard will create the proposal record,
              its 13 standard sections, and (if attached) ingest the RFP file
              for downstream extraction.
            </Typography>
            <Box sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(0,174,230,0.04)',
                       border: '1px solid rgba(0,174,230,0.2)' }}>
              <Typography variant="overline" color="text.secondary">Basics</Typography>
              <Typography variant="body2" fontWeight={700}>{form.title || '—'}</Typography>
              <Typography variant="caption">
                {[form.issuing_agency, form.solicitation_number, form.due_date]
                  .filter(Boolean).join(' · ') || 'No agency / number / due date set'}
              </Typography>
            </Box>
            <Box sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(46,125,50,0.04)',
                       border: '1px solid rgba(46,125,50,0.2)' }}>
              <Typography variant="overline" color="text.secondary">Capture</Typography>
              <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap' }}>
                <Typography variant="body2"><strong>Lead:</strong> {form.capture_lead || '—'}</Typography>
                <Typography variant="body2"><strong>Win %:</strong> {form.win_probability}%</Typography>
                <Typography variant="body2"><strong>Target $:</strong> {form.target_value_usd || '—'}</Typography>
              </Stack>
              {form.description && (
                <Typography variant="body2" sx={{ mt: 1 }}>{form.description}</Typography>
              )}
            </Box>
            <Box sx={{ p: 2, borderRadius: 2,
                       bgcolor: form.upload_file ? 'rgba(0,174,230,0.04)' : 'rgba(0,0,0,0.03)',
                       border: '1px solid rgba(0,0,0,0.08)' }}>
              <Typography variant="overline" color="text.secondary">RFP document</Typography>
              {form.upload_file ? (
                <Stack direction="row" alignItems="center" spacing={1}>
                  <DocIcon color="primary" />
                  <Typography variant="body2" fontWeight={700}>{form.upload_file.name}</Typography>
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  No file attached — upload from RFP Setup later.
                </Typography>
              )}
            </Box>
          </Stack>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={close} disabled={submitting || uploading}>Cancel</Button>
        <Box sx={{ flex: 1 }} />
        {step > 0 && (
          <Button onClick={() => setStep(s => s - 1)} disabled={submitting || uploading}>
            Back
          </Button>
        )}
        {step < STEPS.length - 1 ? (
          <Button
            variant="contained"
            onClick={() => setStep(s => s + 1)}
            disabled={(step === 0 && !canNextFromBasics) || submitting}
          >
            Next
          </Button>
        ) : (
          <Button
            variant="contained"
            color="success"
            onClick={submit}
            disabled={submitting || uploading || !form.title.trim()}
          >
            {submitting ? 'Creating…' : 'Create proposal'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
