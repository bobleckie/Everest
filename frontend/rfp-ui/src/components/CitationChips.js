import React, { useMemo, useState, useCallback } from 'react';
import {
  Box, Chip, Drawer, Typography, IconButton, Divider, Stack,
  LinearProgress, Paper, Alert, Tooltip, Button,
} from '@mui/material';
import {
  Close as CloseIcon,
  Link as LinkIcon,
  MenuBook as BookIcon,
  AttachFile as AttachmentIcon,
  BookmarkBorder as SectionIcon,
} from '@mui/icons-material';
import axios from 'axios';

/* --------------------------------------------------------------------------
 * CitationChips
 *
 * Scans a requirement's text for internal document cross-references
 * (Section 3.2, Attachment 4, Appendix B, Exhibit A, Schedule 2, Paragraph
 * 5(b), Article 7, Clause 4.11) and renders each as a clickable Chip. Clicking
 * a chip opens a side Drawer that fetches the resolved matches from the
 * backend and shows where to read them.
 *
 * Props:
 *   requirementId: number  -- RfpRequirement.id (used to hit /citations)
 *   text: string | string[] -- the text(s) to scan for citations (typically
 *                              description and/or source_text)
 *
 * Back-end endpoint: GET /api/knowledge/requirements/{id}/citations
 * ------------------------------------------------------------------------ */

// Client-side regexes — kept parallel to app/services/citation_resolver.py so
// we only show chips when we're confident the server will find something too.
// If the patterns ever diverge, the worst case is an empty drawer for that
// chip (drawer shows "no matches found" gracefully).
const SECTION_NUM = String.raw`\d+(?:\.\d+)*(?:\([a-zA-Z0-9]+\))*`;

const PATTERNS = [
  { kind: 'appendix',   re: /\bAppendix\s+([A-Z]\d*|\d+)\b/gi },
  { kind: 'attachment', re: /\bAttachment\s+([A-Z]\d*|\d+)\b/gi },
  { kind: 'exhibit',    re: /\bExhibit\s+([A-Z]\d*|\d+)\b/gi },
  { kind: 'schedule',   re: /\bSchedule\s+([A-Z]\d*|\d+)\b/gi },
  { kind: 'section',    re: new RegExp(`(?:\\bSection\\s+|§\\s*)(${SECTION_NUM})`, 'gi') },
  { kind: 'paragraph',  re: new RegExp(`\\bParagraph\\s+(${SECTION_NUM})`, 'gi') },
  { kind: 'article',    re: new RegExp(`\\bArticle\\s+(${SECTION_NUM})`, 'gi') },
  { kind: 'clause',     re: new RegExp(`\\bClause\\s+(${SECTION_NUM})`, 'gi') },
];

const KIND_LABEL = {
  section: 'Sec.', paragraph: 'Para.', article: 'Art.', clause: 'Cl.',
  appendix: 'App.', attachment: 'Att.', exhibit: 'Ex.', schedule: 'Sch.',
};

const KIND_COLOR = {
  section: 'primary', paragraph: 'primary', article: 'primary', clause: 'primary',
  appendix: 'secondary', attachment: 'secondary', exhibit: 'secondary', schedule: 'secondary',
};

const KIND_ICON = {
  section: <SectionIcon sx={{ fontSize: 14 }} />,
  paragraph: <SectionIcon sx={{ fontSize: 14 }} />,
  article: <SectionIcon sx={{ fontSize: 14 }} />,
  clause: <SectionIcon sx={{ fontSize: 14 }} />,
  appendix: <BookIcon sx={{ fontSize: 14 }} />,
  attachment: <AttachmentIcon sx={{ fontSize: 14 }} />,
  exhibit: <BookIcon sx={{ fontSize: 14 }} />,
  schedule: <BookIcon sx={{ fontSize: 14 }} />,
};

