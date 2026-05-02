/**
 * TargetedCompetitorsDialog
 *
 * Modal that lets the capture team grow / shrink the list of competitors
 * tracked on a proposal. The proposal news feed fans out across this list
 * automatically — adding a competitor here makes their news show up in
 * the feed on the next refresh; removing them stops it.
 *
 * Backed by:
 *   GET    /api/proposals/{id}/competitors
 *   POST   /api/proposals/{id}/competitors            { competitor_id, relevance }
 *   DELETE /api/proposals/{id}/competitors/{cid}
 *   GET    /api/competitors                            (full library to add from)
 */
import React, { useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, Stack, Box,
  Typography, IconButton, Chip, Autocomplete, TextField, Alert,
  CircularProgress, MenuItem, Select, Tooltip, Divider,
} from '@mui/material';
import {
  Add as AddIcon,
  Delete as DeleteIcon,
  Star as StarIcon,
  Visibility as TrackingIcon,
  RemoveCircleOutline as RuledOutIcon,
} from '@mui/icons-material';
import axios from 'axios';

const RELEVANCE_VISUAL = {
  primary:   { color: 'primary',  icon: <StarIcon fontSize="small" />, label: 'Primary' },
  tracking:  { color: 'info',     icon: <TrackingIcon fontSize="small" />, label: 'Tracking' },
  ruled_out: { color: 'default',  icon: <RuledOutIcon fontSize="small" />, label: 'Ruled out' },
};

export default function TargetedCompetitorsDialog({ open, onClose, proposalId, onChanged }) {
  const [tracked, setTracked] = useState([]);
  const [allCompetitors, setAllCompetitors] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState(null);
  const [pickedRelevance, setPickedRelevance] = useState('tracking');

  const load = async () => {
    if (!proposalId) return;
    setLoading(true);
    setError(null);
    try {
      const [trackedRes, allRes] = await Promise.all([
        axios.get(`/api/proposals/${proposalId}/competitors`),
        axios.get('/api/competitors'),
      ]);
      setTracked(trackedRes.data?.competitors || []);
      setAllCompetitors(allRes.data?.competitors || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (open) load(); /* eslint-disable-next-line */ }, [open, proposalId]);

  const trackedIds = new Set(tracked.map(t => t.competitor_id));
  const addable = allCompetitors.filter(c => !trackedIds.has(c.id));

  const add = async () => {
    if (!picked) return;
    setAdding(true); setError(null);
    try {
      await axios.post(`/api/proposals/${proposalId}/competitors`, {
        competitor_id: picked.id,
        relevance: pickedRelevance,
      });
      setPicked(null);
      setPickedRelevance('tracking');
      await load();
      onChanged && onChanged();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to add');
    } finally {
      setAdding(false);
    }
  };

  const remove = async (competitorId) => {
    if (!window.confirm('Stop tracking this competitor on this proposal? '
                        + 'Their news will no longer appear in the feed.')) return;
    try {
      await axios.delete(`/api/proposals/${proposalId}/competitors/${competitorId}`);
      await load();
      onChanged && onChanged();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to remove');
    }
  };

  const changeRelevance = async (competitorId, relevance) => {
    try {
      await axios.post(`/api/proposals/${proposalId}/competitors`, {
        competitor_id: competitorId, relevance,
      });
      await load();
      onChanged && onChanged();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to update');
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        Tracked Competitors
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
          The news feed and intel rollups fan out across this list.
          Add or remove competitors anytime — changes take effect on the next refresh.
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

        {/* Add row */}
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <Autocomplete
            sx={{ flex: 1 }}
            options={addable}
            value={picked}
            onChange={(_, v) => setPicked(v)}
            getOptionLabel={(o) => o?.name || ''}
            isOptionEqualToValue={(o, v) => o?.id === v?.id}
            renderInput={(p) => <TextField {...p} label="Add competitor"
                                            size="small" placeholder="Type to search…" />}
            disabled={adding}
            size="small"
          />
          <Select
            size="small"
            value={pickedRelevance}
            onChange={(e) => setPickedRelevance(e.target.value)}
            sx={{ minWidth: 140 }}
            disabled={adding}
          >
            {Object.entries(RELEVANCE_VISUAL).map(([k, v]) => (
              <MenuItem key={k} value={k}>{v.label}</MenuItem>
            ))}
          </Select>
          <Button
            variant="contained"
            startIcon={adding ? <CircularProgress size={16} /> : <AddIcon />}
            onClick={add}
            disabled={!picked || adding}
          >
            Add
          </Button>
        </Stack>

        <Divider sx={{ mb: 2 }} />

        {loading ? (
          <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress /></Box>
        ) : tracked.length === 0 ? (
          <Box sx={{ py: 4, textAlign: 'center' }}>
            <Typography variant="body2" color="text.secondary">
              No competitors tracked yet. Add one above to start pulling
              their news into this RFP's feed.
            </Typography>
          </Box>
        ) : (
          <Stack spacing={1}>
            {tracked.map((t) => {
              const v = RELEVANCE_VISUAL[t.relevance] || RELEVANCE_VISUAL.tracking;
              return (
                <Stack key={t.id} direction="row" alignItems="center" spacing={1}
                       sx={{
                         p: 1.25, borderRadius: 2,
                         border: '1px solid rgba(0,0,0,0.08)',
                       }}>
                  <Tooltip title={v.label}>
                    <Box sx={{ color: 'text.secondary' }}>{v.icon}</Box>
                  </Tooltip>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600} noWrap>
                      {t.competitor_name}
                    </Typography>
                    {t.competitor_website && (
                      <Typography variant="caption" color="text.secondary" noWrap>
                        {t.competitor_website}
                      </Typography>
                    )}
                  </Box>
                  <Select
                    size="small"
                    value={t.relevance}
                    onChange={(e) => changeRelevance(t.competitor_id, e.target.value)}
                    sx={{ minWidth: 120, height: 32 }}
                  >
                    {Object.entries(RELEVANCE_VISUAL).map(([k, vv]) => (
                      <MenuItem key={k} value={k}>{vv.label}</MenuItem>
                    ))}
                  </Select>
                  <Tooltip title="Stop tracking">
                    <IconButton size="small" onClick={() => remove(t.competitor_id)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Stack>
              );
            })}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
