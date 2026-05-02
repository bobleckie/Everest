import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Button, Tabs, Tab, TextField,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Select, MenuItem, FormControl, InputLabel, Switch, IconButton, Tooltip,
  LinearProgress, Alert, Chip, Accordion, AccordionSummary,
  AccordionDetails, Stack, Dialog, DialogTitle, DialogContent, DialogActions,
  RadioGroup, Radio, FormControlLabel, FormLabel, Checkbox, Divider, FormGroup,
} from '@mui/material';
import {
  Add as AddIcon, Delete as DeleteIcon, Calculate as CalculateIcon,
  Save as SaveIcon, ExpandMore as ExpandMoreIcon,
  Insights as InsightsIcon, HistoryToggleOff as HistoryIcon,
  People as PeopleIcon, Edit as EditIcon,
} from '@mui/icons-material';
import axios from 'axios';

// ── Tab definitions ─────────────────────────────────────────────────
const TABS = [
  { code: 'assumptions',   label: 'Assumptions'      },  { code: 'staffing',      label: 'Staffing Plan'    },  { code: 'labor',         label: 'Labor'            },
  { code: 'odcs',          label: 'ODCs'             },
  { code: 'capital',       label: 'Capital'          },
  { code: 'hourly_rates',  label: 'Hourly Rates'     },
  { code: 'submittal',     label: 'Price Submittal'  },
  { code: 'cashflow',      label: 'Cash Flow'        },
  { code: 'comparison',    label: 'Comparison'       },
];

const ALLOCATION_OPTIONS = [
  { value: 'CIF',            label: 'CIF'           },
  { value: 'PIF',            label: 'PIF'           },
  { value: 'SHARED',         label: 'Shared'        },
  { value: 'CAPITAL_AMORT',  label: 'Capital (amortized)' },
];

const fmtCurrency = (v) => {
  if (v == null || isNaN(v)) return '$0.00';
  const n = Number(v);
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
};
const fmtPPT = (v) => (v == null || isNaN(v)) ? '—' : `$${Number(v).toFixed(4)}`;
const fmtPct = (v) => (v == null || isNaN(v)) ? '—' : `${Number(v).toFixed(1)}%`;

// ── Tab metadata: what the system does with each cost type ─────────
const TAB_META = {
  assumptions: {
    label: 'Assumptions',
    color: '#666',
    description: 'Reference data and contract parameters. Items here are NOT included in cost totals — they document constraints and assumptions (volumes, rates, term, etc.).',
    units: ['each', 'annual', 'monthly'],
    computeNote: 'Items in Assumptions are EXCLUDED from all financial totals.',
  },
  labor: {
    label: 'Labor (Salaried / Aggregate)',
    color: '#00AEE6',
    description: 'Aggregate labor cost pools (e.g. US Salaried Management, Union Labor). Enter the total annual dollar cost for the pool — the system escalates it year-over-year using the CPI % you set per line, and splits CIF vs PIF based on Allocation.',
    units: ['annual', 'each'],
    computeNote: 'Each line: annual_cost × (1 + CPI%)^yr × qty, split by Allocation into CIF or PIF totals.',
  },
  odcs: {
    label: 'Other Direct Costs (ODCs)',
    color: '#E6A000',
    description: 'Non-labor direct costs: facilities, consumables, utilities, travel, insurance, subcontracts. Enter current and future annual cost. The system applies reduction %, fringe (if applicable), and CPI escalation year-over-year.',
    units: ['annual', 'monthly', 'each', 'per-station', 'per-txn'],
    computeNote: 'Each line: future_cost × (1 − red%) × (1 + fringe%) escalated by CPI each year × qty.',
  },
  capital: {
    label: 'Capital / One-Time',
    color: '#7B3FA0',
    description: 'One-time capital expenditures (equipment, infrastructure, software licenses). Items appear in startup year only UNLESS you mark them "Capital (amortized)" — in that case the cost is spread evenly across base contract years.',
    units: ['each', 'per-station', 'lot'],
    computeNote: 'Allocation "Capital (amortized)": cost ÷ base_years per year. Other allocations: full cost in year 1.',
  },
  hourly_rates: {
    label: 'Hourly T&M Bill Rates',
    color: '#0E4774',
    description: 'Key Personnel billed on a Time & Material basis per the RFP §3.14.1. Enter the base hourly rate. The system computes the fully-loaded bill rate: base × (1 + Fringe%) × (1 + Burden%) × (1 + G&A%) × (1 + Fee%). Qty = estimated annual hours.',
    units: ['hourly'],
    computeNote: 'Bill rate = base × (1+fringe%) × (1+burden%) × (1+G&A%) × (1+fee%). Annual cost = bill_rate × qty_hours.',
  },
};