function extractCitations(texts) {
  const haystack = (Array.isArray(texts) ? texts : [texts])
    .filter(Boolean)
    .join(' ');
  if (!haystack) return [];
  const seen = new Set();
  const out = [];
  for (const { kind, re } of PATTERNS) {
    // Clone the regex so we don't share lastIndex state across renders.
    const rx = new RegExp(re.source, re.flags);
    let m;
    while ((m = rx.exec(haystack)) !== null) {
      const ref = (m[1] || '').trim();
      if (!ref) continue;
      const key = `${kind}::${ref.toLowerCase()}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ kind, ref, displayText: m[0], start: m.index });
    }
  }
  out.sort((a, b) => a.start - b.start);
  return out;
}

export default function CitationChips({ requirementId, text }) {
  const citations = useMemo(() => extractCitations(text), [text]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [resolved, setResolved] = useState([]);          // full list from server
  const [focusedCitation, setFocusedCitation] = useState(null);

  const openDrawerFor = useCallback(async (citation) => {
    setFocusedCitation(citation);
    setOpen(true);
    // Only fetch once per requirement; we already have all resolved citations
    // in `resolved` if it's non-empty.
    if (resolved.length > 0) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await axios.get(
        `/api/knowledge/requirements/${requirementId}/citations`
      );
      setResolved(data?.citations || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load citations');
    } finally {
      setLoading(false);
    }
  }, [requirementId, resolved.length]);

  const handleClose = useCallback(() => setOpen(false), []);

  if (!citations.length) return null;

  // When the drawer is open, find the server-resolved record matching the
  // chip the user clicked. Fall back to "no matches" if the server saw
  // nothing (e.g. the citation points outside this proposal).
  const focusedResolved = focusedCitation && resolved.find(
    r => r.kind === focusedCitation.kind
      && (r.ref || '').toLowerCase() === focusedCitation.ref.toLowerCase()
  );

  return (
    <>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.5, rowGap: 0.5 }}>
        {citations.map((c) => (
          <Tooltip key={`${c.kind}-${c.ref}-${c.start}`} title={`Open ${c.displayText}`}>
            <Chip
              size="small"
              clickable
              icon={KIND_ICON[c.kind]}
              label={`${KIND_LABEL[c.kind] || c.kind} ${c.ref}`}
              color={KIND_COLOR[c.kind] || 'default'}
              variant="outlined"
              onClick={(e) => { e.stopPropagation(); openDrawerFor(c); }}
              sx={{ height: 22, fontSize: 11, '& .MuiChip-icon': { ml: 0.5, mr: -0.5 } }}
            />
          </Tooltip>
        ))}
      </Stack>

      <Drawer
        anchor="right"
        open={open}
        onClose={handleClose}
        PaperProps={{ sx: { width: { xs: '100%', sm: 520 } } }}
      >
        <Box sx={{ p: 2 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
            <LinkIcon color="primary" />
            <Typography variant="h6" sx={{ flex: 1, minWidth: 0 }}>
              {focusedCitation?.displayText || 'Citations'}
            </Typography>
            <IconButton size="small" onClick={handleClose}><CloseIcon /></IconButton>
          </Stack>
          <Divider sx={{ mb: 2 }} />

          {loading && <LinearProgress />}
          {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}

          {!loading && !error && focusedResolved && focusedResolved.matches.length === 0 && (
            <Alert severity="info">
              No matches found for <strong>{focusedCitation.displayText}</strong> in this proposal.
              It may refer to an external document or a section that wasn't extracted.
            </Alert>
          )}

          {!loading && !error && focusedResolved && focusedResolved.matches.length > 0 && (
            <Stack spacing={1.5}>
              <Typography variant="caption" color="text.secondary">
                {focusedResolved.matches.length} match{focusedResolved.matches.length === 1 ? '' : 'es'} in this proposal
              </Typography>
              {focusedResolved.matches.map((m, idx) => (
                <Paper
                  key={`${m.source}-${m.document_id}-${m.page}-${m.requirement_id || idx}`}
                  variant="outlined"
                  sx={{ p: 1.5 }}
                >
                  <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
                    <Box sx={{ minWidth: 0, flex: 1 }}>
                      {m.source === 'requirement' ? (
                        <Typography variant="body2" fontWeight={700}>
                          {m.requirement_ref ? `[${m.requirement_ref}] ` : ''}{m.requirement_title || '(untitled)'}
                        </Typography>
                      ) : (
                        <Typography variant="body2" fontWeight={700}>
                          Document excerpt
                        </Typography>
                      )}
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {m.document_name || `doc #${m.document_id}`}
                        {m.page ? ` · p. ${m.page}` : ''}
                        {m.requirement_section ? ` · §${m.requirement_section}` : ''}
                      </Typography>
                    </Box>
                    <Chip
                      size="small"
                      label={m.source}
                      color={m.source === 'requirement' ? 'primary' : 'default'}
                      variant="outlined"
                      sx={{ height: 20, fontSize: 10 }}
                    />
                  </Stack>
                  {m.snippet && (
                    <Box sx={{
                      mt: 1, p: 1,
                      backgroundColor: '#f5f7fa',
                      borderLeft: '3px solid #00AEE6',
                      fontFamily: 'Georgia, serif',
                      fontSize: '0.85rem',
                      whiteSpace: 'pre-wrap',
                      maxHeight: 220,
                      overflow: 'auto',
                    }}>
                      {m.snippet}
                    </Box>
                  )}
                </Paper>
              ))}
            </Stack>
          )}

          {/* If the drawer opened before the fetch resolved, just show a spinner */}
          {!loading && !error && !focusedResolved && resolved.length === 0 && (
            <Alert severity="info">Loading citation…</Alert>
          )}
        </Box>
      </Drawer>
    </>
  );
}
