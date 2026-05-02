import React, { useEffect, useState } from 'react';
import { useParams, Link as RouterLink, useNavigate } from 'react-router-dom';
import axios from 'axios';
import {
  Box,
  Paper,
  Typography,
  Stack,
  Chip,
  Alert,
  CircularProgress,
  Divider,
  Button,
  Breadcrumbs,
  Link,
  Tooltip,
  IconButton,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  Card,
  CardContent,
} from '@mui/material';
import {
  Description as DocIcon,
  ArrowBack as BackIcon,
  Link as LinkIcon,
  TableRows as TableIcon,
  MenuBook as GlossaryIcon,
  HelpOutline as RefIcon,
  CheckCircleOutline as VerbatimIcon,
  Warning as CompressedIcon,
} from '@mui/icons-material';
import { useProposal } from '../proposal/ProposalContext';

const PRIORITY_COLOR = {
  critical: 'error', high: 'warning', medium: 'info', low: 'default',
};

const MATCH_QUALITY_LABEL = {
  verbatim: { label: 'verbatim source', color: 'success', icon: <VerbatimIcon fontSize="small" /> },
  whitespace: { label: 'verbatim (whitespace differs)', color: 'success', icon: <VerbatimIcon fontSize="small" /> },
  alnum_compressed: { label: 'layout-compressed (form/table)', color: 'warning', icon: <CompressedIcon fontSize="small" /> },
  span_head: { label: 'spans chunks (start matched)', color: 'info', icon: <CompressedIcon fontSize="small" /> },
  span_middle: { label: 'spans chunks (middle matched)', color: 'info', icon: <CompressedIcon fontSize="small" /> },
  span_tail: { label: 'spans chunks (end matched)', color: 'info', icon: <CompressedIcon fontSize="small" /> },
  unfindable: { label: 'source not found', color: 'error', icon: <CompressedIcon fontSize="small" /> },
  unlinked: { label: 'no link', color: 'error', icon: <CompressedIcon fontSize="small" /> },
};