// ── Add Category Dialog ────────────────────────────────────────────
const AddCategoryDialog = ({ open, tabCode, onClose, onSave }) => {
  const meta = TAB_META[tabCode] || {};
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  useEffect(() => {
    if (open) { setName(''); setDescription(''); }
  }, [open]);

  const handleSave = () => {
    if (!name.trim()) return;
    onSave(name.trim(), description.trim() || null);
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Category — {meta.label || tabCode}</DialogTitle>
      <DialogContent dividers>
        {/* Explain what the system will do */}
        <Alert severity="info" icon={false} sx={{ mb: 2 }}>
          <Typography variant="subtitle2" gutterBottom>What this cost type does:</Typography>
          <Typography variant="body2">{meta.description}</Typography>
          {meta.computeNote && (
            <Typography variant="caption" display="block" sx={{ mt: 1, fontFamily: 'monospace', color: 'text.secondary' }}>
              Calculation: {meta.computeNote}
            </Typography>
          )}
        </Alert>

        <TextField
          fullWidth size="small" label="Category Name *" autoFocus
          value={name} onChange={e => setName(e.target.value)}
          sx={{ mb: 2 }}
          placeholder={`e.g. ${tabCode === 'labor' ? 'Project Management Staff' : tabCode === 'odcs' ? 'Facility Rent & Utilities' : tabCode === 'capital' ? 'Lane Inspection Equipment' : 'Category name'}`}
        />
        <TextField
          fullWidth size="small" label="Description (optional)" multiline rows={2}
          value={description} onChange={e => setDescription(e.target.value)}
          placeholder="What costs belong here? Who owns this budget?"
        />

        {meta.units && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
            Typical units for this cost type: <strong>{meta.units.join(', ')}</strong>. Set per line item.
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={handleSave} disabled={!name.trim()}>Create Category</Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Add Line Item Dialog ───────────────────────────────────────────
const UNIT_OPTIONS = ['each', 'annual', 'monthly', 'hourly', 'per-station', 'per-txn', 'lot'];

const AddLineItemDialog = ({ open, onClose, onSave }) => {
  const [form, setForm] = useState({
    name: '', allocation_basis: 'SHARED',
    current_cost: 0, future_cost: 0,
    reduction_pct: 0, fringe_pct: 0, burden_pct: 0, ga_pct: 0, fee_pct: 0,
    qty: 1, unit: 'each',
  });

  useEffect(() => {
    if (open) setForm({
      name: '', allocation_basis: 'SHARED',
      current_cost: 0, future_cost: 0,
      reduction_pct: 0, fringe_pct: 0, burden_pct: 0, ga_pct: 0, fee_pct: 0,
      qty: 1, unit: 'each',
    });
  }, [open]);

  const set = (f, v) => setForm(prev => ({ ...prev, [f]: v }));

  // Live cost preview
  const preview = (() => {
    const base = (form.future_cost || 0) * (1 - (form.reduction_pct || 0) / 100);
    const loaded = base * (1 + (form.fringe_pct || 0) / 100) * (1 + (form.burden_pct || 0) / 100)
      * (1 + (form.ga_pct || 0) / 100) * (1 + (form.fee_pct || 0) / 100);
    return loaded * (form.qty || 1);
  })();

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Line Item</DialogTitle>
      <DialogContent dividers>
        <Alert severity="info" icon={false} sx={{ mb: 2 }}>
          <Typography variant="body2">
            <strong>How costs are calculated:</strong> Annual cost = Future Cost × (1 − Reduction%) × (1 + Fringe%) × (1 + Burden%) × (1 + G&A%) × (1 + Fee%) × Qty.
            Each year this is escalated by the CPI % you can set per line after saving.
            CIF/PIF allocation controls which revenue pool this cost is charged against.
          </Typography>
        </Alert>

        <Grid container spacing={2}>
          <Grid item xs={12}>
            <TextField fullWidth size="small" label="Line Item Name *" autoFocus
              value={form.name} onChange={e => set('name', e.target.value)} />
          </Grid>
          <Grid item xs={6}>
            <TextField fullWidth size="small" type="number" label="Current Cost ($)"
              value={form.current_cost} onChange={e => set('current_cost', Number(e.target.value) || 0)}
              helperText="What it costs today (reference)" inputProps={{ min: 0, step: 100 }} />
          </Grid>
          <Grid item xs={6}>
            <TextField fullWidth size="small" type="number" label="Future / Bid Cost ($)"
              value={form.future_cost} onChange={e => set('future_cost', Number(e.target.value) || 0)}
              helperText="What you plan to bid" inputProps={{ min: 0, step: 100 }} />
          </Grid>
          <Grid item xs={4}>
            <TextField fullWidth size="small" type="number" label="Reduction %"
              value={form.reduction_pct} onChange={e => set('reduction_pct', Number(e.target.value) || 0)}
              helperText="Discount off future cost" inputProps={{ min: 0, max: 100, step: 1 }} />
          </Grid>
          <Grid item xs={4}>
            <TextField fullWidth size="small" type="number" label="Fringe %"
              value={form.fringe_pct} onChange={e => set('fringe_pct', Number(e.target.value) || 0)}
              helperText="Benefits on top of base" inputProps={{ min: 0, step: 1 }} />
          </Grid>
          <Grid item xs={4}>
            <TextField fullWidth size="small" type="number" label="Burden %"
              value={form.burden_pct} onChange={e => set('burden_pct', Number(e.target.value) || 0)}
              helperText="Payroll taxes, overhead" inputProps={{ min: 0, step: 1 }} />
          </Grid>
          <Grid item xs={4}>
            <TextField fullWidth size="small" type="number" label="G&A %"
              value={form.ga_pct} onChange={e => set('ga_pct', Number(e.target.value) || 0)}
              helperText="General & Administrative" inputProps={{ min: 0, step: 1 }} />
          </Grid>
          <Grid item xs={4}>
            <TextField fullWidth size="small" type="number" label="Fee %"
              value={form.fee_pct} onChange={e => set('fee_pct', Number(e.target.value) || 0)}
              helperText="Profit / fee" inputProps={{ min: 0, step: 1 }} />
          </Grid>
          <Grid item xs={2}>
            <TextField fullWidth size="small" type="number" label="Qty"
              value={form.qty} onChange={e => set('qty', Number(e.target.value) || 1)}
              inputProps={{ min: 0, step: 1 }} />
          </Grid>
          <Grid item xs={2}>
            <FormControl fullWidth size="small">
              <InputLabel>Unit</InputLabel>
              <Select label="Unit" value={form.unit} onChange={e => set('unit', e.target.value)}>
                {UNIT_OPTIONS.map(u => <MenuItem key={u} value={u}>{u}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12}>
            <FormControl fullWidth size="small">
              <InputLabel>Allocation</InputLabel>
              <Select label="Allocation" value={form.allocation_basis} onChange={e => set('allocation_basis', e.target.value)}>
                <MenuItem value="CIF">CIF — CIF inspection stations only</MenuItem>
                <MenuItem value="PIF">PIF — Private Inspection Facilities only</MenuItem>
                <MenuItem value="SHARED">Shared — split across CIF + PIF by volume</MenuItem>
                <MenuItem value="CAPITAL_AMORT">Capital (amortized) — one-time cost spread over base years</MenuItem>
              </Select>
            </FormControl>
          </Grid>
        </Grid>

        <Box sx={{ mt: 2, p: 1, borderRadius: 1, backgroundColor: 'rgba(0,174,230,0.06)', display: 'flex', gap: 2, alignItems: 'center' }}>
          <Typography variant="caption" color="text.secondary">Year 1 estimated cost (before CPI escalation):</Typography>
          <Typography variant="caption" sx={{ fontWeight: 700, color: 'primary.main' }}>{fmtCurrency(preview)}</Typography>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => { if (form.name.trim()) { onSave(form); } }} disabled={!form.name.trim()}>
          Add Line Item
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Create Model Dialog ────────────────────────────────────────────
const CreateModelDialog = ({ open, onClose, onSave }) => {
  const [name, setName] = useState('');
  const [baseYears, setBaseYears] = useState(6);
  const [extYears, setExtYears] = useState(4);
  // Margins start as empty strings so the user MUST type a value — there
  // is no honest default for a real bid. Validation below blocks save
  // until both are valid numbers in [-50, 95].
  const [cifMargin, setCifMargin] = useState('');
  const [pifMargin, setPifMargin] = useState('');

  useEffect(() => {
    if (open) {
      setName(''); setBaseYears(6); setExtYears(4);
      setCifMargin(''); setPifMargin('');
    }
  }, [open]);

  const cifNum = cifMargin === '' ? null : Number(cifMargin);
  const pifNum = pifMargin === '' ? null : Number(pifMargin);
  const cifValid = cifNum !== null && Number.isFinite(cifNum) && cifNum >= -50 && cifNum <= 95;
  const pifValid = pifNum !== null && Number.isFinite(pifNum) && pifNum >= -50 && pifNum <= 95;
  const canSave = !!name.trim() && cifValid && pifValid;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>New Pricing Model</DialogTitle>
      <DialogContent dividers>
        <Alert severity="info" icon={false} sx={{ mb: 2 }}>
          <Typography variant="body2">
            A pricing model contains all cost categories and line items for a specific bid scenario.
            You can clone an existing model instead of starting from scratch — use the Clone option on an existing model.
          </Typography>
        </Alert>
        <TextField fullWidth size="small" label="Model Name *" autoFocus sx={{ mb: 2 }}
          value={name} onChange={e => setName(e.target.value)}
          placeholder="e.g. NJ T1628 2025 Bid — Base Scenario" />
        <Grid container spacing={2}>
          <Grid item xs={6}>
            <FormControl fullWidth size="small">
              <InputLabel>Base Contract Years</InputLabel>
              <Select label="Base Contract Years" value={baseYears} onChange={e => setBaseYears(e.target.value)}>
                {[3,4,5,6,7,8,9,10].map(y => <MenuItem key={y} value={y}>{y} years</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={6}>
            <FormControl fullWidth size="small">
              <InputLabel>Extension Years</InputLabel>
              <Select label="Extension Years" value={extYears} onChange={e => setExtYears(e.target.value)}>
                {[0,1,2,3,4,5,6].map(y => <MenuItem key={y} value={y}>{y} years</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={6}>
            <TextField
              fullWidth size="small" type="number"
              label="CIF Margin % *"
              value={cifMargin}
              onChange={e => setCifMargin(e.target.value)}
              error={cifMargin !== '' && !cifValid}
              helperText={cifMargin !== '' && !cifValid ? 'Enter -50 to 95' : 'Required'}
              inputProps={{ step: 0.5, min: -50, max: 95 }}
            />
          </Grid>
          <Grid item xs={6}>
            <TextField
              fullWidth size="small" type="number"
              label="PIF Margin % *"
              value={pifMargin}
              onChange={e => setPifMargin(e.target.value)}
              error={pifMargin !== '' && !pifValid}
              helperText={pifMargin !== '' && !pifValid ? 'Enter -50 to 95' : 'Required'}
              inputProps={{ step: 0.5, min: -50, max: 95 }}
            />
          </Grid>
        </Grid>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          Total contract duration: <strong>{Number(baseYears) + Number(extYears)} years</strong>. All cost escalations and headcount arrays are sized to this.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained"
                onClick={() => canSave && onSave(name.trim(), baseYears, extYears, cifNum, pifNum)}
                disabled={!canSave}>
          Create Model
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Main component ──────────────────────────────────────────────────
const PricingModel = () => {
  const [models, setModels] = useState([]);
  const [selectedModelId, setSelectedModelId] = useState(null);
  const [full, setFull] = useState(null); // full pricing payload
  const [activeTab, setActiveTab] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [computing, setComputing] = useState(false);
  const [error, setError] = useState(null);

  // Header editable state (sent back on save)
  const [header, setHeader] = useState(null);

  // Dialog state
  const [catDialogTab, setCatDialogTab] = useState(null);   // non-null = open
  const [lineDialogCatId, setLineDialogCatId] = useState(null);  // non-null = open
  const [modelDialogOpen, setModelDialogOpen] = useState(false);

  // Historical bids (read-only context)
  const [historicalBids, setHistoricalBids] = useState([]);
  const [staffingPositions, setStaffingPositions] = useState([]);

  const fetchStaffing = useCallback(async (id) => {
    if (!id) return;
    try {
      const r = await axios.get(`/api/pricing/models/${id}/staffing`);
      setStaffingPositions(r.data || []);
    } catch { /* ignore */ }
  }, []);

  const fetchModels = useCallback(async () => {
    try {
      const r = await axios.get('/api/pricing/models');
      const list = r.data.models || r.data || [];
      setModels(list);
      if (list.length > 0 && selectedModelId == null) {
        setSelectedModelId(list[0].id);
      }
    } catch (e) {
      setError('Failed to load pricing models');
    }
  }, [selectedModelId]);

  const fetchFull = useCallback(async (id) => {
    if (!id) return;
    setLoading(true);
    try {
      const r = await axios.get(`/api/pricing/models/${id}/full`);
      setFull(r.data);
      const m = r.data;  // /full returns the model fields flat (not nested under 'model')
      // IMPORTANT: do NOT inject default margins (the old `?? 12 / ?? 15`
      // pattern). If the DB has a real value we show it; if it's null we
      // leave the field empty so the user is forced to enter a real value.
      // This is what made every model appear to "reset to 12 / 15" — the
      // typed margin was discarded on refetch.
      setHeader({
        name: m.name || '',
        description: m.description || '',
        base_years: m.base_years || 6,
        extension_years: m.extension_years || 4,
        target_contract_value: m.target_contract_value || 0,
        cif_volumes: (m.cif_volumes || []).slice(),
        pif_volumes: (m.pif_volumes || []).slice(),
        cif_margin_pct: m.cif_margin_pct == null ? '' : m.cif_margin_pct,
        pif_margin_pct: m.pif_margin_pct == null ? '' : m.pif_margin_pct,
      });
    } catch (e) {
      setError('Failed to load pricing model');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchHistoricalBids = useCallback(async () => {
    try {
      const r = await axios.get('/api/pricing/competitor-bids');
      setHistoricalBids(r.data.bids || r.data || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchModels(); fetchHistoricalBids(); }, [fetchModels, fetchHistoricalBids]);
  useEffect(() => { if (selectedModelId) fetchFull(selectedModelId); }, [selectedModelId, fetchFull]);
  useEffect(() => { if (selectedModelId) fetchStaffing(selectedModelId); }, [selectedModelId, fetchStaffing]);

  // Derived — total contract years
  const totalYears = useMemo(
    () => (header ? (header.base_years || 0) + (header.extension_years || 0) : 0),
    [header],
  );

  // Ensure volume arrays are totalYears long
  useEffect(() => {
    if (!header) return;
    const pad = (arr) => {
      const a = [...(arr || [])];
      while (a.length < totalYears) a.push(0);
      return a.slice(0, totalYears);
    };
    if ((header.cif_volumes || []).length !== totalYears
        || (header.pif_volumes || []).length !== totalYears) {
      setHeader(h => ({ ...h, cif_volumes: pad(h.cif_volumes), pif_volumes: pad(h.pif_volumes) }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalYears]);

  // ── Header dirty/validity tracking ──
  // The user-typed margin/volume/etc. is "dirty" until a successful PUT
  // to /api/pricing/models/{id}. Recompute previously called POST /compute
  // and then refetch /full, which silently overwrote any unsaved typed
  // values with whatever was in the DB — making typed margins appear to
  // "reset" to defaults. The fix: Recompute auto-saves dirty headers first.
  const headerDirty = useMemo(() => {
    if (!full || !header) return false;
    const persisted = {
      name: full.name || '',
      description: full.description || '',
      base_years: full.base_years || 6,
      extension_years: full.extension_years || 4,
      target_contract_value: full.target_contract_value || 0,
      cif_margin_pct: full.cif_margin_pct == null ? '' : full.cif_margin_pct,
      pif_margin_pct: full.pif_margin_pct == null ? '' : full.pif_margin_pct,
    };
    const numEq = (a, b) => Number(a) === Number(b);
    if ((header.name || '') !== persisted.name) return true;
    if ((header.description || '') !== persisted.description) return true;
    if (!numEq(header.base_years, persisted.base_years)) return true;
    if (!numEq(header.extension_years, persisted.extension_years)) return true;
    if (!numEq(header.target_contract_value, persisted.target_contract_value)) return true;
    // Margin: empty string in either side means "not set" — they're equal
    // only if both are empty or both are equal numbers.
    const margEq = (a, b) => {
      if (a === '' && b === '') return true;
      if (a === '' || b === '') return false;
      return Number(a) === Number(b);
    };
    if (!margEq(header.cif_margin_pct, persisted.cif_margin_pct)) return true;
    if (!margEq(header.pif_margin_pct, persisted.pif_margin_pct)) return true;
    const arrEq = (a, b) => {
      if (!Array.isArray(a) || !Array.isArray(b)) return false;
      if (a.length !== b.length) return false;
      for (let i = 0; i < a.length; i++) if (Number(a[i]) !== Number(b[i])) return false;
      return true;
    };
    if (!arrEq(header.cif_volumes, full.cif_volumes || [])) return true;
    if (!arrEq(header.pif_volumes, full.pif_volumes || [])) return true;
    return false;
  }, [header, full]);

  const headerValid = useMemo(() => {
    if (!header) return false;
    const cif = header.cif_margin_pct;
    const pif = header.pif_margin_pct;
    const cifNum = cif === '' ? null : Number(cif);
    const pifNum = pif === '' ? null : Number(pif);
    if (cifNum === null || pifNum === null) return false;
    if (!Number.isFinite(cifNum) || !Number.isFinite(pifNum)) return false;
    if (cifNum < -50 || cifNum > 95) return false;
    if (pifNum < -50 || pifNum > 95) return false;
    return true;
  }, [header]);

  // ── Save header ──
  // Returns true on success. Validates margins first so we never PUT a
  // request the backend will reject for missing/out-of-range values.
  const saveHeader = useCallback(async () => {
    if (!full || !header) return false;
    if (!headerValid) {
      setError('Cannot save: both margins are required and must be between -50 and 95.');
      return false;
    }
    setSaving(true);
    try {
      await axios.put(`/api/pricing/models/${full.id}`, {
        name: header.name,
        description: header.description,
        base_years: Number(header.base_years),
        extension_years: Number(header.extension_years),
        target_contract_value: Number(header.target_contract_value),
        cif_volumes: header.cif_volumes.map(Number),
        pif_volumes: header.pif_volumes.map(Number),
        cif_margin_pct: Number(header.cif_margin_pct),
        pif_margin_pct: Number(header.pif_margin_pct),
        expected_version: full.version,
      });
      await fetchFull(full.id);
      return true;
    } catch (e) {
      setError('Save failed: ' + (e.response?.data?.detail || e.message));
      return false;
    } finally {
      setSaving(false);
    }
  }, [full, header, headerValid, fetchFull]);

  // Recompute. If the user has unsaved typed changes (e.g. they edited
  // margin then clicked Recompute without Save), auto-save first so the
  // recomputation reflects what's on screen — not the stale DB values.
  // This is the fix for "margin keeps resetting to 12 / 15".
  const compute = async () => {
    if (!full) return;
    setComputing(true);
    try {
      if (headerDirty) {
        if (!headerValid) {
          setError('Recompute aborted: both margins must be valid before recomputing. '
                 + 'Either fix the margin fields or click Discard.');
          return;
        }
        const ok = await saveHeader();
        if (!ok) return;  // saveHeader already set the error
        // saveHeader's PUT already triggers a backend recompute — we still
        // call /compute below to be defensive (cheap, idempotent), then
        // a single refetch picks up the fresh totals.
      }
      await axios.post(`/api/pricing/models/${full.id}/compute`);
      await fetchFull(full.id);
    } catch (e) {
      setError('Compute failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setComputing(false);
    }
  };

  // Discard typed changes and revert to the persisted values.
  const discardHeaderChanges = () => {
    if (!full) return;
    fetchFull(full.id);
  };

  // ── Line-item edits ──
  const updateLineField = async (lineId, patch) => {
    try {
      await axios.put(`/api/pricing/line-items/${lineId}`, patch);
      // Refetch so computed totals + version reflect the server's auto-recompute
      await fetchFull(full.id);
    } catch (e) {
      setError('Line update failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const deleteLineItem = async (lineId) => {
    if (!window.confirm('Delete this line item?')) return;
    try {
      await axios.delete(`/api/pricing/line-items/${lineId}`);
      await fetchFull(full.id);
    } catch (e) {
      setError('Delete failed');
    }
  };

  const addCategory = async (tabCode, name, description) => {
    try {
      await axios.post(`/api/pricing/models/${full.id}/categories`, {
        tab: tabCode, name, description: description || null, user_defined: true,
      });
      await fetchFull(full.id);
    } catch (e) {
      setError('Add category failed');
    }
  };

  const addLineItem = async (categoryId, fields) => {
    try {
      await axios.post(`/api/pricing/categories/${categoryId}/line-items`, {
        name: fields.name || 'New line item',
        allocation_basis: fields.allocation_basis || 'SHARED',
        included: true,
        current_cost: fields.current_cost || 0,
        future_cost: fields.future_cost || 0,
        reduction_pct: fields.reduction_pct || 0,
        fringe_pct: fields.fringe_pct || 0,
        burden_pct: fields.burden_pct || 0,
        ga_pct: fields.ga_pct || 0,
        fee_pct: fields.fee_pct || 0,
        qty: fields.qty || 1,
        unit: fields.unit || 'each',
        user_defined: true,
      });
      await fetchFull(full.id);
    } catch (e) {
      setError('Add line failed');
    }
  };

  const createNewModel = async (name, base_years, extension_years, cif_margin_pct, pif_margin_pct) => {
    // The dialog enforces non-null margins; we re-validate here so the
    // backend never receives the old "always 12 / 15" defaults.
    if (cif_margin_pct == null || pif_margin_pct == null
        || !Number.isFinite(cif_margin_pct) || !Number.isFinite(pif_margin_pct)) {
      setError('Both margins are required to create a model.');
      return;
    }
    try {
      const r = await axios.post('/api/pricing/models', {
        name,
        base_years: base_years || 6,
        extension_years: extension_years || 4,
        cif_margin_pct,
        pif_margin_pct,
      });
      await fetchModels();
      setSelectedModelId(r.data.id);
    } catch (e) {
      setError('Create model failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  // ── Volume cell edit helper ──
  const setVolume = (typ, idx, val) => {
    setHeader(h => {
      const arr = [...(typ === 'cif' ? h.cif_volumes : h.pif_volumes)];
      arr[idx] = Number(val) || 0;
      return typ === 'cif' ? { ...h, cif_volumes: arr } : { ...h, pif_volumes: arr };
    });
  };

  if (loading && !full) {
    return (
      <Box sx={{ p: 3 }}>
        <Typography variant="h4" gutterBottom>Pricing Model</Typography>
        <LinearProgress />
      </Box>
    );
  }

  const m = full;  // /full returns model fields flat
  const currentTab = TABS[activeTab];
  const tabCategories = (full?.categories || []).filter(c => c.tab === currentTab.code);

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 2 }}>
        <Typography variant="h4">Pricing Model</Typography>
        <FormControl size="small" sx={{ minWidth: 280 }}>
          <InputLabel>Model</InputLabel>
          <Select
            label="Model"
            value={selectedModelId || ''}
            onChange={(e) => setSelectedModelId(e.target.value)}
          >
            {models.map(mm => (
              <MenuItem key={mm.id} value={mm.id}>{mm.name}</MenuItem>
            ))}
          </Select>
        </FormControl>
        <Button startIcon={<AddIcon />} onClick={() => setModelDialogOpen(true)} variant="outlined" size="small">
          New Model
        </Button>
        <Box sx={{ flex: 1 }} />
        {headerDirty && (
          <>
            <Chip
              size="small"
              color="warning"
              label="Unsaved changes"
              sx={{ mr: 1 }}
            />
            <Button
              size="small"
              variant="outlined"
              color="warning"
              onClick={discardHeaderChanges}
              disabled={computing || saving}
              sx={{ mr: 1 }}
            >
              Discard
            </Button>
          </>
        )}
        <Button
          startIcon={<CalculateIcon />}
          onClick={compute}
          disabled={computing || saving || !m || (headerDirty && !headerValid)}
          variant="contained"
          color="secondary"
        >
          {computing
            ? (headerDirty ? 'Saving + Computing…' : 'Computing…')
            : (headerDirty ? 'Save & Recompute' : 'Recompute')}
        </Button>
      </Box>

      {error && <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>{error}</Alert>}

      {!m ? (
        <Alert severity="info">No pricing model selected. Create one to get started.</Alert>
      ) : (
        <>
          {/* ── Header panel ─────────────────────────────────────── */}
          <Card sx={{ mb: 2 }}>
            <CardContent>
              <Grid container spacing={2} alignItems="center">
                <Grid item xs={12} md={4}>
                  <TextField
                    fullWidth size="small" label="Model Name"
                    value={header.name}
                    onChange={(e) => setHeader({ ...header, name: e.target.value })}
                  />
                </Grid>
                <Grid item xs={6} md={2}>
                  <FormControl fullWidth size="small">
                    <InputLabel>Contract Years</InputLabel>
                    <Select
                      label="Contract Years"
                      value={header.base_years}
                      onChange={(e) => setHeader({ ...header, base_years: e.target.value })}
                    >
                      {[1,2,3,4,5,6,7,8,9,10].map(y => <MenuItem key={y} value={y}>{y}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={6} md={2}>
                  <FormControl fullWidth size="small">
                    <InputLabel>Extension Years</InputLabel>
                    <Select
                      label="Extension Years"
                      value={header.extension_years}
                      onChange={(e) => setHeader({ ...header, extension_years: e.target.value })}
                    >
                      {[0,1,2,3,4,5,6].map(y => <MenuItem key={y} value={y}>{y}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={6} md={2}>
                  <TextField
                    fullWidth size="small" label="Target Contract Value" type="number"
                    value={header.target_contract_value}
                    onChange={(e) => setHeader({ ...header, target_contract_value: e.target.value })}
                  />
                </Grid>
                <Grid item xs={6} md={1}>
                  <TextField
                    fullWidth size="small" label="CIF Margin %" type="number"
                    value={header.cif_margin_pct}
                    onChange={(e) => setHeader({ ...header, cif_margin_pct: e.target.value })}
                    inputProps={{ step: 0.5 }}
                  />
                </Grid>
                <Grid item xs={6} md={1}>
                  <TextField
                    fullWidth size="small" label="PIF Margin %" type="number"
                    value={header.pif_margin_pct}
                    onChange={(e) => setHeader({ ...header, pif_margin_pct: e.target.value })}
                    inputProps={{ step: 0.5 }}
                  />
                </Grid>
              </Grid>

              {/* Per-year volumes */}
              <Box sx={{ mt: 2 }}>
                <Typography variant="subtitle2" gutterBottom>Per-Year Inspection Volumes</Typography>
                <TableContainer component={Paper} variant="outlined">
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 600 }}>Type</TableCell>
                        {Array.from({ length: totalYears }).map((_, i) => (
                          <TableCell key={i} align="center" sx={{ fontWeight: 600 }}>
                            Y{i + 1}
                            {i >= (header.base_years || 0) && (
                              <Chip label="ext" size="small" sx={{ ml: 0.5, height: 16, fontSize: 10 }} />
                            )}
                          </TableCell>
                        ))}
                        <TableCell align="right" sx={{ fontWeight: 600 }}>Total</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      <TableRow>
                        <TableCell><strong>CIF</strong></TableCell>
                        {(header.cif_volumes || []).map((v, i) => (
                          <TableCell key={i} align="center" sx={{ p: 0.5 }}>
                            <TextField
                              size="small" variant="standard" type="number"
                              value={v}
                              onChange={(e) => setVolume('cif', i, e.target.value)}
                              sx={{ width: 90 }}
                              inputProps={{ style: { textAlign: 'right' } }}
                            />
                          </TableCell>
                        ))}
                        <TableCell align="right">
                          {(header.cif_volumes || []).reduce((a, b) => a + Number(b || 0), 0).toLocaleString()}
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell><strong>PIF</strong></TableCell>
                        {(header.pif_volumes || []).map((v, i) => (
                          <TableCell key={i} align="center" sx={{ p: 0.5 }}>
                            <TextField
                              size="small" variant="standard" type="number"
                              value={v}
                              onChange={(e) => setVolume('pif', i, e.target.value)}
                              sx={{ width: 90 }}
                              inputProps={{ style: { textAlign: 'right' } }}
                            />
                          </TableCell>
                        ))}
                        <TableCell align="right">
                          {(header.pif_volumes || []).reduce((a, b) => a + Number(b || 0), 0).toLocaleString()}
                        </TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </TableContainer>
              </Box>

              <Box sx={{ mt: 2, display: 'flex', gap: 1, alignItems: 'center' }}>
                <Button
                  startIcon={<SaveIcon />}
                  variant="contained"
                  onClick={saveHeader}
                  disabled={saving || !headerDirty || !headerValid}
                >
                  {saving ? 'Saving…' : 'Save & Recompute'}
                </Button>
                {headerDirty && (
                  <Button
                    variant="text"
                    color="warning"
                    onClick={discardHeaderChanges}
                    disabled={saving}
                  >
                    Discard changes
                  </Button>
                )}
                {!headerValid && (
                  <Typography variant="caption" color="error" sx={{ ml: 1 }}>
                    Both margins required (-50 to 95).
                  </Typography>
                )}
                {headerDirty && headerValid && (
                  <Typography variant="caption" color="warning.main" sx={{ ml: 1 }}>
                    Unsaved changes — KPIs below reflect last saved state.
                  </Typography>
                )}
              </Box>
            </CardContent>
          </Card>

          {/* ── KPI readout ─────────────────────────────────────── */}
          <Grid container spacing={2} sx={{ mb: 2 }}>
            <KpiCard title="CIF Cost"    value={fmtCurrency(m.computed_cif_cost)}    />
            <KpiCard title="CIF Revenue" value={fmtCurrency(m.computed_cif_revenue)} />
            <KpiCard title="CIF PPT"     value={fmtPPT(m.computed_cif_ppt)}          highlight />
            <KpiCard title="CIF GP"      value={fmtCurrency(m.computed_cif_gp)}      />
            <KpiCard title="PIF Cost"    value={fmtCurrency(m.computed_pif_cost)}    />
            <KpiCard title="PIF Revenue" value={fmtCurrency(m.computed_pif_revenue)} />
            <KpiCard title="PIF PPT"     value={fmtPPT(m.computed_pif_ppt)}          highlight />
            <KpiCard title="PIF GP"      value={fmtCurrency(m.computed_pif_gp)}      />
            <KpiCard title="Total Cost"    value={fmtCurrency(m.computed_total_cost)}    />
            <KpiCard title="Total Revenue" value={fmtCurrency(m.computed_total_revenue)} />
            <KpiCard title="Total GP"      value={fmtCurrency(m.computed_total_gp)}      highlight />
            <KpiCard title="GP Margin"
              value={m.computed_total_revenue
                ? fmtPct(100 * m.computed_total_gp / m.computed_total_revenue)
                : '—'}
            />
          </Grid>

          {/* ── Tabs ─────────────────────────────────────── */}
          <Card>
            <Tabs
              value={activeTab}
              onChange={(_, v) => setActiveTab(v)}
              variant="scrollable"
              scrollButtons="auto"
              sx={{ borderBottom: 1, borderColor: 'divider' }}
            >
              {TABS.map((t, i) => <Tab key={t.code} label={t.label} />)}
            </Tabs>

            <CardContent>
              {currentTab.code === 'comparison' ? (
                <ComparisonTab bids={historicalBids} model={m} />
              ) : currentTab.code === 'cashflow' ? (
                <CashFlowTab modelId={m.id} modelVersion={m.version} totalYears={totalYears} />
              ) : currentTab.code === 'submittal' ? (
                <SubmittalTab modelId={m.id} modelVersion={m.version} totalYears={totalYears} />              ) : currentTab.code === 'staffing' ? (
                <StaffingPlanTab
                  modelId={m.id}
                  totalYears={totalYears}
                  baseYears={header.base_years}
                  positions={staffingPositions}
                  onRefresh={() => fetchStaffing(m.id)}
                />              ) : (
                <>
                  <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
                    <Button
                      startIcon={<AddIcon />}
                      size="small"
                      onClick={() => setCatDialogTab(currentTab.code)}
                    >
                      Add Category
                    </Button>
                  </Box>
                  {tabCategories.length === 0 && (
                    <Alert severity="info">No categories in this tab yet. Click "Add Category" to create one.</Alert>
                  )}
                  {tabCategories.map(cat => (
                    <CategorySection
                      key={cat.id}
                      category={cat}
                      totalYears={totalYears}
                      baseYears={header.base_years}
                      onLineUpdate={updateLineField}
                      onLineDelete={deleteLineItem}
                      onAddLine={() => setLineDialogCatId(cat.id)}
                    />
                  ))}
                </>
              )}

              {/* Dialogs */}
              <AddCategoryDialog
                open={catDialogTab !== null}
                tabCode={catDialogTab || ''}
                onClose={() => setCatDialogTab(null)}
                onSave={(name, desc) => { addCategory(catDialogTab, name, desc); setCatDialogTab(null); }}
              />
              <AddLineItemDialog
                open={lineDialogCatId !== null}
                onClose={() => setLineDialogCatId(null)}
                onSave={(fields) => { addLineItem(lineDialogCatId, fields); setLineDialogCatId(null); }}
              />
              <CreateModelDialog
                open={modelDialogOpen}
                onClose={() => setModelDialogOpen(false)}
                onSave={(name, baseYrs, extYrs, cifM, pifM) => {
                  createNewModel(name, baseYrs, extYrs, cifM, pifM);
                  setModelDialogOpen(false);
                }}
              />
            </CardContent>
          </Card>
        </>
      )}
    </Box>
  );
};

// ── Small KPI card ─────────────────────────────────────────────────
const KpiCard = ({ title, value, highlight }) => (
  <Grid item xs={6} md={3} lg={2}>
    <Card variant="outlined" sx={{
      borderColor: highlight ? 'secondary.main' : undefined,
      backgroundColor: highlight ? 'rgba(80,191,52,0.06)' : undefined,
    }}>
      <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
        <Typography variant="caption" color="text.secondary">{title}</Typography>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>{value}</Typography>
      </CardContent>
    </Card>
  </Grid>
);

// ── One category with its line-items table ─────────────────────────
// Bill-rate build-up: base = future × (1 - red%) × (1 + fringe%);
// bill = base × (1 + burden%) × (1 + G&A%) × (1 + fee%).
// Matches server-side _line_effective_per_year for unit === 'hourly'.
const computeBillRate = (li) => {
  const base = (Number(li.future_cost) || 0)
    * (1 - (Number(li.reduction_pct) || 0) / 100)
    * (1 + (Number(li.fringe_pct) || 0) / 100);
  return base
    * (1 + (Number(li.burden_pct) || 0) / 100)
    * (1 + (Number(li.ga_pct)     || 0) / 100)
    * (1 + (Number(li.fee_pct)    || 0) / 100);
};

const CategorySection = ({ category, totalYears, baseYears, onLineUpdate, onLineDelete, onAddLine }) => {
  const isHourly = category.tab === 'hourly_rates';
  return (
    <Accordion defaultExpanded sx={{ mb: 1 }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography sx={{ fontWeight: 600 }}>
          {category.name}
          {category.user_defined && <Chip label="custom" size="small" sx={{ ml: 1 }} />}
          <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
            ({(category.line_items || []).length} lines)
          </Typography>
        </Typography>
      </AccordionSummary>
      <AccordionDetails sx={{ p: 0 }}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ minWidth: 220 }}>Line Item</TableCell>
                <TableCell>Alloc</TableCell>
                <TableCell align="center">Inc.</TableCell>
                <TableCell align="right">Current</TableCell>
                <TableCell align="right">Future</TableCell>
                <TableCell align="right">Red %</TableCell>
                <TableCell align="right">Fringe %</TableCell>
                {isHourly && <TableCell align="right">Burden %</TableCell>}
                {isHourly && <TableCell align="right">G&amp;A %</TableCell>}
                {isHourly && <TableCell align="right">Fee %</TableCell>}
                {isHourly && <TableCell align="right">Bill Rate</TableCell>}
                <TableCell align="right">Qty</TableCell>
                <TableCell>Unit</TableCell>
                <TableCell width={44}></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(category.line_items || []).map(li => (
                <TableRow key={li.id} hover sx={{ opacity: li.included ? 1 : 0.5 }}>
                  <TableCell>
                    <TextField
                      size="small" variant="standard"
                      fullWidth
                      defaultValue={li.name}
                      onBlur={(e) => e.target.value !== li.name && onLineUpdate(li.id, { name: e.target.value })}
                    />
                  </TableCell>
                  <TableCell>
                    <Select
                      size="small" variant="standard"
                      value={li.allocation_basis || 'SHARED'}
                      onChange={(e) => onLineUpdate(li.id, { allocation_basis: e.target.value })}
                      sx={{ fontSize: 12, minWidth: 90 }}
                    >
                      {ALLOCATION_OPTIONS.map(o => (
                        <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
                      ))}
                    </Select>
                  </TableCell>
                  <TableCell align="center">
                    <Switch
                      size="small"
                      checked={!!li.included}
                      onChange={(e) => onLineUpdate(li.id, { included: e.target.checked })}
                    />
                  </TableCell>
                  <NumCell value={li.current_cost} onCommit={(v) => onLineUpdate(li.id, { current_cost: v })} />
                  <NumCell value={li.future_cost}  onCommit={(v) => onLineUpdate(li.id, { future_cost: v })} />
                  <NumCell value={li.reduction_pct} onCommit={(v) => onLineUpdate(li.id, { reduction_pct: v })} />
                  <NumCell value={li.fringe_pct}    onCommit={(v) => onLineUpdate(li.id, { fringe_pct: v })} />
                  {isHourly && (
                    <NumCell value={li.burden_pct} onCommit={(v) => onLineUpdate(li.id, { burden_pct: v })} />
                  )}
                  {isHourly && (
                    <NumCell value={li.ga_pct}     onCommit={(v) => onLineUpdate(li.id, { ga_pct: v })} />
                  )}
                  {isHourly && (
                    <NumCell value={li.fee_pct}    onCommit={(v) => onLineUpdate(li.id, { fee_pct: v })} />
                  )}
                  {isHourly && (
                    <Tooltip title="Computed: future × (1 − Red%) × (1 + Fringe%) × (1 + Burden%) × (1 + G&A%) × (1 + Fee%)">
                      <TableCell align="right" sx={{ p: 0.5, fontWeight: 600, color: 'primary.main', fontVariantNumeric: 'tabular-nums' }}>
                        {computeBillRate(li).toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </TableCell>
                    </Tooltip>
                  )}
                  <NumCell value={li.qty}           onCommit={(v) => onLineUpdate(li.id, { qty: v })} />
                  <TableCell>
                    <TextField
                      size="small" variant="standard"
                      defaultValue={li.unit || 'each'}
                      onBlur={(e) => e.target.value !== li.unit && onLineUpdate(li.id, { unit: e.target.value })}
                      sx={{ width: 70 }}
                    />
                  </TableCell>
                  <TableCell align="center">
                    <Tooltip title="Delete">
                      <IconButton size="small" onClick={() => onLineDelete(li.id)}>
                        <DeleteIcon fontSize="inherit" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <Box sx={{ p: 1, display: 'flex', justifyContent: 'flex-end' }}>
          <Button startIcon={<AddIcon />} size="small" onClick={onAddLine}>Add Line Item</Button>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

// Numeric cell that only fires on blur to avoid each-keystroke API calls
const NumCell = ({ value, onCommit }) => (
  <TableCell align="right" sx={{ p: 0.5 }}>
    <TextField
      size="small" variant="standard" type="number"
      defaultValue={value ?? 0}
      onBlur={(e) => {
        const v = Number(e.target.value) || 0;
        if (v !== value) onCommit(v);
      }}
      sx={{ width: 80 }}
      inputProps={{ style: { textAlign: 'right' } }}
    />
  </TableCell>
);

// ── Comparison tab ─────────────────────────────────────────────────
const ComparisonTab = ({ bids, model }) => {
  if (!bids || bids.length === 0) {
    return <Alert severity="info">No historical competitor bids seeded yet.</Alert>;
  }
  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
        <HistoryIcon color="action" />
        <Typography variant="subtitle1">
          Historical Competitor Bids ({bids.length})
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Feeds the writing personas when they impersonate a competitor.
        </Typography>
      </Stack>
      <Grid container spacing={2}>
        {bids.map(b => (
          <Grid item xs={12} md={6} key={b.id}>
            <Card variant="outlined">
              <CardContent>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                  <Typography variant="h6" sx={{ flex: 1 }}>{b.rfp_name}</Typography>
                  <Chip
                    label={b.award_status}
                    size="small"
                    color={b.award_status === 'won' ? 'success'
                      : b.award_status === 'lost' ? 'error'
                      : 'default'}
                  />
                </Box>
                <Typography variant="body2" color="text.secondary">
                  {b.state} · {b.bid_year} · {b.contract_term_years} yr ·
                  <strong> {fmtCurrency(b.total_value)}</strong>
                </Typography>
                {b.summary && (
                  <Typography variant="body2" sx={{ mt: 1 }}>{b.summary}</Typography>
                )}
                {b.strategies?.length > 0 && (
                  <Box sx={{ mt: 1 }}>
                    <Typography variant="caption" color="text.secondary">Strategies:</Typography>
                    {b.strategies.map(s => (
                      <Accordion key={s.id} variant="outlined" sx={{ mt: 0.5 }}>
                        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                          <InsightsIcon fontSize="small" sx={{ mr: 1 }} />
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {s.strategy_label}
                          </Typography>
                          <Chip label={s.confidence} size="small" sx={{ ml: 'auto' }} />
                        </AccordionSummary>
                        <AccordionDetails>
                          <Typography variant="body2">{s.description}</Typography>
                          {s.evidence && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                              Evidence: {s.evidence}
                            </Typography>
                          )}
                        </AccordionDetails>
                      </Accordion>
                    ))}
                  </Box>
                )}
                {b.line_items?.length > 0 && (
                  <Accordion variant="outlined" sx={{ mt: 1 }}>
                    <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                      <Typography variant="body2">
                        Line Items ({b.line_items.length})
                      </Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ p: 0 }}>
                      <TableContainer>
                        <Table size="small">
                          <TableHead>
                            <TableRow>
                              <TableCell>Category</TableCell>
                              <TableCell>Name</TableCell>
                              <TableCell align="right">Qty</TableCell>
                              <TableCell align="right">Unit $</TableCell>
                              <TableCell align="right">Total</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {b.line_items.map(li => (
                              <TableRow key={li.id}>
                                <TableCell sx={{ fontSize: 11 }}>{li.category}</TableCell>
                                <TableCell sx={{ fontSize: 11 }}>{li.line_name}</TableCell>
                                <TableCell align="right" sx={{ fontSize: 11 }}>
                                  {li.qty?.toLocaleString()}
                                </TableCell>
                                <TableCell align="right" sx={{ fontSize: 11 }}>
                                  {li.unit_price != null ? fmtCurrency(li.unit_price) : '—'}
                                </TableCell>
                                <TableCell align="right" sx={{ fontSize: 11 }}>
                                  {li.total_value != null ? fmtCurrency(li.total_value) : '—'}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </TableContainer>
                    </AccordionDetails>
                  </Accordion>
                )}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>
    </Box>
  );
};

// ── Cash Flow tab ──────────────────────────────────────────────────
const CashFlowTab = ({ modelId, modelVersion, totalYears }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setErr(null);
    axios.get(`/api/pricing/models/${modelId}/cash-flow`)
      .then(r => { if (!cancelled) setData(r.data); })
      .catch(e => { if (!cancelled) setErr(e.response?.data?.detail || e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [modelId, modelVersion]);

  const downloadCsv = () => {
    if (!data) return;
    const rows = [
      ['Year', 'CIF Cost', 'PIF Cost', 'Total Cost',
       'CIF Revenue', 'PIF Revenue', 'Total Revenue',
       'CIF GP', 'PIF GP', 'Total GP', 'CIF Volume', 'PIF Volume'],
      ...data.cash_flow.map(r => [
        r.year_idx, r.cif_cost, r.pif_cost, r.total_cost,
        r.cif_revenue, r.pif_revenue, r.total_revenue,
        r.cif_gp, r.pif_gp, r.total_gp, r.cif_volume, r.pif_volume,
      ]),
    ];
    const csv = rows.map(r => r.map(v => `"${v ?? ''}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `cash-flow-model-${modelId}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <LinearProgress />;
  if (err) return <Alert severity="error">{err}</Alert>;
  if (!data) return null;

  return (
    <>
      {(data.warnings || []).length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {data.warnings.map((w, i) => <div key={i}>{w}</div>)}
        </Alert>
      )}
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
        <Button size="small" onClick={downloadCsv}>Download CSV</Button>
      </Box>
      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow sx={{ backgroundColor: 'rgba(0,174,230,0.06)' }}>
              <TableCell>Year</TableCell>
              <TableCell align="right">CIF Cost</TableCell>
              <TableCell align="right">PIF Cost</TableCell>
              <TableCell align="right">Total Cost</TableCell>
              <TableCell align="right">CIF Revenue</TableCell>
              <TableCell align="right">PIF Revenue</TableCell>
              <TableCell align="right">Total Revenue</TableCell>
              <TableCell align="right">Total GP</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.cash_flow.map(r => (
              <TableRow key={r.year_idx}>
                <TableCell>Year {r.year_idx}</TableCell>
                <TableCell align="right">{fmtCurrency(r.cif_cost)}</TableCell>
                <TableCell align="right">{fmtCurrency(r.pif_cost)}</TableCell>
                <TableCell align="right">{fmtCurrency(r.total_cost)}</TableCell>
                <TableCell align="right">{fmtCurrency(r.cif_revenue)}</TableCell>
                <TableCell align="right">{fmtCurrency(r.pif_revenue)}</TableCell>
                <TableCell align="right">{fmtCurrency(r.total_revenue)}</TableCell>
                <TableCell align="right">{fmtCurrency(r.total_gp)}</TableCell>
              </TableRow>
            ))}
            <TableRow sx={{ fontWeight: 700, backgroundColor: 'rgba(80,191,52,0.08)' }}>
              {(() => {
                const T = (data.cash_flow || []).reduce(
                  (a, r) => ({ cif_cost: a.cif_cost+r.cif_cost, pif_cost: a.pif_cost+r.pif_cost,
                    total_cost: a.total_cost+r.total_cost, cif_revenue: a.cif_revenue+r.cif_revenue,
                    pif_revenue: a.pif_revenue+r.pif_revenue, total_revenue: a.total_revenue+r.total_revenue,
                    total_gp: a.total_gp+r.total_gp }),
                  { cif_cost:0, pif_cost:0, total_cost:0, cif_revenue:0, pif_revenue:0, total_revenue:0, total_gp:0 }
                );
                return (<>
                  <TableCell><strong>Total ({totalYears} yrs)</strong></TableCell>
                  <TableCell align="right"><strong>{fmtCurrency(T.cif_cost)}</strong></TableCell>
                  <TableCell align="right"><strong>{fmtCurrency(T.pif_cost)}</strong></TableCell>
                  <TableCell align="right"><strong>{fmtCurrency(T.total_cost)}</strong></TableCell>
                  <TableCell align="right"><strong>{fmtCurrency(T.cif_revenue)}</strong></TableCell>
                  <TableCell align="right"><strong>{fmtCurrency(T.pif_revenue)}</strong></TableCell>
                  <TableCell align="right"><strong>{fmtCurrency(T.total_revenue)}</strong></TableCell>
                  <TableCell align="right"><strong>{fmtCurrency(T.total_gp)}</strong></TableCell>
                </>);
              })()}
            </TableRow>
          </TableBody>
        </Table>
      </TableContainer>
    </>
  );
};

// ── Price Submittal tab ────────────────────────────────────────────
const SubmittalTab = ({ modelId, modelVersion, totalYears }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setErr(null);
    axios.get(`/api/pricing/models/${modelId}/submittal`)
      .then(r => { if (!cancelled) setData(r.data); })
      .catch(e => { if (!cancelled) setErr(e.response?.data?.detail || e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [modelId, modelVersion]);

  const yearCols = useMemo(
    () => Array.from({ length: totalYears }, (_, i) => i + 1),
    [totalYears]
  );

  const downloadCsv = () => {
    if (!data) return;
    const head = ['Tab', 'Category', 'Line', 'Unit', 'Qty', 'Allocation',
      ...yearCols.map(y => `Y${y} Total`),
      ...yearCols.map(y => `Y${y} CIF`),
      ...yearCols.map(y => `Y${y} PIF`),
      'Line Total'];
    const body = data.line_items.map(r => [
      r.tab, r.category, r.line_name, r.unit, r.qty, r.allocation_basis,
      ...(r.per_year_total || []),
      ...(r.per_year_cif || []),
      ...(r.per_year_pif || []),
      r.total,
    ]);
    const csv = [head, ...body].map(r => r.map(v => `"${v ?? ''}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `submittal-model-${modelId}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <LinearProgress />;
  if (err) return <Alert severity="error">{err}</Alert>;
  if (!data) return null;

  return (
    <>
      {(data.warnings || []).length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {data.warnings.map((w, i) => <div key={i}>{w}</div>)}
        </Alert>
      )}
      <Stack direction="row" spacing={1} sx={{ mb: 1, justifyContent: 'space-between' }}>
        <Chip size="small" label={`Model v${data.model_version}`} />
        <Button size="small" onClick={downloadCsv}>Download CSV</Button>
      </Stack>
      <TableContainer component={Paper} variant="outlined">
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow sx={{ '& th': { backgroundColor: 'rgba(0,174,230,0.08)' } }}>
              <TableCell>Tab</TableCell>
              <TableCell>Category</TableCell>
              <TableCell>Line Item</TableCell>
              <TableCell>Alloc</TableCell>
              <TableCell align="right">Qty</TableCell>
              {yearCols.map(y => (
                <TableCell key={y} align="right">Y{y}</TableCell>
              ))}
              <TableCell align="right"><strong>Total</strong></TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.line_items.map((r, idx) => (
              <TableRow key={idx} hover>
                <TableCell sx={{ fontSize: 11 }}>{r.tab}</TableCell>
                <TableCell sx={{ fontSize: 11 }}>{r.category}</TableCell>
                <TableCell sx={{ fontSize: 11 }}>{r.line_name}</TableCell>
                <TableCell sx={{ fontSize: 11 }}>{r.allocation_basis}</TableCell>
                <TableCell align="right" sx={{ fontSize: 11 }}>{r.qty?.toLocaleString()}</TableCell>
                {yearCols.map((y, i) => (
                  <TableCell key={y} align="right" sx={{ fontSize: 11 }}>
                    {fmtCurrency(r.per_year_total?.[i] || 0)}
                  </TableCell>
                ))}
                <TableCell align="right" sx={{ fontSize: 11, fontWeight: 600 }}>
                  {fmtCurrency(r.total)}
                </TableCell>
              </TableRow>
            ))}
            <TableRow sx={{ '& td': { backgroundColor: 'rgba(80,191,52,0.08)', fontWeight: 700 } }}>
              <TableCell colSpan={5}><strong>Grand Total</strong></TableCell>
              {yearCols.map((y, i) => {
                const total = (data.line_items || []).reduce(
                  (acc, li) => acc + (li.per_year_total?.[i] || 0), 0);
                return (
                  <TableCell key={y} align="right">
                    <strong>{fmtCurrency(total)}</strong>
                  </TableCell>
                );
              })}
              <TableCell align="right">
                <strong>{fmtCurrency((data.totals || {}).total_cost ?? (data.line_items||[]).reduce((a,li)=>a+(li.total||0),0))}</strong>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </TableContainer>
    </>
  );
};
// ── Staffing Plan tab ──────────────────────────────────────────────
const CLASSIFICATION_LABELS = {
  hourly_key_personnel: 'Key Personnel (T&M)',
  salaried_mgmt:        'Management (Salaried)',
  union_hourly:         'Union Hourly',
  development:          'Development (US)',
  odc:                  'ODC / Consulting',
  field_operations:     'Field Operations',
};

const CLASSIFICATION_COLORS = {
  hourly_key_personnel: '#0E4774',
  salaried_mgmt:        '#00AEE6',
  union_hourly:         '#50BF34',
  development:          '#7B3FA0',
  odc:                  '#E6A000',
  field_operations:     '#E64000',
};

// ── Constants for staffing form ────────────────────────────────────
const ENGAGEMENT_OPTIONS = [
  { value: 'fte',            label: 'FTE — Full Term (permanent)' },
  { value: 'project',        label: 'Project Resource (variable by year)' },
  { value: 'implementation', label: 'Implementation Phase only' },
  { value: 'temporary',      label: 'Temporary / Consulting' },
  { value: 'contract',       label: 'Long-term Contract' },
];

const EMPTY_POSITION = {
  role_title: '', classification: 'salaried_mgmt', pay_type: 'salaried',
  base_salary: 0, hourly_rate: 0, hours_per_year: 1696,
  overtime_eligible: false, overtime_pct: 0,
  fringe_pct: 0, burden_pct: 0, ga_pct: 0, fee_pct: 0,
  escalation_pct: 3, engagement_type: 'fte',
  allocation_basis: 'SHARED', notes: '', included: true,
  headcount_by_year: [],
};

// ── Position Dialog ────────────────────────────────────────────────
const PositionDialog = ({ open, onClose, onSave, position, totalYears, baseYears }) => {
  const isNew = !position?.id;
  const [form, setForm] = useState(EMPTY_POSITION);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  // Populate form when dialog opens or position changes
  useEffect(() => {
    if (open) {
      if (position) {
        const hc = [...(position.headcount_by_year || [])];
        while (hc.length < totalYears) hc.push(0);
        setForm({ ...EMPTY_POSITION, ...position, headcount_by_year: hc.slice(0, totalYears) });
      } else {
        const hc = Array(totalYears).fill(0);
        setForm({ ...EMPTY_POSITION, headcount_by_year: hc });
      }
      setErr(null);
    }
  }, [open, position, totalYears]);

  const set = (field, value) => setForm(f => ({ ...f, [field]: value }));
  const setHc = (yi, v) => setForm(f => {
    const hc = [...f.headcount_by_year];
    hc[yi] = Number(v) || 0;
    return { ...f, headcount_by_year: hc };
  });

  const handleSave = async () => {
    if (!form.role_title.trim()) { setErr('Role title is required.'); return; }
    setSaving(true); setErr(null);
    try {
      await onSave(form, position?.id);
      onClose();
    } catch (e) {
      setErr(e.response?.data?.detail || e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const isHourly = form.pay_type === 'hourly';

  // Live preview of year 1 loaded cost
  const previewY1 = (() => {
    const hc = form.headcount_by_year[0] || 0;
    let base = isHourly
      ? (form.hourly_rate || 0) * (form.hours_per_year || 1696) * (form.overtime_eligible ? 1 + 0.5 * (form.overtime_pct || 0) / 100 : 1)
      : (form.base_salary || 0);
    const mult = (1 + (form.fringe_pct||0)/100) * (1 + (form.burden_pct||0)/100) * (1 + (form.ga_pct||0)/100) * (1 + (form.fee_pct||0)/100);
    return base * mult * hc;
  })();

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        {isNew ? 'Add Staffing Position' : `Edit: ${position?.role_title}`}
      </DialogTitle>
      <DialogContent dividers>
        {err && <Alert severity="error" sx={{ mb: 2 }}>{err}</Alert>}

        {/* ── Section 1: Identity ── */}
        <Typography variant="subtitle2" color="primary" gutterBottom>Role Identity</Typography>
        <Grid container spacing={2} sx={{ mb: 2 }}>
          <Grid item xs={12} md={5}>
            <TextField fullWidth size="small" label="Role Title *"
              value={form.role_title} onChange={e => set('role_title', e.target.value)} />
          </Grid>
          <Grid item xs={12} md={4}>
            <FormControl fullWidth size="small">
              <InputLabel>Classification</InputLabel>
              <Select label="Classification" value={form.classification}
                onChange={e => set('classification', e.target.value)}>
                {Object.entries(CLASSIFICATION_LABELS).map(([v, l]) => (
                  <MenuItem key={v} value={v}>{l}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} md={3}>
            <FormControl fullWidth size="small">
              <InputLabel>Engagement Type</InputLabel>
              <Select label="Engagement Type" value={form.engagement_type}
                onChange={e => set('engagement_type', e.target.value)}>
                {ENGAGEMENT_OPTIONS.map(o => (
                  <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
        </Grid>

        <Divider sx={{ my: 1.5 }} />
        {/* ── Section 2: Compensation ── */}
        <Typography variant="subtitle2" color="primary" gutterBottom>Compensation</Typography>
        <Grid container spacing={2} alignItems="center" sx={{ mb: 1 }}>
          <Grid item xs={12} md={3}>
            <FormControl>
              <FormLabel sx={{ fontSize: 12 }}>Pay Type</FormLabel>
              <RadioGroup row value={form.pay_type} onChange={e => set('pay_type', e.target.value)}>
                <FormControlLabel value="hourly" control={<Radio size="small" />} label="Hourly" />
                <FormControlLabel value="salaried" control={<Radio size="small" />} label="Salaried" />
              </RadioGroup>
            </FormControl>
          </Grid>
          {isHourly ? (
            <Grid item xs={6} md={3}>
              <TextField fullWidth size="small" type="number" label="Hourly Rate ($/hr)"
                value={form.hourly_rate} onChange={e => set('hourly_rate', Number(e.target.value)||0)}
                inputProps={{ min: 0, step: 0.5 }} />
            </Grid>
          ) : (
            <Grid item xs={6} md={3}>
              <TextField fullWidth size="small" type="number" label="Annual Salary ($)"
                value={form.base_salary} onChange={e => set('base_salary', Number(e.target.value)||0)}
                inputProps={{ min: 0, step: 1000 }} />
            </Grid>
          )}
          {isHourly && (
            <Grid item xs={6} md={3}>
              <TextField fullWidth size="small" type="number" label="Hours / Year"
                value={form.hours_per_year} onChange={e => set('hours_per_year', Number(e.target.value)||1696)}
                inputProps={{ min: 0, step: 8 }}
                helperText="FT=1696 PT=1066 Field=1920" />
            </Grid>
          )}
        </Grid>

        {/* ── Section 3: Overtime ── */}
        {isHourly && (
          <Grid container spacing={2} alignItems="center" sx={{ mb: 1 }}>
            <Grid item xs={12} md={3}>
              <FormGroup>
                <FormControlLabel
                  control={<Checkbox size="small" checked={!!form.overtime_eligible}
                    onChange={e => set('overtime_eligible', e.target.checked)} />}
                  label="Eligible for Overtime"
                />
              </FormGroup>
            </Grid>
            {form.overtime_eligible && (
              <Grid item xs={6} md={3}>
                <TextField fullWidth size="small" type="number"
                  label="OT % (% of hours at 1.5×)"
                  value={form.overtime_pct}
                  onChange={e => set('overtime_pct', Number(e.target.value)||0)}
                  inputProps={{ min: 0, max: 100, step: 1 }}
                  helperText="e.g. 10 = 10% of hours at 1.5× rate" />
              </Grid>
            )}
          </Grid>
        )}

        <Divider sx={{ my: 1.5 }} />
        {/* ── Section 4: Cost Build-Up ── */}
        <Typography variant="subtitle2" color="primary" gutterBottom>Loaded Cost Build-Up</Typography>
        <Grid container spacing={2} sx={{ mb: 2 }}>
          <Grid item xs={6} md={2}>
            <TextField fullWidth size="small" type="number" label="Fringe %"
              value={form.fringe_pct} onChange={e => set('fringe_pct', Number(e.target.value)||0)}
              inputProps={{ min: 0, step: 0.5 }} />
          </Grid>
          <Grid item xs={6} md={2}>
            <TextField fullWidth size="small" type="number" label="Burden %"
              value={form.burden_pct} onChange={e => set('burden_pct', Number(e.target.value)||0)}
              inputProps={{ min: 0, step: 0.5 }} />
          </Grid>
          <Grid item xs={6} md={2}>
            <TextField fullWidth size="small" type="number" label="G&A %"
              value={form.ga_pct} onChange={e => set('ga_pct', Number(e.target.value)||0)}
              inputProps={{ min: 0, step: 0.5 }} />
          </Grid>
          <Grid item xs={6} md={2}>
            <TextField fullWidth size="small" type="number" label="Fee %"
              value={form.fee_pct} onChange={e => set('fee_pct', Number(e.target.value)||0)}
              inputProps={{ min: 0, step: 0.5 }} />
          </Grid>
          <Grid item xs={6} md={2}>
            <TextField fullWidth size="small" type="number" label="Annual Esc %"
              value={form.escalation_pct} onChange={e => set('escalation_pct', Number(e.target.value)||0)}
              inputProps={{ min: 0, step: 0.5 }} />
          </Grid>
          <Grid item xs={6} md={2}>
            <FormControl fullWidth size="small">
              <InputLabel>Allocation</InputLabel>
              <Select label="Allocation" value={form.allocation_basis}
                onChange={e => set('allocation_basis', e.target.value)}>
                <MenuItem value="CIF">CIF</MenuItem>
                <MenuItem value="PIF">PIF</MenuItem>
                <MenuItem value="SHARED">Shared</MenuItem>
              </Select>
            </FormControl>
          </Grid>
        </Grid>

        {/* Live preview */}
        <Box sx={{ mb: 2, p: 1, borderRadius: 1, backgroundColor: 'rgba(0,174,230,0.06)', display: 'flex', gap: 3 }}>
          <Typography variant="caption" color="text.secondary">
            Year 1 loaded cost preview (headcount × base × multipliers):
          </Typography>
          <Typography variant="caption" sx={{ fontWeight: 700, color: 'primary.main' }}>
            {fmtCurrency(previewY1)} / yr (for Y1 headcount = {form.headcount_by_year[0] || 0})
          </Typography>
        </Box>

        <Divider sx={{ my: 1.5 }} />
        {/* ── Section 5: Headcount by Year ── */}
        <Typography variant="subtitle2" color="primary" gutterBottom>
          Headcount by Year
          <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
            Set 0 for years this role is not active
          </Typography>
        </Typography>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
          {(form.headcount_by_year || []).map((hc, yi) => (
            <Box key={yi} sx={{ textAlign: 'center' }}>
              <Typography variant="caption" color={yi >= baseYears ? 'warning.main' : 'text.secondary'} display="block">
                Y{yi + 1}{yi >= baseYears ? '*' : ''}
              </Typography>
              <TextField
                size="small" type="number" variant="outlined"
                value={hc}
                onChange={e => setHc(yi, e.target.value)}
                sx={{ width: 64 }}
                inputProps={{ min: 0, step: 0.5, style: { textAlign: 'center', padding: '6px 4px' } }}
              />
            </Box>
          ))}
          <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'flex-end', mb: 1.5 }}>
            * extension years
          </Typography>
        </Box>

        {/* ── Section 6: Notes ── */}
        <TextField fullWidth size="small" label="Notes" multiline rows={2}
          value={form.notes || ''} onChange={e => set('notes', e.target.value)}
          placeholder="Source, assumptions, CBA reference, etc." />
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 1.5, justifyContent: 'space-between' }}>
        <Box>
          <FormControlLabel
            control={<Switch size="small" checked={!!form.included} onChange={e => set('included', e.target.checked)} />}
            label={<Typography variant="caption">Include in cost model</Typography>}
          />
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="contained" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : isNew ? 'Add Position' : 'Save Changes'}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
};

// ── Staffing Plan Tab ──────────────────────────────────────────────
const StaffingPlanTab = ({ modelId, totalYears, baseYears, positions, onRefresh }) => {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingPos, setEditingPos] = useState(null);  // null=new, object=edit
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const yearLabels = Array.from({ length: totalYears }, (_, i) => ({
    label: `Y${i + 1}`, ext: i >= baseYears,
  }));

  const handleSave = async (form, posId) => {
    const payload = {
      ...form,
      headcount_by_year: (form.headcount_by_year || []).map(Number),
    };
    if (posId) {
      await axios.put(`/api/pricing/staffing/${posId}`, payload);
    } else {
      await axios.post(`/api/pricing/models/${modelId}/staffing`, { ...payload, user_defined: true });
    }
    onRefresh();
  };

  const quickToggle = async (posId, included) => {
    try { await axios.put(`/api/pricing/staffing/${posId}`, { included }); onRefresh(); }
    catch (e) { setError('Update failed'); }
  };

  const deletePosition = async (posId) => {
    if (!window.confirm('Delete this position?')) return;
    try { await axios.delete(`/api/pricing/staffing/${posId}`); onRefresh(); }
    catch { setError('Delete failed'); }
  };

  // Aggregates
  const totalsByYear = Array(totalYears).fill(0);
  const hcByYear = Array(totalYears).fill(0);
  for (const pos of positions) {
    if (!pos.included) continue;
    (pos.annual_costs || []).forEach((c, i) => { totalsByYear[i] += c; });
    (pos.headcount_by_year || []).forEach((h, i) => { hcByYear[i] += Number(h || 0); });
  }
  const grandTotal = totalsByYear.reduce((a, b) => a + b, 0);
  const grouped = {};
  for (const pos of positions) {
    const cls = pos.classification || 'salaried_mgmt';
    if (!grouped[cls]) grouped[cls] = [];
    grouped[cls].push(pos);
  }

  return (
    <Box>
      {error && <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1 }}>{error}</Alert>}

      {/* KPIs */}
      <Grid container spacing={1} sx={{ mb: 2 }}>
        <Grid item xs={6} md={3}>
          <Card variant="outlined"><CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
            <Typography variant="caption" color="text.secondary">Total Positions</Typography>
            <Typography variant="h6">{positions.length}</Typography>
          </CardContent></Card>
        </Grid>
        <Grid item xs={6} md={3}>
          <Card variant="outlined"><CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
            <Typography variant="caption" color="text.secondary">Peak Headcount</Typography>
            <Typography variant="h6">{Math.max(...hcByYear, 0)}</Typography>
          </CardContent></Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card variant="outlined" sx={{ borderColor: 'secondary.main', backgroundColor: 'rgba(80,191,52,0.06)' }}>
            <CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
              <Typography variant="caption" color="text.secondary">Total Loaded Labor Cost</Typography>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>{fmtCurrency(grandTotal)}</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
        <Button startIcon={<AddIcon />} size="small" variant="outlined"
          onClick={() => { setEditingPos(null); setDialogOpen(true); }}>
          Add Position
        </Button>
      </Box>

      {/* Per-classification accordions */}
      {Object.entries(CLASSIFICATION_LABELS).map(([cls, clsLabel]) => {
        const rows = grouped[cls] || [];
        if (rows.length === 0) return null;
        const groupCost = rows.filter(p => p.included).reduce((a, p) => a + (p.total_cost || 0), 0);
        return (
          <Accordion key={cls} defaultExpanded sx={{ mb: 1 }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                <PeopleIcon fontSize="small" sx={{ color: CLASSIFICATION_COLORS[cls] }} />
                <Typography sx={{ fontWeight: 600, flex: 1 }}>
                  {clsLabel}
                  <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                    ({rows.length} role{rows.length !== 1 ? 's' : ''})
                  </Typography>
                </Typography>
                <Typography variant="body2" sx={{ fontWeight: 600, color: CLASSIFICATION_COLORS[cls] }}>
                  {fmtCurrency(groupCost)}
                </Typography>
              </Box>
            </AccordionSummary>
            <AccordionDetails sx={{ p: 0 }}>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ minWidth: 180 }}>Role</TableCell>
                      <TableCell>Engagement</TableCell>
                      <TableCell>Pay</TableCell>
                      <TableCell align="right">Rate</TableCell>
                      <TableCell align="right">Fringe%</TableCell>
                      <TableCell align="right">OT</TableCell>
                      {yearLabels.map((y, i) => (
                        <TableCell key={i} align="center" sx={{ minWidth: 44, p: 0.5, fontSize: 11 }}>
                          {y.label}{y.ext && '†'}
                        </TableCell>
                      ))}
                      <TableCell align="right">Total Cost</TableCell>
                      <TableCell align="center" sx={{ width: 44 }}>Inc.</TableCell>
                      <TableCell sx={{ width: 72 }}></TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {rows.map(pos => (
                      <TableRow key={pos.id} hover sx={{ opacity: pos.included ? 1 : 0.45 }}>
                        <TableCell sx={{ fontSize: 12 }}>
                          <Tooltip title={pos.notes || ''}>
                            <span>{pos.role_title}</span>
                          </Tooltip>
                        </TableCell>
                        <TableCell sx={{ fontSize: 11, color: 'text.secondary' }}>
                          {ENGAGEMENT_OPTIONS.find(o => o.value === pos.engagement_type)?.label.split(' ')[0] || pos.engagement_type}
                        </TableCell>
                        <TableCell sx={{ fontSize: 11 }}>
                          <Chip label={pos.pay_type} size="small"
                            color={pos.pay_type === 'hourly' ? 'primary' : 'default'} />
                        </TableCell>
                        <TableCell align="right" sx={{ fontSize: 11 }}>
                          {pos.pay_type === 'hourly'
                            ? `$${(pos.hourly_rate || 0).toFixed(2)}/hr`
                            : fmtCurrency(pos.base_salary)}
                        </TableCell>
                        <TableCell align="right" sx={{ fontSize: 11 }}>{fmtPct(pos.fringe_pct)}</TableCell>
                        <TableCell align="right" sx={{ fontSize: 11 }}>
                          {pos.overtime_eligible ? (
                            <Chip label={`OT ${pos.overtime_pct}%`} size="small" color="warning" />
                          ) : '—'}
                        </TableCell>
                        {(pos.headcount_by_year || Array(totalYears).fill(0)).map((hc, yi) => (
                          <TableCell key={yi} align="center" sx={{ fontSize: 11, color: hc > 0 ? 'text.primary' : 'text.disabled' }}>
                            {hc > 0 ? hc : '—'}
                          </TableCell>
                        ))}
                        <TableCell align="right" sx={{ fontWeight: 600, fontSize: 12 }}>
                          {fmtCurrency(pos.total_cost || 0)}
                        </TableCell>
                        <TableCell align="center">
                          <Switch size="small" checked={!!pos.included}
                            onChange={e => quickToggle(pos.id, e.target.checked)} />
                        </TableCell>
                        <TableCell align="center" sx={{ whiteSpace: 'nowrap' }}>
                          <Tooltip title="Edit">
                            <IconButton size="small"
                              onClick={() => { setEditingPos(pos); setDialogOpen(true); }}>
                              <EditIcon fontSize="inherit" />
                            </IconButton>
                          </Tooltip>
                          <Tooltip title="Delete">
                            <IconButton size="small" onClick={() => deletePosition(pos.id)}>
                              <DeleteIcon fontSize="inherit" />
                            </IconButton>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </AccordionDetails>
          </Accordion>
        );
      })}

      {/* Year totals footer */}
      <Card variant="outlined" sx={{ mt: 2 }}>
        <CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
          <Typography variant="subtitle2" gutterBottom>Annual Loaded Labor Cost</Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  {yearLabels.map((y, i) => (
                    <TableCell key={i} align="center" sx={{ fontSize: 11 }}>
                      {y.label}{y.ext && <Chip label="ext" size="small" sx={{ ml: 0.3, height: 14, fontSize: 9 }} />}
                    </TableCell>
                  ))}
                  <TableCell align="right"><strong>Total</strong></TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                <TableRow sx={{ backgroundColor: 'rgba(0,174,230,0.06)' }}>
                  {totalsByYear.map((v, i) => (
                    <TableCell key={i} align="center" sx={{ fontWeight: 600, fontSize: 12 }}>
                      {fmtCurrency(v)}
                    </TableCell>
                  ))}
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{fmtCurrency(grandTotal)}</TableCell>
                </TableRow>
                <TableRow>
                  {hcByYear.map((h, i) => (
                    <TableCell key={i} align="center" sx={{ color: 'text.secondary', fontSize: 11 }}>
                      {h > 0 ? `${h} FTE` : '—'}
                    </TableCell>
                  ))}
                  <TableCell />
                </TableRow>
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>

      {/* Add / Edit dialog */}
      <PositionDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        position={editingPos}
        totalYears={totalYears}
        baseYears={baseYears}
      />
    </Box>
  );
};
export default PricingModel;
