/**
 * ParsonsKnowledge
 *
 * Top-level workspace for collecting "what Parsons is and does." Two
 * scopes:
 *   • Global Parsons Library — visible to every proposal (past proposals,
 *     SOPs, certs, capability statements, case studies, pricing history)
 *   • Per-RFP industry-specific — same shape, but only visible inside the
 *     specified proposal. Useful when a proposal-specific reference doc
 *     shouldn't pollute the global library.
 *
 * The page mirrors the polished aesthetic of PortfolioDashboard: animated
 * KPI strip, tab-based scope switcher, drag-drop upload card, table of
 * documents with category chips and inline edit, plus a small "Manage
 * categories" admin drawer for adding new buckets.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Chip, Button, Stack, Tooltip,
  IconButton, Tabs, Tab, Tooltip as MuiTooltip, Alert, CircularProgress,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, MenuItem,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Drawer, Stack as MuiStack, Divider, Grow, Fade, LinearProgress,
} from '@mui/material';
import {
  Add as AddIcon,
  CloudUpload as UploadIcon,
  Refresh as RefreshIcon,
  Description as DocIcon,
  MenuBook as MenuBookIcon,
  Campaign as CampaignIcon,
  VerifiedUser as VerifiedUserIcon,
  WorkspacePremium as PremiumIcon,
  AttachMoney as MoneyIcon,
  People as PeopleIcon,
  FolderOpen as FolderOpenIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  Public as GlobalIcon,
  Article as ArticleIcon,
  Settings as SettingsIcon,
  CheckCircle as CheckIcon,
} from '@mui/icons-material';
import axios from 'axios';
import { useProposal } from '../proposal/ProposalContext';
import ParsonsDocAccordion from '../components/ParsonsDocAccordion';
import ParsonsUploadWizard from '../components/ParsonsUploadWizard';

// Map the icon_hint string from the DB to an actual MUI icon component.
const ICON_LOOKUP = {
  Description: DocIcon,
  Campaign: CampaignIcon,
  MenuBook: MenuBookIcon,
  VerifiedUser: VerifiedUserIcon,
  WorkspacePremium: PremiumIcon,
  AttachMoney: MoneyIcon,
  People: PeopleIcon,
  FolderOpen: FolderOpenIcon,
  Article: ArticleIcon,
};

function CategoryIcon({ slug, categories, sx }) {
  const cat = categories.find(c => c.slug === slug);
  const IconCmp = (cat && ICON_LOOKUP[cat.icon_hint]) || ArticleIcon;
  return <IconCmp sx={sx} />;
}

// Animated counter (matches the portfolio aesthetic).
function CountUp({ value = 0, duration = 700 }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    let raf;
    const start = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setN(Math.round((value || 0) * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return <span>{n.toLocaleString()}</span>;
}

// ── Upload dialog ────────────────────────────────────────────────────
function UploadDialog({ open, onClose, onUploaded, categories, scopeProposalId }) {
  const [file, setFile] = useState(null);
  const [parsonsCategory, setParsonsCategory] = useState('past_proposal');
  const [description, setDescription] = useState('');
  const [practiceArea, setPracticeArea] = useState('');
  const [jurisdictions, setJurisdictions] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const reset = () => {
    setFile(null); setParsonsCategory('past_proposal');
    setDescription(''); setPracticeArea(''); setJurisdictions('');
    setError(null);
  };

  const close = () => { if (!submitting) { reset(); onClose && onClose(); } };

  const submit = async () => {
    if (!file) return;
    setSubmitting(true); setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('parsons_category', parsonsCategory);
      if (description.trim()) fd.append('description', description.trim());
      if (practiceArea.trim()) fd.append('practice_area', practiceArea.trim());
      const jurList = jurisdictions.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
      if (jurList.length) fd.append('jurisdictions', JSON.stringify(jurList));
      if (scopeProposalId) fd.append('scope_proposal_id', String(scopeProposalId));
      const { data } = await axios.post('/api/parsons-knowledge/documents', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      onUploaded && onUploaded(data);
      reset();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Upload failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={close} maxWidth="sm" fullWidth>
      <DialogTitle>
        Add Parsons Knowledge {scopeProposalId ? '(scoped to this RFP)' : '(global)'}
      </DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {submitting && <LinearProgress sx={{ mb: 2 }} />}
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Box sx={{
            border: '2px dashed rgba(0,174,230,0.4)', borderRadius: 2,
            p: 3, textAlign: 'center',
            bgcolor: file ? 'rgba(46,125,50,0.04)' : 'rgba(0,174,230,0.04)',
          }}>
            {file ? (
              <Stack spacing={0.5} alignItems="center">
                <CheckIcon color="success" />
                <Typography variant="body2" fontWeight={700}>{file.name}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {(file.size / 1024 / 1024).toFixed(2)} MB
                </Typography>
                <Button size="small" onClick={() => setFile(null)}>Replace</Button>
              </Stack>
            ) : (
              <>
                <UploadIcon sx={{ fontSize: 36, color: 'rgba(0,174,230,0.6)', mb: 1 }} />
                <Typography variant="body2" sx={{ mb: 1 }}>
                  Drop file or click to browse
                </Typography>
                <Button variant="outlined" component="label" startIcon={<UploadIcon />}>
                  Choose file
                  <input
                    type="file" hidden
                    accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx,.xls,.pptx,.ppt"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                  />
                </Button>
              </>
            )}
          </Box>
          <TextField
            select fullWidth label="Category" required
            value={parsonsCategory}
            onChange={(e) => setParsonsCategory(e.target.value)}
          >
            {categories.map(c => (
              <MenuItem key={c.slug} value={c.slug}>{c.label}</MenuItem>
            ))}
          </TextField>
          <TextField
            fullWidth label="Description"
            value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="Short description — why this doc matters for capture"
            multiline rows={2}
          />
          <Stack direction="row" spacing={2}>
            <TextField
              fullWidth label="Practice area"
              value={practiceArea} onChange={(e) => setPracticeArea(e.target.value)}
              placeholder="e.g. vehicle inspection"
              helperText="Free text — used to filter relevance"
            />
            <TextField
              fullWidth label="Jurisdictions"
              value={jurisdictions} onChange={(e) => setJurisdictions(e.target.value)}
              placeholder="NJ, MD, AZ"
              helperText="Comma-separated state/country codes"
            />
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={close} disabled={submitting}>Cancel</Button>
        <Button
          variant="contained"
          startIcon={<UploadIcon />}
          onClick={submit}
          disabled={!file || submitting}
        >
          {submitting ? 'Uploading…' : 'Upload'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ── Manage-categories drawer ────────────────────────────────────────
function CategoryAdmin({ open, onClose, categories, onChanged }) {
  const [newLabel, setNewLabel] = useState('');
  const [newSlug, setNewSlug] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const create = async () => {
    if (!newLabel.trim()) return;
    setSubmitting(true); setError(null);
    try {
      await axios.post('/api/parsons-knowledge/categories', {
        label: newLabel.trim(),
        slug: newSlug.trim() || undefined,
      });
      setNewLabel(''); setNewSlug('');
      onChanged && onChanged();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to create');
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (slug) => {
    if (!window.confirm(`Delete category "${slug}"?`)) return;
    try {
      await axios.delete(`/api/parsons-knowledge/categories/${encodeURIComponent(slug)}`);
      onChanged && onChanged();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Delete failed');
    }
  };

  return (
    <Drawer anchor="right" open={open} onClose={onClose}>
      <Box sx={{ width: 380, p: 3 }}>
        <Typography variant="h6" fontWeight={700} gutterBottom>
          Manage Categories
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          System-defined buckets are protected. Add your own for organization-
          specific content types (e.g. "lessons learned").
        </Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

        <Stack spacing={1.5} sx={{ mb: 3 }}>
          <TextField
            label="New category label" fullWidth
            value={newLabel} onChange={(e) => setNewLabel(e.target.value)}
          />
          <TextField
            label="Slug (optional)" fullWidth
            value={newSlug} onChange={(e) => setNewSlug(e.target.value)}
            helperText="Auto-generated from the label if blank"
          />
          <Button
            variant="contained" startIcon={<AddIcon />}
            onClick={create} disabled={submitting || !newLabel.trim()}
          >
            Add category
          </Button>
        </Stack>

        <Divider sx={{ mb: 2 }} />
        <Typography variant="overline" color="text.secondary">Existing</Typography>
        <Stack spacing={1} sx={{ mt: 1 }}>
          {categories.map(c => (
            <Stack key={c.slug} direction="row" alignItems="center" spacing={1}>
              <CategoryIcon slug={c.slug} categories={categories}
                            sx={{ fontSize: 18, color: 'text.secondary' }} />
              <Box sx={{ flex: 1 }}>
                <Typography variant="body2" fontWeight={600}>{c.label}</Typography>
                <Typography variant="caption" color="text.secondary">{c.slug}</Typography>
              </Box>
              {c.is_system ? (
                <Chip size="small" label="system" />
              ) : (
                <IconButton size="small" onClick={() => remove(c.slug)}>
                  <DeleteIcon fontSize="small" />
                </IconButton>
              )}
            </Stack>
          ))}
        </Stack>
      </Box>
    </Drawer>
  );
}

// ── Page ─────────────────────────────────────────────────────────────
export default function ParsonsKnowledge() {
  const { proposalId, proposal } = useProposal();
  const [tab, setTab] = useState(0); // 0 = Global, 1 = This RFP
  const [categories, setCategories] = useState([]);
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [filterCat, setFilterCat] = useState('');

  const loadCategories = async () => {
    try {
      const { data } = await axios.get('/api/parsons-knowledge/categories');
      setCategories(data?.categories || []);
    } catch { /* ignore — non-fatal */ }
  };

  const loadDocs = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (tab === 0) {
        params.scope = 'global';
      } else if (proposalId) {
        params.scope = 'proposal';
        params.proposal_id = proposalId;
      } else {
        setDocs([]); setLoading(false); return;
      }
      const { data } = await axios.get('/api/parsons-knowledge/documents', { params });
      setDocs(data?.documents || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadCategories(); }, []);
  // loadDocs is intentionally omitted — it's recreated every render and
  // would cause an infinite fetch loop. We re-fetch on tab/proposal change.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadDocs(); }, [tab, proposalId]);

  const filteredDocs = useMemo(
    () => filterCat ? docs.filter(d => d.parsons_category === filterCat) : docs,
    [docs, filterCat]
  );

  // Per-category counts for the filter strip.
  const countsByCategory = useMemo(() => {
    const m = {};
    for (const d of docs) {
      m[d.parsons_category] = (m[d.parsons_category] || 0) + 1;
    }
    return m;
  }, [docs]);

  const totalChunks = useMemo(
    () => docs.reduce((s, d) => s + (d.total_chunks || 0), 0), [docs]
  );

  const onDelete = async (doc) => {
    if (!window.confirm(`Delete "${doc.original_filename}"? This removes all its chunks.`)) return;
    try {
      await axios.delete(`/api/parsons-knowledge/documents/${doc.id}`);
      loadDocs();
    } catch (e) {
      alert(e?.response?.data?.detail || 'Delete failed');
    }
  };

  return (
    <Fade in timeout={400}>
      <Box>
        <Stack direction="row" alignItems="center" sx={{ mb: 3 }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h4" fontWeight={800}>Parsons Knowledge</Typography>
            <Typography variant="body2" color="text.secondary">
              The library the AI uses to substantiate Parsons responses.
              Two layers: a global library and per-RFP industry-specific docs.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Tooltip title="Manage categories">
              <IconButton onClick={() => setAdminOpen(true)}>
                <SettingsIcon />
              </IconButton>
            </Tooltip>
            <IconButton onClick={loadDocs} title="Refresh"><RefreshIcon /></IconButton>
            <Button
              variant="contained" startIcon={<AddIcon />}
              onClick={() => setUploadOpen(true)}
              sx={{ borderRadius: 3,
                    background: 'linear-gradient(135deg,#00AEE6 0%,#50BF34 100%)' }}
            >
              Add Knowledge
            </Button>
          </Stack>
        </Stack>

        {/* KPI strip */}
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {[
            { label: 'Documents', v: docs.length, c: '#00AEE6', icon: <DocIcon /> },
            { label: 'Chunks indexed', v: totalChunks, c: '#0288d1', icon: <ArticleIcon /> },
            { label: 'Categories', v: categories.length, c: '#7b1fa2', icon: <FolderOpenIcon /> },
            { label: tab === 0 ? 'Global library' : (proposal?.title?.slice(0, 30) || 'No RFP scope'),
              v: tab === 0 ? '🌐' : (proposalId || '—'), c: '#2e7d32',
              icon: tab === 0 ? <GlobalIcon /> : <ArticleIcon />, isStr: true },
          ].map((k, i) => (
            <Grid item xs={6} md={3} key={k.label}>
              <Grow in timeout={300 + i * 100}>
                <Card sx={{
                  borderRadius: 3,
                  background: `linear-gradient(135deg, #fff, ${k.c}11)`,
                  border: `1px solid ${k.c}33`,
                }}>
                  <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 2.5 }}>
                    <Box sx={{
                      width: 48, height: 48, borderRadius: 2,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: k.c, color: '#fff',
                    }}>{k.icon}</Box>
                    <Box>
                      <Typography variant="caption" color="text.secondary"
                                  sx={{ textTransform: 'uppercase', letterSpacing: 0.5 }}>
                        {k.label}
                      </Typography>
                      <Typography variant="h5" fontWeight={800} noWrap>
                        {k.isStr ? k.v : <CountUp value={k.v} />}
                      </Typography>
                    </Box>
                  </CardContent>
                </Card>
              </Grow>
            </Grid>
          ))}
        </Grid>

        {/* Scope tabs */}
        <Card sx={{ mb: 2, borderRadius: 3 }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}
                sx={{ borderBottom: '1px solid rgba(0,0,0,0.06)' }}>
            <Tab icon={<GlobalIcon />} iconPosition="start"
                 label="Global Parsons Library" />
            <Tab icon={<ArticleIcon />} iconPosition="start"
                 label={proposal?.title ? `This RFP: ${proposal.title.slice(0, 40)}` : 'This RFP'}
                 disabled={!proposalId} />
          </Tabs>
        </Card>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

        {/* Category filter strip */}
        {!loading && docs.length > 0 && (
          <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: 'wrap', gap: 1 }}>
            <Chip
              label={`All (${docs.length})`}
              clickable
              color={filterCat === '' ? 'primary' : 'default'}
              variant={filterCat === '' ? 'filled' : 'outlined'}
              onClick={() => setFilterCat('')}
            />
            {categories
              .filter(c => countsByCategory[c.slug])
              .map(c => (
                <Chip
                  key={c.slug}
                  icon={<CategoryIcon slug={c.slug} categories={categories}
                                       sx={{ fontSize: 16 }} />}
                  label={`${c.label} (${countsByCategory[c.slug]})`}
                  clickable
                  color={filterCat === c.slug ? 'primary' : 'default'}
                  variant={filterCat === c.slug ? 'filled' : 'outlined'}
                  onClick={() => setFilterCat(c.slug)}
                />
              ))}
          </Stack>
        )}

        {/* Document table */}
        {loading ? (
          <Box sx={{ p: 6, textAlign: 'center' }}><CircularProgress /></Box>
        ) : filteredDocs.length === 0 ? (
          <Card sx={{ p: 4, textAlign: 'center', borderRadius: 3 }}>
            <Typography variant="h6" gutterBottom>
              {tab === 0
                ? 'No global Parsons documents yet'
                : 'No proposal-scoped Parsons docs yet'}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Click "Add Knowledge" to upload past proposals, capability statements,
              SOPs, certs, case studies, or any reference material the AI should
              draw from when responding to RFP requirements.
            </Typography>
            <Button variant="contained" startIcon={<AddIcon />}
                    onClick={() => setUploadOpen(true)}>
              Upload first document
            </Button>
          </Card>
        ) : (
          // Each document is its own expandable accordion. Click to expand
          // and you get: a quality dial + headline, a Reassess button, a
          // semantic-search bar over the doc's chunks, a suggestions panel
          // with accept/edit/reject/ignore actions, and the chunk content.
          <Box>
            {filteredDocs.map((d) => (
              <ParsonsDocAccordion
                key={d.id}
                doc={d}
                categoryLabel={categories.find(c => c.slug === d.parsons_category)?.label
                               || d.parsons_category}
                onChanged={loadDocs}
                onDelete={onDelete}
              />
            ))}
          </Box>
        )}

        <ParsonsUploadWizard
          open={uploadOpen}
          onClose={() => setUploadOpen(false)}
          onComplete={() => { loadDocs(); }}
          categories={categories}
          scopeProposalId={tab === 1 ? proposalId : null}
        />
        <CategoryAdmin
          open={adminOpen}
          onClose={() => setAdminOpen(false)}
          categories={categories}
          onChanged={loadCategories}
        />
      </Box>
    </Fade>
  );
}