export default function RequirementDetail() {
  const { requirementId } = useParams();
  const { proposalId } = useProposal();
  const navigate = useNavigate();
  const [bundle, setBundle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [highlightSrc, setHighlightSrc] = useState('');

  useEffect(() => {
    setLoading(true);
    axios
      .get(`/api/knowledge/requirements/${requirementId}/bundle`)
      .then((res) => {
        setBundle(res.data);
        setHighlightSrc(res.data?.requirement?.source_text || '');
        setError(null);
      })
      .catch((err) => {
        setError(err?.response?.data?.detail || err.message || 'Failed to load');
      })
      .finally(() => setLoading(false));
  }, [requirementId]);

  if (loading) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (error) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{error}</Alert>
      </Box>
    );
  }
  if (!bundle) return null;

  const r = bundle.requirement;
  const src = bundle.source || {};
  const chunk = src.chunk;
  const matchInfo = MATCH_QUALITY_LABEL[src.match_quality] || MATCH_QUALITY_LABEL.unlinked;

  const browseUrl = proposalId ? `/p/${proposalId}/requirement-browser` : '/requirement-browser';

  return (
    <Box sx={{ p: 3 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Button startIcon={<BackIcon />} component={RouterLink} to={browseUrl}>
          Back to browser
        </Button>
      </Stack>

      <Paper sx={{ p: 3, mb: 2 }}>
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          {bundle.theme?.label ? (
            <Chip label={bundle.theme.label} color="primary" variant="outlined" />
          ) : null}
          {r.priority ? (
            <Chip label={`priority: ${r.priority}`} color={PRIORITY_COLOR[r.priority] || 'default'} />
          ) : null}
          {r.category ? <Chip label={r.category} variant="outlined" /> : null}
          {r.requirement_class && r.requirement_class !== 'unclassified' ? (
            <Chip label={r.requirement_class} variant="outlined" />
          ) : null}
          {r.compliance_status && r.compliance_status !== 'not_assessed' ? (
            <Chip label={r.compliance_status} color="success" variant="outlined" />
          ) : null}
        </Stack>

        <Typography variant="h5" sx={{ mb: 1 }}>{r.title}</Typography>

        {bundle.breadcrumb && bundle.breadcrumb.length ? (
          <Breadcrumbs sx={{ mb: 1 }}>
            {bundle.breadcrumb.map((b, i) => (
              <Typography key={i} variant="body2" color="text.secondary">
                {b.section_code}{b.title ? ` — ${b.title}` : ''}
              </Typography>
            ))}
          </Breadcrumbs>
        ) : (
          <Typography variant="caption" color="text.secondary">
            {r.section_id ? `Section: ${r.section_id}` : ''}
            {src.document_name ? ` · ${src.document_name}` : ''}
            {r.source_page ? ` · p.${r.source_page}` : ''}
          </Typography>
        )}

        <Divider sx={{ my: 2 }} />

        <Typography variant="overline" color="text.secondary">Description (paraphrase)</Typography>
        <Typography variant="body1" sx={{ mb: 2 }}>{r.description}</Typography>

        <Typography variant="overline" color="text.secondary">
          Verbatim source quote &nbsp;
          <Chip size="small" icon={matchInfo.icon} label={matchInfo.label} color={matchInfo.color} variant="outlined" />
        </Typography>
        <Paper variant="outlined" sx={{ p: 2, mt: 1, bgcolor: 'grey.50', fontFamily: 'serif' }}>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{r.source_text}</Typography>
        </Paper>
      </Paper>

      {/* Source paragraph (full surrounding context) */}
      {chunk ? (
        <Paper sx={{ p: 3, mb: 2 }}>
          <Typography variant="overline" color="text.secondary">
            <DocIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
            Source paragraph
            <Typography component="span" variant="caption" sx={{ ml: 1 }}>
              {src.document_name} · chunk #{chunk.id} · p.{chunk.page_number}
            </Typography>
          </Typography>
          {bundle.source.context_before?.map((c) => (
            <Typography key={c.id} variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', mt: 1 }}>
              {c.content}
            </Typography>
          ))}
          <Box sx={{ mt: 1, p: 1.5, bgcolor: 'info.light', borderLeft: '4px solid', borderColor: 'info.main', borderRadius: 1 }}>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
              <Highlighted text={chunk.content} needle={highlightSrc} />
            </Typography>
          </Box>
          {bundle.source.context_after?.map((c) => (
            <Typography key={c.id} variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', mt: 1 }}>
              {c.content}
            </Typography>
          ))}
        </Paper>
      ) : null}

      {/* References (forward) */}
      {bundle.references?.length ? (
        <Paper sx={{ p: 3, mb: 2 }}>
          <Typography variant="overline" color="text.secondary">
            <LinkIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
            Cross-references in this requirement ({bundle.references.length})
          </Typography>
          <Stack spacing={1.5} sx={{ mt: 1 }}>
            {bundle.references.map((ref, i) => (
              <Card key={i} variant="outlined">
                <CardContent sx={{ pb: '12px !important' }}>
                  <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                    <Chip size="small" label={ref.kind} color="primary" variant="outlined" />
                    <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                      “{ref.label}”
                    </Typography>
                    <Box sx={{ flex: 1 }} />
                    <Chip size="small" label={ref.target_kind} color={ref.target_kind === 'unresolved' ? 'default' : 'success'} variant="outlined" />
                  </Stack>
                  {ref.target?.section_code ? (
                    <Typography variant="body2">
                      → <strong>{ref.target.section_code}</strong>
                      {ref.target.section_title ? ` — ${ref.target.section_title}` : ''}
                      {ref.target.document_name ? (
                        <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                          ({ref.target.document_name})
                        </Typography>
                      ) : null}
                    </Typography>
                  ) : ref.target?.document_name ? (
                    <Typography variant="body2">→ {ref.target.document_name}</Typography>
                  ) : null}
                  {ref.target?.excerpt ? (
                    <Paper variant="outlined" sx={{ p: 1, mt: 1, bgcolor: 'grey.50' }}>
                      <Typography variant="caption" sx={{ whiteSpace: 'pre-wrap' }}>{ref.target.excerpt}</Typography>
                    </Paper>
                  ) : null}
                </CardContent>
              </Card>
            ))}
          </Stack>
        </Paper>
      ) : null}

      {/* Sub-parts (children of this parent obligation) */}
      {bundle.sub_parts?.length ? (
        <Paper sx={{ p: 3, mb: 2 }}>
          <Typography variant="overline" color="text.secondary">
            Sub-parts of this obligation ({bundle.sub_parts.length})
          </Typography>
          <Stack spacing={1.5} sx={{ mt: 1 }}>
            {bundle.sub_parts.map((sp) => (
              <Box key={sp.id} sx={{ pl: 2, borderLeft: '3px solid', borderColor: 'secondary.light' }}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <RouterLink
                    to={proposalId ? `/p/${proposalId}/requirement/${sp.id}` : `/requirement/${sp.id}`}
                    style={{ textDecoration: 'none', color: 'inherit', flex: 1 }}
                  >
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{sp.title}</Typography>
                  </RouterLink>
                  {sp.priority ? <Chip size="small" label={sp.priority} color={PRIORITY_COLOR[sp.priority] || 'default'} /> : null}
                </Stack>
                {sp.description ? (
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                    {sp.description}
                  </Typography>
                ) : null}
              </Box>
            ))}
          </Stack>
        </Paper>
      ) : null}

      {/* Parent pointer (if this is a sub-part) */}
      {r.rollup_parent_id ? (
        <Paper sx={{ p: 2, mb: 2, bgcolor: 'grey.50' }}>
          <Typography variant="caption" color="text.secondary">
            This is a sub-part of a larger obligation —{' '}
            <RouterLink
              to={proposalId ? `/p/${proposalId}/requirement/${r.rollup_parent_id}` : `/requirement/${r.rollup_parent_id}`}
              style={{ color: 'inherit' }}
            >
              view the parent
            </RouterLink>
          </Typography>
        </Paper>
      ) : null}

      {/* Glossary terms appearing here */}
      {bundle.glossary_terms?.length ? (
        <Paper sx={{ p: 3, mb: 2 }}>
          <Typography variant="overline" color="text.secondary">
            <GlossaryIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
            Defined terms in this requirement ({bundle.glossary_terms.length})
          </Typography>
          <Stack spacing={1} sx={{ mt: 1 }}>
            {bundle.glossary_terms.map((g, i) => (
              <Box key={i}>
                <Typography variant="body2"><strong>{g.term}</strong></Typography>
                <Typography variant="body2" color="text.secondary">{g.definition}</Typography>
              </Box>
            ))}
          </Stack>
        </Paper>
      ) : null}

      {/* Tables in same section */}
      {bundle.tables_in_section?.length ? (
        <Paper sx={{ p: 3, mb: 2 }}>
          <Typography variant="overline" color="text.secondary">
            <TableIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
            Tables in this section ({bundle.tables_in_section.length})
          </Typography>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {bundle.tables_in_section.map((t) => (
              <Box key={t.id}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {t.title || `Table #${t.id}`}
                  <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                    {t.n_rows} rows · {t.n_cols} cols
                  </Typography>
                </Typography>
                <Table size="small" sx={{ mt: 0.5, maxWidth: 900 }}>
                  {t.header ? (
                    <TableHead>
                      <TableRow>
                        {t.header.map((c, j) => (
                          <TableCell key={j} sx={{ fontWeight: 600 }}>{c}</TableCell>
                        ))}
                      </TableRow>
                    </TableHead>
                  ) : null}
                  <TableBody>
                    {t.preview_rows.map((row, ri) => (
                      <TableRow key={ri}>
                        {row.map((cell, cj) => (
                          <TableCell key={cj}>{cell}</TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            ))}
          </Stack>
        </Paper>
      ) : null}

      {/* Reverse refs */}
      {bundle.reverse_refs?.length ? (
        <Paper sx={{ p: 3, mb: 2 }}>
          <Typography variant="overline" color="text.secondary">
            <RefIcon fontSize="small" sx={{ verticalAlign: 'middle', mr: 0.5 }} />
            Other requirements that reference this one ({bundle.reverse_refs.length})
          </Typography>
          <Stack spacing={0.5} sx={{ mt: 1 }}>
            {bundle.reverse_refs.map((rr, i) => (
              <Link
                key={i}
                component={RouterLink}
                to={proposalId ? `/p/${proposalId}/requirement/${rr.requirement_id}` : `/requirement/${rr.requirement_id}`}
                variant="body2"
              >
                {rr.section_id ? `[${rr.section_id}] ` : ''}{rr.title}
              </Link>
            ))}
          </Stack>
        </Paper>
      ) : null}
    </Box>
  );
}

/** Highlight occurrences of `needle` (case-insensitive) inside `text`. */
function Highlighted({ text, needle }) {
  if (!text) return null;
  if (!needle || needle.length < 4) return <>{text}</>;
  const lower = text.toLowerCase();
  const n = needle.toLowerCase();
  const idx = lower.indexOf(n);
  if (idx === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <mark style={{ background: '#fff59d', padding: '1px 2px' }}>
        {text.slice(idx, idx + needle.length)}
      </mark>
      {text.slice(idx + needle.length)}
    </>
  );
}
