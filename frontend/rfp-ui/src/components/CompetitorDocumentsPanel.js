import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Box, Typography, Button, List, ListItem, ListItemText, Chip, IconButton,
  LinearProgress, MenuItem, TextField, Stack, Alert, Tooltip,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  Refresh as RefreshIcon,
  Description as DocIcon,
  Delete as DeleteIcon,
} from '@mui/icons-material';
import axios from 'axios';

const SOURCE_TYPES = [
  { value: 'competitor_foia', label: 'FOIA response' },
  { value: 'competitor_proposal', label: 'Proposal / bid' },
  { value: 'reference', label: 'Reference / filing / news' },
];

/**
 * Panel shown on the Competitor Intelligence page that lets a user upload
 * documents tagged to a specific competitor. The persona and the analyst
 * playbook both look at these uploads as their highest-confidence source.
 */
const CompetitorDocumentsPanel = ({ competitor, onChange }) => {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sourceType, setSourceType] = useState('competitor_foia');
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const fileInputRef = useRef(null);

  const fetchDocs = useCallback(async () => {
    if (!competitor) return;
    setLoading(true);
    try {
      const res = await axios.get('/api/documents', {
        params: { competitor_id: competitor.id, limit: 100 },
      });
      setDocs(res.data.documents || res.data || []);
    } catch {
      setDocs([]);
    } finally {
      setLoading(false);
    }
  }, [competitor]);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  const handleFilePick = () => fileInputRef.current?.click();

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !competitor) return;
    setError('');
    setSuccess('');
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('source_type', sourceType);
      form.append('competitor_id', competitor.id);
      if (description) form.append('description', description);
      await axios.post('/api/documents/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setSuccess(`Uploaded "${file.name}" — processing.`);
      setDescription('');
      await fetchDocs();
      onChange?.();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (docId) => {
    if (!window.confirm('Remove this document from the competitor?')) return;
    try {
      await axios.delete(`/api/documents/${docId}`);
      await fetchDocs();
      onChange?.();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Delete failed');
    }
  };

  if (!competitor) return null;

  return (
    <Box
      sx={{
        mt: 2,
        p: 2,
        borderRadius: 2,
        border: '1px dashed',
        borderColor: 'divider',
        bgcolor: 'background.paper',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <DocIcon fontSize="small" color="action" />
        <Typography variant="subtitle2" fontWeight={700}>
          Source Documents for {competitor.name}
        </Typography>
        <Chip label={`${docs.length} file${docs.length === 1 ? '' : 's'}`} size="small" sx={{ ml: 1 }} />
        <Tooltip title="Refresh">
          <IconButton size="small" onClick={fetchDocs} sx={{ ml: 'auto' }} disabled={loading}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
        FOIA responses, past proposals, SEC filings, news clippings, audit reports — anything tagged
        here is fed directly to the analyst and the competitor-writer persona as their highest-confidence
        source. The persona is explicitly told these documents exist.
      </Typography>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 1 }}>
        <TextField
          select
          label="Source type"
          size="small"
          value={sourceType}
          onChange={(e) => setSourceType(e.target.value)}
          sx={{ minWidth: 180 }}
        >
          {SOURCE_TYPES.map((s) => (
            <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>
          ))}
        </TextField>
        <TextField
          label="Short description (optional)"
          size="small"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          fullWidth
          placeholder="e.g. Virginia 2023 emissions contract — FOIA'd technical proposal"
        />
        <Button
          variant="contained"
          startIcon={<UploadIcon />}
          onClick={handleFilePick}
          disabled={uploading}
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          hidden
          accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx,.xls"
          onChange={handleFileChange}
        />
      </Stack>
      {uploading && <LinearProgress sx={{ mb: 1 }} />}
      {error && <Alert severity="error" sx={{ mb: 1 }} onClose={() => setError('')}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 1 }} onClose={() => setSuccess('')}>{success}</Alert>}

      {docs.length === 0 ? (
        <Alert severity="info" variant="outlined" sx={{ mt: 1 }}>
          No documents uploaded yet. Upload at least one FOIA response or past proposal before regenerating
          the dossier for richer, sourced intelligence.
        </Alert>
      ) : (
        <List dense disablePadding sx={{ mt: 0.5, maxHeight: 260, overflow: 'auto' }}>
          {docs.map((d) => (
            <ListItem
              key={d.id}
              sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, mb: 0.5 }}
              secondaryAction={
                <Tooltip title="Remove">
                  <IconButton size="small" edge="end" onClick={() => handleDelete(d.id)}>
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              }
            >
              <ListItemText
                primary={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                    <Typography variant="body2" fontWeight={600}>
                      {d.original_filename || d.filename || `Document ${d.id}`}
                    </Typography>
                    {d.source_type && (
                      <Chip label={d.source_type.replace(/_/g, ' ')} size="small" variant="outlined" />
                    )}
                    {d.status && d.status !== 'completed' && (
                      <Chip label={d.status} size="small" color="warning" variant="outlined" />
                    )}
                  </Box>
                }
                secondary={
                  <Typography variant="caption" color="text.secondary">
                    {(d.total_pages || 0)} pg · {(d.total_chunks || 0)} chunks
                    {d.description ? ` · ${d.description}` : ''}
                  </Typography>
                }
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
};

export default CompetitorDocumentsPanel;
