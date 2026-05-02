import React, { useMemo } from 'react';
import { Card, CardContent, Typography, Grid, Box, Chip, Divider } from '@mui/material';
import { AutoAwesome as AutoAwesomeIcon } from '@mui/icons-material';

const CATEGORY_LABELS = {
  leadership: 'Leadership',
  technology: 'Technology',
  customer_service: 'Customer Service',
  contracts: 'Contracts & Wins',
  financials: 'Financials',
  strengths: 'Strengths',
  weaknesses: 'Weaknesses',
  strategy: 'Strategy',
};

const CONFIDENCE_COLORS = { high: 'success', medium: 'warning', low: 'error', unverified: 'default' };

/**
 * Extract 1-2 sentence distilled takeaway from a markdown dossier entry so the
 * executive summary stays scannable. Strips markdown, bullets, headings and
 * trims to ~240 characters, ending on a sentence boundary when possible.
 */
const distill = (markdown = '') => {
  if (!markdown) return '';
  // Strip code fences, headings, bullets, emphasis, links.
  let text = markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(\*|_)(.*?)\1/g, '$2')
    .replace(/>\s?/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (text.length <= 260) return text;
  // Try to end on sentence boundary within window.
  const window = text.slice(0, 260);
  const lastStop = Math.max(window.lastIndexOf('. '), window.lastIndexOf('! '), window.lastIndexOf('? '));
  if (lastStop > 120) return window.slice(0, lastStop + 1).trim();
  return window.trim() + '…';
};

const DossierExecutiveSummary = ({ competitorName, entries = [] }) => {
  const summary = useMemo(
    () =>
      entries
        .slice()
        .sort((a, b) => (a.category || '').localeCompare(b.category || ''))
        .map((entry) => ({
          id: entry.id,
          category: entry.category,
          label: CATEGORY_LABELS[entry.category] || (entry.category || '').replace(/_/g, ' '),
          confidence: entry.confidence,
          takeaway: distill(entry.content || ''),
        })),
    [entries]
  );

  if (!summary.length) return null;

  return (
    <Card
      variant="outlined"
      sx={{
        mb: 2,
        borderRadius: 2,
        borderColor: 'rgba(0,174,230,0.35)',
        background:
          'linear-gradient(135deg, rgba(0,174,230,0.06) 0%, rgba(0,174,230,0.01) 100%)',
      }}
    >
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <AutoAwesomeIcon sx={{ color: '#00AEE6' }} fontSize="small" />
          <Typography variant="overline" fontWeight={800} sx={{ letterSpacing: 1 }}>
            Executive Summary
          </Typography>
          {competitorName && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
              {competitorName} · {summary.length} categor{summary.length === 1 ? 'y' : 'ies'}
            </Typography>
          )}
        </Box>
        <Divider sx={{ mb: 1.5 }} />
        <Grid container spacing={1.5}>
          {summary.map((item) => (
            <Grid item xs={12} md={6} key={item.id}>
              <Box
                sx={{
                  p: 1.25,
                  borderRadius: 1.5,
                  bgcolor: 'background.paper',
                  border: '1px solid',
                  borderColor: 'divider',
                  height: '100%',
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75 }}>
                  <Typography variant="subtitle2" fontWeight={700}>
                    {item.label}
                  </Typography>
                  {item.confidence && (
                    <Chip
                      label={item.confidence}
                      size="small"
                      color={CONFIDENCE_COLORS[item.confidence] || 'default'}
                      sx={{ ml: 'auto', height: 20, fontSize: 11 }}
                    />
                  )}
                </Box>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: 13, lineHeight: 1.5 }}>
                  {item.takeaway || '—'}
                </Typography>
              </Box>
            </Grid>
          ))}
        </Grid>
      </CardContent>
    </Card>
  );
};

export default DossierExecutiveSummary;
