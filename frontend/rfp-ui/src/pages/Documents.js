import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Select,
  InputLabel,
  FormControl,
  IconButton,
  Tooltip,
  LinearProgress,
  Fade,
  Grid,
  Alert,
  Drawer,
  Divider,
} from '@mui/material';
import {
  CloudUpload as UploadIcon,
  Search as SearchIcon,
  Delete as DeleteIcon,
  Visibility as ViewIcon,
  Description as DocIcon,
  Close as CloseIcon,
  FilterList as FilterIcon,
} from '@mui/icons-material';
import axios from 'axios';

const SOURCE_TYPES = [
  { value: '', label: 'All Sources' },
  { value: 'parsons', label: 'Parsons Internal' },
  { value: 'competitor_foia', label: 'Competitor FOIA' },
  { value: 'competitor_proposal', label: 'Competitor Proposal' },
  { value: 'rfp', label: 'RFP Document' },
  { value: 'reference', label: 'Reference Material' },
];

const SOURCE_COLORS = {
  parsons: 'primary',
  competitor_foia: 'error',
  competitor_proposal: 'warning',
  rfp: 'success',
  reference: 'default',
};

const STATUS_COLORS = {
  completed: 'success',
  processing: 'info',
  failed: 'error',
};

function formatSize(bytes) {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const Documents = () => {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploadDialog, setUploadDialog] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadSource, setUploadSource] = useState('reference');
  const [uploadCompetitor, setUploadCompetitor] = useState('');
  const [uploadDesc, setUploadDesc] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');

  const [filterSource, setFilterSource] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [competitors, setCompetitors] = useState([]);

  // Detail drawer
  const [detailDoc, setDetailDoc] = useState(null);
  const [detailChunks, setDetailChunks] = useState([]);

  const fetchDocuments = useCallback(async () => {
    try {
      const params = {};
      if (filterSource) params.source_type = filterSource;
      const { data } = await axios.get('/api/documents', { params });
      setDocuments(data.documents || []);
    } catch (e) {
      console.error('Failed to load documents', e);
      setDocuments([]);
    } finally { setLoading(false); }
  }, [filterSource]);

  const fetchCompetitors = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/competitors');
      setCompetitors(data.competitors || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchDocuments(); fetchCompetitors(); }, [fetchDocuments, fetchCompetitors]);

  // Upload
  const handleUpload = async () => {
    if (!selectedFile) return;
    setUploading(true);
    setUploadError('');
    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('source_type', uploadSource);
    if (uploadCompetitor) formData.append('competitor_id', uploadCompetitor);
    if (uploadDesc) formData.append('description', uploadDesc);
    try {
      await axios.post('/api/documents/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadDialog(false);
      setSelectedFile(null);
      setUploadDesc('');
      fetchDocuments();
    } catch (e) {
      setUploadError(e.response?.data?.detail || 'Upload failed');
    } finally { setUploading(false); }
  };

  // Search
  const handleSearch = async () => {
    if (!searchQuery.trim() || searchQuery.trim().length < 2) return;
    try {
      const { data } = await axios.get('/api/documents/search/chunks', {
        params: { q: searchQuery.trim(), limit: 20 },
      });
      setSearchResults(data.results || []);
    } catch (e) {
      console.error('Search failed', e);
      setSearchResults([]);
    }
  };

  // Detail
  const openDetail = async (doc) => {
    setDetailDoc(doc);
    try {
      const { data } = await axios.get(`/api/documents/${doc.id}`);
      setDetailChunks(data.chunks || []);
    } catch { setDetailChunks([]); }
  };

  // Delete
  const handleDelete = async (id) => {
    if (!window.confirm('Delete this document and all its chunks?')) return;
    try { await axios.delete(`/api/documents/${id}`); fetchDocuments(); } catch { /* ignore */ }
  };

  return (
    <Fade in timeout={600}>
      <Box>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
          <Button variant="contained" startIcon={<UploadIcon />} onClick={() => setUploadDialog(true)}
            sx={{ borderRadius: 3, px: 3, py: 1.5,
              background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)',
              '&:hover': { background: 'linear-gradient(135deg, #50BF34 0%, #00AEE6 100%)' },
              transition: 'all 0.3s ease' }}>
            Upload Document
          </Button>
        </Box>

        {/* Search + Filter bar */}
        <Card className="card" sx={{ mb: 3 }}>
          <CardContent sx={{ py: 2 }}>
            <Grid container spacing={2} alignItems="center">
              <Grid item xs={12} sm={5}>
                <TextField fullWidth size="small" placeholder="Search document contents…"
                  value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                  InputProps={{ endAdornment: (
                    <IconButton size="small" onClick={handleSearch}><SearchIcon /></IconButton>
                  )}} />
              </Grid>
              <Grid item xs={12} sm={3}>
                <FormControl fullWidth size="small">
                  <InputLabel>Source Filter</InputLabel>
                  <Select value={filterSource} label="Source Filter"
                    onChange={(e) => setFilterSource(e.target.value)}>
                    {SOURCE_TYPES.map(st => <MenuItem key={st.value} value={st.value}>{st.label}</MenuItem>)}
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={4}>
                <Typography variant="body2" color="text.secondary">
                  {documents.length} document{documents.length !== 1 ? 's' : ''}
                  {filterSource && ` (filtered: ${filterSource.replace('_', ' ')})`}
                </Typography>
              </Grid>
            </Grid>
          </CardContent>
        </Card>

        {/* Search Results */}
        {searchResults !== null && (
          <Card className="card" sx={{ mb: 3 }}>
            <CardContent>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 2 }}>
                <Typography variant="h6" fontWeight={700}>
                  Search Results ({searchResults.length})
                </Typography>
                <Button size="small" onClick={() => setSearchResults(null)}>Clear</Button>
              </Box>
              {searchResults.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No matches found</Typography>
              ) : (
                searchResults.map((r, i) => (
                  <Paper key={r.chunk_id} variant="outlined" sx={{ p: 2, mb: 1, borderRadius: 2 }}>
                    <Typography variant="caption" color="text.secondary">
                      Doc #{r.document_id} · Chunk {r.chunk_index} · Page {r.page_number}
                    </Typography>
                    <Typography variant="body2" sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                      {r.content}
                    </Typography>
                  </Paper>
                ))
              )}
            </CardContent>
          </Card>
        )}

        {/* Documents Table */}
        {loading ? <LinearProgress /> : (
          <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 3 }}>
            <Table>
              <TableHead>
                <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                  <TableCell sx={{ fontWeight: 700 }}>Document</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Source</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Type</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Size</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Pages</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Chunks</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {documents.map(doc => (
                  <TableRow key={doc.id} hover>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{doc.filename}</Typography>
                      {doc.description && (
                        <Typography variant="caption" color="text.secondary">{doc.description}</Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Chip label={doc.source_type?.replace('_', ' ') || 'unknown'} size="small"
                        color={SOURCE_COLORS[doc.source_type] || 'default'} variant="outlined" />
                    </TableCell>
                    <TableCell align="center">
                      <Chip label={doc.file_type?.toUpperCase()} size="small" variant="outlined" />
                    </TableCell>
                    <TableCell align="center">{formatSize(doc.file_size)}</TableCell>
                    <TableCell align="center">{doc.total_pages}</TableCell>
                    <TableCell align="center">{doc.total_chunks}</TableCell>
                    <TableCell align="center">
                      <Chip label={doc.status} size="small"
                        color={STATUS_COLORS[doc.status] || 'default'} />
                    </TableCell>
                    <TableCell align="center">
                      <Tooltip title="View chunks">
                        <IconButton size="small" onClick={() => openDetail(doc)}>
                          <ViewIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton size="small" color="error" onClick={() => handleDelete(doc.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
                {documents.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={8} align="center" sx={{ py: 4 }}>
                      <DocIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 1 }} />
                      <Typography variant="body1" color="text.secondary">
                        No documents yet. Upload your first document to get started.
                      </Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        )}

        {/* Upload Dialog */}
        <Dialog open={uploadDialog} onClose={() => setUploadDialog(false)} maxWidth="sm" fullWidth>
          <DialogTitle>Upload Document</DialogTitle>
          <DialogContent>
            {uploadError && <Alert severity="error" sx={{ mb: 2 }}>{uploadError}</Alert>}
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
              <input accept=".pdf,.doc,.docx,.txt,.md,.csv,.xlsx,.xls"
                style={{ display: 'none' }} id="doc-upload-input" type="file"
                onChange={(e) => setSelectedFile(e.target.files[0])} />
              <label htmlFor="doc-upload-input">
                <Box sx={{ cursor: 'pointer', border: '2px dashed rgba(0,174,230,0.3)', borderRadius: 3,
                  p: 3, textAlign: 'center', '&:hover': { borderColor: '#00AEE6', bgcolor: 'rgba(0,174,230,0.05)' } }}>
                  <UploadIcon sx={{ fontSize: 40, color: '#00AEE6', mb: 1 }} />
                  <Typography variant="body1" fontWeight={600}>
                    {selectedFile ? selectedFile.name : 'Click to select a file'}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    PDF, Word, Excel, Text, CSV (max 50 MB)
                  </Typography>
                </Box>
              </label>

              <FormControl fullWidth size="small">
                <InputLabel>Document Source</InputLabel>
                <Select value={uploadSource} label="Document Source"
                  onChange={(e) => setUploadSource(e.target.value)}>
                  <MenuItem value="parsons">Parsons Internal</MenuItem>
                  <MenuItem value="competitor_foia">Competitor FOIA Response</MenuItem>
                  <MenuItem value="competitor_proposal">Competitor Proposal</MenuItem>
                  <MenuItem value="rfp">RFP Document</MenuItem>
                  <MenuItem value="reference">Reference Material</MenuItem>
                </Select>
              </FormControl>

              {(uploadSource === 'competitor_foia' || uploadSource === 'competitor_proposal') && (
                <FormControl fullWidth size="small">
                  <InputLabel>Competitor</InputLabel>
                  <Select value={uploadCompetitor} label="Competitor"
                    onChange={(e) => setUploadCompetitor(e.target.value)}>
                    <MenuItem value="">None</MenuItem>
                    {competitors.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
                  </Select>
                </FormControl>
              )}

              <TextField fullWidth size="small" label="Description (optional)"
                value={uploadDesc} onChange={(e) => setUploadDesc(e.target.value)}
                multiline rows={2} />
            </Box>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setUploadDialog(false)}>Cancel</Button>
            <Button variant="contained" onClick={handleUpload}
              disabled={!selectedFile || uploading}>
              {uploading ? 'Processing…' : 'Upload & Ingest'}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Detail Drawer */}
        <Drawer anchor="right" open={!!detailDoc} onClose={() => setDetailDoc(null)}>
          <Box sx={{ width: 500, p: 3 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
              <Typography variant="h6">Document Detail</Typography>
              <IconButton onClick={() => setDetailDoc(null)}><CloseIcon /></IconButton>
            </Box>
            {detailDoc && (
              <>
                <Typography variant="body1" fontWeight={600}>{detailDoc.filename}</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  {detailDoc.source_type?.replace('_', ' ')} · {detailDoc.file_type?.toUpperCase()} · {formatSize(detailDoc.file_size)}
                </Typography>
                {detailDoc.description && (
                  <Typography variant="body2" sx={{ mb: 1 }}>{detailDoc.description}</Typography>
                )}
                <Divider sx={{ my: 2 }} />
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  {detailChunks.length} Chunks · {detailDoc.total_pages} Pages
                </Typography>
                <Box sx={{ maxHeight: 'calc(100vh - 240px)', overflow: 'auto' }}>
                  {detailChunks.map(chunk => (
                    <Paper key={chunk.id} variant="outlined" sx={{ p: 2, mb: 1, borderRadius: 2 }}>
                      <Typography variant="caption" color="text.secondary">
                        Chunk {chunk.chunk_index} · Page {chunk.page_number} · {chunk.char_count} chars
                      </Typography>
                      <Typography variant="body2" sx={{ mt: 0.5, whiteSpace: 'pre-wrap', fontSize: 12 }}>
                        {chunk.content}
                      </Typography>
                    </Paper>
                  ))}
                </Box>
              </>
            )}
          </Box>
        </Drawer>
      </Box>
    </Fade>
  );
};

export default Documents;