import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box, Typography, Card, CardContent, Grid, Button, Chip, LinearProgress,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  IconButton, Tooltip, Fade,
} from '@mui/material';
import {
  Add as AddIcon, Edit as EditIcon, Visibility as ViewIcon,
  Delete as DeleteIcon, PlayArrow as StartIcon,
  CheckCircle as CheckIcon,
} from '@mui/icons-material';
import axios from 'axios';
import { useAuth } from '../auth/AuthContext';

const STATUS_COLOR = {
  draft: 'default', under_review: 'primary', approved: 'success', submitted: 'info', rejected: 'error',
};

const Proposals = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [proposals, setProposals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newRef, setNewRef] = useState('');

  const fetchProposals = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/proposals/');
      setProposals(Array.isArray(data) ? data : data.proposals || []);
    } catch (e) {
      console.error('Failed to fetch proposals', e);
      setProposals([]);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchProposals(); }, [fetchProposals]);

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    try {
      const { data } = await axios.post('/api/proposals/', {
        title: newTitle.trim(),
        rfp_reference: newRef.trim() || null,
      });
      setCreateOpen(false);
      setNewTitle('');
      setNewRef('');
      fetchProposals();
      // Navigate to authoring for the new proposal
      const id = data.id || data.proposal?.id;
      if (id) navigate(`/parsons-services?proposal_id=${id}&tab=0`);
    } catch (e) { alert(e.response?.data?.detail || 'Failed to create proposal'); }
  };

  const selectProposal = (id) => {
    localStorage.setItem('activeProposalId', String(id));
    navigate(`/parsons-services?proposal_id=${id}&tab=0`);
  };

  return (
    <Fade in timeout={600}>
      <Box>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}
            sx={{ borderRadius: 3, px: 3, py: 1.5,
              background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)',
              '&:hover': { background: 'linear-gradient(135deg, #50BF34 0%, #00AEE6 100%)', transform: 'translateY(-2px)' },
              transition: 'all 0.3s ease' }}>
            New Proposal
          </Button>
        </Box>

        {loading ? (
          <LinearProgress sx={{ mt: 4 }} />
        ) : proposals.length === 0 ? (
          <Card sx={{ mt: 4, textAlign: 'center', py: 6 }}>
            <CardContent>
              <Typography variant="h6" color="text.secondary" gutterBottom>No proposals yet</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Create your first proposal to start building an RFP response.
              </Typography>
              <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
                Create Proposal
              </Button>
            </CardContent>
          </Card>
        ) : (
          <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 3, mt: 2 }}>
            <Table>
              <TableHead>
                <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                  <TableCell sx={{ fontWeight: 700 }}>Title</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>RFP Reference</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Created</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 700 }}>Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {proposals.map(p => (
                  <TableRow key={p.id} hover sx={{ cursor: 'pointer' }} onClick={() => selectProposal(p.id)}>
                    <TableCell>
                      <Typography variant="body1" fontWeight={600}>{p.title || `Proposal #${p.id}`}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">{p.rfp_reference || '—'}</Typography>
                    </TableCell>
                    <TableCell align="center">
                      <Chip label={p.status?.replace('_', ' ') || 'draft'} size="small" color={STATUS_COLOR[p.status] || 'default'} />
                    </TableCell>
                    <TableCell align="center">
                      <Typography variant="body2" color="text.secondary">
                        {p.created_at ? new Date(p.created_at).toLocaleDateString() : '—'}
                      </Typography>
                    </TableCell>
                    <TableCell align="center" onClick={(e) => e.stopPropagation()}>
                      <Tooltip title="Open in Authoring">
                        <IconButton size="small" onClick={() => selectProposal(p.id)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="View Workflow">
                        <IconButton size="small" onClick={() => navigate(`/workflows?proposal_id=${p.id}`)}>
                          <ViewIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}

        {/* Create Dialog */}
        <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="sm" fullWidth>
          <DialogTitle>Create New Proposal</DialogTitle>
          <DialogContent>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
              <TextField fullWidth label="Proposal Title" value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)} required autoFocus />
              <TextField fullWidth label="RFP Reference (optional)" value={newRef}
                onChange={(e) => setNewRef(e.target.value)} />
            </Box>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button variant="contained" onClick={handleCreate} disabled={!newTitle.trim()}>Create & Open</Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Fade>
  );
};

export default Proposals;
