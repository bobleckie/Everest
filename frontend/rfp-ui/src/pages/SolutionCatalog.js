import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  Alert,
  CircularProgress,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Divider,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  Tooltip,
  ToggleButton,
  ToggleButtonGroup,
  TextField,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  Workspaces as ThemeIcon,
  CheckCircle as EvidenceIcon,
  TrendingUp as MetricIcon,
  Star as DiffIcon,
  Warning as GapIcon,
  Article as DocumentIcon,
  ViewList as BrowseIcon,
  FileDownload as ExportIcon,
} from '@mui/icons-material';
import { Button } from '@mui/material';

/**
 * Parsons Solution Catalog — one entry per requirement theme.
 * State-neutral capability write-ups synthesized from the ingested
 * Parsons knowledge corpus. Used as the source of truth when drafting
 * per-requirement responses.
 */
export default function SolutionCatalog() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [categoryFilter, setCategoryFilter] = useState('');
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState('browse'); // 'browse' | 'document'

  const exportDocx = async () => {
    try {
      const res = await axios.get('/api/knowledge/solution-catalog.docx', { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'parsons-solution-document.docx';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Export failed');
    }
  };

  useEffect(() => {
    setLoading(true);
    axios
      .get('/api/knowledge/solution-catalog')
      .then((res) => { setEntries(res.data.entries || []); setError(null); })
      .catch((err) => setError(err?.response?.data?.detail || err.message || 'Failed'))
      .finally(() => setLoading(false));
  }, []);

  const grouped = useMemo(() => {
    const filtered = entries.filter((e) => {
      if (categoryFilter && e.category !== categoryFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        const blob = `${e.theme_label} ${e.title} ${e.capability_statement}`.toLowerCase();
        if (!blob.includes(q)) return false;
      }
      return true;
    });
    const byCat = {};
    filtered.forEach((e) => {
      if (!byCat[e.category]) byCat[e.category] = [];
      byCat[e.category].push(e);
    });
    return byCat;
  }, [entries, categoryFilter, search]);

  if (loading) return <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress /></Box>;
  if (error) return <Box sx={{ p: 3 }}><Alert severity="error">{error}</Alert></Box>;

  const totalReqs = entries.reduce((acc, e) => acc + e.n_requirements_addressed, 0);
  const totalNpp = entries.reduce((acc, e) => acc + (e.named_past_performance?.length || 0), 0);
  const totalQo = entries.reduce((acc, e) => acc + (e.quantified_outcomes?.length || 0), 0);
  const categories = Array.from(new Set(entries.map((e) => e.category)));

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2, flexWrap: 'wrap' }}>
        <Typography variant="h5">Parsons Solution Document</Typography>
        <Chip label={`${entries.length} entries`} variant="outlined" />
        <Chip label={`${totalReqs} requirements covered`} variant="outlined" />
        <Chip label={`${totalNpp} past-performance citations`} variant="outlined" />
        <Chip label={`${totalQo} quantified outcomes`} variant="outlined" />
        <Box sx={{ flex: 1 }} />
        <ToggleButtonGroup
          size="small"
          value={viewMode}
          exclusive
          onChange={(_, v) => v && setViewMode(v)}
        >
          <ToggleButton value="browse">
            <BrowseIcon fontSize="small" sx={{ mr: 0.5 }} /> Browse
          </ToggleButton>
          <ToggleButton value="document">
            <DocumentIcon fontSize="small" sx={{ mr: 0.5 }} /> Read as document
          </ToggleButton>
        </ToggleButtonGroup>
        <Button variant="contained" size="small" startIcon={<ExportIcon />} onClick={exportDocx}>
          Export .docx
        </Button>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        State-neutral capability write-ups synthesized from the ingested Parsons evidence
        corpus. Each entry covers one requirement theme. Used as the canonical source of
        truth when drafting per-requirement responses to the live 2026 NJ MVC RFP.
      </Typography>

      <Stack direction="row" spacing={2} sx={{ mb: 2 }} alignItems="center">
        <ToggleButtonGroup
          size="small"
          value={categoryFilter}
          exclusive
          onChange={(_, v) => setCategoryFilter(v || '')}
        >
          <ToggleButton value="">All</ToggleButton>
          {categories.map((c) => (
            <ToggleButton key={c} value={c}>{c}</ToggleButton>
          ))}
        </ToggleButtonGroup>
        <TextField
          size="small"
          placeholder="Search title / theme / capability text…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ flex: 1 }}
        />
      </Stack>

      {viewMode === 'browse' ? (
        <>
          {Object.keys(grouped).sort().map((cat) => (
            <Box key={cat} sx={{ mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 1 }}>{cat}</Typography>
              {grouped[cat].map((e) => (
                <CatalogEntry key={e.id} entry={e} />
              ))}
            </Box>
          ))}
        </>
      ) : (
        <DocumentView grouped={grouped} />
      )}
      {Object.keys(grouped).length === 0 ? (
        <Alert severity="info">No entries match the current filter.</Alert>
      ) : null}
    </Box>
  );
}

