import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Grid,
  Card,
  CardContent,
  Button,
  Chip,
  LinearProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Fab,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Divider,
} from '@mui/material';
import {
  PlayArrow as PlayIcon,
  Description as DocIcon,
  People as PeopleIcon,
  CheckCircle as CheckIcon,
  CloudUpload as UploadIcon,
  Assessment as AnalysisIcon,
  Business as VendorIcon,
  Add as AddIcon,
  Search as SearchIcon,
  TrendingUp as TrendingIcon,
  Score as ScoreIcon,
  Warning as WarningIcon,
  Newspaper as NewsIcon,
  Timeline as TimelineIcon,
  Assignment as AssignmentIcon,
} from '@mui/icons-material';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  ResponsiveContainer,
  Cell,
  Tooltip as RechartsTooltip,
} from 'recharts';
import axios from 'axios';

const FUNNEL_COLORS = ['#00AEE6', '#0052CC', '#A06CD5', '#50BF34', '#FF6B6B'];

const Dashboard = () => {
  const [kpis, setKpis] = useState(null);
  const [uploadDialog, setUploadDialog] = useState(false);
  const [vendorDialog, setVendorDialog] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [vendorName, setVendorName] = useState('');
  const [loading, setLoading] = useState(false);
  // Deep vendor research result — null until the first analysis completes.
  // Kept here (not on a separate page) so capture managers can see the brief
  // inline without navigating away from the dashboard.
  const [vendorResult, setVendorResult] = useState(null);
  const [vendorError, setVendorError] = useState(null);

  useEffect(() => { fetchKpis(); }, []);

  const fetchKpis = async () => {
    try {
      const { data } = await axios.get('/api/dashboard/kpis');
      setKpis(data);
    } catch (e) {
      console.error('Dashboard KPI fetch failed', e);
      // Minimal fallback
      setKpis({
        totalDocuments: 0, totalProposals: 0, activeProposals: 0,
        activeWorkflows: 0, completedWorkflows: 0, pendingApprovals: 0,
        avgParsonsScore: 0, avgCompletion: 0,
        funnel: { drafting: 0, under_review: 0, approved: 0, submitted: 0, rejected: 0 },
        proposalsInFlight: [], heatmap: [], recentActivity: [], competitorNews: [],
      });
    }
  };

  const handleFileUpload = async () => {
    if (!selectedFile) return;
    setLoading(true);
    const formData = new FormData();
    formData.append('file', selectedFile);
    try {
      await axios.post('/api/orchestrator/upload-document', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
      setUploadDialog(false); setSelectedFile(null); fetchKpis();
    } catch (e) { console.error('Upload error', e); }
    finally { setLoading(false); }
  };

  const handleVendorAnalysis = async () => {
    if (!vendorName.trim()) return;
    setLoading(true);
    setVendorError(null);
    setVendorResult(null);
    try {
      // Deep research is expensive (multiple Opus calls + web_search + SEC/GLEIF
      // lookups); give it a generous timeout. We keep the dialog open and render
      // the result inline so the capture manager can read it immediately.
      const { data } = await axios.post(
        '/api/orchestrator/start-vendor-analysis',
        { vendorName: vendorName.trim(), analysisType: 'comprehensive' },
        { timeout: 15 * 60 * 1000 },
      );
      setVendorResult(data);
      fetchKpis();
    } catch (e) {
      console.error('Vendor analysis error', e);
      setVendorError(e?.response?.data?.detail || e?.message || 'Vendor analysis failed');
    } finally {
      setLoading(false);
    }
  };

  const closeVendorDialog = () => {
    setVendorDialog(false);
    setVendorName('');
    setVendorResult(null);
    setVendorError(null);
  };

  if (!kpis) return <Box sx={{ p: 4 }}><LinearProgress /></Box>;

  const funnelData = [
    { name: 'Drafting', value: kpis.funnel.drafting },
    { name: 'Review', value: kpis.funnel.under_review },
    { name: 'Approved', value: kpis.funnel.approved },
    { name: 'Submitted', value: kpis.funnel.submitted },
    { name: 'Rejected', value: kpis.funnel.rejected },
  ];

  // Single source of truth for the top-of-page launch bar.
  // We keep the visual treatment uniform — one brand gradient, icons do the
  // differentiating — so the row reads as one cohesive launcher band instead
  // of a carnival of five different color pairs.
  const LAUNCH_GRADIENT = 'linear-gradient(135deg, #0052CC 0%, #00AEE6 60%, #50BF34 100%)';
  const launchActions = [
    { label: 'Start New Workflow', icon: <PlayIcon />,    onClick: () => { window.location.href = '/workflows'; } },
    { label: 'Upload RFP',         icon: <UploadIcon />,  onClick: () => setUploadDialog(true) },
    { label: 'New Proposal',       icon: <AddIcon />,     onClick: () => { window.location.href = '/proposals'; } },
    { label: 'Vendor Analysis',    icon: <VendorIcon />,  onClick: () => setVendorDialog(true) },
    { label: 'Browse Documents',   icon: <SearchIcon />,  onClick: () => { window.location.href = '/documents'; } },
  ];

  // ── 4 hero stat cards (full-color, admin-template style) ──
  const statCards = [
    { icon: <DocIcon />,      value: kpis.totalProposals,     label: 'Total Proposals',    bg: 'linear-gradient(135deg, #1E88E5 0%, #42A5F5 100%)' },
    { icon: <PlayIcon />,     value: kpis.activeProposals,    label: 'Active RFPs',         bg: 'linear-gradient(135deg, #43A047 0%, #66BB6A 100%)' },
    { icon: <TrendingIcon />, value: `${kpis.avgCompletion}%`, label: 'Avg Completion',     bg: 'linear-gradient(135deg, #FB8C00 0%, #FFA726 100%)' },
    { icon: <ScoreIcon />,    value: kpis.avgParsonsScore,    label: 'Avg Score',            bg: 'linear-gradient(135deg, #E53935 0%, #EF5350 100%)' },
  ];

  // Uniform card style: every panel on the main grid opts into full-height so sibling
  // cards in the same row align at both top and bottom — no more ragged edges.
  const panelCardSx = { flex: 1, width: '100%', minHeight: 280, display: 'flex', flexDirection: 'column' };
  const panelContentSx = { flex: 1, display: 'flex', flexDirection: 'column' };

  return (
    <Box>
      {/* ── Page header + quick-launch actions ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3, flexWrap: 'wrap', gap: 1 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700, color: '#1B3349' }}>
            Dashboard
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>
            Pipeline, proposals in flight, and competitor signals at a glance.
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          {launchActions.map((a, i) => (
            <Button
              key={i}
              size="small"
              variant={i === 0 ? 'contained' : 'outlined'}
              startIcon={a.icon}
              onClick={a.onClick}
              sx={{
                borderRadius: '8px',
                textTransform: 'none',
                fontWeight: 600,
                fontSize: 13,
                ...(i === 0
                  ? { background: LAUNCH_GRADIENT, '&:hover': { filter: 'brightness(1.06)' } }
                  : { borderColor: '#c4cdd5', color: '#1B3349', '&:hover': { borderColor: '#0052CC', color: '#0052CC', bgcolor: 'rgba(0,82,204,0.04)' } }),
              }}
            >
              {a.label}
            </Button>
          ))}
        </Box>
      </Box>

      {/* ── Stat Cards (4, full-color, admin template style) ─────────────────── */}
      <Grid container spacing={2.5} sx={{ mb: 3 }}>
        {statCards.map((s, i) => (
          <Grid item xs={12} sm={6} md={3} key={i}>
            <Card
              className="metric-card"
              sx={{
                background: s.bg,
                color: '#fff',
                borderRadius: '12px',
                border: 'none',
                overflow: 'hidden',
                position: 'relative',
              }}
            >
              <CardContent sx={{ p: 2.5, pb: '20px !important' }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <Box>
                    <Typography sx={{ fontSize: 32, fontWeight: 800, lineHeight: 1.1, mb: 0.5 }}>
                      {s.value}
                    </Typography>
                    <Typography sx={{ fontSize: 13, fontWeight: 500, opacity: 0.85, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                      {s.label}
                    </Typography>
                  </Box>
                  <Box sx={{ opacity: 0.25, mt: -0.5 }}>
                    {React.cloneElement(s.icon, { sx: { fontSize: 48 } })}
                  </Box>
                </Box>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      {/* ── Row 2: Proposals in Flight + Pipeline Funnel ─────────────────────── */}
      <Grid container spacing={2.5} sx={{ mb: 2.5 }} alignItems="stretch">
        {/* ── Proposals in Flight ── */}
        <Grid item xs={12} md={7} sx={{ display: 'flex' }}>
          <Card className="card" sx={panelCardSx}>
            <CardContent sx={panelContentSx}>
              <Typography variant="h6" gutterBottom sx={{ fontWeight: 700, color: '#1B3349' }}>
                RFPs in Flight
              </Typography>
              {kpis.proposalsInFlight.length === 0 ? (
                <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Typography variant="body2" color="text.secondary">
                    No active proposals. Create one to get started.
                  </Typography>
                </Box>
              ) : (
                <TableContainer sx={{ flex: 1 }}>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell sx={{ fontWeight: 700 }}>Proposal</TableCell>
                        <TableCell sx={{ fontWeight: 700 }}>Progress</TableCell>
                        <TableCell align="center" sx={{ fontWeight: 700 }}>Score</TableCell>
                        <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {kpis.proposalsInFlight.map(p => (
                        <TableRow key={p.id} hover>
                          <TableCell>
                            <Typography variant="body2" fontWeight={600}>{p.title}</Typography>
                            {p.due_date && <Typography variant="caption" color="text.secondary">Due: {new Date(p.due_date).toLocaleDateString()}</Typography>}
                          </TableCell>
                          <TableCell sx={{ minWidth: 140 }}>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <LinearProgress variant="determinate" value={p.completion_pct}
                                sx={{ flexGrow: 1, height: 8, borderRadius: 4, bgcolor: 'rgba(0,174,230,0.15)',
                                  '& .MuiLinearProgress-bar': { bgcolor: p.completion_pct >= 80 ? '#50BF34' : '#00AEE6', borderRadius: 4 } }} />
                              <Typography variant="caption" fontWeight={600}>{p.completion_pct}%</Typography>
                            </Box>
                          </TableCell>
                          <TableCell align="center">
                            <Typography variant="body2" fontWeight={600}>{p.avg_score ?? '—'}</Typography>
                          </TableCell>
                          <TableCell align="center">
                            <Chip label={p.status} size="small" color={p.status === 'draft' ? 'default' : 'primary'} variant="outlined" />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* ── Pipeline Funnel ── */}
        <Grid item xs={12} md={5} sx={{ display: 'flex' }}>
          <Card className="card" sx={panelCardSx}>
            <CardContent sx={panelContentSx}>
              <Typography variant="h6" gutterBottom sx={{ fontWeight: 700, color: '#1B3349' }}>Pipeline Funnel</Typography>
              <Box sx={{ flex: 1, display: 'flex', alignItems: 'center' }}>
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={funnelData} layout="vertical" margin={{ left: 10, right: 20, top: 5, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" allowDecimals={false} />
                    <YAxis type="category" dataKey="name" width={80} tick={{ fontSize: 12 }} />
                    <RechartsTooltip />
                    <Bar dataKey="value" radius={[0, 6, 6, 0]}>
                      {funnelData.map((_, i) => <Cell key={i} fill={FUNNEL_COLORS[i % FUNNEL_COLORS.length]} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Box>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* ── Row 3: Recent Activity + Competitor Intelligence ─────────────────── */}
      <Grid container spacing={2.5} alignItems="stretch">
        {/* ── Recent Activity ── */}
        <Grid item xs={12} md={6} sx={{ display: 'flex' }}>
          <Card className="card" sx={panelCardSx}>
            <CardContent sx={panelContentSx}>
              <Typography variant="h6" gutterBottom sx={{ fontWeight: 700, color: '#1B3349' }}>Recent Activity</Typography>
              {kpis.recentActivity.length === 0 ? (
                <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Typography variant="body2" color="text.secondary">No recent activity</Typography>
                </Box>
              ) : (
                <List dense sx={{ flex: 1 }}>
                  {kpis.recentActivity.slice(0, 8).map((a, i) => (
                    <React.Fragment key={`${a.type}-${a.id}`}>
                      <ListItem>
                        <ListItemIcon sx={{ minWidth: 36 }}>
                          {a.type === 'workflow' ? <TimelineIcon fontSize="small" color="primary" /> : <ScoreIcon fontSize="small" sx={{ color: '#A06CD5' }} />}
                        </ListItemIcon>
                        <ListItemText primary={a.label} secondary={a.detail} primaryTypographyProps={{ variant: 'body2', fontWeight: 600 }} />
                      </ListItem>
                      {i < Math.min(kpis.recentActivity.length, 8) - 1 && <Divider variant="inset" component="li" />}
                    </React.Fragment>
                  ))}
                </List>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* ── Competitor Intelligence ── */}
        <Grid item xs={12} md={6} sx={{ display: 'flex' }}>
          <Card className="card" sx={panelCardSx}>
            <CardContent sx={panelContentSx}>
              <Typography variant="h6" gutterBottom sx={{ fontWeight: 700, color: '#1B3349' }}>
                <NewsIcon sx={{ mr: 1, verticalAlign: 'middle', color: '#FF6B6B' }} />
                Competitor Intelligence
              </Typography>
              {kpis.competitorNews.length === 0 ? (
                <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Typography variant="body2" color="text.secondary">
                    No competitor news yet. Add competitors in Settings and trigger a refresh.
                  </Typography>
                </Box>
              ) : (
                // Single-column list (4 max) so this card's visual weight matches
                // Recent Activity beside it. The previous 2×3 grid bloated the card
                // to ~635px, breaking the bottom-row alignment.
                <List dense disablePadding sx={{ flex: 1 }}>
                  {kpis.competitorNews.slice(0, 4).map((n, idx) => (
                    <React.Fragment key={n.id}>
                      {idx > 0 && <Divider component="li" />}
                      <ListItem sx={{ px: 0, py: 1, alignItems: 'flex-start' }}>
                        <ListItemIcon sx={{ minWidth: 28, mt: 0.5 }}>
                          <NewsIcon sx={{ fontSize: 18, color: '#FF6B6B' }} />
                        </ListItemIcon>
                        <ListItemText
                          primary={
                            <Typography variant="body2" fontWeight={600} sx={{ lineHeight: 1.35 }}>
                              {n.url
                                ? <a href={n.url} target="_blank" rel="noreferrer" style={{ color: '#1B3349', textDecoration: 'none' }}>{n.title}</a>
                                : n.title}
                            </Typography>
                          }
                          secondary={
                            <Typography variant="caption" color="text.secondary">
                              <strong>{n.competitor}</strong>
                              {n.source && ` · ${n.source}`}
                              {n.published_at && ` · ${new Date(n.published_at).toLocaleDateString()}`}
                            </Typography>
                          }
                        />
                      </ListItem>
                    </React.Fragment>
                  ))}
                </List>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Upload Dialog */}
      <Dialog open={uploadDialog} onClose={() => setUploadDialog(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Import Documents</DialogTitle>
        <DialogContent>
          <Box sx={{ mt: 2 }}>
            <input accept=".pdf,.doc,.docx,.txt,.xlsx,.pptx" style={{ display: 'none' }} id="doc-upload" type="file"
              onChange={(e) => setSelectedFile(e.target.files[0])} />
            <label htmlFor="doc-upload">
              <Box sx={{ cursor: 'pointer', border: '2px dashed rgba(0,174,230,0.3)', borderRadius: 4, p: 4, textAlign: 'center',
                '&:hover': { borderColor: '#00AEE6', background: 'rgba(0,174,230,0.05)' } }}>
                <UploadIcon sx={{ fontSize: 48, color: '#00AEE6', mb: 2 }} />
                <Typography variant="h6">{selectedFile ? selectedFile.name : 'Click to select a document'}</Typography>
              </Box>
            </label>
          </Box>
        </DialogContent>
        <DialogActions sx={{ p: 3 }}>
          <Button onClick={() => setUploadDialog(false)} color="inherit">Cancel</Button>
          <Button onClick={handleFileUpload} variant="contained" disabled={!selectedFile || loading}>
            {loading ? 'Uploading…' : 'Upload'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Vendor Analysis Dialog — deep research, Opus 4.7 */}
      <Dialog
        open={vendorDialog}
        onClose={() => !loading && closeVendorDialog()}
        maxWidth={vendorResult ? 'lg' : 'sm'}
        fullWidth
      >
        <DialogTitle>
          {vendorResult ? `Deep Vendor Analysis — ${vendorResult.entity_profile?.canonical_legal_name || vendorName}` : 'Deep Vendor Analysis'}
        </DialogTitle>
        <DialogContent dividers>
          {!vendorResult && (
            <Box>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Runs a Claude Opus 4.7 research loop with native <code>web_search</code>, SEC EDGAR,
                OpenCorporates, GLEIF, and (if <code>GOOGLE_PLACES_API_KEY</code> is configured)
                Google Places reviews across operating locations. It follows the ownership chain
                — historical PE owners, parents, former names — so news buried under those
                entities is not missed.
              </Typography>
              <TextField
                fullWidth
                label="Vendor Name"
                value={vendorName}
                onChange={(e) => setVendorName(e.target.value)}
                disabled={loading}
                autoFocus
                helperText="Enter the brand name; the entity-resolution pass will expand it to legal name, parents, and former names."
                sx={{ mt: 2 }}
              />
              {loading && (
                <Box sx={{ mt: 3 }}>
                  <LinearProgress sx={{ height: 8, borderRadius: 4 }} />
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                    Researching… this can take several minutes (entity resolution → deep research → synthesis).
                  </Typography>
                </Box>
              )}
              {vendorError && (
                <Box sx={{ mt: 2, p: 2, border: '1px solid #FF6B6B', borderRadius: 2, bgcolor: '#FFF5F5' }}>
                  <Typography variant="body2" color="error" fontWeight={600}>Analysis failed</Typography>
                  <Typography variant="caption" color="text.secondary">{vendorError}</Typography>
                </Box>
              )}
            </Box>
          )}

          {vendorResult && (
            <Box>
              {/* Entity profile summary */}
              <Paper variant="outlined" sx={{ p: 2, mb: 2, borderRadius: 2 }}>
                <Typography variant="overline" color="text.secondary" fontWeight={700}>Entity resolution</Typography>
                <Grid container spacing={1} sx={{ mt: 0.5 }}>
                  {[
                    ['Legal name', vendorResult.entity_profile?.canonical_legal_name],
                    ['Ticker', vendorResult.entity_profile?.ticker],
                    ['Parent', vendorResult.entity_profile?.parent_company],
                    ['Ultimate parent', vendorResult.entity_profile?.ultimate_parent],
                    ['Ownership type', vendorResult.entity_profile?.ownership_type],
                    ['Entity confidence', vendorResult.entity_profile?.entity_resolution_confidence],
                  ].map(([k, v]) => (
                    <Grid item xs={12} sm={6} md={4} key={k}>
                      <Typography variant="caption" color="text.secondary">{k}</Typography>
                      <Typography variant="body2" fontWeight={600}>{v || '—'}</Typography>
                    </Grid>
                  ))}
                </Grid>
                {(vendorResult.entity_profile?.former_names || []).length > 0 && (
                  <Box sx={{ mt: 1.5 }}>
                    <Typography variant="caption" color="text.secondary">Former names</Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
                      {vendorResult.entity_profile.former_names.map((n, i) => (
                        <Chip key={i} size="small" label={n} variant="outlined" />
                      ))}
                    </Box>
                  </Box>
                )}
                {(vendorResult.entity_profile?.known_owners_historical || []).length > 0 && (
                  <Box sx={{ mt: 1.5 }}>
                    <Typography variant="caption" color="text.secondary">Historical owners</Typography>
                    <Box sx={{ mt: 0.5 }}>
                      {vendorResult.entity_profile.known_owners_historical.map((o, i) => (
                        <Typography key={i} variant="body2" sx={{ fontSize: 13 }}>
                          • <strong>{o.owner}</strong> {o.period ? `(${o.period})` : ''}
                          {o.source_hint ? ` — ${o.source_hint}` : ''}
                        </Typography>
                      ))}
                    </Box>
                  </Box>
                )}
              </Paper>

              {/* Source availability chips */}
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
                {Object.entries(vendorResult.structured_sources_summary || {}).map(([k, v]) => (
                  <Chip
                    key={k}
                    size="small"
                    label={`${k}: ${v.available ? 'ok' : 'n/a'}`}
                    color={v.available ? 'success' : 'default'}
                    variant={v.available ? 'filled' : 'outlined'}
                    title={v.reason || ''}
                  />
                ))}
                <Chip
                  size="small"
                  label={`model: ${vendorResult.model || 'unknown'}`}
                  color="primary"
                  variant="outlined"
                />
                <Chip
                  size="small"
                  label={`citations: ${(vendorResult.citations || []).length}`}
                  variant="outlined"
                />
              </Box>

              {/* Errors (non-fatal) */}
              {(vendorResult.errors || []).length > 0 && (
                <Box sx={{ p: 1.5, mb: 2, border: '1px solid #F0C14B', borderRadius: 2, bgcolor: '#FFF9E6' }}>
                  <Typography variant="caption" fontWeight={700} color="warning.main">
                    Partial result — some passes failed:
                  </Typography>
                  {vendorResult.errors.map((e, i) => (
                    <Typography key={i} variant="caption" display="block" sx={{ ml: 1 }}>• {e}</Typography>
                  ))}
                </Box>
              )}

              {/* Synthesis brief */}
              {vendorResult.synthesis_markdown && (
                <Paper variant="outlined" sx={{ p: 2, mb: 2, borderRadius: 2, maxHeight: 420, overflow: 'auto' }}>
                  <Typography variant="overline" color="text.secondary" fontWeight={700}>Capture brief</Typography>
                  <Box sx={{ mt: 1, '& pre': { whiteSpace: 'pre-wrap', fontSize: 13 } }}>
                    <pre>{vendorResult.synthesis_markdown}</pre>
                  </Box>
                </Paper>
              )}

              {/* Top findings roll-up */}
              {vendorResult.findings && (
                <Grid container spacing={2}>
                  {[
                    ['Ownership history', vendorResult.findings.ownership_history, (x) => `${x.period || ''} — ${x.owner || ''} (${x.type || ''})`],
                    ['Financial signals', vendorResult.findings.financial_signals, (x) => `[${x.severity}] ${x.date || ''} — ${x.signal}`],
                    ['Leadership changes', vendorResult.findings.leadership_changes, (x) => `${x.date || ''} — ${x.person} ${x.change} ${x.role}`],
                    ['Contracts won/lost', vendorResult.findings.contracts_won_lost, (x) => `${x.date || ''} [${x.outcome}] ${x.customer}: ${x.contract}`],
                    ['Regulatory / legal', vendorResult.findings.regulatory_or_legal, (x) => `${x.date || ''} [${x.severity}] ${x.action} (${x.agency_or_court})`],
                    ['Risks for Parsons', vendorResult.findings.risks_for_parsons, (x) => `[${x.likelihood}] ${x.risk} — ${x.impact_on_bid}`],
                  ].map(([title, rows, fmt]) => (
                    (rows && rows.length > 0) && (
                      <Grid item xs={12} md={6} key={title}>
                        <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, height: '100%' }}>
                          <Typography variant="overline" color="text.secondary" fontWeight={700}>{title}</Typography>
                          <Box sx={{ mt: 1 }}>
                            {rows.slice(0, 8).map((r, i) => (
                              <Typography key={i} variant="body2" sx={{ fontSize: 13, mb: 0.5 }}>
                                • {fmt(r)}
                              </Typography>
                            ))}
                            {rows.length > 8 && (
                              <Typography variant="caption" color="text.secondary">
                                …and {rows.length - 8} more
                              </Typography>
                            )}
                          </Box>
                        </Paper>
                      </Grid>
                    )
                  ))}

                  {/* Public reputation (Google Places) */}
                  {vendorResult.findings.public_reputation && (
                    <Grid item xs={12}>
                      <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>
                        <Typography variant="overline" color="text.secondary" fontWeight={700}>
                          Public reputation (Google Places)
                        </Typography>
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          <strong>Overall avg rating:</strong> {vendorResult.findings.public_reputation.overall_avg_rating ?? '—'}
                          {' · '}
                          <strong>Reviews observed:</strong> {vendorResult.findings.public_reputation.total_reviews_observed ?? '—'}
                        </Typography>
                        {(vendorResult.findings.public_reputation.recurring_complaints || []).length > 0 && (
                          <Box sx={{ mt: 1 }}>
                            <Typography variant="caption" fontWeight={700}>Recurring complaints</Typography>
                            {vendorResult.findings.public_reputation.recurring_complaints.slice(0, 6).map((c, i) => (
                              <Typography key={i} variant="body2" sx={{ fontSize: 13 }}>
                                • [{c.frequency}] <strong>{c.theme}</strong> — {c.operational_implication}
                              </Typography>
                            ))}
                          </Box>
                        )}
                        {(vendorResult.findings.public_reputation.state_or_region_patterns || []).length > 0 && (
                          <Box sx={{ mt: 1 }}>
                            <Typography variant="caption" fontWeight={700}>Regional patterns</Typography>
                            {vendorResult.findings.public_reputation.state_or_region_patterns.slice(0, 8).map((p, i) => (
                              <Typography key={i} variant="body2" sx={{ fontSize: 13 }}>
                                • <strong>{p.region}</strong>: avg {p.avg_rating} (n={p.sample_size}) — {(p.dominant_themes || []).join(', ')}
                              </Typography>
                            ))}
                          </Box>
                        )}
                      </Paper>
                    </Grid>
                  )}

                  {/* Collection gaps */}
                  {(vendorResult.findings.collection_gaps || []).length > 0 && (
                    <Grid item xs={12}>
                      <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, bgcolor: '#FFF9E6' }}>
                        <Typography variant="overline" color="text.secondary" fontWeight={700}>Collection gaps</Typography>
                        <Box sx={{ mt: 1 }}>
                          {vendorResult.findings.collection_gaps.map((g, i) => (
                            <Typography key={i} variant="body2" sx={{ fontSize: 13, mb: 0.5 }}>• {g}</Typography>
                          ))}
                        </Box>
                      </Paper>
                    </Grid>
                  )}
                </Grid>
              )}

              {/* Citations list */}
              {(vendorResult.citations || []).length > 0 && (
                <Paper variant="outlined" sx={{ p: 1.5, mt: 2, borderRadius: 2, maxHeight: 260, overflow: 'auto' }}>
                  <Typography variant="overline" color="text.secondary" fontWeight={700}>
                    Citations ({vendorResult.citations.length})
                  </Typography>
                  <Box sx={{ mt: 1 }}>
                    {vendorResult.citations.map((c, i) => (
                      <Typography key={i} variant="caption" display="block" sx={{ mb: 0.5 }}>
                        {i + 1}.{' '}
                        <a href={c.url} target="_blank" rel="noreferrer" style={{ color: '#0052CC' }}>
                          {c.title || c.url}
                        </a>
                      </Typography>
                    ))}
                  </Box>
                </Paper>
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ p: 3 }}>
          <Button onClick={closeVendorDialog} color="inherit" disabled={loading}>
            {vendorResult ? 'Close' : 'Cancel'}
          </Button>
          {!vendorResult && (
            <Button onClick={handleVendorAnalysis} variant="contained" disabled={!vendorName.trim() || loading}>
              {loading ? 'Researching…' : 'Run Deep Analysis'}
            </Button>
          )}
          {vendorResult && (
            <Button
              onClick={() => { setVendorResult(null); setVendorError(null); setVendorName(''); }}
              variant="outlined"
            >
              Run another
            </Button>
          )}
        </DialogActions>
      </Dialog>

      {/* FAB */}
      <Tooltip title="Start New Workflow" placement="left">
        <Fab color="primary" onClick={() => { window.location.href = '/workflows'; }}
          sx={{ position: 'fixed', bottom: 24, right: 24,
            background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)',
            '&:hover': { background: 'linear-gradient(135deg, #50BF34 0%, #00AEE6 100%)', transform: 'scale(1.1)' },
            boxShadow: '0 8px 25px rgba(0,174,230,0.3)', transition: 'all 0.25s ease' }}>
          <AddIcon />
        </Fab>
      </Tooltip>
    </Box>
  );
};

export default Dashboard;