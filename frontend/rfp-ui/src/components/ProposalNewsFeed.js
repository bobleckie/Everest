/**
 * ProposalNewsFeed
 *
 * Combined news feed for one proposal — RFP-specific items plus every
 * news item tied to a competitor on the proposal's tracked list. Items
 * link out to the underlying article. Manual refresh button fans out to
 * every source. A "manage tracked competitors" affordance lets the user
 * grow / shrink the watchlist without leaving the dashboard.
 *
 * Data source: GET /api/proposals/{id}/news, refreshed via
 * POST /api/proposals/{id}/news/refresh.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Card, CardContent, Typography, Stack, Chip, IconButton, Tooltip,
  CircularProgress, Alert, Link, Button, Divider, Snackbar, ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import {
  Refresh as RefreshIcon,
  OpenInNew as ExternalIcon,
  Article as ArticleIcon,
  Business as BusinessIcon,
  Public as PublicIcon,
  Settings as SettingsIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import axios from 'axios';

// ── Helpers ────────────────────────────────────────────────────────
const SCOPE_VISUAL = {
  rfp:        { color: '#0288d1', label: 'RFP', icon: <PublicIcon sx={{ fontSize: 14 }} /> },
  competitor: { color: '#7b1fa2', label: 'Competitor', icon: <BusinessIcon sx={{ fontSize: 14 }} /> },
};

function timeAgo(iso) {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return 'just now';
  const m = Math.floor(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

// Strip Google News' "—Source" suffix that often shows up on titles.
function cleanTitle(t) {
  if (!t) return '(untitled)';
  return t.replace(/\s+-\s+[^-]+$/, '').trim() || t;
}

// ── Component ──────────────────────────────────────────────────────
export default function ProposalNewsFeed({ proposalId, onManageCompetitors }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [scope, setScope] = useState('all'); // all | rfp | competitor
  const [toast, setToast] = useState(null);

  const load = useCallback(async () => {
    if (!proposalId) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await axios.get(`/api/proposals/${proposalId}/news`);
      setItems(data?.items || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load news');
    } finally {
      setLoading(false);
    }
  }, [proposalId]);

  useEffect(() => { load(); }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const { data } = await axios.post(`/api/proposals/${proposalId}/news/refresh`);
      const rfpNew = data?.rfp || 0;
      const compNew = Object.values(data?.competitors || {})
        .reduce((a, n) => a + (Number.isFinite(n) && n > 0 ? n : 0), 0);
      const total = rfpNew + compNew;
      setToast(total === 0
        ? 'No new articles found.'
        : `${total} new article${total === 1 ? '' : 's'} pulled (${rfpNew} RFP · ${compNew} competitor).`);
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Refresh failed');
    } finally {
      setRefreshing(false);
    }
  };

  const hide = async (id) => {
    try {
      await axios.delete(`/api/proposals/${proposalId}/news/${id}`);
      setItems(items.filter(it => it.id !== id));
    } catch (e) {
      setToast(e?.response?.data?.detail || 'Hide failed');
    }
  };

  const filtered = useMemo(() => {
    if (scope === 'all') return items;
    return items.filter(it => it.scope === scope);
  }, [items, scope]);

  const counts = useMemo(() => {
    const m = { all: items.length, rfp: 0, competitor: 0 };
    for (const it of items) m[it.scope] = (m[it.scope] || 0) + 1;
    return m;
  }, [items]);

  return (
    <Card sx={{ borderRadius: 3, height: '100%' }}>
      <CardContent sx={{ pb: 1 }}>
        {/* Header */}
        <Stack direction="row" alignItems="center" sx={{ mb: 1.5 }}>
          <ArticleIcon sx={{ color: 'primary.main', mr: 1 }} />
          <Box sx={{ flex: 1 }}>
            <Typography variant="overline" color="text.secondary"
                        sx={{ display: 'block', lineHeight: 1 }}>
              News feed
            </Typography>
            <Typography variant="caption" color="text.secondary">
              RFP-specific + tracked competitors
            </Typography>
          </Box>
          {onManageCompetitors && (
            <Tooltip title="Manage tracked competitors">
              <IconButton size="small" onClick={onManageCompetitors}>
                <SettingsIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="Pull latest articles">
            <span>
              <IconButton size="small" onClick={refresh}
                          disabled={refreshing || loading}>
                {refreshing
                  ? <CircularProgress size={18} />
                  : <RefreshIcon fontSize="small" />}
              </IconButton>
            </span>
          </Tooltip>
        </Stack>

        {/* Scope toggle */}
        <ToggleButtonGroup
          size="small" value={scope} exclusive
          onChange={(_, v) => v && setScope(v)}
          sx={{ mb: 1.5 }}
        >
          <ToggleButton value="all" sx={{ textTransform: 'none', px: 1.5 }}>
            All ({counts.all})
          </ToggleButton>
          <ToggleButton value="rfp" sx={{ textTransform: 'none', px: 1.5 }}>
            <PublicIcon sx={{ fontSize: 14, mr: 0.5 }} />
            RFP ({counts.rfp})
          </ToggleButton>
          <ToggleButton value="competitor" sx={{ textTransform: 'none', px: 1.5 }}>
            <BusinessIcon sx={{ fontSize: 14, mr: 0.5 }} />
            Competitors ({counts.competitor})
          </ToggleButton>
        </ToggleButtonGroup>

        {error && <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>{error}</Alert>}

        {/* Body */}
        {loading ? (
          <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={24} /></Box>
        ) : filtered.length === 0 ? (
          <Box sx={{ py: 4, textAlign: 'center' }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              {items.length === 0
                ? 'No articles yet. Click refresh to pull the latest news.'
                : `No items in the "${scope}" scope.`}
            </Typography>
            {items.length === 0 && (
              <Button size="small" startIcon={<RefreshIcon />} onClick={refresh}
                      disabled={refreshing}>
                Refresh now
              </Button>
            )}
          </Box>
        ) : (
          <Stack spacing={1} sx={{ maxHeight: 480, overflowY: 'auto', pr: 0.5 }}>
            {filtered.map((it) => {
              const sv = SCOPE_VISUAL[it.scope] || SCOPE_VISUAL.rfp;
              return (
                <Box key={it.id} sx={{
                  p: 1.5, borderRadius: 2,
                  border: '1px solid rgba(0,0,0,0.08)',
                  transition: 'all 0.2s ease',
                  '&:hover': { borderColor: '#00AEE6', bgcolor: 'rgba(0,174,230,0.03)' },
                }}>
                  <Stack direction="row" spacing={1} alignItems="flex-start">
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Stack direction="row" spacing={0.75} sx={{ mb: 0.5, flexWrap: 'wrap' }}>
                        <Chip
                          size="small"
                          label={it.scope === 'competitor' && it.competitor_name
                            ? it.competitor_name : sv.label}
                          icon={sv.icon}
                          sx={{ height: 20, fontSize: '0.68rem',
                                bgcolor: sv.color, color: '#fff' }}
                        />
                        {it.source && it.source !== 'mock' && (
                          <Chip size="small" variant="outlined" label={it.source}
                                sx={{ height: 20, fontSize: '0.65rem' }} />
                        )}
                        <Typography variant="caption" color="text.secondary">
                          {timeAgo(it.published_at || it.fetched_at)}
                        </Typography>
                      </Stack>
                      {it.url ? (
                        <Link
                          href={it.url} target="_blank" rel="noopener noreferrer"
                          underline="hover"
                          sx={{
                            display: 'block', fontWeight: 600, color: 'text.primary',
                            fontSize: '0.92rem', lineHeight: 1.3,
                            '&:hover': { color: 'primary.main' },
                          }}
                        >
                          {cleanTitle(it.title)}
                          <ExternalIcon sx={{
                            fontSize: 13, verticalAlign: 'baseline', ml: 0.5,
                            color: 'text.secondary',
                          }} />
                        </Link>
                      ) : (
                        <Typography variant="body2" fontWeight={600}>
                          {cleanTitle(it.title)}
                        </Typography>
                      )}
                      {it.summary && (
                        <Typography variant="caption" color="text.secondary"
                                    sx={{ display: 'block', mt: 0.5 }}>
                          {it.summary}
                        </Typography>
                      )}
                    </Box>
                    <Tooltip title="Hide this article">
                      <IconButton size="small" onClick={() => hide(it.id)}
                                  sx={{ opacity: 0.6, '&:hover': { opacity: 1 } }}>
                        <CloseIcon sx={{ fontSize: 16 }} />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                </Box>
              );
            })}
          </Stack>
        )}
      </CardContent>
      <Snackbar open={!!toast} autoHideDuration={4000} onClose={() => setToast(null)}
                message={toast || ''} />
    </Card>
  );
}
