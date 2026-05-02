import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Button, TextField, Switch, Alert,
  Fade, Chip, Avatar, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, Paper, IconButton, FormControlLabel, Dialog, DialogTitle,
  DialogContent, DialogActions, Tabs, Tab, MenuItem,
  Tooltip,
} from '@mui/material';
import {
  Save as SaveIcon, Refresh as RefreshIcon, Security as SecurityIcon,
  Settings as SettingsIcon,
  Score as ScoreIcon,
  Business as CompetitorIcon, Delete as DeleteIcon, Add as AddIcon,
  People as UsersIcon, LockReset as LockResetIcon, PersonOff as DeactivateIcon,
  PersonAdd as PersonAddIcon, Edit as EditIcon,
  MailOutline as InviteIcon, ContentCopy as CopyIcon,
} from '@mui/icons-material';
import axios from 'axios';
import { useAuth } from '../auth/AuthContext';

const Settings = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [activeTab, setActiveTab] = useState(0);
  const [settings, setSettings] = useState({});
  const [saveStatus, setSaveStatus] = useState(null);
  const [loading, setLoading] = useState(false);

  // Feature flags (local-only; persisted in localStorage)
  const [featureFlags, setFeatureFlags] = useState(() => ({
    responseWorkbench: (() => {
      try { return window.localStorage.getItem('responseWorkbench.enabled') === 'true'; }
      catch { return false; }
    })(),
  }));
  const toggleFeatureFlag = (key, storageKey) => (e) => {
    const on = e.target.checked;
    setFeatureFlags((prev) => ({ ...prev, [key]: on }));
    try { window.localStorage.setItem(storageKey, on ? 'true' : 'false'); } catch { /* ignore */ }
    // Reload so AppShell picks up the new nav configuration
    window.location.reload();
  };

  // Rubric state
  const [rubric, setRubric] = useState(null);
  const [editSections, setEditSections] = useState([]);
  const [rubricError, setRubricError] = useState('');

  // Competitor state
  const [competitors, setCompetitors] = useState([]);
  const [newCompDialog, setNewCompDialog] = useState(false);
  const [newComp, setNewComp] = useState({ name: '', website: '', description: '' });

  // User management state
  const [users, setUsers] = useState([]);
  const [userDialog, setUserDialog] = useState(null); // null | 'create' | 'edit' | 'reset'
  const [editingUser, setEditingUser] = useState(null);
  const [userForm, setUserForm] = useState({ username: '', email: '', first_name: '', last_name: '', role: 'proposal_manager', password: '', must_change_password: true });
  const [userError, setUserError] = useState('');
  const [resetPw, setResetPw] = useState('');
  // Invitation dialog state — shown after admin clicks the Invite button.
  const [inviteResult, setInviteResult] = useState(null);  // { user, redeem_url, expires_at, email_sent, smtp_configured, email_body }
  const [inviteCopied, setInviteCopied] = useState(null);  // 'url' | 'body' | null

  // ── Data fetching ──────────────────────────────────────────────────

  const fetchSettings = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/settings');
      setSettings(data.settings || {});
    } catch (e) { console.error('Failed to load settings', e); }
  }, []);

  const fetchRubric = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/scoring/rubric');
      setRubric(data);
      setEditSections(data.sections.map(s => ({ ...s })));
    } catch (e) { console.error('Failed to load rubric', e); }
  }, []);

  const fetchCompetitors = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/competitors');
      setCompetitors(data.competitors || []);
    } catch (e) { console.error('Failed to load competitors', e); }
  }, []);

  const fetchUsers = useCallback(async () => {
    if (!isAdmin) return;
    try { const { data } = await axios.get('/api/auth/users'); setUsers(data || []); } catch { /* ignore */ }
  }, [isAdmin]);

  useEffect(() => { fetchSettings(); fetchRubric(); fetchCompetitors(); fetchUsers(); }, [fetchSettings, fetchRubric, fetchCompetitors, fetchUsers]);

  // ── Save helpers ───────────────────────────────────────────────────

  const showStatus = (type) => { setSaveStatus(type); setTimeout(() => setSaveStatus(null), 3000); };

  const handleSaveSettings = async () => {
    setLoading(true);
    try {
      await axios.put('/api/settings', { settings });
      showStatus('success');
    } catch { showStatus('error'); }
    finally { setLoading(false); }
  };

  const updateSetting = (key, value) => setSettings(prev => ({ ...prev, [key]: value }));

  // Rubric save
  const weightedSum = editSections.filter(s => !s.pass_fail).reduce((sum, s) => sum + (parseInt(s.weight_points) || 0), 0);

  const handleSaveRubric = async () => {
    if (weightedSum !== 100) { setRubricError(`Weighted sections must sum to 100 (currently ${weightedSum})`); return; }
    setLoading(true);
    try {
      await axios.put('/api/scoring/rubric', {
        sections: editSections.map((s, i) => ({
          section_id: s.section_id, title: s.title,
          weight_points: parseInt(s.weight_points) || 0,
          pass_fail: s.pass_fail, sort_order: i,
        })),
      });
      setRubricError('');
      fetchRubric();
      showStatus('success');
    } catch (e) { setRubricError(e.response?.data?.detail || 'Failed to save rubric'); }
    finally { setLoading(false); }
  };

  // Competitor CRUD
  const handleAddCompetitor = async () => {
    try {
      await axios.post('/api/competitors', newComp);
      setNewCompDialog(false);
      setNewComp({ name: '', website: '', description: '' });
      fetchCompetitors();
      showStatus('success');
    } catch (e) { alert(e.response?.data?.detail || 'Failed to add competitor'); }
  };

  const handleDeleteCompetitor = async (id) => {
    if (!window.confirm('Delete this competitor and all their news?')) return;
    try { await axios.delete(`/api/competitors/${id}`); fetchCompetitors(); } catch { /* ignore */ }
  };

  const handleRefreshNews = async (id) => {
    try {
      const { data } = await axios.post(`/api/competitors/${id}/refresh-news`);
      alert(`Fetched ${data.new_items} new article(s)`);
    } catch { alert('News refresh failed'); }
  };

  // ── Tab panels ─────────────────────────────────────────────────────

  const cardSx = {
    borderRadius: 3, boxShadow: '0 4px 20px rgba(0,0,0,0.08)',
    border: '1px solid rgba(99,102,241,0.1)', background: 'linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%)',
  };

  const renderScoringPanel = () => (
    <Card sx={cardSx}>
      <CardContent sx={{ p: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
          <Avatar sx={{ bgcolor: '#A06CD5', mr: 2 }}><ScoreIcon /></Avatar>
          <Box>
            <Typography variant="h6" fontWeight={600}>Scoring Rubric</Typography>
            <Typography variant="body2" color="text.secondary">Configure section weights for proposal evaluation. Weighted sections must sum to 100.</Typography>
          </Box>
        </Box>

        {rubricError && <Alert severity="error" sx={{ mb: 2 }}>{rubricError}</Alert>}

        <Box sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
          <Chip label={`Sum: ${weightedSum}/100`} color={weightedSum === 100 ? 'success' : 'error'} />
          <Chip label={rubric?.name || 'Loading…'} variant="outlined" />
        </Box>

        <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                <TableCell sx={{ fontWeight: 700 }}>Section</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700 }}>Pass/Fail</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700 }}>Weight</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {editSections.map((s, idx) => (
                <TableRow key={s.section_id}>
                  <TableCell>{s.title}</TableCell>
                  <TableCell align="center">
                    <Switch
                      checked={s.pass_fail}
                      disabled={!isAdmin}
                      size="small"
                      onChange={(e) => {
                        const u = [...editSections];
                        u[idx] = { ...u[idx], pass_fail: e.target.checked, weight_points: e.target.checked ? 0 : u[idx].weight_points };
                        setEditSections(u);
                      }}
                    />
                  </TableCell>
                  <TableCell align="center">
                    <TextField
                      type="number" size="small" value={s.weight_points}
                      disabled={s.pass_fail || !isAdmin}
                      onChange={(e) => {
                        const u = [...editSections];
                        u[idx] = { ...u[idx], weight_points: parseInt(e.target.value) || 0 };
                        setEditSections(u);
                      }}
                      inputProps={{ min: 0, max: 100, style: { width: 60, textAlign: 'center' } }}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>

        {isAdmin && (
          <Box sx={{ mt: 2, textAlign: 'right' }}>
            <Button variant="contained" startIcon={<SaveIcon />} onClick={handleSaveRubric} disabled={loading || weightedSum !== 100}>
              Save Rubric
            </Button>
          </Box>
        )}
      </CardContent>
    </Card>
  );

  const renderAIPanel = () => (
    <Card sx={cardSx}>
      <CardContent sx={{ p: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 3 }}>
          <Avatar sx={{ bgcolor: '#00AEE6', mr: 2 }}><SecurityIcon /></Avatar>
          <Box>
            <Typography variant="h6" fontWeight={600}>AI / Models</Typography>
            <Typography variant="body2" color="text.secondary">Configure AI providers and model settings</Typography>
          </Box>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField fullWidth label="LLM Provider" select size="small" value={settings.llm_provider || 'anthropic'}
            onChange={(e) => updateSetting('llm_provider', e.target.value)} disabled={!isAdmin}>
            <MenuItem value="anthropic">Anthropic (Claude)</MenuItem>
            <MenuItem value="openai">OpenAI</MenuItem>
          </TextField>
          <TextField fullWidth label="Refiner Model" size="small" value={settings.refiner_model || 'claude-sonnet-4-5-20250929'}
            onChange={(e) => updateSetting('refiner_model', e.target.value)} disabled={!isAdmin}
            helperText="Default: claude-sonnet-4-5-20250929 (latest Sonnet). Use gpt-4o for OpenAI." />
          <TextField fullWidth label="Embedding Model" size="small" value={settings.embedding_model || 'text-embedding-ada-002'}
            onChange={(e) => updateSetting('embedding_model', e.target.value)} disabled={!isAdmin} />
          <TextField fullWidth label="OpenAI API Key" type="password" size="small" value={settings.openai_api_key || ''}
            onChange={(e) => updateSetting('openai_api_key', e.target.value)} disabled={!isAdmin} />
          <TextField fullWidth label="Anthropic API Key" type="password" size="small" value={settings.anthropic_api_key || ''}
            onChange={(e) => updateSetting('anthropic_api_key', e.target.value)} disabled={!isAdmin} />
          <Button variant="outlined" size="small" onClick={async () => {
            try {
              const { data } = await axios.post('/api/prompts/refine', {
                prompt: 'Say the single word OK.',
                target_provider: settings.llm_provider || 'anthropic',
                target_model: settings.refiner_model || 'claude-sonnet-4-5-20250929',
              });
              const model = data.refiner_model || 'unknown';
              const note = data.notes ? `\n\nNote: ${data.notes}` : '';
              alert(`Connection successful — refiner: ${model}${note}`);
            } catch (e) {
              const d = e.response?.data?.detail;
              let msg;
              if (typeof d === 'string') msg = d;
              else if (Array.isArray(d)) msg = d.map(x => x.msg || JSON.stringify(x)).join('; ');
              else if (d) msg = JSON.stringify(d);
              else msg = e.message;
              alert(`Connection failed: ${msg}`);
            }
          }}>Test Connection</Button>
        </Box>
      </CardContent>
    </Card>
  );

  const renderWorkflowPanel = () => (
    <Card sx={{ ...cardSx, border: '1px solid rgba(245,158,11,0.1)', background: 'linear-gradient(135deg, #fefce8 0%, #fef3c7 100%)' }}>
      <CardContent sx={{ p: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 3 }}>
          <Avatar sx={{ bgcolor: '#00d084', mr: 2 }}><SettingsIcon /></Avatar>
          <Box>
            <Typography variant="h6" fontWeight={600}>Workflow Policy</Typography>
            <Typography variant="body2" color="text.secondary">Control workflow processing and approval gates</Typography>
          </Box>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 2, bgcolor: 'white', borderRadius: 2 }}>
            <Box>
              <Typography variant="body1" fontWeight={500}>Auto-start workflows</Typography>
              <Typography variant="body2" color="text.secondary">Begin processing when documents are uploaded</Typography>
            </Box>
            <Switch checked={settings.workflow_auto_start ?? true}
              onChange={(e) => updateSetting('workflow_auto_start', e.target.checked)} disabled={!isAdmin} />
          </Box>
          <TextField fullWidth label="Approval Levels Required" type="number" size="small"
            value={settings.approval_levels ?? 2}
            onChange={(e) => updateSetting('approval_levels', parseInt(e.target.value) || 1)}
            disabled={!isAdmin} inputProps={{ min: 1, max: 5 }} />
          <TextField fullWidth label="Conflict Detection Sensitivity" select size="small"
            value={settings.conflict_sensitivity || 'high'}
            onChange={(e) => updateSetting('conflict_sensitivity', e.target.value)} disabled={!isAdmin}>
            <MenuItem value="low">Low</MenuItem>
            <MenuItem value="medium">Medium</MenuItem>
            <MenuItem value="high">High</MenuItem>
          </TextField>
        </Box>
      </CardContent>
    </Card>
  );

  const renderFeatureFlagsPanel = () => (
    <Card sx={{ ...cardSx, border: '1px solid rgba(0,174,230,0.15)', background: 'linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%)' }}>
      <CardContent sx={{ p: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 3 }}>
          <Avatar sx={{ bgcolor: '#00AEE6', mr: 2 }}><SettingsIcon /></Avatar>
          <Box>
            <Typography variant="h6" fontWeight={600}>Feature Flags</Typography>
            <Typography variant="body2" color="text.secondary">Enable or disable new or in-progress UI surfaces (local to this browser).</Typography>
          </Box>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 2, bgcolor: 'white', borderRadius: 2 }}>
            <Box sx={{ maxWidth: 560 }}>
              <Typography variant="body1" fontWeight={500}>Response Workbench</Typography>
              <Typography variant="body2" color="text.secondary">
                Single section-keyed authoring surface: Parsons draft + competitor responses + rubric-driven scoring + AI cure suggestions + Excel/Word/PDF export — all in one page at <b>/workbench</b>. Reloads the browser on toggle.
              </Typography>
            </Box>
            <Switch checked={featureFlags.responseWorkbench}
              onChange={toggleFeatureFlag('responseWorkbench', 'responseWorkbench.enabled')} />
          </Box>
        </Box>
      </CardContent>
    </Card>
  );

  const renderCompetitorsPanel = () => (
    <Card sx={{ ...cardSx, border: '1px solid rgba(255,107,107,0.1)', background: 'linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%)' }}>
      <CardContent sx={{ p: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Avatar sx={{ bgcolor: '#FF6B6B', mr: 2 }}><CompetitorIcon /></Avatar>
            <Box>
              <Typography variant="h6" fontWeight={600}>Competitor Library</Typography>
              <Typography variant="body2" color="text.secondary">Manage tracked competitors and their news</Typography>
            </Box>
          </Box>
          {isAdmin && (
            <Button size="small" variant="outlined" startIcon={<AddIcon />} onClick={() => setNewCompDialog(true)}>
              Add Competitor
            </Button>
          )}
        </Box>

        <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                <TableCell sx={{ fontWeight: 700 }}>Name</TableCell>
                <TableCell sx={{ fontWeight: 700 }}>Website</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700 }}>Watchlist</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700 }}>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {competitors.map(c => (
                <TableRow key={c.id} hover>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>{c.name}</Typography>
                    {c.description && <Typography variant="caption" color="text.secondary">{c.description.length > 60 ? c.description.substring(0, 60) + '…' : c.description}</Typography>}
                  </TableCell>
                  <TableCell>
                    {c.website ? <a href={c.website} target="_blank" rel="noreferrer" style={{ color: '#00AEE6', fontSize: 13 }}>{c.website}</a> : '—'}
                  </TableCell>
                  <TableCell align="center">
                    <Chip label={c.watchlist ? 'Active' : 'Off'} size="small" color={c.watchlist ? 'success' : 'default'} variant="outlined" />
                  </TableCell>
                  <TableCell align="center">
                    <Tooltip title="Refresh news now">
                      <IconButton size="small" onClick={() => handleRefreshNews(c.id)}><RefreshIcon fontSize="small" /></IconButton>
                    </Tooltip>
                    {isAdmin && (
                      <Tooltip title="Delete">
                        <IconButton size="small" color="error" onClick={() => handleDeleteCompetitor(c.id)}><DeleteIcon fontSize="small" /></IconButton>
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {competitors.length === 0 && (
                <TableRow><TableCell colSpan={4} align="center"><Typography variant="body2" color="text.secondary">No competitors added yet</Typography></TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );

  const ROLES = ['admin', 'proposal_manager', 'evaluator', 'vendor'];
  const ROLE_COLORS = { admin: 'error', proposal_manager: 'primary', evaluator: 'warning', vendor: 'default' };

  const handleCreateUser = async () => {
    setUserError('');
    if (!userForm.username.trim() || !userForm.email.trim() || !userForm.password) { setUserError('Username, email and password are required.'); return; }
    if (userForm.password.length < 8) { setUserError('Password must be at least 8 characters.'); return; }
    try {
      await axios.post('/api/auth/register', userForm);
      setUserDialog(null);
      setUserForm({ username: '', email: '', first_name: '', last_name: '', role: 'proposal_manager', password: '', must_change_password: true });
      fetchUsers();
      showStatus('success');
    } catch (e) { setUserError(e.response?.data?.detail || 'Failed to create user'); }
  };

  const handleUpdateUser = async () => {
    setUserError('');
    try {
      await axios.put(`/api/auth/users/${editingUser.id}`, {
        first_name: userForm.first_name || null,
        last_name: userForm.last_name || null,
        role: userForm.role,
      });
      setUserDialog(null);
      fetchUsers();
      showStatus('success');
    } catch (e) { setUserError(e.response?.data?.detail || 'Failed to update user'); }
  };

  const handleToggleActive = async (u) => {
    try { await axios.put(`/api/auth/users/${u.id}/activate`); fetchUsers(); } catch { alert('Failed to toggle user status'); }
  };

  const handleResetPassword = async () => {
    setUserError('');
    if (!resetPw || resetPw.length < 8) { setUserError('New password must be at least 8 characters.'); return; }
    try {
      await axios.post(`/api/auth/users/${editingUser.id}/reset-password`, { new_password: resetPw });
      setUserDialog(null);
      setResetPw('');
      showStatus('success');
    } catch (e) { setUserError(e.response?.data?.detail || 'Failed to reset password'); }
  };

  const handleSendInvite = async (u) => {
    try {
      const { data } = await axios.post(`/api/auth/users/${u.id}/invite`);
      setInviteResult({ user: u, ...data });
      setInviteCopied(null);
    } catch (e) {
      alert(e.response?.data?.detail || 'Failed to create invitation');
    }
  };

  const copyToClipboard = async (text, kind) => {
    try {
      await navigator.clipboard.writeText(text);
      setInviteCopied(kind);
      setTimeout(() => setInviteCopied(null), 2000);
    } catch {
      // Fallback for older browsers / restricted contexts
      try {
        const ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
        setInviteCopied(kind);
        setTimeout(() => setInviteCopied(null), 2000);
      } catch { /* give up */ }
    }
  };

  const renderUsersPanel = () => (
    <Card sx={{ ...cardSx, border: '1px solid rgba(0,174,230,0.15)', background: 'linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%)' }}>
      <CardContent sx={{ p: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Avatar sx={{ bgcolor: '#00AEE6', mr: 2 }}><UsersIcon /></Avatar>
            <Box>
              <Typography variant="h6" fontWeight={600}>User Management</Typography>
              <Typography variant="body2" color="text.secondary">Create accounts, assign roles, reset passwords</Typography>
            </Box>
          </Box>
          <Button size="small" variant="contained" startIcon={<PersonAddIcon />}
            onClick={() => { setUserForm({ username: '', email: '', first_name: '', last_name: '', role: 'proposal_manager', password: '', must_change_password: true }); setUserError(''); setUserDialog('create'); }}>
            Add User
          </Button>
        </Box>

        <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: '#F5F7FA' }}>
                <TableCell sx={{ fontWeight: 700 }}>Name / Username</TableCell>
                <TableCell sx={{ fontWeight: 700 }}>Email</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700 }}>Role</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700 }}>Status</TableCell>
                <TableCell align="center" sx={{ fontWeight: 700 }}>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {users.map(u => (
                <TableRow key={u.id} hover sx={{ opacity: u.is_active ? 1 : 0.5 }}>
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>
                      {[u.first_name, u.last_name].filter(Boolean).join(' ') || u.username}
                    </Typography>
                    {(u.first_name || u.last_name) && <Typography variant="caption" color="text.secondary">{u.username}</Typography>}
                    {u.must_change_password && <Chip label="must change pw" size="small" color="warning" sx={{ ml: 1, height: 16, fontSize: 10 }} />}
                  </TableCell>
                  <TableCell sx={{ fontSize: 13 }}>{u.email}</TableCell>
                  <TableCell align="center">
                    <Chip label={u.role} size="small" color={ROLE_COLORS[u.role] || 'default'} />
                  </TableCell>
                  <TableCell align="center">
                    <Chip label={u.is_active ? 'Active' : 'Inactive'} size="small" color={u.is_active ? 'success' : 'default'} variant="outlined" />
                  </TableCell>
                  <TableCell align="center" sx={{ whiteSpace: 'nowrap' }}>
                    <Tooltip title="Edit role / name">
                      <IconButton size="small" onClick={() => { setEditingUser(u); setUserForm({ first_name: u.first_name || '', last_name: u.last_name || '', role: u.role }); setUserError(''); setUserDialog('edit'); }}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Send invitation (user sets their own password)">
                      <IconButton size="small" color="primary" onClick={() => handleSendInvite(u)}>
                        <InviteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Reset password (admin sets a temp password)">
                      <IconButton size="small" onClick={() => { setEditingUser(u); setResetPw(''); setUserError(''); setUserDialog('reset'); }}>
                        <LockResetIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title={u.is_active ? 'Deactivate' : 'Activate'}>
                      <IconButton size="small" color={u.is_active ? 'warning' : 'success'} onClick={() => handleToggleActive(u)}>
                        <DeactivateIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
              {users.length === 0 && (
                <TableRow><TableCell colSpan={5} align="center"><Typography variant="body2" color="text.secondary">No users found</Typography></TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>

        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
          Roles: <strong>admin</strong> — full access &nbsp;|&nbsp; <strong>proposal_manager</strong> — create/edit proposals &nbsp;|&nbsp;
          <strong>evaluator</strong> — score and review &nbsp;|&nbsp; <strong>vendor</strong> — read-only
        </Typography>
      </CardContent>

      {/* Create User Dialog */}
      <Dialog open={userDialog === 'create'} onClose={() => setUserDialog(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Create New User</DialogTitle>
        <DialogContent dividers>
          {userError && <Alert severity="error" sx={{ mb: 2 }}>{userError}</Alert>}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 0.5 }}>
            <Box sx={{ display: 'flex', gap: 2 }}>
              <TextField fullWidth size="small" label="First Name" value={userForm.first_name || ''} onChange={e => setUserForm(f => ({ ...f, first_name: e.target.value }))} />
              <TextField fullWidth size="small" label="Last Name" value={userForm.last_name || ''} onChange={e => setUserForm(f => ({ ...f, last_name: e.target.value }))} />
            </Box>
            <TextField fullWidth size="small" label="Email / Username *" value={userForm.email} onChange={e => setUserForm(f => ({ ...f, email: e.target.value, username: e.target.value }))} helperText="Used to log in" />
            <TextField fullWidth size="small" label="Role" select value={userForm.role} onChange={e => setUserForm(f => ({ ...f, role: e.target.value }))}>
              {ROLES.map(r => <MenuItem key={r} value={r}>{r}</MenuItem>)}
            </TextField>
            <TextField fullWidth size="small" type="password" label="Temporary Password *" value={userForm.password} onChange={e => setUserForm(f => ({ ...f, password: e.target.value }))} helperText="User must change on first login" />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUserDialog(null)}>Cancel</Button>
          <Button variant="contained" onClick={handleCreateUser}>Create User</Button>
        </DialogActions>
      </Dialog>

      {/* Edit User Dialog */}
      <Dialog open={userDialog === 'edit'} onClose={() => setUserDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Edit: {editingUser?.username}</DialogTitle>
        <DialogContent dividers>
          {userError && <Alert severity="error" sx={{ mb: 2 }}>{userError}</Alert>}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 0.5 }}>
            <Box sx={{ display: 'flex', gap: 2 }}>
              <TextField fullWidth size="small" label="First Name" value={userForm.first_name || ''} onChange={e => setUserForm(f => ({ ...f, first_name: e.target.value }))} />
              <TextField fullWidth size="small" label="Last Name" value={userForm.last_name || ''} onChange={e => setUserForm(f => ({ ...f, last_name: e.target.value }))} />
            </Box>
            <TextField fullWidth size="small" label="Role" select value={userForm.role || 'proposal_manager'} onChange={e => setUserForm(f => ({ ...f, role: e.target.value }))}>
              {ROLES.map(r => <MenuItem key={r} value={r}>{r}</MenuItem>)}
            </TextField>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUserDialog(null)}>Cancel</Button>
          <Button variant="contained" onClick={handleUpdateUser}>Save</Button>
        </DialogActions>
      </Dialog>

      {/* Reset Password Dialog */}
      <Dialog open={userDialog === 'reset'} onClose={() => setUserDialog(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Reset Password: {editingUser?.username}</DialogTitle>
        <DialogContent dividers>
          {userError && <Alert severity="error" sx={{ mb: 2 }}>{userError}</Alert>}
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            The user will be required to change this password on their next login.
          </Typography>
          <TextField fullWidth size="small" type="password" label="New Temporary Password" value={resetPw}
            onChange={e => setResetPw(e.target.value)} inputProps={{ minLength: 8 }} helperText="Minimum 8 characters" />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUserDialog(null)}>Cancel</Button>
          <Button variant="contained" color="warning" onClick={handleResetPassword}>Reset Password</Button>
        </DialogActions>
      </Dialog>

      {/* Invitation Result Dialog */}
      <Dialog open={!!inviteResult} onClose={() => setInviteResult(null)} maxWidth="sm" fullWidth>
        <DialogTitle>
          Invitation for {inviteResult?.user?.username}
        </DialogTitle>
        <DialogContent dividers>
          {inviteResult?.email_sent ? (
            <Alert severity="success" sx={{ mb: 2 }}>
              Email sent to <strong>{inviteResult.email}</strong>. The user
              has {Math.max(1, Math.round((new Date(inviteResult.expires_at) - new Date()) / (1000 * 60 * 60 * 24)))} days
              to redeem.
            </Alert>
          ) : (
            <Alert severity="info" sx={{ mb: 2 }}>
              {inviteResult?.smtp_configured
                ? 'Email delivery failed — copy the link below and send it manually via Teams or email.'
                : 'Email delivery is not configured. Copy the link below and send it to the user via Teams or email.'}
            </Alert>
          )}

          <Typography variant="caption" color="text.secondary">REDEEM URL</Typography>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 0.5, mb: 2 }}>
            <TextField fullWidth size="small" value={inviteResult?.redeem_url || ''} InputProps={{ readOnly: true, sx: { fontFamily: 'monospace', fontSize: 12 } }} />
            <Button
              variant="outlined" size="small" startIcon={<CopyIcon />}
              onClick={() => copyToClipboard(inviteResult.redeem_url, 'url')}
            >
              {inviteCopied === 'url' ? 'Copied!' : 'Copy'}
            </Button>
          </Box>

          <Typography variant="caption" color="text.secondary">EMAIL BODY (paste-ready)</Typography>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', mt: 0.5 }}>
            <TextField fullWidth multiline minRows={6} size="small" value={inviteResult?.email_body || ''} InputProps={{ readOnly: true, sx: { fontFamily: 'monospace', fontSize: 12 } }} />
            <Button
              variant="outlined" size="small" startIcon={<CopyIcon />}
              sx={{ mt: 0.5, flexShrink: 0 }}
              onClick={() => copyToClipboard(inviteResult.email_body, 'body')}
            >
              {inviteCopied === 'body' ? 'Copied!' : 'Copy'}
            </Button>
          </Box>

          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
            The user will set their own password when they click the link.
            You will not see or store their password. Expires {inviteResult ? new Date(inviteResult.expires_at).toLocaleString() : ''}.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setInviteResult(null)} variant="contained">Done</Button>
        </DialogActions>
      </Dialog>
    </Card>
  );

  // ── Main render ────────────────────────────────────────────────────

  return (
    <Fade in={true} timeout={600}>
      <Box>
        {isAdmin && (
          <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
            <Button variant="contained" startIcon={<SaveIcon />} onClick={handleSaveSettings} disabled={loading}
              sx={{
                borderRadius: 3, px: 3, py: 1.5,
                background: 'linear-gradient(135deg, #50BF34 0%, #00AEE6 100%)',
                boxShadow: '0 4px 15px rgba(0,174,230,0.35)',
                '&:hover': { background: 'linear-gradient(135deg, #00AEE6 0%, #0052CC 100%)', transform: 'translateY(-2px)' },
                transition: 'all 0.3s ease',
              }}>
              Save All Settings
            </Button>
          </Box>
        )}

        {saveStatus && (
          <Fade in={true}>
            <Alert severity={saveStatus} sx={{ mb: 3, borderRadius: 2 }}>
              {saveStatus === 'success' ? 'Settings saved successfully!' : 'Error saving settings.'}
            </Alert>
          </Fade>
        )}

        <Tabs value={activeTab} onChange={(_, v) => setActiveTab(v)} sx={{ mb: 3 }} variant="scrollable" scrollButtons="auto">
          <Tab label="Scoring Rubric" icon={<ScoreIcon />} iconPosition="start" />
          <Tab label="AI / Models" icon={<SecurityIcon />} iconPosition="start" />
          <Tab label="Workflow Policy" icon={<SettingsIcon />} iconPosition="start" />
          <Tab label="Competitors" icon={<CompetitorIcon />} iconPosition="start" />
          <Tab label="Feature Flags" icon={<SettingsIcon />} iconPosition="start" />
          {isAdmin && <Tab label="Users" icon={<UsersIcon />} iconPosition="start" />}
        </Tabs>

        {activeTab === 0 && renderScoringPanel()}
        {activeTab === 1 && renderAIPanel()}
        {activeTab === 2 && renderWorkflowPanel()}
        {activeTab === 3 && renderCompetitorsPanel()}
        {activeTab === 4 && renderFeatureFlagsPanel()}
        {activeTab === 5 && isAdmin && renderUsersPanel()}

        {/* Add Competitor Dialog */}
        <Dialog open={newCompDialog} onClose={() => setNewCompDialog(false)} maxWidth="sm" fullWidth>
          <DialogTitle>Add Competitor</DialogTitle>
          <DialogContent>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
              <TextField fullWidth label="Company Name" size="small" value={newComp.name}
                onChange={(e) => setNewComp({ ...newComp, name: e.target.value })} required />
              <TextField fullWidth label="Website" size="small" value={newComp.website}
                onChange={(e) => setNewComp({ ...newComp, website: e.target.value })} />
              <TextField fullWidth label="Description" size="small" multiline rows={2} value={newComp.description}
                onChange={(e) => setNewComp({ ...newComp, description: e.target.value })} />
            </Box>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setNewCompDialog(false)}>Cancel</Button>
            <Button variant="contained" onClick={handleAddCompetitor} disabled={!newComp.name.trim()}>Add</Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Fade>
  );
};

export default Settings;