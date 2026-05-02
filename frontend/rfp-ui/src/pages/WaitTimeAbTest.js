/**
 * WaitTimeAbTest
 *
 * A/B-test the OLD wait-time LD rule against the NEW T1628 LD rule
 * over a real month of station-hour observations imported from Excel.
 *
 * Sections (vertical):
 *   1. Imports — upload .xlsx, list/delete prior imports
 *   2. LD Rules — define the OLD and NEW rules (parameter editor, no code)
 *   3. Compare — pick (import, old, new), run, persist a WaitTimeAbRun
 *   4. Run history — past comparisons (delta totals at a glance)
 *   5. Result — full breakdown of the last/selected run:
 *        a) Top-line totals + delta
 *        b) Monthly rollup
 *        c) Per-station table sorted by delta
 *        d) Daily detail (collapsed by default)
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress,
  Dialog, DialogActions, DialogContent, DialogTitle, Divider,
  FormControl, FormControlLabel, Grid, IconButton, InputLabel,
  LinearProgress, MenuItem, Paper, Select, Stack, Switch, Tab, Table,
  TableBody, TableCell, TableContainer, TableHead, TablePagination,
  TableRow, Tabs, TextField, Tooltip, Typography,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  PlayArrow as RunIcon,
  Delete as DeleteIcon,
  Add as AddIcon,
  Refresh as RefreshIcon,
  Compare as CompareIcon,
  Edit as EditIcon,
  FileDownload as DownloadIcon,
} from '@mui/icons-material';
import { useProposal } from '../proposal/ProposalContext';

const RULE_MODES = [
  // NJ MVC contract-specific modes (the two we actually need for this A/B test)
  { value: 'nj_old_2011',     label: '🇺🇸 NJ OLD — 2011 Amendment (45-min, $2,500 + $2,500 per 10-min increment)' },
  { value: 'nj_new_t1628',    label: '🇺🇸 NJ NEW — T1628 O-30+O-31 ($1,000 first hr + $500 each additional hr; +$500/10-min band over 40)' },
  // Generic fallback modes
  { value: 'per_minute_over', label: 'Per minute over threshold ($/min)' },
  { value: 'per_hour_over',   label: 'Per breaching hour ($/breach hour)' },
  { value: 'per_day_if_any',  label: 'Per day if ANY breach (flat $/day)' },
  { value: 'tiered',          label: 'Tiered (bands of over-minutes)' },
];

function fmtUsd(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  return (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ── Rule editor dialog ────────────────────────────────────────────

function RuleDialog({ open, onClose, onSaved, initial, proposalId }) {
  const blank = useMemo(() => ({
    name: '', description: '', is_baseline: false,
    threshold_minutes: 15, mode: 'per_hour_over',
    dollars_per_unit: 100, daily_cap_usd: '', monthly_cap_usd: '',
    grace_period_minutes: 0, exclude_hours_csv: '',
    count_null_hours_as_breach: false,
    tiers: [{ min_over: 0, max_over: '', dollars: 0 }],
    notes: '',
    excluded_facilities_csv: '',
    monthly_grace_days: 0,
  }), []);
  const [form, setForm] = useState(blank);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (open) {
      setErr(null);
      if (initial) {
        setForm({
          ...blank,
          ...initial,
          daily_cap_usd: initial.daily_cap_usd ?? '',
          monthly_cap_usd: initial.monthly_cap_usd ?? '',
          excluded_facilities_csv: initial.excluded_facilities_csv ?? '',
          monthly_grace_days: initial.monthly_grace_days ?? 0,
          // tiers_json on the new NJ modes is a parameter dict, not an
          // array — only treat it as tier rows when it's actually an array.
          tiers: Array.isArray(initial.tiers_json) && initial.tiers_json.length
            ? initial.tiers_json.map(t => ({
                min_over: t.min_over ?? 0,
                max_over: t.max_over ?? '',
                dollars: t.dollars ?? 0,
              }))
            : blank.tiers,
        });
      } else {
        setForm(blank);
      }
    }
  }, [open, initial, blank]);

  const submit = async () => {
    setSaving(true); setErr(null);
    try {
      const payload = {
        proposal_id: proposalId || null,
        name: form.name.trim(),
        description: form.description || null,
        is_baseline: !!form.is_baseline,
        threshold_minutes: Number(form.threshold_minutes),
        mode: form.mode,
        dollars_per_unit: Number(form.dollars_per_unit),
        daily_cap_usd: form.daily_cap_usd === '' ? null : Number(form.daily_cap_usd),
        monthly_cap_usd: form.monthly_cap_usd === '' ? null : Number(form.monthly_cap_usd),
        grace_period_minutes: Number(form.grace_period_minutes),
        exclude_hours_csv: form.exclude_hours_csv || null,
        count_null_hours_as_breach: !!form.count_null_hours_as_breach,
        tiers_json: form.mode === 'tiered'
          ? form.tiers.map(t => ({
              min_over: Number(t.min_over),
              max_over: t.max_over === '' ? null : Number(t.max_over),
              dollars: Number(t.dollars),
            }))
          : null,
        notes: form.notes || null,
        excluded_facilities_csv: form.excluded_facilities_csv || null,
        monthly_grace_days: Number(form.monthly_grace_days) || 0,
      };
      if (initial?.id) {
        await axios.put(`/api/wait-time-ab/rules/${initial.id}`, payload);
      } else {
        await axios.post('/api/wait-time-ab/rules', payload);
      }
      onSaved && onSaved();
      onClose();
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const updateTier = (idx, key, val) => {
    setForm(f => {
      const tiers = f.tiers.slice();
      tiers[idx] = { ...tiers[idx], [key]: val };
      return { ...f, tiers };
    });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{initial?.id ? 'Edit LD rule' : 'New LD rule'}</DialogTitle>
      <DialogContent dividers>
        {err && <Alert severity="error" sx={{ mb: 2 }}>{err}</Alert>}
        <Grid container spacing={2}>
          <Grid item xs={12} md={8}>
            <TextField fullWidth size="small" label="Rule name *" required
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="e.g. Old contract — wait > 30 min, $50/min"
            />
          </Grid>
          <Grid item xs={12} md={4}>
            <FormControlLabel
              control={<Switch checked={!!form.is_baseline}
                onChange={e => setForm(f => ({ ...f, is_baseline: e.target.checked }))} />}
              label="Baseline (= 'Old' rule)"
            />
          </Grid>
          <Grid item xs={12}>
            <TextField fullWidth size="small" label="Description"
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <FormControl fullWidth size="small">
              <InputLabel>LD mode *</InputLabel>
              <Select label="LD mode *" value={form.mode}
                onChange={e => setForm(f => ({ ...f, mode: e.target.value }))}>
                {RULE_MODES.map(m => (
                  <MenuItem key={m.value} value={m.value}>{m.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={6} md={3}>
            <TextField fullWidth size="small" type="number" label="Threshold (min) *"
              value={form.threshold_minutes}
              onChange={e => setForm(f => ({ ...f, threshold_minutes: e.target.value }))}
              inputProps={{ step: 0.5, min: 0 }}
            />
          </Grid>
          <Grid item xs={6} md={3}>
            <TextField fullWidth size="small" type="number" label="Grace period (min)"
              value={form.grace_period_minutes}
              onChange={e => setForm(f => ({ ...f, grace_period_minutes: e.target.value }))}
              inputProps={{ step: 0.5, min: 0 }}
            />
          </Grid>
          {form.mode !== 'tiered' && (
            <Grid item xs={12} md={6}>
              <TextField fullWidth size="small" type="number" label="Dollar amount per unit"
                helperText={
                  form.mode === 'per_minute_over' ? '$ per minute over threshold' :
                  form.mode === 'per_day_if_any'  ? 'flat $ per day with any breach' :
                                                    '$ per breaching hour'
                }
                value={form.dollars_per_unit}
                onChange={e => setForm(f => ({ ...f, dollars_per_unit: e.target.value }))}
                inputProps={{ step: 1, min: 0 }}
              />
            </Grid>
          )}
          <Grid item xs={6} md={3}>
            <TextField fullWidth size="small" type="number" label="Daily cap (USD)"
              value={form.daily_cap_usd}
              onChange={e => setForm(f => ({ ...f, daily_cap_usd: e.target.value }))}
              helperText="Blank = no cap"
              inputProps={{ step: 1, min: 0 }}
            />
          </Grid>
          <Grid item xs={6} md={3}>
            <TextField fullWidth size="small" type="number" label="Monthly cap (USD/station)"
              value={form.monthly_cap_usd}
              onChange={e => setForm(f => ({ ...f, monthly_cap_usd: e.target.value }))}
              helperText="Blank = no cap"
              inputProps={{ step: 1, min: 0 }}
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <TextField fullWidth size="small" label="Exclude hours (CSV of H labels)"
              value={form.exclude_hours_csv}
              onChange={e => setForm(f => ({ ...f, exclude_hours_csv: e.target.value }))}
              placeholder="e.g. H06,H19"
              helperText="Hours that are never penalized (early/late edge cases)"
            />
          </Grid>
          <Grid item xs={12} md={8}>
            <TextField fullWidth size="small"
              label="Excluded facilities (CSV of names or station IDs)"
              value={form.excluded_facilities_csv}
              onChange={e => setForm(f => ({ ...f, excluded_facilities_csv: e.target.value }))}
              placeholder="e.g. Cape May,Millville,Salem,Washington"
              helperText="Facilities exempt from this rule (per T1628 § 4.12, 4 CIFs are exempt)"
            />
          </Grid>
          <Grid item xs={12} md={4}>
            <TextField fullWidth size="small" type="number"
              label="Monthly grace days (nj_old_2011 only)"
              value={form.monthly_grace_days}
              onChange={e => setForm(f => ({ ...f, monthly_grace_days: e.target.value }))}
              helperText="2011 amendment: 4 (LDs apply on the 5th+ breaching day)"
              inputProps={{ step: 1, min: 0, max: 31 }}
            />
          </Grid>
          {form.mode === 'tiered' && (
            <Grid item xs={12}>
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Tiers — penalty by amount of minutes over threshold
                </Typography>
                {form.tiers.map((t, i) => (
                  <Stack key={i} direction="row" spacing={1} sx={{ mb: 1 }}>
                    <TextField size="small" type="number" label="Min over"
                      value={t.min_over}
                      onChange={e => updateTier(i, 'min_over', e.target.value)} />
                    <TextField size="small" type="number" label="Max over (blank = ∞)"
                      value={t.max_over}
                      onChange={e => updateTier(i, 'max_over', e.target.value)} />
                    <TextField size="small" type="number" label="Penalty $"
                      value={t.dollars}
                      onChange={e => updateTier(i, 'dollars', e.target.value)} />
                    <IconButton size="small" color="error"
                      onClick={() => setForm(f => ({
                        ...f, tiers: f.tiers.filter((_, j) => j !== i),
                      }))}><DeleteIcon /></IconButton>
                  </Stack>
                ))}
                <Button startIcon={<AddIcon />} size="small"
                  onClick={() => setForm(f => ({
                    ...f, tiers: [...f.tiers, { min_over: 0, max_over: '', dollars: 0 }],
                  }))}>
                  Add tier
                </Button>
              </Paper>
            </Grid>
          )}
          <Grid item xs={12}>
            <TextField fullWidth multiline minRows={2} size="small" label="Notes"
              value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit}
                disabled={saving || !form.name.trim()}>
          {saving ? 'Saving…' : 'Save rule'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}


// ── Main page ─────────────────────────────────────────────────────

export default function WaitTimeAbTest() {
  const { proposalId } = useProposal();
  const [tab, setTab] = useState(0);
  const [imports, setImports] = useState([]);
  const [rules, setRules] = useState([]);
  const [runs, setRuns] = useState([]);
  const [activeRun, setActiveRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [info, setInfo] = useState(null);

  // Compare form state
  const [cmpName, setCmpName] = useState('');
  const [cmpImportId, setCmpImportId] = useState('');
  const [cmpOldId, setCmpOldId] = useState('');
  const [cmpNewId, setCmpNewId] = useState('');

  // Upload form
  const [uploadName, setUploadName] = useState('');
  const [uploadFile, setUploadFile] = useState(null);
  const [metricFilter, setMetricFilter] = useState('Facility Average Wait Time');
  // Upload state: 0..100 = upload bytes %, then 'parsing' while server is
  // ingesting the workbook (which can take seconds for big files).
  // null = idle. Visible to the user as a progress bar + label.
  const [uploadProgress, setUploadProgress] = useState(null);
  const [uploadStage, setUploadStage] = useState(null); // 'uploading' | 'parsing' | null

  // Rule editor
  const [ruleDialogOpen, setRuleDialogOpen] = useState(false);
  const [editingRule, setEditingRule] = useState(null);

  // Daily-detail pagination
  const [dailyPage, setDailyPage] = useState(0);
  const [dailyPerPage, setDailyPerPage] = useState(25);
  useEffect(() => { setDailyPage(0); }, [activeRun?.id]);

  const refresh = useCallback(async () => {
    setBusy(true); setErr(null);
    try {
      const [imp, ru, rn] = await Promise.all([
        axios.get('/api/wait-time-ab/imports', { params: { proposal_id: proposalId || undefined } }),
        axios.get('/api/wait-time-ab/rules',   { params: { proposal_id: proposalId || undefined } }),
        axios.get('/api/wait-time-ab/runs',    { params: { proposal_id: proposalId || undefined } }),
      ]);
      setImports(imp.data?.imports || []);
      setRules(ru.data?.rules || []);
      setRuns(rn.data?.runs || []);
    } catch (e) {
      setErr('Failed to load: ' + (e.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  }, [proposalId]);

  useEffect(() => { refresh(); }, [refresh]);

  // ── Upload ──
  const upload = async () => {
    if (!uploadFile || !uploadName.trim()) {
      setErr('Pick a file and enter a name first.');
      return;
    }
    const fd = new FormData();
    fd.append('file', uploadFile);
    fd.append('name', uploadName.trim());
    if (proposalId) fd.append('proposal_id', String(proposalId));
    if (metricFilter) fd.append('metric_name_filter', metricFilter);
    setBusy(true); setErr(null);
    setUploadStage('uploading'); setUploadProgress(0);
    try {
      const r = await axios.post('/api/wait-time-ab/imports', fd, {
        // 5-minute timeout so a hung server doesn't lock the user forever.
        timeout: 5 * 60 * 1000,
        onUploadProgress: (evt) => {
          if (evt.total) {
            const pct = Math.round((evt.loaded / evt.total) * 100);
            setUploadProgress(pct);
            // Once the bytes are all sent, the server is now parsing the
            // workbook. Surface that explicitly — for big files this can
            // be 5-30 seconds and the user should know the system is
            // still working, not hung.
            if (pct >= 100) setUploadStage('parsing');
          }
        },
      });
      setInfo(`Imported ${r.data.row_count} rows across ${r.data.station_count} stations.`);
      if (Array.isArray(r.data.warnings) && r.data.warnings.length) {
        // Keep warnings visible alongside the success message.
        setErr('Import warnings: ' + r.data.warnings.join(' · '));
      }
      setUploadFile(null); setUploadName('');
      // Reset native file input
      const inp = document.getElementById('wait-time-file-input');
      if (inp) inp.value = '';
      await refresh();
    } catch (e) {
      setErr('Upload failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
      setUploadStage(null); setUploadProgress(null);
    }
  };

  const deleteImport = async (id) => {
    if (!window.confirm('Delete this import? Any A/B runs that referenced it will also be deleted.')) return;
    try {
      await axios.delete(`/api/wait-time-ab/imports/${id}`);
      await refresh();
    } catch (e) {
      setErr('Delete failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const deleteRule = async (id) => {
    if (!window.confirm('Delete this rule?')) return;
    try {
      await axios.delete(`/api/wait-time-ab/rules/${id}`);
      await refresh();
    } catch (e) {
      setErr(e.response?.data?.detail || e.message);
    }
  };

  // ── Compare ──
  const runCompare = async () => {
    if (!cmpName.trim() || !cmpImportId || !cmpOldId || !cmpNewId) {
      setErr('Pick an import, two rules, and a name first.');
      return;
    }
    if (cmpOldId === cmpNewId) {
      setErr('Old and New rules must differ.');
      return;
    }
    setBusy(true); setErr(null);
    try {
      const r = await axios.post('/api/wait-time-ab/compare', {
        name: cmpName.trim(),
        import_id: Number(cmpImportId),
        old_rule_id: Number(cmpOldId),
        new_rule_id: Number(cmpNewId),
        proposal_id: proposalId || null,
      });
      setActiveRun(r.data);
      setTab(2); // jump to Result
      setCmpName('');
      await refresh();
    } catch (e) {
      setErr('Comparison failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setBusy(false);
    }
  };

  const openRun = async (id) => {
    try {
      const r = await axios.get(`/api/wait-time-ab/runs/${id}`);
      setActiveRun(r.data);
      setTab(2);
    } catch (e) {
      setErr('Load failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const deleteRun = async (id) => {
    if (!window.confirm('Delete this run?')) return;
    try {
      await axios.delete(`/api/wait-time-ab/runs/${id}`);
      if (activeRun?.id === id) setActiveRun(null);
      await refresh();
    } catch (e) {
      setErr(e.response?.data?.detail || e.message);
    }
  };

  // Download a polished .xlsx for a given run id. Uses a blob URL so we
  // get the filename from the Content-Disposition header.
  const downloadRunXlsx = async (id, runName) => {
    try {
      const r = await axios.get(`/api/wait-time-ab/runs/${id}/export.xlsx`, {
        responseType: 'blob',
      });
      const blob = new Blob([r.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      // Pull filename from Content-Disposition if present, else build one
      const cd = r.headers['content-disposition'] || '';
      const m = /filename="?([^"]+)"?/i.exec(cd);
      const safe = (runName || `run_${id}`).replace(/[^a-z0-9_-]/gi, '_').slice(0, 60);
      a.download = m ? m[1] : `wait_time_ab_${safe}_run${id}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr('Excel export failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  // ── Render ──
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h4" fontWeight={800}>Wait-Time LD A/B Test</Typography>
          <Typography variant="body2" color="text.secondary">
            Quantify the cost differential between the OLD wait-time LD calculation
            and the NEW one against a real month of station-hour observations.
            Closed hours (blank cells in the upload) are NEVER penalized.
          </Typography>
        </Box>
        <Tooltip title="Reload">
          <IconButton onClick={refresh}><RefreshIcon /></IconButton>
        </Tooltip>
      </Stack>

      {err && <Alert severity="error" onClose={() => setErr(null)} sx={{ mb: 2 }}>{err}</Alert>}
      {info && <Alert severity="success" onClose={() => setInfo(null)} sx={{ mb: 2 }}>{info}</Alert>}
      {busy && <LinearProgress sx={{ mb: 2 }} />}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label={`Setup (${imports.length} imports · ${rules.length} rules)`} />
        <Tab label={`Run history (${runs.length})`} />
        <Tab label={`Result${activeRun ? ` — ${activeRun.name}` : ''}`}
             disabled={!activeRun} />
      </Tabs>

      {/* ── Tab 0: Setup ──────────────────────────────────────── */}
      {tab === 0 && (
        <Stack spacing={2}>
          {/* Imports */}
          <Card variant="outlined">
            <CardContent>
              <Typography variant="h6" sx={{ mb: 1 }}>1. Imports</Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
                Upload a wait-time .xlsx with these columns: STATION_ID, STATION_NAME,
                TEST_DATE, DAY_OF_MONTH, METRIC_NAME, H06…H19. Blank H-cells = closed hours.
              </Typography>
              <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ mb: 2 }}>
                <TextField size="small" label="Import name *"
                  value={uploadName}
                  onChange={e => setUploadName(e.target.value)}
                  sx={{ flex: 1, minWidth: 200 }} />
                <TextField size="small" label="Metric filter"
                  value={metricFilter}
                  onChange={e => setMetricFilter(e.target.value)}
                  sx={{ minWidth: 240 }} />
                <Button component="label" variant="outlined" startIcon={<UploadIcon />}
                        disabled={uploadStage !== null}>
                  {uploadFile ? uploadFile.name : 'Choose .xlsx'}
                  <input id="wait-time-file-input" hidden type="file"
                    accept=".xlsx,.xlsm"
                    onChange={e => setUploadFile(e.target.files?.[0] || null)} />
                </Button>
                <Tooltip title={
                  uploadStage !== null ? '' :
                  !uploadFile ? 'Pick an .xlsx file first' :
                  !uploadName.trim() ? 'Enter an Import name first (the leftmost field)' :
                  busy ? 'Other request in flight…' : ''
                }>
                  <span>
                    <Button variant="contained" onClick={upload}
                            startIcon={uploadStage !== null
                              ? <CircularProgress size={16} color="inherit" />
                              : <UploadIcon />}
                            // Note: 'busy' alone should NOT block the upload
                            // button — refresh() sets busy=true and that
                            // shouldn't lock out the upload.
                            disabled={uploadStage !== null || !uploadFile || !uploadName.trim()}>
                      {uploadStage === 'uploading'
                        ? `Uploading… ${uploadProgress != null ? uploadProgress + '%' : ''}`
                        : uploadStage === 'parsing'
                        ? 'Parsing workbook…'
                        : 'Upload & ingest'}
                    </Button>
                  </span>
                </Tooltip>
              </Stack>
              {/* Inline hint: tell the user exactly what's missing instead
                  of leaving them to guess why the button is grey. */}
              {uploadStage === null && (!uploadFile || !uploadName.trim()) && (
                <Typography variant="caption" color="warning.main"
                            sx={{ display: 'block', mb: 1 }}>
                  {!uploadName.trim() && !uploadFile
                    ? '↑ Enter an Import name AND pick an .xlsx file before clicking Upload & ingest.'
                    : !uploadName.trim()
                    ? '↑ Enter an Import name (leftmost field) — required.'
                    : '↑ Pick an .xlsx file before clicking Upload & ingest.'}
                </Typography>
              )}
              {uploadStage !== null && (
                <Box sx={{ mb: 2 }}>
                  <LinearProgress
                    variant={uploadStage === 'parsing' ? 'indeterminate' : 'determinate'}
                    value={uploadProgress || 0}
                  />
                  <Typography variant="caption" color="text.secondary"
                              sx={{ display: 'block', mt: 0.5 }}>
                    {uploadStage === 'uploading'
                      ? `Sending file to server… ${uploadProgress != null ? uploadProgress + '%' : ''}`
                      : 'Server is parsing the workbook (this can take 5–30 seconds for large files)…'}
                  </Typography>
                </Box>
              )}
              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell>File</TableCell>
                      <TableCell align="right">Stations</TableCell>
                      <TableCell align="right">Rows</TableCell>
                      <TableCell>Date range</TableCell>
                      <TableCell>Uploaded</TableCell>
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {imports.length === 0 && (
                      <TableRow><TableCell colSpan={7} align="center">
                        <Typography variant="body2" color="text.secondary">
                          No imports yet.
                        </Typography>
                      </TableCell></TableRow>
                    )}
                    {imports.map(i => (
                      <TableRow key={i.id} hover>
                        <TableCell>{i.name}</TableCell>
                        <TableCell>
                          <Typography variant="caption">{i.source_filename}</Typography>
                        </TableCell>
                        <TableCell align="right">{i.station_count}</TableCell>
                        <TableCell align="right">{i.row_count.toLocaleString()}</TableCell>
                        <TableCell>{i.date_min} → {i.date_max}</TableCell>
                        <TableCell>
                          <Typography variant="caption">
                            {i.created_at && new Date(i.created_at).toLocaleString()}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <IconButton size="small" color="error"
                            onClick={() => deleteImport(i.id)}><DeleteIcon /></IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </CardContent>
          </Card>

          {/* Rules */}
          <Card variant="outlined">
            <CardContent>
              <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
                <Typography variant="h6" sx={{ flex: 1 }}>2. LD rules</Typography>
                <Button startIcon={<AddIcon />} variant="contained" size="small"
                  onClick={() => { setEditingRule(null); setRuleDialogOpen(true); }}>
                  New rule
                </Button>
              </Stack>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
                Define the OLD contract rule and the NEW T1628 rule. Mark one as
                "Baseline" to flag it as the OLD side. The compare step lets you
                pick any two rules.
              </Typography>
              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell>Mode</TableCell>
                      <TableCell align="right">Threshold</TableCell>
                      <TableCell align="right">$ per unit</TableCell>
                      <TableCell align="right">Daily cap</TableCell>
                      <TableCell align="right">Monthly cap</TableCell>
                      <TableCell>Baseline</TableCell>
                      <TableCell align="right">Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {rules.length === 0 && (
                      <TableRow><TableCell colSpan={8} align="center">
                        <Typography variant="body2" color="text.secondary">
                          No rules yet — add the OLD and the NEW rule.
                        </Typography>
                      </TableCell></TableRow>
                    )}
                    {rules.map(r => (
                      <TableRow key={r.id} hover>
                        <TableCell>{r.name}</TableCell>
                        <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{r.mode}</Typography></TableCell>
                        <TableCell align="right">{r.threshold_minutes} min{r.grace_period_minutes ? ` (+${r.grace_period_minutes} grace)` : ''}</TableCell>
                        <TableCell align="right">{r.mode === 'tiered' ? '—' : fmtUsd(r.dollars_per_unit)}</TableCell>
                        <TableCell align="right">{r.daily_cap_usd ? fmtUsd(r.daily_cap_usd) : '—'}</TableCell>
                        <TableCell align="right">{r.monthly_cap_usd ? fmtUsd(r.monthly_cap_usd) : '—'}</TableCell>
                        <TableCell>{r.is_baseline ? <Chip size="small" color="warning" label="OLD" /> : ''}</TableCell>
                        <TableCell align="right">
                          <IconButton size="small"
                            onClick={() => { setEditingRule(r); setRuleDialogOpen(true); }}><EditIcon /></IconButton>
                          <IconButton size="small" color="error"
                            onClick={() => deleteRule(r.id)}><DeleteIcon /></IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </CardContent>
          </Card>

          {/* Run a comparison */}
          <Card variant="outlined">
            <CardContent>
              <Typography variant="h6" sx={{ mb: 1 }}>3. Run an A/B comparison</Typography>
              <Grid container spacing={1} alignItems="center">
                <Grid item xs={12} md={3}>
                  <TextField fullWidth size="small" label="Run name *"
                    value={cmpName}
                    onChange={e => setCmpName(e.target.value)}
                    placeholder="e.g. March 2026 — Old vs New" />
                </Grid>
                <Grid item xs={12} md={3}>
                  <FormControl fullWidth size="small">
                    <InputLabel>Import *</InputLabel>
                    <Select label="Import *" value={cmpImportId}
                      onChange={e => setCmpImportId(e.target.value)}>
                      {imports.map(i => (
                        <MenuItem key={i.id} value={i.id}>
                          {i.name} ({i.row_count} rows)
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} md={3}>
                  <FormControl fullWidth size="small">
                    <InputLabel>OLD rule *</InputLabel>
                    <Select label="OLD rule *" value={cmpOldId}
                      onChange={e => setCmpOldId(e.target.value)}>
                      {rules.map(r => (
                        <MenuItem key={r.id} value={r.id}>
                          {r.name}{r.is_baseline ? ' ✓' : ''}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} md={3}>
                  <FormControl fullWidth size="small">
                    <InputLabel>NEW rule *</InputLabel>
                    <Select label="NEW rule *" value={cmpNewId}
                      onChange={e => setCmpNewId(e.target.value)}>
                      {rules.map(r => (
                        <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12}>
                  <Button variant="contained" startIcon={<RunIcon />}
                          onClick={runCompare}
                          disabled={busy || !cmpName.trim() || !cmpImportId || !cmpOldId || !cmpNewId}>
                    Run comparison
                  </Button>
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Stack>
      )}

      {/* ── Tab 1: Run history ──────────────────────────────── */}
      {tab === 1 && (
        <Card variant="outlined">
          <CardContent>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell align="right">Old total</TableCell>
                    <TableCell align="right">New total</TableCell>
                    <TableCell align="right">Δ (new − old)</TableCell>
                    <TableCell align="right">Breaches old → new</TableCell>
                    <TableCell>Created</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {runs.length === 0 && (
                    <TableRow><TableCell colSpan={7} align="center">
                      <Typography variant="body2" color="text.secondary">No runs yet.</Typography>
                    </TableCell></TableRow>
                  )}
                  {runs.map(r => (
                    <TableRow key={r.id} hover sx={{ cursor: 'pointer' }}
                              onClick={() => openRun(r.id)}>
                      <TableCell><strong>{r.name}</strong></TableCell>
                      <TableCell align="right">{fmtUsd(r.old_total_usd)}</TableCell>
                      <TableCell align="right">{fmtUsd(r.new_total_usd)}</TableCell>
                      <TableCell align="right" sx={{
                        color: r.delta_usd > 0 ? 'error.main'
                             : r.delta_usd < 0 ? 'success.main'
                             : 'text.primary',
                        fontWeight: 700,
                      }}>
                        {fmtUsd(r.delta_usd)}
                      </TableCell>
                      <TableCell align="right">{r.breaches_old} → {r.breaches_new}</TableCell>
                      <TableCell>
                        <Typography variant="caption">
                          {r.created_at && new Date(r.created_at).toLocaleString()}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Button size="small" startIcon={<CompareIcon />}
                          onClick={(e) => { e.stopPropagation(); openRun(r.id); }}>
                          Open
                        </Button>
                        <Tooltip title="Download a polished Excel workbook">
                          <IconButton size="small" color="primary"
                            onClick={(e) => { e.stopPropagation(); downloadRunXlsx(r.id, r.name); }}>
                            <DownloadIcon />
                          </IconButton>
                        </Tooltip>
                        <IconButton size="small" color="error"
                          onClick={(e) => { e.stopPropagation(); deleteRun(r.id); }}>
                          <DeleteIcon />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>
      )}

      {/* ── Tab 2: Result ───────────────────────────────────── */}
      {tab === 2 && activeRun && (
        <>
          <Stack direction="row" justifyContent="flex-end" sx={{ mb: 2 }}>
            <Button variant="contained" color="primary"
                    startIcon={<DownloadIcon />}
                    onClick={() => downloadRunXlsx(activeRun.id, activeRun.name)}>
              Download Excel report
            </Button>
          </Stack>
          <RunResult run={activeRun}
                     page={dailyPage} setPage={setDailyPage}
                     perPage={dailyPerPage} setPerPage={setDailyPerPage} />
        </>
      )}

      <RuleDialog open={ruleDialogOpen}
                  onClose={() => setRuleDialogOpen(false)}
                  onSaved={refresh}
                  initial={editingRule}
                  proposalId={proposalId} />
    </Box>
  );
}


// ── Result tab body ───────────────────────────────────────────────

function RunResult({ run, page, setPage, perPage, setPerPage }) {
  const breakdown = run.breakdown || {};
  const totals = breakdown.totals || run;
  const monthly = breakdown.monthly || [];
  const perStation = breakdown.per_station || [];
  const daily = breakdown.daily || [];

  const dailyPage = useMemo(() => {
    const start = page * perPage;
    return daily.slice(start, start + perPage);
  }, [daily, page, perPage]);

  const deltaColor = totals.delta_usd > 0 ? 'error.main'
    : totals.delta_usd < 0 ? 'success.main'
    : 'text.primary';

  return (
    <Stack spacing={2}>
      {/* Top-line */}
      <Card variant="outlined">
        <CardContent>
          <Grid container spacing={2}>
            <Grid item xs={6} md={3}>
              <Typography variant="overline" color="text.secondary">OLD total</Typography>
              <Typography variant="h5">{fmtUsd(totals.old_total_usd)}</Typography>
              <Typography variant="caption" color="text.secondary">
                {totals.breaches_old?.toLocaleString()} breach(es)
              </Typography>
            </Grid>
            <Grid item xs={6} md={3}>
              <Typography variant="overline" color="text.secondary">NEW total</Typography>
              <Typography variant="h5">{fmtUsd(totals.new_total_usd)}</Typography>
              <Typography variant="caption" color="text.secondary">
                {totals.breaches_new?.toLocaleString()} breach(es)
              </Typography>
            </Grid>
            <Grid item xs={12} md={3}>
              <Typography variant="overline" color="text.secondary">Δ (new − old)</Typography>
              <Typography variant="h5" sx={{ color: deltaColor, fontWeight: 800 }}>
                {fmtUsd(totals.delta_usd)}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {totals.delta_usd > 0
                  ? 'NEW is MORE expensive than old'
                  : totals.delta_usd < 0
                  ? 'NEW is CHEAPER than old'
                  : 'No difference'}
              </Typography>
            </Grid>
            <Grid item xs={12} md={3}>
              <Typography variant="overline" color="text.secondary">Scope</Typography>
              <Typography variant="body2">
                {totals.stations?.toLocaleString()} station(s) ·{' '}
                {totals.days?.toLocaleString()} station-days
              </Typography>
            </Grid>
          </Grid>
          {Array.isArray(run.warnings) && run.warnings.length > 0 && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              {run.warnings.join(' · ')}
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* Monthly */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1 }}>Monthly rollup</Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead><TableRow>
                <TableCell>Month</TableCell>
                <TableCell align="right">OLD</TableCell>
                <TableCell align="right">NEW</TableCell>
                <TableCell align="right">Δ</TableCell>
                <TableCell align="right">Breaches old → new</TableCell>
              </TableRow></TableHead>
              <TableBody>
                {monthly.map(m => (
                  <TableRow key={m.month}>
                    <TableCell>{m.month}</TableCell>
                    <TableCell align="right">{fmtUsd(m.old_total_usd)}</TableCell>
                    <TableCell align="right">{fmtUsd(m.new_total_usd)}</TableCell>
                    <TableCell align="right" sx={{ color: m.delta_usd > 0 ? 'error.main' : m.delta_usd < 0 ? 'success.main' : 'text.primary', fontWeight: 600 }}>
                      {fmtUsd(m.delta_usd)}
                    </TableCell>
                    <TableCell align="right">{m.breaches_old} → {m.breaches_new}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* Per-station */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1 }}>Per-station — sorted by Δ (worst delta first)</Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead><TableRow>
                <TableCell>Station</TableCell>
                <TableCell align="right">Days</TableCell>
                <TableCell align="right">OLD total</TableCell>
                <TableCell align="right">NEW total</TableCell>
                <TableCell align="right">Δ</TableCell>
                <TableCell align="right">Breaches</TableCell>
              </TableRow></TableHead>
              <TableBody>
                {perStation.map(s => (
                  <TableRow key={s.station_id}>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{s.station_id}</Typography>
                      <Typography variant="caption" color="text.secondary">{s.station_name}</Typography>
                    </TableCell>
                    <TableCell align="right">{s.days}</TableCell>
                    <TableCell align="right">{fmtUsd(s.old_total)}</TableCell>
                    <TableCell align="right">{fmtUsd(s.new_total)}</TableCell>
                    <TableCell align="right" sx={{ color: s.delta_total > 0 ? 'error.main' : s.delta_total < 0 ? 'success.main' : 'text.primary', fontWeight: 600 }}>
                      {fmtUsd(s.delta_total)}
                    </TableCell>
                    <TableCell align="right">{s.old_breaches} → {s.new_breaches}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* Daily */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1 }}>Daily detail ({daily.length.toLocaleString()} rows)</Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead><TableRow>
                <TableCell>Station</TableCell>
                <TableCell>Date</TableCell>
                <TableCell align="right">OLD</TableCell>
                <TableCell align="right">NEW</TableCell>
                <TableCell align="right">Δ</TableCell>
                <TableCell>OLD breach hours</TableCell>
                <TableCell>NEW breach hours</TableCell>
              </TableRow></TableHead>
              <TableBody>
                {dailyPage.map((d, i) => (
                  <TableRow key={i}>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{d.station_id}</Typography>
                      <Typography variant="caption" color="text.secondary">{d.station_name}</Typography>
                    </TableCell>
                    <TableCell>{d.test_date}</TableCell>
                    <TableCell align="right">{fmtUsd(d.old_penalty_usd)}</TableCell>
                    <TableCell align="right">{fmtUsd(d.new_penalty_usd)}</TableCell>
                    <TableCell align="right" sx={{ color: d.delta_usd > 0 ? 'error.main' : d.delta_usd < 0 ? 'success.main' : 'text.primary' }}>
                      {fmtUsd(d.delta_usd)}
                    </TableCell>
                    <TableCell>
                      {(d.old_breach_hours || []).join(', ') || <Typography variant="caption" color="text.secondary">—</Typography>}
                    </TableCell>
                    <TableCell>
                      {(d.new_breach_hours || []).join(', ') || <Typography variant="caption" color="text.secondary">—</Typography>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <TablePagination
            component="div"
            count={daily.length}
            page={page}
            onPageChange={(_, p) => setPage(p)}
            rowsPerPage={perPage}
            onRowsPerPageChange={(e) => { setPerPage(parseInt(e.target.value, 10)); setPage(0); }}
            rowsPerPageOptions={[10, 25, 50, 100, 250]}
          />
        </CardContent>
      </Card>
    </Stack>
  );
}
