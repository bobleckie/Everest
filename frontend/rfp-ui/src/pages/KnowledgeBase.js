import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Button, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Fade, MenuItem, Select, FormControl, InputLabel, Checkbox, ListItemText,
  OutlinedInput, Stack, Link as MuiLink,
} from '@mui/material';
import {
  CloudUpload as UploadIcon, Search as SearchIcon,
} from '@mui/icons-material';
import axios from 'axios';

const READINESS_COLORS = (score) => {
  if (score >= 70) return '#50BF34';
  if (score >= 40) return '#FFB020';
  if (score > 0) return '#FF6B6B';
  return '#E0E0E0';
};

const KnowledgeBase = () => {
  const [readiness, setReadiness] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [facts, setFacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searchScope, setSearchScope] = useState(null); // echoed back from API for display
  const [searching, setSearching] = useState(false);
  const [competitors, setCompetitors] = useState([]);

  // Scope picker state
  // scopeMode: 'all' | 'parsons' | 'rfp' | 'competitor_foia' | 'competitor_proposal' | 'custom'
  const [scopeMode, setScopeMode] = useState('all');
  const [scopeDocIds, setScopeDocIds] = useState([]); // only used when scopeMode === 'custom'
  const [topK, setTopK] = useState(15);

  // Upload state
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadSource, setUploadSource] = useState('parsons');
  const [uploadCompetitor, setUploadCompetitor] = useState('');
  const [uploadDesc, setUploadDesc] = useState('');
  const [uploading, setUploading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [readRes, docsRes, factsRes, compRes] = await Promise.all([
        axios.get('/api/knowledge/readiness'),
        axios.get('/api/documents'),
        axios.get('/api/knowledge/facts', { params: { limit: 50 } }),
        axios.get('/api/competitors'),
      ]);
      setReadiness(readRes.data);
      setDocuments(docsRes.data.documents || []);
      setFacts(factsRes.data.facts || []);
      setCompetitors(compRes.data.competitors || []);
    } catch (e) { console.error('Failed to load KB data', e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleUpload = async () => {
    if (!selectedFile) return;
    setUploading(true);
    const fd = new FormData();
    fd.append('file', selectedFile);
    fd.append('source_type', uploadSource);
    if (uploadCompetitor) fd.append('competitor_id', uploadCompetitor);
    if (uploadDesc) fd.append('description', uploadDesc);
    try {
      await axios.post('/api/documents/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      setUploadOpen(false); setSelectedFile(null); setUploadDesc(''); fetchData();
    } catch (e) { alert(e.response?.data?.detail || 'Upload failed'); }
    finally { setUploading(false); }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    const params = { q: searchQuery, top_k: topK };
    if (scopeMode === 'parsons') params.source_type = 'parsons';
    else if (scopeMode === 'rfp') params.source_type = 'rfp';
    else if (scopeMode === 'competitor_foia') params.source_type = 'competitor_foia';
    else if (scopeMode === 'competitor_proposal') params.source_type = 'competitor_proposal';
    else if (scopeMode === 'custom' && scopeDocIds.length > 0) {
      params.document_ids = scopeDocIds.join(',');
    }
    try {
      const { data } = await axios.get('/api/knowledge/search', { params });
      setSearchResults(data.results || []);
      setSearchScope(data.scope || null);
    } catch (e) {
      setSearchResults([]);
      setSearchScope(null);
    } finally {
      setSearching(false);
    }
  };

  // When user picks a scope mode other than 'custom' we clear the custom doc list
  // so it doesn't silently get re-applied the next time they switch back.
  const handleScopeModeChange = (mode) => {
    setScopeMode(mode);
    if (mode !== 'custom') setScopeDocIds([]);
  };

  const selectAllDocsForCurrentFilter = (filterFn) => {
    setScopeMode('custom');
    setScopeDocIds(documents.filter(filterFn).map(d => d.id));
  };

  // Sorted + grouped document list for the multi-select picker
  const documentsSorted = useMemo(() => {
    return [...documents].sort((a, b) => {
      const sa = a.source_type || '';
      const sb = b.source_type || '';
      if (sa !== sb) return sa.localeCompare(sb);
      return (a.filename || '').localeCompare(b.filename || '');
    });
  }, [documents]);

  if (loading) return <Box sx={{ p: 4 }}><LinearProgress /></Box>;

  const sections = readiness?.sections || [];
  const totals = readiness?.totals || {};

  return (
    <Fade in timeout={600}>
      <Box>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
          <Button variant="contained" startIcon={<UploadIcon />} onClick={() => setUploadOpen(true)}
            sx={{ borderRadius: 3, background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)' }}>
            Upload Document
          </Button>
        </Box>

        {/* Readiness Overview */}
        <Card className="card" sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" fontWeight={700} gutterBottom>Section Readiness</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              How much material exists for each RFP section. Upload more documents to improve coverage.
            </Typography>
            <Grid container spacing={1}>
              {sections.map(s => (
                <Grid item xs={6} sm={4} md={3} key={s.section_id}>
                  <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, borderLeft: `4px solid ${READINESS_COLORS(s.readiness_score)}` }}>
                    <Typography variant="body2" fontWeight={600} noWrap
                                sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.title}</Typography>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
                      <LinearProgress variant="determinate" value={s.readiness_score}
                        sx={{ flexGrow: 1, height: 6, borderRadius: 3, bgcolor: '#F0F0F0',
                          '& .MuiLinearProgress-bar': { bgcolor: READINESS_COLORS(s.readiness_score), borderRadius: 3 } }} />
                      <Typography variant="caption" fontWeight={700}>{s.readiness_score}%</Typography>
                    </Box>
                    <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
                      {s.parsons_chunks > 0 && <Chip label={`${s.parsons_chunks} Parsons`} size="small" color="primary" variant="outlined" sx={{ fontSize: 10 }} />}
                      {s.competitor_chunks > 0 && <Chip label={`${s.competitor_chunks} Comp`} size="small" color="error" variant="outlined" sx={{ fontSize: 10 }} />}
                      {s.rfp_chunks > 0 && <Chip label={`${s.rfp_chunks} RFP`} size="small" color="success" variant="outlined" sx={{ fontSize: 10 }} />}
                      {s.parsons_chunks === 0 && s.competitor_chunks === 0 && s.rfp_chunks === 0 && (
                        <Chip label="No data" size="small" variant="outlined" sx={{ fontSize: 10 }} />
                      )}
                    </Box>
                  </Paper>
                </Grid>
              ))}
            </Grid>
          </CardContent>
        </Card>

        {/* Totals */}
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {[
            { label: 'Parsons Chunks', value: totals.parsons_chunks, color: '#00AEE6' },
            { label: 'Competitor Chunks', value: totals.competitor_chunks, color: '#FF6B6B' },
            { label: 'RFP Chunks', value: totals.rfp_chunks, color: '#50BF34' },
            { label: 'Requirements', value: totals.total_requirements, color: '#A06CD5' },
            { label: 'Parsons Facts', value: totals.parsons_facts, color: '#00AEE6' },
            { label: 'Competitor Facts', value: totals.competitor_facts, color: '#FF6B6B' },
          ].map((t, i) => (
            <Grid item xs={4} sm={2} key={i}>
              <Card sx={{ textAlign: 'center', py: 1 }}>
                <CardContent sx={{ p: '8px !important' }}>
                  <Typography variant="h5" fontWeight={700} sx={{ color: t.color }}>{t.value || 0}</Typography>
                  <Typography variant="caption" color="text.secondary">{t.label}</Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>

        {/* Semantic Search */}
        <Card className="card" sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" fontWeight={700} gutterBottom>Semantic Search</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Search by meaning. Choose a scope to restrict the search to a subset of documents —
              useful when you need to isolate the RFP itself, a single competitor's proposals,
              or a hand-picked set of files.
            </Typography>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ mb: 2 }} alignItems={{ md: 'center' }}>
              <FormControl size="small" sx={{ minWidth: 200 }}>
                <InputLabel>Scope</InputLabel>
                <Select
                  value={scopeMode}
                  label="Scope"
                  onChange={(e) => handleScopeModeChange(e.target.value)}
                >
                  <MenuItem value="all">All documents ({documents.length})</MenuItem>
                  <MenuItem value="parsons">Parsons internal only</MenuItem>
                  <MenuItem value="rfp">Active RFP only</MenuItem>
                  <MenuItem value="competitor_foia">Competitor FOIAs only</MenuItem>
                  <MenuItem value="competitor_proposal">Competitor proposals only</MenuItem>
                  <MenuItem value="custom">Custom — pick documents…</MenuItem>
                </Select>
              </FormControl>

              {scopeMode === 'custom' && (
                <FormControl size="small" sx={{ minWidth: 260, flex: 1 }}>
                  <InputLabel>Documents ({scopeDocIds.length} selected)</InputLabel>
                  <Select
                    multiple
                    value={scopeDocIds}
                    onChange={(e) => setScopeDocIds(
                      typeof e.target.value === 'string' ? e.target.value.split(',').map(Number) : e.target.value
                    )}
                    input={<OutlinedInput label={`Documents (${scopeDocIds.length} selected)`} />}
                    renderValue={(selected) => {
                      if (selected.length === 0) return 'Select documents…';
                      if (selected.length === 1) {
                        const d = documents.find(x => x.id === selected[0]);
                        return d ? d.filename : `Doc ${selected[0]}`;
                      }
                      return `${selected.length} documents`;
                    }}
                    MenuProps={{ PaperProps: { style: { maxHeight: 400 } } }}
                  >
                    {documentsSorted.map(d => (
                      <MenuItem key={d.id} value={d.id}>
                        <Checkbox checked={scopeDocIds.indexOf(d.id) > -1} />
                        <ListItemText
                          primary={d.filename}
                          secondary={`${(d.source_type || 'unknown').replace('_', ' ')} · ${d.total_chunks || 0} chunks`}
                        />
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}

              <FormControl size="small" sx={{ minWidth: 110 }}>
                <InputLabel>Top-K</InputLabel>
                <Select value={topK} label="Top-K" onChange={(e) => setTopK(parseInt(e.target.value, 10))}>
                  {[5, 10, 15, 25, 50].map(k => <MenuItem key={k} value={k}>{k}</MenuItem>)}
                </Select>
              </FormControl>
            </Stack>

            {scopeMode === 'custom' && (
              <Box sx={{ mb: 2, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                <MuiLink component="button" variant="caption" onClick={() => setScopeDocIds(documents.map(d => d.id))}>Select all</MuiLink>
                <Typography variant="caption" color="text.secondary">·</Typography>
                <MuiLink component="button" variant="caption" onClick={() => setScopeDocIds([])}>Clear</MuiLink>
                <Typography variant="caption" color="text.secondary">·</Typography>
                <MuiLink component="button" variant="caption" onClick={() => selectAllDocsForCurrentFilter(d => d.source_type === 'parsons')}>All Parsons</MuiLink>
                <Typography variant="caption" color="text.secondary">·</Typography>
                <MuiLink component="button" variant="caption" onClick={() => selectAllDocsForCurrentFilter(d => d.source_type === 'rfp')}>All RFP</MuiLink>
                <Typography variant="caption" color="text.secondary">·</Typography>
                <MuiLink component="button" variant="caption" onClick={() => selectAllDocsForCurrentFilter(d => (d.source_type || '').startsWith('competitor'))}>All competitors</MuiLink>
              </Box>
            )}

            <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
              <TextField fullWidth size="small" placeholder="Search across the selected scope by meaning…"
                value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()} />
              <Button
                variant="contained"
                onClick={handleSearch}
                startIcon={<SearchIcon />}
                disabled={searching || !searchQuery.trim() || (scopeMode === 'custom' && scopeDocIds.length === 0)}
              >
                {searching ? 'Searching…' : 'Search'}
              </Button>
            </Box>

            {searching && <LinearProgress sx={{ mb: 1 }} />}

            {searchResults !== null && !searching && (
              <>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  {searchResults.length} results (ranked by semantic similarity)
                  {searchScope?.document_ids?.length ? ` · scoped to ${searchScope.document_ids.length} document(s)` :
                   searchScope?.source_type ? ` · scoped to ${searchScope.source_type.replace('_', ' ')}` :
                   ' · scoped to all documents'}
                </Typography>
                {searchResults.map((r) => (
                  <Paper key={r.chunk_id} variant="outlined" sx={{ p: 2, mb: 1, borderRadius: 2 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5, gap: 2, flexWrap: 'wrap' }}>
                      <Typography variant="caption" color="text.secondary">
                        <strong>{r.document_name || `Doc ${r.document_id}`}</strong>
                        {r.source_type && ` · ${r.source_type.replace('_', ' ')}`}
                        {` · page ${r.page_number ?? '—'}`}
                        {` · similarity ${(r.similarity * 100).toFixed(1)}%`}
                        {r.classification && ` · ${r.classification}`}
                      </Typography>
                      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                        {(r.section_tags || []).map(tag => <Chip key={tag} label={tag} size="small" variant="outlined" sx={{ fontSize: 10 }} />)}
                      </Box>
                    </Box>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
                      {r.content.substring(0, 400)}{r.content.length > 400 ? '…' : ''}
                    </Typography>
                  </Paper>
                ))}
                {searchResults.length === 0 && (
                  <Typography variant="body2" color="text.secondary">
                    No results in this scope. Try a broader scope or different wording.
                  </Typography>
                )}
              </>
            )}
          </CardContent>
        </Card>

        {/* Documents Table */}
        <Card className="card">
          <CardContent>
            <Typography variant="h6" fontWeight={700} gutterBottom>All Documents ({documents.length})</Typography>
            <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2, maxHeight: 400 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 700 }}>Document</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Source</TableCell>
                    <TableCell align="center" sx={{ fontWeight: 700 }}>Chunks</TableCell>
                    <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {documents.map(doc => (
                    <TableRow key={doc.id} hover>
                      <TableCell sx={{ maxWidth: 400, overflow: 'hidden' }}>
                        <Typography variant="body2" fontWeight={600} sx={{ wordBreak: 'break-word' }}>{doc.filename}</Typography>
                        {doc.description && <Typography variant="caption" color="text.secondary" sx={{ wordBreak: 'break-word' }}>{doc.description}</Typography>}
                      </TableCell>
                      <TableCell><Chip label={doc.source_type?.replace('_', ' ')} size="small" variant="outlined" /></TableCell>
                      <TableCell align="center">{doc.total_chunks}</TableCell>
                      <TableCell align="center"><Chip label={doc.status} size="small" color={doc.status === 'completed' ? 'success' : 'warning'} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </CardContent>
        </Card>

        {/* Upload Dialog */}
        <Dialog open={uploadOpen} onClose={() => setUploadOpen(false)} maxWidth="sm" fullWidth>
          <DialogTitle>Upload Document</DialogTitle>
          <DialogContent>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
              <input accept=".pdf,.doc,.docx,.txt,.md,.csv,.xlsx" style={{ display: 'none' }} id="kb-upload" type="file"
                onChange={(e) => setSelectedFile(e.target.files[0])} />
              <label htmlFor="kb-upload">
                <Box sx={{ cursor: 'pointer', border: '2px dashed rgba(0,174,230,0.3)', borderRadius: 3,
                  p: 3, textAlign: 'center', '&:hover': { borderColor: '#00AEE6' } }}>
                  <UploadIcon sx={{ fontSize: 40, color: '#00AEE6', mb: 1 }} />
                  <Typography variant="body1" fontWeight={600}>{selectedFile ? selectedFile.name : 'Select a file'}</Typography>
                </Box>
              </label>
              <FormControl fullWidth size="small">
                <InputLabel>Source</InputLabel>
                <Select value={uploadSource} label="Source" onChange={(e) => setUploadSource(e.target.value)}>
                  <MenuItem value="parsons">Parsons Internal</MenuItem>
                  <MenuItem value="competitor_foia">Competitor FOIA</MenuItem>
                  <MenuItem value="competitor_proposal">Competitor Proposal</MenuItem>
                  <MenuItem value="reference">Reference</MenuItem>
                </Select>
              </FormControl>
              {(uploadSource === 'competitor_foia' || uploadSource === 'competitor_proposal') && (
                <FormControl fullWidth size="small">
                  <InputLabel>Competitor</InputLabel>
                  <Select value={uploadCompetitor} label="Competitor" onChange={(e) => setUploadCompetitor(e.target.value)}>
                    {competitors.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
                  </Select>
                </FormControl>
              )}
              <TextField fullWidth size="small" label="Description" value={uploadDesc} onChange={(e) => setUploadDesc(e.target.value)} />
            </Box>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setUploadOpen(false)}>Cancel</Button>
            <Button variant="contained" onClick={handleUpload} disabled={!selectedFile || uploading}>
              {uploading ? 'Processing…' : 'Upload & Enrich'}
            </Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Fade>
  );
};

export default KnowledgeBase;
