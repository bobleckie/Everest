import React, { useEffect, useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import axios from 'axios';
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  Alert,
  CircularProgress,
  TablePagination,
  Tooltip,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  Warning as WarnIcon,
  HelpOutline as QuestionIcon,
  ErrorOutline as ErrorIcon,
  ThumbUp as BulkApproveIcon,
} from '@mui/icons-material';
import { useProposal } from '../proposal/ProposalContext';

/**
 * Review Queue: drafts that need human attention first.
 * Sourced from /api/knowledge/requirements/needs-attention.
 */
export default function ReviewQueue() {
  const { proposalId } = useProposal();
  const [data, setData] = useState({ total: 0, requirements: [] });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [bulkPreview, setBulkPreview] = useState(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const reload = () => {
    setLoading(true);
    const params = { offset: page * pageSize, limit: pageSize };
    if (proposalId) params.proposal_id = proposalId;
    axios
      .get('/api/knowledge/requirements/needs-attention', { params })
      .then((res) => {
        setData(res.data);
        setError(null);
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || 'Failed to load');
      })
      .finally(() => setLoading(false));
  };

  const previewBulkApprove = async () => {
    setBulkBusy(true);
    try {
      const res = await axios.post('/api/parsons-response/requirements/bulk-approve', {
        proposal_id: proposalId,
        only_disposition: 'Comply',
        require_evidence: true,
        dry_run: true,
      });
      setBulkPreview(res.data.would_approve);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Preview failed');
    } finally {
      setBulkBusy(false);
    }
  };

  const confirmBulkApprove = async () => {
    setBulkBusy(true);
    try {
      await axios.post('/api/parsons-response/requirements/bulk-approve', {
        proposal_id: proposalId,
        only_disposition: 'Comply',
        require_evidence: true,
        dry_run: false,
      });
      setBulkPreview(null);
      reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Bulk-approve failed');
    } finally {
      setBulkBusy(false);
    }
  };

  useEffect(reload, [proposalId, page, pageSize]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) {
    return <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={28} /></Box>;
  }
  if (error) {
    return <Box sx={{ p: 3 }}><Alert severity="error">{error}</Alert></Box>;
  }

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <Typography variant="h5">Review Queue</Typography>
        <Chip label={`${data.total.toLocaleString()} drafts need attention`} color="warning" />
        <Box sx={{ flex: 1 }} />
        <Button
          variant="outlined"
          startIcon={<BulkApproveIcon />}
          disabled={bulkBusy}
          onClick={previewBulkApprove}
        >
          Bulk-approve clean drafts
        </Button>
      </Stack>

      <Dialog open={bulkPreview != null} onClose={() => setBulkPreview(null)}>
        <DialogTitle>Bulk-approve {bulkPreview} drafts?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            This will mark <strong>{bulkPreview}</strong> drafts as <em>approved</em> in one click.
            Selection criteria:
          </Typography>
          <ul>
            <li><Typography variant="body2">disposition is <strong>Comply</strong> (not exception / clarification)</Typography></li>
            <li><Typography variant="body2">at least one Parsons evidence chunk was cited</Typography></li>
            <li><Typography variant="body2">response text is non-empty</Typography></li>
          </ul>
          <Typography variant="body2" color="text.secondary">
            Drafts that don't meet the bar (Comply-with-exception, Take-exception, Needs-Clarification, or no evidence) are <strong>not</strong> touched and remain in this Review Queue for individual attention.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBulkPreview(null)} disabled={bulkBusy}>Cancel</Button>
          <Button onClick={confirmBulkApprove} variant="contained" color="success" disabled={bulkBusy}>
            Approve {bulkPreview}
          </Button>
        </DialogActions>
      </Dialog>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Drafts where Parsons doesn't fully comply (Comply-with-exception / Take-exception / Needs-Clarification),
        no Parsons evidence was cited, or a previous reviewer rejected the draft.
        Click any title to review and approve / reject / re-draft.
      </Typography>

      {data.requirements.length === 0 ? (
        <Alert severity="success">All drafts are well-grounded with no outstanding flags. Nothing in the review queue.</Alert>
      ) : (
        <Paper>
          <Stack divider={<Box sx={{ borderBottom: 1, borderColor: 'divider' }} />}>
            {data.requirements.map((r) => {
              const detailUrl = proposalId
                ? `/p/${proposalId}/requirement/${r.requirement_id}`
                : `/requirement/${r.requirement_id}`;
              const flags = [];
              if (r.compliance_disposition && r.compliance_disposition !== 'Comply') {
                flags.push({ icon: <WarnIcon />, label: r.compliance_disposition, color: 'warning' });
              }
              if (r.no_evidence) {
                flags.push({ icon: <ErrorIcon />, label: 'no Parsons evidence', color: 'error' });
              }
              if (r.empty_response) {
                flags.push({ icon: <ErrorIcon />, label: 'empty response', color: 'error' });
              }
              if (r.parsons_response_status === 'rejected') {
                flags.push({ icon: <QuestionIcon />, label: 'previously rejected', color: 'info' });
              }
              return (
                <Box key={r.requirement_id} sx={{ p: 2, '&:hover': { bgcolor: 'action.hover' } }}>
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                    <Typography
                      variant="body1"
                      component={RouterLink}
                      to={detailUrl}
                      sx={{ fontWeight: 600, color: 'primary.main', textDecoration: 'none', flex: 1 }}
                    >
                      {r.title}
                    </Typography>
                    {r.priority ? <Chip size="small" label={r.priority} /> : null}
                    <Typography variant="caption" color="text.secondary">{r.section_id}</Typography>
                  </Stack>
                  <Stack direction="row" spacing={0.5} sx={{ mb: 0.5, flexWrap: 'wrap' }}>
                    {flags.map((f, i) => (
                      <Chip key={i} size="small" icon={f.icon} label={f.label} color={f.color} variant="outlined" />
                    ))}
                  </Stack>
                  {r.response_preview ? (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      {r.response_preview}…
                    </Typography>
                  ) : null}
                </Box>
              );
            })}
          </Stack>
          <TablePagination
            component="div"
            count={data.total}
            page={page}
            onPageChange={(_, p) => setPage(p)}
            rowsPerPage={pageSize}
            onRowsPerPageChange={(e) => { setPageSize(parseInt(e.target.value, 10)); setPage(0); }}
            rowsPerPageOptions={[10, 25, 50, 100]}
          />
        </Paper>
      )}
    </Box>
  );
}
