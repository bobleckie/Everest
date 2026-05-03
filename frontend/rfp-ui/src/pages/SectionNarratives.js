import React, { useEffect, useState } from 'react';
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
  LinearProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Divider,
} from '@mui/material';
import {
  AutoFixHigh as AssembleIcon,
  Visibility as ViewIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import { useProposal } from '../proposal/ProposalContext';

/**
 * Section Narratives — assembles approved per-requirement drafts within
 * each RFP submission section into a single cohesive narrative ready for
 * the final proposal.
 *
 * Backed by:
 *   GET /api/parsons-response/proposals/{id}/submission   (readiness rollup)
 *   POST /api/parsons-response/proposals/{id}/sections/{root}/assemble?persist=true
 *   GET /api/parsons-response/proposals/{id}/narratives/{root}
 */
export default function SectionNarratives() {
  const { proposalId } = useProposal();
  const [submission, setSubmission] = useState(null);
  const [narratives, setNarratives] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busySection, setBusySection] = useState(null);
  const [previewSection, setPreviewSection] = useState(null);
  const [previewBody, setPreviewBody] = useState('');

  const reload = () => {
    if (!proposalId) return;
    setLoading(true);
    Promise.all([
      axios.get(`/api/parsons-response/proposals/${proposalId}/submission`),
      axios.get(`/api/parsons-response/proposals/${proposalId}/narratives`),
    ])
      .then(([sub, nar]) => {
        setSubmission(sub.data);
        const map = {};
        for (const n of nar.data?.narratives || []) {
          map[n.section_root] = n;
        }
        setNarratives(map);
        setError(null);
      })
      .catch((err) => setError(err?.response?.data?.detail || err.message || 'Failed to load'))
      .finally(() => setLoading(false));
  };

  useEffect(reload, [proposalId]); // eslint-disable-line react-hooks/exhaustive-deps

  const assemble = async (root) => {
    setBusySection(root);
    try {
      await axios.post(
        `/api/parsons-response/proposals/${proposalId}/sections/${encodeURIComponent(root)}/assemble`,
        null,
        { params: { persist: true } },
      );
      reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Assembly failed');
    } finally {
      setBusySection(null);
    }
  };

  const preview = async (root) => {
    setPreviewSection(root);
    setPreviewBody('');
    try {
      const res = await axios.get(
        `/api/parsons-response/proposals/${proposalId}/narratives/${encodeURIComponent(root)}`,
      );
      setPreviewBody(res.data?.narrative_md || '(no body)');
    } catch (err) {
      setPreviewBody(`Error: ${err?.response?.data?.detail || err.message}`);
    }
  };

  if (loading) return <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={28} /></Box>;
  if (error) return <Box sx={{ p: 3 }}><Alert severity="error">{error}</Alert></Box>;
  if (!submission) return null;

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h5">Section Narratives</Typography>
        <Chip label={`${submission.section_count} sections · ${submission.total_requirements} requirements`} />
        <Box sx={{ flex: 1 }} />
        <Button startIcon={<RefreshIcon />} onClick={reload} size="small">Refresh</Button>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Assemble approved per-requirement drafts within each section into a single narrative
        ready for the final proposal. Once assembled, the section narrative is editable and
        won't auto-overwrite on re-assembly unless explicitly triggered.
      </Typography>

      <Stack spacing={2}>
        {submission.sections.map((s) => {
          const narr = narratives[s.section_root];
          const has = !!narr;
          const stale = narr?.is_stale;
          return (
            <Paper key={s.section_root} sx={{ p: 2 }}>
              <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 1 }}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                    {s.label}
                    <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                      {s.rfp_section_ref}
                    </Typography>
                  </Typography>
                  <Typography variant="caption" color="text.secondary">{s.description}</Typography>
                </Box>
                <Chip
                  size="small"
                  label={s.readiness}
                  color={s.readiness === 'green' ? 'success' : s.readiness === 'yellow' ? 'warning' : 'error'}
                />
                <Chip size="small" label={`${s.total} reqs`} variant="outlined" />
              </Stack>

              <Stack direction="row" spacing={2} sx={{ mb: 1 }}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="caption">Drafted: {s.drafted_count}/{s.total} ({s.pct_drafted}%)</Typography>
                  <LinearProgress variant="determinate" value={s.pct_drafted} sx={{ height: 6 }} />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="caption">Approved: {s.approved_count}/{s.total} ({s.pct_approved}%)</Typography>
                  <LinearProgress variant="determinate" value={s.pct_approved} color="success" sx={{ height: 6 }} />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="caption">Evidence: {s.evidence_covered_count}/{s.total} ({s.pct_evidence_covered}%)</Typography>
                  <LinearProgress variant="determinate" value={s.pct_evidence_covered} color="info" sx={{ height: 6 }} />
                </Box>
              </Stack>

              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                {has ? (
                  <Chip
                    size="small"
                    label={stale ? 'Narrative assembled — stale (re-assemble recommended)' : 'Narrative assembled'}
                    color={stale ? 'warning' : 'success'}
                    variant="outlined"
                  />
                ) : (
                  <Chip size="small" label="No narrative yet" variant="outlined" />
                )}
                <Box sx={{ flex: 1 }} />
                {has ? (
                  <Button size="small" startIcon={<ViewIcon />} onClick={() => preview(s.section_root)}>
                    View
                  </Button>
                ) : null}
                <Button
                  size="small"
                  variant={has && !stale ? 'outlined' : 'contained'}
                  startIcon={<AssembleIcon />}
                  disabled={busySection === s.section_root || s.approved_count === 0}
                  onClick={() => assemble(s.section_root)}
                >
                  {has ? 'Re-assemble' : 'Assemble'}
                </Button>
              </Stack>
            </Paper>
          );
        })}
      </Stack>

      <Dialog open={!!previewSection} onClose={() => setPreviewSection(null)} maxWidth="md" fullWidth>
        <DialogTitle>Section narrative — {previewSection}</DialogTitle>
        <DialogContent dividers>
          <Typography
            variant="body2"
            component="pre"
            sx={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit' }}
          >
            {previewBody || <CircularProgress size={20} />}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPreviewSection(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
