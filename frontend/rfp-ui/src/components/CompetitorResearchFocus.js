import React, { useEffect, useState } from 'react';
import {
  Box, Typography, TextField, Button, Stack, Chip, Alert, IconButton, Tooltip,
} from '@mui/material';
import { Edit as EditIcon, Save as SaveIcon, Close as CloseIcon, TipsAndUpdates as TipsIcon } from '@mui/icons-material';
import axios from 'axios';

/**
 * Editable "Research Focus" block that is persisted on competitor.description
 * and fed verbatim into every analyst and persona prompt. This is where the
 * capture manager tells the AI which division to focus on, what public
 * signals to look for, and any context the AI cannot infer on its own.
 */
const PLACEHOLDER = `Example:
- Target division: Transportation Solutions > Vehicle Inspection Services (formerly Opus Inspection).
- Parent: Publicly traded on <exchange> under ticker <TKR>; pull segment financials from latest 10-K.
- Known active contracts: Virginia, Colorado, Utah, Missouri — see uploaded FOIA docs.
- Financial signal: the vehicle-inspection division has been publicly flagged for margin pressure and
  goodwill risk in FY24 filings — validate.
- Care most about: technology stack per state, executive leadership of this division, service-quality
  metrics from state audits, contract wins/losses in the last 5 years, scale (stations, inspections/year).`;

const CompetitorResearchFocus = ({ competitor, onSaved }) => {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setValue(competitor?.description || '');
    setEditing(false);
    setError('');
  }, [competitor?.id, competitor?.description]);

  if (!competitor) return null;

  const hasFocus = !!(competitor.description && competitor.description.trim());

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const { data } = await axios.put(`/api/competitors/${competitor.id}`, {
        description: value,
      });
      setEditing(false);
      onSaved?.(data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box
      sx={{
        mt: 2,
        p: 2,
        borderRadius: 2,
        border: '1px solid',
        borderColor: hasFocus ? 'rgba(0,174,230,0.35)' : 'divider',
        bgcolor: hasFocus ? 'rgba(0,174,230,0.04)' : 'background.paper',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <TipsIcon fontSize="small" sx={{ color: '#00AEE6' }} />
        <Typography variant="subtitle2" fontWeight={700}>Research Focus</Typography>
        <Chip
          size="small"
          label={hasFocus ? 'Configured' : 'Not set'}
          color={hasFocus ? 'info' : 'default'}
          variant="outlined"
          sx={{ ml: 1 }}
        />
        {!editing ? (
          <Tooltip title="Edit">
            <IconButton size="small" onClick={() => setEditing(true)} sx={{ ml: 'auto' }}>
              <EditIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        ) : (
          <Stack direction="row" spacing={0.5} sx={{ ml: 'auto' }}>
            <Button size="small" variant="contained" startIcon={<SaveIcon />} onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
            <Button
              size="small"
              variant="text"
              startIcon={<CloseIcon />}
              onClick={() => { setEditing(false); setValue(competitor.description || ''); }}
              disabled={saving}
            >
              Cancel
            </Button>
          </Stack>
        )}
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.25 }}>
        Tell the analyst which division, ticker, contracts, and signals to prioritize. This block is
        pasted verbatim into every dossier and persona prompt — the more specific, the better.
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {editing ? (
        <TextField
          multiline
          minRows={6}
          fullWidth
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={PLACEHOLDER}
          size="small"
        />
      ) : hasFocus ? (
        <Typography
          variant="body2"
          sx={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.55, color: 'text.primary' }}
        >
          {competitor.description}
        </Typography>
      ) : (
        <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic', fontSize: 13 }}>
          No research focus set yet. Click the edit icon and paste the division, ticker, known contracts,
          and signals the analyst should prioritize.
        </Typography>
      )}
    </Box>
  );
};

export default CompetitorResearchFocus;
