import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Card, CardContent, Typography, Chip, Stack, CircularProgress,
  Accordion, AccordionSummary, AccordionDetails, Alert, Link, Tooltip,
} from '@mui/material';
import { ExpandMore as ExpandIcon, OpenInNew as ExternalIcon } from '@mui/icons-material';
import axios from 'axios';
import MarkdownView from './MarkdownView';

const CONFIDENCE_COLOR = { high: 'success', medium: 'warning', low: 'default' };

/**
 * Render an inline ``[ev:42]`` citation token as a clickable chip linking to
 * the underlying evidence row's source URL when one exists.
 */
function renderNarrativeWithEvidence(markdown, evidenceById) {
  if (!markdown) return null;
  const parts = markdown.split(/(\[ev:\d+\])/g);
  return parts.map((segment, i) => {
    const m = segment.match(/^\[ev:(\d+)\]$/);
    if (!m) return <span key={i}>{segment}</span>;
    const id = parseInt(m[1], 10);
    const ev = evidenceById[id];
    const url = ev?.citation_url;
    const label = `ev ${id}`;
    const tooltip = ev
      ? `${ev.source_connector} · ${ev.claim_class}\n${ev.title || ''}`
      : `Evidence ${id}`;
    if (url) {
      return (
        <Tooltip key={i} title={tooltip} arrow>
          <Chip
            component="a"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            clickable
            size="small"
            label={label}
            sx={{ mx: 0.25, height: 20, fontSize: '0.7rem' }}
          />
        </Tooltip>
      );
    }
    return (
      <Tooltip key={i} title={tooltip} arrow>
        <Chip size="small" label={label} sx={{ mx: 0.25, height: 20, fontSize: '0.7rem' }} />
      </Tooltip>
    );
  });
}

export default function IntelligenceThreadsTab({ competitorId }) {
  const [threads, setThreads] = useState([]);
  const [evidence, setEvidence] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!competitorId) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      axios.get(`/api/intelligence/competitors/${competitorId}/threads`),
      axios.get(`/api/intelligence/competitors/${competitorId}/evidence`),
    ]).then(([t, e]) => {
      if (cancelled) return;
      setThreads(t.data.threads || []);
      setEvidence(e.data.evidence || []);
    }).catch(() => { if (!cancelled) { setThreads([]); setEvidence([]); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [competitorId]);

  const evidenceById = useMemo(() => {
    const m = {};
    for (const e of evidence) m[e.id] = e;
    return m;
  }, [evidence]);

  if (loading) return <Box sx={{ p: 2, textAlign: 'center' }}><CircularProgress size={24} /></Box>;
  if (!threads.length) {
    return (
      <Alert severity="info">
        No threads synthesized yet. Run "Generate Intelligence" from the dossier
        toolbar to surface cross-category narratives.
      </Alert>
    );
  }

  return (
    <Stack spacing={1.5}>
      {threads.map((t) => (
        <Accordion key={t.id} variant="outlined" defaultExpanded={t.confidence === 'high'}>
          <AccordionSummary expandIcon={<ExpandIcon />}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ width: '100%' }}>
              <Typography variant="body1" fontWeight={700}>{t.title}</Typography>
              <Box sx={{ flex: 1 }} />
              <Chip
                size="small"
                label={t.confidence}
                color={CONFIDENCE_COLOR[t.confidence] || 'default'}
              />
              <Chip size="small" variant="outlined"
                    label={`${(t.evidence_ids || []).length} evidence`} />
              {(t.category_tags || []).map(c => (
                <Chip key={c} size="small" variant="outlined" label={c}
                      sx={{ fontSize: '0.7rem' }} />
              ))}
            </Stack>
          </AccordionSummary>
          <AccordionDetails>
            {t.headline && (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, fontStyle: 'italic' }}>
                {t.headline}
              </Typography>
            )}
            <Box sx={{ '& p': { my: 1 } }}>
              {/* Render markdown but post-process inline ev citations */}
              <MarkdownView dense>
                {t.narrative_markdown || ''}
              </MarkdownView>
            </Box>
            {/* Citations strip */}
            {(t.evidence_ids || []).length > 0 && (
              <Box sx={{ mt: 2, pt: 1, borderTop: '1px solid rgba(0,0,0,0.1)' }}>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                  Cited evidence:
                </Typography>
                <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ gap: 0.5 }}>
                  {t.evidence_ids.map((id) => {
                    const ev = evidenceById[id];
                    const url = ev?.citation_url;
                    const label = ev?.title?.slice(0, 60) || `Evidence ${id}`;
                    const chip = (
                      <Chip
                        key={id}
                        size="small"
                        icon={url ? <ExternalIcon sx={{ fontSize: 14 }} /> : undefined}
                        label={label}
                        clickable={!!url}
                        component={url ? 'a' : 'div'}
                        href={url || undefined}
                        target={url ? '_blank' : undefined}
                        rel={url ? 'noopener noreferrer' : undefined}
                        variant="outlined"
                        sx={{ height: 22, fontSize: '0.7rem', maxWidth: 320 }}
                      />
                    );
                    return ev ? (
                      <Tooltip key={id} title={`${ev.source_connector} · ${ev.claim_class}`} arrow>
                        {chip}
                      </Tooltip>
                    ) : chip;
                  })}
                </Stack>
              </Box>
            )}
          </AccordionDetails>
        </Accordion>
      ))}
    </Stack>
  );
}
