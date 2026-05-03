import React, { useEffect, useMemo, useRef, useState } from 'react';
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
  TextField,
  Divider,
  IconButton,
  Tooltip,
  ToggleButton,
  ToggleButtonGroup,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import {
  Send as SendIcon,
  Upload as UploadIcon,
  CheckCircle as ResolvedIcon,
  Block as WontIcon,
  Refresh as ReanalyzeIcon,
  AttachFile as AttachIcon,
  ExpandMore as ExpandMoreIcon,
  Person as UserIcon,
  SmartToy as AgentIcon,
  Settings as SystemIcon,
} from '@mui/icons-material';

const STATUS_COLORS = {
  open: 'default',
  in_progress: 'info',
  resolved: 'success',
  wont_address: 'warning',
};

export default function GapWorkspace() {
  const [gaps, setGaps] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState('open');
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef(null);
  const [uploadDesc, setUploadDesc] = useState('');

  const reload = () => {
    setLoading(true);
    Promise.all([
      axios.get('/api/gaps/', { params: statusFilter !== 'all' ? { status: statusFilter } : {} }),
      axios.get('/api/gaps/by-status-summary'),
    ])
      .then(([list, sum]) => { setGaps(list.data.gaps); setCounts(sum.data.counts); setError(null); })
      .catch((err) => setError(err?.response?.data?.detail || err.message))
      .finally(() => setLoading(false));
  };

  useEffect(reload, [statusFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadDetail = (gid) => {
    setSelectedId(gid);
    setDetail(null);
    if (!gid) return;
    axios.get(`/api/gaps/${gid}`).then((res) => setDetail(res.data));
  };

  const sendMessage = async () => {
    if (!draft.trim() || !selectedId) return;
    setBusy(true);
    try {
      await axios.post(`/api/gaps/${selectedId}/messages`, { content: draft.trim() });
      setDraft('');
      loadDetail(selectedId);
      reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
    }
  };

  const reanalyze = async () => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await axios.post(`/api/gaps/${selectedId}/reanalyze`);
      loadDetail(selectedId);
      reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
    }
  };

  const setStatus = async (status, summary) => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await axios.post(`/api/gaps/${selectedId}/resolve`, { status, summary });
      loadDetail(selectedId);
      reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !selectedId) return;
    setBusy(true);
    const fd = new FormData();
    fd.append('file', file);
    fd.append('description', uploadDesc || '');
    try {
      await axios.post(`/api/gaps/${selectedId}/upload`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadDesc('');
      loadDetail(selectedId);
      reload();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const groupedByTheme = useMemo(() => {
    const m = {};
    gaps.forEach((g) => {
      if (!m[g.theme_label]) m[g.theme_label] = [];
      m[g.theme_label].push(g);
    });
    return m;
  }, [gaps]);

  return (
    <Box sx={{ p: 2, height: 'calc(100vh - 120px)', display: 'flex', gap: 2, overflow: 'hidden' }}>
      {/* Left rail: gap list */}
      <Box sx={{ flex: '0 0 380px', display: 'flex', flexDirection: 'column' }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Typography variant="h6">Gap Workspace</Typography>
          <Box sx={{ flex: 1 }} />
          <Tooltip title={`open: ${counts.open || 0} · in_progress: ${counts.in_progress || 0} · resolved: ${counts.resolved || 0}`}>
            <Chip size="small" label={`${gaps.length} shown`} />
          </Tooltip>
        </Stack>

        <ToggleButtonGroup
          size="small"
          value={statusFilter}
          exclusive
          onChange={(_, v) => v && setStatusFilter(v)}
          sx={{ mb: 1 }}
        >
          <ToggleButton value="open">Open ({counts.open || 0})</ToggleButton>
          <ToggleButton value="in_progress">In progress ({counts.in_progress || 0})</ToggleButton>
          <ToggleButton value="resolved">Resolved ({counts.resolved || 0})</ToggleButton>
          <ToggleButton value="all">All</ToggleButton>
        </ToggleButtonGroup>

        <Box sx={{ flex: 1, overflowY: 'auto' }}>
          {loading ? (
            <Box sx={{ p: 2, textAlign: 'center' }}><CircularProgress size={20} /></Box>
          ) : error ? (
            <Alert severity="error">{error}</Alert>
          ) : Object.keys(groupedByTheme).length === 0 ? (
            <Alert severity="info" sx={{ mt: 1 }}>No gaps for this status.</Alert>
          ) : (
            Object.entries(groupedByTheme).map(([theme, list]) => (
              <Accordion key={theme} defaultExpanded sx={{ '&:before': { display: 'none' } }}>
                <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ minHeight: 36, py: 0 }}>
                  <Typography variant="caption" sx={{ fontWeight: 700, flex: 1 }}>
                    {theme}
                  </Typography>
                  <Chip size="small" label={list.length} />
                </AccordionSummary>
                <AccordionDetails sx={{ p: 0 }}>
                  {list.map((g) => (
                    <Box
                      key={g.id}
                      onClick={() => loadDetail(g.id)}
                      sx={{
                        p: 1.2, cursor: 'pointer',
                        borderBottom: '1px solid', borderColor: 'divider',
                        bgcolor: selectedId === g.id ? 'action.selected' : 'transparent',
                        '&:hover': { bgcolor: 'action.hover' },
                      }}
                    >
                      <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mb: 0.3 }}>
                        <Chip size="small" label={g.status} color={STATUS_COLORS[g.status]} />
                        {(g.rfp_req_codes || []).slice(0, 4).map((c) => (
                          <Chip key={c} size="small" label={c} variant="outlined" />
                        ))}
                        <Box sx={{ flex: 1 }} />
                        {g.msg_count ? <Chip size="small" label={`${g.msg_count}msg`} variant="outlined" /> : null}
                        {g.ev_count ? <Chip size="small" label={`${g.ev_count}ev`} variant="outlined" color="info" /> : null}
                      </Stack>
                      <Typography variant="caption" sx={{ display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {g.gap_text}
                      </Typography>
                    </Box>
                  ))}
                </AccordionDetails>
              </Accordion>
            ))
          )}
        </Box>
      </Box>

      <Divider orientation="vertical" flexItem />

      {/* Right pane: detail */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {!detail ? (
          <Box sx={{ p: 4, textAlign: 'center', color: 'text.secondary' }}>
            <Typography>Select a gap from the left rail to begin.</Typography>
          </Box>
        ) : (
          <>
            {/* Header */}
            <Paper sx={{ p: 2, mb: 1, borderLeft: '4px solid', borderColor: `${STATUS_COLORS[detail.status]}.main` }}>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1, flexWrap: 'wrap' }}>
                <Chip size="small" label={detail.status} color={STATUS_COLORS[detail.status]} />
                <Chip size="small" label={detail.theme_label} variant="outlined" />
                {(detail.rfp_req_codes || []).map((c) => (
                  <Chip key={c} size="small" label={c} variant="outlined" />
                ))}
                <Box sx={{ flex: 1 }} />
                <Tooltip title="Re-analyze the gap with all current evidence">
                  <span>
                    <Button size="small" startIcon={<ReanalyzeIcon />} disabled={busy} onClick={reanalyze}>
                      Re-analyze
                    </Button>
                  </span>
                </Tooltip>
                <Button size="small" color="success" variant="outlined" startIcon={<ResolvedIcon />} disabled={busy}
                        onClick={() => setStatus('resolved', null)}>
                  Mark resolved
                </Button>
                <Button size="small" color="warning" variant="outlined" startIcon={<WontIcon />} disabled={busy}
                        onClick={() => setStatus('wont_address', null)}>
                  Won't address
                </Button>
              </Stack>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                {detail.gap_text}
              </Typography>
              {detail.entry_title ? (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                  Catalog entry: <em>{detail.entry_title}</em>
                </Typography>
              ) : null}
            </Paper>

            {/* Conversation */}
            <Paper sx={{ p: 2, flex: 1, overflowY: 'auto', mb: 1 }}>
              {(detail.messages || []).length === 0 ? (
                <Typography variant="caption" color="text.secondary">
                  Start a conversation. The agent will respond grounded in the gap context, the catalog entry,
                  and any evidence you've uploaded. Or upload a document below to add it to the Parsons knowledge.
                </Typography>
              ) : (
                <Stack spacing={1.5}>
                  {detail.messages.map((m) => (
                    <Box key={m.id} sx={{ display: 'flex', gap: 1 }}>
                      <Box sx={{ pt: 0.5, color:
                        m.role === 'user' ? 'primary.main' :
                        m.role === 'agent' ? 'success.main' : 'text.disabled' }}>
                        {m.role === 'user' ? <UserIcon fontSize="small" /> :
                         m.role === 'agent' ? <AgentIcon fontSize="small" /> :
                         <SystemIcon fontSize="small" />}
                      </Box>
                      <Box sx={{ flex: 1 }}>
                        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.3 }}>
                          <Typography variant="caption" sx={{ fontWeight: 600 }}>
                            {m.role}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {m.created_at}
                          </Typography>
                          {m.attachment_filename ? (
                            <Chip size="small" icon={<AttachIcon />} label={m.attachment_filename} variant="outlined" />
                          ) : null}
                        </Stack>
                        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                          {m.content}
                        </Typography>
                      </Box>
                    </Box>
                  ))}
                </Stack>
              )}
            </Paper>

            {/* Evidence list */}
            {detail.evidence?.length ? (
              <Paper sx={{ p: 1.5, mb: 1, bgcolor: 'info.light' }}>
                <Typography variant="caption" sx={{ fontWeight: 600 }}>
                  Attached evidence ({detail.evidence.length})
                </Typography>
                <Stack spacing={0.3} sx={{ mt: 0.5 }}>
                  {detail.evidence.map((e) => (
                    <Typography key={e.id} variant="caption">
                      • {e.filename} {e.description ? `— ${e.description}` : ''}
                    </Typography>
                  ))}
                </Stack>
              </Paper>
            ) : null}

            {/* Compose + upload */}
            <Paper sx={{ p: 1.5 }}>
              <Stack direction="row" spacing={1} alignItems="flex-end">
                <TextField
                  fullWidth
                  multiline
                  minRows={2}
                  maxRows={6}
                  size="small"
                  placeholder="Tell the agent how Parsons satisfies this gap, or ask a clarifying question…"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  disabled={busy}
                />
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<SendIcon />}
                  disabled={busy || !draft.trim()}
                  onClick={sendMessage}
                >
                  Send
                </Button>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
                <TextField
                  size="small"
                  placeholder="Upload description (optional)"
                  value={uploadDesc}
                  onChange={(e) => setUploadDesc(e.target.value)}
                  sx={{ flex: 1 }}
                />
                <input ref={fileInputRef} type="file" hidden onChange={handleUpload} />
                <Button
                  size="small"
                  startIcon={<UploadIcon />}
                  variant="outlined"
                  disabled={busy}
                  onClick={() => fileInputRef.current?.click()}
                >
                  Upload evidence
                </Button>
              </Stack>
            </Paper>
          </>
        )}
      </Box>
    </Box>
  );
}