function DocumentView({ grouped }) {
  return (
    <Paper sx={{ p: 4, maxWidth: 1000, mx: 'auto' }}>
      <Typography variant="h4" sx={{ textAlign: 'center', mb: 1 }}>
        Parsons Solution Document
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', textAlign: 'center', mb: 4 }}>
        State-neutral capability write-ups for the 2026 NJ MVC RFP
      </Typography>

      {Object.keys(grouped).sort().map((cat) => (
        <Box key={cat} sx={{ mb: 5 }}>
          <Typography variant="h5" sx={{
            mt: 4, mb: 2, pb: 1,
            borderBottom: '2px solid', borderColor: 'primary.main',
          }}>
            {cat}
          </Typography>
          {grouped[cat].map((e) => (
            <Box key={e.id} sx={{ mb: 4 }}>
              <Typography variant="h6" sx={{ mt: 3, mb: 0.5 }}>
                {e.theme_label}
              </Typography>
              <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 2 }}>
                {e.title} · covers {e.n_requirements_addressed} RFP requirements
              </Typography>

              {e.capability_statement ? (
                <>
                  <Typography variant="overline" color="primary">Capability Statement</Typography>
                  <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', mb: 2, lineHeight: 1.7 }}>
                    {e.capability_statement}
                  </Typography>
                </>
              ) : null}

              {e.named_past_performance?.length ? (
                <>
                  <Typography variant="overline" color="primary">Named Past Performance</Typography>
                  <Box component="ul" sx={{ mb: 2, pl: 3 }}>
                    {e.named_past_performance.map((p, i) => (
                      <li key={i}>
                        <Typography variant="body2">
                          <strong>{p.name}</strong> — {p.scope}. <em>Outcome: {p.outcome}</em>
                        </Typography>
                      </li>
                    ))}
                  </Box>
                </>
              ) : null}

              {e.quantified_outcomes?.length ? (
                <>
                  <Typography variant="overline" color="primary">Quantified Outcomes</Typography>
                  <Box component="ul" sx={{ mb: 2, pl: 3 }}>
                    {e.quantified_outcomes.map((q, i) => (
                      <li key={i}>
                        <Typography variant="body2">
                          <strong>{q.metric}:</strong> {q.value} <em>({q.context})</em>
                        </Typography>
                      </li>
                    ))}
                  </Box>
                </>
              ) : null}

              {e.differentiators ? (
                <>
                  <Typography variant="overline" color="primary">Differentiators</Typography>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mb: 2, lineHeight: 1.7 }}>
                    {e.differentiators}
                  </Typography>
                </>
              ) : null}

              {e.gap_notes ? (
                <Box sx={{ p: 2, bgcolor: 'warning.light', borderRadius: 1, mb: 2 }}>
                  <Typography variant="overline" color="warning.dark">
                    Gap Notes — what evidence does NOT substantiate
                  </Typography>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
                    {e.gap_notes}
                  </Typography>
                </Box>
              ) : null}
            </Box>
          ))}
        </Box>
      ))}
    </Paper>
  );
}

function CatalogEntry({ entry }) {
  const npp = entry.named_past_performance || [];
  const qo = entry.quantified_outcomes || [];
  return (
    <Accordion sx={{ mb: 1 }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ flex: 1 }}>
          <ThemeIcon fontSize="small" />
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            {entry.theme_label}
          </Typography>
          <Box sx={{ flex: 1 }} />
          <Chip size="small" label={`${entry.n_requirements_addressed} reqs`} />
          {npp.length ? <Chip size="small" icon={<EvidenceIcon />} label={`${npp.length} past perf`} variant="outlined" /> : null}
          {qo.length ? <Chip size="small" icon={<MetricIcon />} label={`${qo.length} metrics`} variant="outlined" /> : null}
        </Stack>
      </AccordionSummary>
      <AccordionDetails>
        <Typography variant="overline" color="text.secondary">{entry.title}</Typography>
        <Divider sx={{ my: 1 }} />

        <Typography variant="overline">Capability statement</Typography>
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mb: 2 }}>
          {entry.capability_statement}
        </Typography>

        {npp.length ? (
          <>
            <Typography variant="overline">
              <EvidenceIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
              Named past performance ({npp.length})
            </Typography>
            <Table size="small" sx={{ mb: 2 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Scope</TableCell>
                  <TableCell>Outcome</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {npp.map((p, i) => (
                  <TableRow key={i}>
                    <TableCell sx={{ fontWeight: 500 }}>{p.name}</TableCell>
                    <TableCell>{p.scope}</TableCell>
                    <TableCell>{p.outcome}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        ) : null}

        {qo.length ? (
          <>
            <Typography variant="overline">
              <MetricIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
              Quantified outcomes ({qo.length})
            </Typography>
            <Table size="small" sx={{ mb: 2 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Metric</TableCell>
                  <TableCell>Value</TableCell>
                  <TableCell>Context</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {qo.map((o, i) => (
                  <TableRow key={i}>
                    <TableCell sx={{ fontWeight: 500 }}>{o.metric}</TableCell>
                    <TableCell>{o.value}</TableCell>
                    <TableCell>{o.context}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        ) : null}

        {entry.differentiators ? (
          <>
            <Typography variant="overline">
              <DiffIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
              Differentiators
            </Typography>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mb: 2 }}>
              {entry.differentiators}
            </Typography>
          </>
        ) : null}

        {entry.gap_notes ? (
          <>
            <Typography variant="overline" color="warning.dark">
              <GapIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
              Gap notes — what evidence does NOT substantiate
            </Typography>
            <Paper variant="outlined" sx={{ p: 2, bgcolor: 'warning.light', mb: 2 }}>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                {entry.gap_notes}
              </Typography>
            </Paper>
          </>
        ) : null}

        <Typography variant="caption" color="text.secondary">
          Sourced from {entry.source_chunk_ids.length} Parsons evidence chunks · state-neutral synthesis
        </Typography>
      </AccordionDetails>
    </Accordion>
  );
}
