import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Box, Typography, Card, CardContent, Grid, Button, Chip, LinearProgress,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert,
  Fade, IconButton, Tooltip, MenuItem, Select, FormControl, InputLabel,
  Stack, Divider, Collapse,
  Tabs, Tab,
} from '@mui/material';
import {
  EventAvailable as EventIcon,
  Refresh as RefreshIcon,
  AutoAwesome as ExtractIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  Add as AddIcon,
  CheckCircle as CheckIcon,
  Warning as WarnIcon,
  Schedule as ClockIcon,
  Flag as FlagIcon,
  FileDownload as DownloadIcon,
  KeyboardArrowDown as ExpandIcon,
  KeyboardArrowRight as CollapseIconCol,
  Done as DoneIcon,
  Description as DocIcon,
  Replay as ReopenIcon,
} from '@mui/icons-material';
import axios from 'axios';
import { useProposal } from '../proposal/ProposalContext';

const EVENT_TYPES = [
  'question_period_start', 'question_period_end', 'pre_bid_conference',
  'mandatory_site_visit', 'addenda_cutoff', 'proposal_due', 'bid_opening',
  'evaluation_period_start', 'evaluation_period_end', 'bafo_due',
  'award_notification', 'contract_start', 'period_of_performance_start',
  'period_of_performance_end',
  'implementation_milestone',
  'internal_review', 'internal_sme_signoff', 'internal_red_team',
  'internal_pricing_final', 'internal_production',
  'other',
];

const TYPE_LABEL = {
  question_period_start: 'Q&A Period Opens',
  question_period_end: 'Questions Due',
  pre_bid_conference: 'Pre-Bid Conference',
  mandatory_site_visit: 'Site Visit',
  addenda_cutoff: 'Addenda Cutoff',
  proposal_due: 'Proposal Due',
  bid_opening: 'Bid Opening',
  evaluation_period_start: 'Evaluation Begins',
  evaluation_period_end: 'Evaluation Ends',
  bafo_due: 'BAFO Due',
  award_notification: 'Award Notification',
  contract_start: 'Contract Start',
  period_of_performance_start: 'PoP Start',
  period_of_performance_end: 'PoP End',
  implementation_milestone: 'Implementation Milestone',
  internal_review: 'Internal Review',
  internal_sme_signoff: 'SME Sign-Off',
  internal_red_team: 'Red-Team Review',
  internal_pricing_final: 'Pricing Final',
  internal_production: 'Production & Packaging',
  other: 'Other',
};

const CONFIDENCE_COLOR = { high: 'success', medium: 'warning', low: 'error' };
// Status enum (must match backend model RfpScheduleEvent.status)
const STATUS_COLOR = {
  upcoming: 'info', in_progress: 'warning', complete: 'success',
  cancelled: 'default', missed: 'error',
};
const STATUS_LABEL = {
  upcoming: 'Upcoming', in_progress: 'In Progress', complete: 'Complete',
  cancelled: 'Cancelled', missed: 'Missed',
};

function formatDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
      + (d.getHours() || d.getMinutes() ? ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');
  } catch { return iso; }
}

function daysFromNow(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const diffMs = d.getTime() - Date.now();
  return Math.ceil(diffMs / (1000 * 60 * 60 * 24));
}

// ── Expandable event row ─────────────────────────────────────────────
// Click the chevron (or row) to expand and see:
//  - Source document name + page + verbatim excerpt from the RFP
//  - Notes / assignee / verification info
//  - Quick "Mark Complete" / "Reopen" / "Verify" actions inline
function EventRow({ ev, onEdit, onDelete, onVerify, onStatusChange }) {
  const [open, setOpen] = useState(false);
  const isOverdue = ev.event_date && new Date(ev.event_date) < new Date()
    && !['complete', 'cancelled'].includes(ev.status);
  const isTerminal = ['complete', 'cancelled'].includes(ev.status);
  return (
    <>
      <TableRow hover sx={{ '& > *': { borderBottom: 'unset' },
                            ...(isOverdue && { bgcolor: 'rgba(198,40,40,0.04)' }) }}>
        <TableCell sx={{ width: 36, p: 0.5 }}>
          <IconButton size="small" onClick={() => setOpen(v => !v)}>
            {open ? <ExpandIcon fontSize="small" /> : <CollapseIconCol fontSize="small" />}
          </IconButton>
        </TableCell>
        <TableCell>
          <Chip size="small" variant="outlined"
            label={TYPE_LABEL[ev.event_type] || ev.event_type} />
        </TableCell>
        <TableCell sx={{ wordBreak: 'break-word' }}>{ev.label}</TableCell>
        <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDate(ev.event_date)}</TableCell>
        <TableCell sx={{ whiteSpace: 'nowrap' }}>{ev.end_date ? formatDate(ev.end_date) : '—'}</TableCell>
        <TableCell>
          {ev.document_id ? (
            <Tooltip title="Click row to expand for source excerpt">
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <DocIcon fontSize="inherit" color="action" />
                <Typography variant="caption" noWrap sx={{ maxWidth: 180 }}>
                  {ev.document_name || `doc #${ev.document_id}`}
                  {ev.source_page ? ` · p.${ev.source_page}` : ''}
                </Typography>
              </Stack>
            </Tooltip>
          ) : (
            <Typography variant="caption" color="text.secondary">
              {ev.extracted_by_ai ? 'AI extracted' : 'Manual'}
            </Typography>
          )}
        </TableCell>
        <TableCell>
          <Chip size="small" color={isOverdue ? 'error' : (STATUS_COLOR[ev.status] || 'default')}
                label={isOverdue ? 'OVERDUE' : (STATUS_LABEL[ev.status] || ev.status)} />
        </TableCell>
        <TableCell>
          <Chip size="small" variant="outlined"
            color={CONFIDENCE_COLOR[ev.confidence] || 'default'}
            label={ev.confidence || '—'} />
        </TableCell>
        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
          {!isTerminal ? (
            <Tooltip title="Mark this event Complete (so it stops showing as overdue)">
              <IconButton size="small" color="success" onClick={() => onStatusChange(ev, 'complete')}>
                <DoneIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          ) : (
            <Tooltip title="Reopen — set back to upcoming">
              <IconButton size="small" onClick={() => onStatusChange(ev, 'upcoming')}>
                <ReopenIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title={ev.verified ? 'Verified' : 'Mark verified'}>
            <IconButton size="small" onClick={() => onVerify(ev)}>
              {ev.verified ? <CheckIcon color="success" fontSize="small" /> : <WarnIcon color="disabled" fontSize="small" />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Edit">
            <IconButton size="small" onClick={() => onEdit(ev)}><EditIcon fontSize="small" /></IconButton>
          </Tooltip>
          <Tooltip title="Delete">
            <IconButton size="small" onClick={() => onDelete(ev)}><DeleteIcon fontSize="small" /></IconButton>
          </Tooltip>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={9} sx={{ p: 0, borderBottom: open ? undefined : 'none' }}>
          <Collapse in={open} timeout="auto" unmountOnExit>
            <Box sx={{ p: 2, bgcolor: 'rgba(0,0,0,0.02)' }}>
              {/* Source document + verbatim excerpt — primary detail */}
              {ev.document_id ? (
                <Box sx={{ mb: 2 }}>
                  <Typography variant="overline" color="text.secondary">
                    Source citation
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {ev.document_name || `Document #${ev.document_id}`}
                    {ev.source_page ? ` — page ${ev.source_page}` : ''}
                  </Typography>
                  {ev.source_text && (
                    <Box sx={{
                      mt: 1, p: 1.5, borderRadius: 1.5,
                      borderLeft: '3px solid',
                      borderLeftColor: 'primary.main',
                      bgcolor: 'background.paper',
                      fontStyle: 'italic',
                      fontSize: '0.88rem',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}>
                      "{ev.source_text}"
                    </Box>
                  )}
                </Box>
              ) : (
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  {ev.extracted_by_ai
                    ? 'No source document linked.'
                    : 'Manually entered — no document citation.'}
                </Typography>
              )}

              {/* Other metadata */}
              <Grid container spacing={2}>
                <Grid item xs={12} sm={6} md={3}>
                  <Typography variant="overline" color="text.secondary">Mandatory</Typography>
                  <Typography variant="body2">{ev.is_mandatory ? 'Yes' : 'No'}</Typography>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Typography variant="overline" color="text.secondary">Assignee</Typography>
                  <Typography variant="body2">{ev.assignee || '—'}</Typography>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Typography variant="overline" color="text.secondary">Verified by</Typography>
                  <Typography variant="body2">{ev.verified_by || '—'}</Typography>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Typography variant="overline" color="text.secondary">Source</Typography>
                  <Typography variant="body2">
                    {ev.extracted_by_ai ? 'AI extraction' : 'Manual entry'}
                  </Typography>
                </Grid>
                {ev.notes && (
                  <Grid item xs={12}>
                    <Typography variant="overline" color="text.secondary">Notes</Typography>
                    <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                      {ev.notes}
                    </Typography>
                  </Grid>
                )}
              </Grid>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}


// ── Implementation Plan tab ──────────────────────────────────────────
// Two display modes:
//   1) RELATIVE: no contract anchor yet — show a Gantt by offset_days
//      (each bar starts at Day 0 = "Start"). The lane width is based
//      on the maximum offset across all events, so the user sees the
//      full plan duration in days even with no real dates.
//   2) ABSOLUTE: contract_effective_date is set — the bars are placed
//      on a real-date timeline. Each bar starts on the resolved date.
//
// Bars are bucketed in two ways:
//   * "Submit with quote" (is_draft_with_quote) — yellow, sorted to top
//   * Standard delivery — blue, sorted by offset_days asc
//
// The chart is plain SVG. No new dependencies.
function ImplementationTab({
  events, contractAnchor, anchorDraft, setAnchorDraft, onSaveAnchor,
  working, proposalId, onEdit, onDelete, onStatusChange,
}) {
  // Ordered for display: drafts-with-quote first, then by offset_days,
  // then events that have absolute dates but no offset, then undated.
  const sorted = useMemo(() => {
    const arr = [...events];
    arr.sort((a, b) => {
      // Drafts-with-quote always at the top
      if (!!a.is_draft_with_quote !== !!b.is_draft_with_quote) {
        return b.is_draft_with_quote - a.is_draft_with_quote;
      }
      const ao = a.offset_days;
      const bo = b.offset_days;
      if (ao != null && bo != null) return ao - bo;
      if (ao != null) return -1;
      if (bo != null) return 1;
      const ad = a.event_date ? new Date(a.event_date).getTime() : Number.POSITIVE_INFINITY;
      const bd = b.event_date ? new Date(b.event_date).getTime() : Number.POSITIVE_INFINITY;
      return ad - bd;
    });
    return arr;
  }, [events]);

  const maxOffset = useMemo(
    () => sorted.reduce((m, e) => Math.max(m, e.offset_days || 0), 0),
    [sorted]
  );

  if (events.length === 0) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Typography variant="body2" color="text.secondary">
            No implementation milestones yet. On the <strong>Solicitation Schedule</strong> tab,
            run AI extraction on the RFP — the extractor now picks up plan deadlines
            expressed as "Start + Nd" (e.g. Mobilization Plan, Project Management Plan,
            QA Plan, NGSystem Training Plan, etc.) and stores them as offsets.
            They'll appear here once extracted.
          </Typography>
        </CardContent>
      </Card>
    );
  }

  return (
    <Stack spacing={2}>
      {/* Contract anchor card */}
      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap" useFlexGap>
            <Box sx={{ flex: 1, minWidth: 280 }}>
              <Typography variant="overline" color="text.secondary">
                Contract Effective Date (Start)
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Anchor for every "Start + Nd" deadline. Set this once the contract is awarded —
                the timeline will switch from days-based to calendar dates automatically.
              </Typography>
            </Box>
            <TextField
              size="small"
              type="date"
              label="Effective Date"
              InputLabelProps={{ shrink: true }}
              value={anchorDraft}
              onChange={(e) => setAnchorDraft(e.target.value)}
              sx={{ minWidth: 180 }}
              disabled={!proposalId}
            />
            <Button
              variant="contained"
              onClick={onSaveAnchor}
              disabled={working || !proposalId}
            >
              {anchorDraft ? 'Save & Compute Dates' : 'Clear & Show Days'}
            </Button>
          </Stack>
          {!proposalId && (
            <Alert severity="warning" variant="outlined" sx={{ mt: 2 }}>
              Select an active proposal to set its Contract Effective Date.
            </Alert>
          )}
          {proposalId && contractAnchor && (
            <Alert severity="success" variant="outlined" sx={{ mt: 2 }}>
              Anchor active: <strong>{new Date(contractAnchor).toLocaleDateString()}</strong>.
              Deadlines below show absolute dates.
            </Alert>
          )}
          {proposalId && !contractAnchor && (
            <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>
              No anchor set — showing the plan as a duration-based Gantt
              (Day 0 = Contract Start). Bars represent days from Start.
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* Gantt chart */}
      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="h6">
              {contractAnchor ? 'Implementation Timeline' : 'Implementation Plan (relative)'}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {contractAnchor
                ? `Start: ${new Date(contractAnchor).toLocaleDateString()}  ·  ${sorted.length} milestones`
                : `Max horizon: ${maxOffset} days  ·  ${sorted.length} milestones`}
            </Typography>
          </Stack>
          <ImplementationGantt
            events={sorted}
            maxOffset={maxOffset}
            contractAnchor={contractAnchor}
          />
        </CardContent>
      </Card>

      {/* Plan deliverables table */}
      <Card variant="outlined">
        <CardContent sx={{ p: 0 }}>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ '& th': { fontWeight: 700 } }}>
                  <TableCell>Plan / Deliverable</TableCell>
                  <TableCell sx={{ width: 110 }}>Offset</TableCell>
                  <TableCell sx={{ width: 130 }}>Computed date</TableCell>
                  <TableCell sx={{ width: 110 }}>Draft w/ Quote?</TableCell>
                  <TableCell>Section refs</TableCell>
                  <TableCell sx={{ width: 110 }}>Status</TableCell>
                  <TableCell align="right" sx={{ width: 120 }}>Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {sorted.map(ev => (
                  <TableRow key={ev.id} hover>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{ev.label}</Typography>
                      {ev.notes && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {ev.notes}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      {ev.offset_days != null ? (
                        <Chip
                          size="small"
                          variant="outlined"
                          label={
                            ev.offset_anchor === 'pop_end'
                              ? `End ${ev.offset_days >= 0 ? '+' : ''}${ev.offset_days}d`
                              : `Start ${ev.offset_days >= 0 ? '+' : ''}${ev.offset_days}d`
                          }
                        />
                      ) : '—'}
                    </TableCell>
                    <TableCell>
                      {ev.event_date
                        ? new Date(ev.event_date).toLocaleDateString()
                        : <Typography variant="caption" color="text.secondary">awaiting Start</Typography>}
                    </TableCell>
                    <TableCell>
                      {ev.is_draft_with_quote
                        ? <Chip size="small" color="warning" label="Yes" />
                        : <Typography variant="caption" color="text.secondary">No</Typography>}
                    </TableCell>
                    <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>
                      {ev.section_refs || '—'}
                    </TableCell>
                    <TableCell>
                      <Chip size="small" color={STATUS_COLOR[ev.status] || 'default'}
                            label={STATUS_LABEL[ev.status] || ev.status} />
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                      {ev.status !== 'complete' ? (
                        <Tooltip title="Mark Complete">
                          <IconButton size="small" color="success" onClick={() => onStatusChange(ev, 'complete')}>
                            <DoneIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      ) : (
                        <Tooltip title="Reopen">
                          <IconButton size="small" onClick={() => onStatusChange(ev, 'upcoming')}>
                            <ReopenIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                      <Tooltip title="Edit">
                        <IconButton size="small" onClick={() => onEdit(ev)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton size="small" onClick={() => onDelete(ev)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>
    </Stack>
  );
}


// ── Gantt chart (plain SVG, no new deps) ─────────────────────────────
// Each row is one milestone. The bar runs from Day 0 (Contract Start)
// to its offset_days value. When `contractAnchor` is set, the column
// labels switch from "Day N" to actual dates.
function ImplementationGantt({ events, maxOffset, contractAnchor }) {
  const ROW_HEIGHT = 28;
  const ROW_GAP = 4;
  const LEFT_LABEL_WIDTH = 280;
  const RIGHT_PADDING = 40;
  const TOP_PADDING = 30;
  const BOTTOM_PADDING = 12;
  const CHART_WIDTH = 1100; // SVG viewBox; the SVG itself is responsive.
  const trackWidth = CHART_WIDTH - LEFT_LABEL_WIDTH - RIGHT_PADDING;

  // Round the horizon up to the nearest 30, with a 60-day minimum.
  const horizonDays = Math.max(60, Math.ceil((maxOffset + 30) / 30) * 30);

  const xForDay = (d) => LEFT_LABEL_WIDTH + (d / horizonDays) * trackWidth;
  const formatDate = (offsetDays) => {
    if (!contractAnchor) return null;
    const d = new Date(contractAnchor);
    d.setDate(d.getDate() + offsetDays);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' });
  };

  // Tick marks at every 30 days (or 90 if horizon > 360).
  const tickInterval = horizonDays > 360 ? 90 : 30;
  const ticks = [];
  for (let d = 0; d <= horizonDays; d += tickInterval) ticks.push(d);

  const totalHeight = TOP_PADDING + events.length * (ROW_HEIGHT + ROW_GAP) + BOTTOM_PADDING;

  return (
    <Box sx={{ width: '100%', overflowX: 'auto' }}>
      <svg
        width="100%"
        viewBox={`0 0 ${CHART_WIDTH} ${totalHeight}`}
        style={{ minWidth: 700, fontFamily: 'inherit' }}
        role="img"
        aria-label="Implementation plan Gantt chart"
      >
        {/* Tick lines */}
        {ticks.map(d => (
          <g key={`tick-${d}`}>
            <line
              x1={xForDay(d)} x2={xForDay(d)}
              y1={TOP_PADDING - 6} y2={totalHeight - BOTTOM_PADDING}
              stroke="#e0e0e0"
              strokeDasharray={d === 0 ? '' : '4,4'}
              strokeWidth={d === 0 ? 1.5 : 1}
            />
            <text
              x={xForDay(d)} y={TOP_PADDING - 10}
              textAnchor="middle"
              fontSize="10"
              fill="#666"
            >
              {contractAnchor ? formatDate(d) : (d === 0 ? 'Start' : `Day ${d}`)}
            </text>
          </g>
        ))}
        {/* Today marker if anchor set and today is within horizon */}
        {contractAnchor && (() => {
          const today = new Date();
          const start = new Date(contractAnchor);
          const dayIdx = Math.floor((today - start) / (1000 * 60 * 60 * 24));
          if (dayIdx < 0 || dayIdx > horizonDays) return null;
          return (
            <g>
              <line
                x1={xForDay(dayIdx)} x2={xForDay(dayIdx)}
                y1={TOP_PADDING - 6} y2={totalHeight - BOTTOM_PADDING}
                stroke="#d32f2f" strokeWidth="1.5"
              />
              <text
                x={xForDay(dayIdx) + 4} y={TOP_PADDING - 10}
                fontSize="10" fill="#d32f2f" fontWeight="600"
              >Today</text>
            </g>
          );
        })()}

        {/* Rows */}
        {events.map((ev, i) => {
          const y = TOP_PADDING + i * (ROW_HEIGHT + ROW_GAP);
          const offset = ev.offset_days ?? 0;
          const x0 = xForDay(0);
          const x1 = xForDay(Math.max(0, Math.min(offset, horizonDays)));
          const barColor = ev.is_draft_with_quote
            ? '#FBC02D'  // yellow for draft-with-quote
            : (ev.event_type === 'implementation_milestone' ? '#00AEE6' : '#7E57C2');
          const barTextColor = ev.is_draft_with_quote ? '#3a2a00' : '#fff';
          const labelTrunc = (ev.label || '').length > 38
            ? (ev.label || '').slice(0, 36) + '…'
            : (ev.label || '');
          const status = ev.status || 'upcoming';
          const isComplete = status === 'complete';
          return (
            <g key={ev.id} opacity={isComplete ? 0.5 : 1}>
              {/* Row label (left) */}
              <text
                x={LEFT_LABEL_WIDTH - 10}
                y={y + ROW_HEIGHT / 2 + 4}
                textAnchor="end"
                fontSize="12"
                fill={isComplete ? '#888' : '#222'}
              >
                {labelTrunc}
              </text>
              {/* Bar from Day 0 to offset */}
              <rect
                x={x0}
                y={y + 4}
                width={Math.max(2, x1 - x0)}
                height={ROW_HEIGHT - 8}
                rx={3}
                fill={barColor}
              />
              {/* End-cap tick at the deadline day */}
              <circle
                cx={x1}
                cy={y + ROW_HEIGHT / 2}
                r={4}
                fill={barColor}
                stroke="#fff"
                strokeWidth="1.5"
              />
              {/* Day count or computed date inside the bar */}
              <text
                x={x1 + 8}
                y={y + ROW_HEIGHT / 2 + 4}
                fontSize="11"
                fill="#444"
              >
                {contractAnchor && ev.event_date
                  ? new Date(ev.event_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
                  : `+${offset}d`}
                {ev.is_draft_with_quote && '  (draft w/ quote)'}
              </text>
            </g>
          );
        })}
      </svg>
    </Box>
  );
}


const Schedule = () => {
  // Active proposal — every fetch and the AI-extract picker are scoped to it.
  // Falls back to "all events / all docs" only when no proposal is selected.
  const { proposalId, proposal } = useProposal();

  const [events, setEvents] = useState([]);
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [dedupOpen, setDedupOpen] = useState(false);
  const [dedupResult, setDedupResult] = useState(null); // {dry_run, considered, would_remove, removed?}
  // Tabs: 0 = Solicitation (procurement-period events) | 1 = Implementation Plan (post-award)
  const [tab, setTab] = useState(0);
  // Contract anchor (proposals.contract_effective_date) — drives whether
  // the implementation Gantt shows real dates or relative "Start + Nd" labels.
  const [contractAnchor, setContractAnchor] = useState(null);
  const [anchorDraft, setAnchorDraft] = useState('');

  // dialog state
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null); // null = creating
  const [form, setForm] = useState({
    event_type: 'other',
    label: '',
    event_date: '',
    end_date: '',
    is_mandatory: false,
    document_id: '',
    proposal_id: '',
    source_page: '',
    source_text: '',
    notes: '',
    confidence: 'high',
    assignee: '',
    status: 'upcoming',
  });

  const fetchAll = useCallback(async () => {
    try {
      // Use allSettled so the schedule still renders when /api/documents
      // fails (e.g. transient backend bug). The events list is the
      // primary content; the docs list only powers the AI-extract picker.
      // Both calls are SCOPED to the active proposal when one is selected
      // (so the page no longer shows legacy 2021 docs / events).
      const eventsUrl = proposalId
        ? `/api/schedule/events?proposal_id=${proposalId}`
        : '/api/schedule/events';
      const anchorUrl = proposalId
        ? `/api/schedule/proposal/${proposalId}/contract-anchor`
        : null;
      const [evRes, docRes, anchorRes] = await Promise.allSettled([
        axios.get(eventsUrl),
        axios.get('/api/documents'),
        anchorUrl ? axios.get(anchorUrl) : Promise.reject(new Error('no proposal')),
      ]);
      if (anchorRes.status === 'fulfilled') {
        const ced = anchorRes.value.data?.contract_effective_date || null;
        setContractAnchor(ced);
        setAnchorDraft(ced ? ced.slice(0, 10) : '');
      } else {
        setContractAnchor(null);
        setAnchorDraft('');
      }
      if (evRes.status === 'fulfilled') {
        setEvents(evRes.value.data.events || []);
      } else {
        const msg = evRes.reason?.response?.data?.detail
          || evRes.reason?.message
          || 'Failed to load schedule events';
        setError(msg);
      }
      if (docRes.status === 'fulfilled') {
        const all = docRes.value.data.documents || [];
        // Scope the docs list to the active proposal so the picker only
        // shows the 2026 RFP docs (not legacy 2021 amendments etc.).
        setDocs(proposalId ? all.filter(d => d.proposal_id === proposalId) : all);
      } else {
        // Non-fatal: leave docs empty so the AI-extract picker hides.
        setDocs([]);
        // eslint-disable-next-line no-console
        console.warn('Schedule: /api/documents unavailable —',
          docRes.reason?.response?.data?.detail || docRes.reason?.message);
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load schedule');
    } finally {
      setLoading(false);
    }
  }, [proposalId]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Candidates for AI extraction: RFP-like documents (already scoped to
  // the active proposal in fetchAll).
  const extractableDocs = useMemo(
    () => docs.filter(d => ['notice_of_solicitation', 'rfp', 'addendum'].includes(d.document_type)
      || d.source_type === 'rfp'),
    [docs]
  );

  // Only the documents that ACTUALLY contributed events — what the user
  // wanted: "show only the documents that were used to extract the dates".
  const sourceDocs = useMemo(() => {
    const ids = new Set(events.map(e => e.document_id).filter(Boolean));
    if (ids.size === 0) return [];
    return docs.filter(d => ids.has(d.id));
  }, [events, docs]);

  // Partition events into the two tabs. An event belongs to the
  // "Implementation Plan" tab when it's an implementation_milestone OR
  // it has any offset_anchor set. Everything else (procurement events
  // + your internal working-backwards rows) stays on the Solicitation tab.
  const isImplementation = (e) => (
    e.event_type === 'implementation_milestone' || !!e.offset_anchor
  );
  const solicitationEvents = useMemo(
    () => events.filter(e => !isImplementation(e)),
    [events]
  );
  const implementationEvents = useMemo(
    () => events.filter(isImplementation),
    [events]
  );

  // "Upcoming" tiles + sorted lists are scoped to the active tab so the
  // page never mixes pre-award and post-award milestones in one view.
  const tabEvents = tab === 0 ? solicitationEvents : implementationEvents;
  const upcoming = useMemo(
    () => tabEvents
      .filter(e => e.event_date && new Date(e.event_date) >= new Date())
      .sort((a, b) => new Date(a.event_date) - new Date(b.event_date))
      .slice(0, 6),
    [tabEvents]
  );

  const handleExtract = async (docId) => {
    setWorking(true); setError(''); setInfo('');
    try {
      const { data } = await axios.post(`/api/schedule/extract/${docId}`);
      setInfo(`Extracted ${data.saved} events from document ${docId}`);
      await fetchAll();
    } catch (e) {
      setError(e.response?.data?.detail || 'Extraction failed');
    } finally {
      setWorking(false);
    }
  };

  const openCreate = () => {
    setEditing(null);
    setForm({
      event_type: 'other', label: '', event_date: '', end_date: '',
      is_mandatory: false, document_id: '', proposal_id: '',
      source_page: '', source_text: '', notes: '',
      confidence: 'high', assignee: '', status: 'upcoming',
    });
    setDialogOpen(true);
  };

  const openEdit = (ev) => {
    setEditing(ev);
    setForm({
      event_type: ev.event_type || 'other',
      label: ev.label || '',
      event_date: ev.event_date ? ev.event_date.slice(0, 16) : '',
      end_date: ev.end_date ? ev.end_date.slice(0, 16) : '',
      is_mandatory: !!ev.is_mandatory,
      document_id: ev.document_id ?? '',
      proposal_id: ev.proposal_id ?? '',
      source_page: ev.source_page ?? '',
      source_text: ev.source_text || '',
      notes: ev.notes || '',
      confidence: ev.confidence || 'high',
      assignee: ev.assignee || '',
      status: ev.status || 'upcoming',
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    setWorking(true); setError(''); setInfo('');
    const payload = {
      event_type: form.event_type,
      label: form.label.trim() || 'Event',
      event_date: form.event_date || null,
      end_date: form.end_date || null,
      is_mandatory: !!form.is_mandatory,
      document_id: form.document_id ? Number(form.document_id) : null,
      proposal_id: form.proposal_id ? Number(form.proposal_id) : null,
      source_page: form.source_page ? Number(form.source_page) : null,
      source_text: form.source_text || null,
      notes: form.notes || null,
      confidence: form.confidence,
      assignee: form.assignee || null,
      status: form.status,
    };
    try {
      if (editing) {
        await axios.put(`/api/schedule/events/${editing.id}`, payload);
        setInfo('Event updated');
      } else {
        await axios.post('/api/schedule/events', payload);
        setInfo('Event added');
      }
      setDialogOpen(false);
      await fetchAll();
    } catch (e) {
      setError(e.response?.data?.detail || 'Save failed');
    } finally {
      setWorking(false);
    }
  };

  const handleDelete = async (ev) => {
    if (!window.confirm(`Delete "${ev.label}"?`)) return;
    setWorking(true); setError('');
    try {
      await axios.delete(`/api/schedule/events/${ev.id}`);
      await fetchAll();
    } catch (e) {
      setError(e.response?.data?.detail || 'Delete failed');
    } finally { setWorking(false); }
  };

  const handleVerify = async (ev) => {
    try {
      await axios.put(`/api/schedule/events/${ev.id}`, { verified: !ev.verified });
      await fetchAll();
    } catch (e) { setError(e.response?.data?.detail || 'Update failed'); }
  };

  // Quick status change — primarily for "Mark Complete" so users don't
  // have to open the Edit dialog. Also used for "Reopen" (back to upcoming).
  const handleStatusChange = async (ev, newStatus) => {
    try {
      await axios.put(`/api/schedule/events/${ev.id}`, { status: newStatus });
      await fetchAll();
      setInfo(`Event "${ev.label}" marked ${STATUS_LABEL[newStatus] || newStatus}.`);
    } catch (e) { setError(e.response?.data?.detail || 'Update failed'); }
  };

  const handleSaveAnchor = async () => {
    if (!proposalId) return;
    setWorking(true); setError(''); setInfo('');
    try {
      const body = {
        contract_effective_date: anchorDraft
          // datetime-local strips seconds; backend accepts the ISO date.
          ? new Date(anchorDraft + 'T00:00:00').toISOString()
          : null,
      };
      const { data } = await axios.put(
        `/api/schedule/proposal/${proposalId}/contract-anchor`,
        body,
      );
      if (data.cleared) {
        setInfo('Contract Effective Date cleared. Implementation deadlines reverted to relative offsets.');
      } else {
        setInfo(`Contract Effective Date saved. ${data.resolved} implementation deadline(s) computed.`);
      }
      await fetchAll();
    } catch (e) {
      setError(e.response?.data?.detail || 'Save failed');
    } finally {
      setWorking(false);
    }
  };

  const handleDedupe = async (apply) => {
    setWorking(true); setError(''); setInfo('');
    try {
      const params = new URLSearchParams();
      if (proposalId) params.set('proposal_id', String(proposalId));
      params.set('apply', apply ? 'true' : 'false');
      const { data } = await axios.post(`/api/schedule/dedupe?${params.toString()}`);
      if (apply) {
        setInfo(`Removed ${data.removed || 0} duplicate event(s).`);
        setDedupOpen(false);
        setDedupResult(null);
        await fetchAll();
      } else {
        setDedupResult(data);
        setDedupOpen(true);
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Dedupe failed');
    } finally {
      setWorking(false);
    }
  };

  const handleExportXlsx = async () => {
    setWorking(true); setError(''); setInfo('');
    try {
      const res = await axios.get('/api/schedule/export.xlsx', { responseType: 'blob' });
      // Filename from Content-Disposition if present, else fallback with timestamp.
      let filename = 'schedule.xlsx';
      const cd = res.headers['content-disposition'] || res.headers['Content-Disposition'];
      if (cd) {
        const m = /filename="?([^"]+)"?/.exec(cd);
        if (m) filename = m[1];
      }
      const blob = new Blob([res.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setInfo(`Exported ${filename}`);
    } catch (e) {
      setError(e.response?.data?.detail || 'Export failed');
    } finally {
      setWorking(false);
    }
  };

  return (
    <Fade in timeout={400}>
      <Box sx={{ p: 3, maxWidth: 1400, mx: 'auto' }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 3 }}>
          <Box>
            <Typography variant="h4" fontWeight={700}>Schedule of Key Events</Typography>
            <Typography variant="body2" color="text.secondary">
              AI-extracted dates from the solicitation + your internal working-backwards deadlines.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button startIcon={<RefreshIcon />} onClick={fetchAll} disabled={working}>Refresh</Button>
            <Button
              startIcon={<DeleteIcon />}
              color="warning"
              onClick={() => handleDedupe(false)}
              disabled={working || events.length === 0}
            >
              Dedupe
            </Button>
            <Button
              startIcon={<DownloadIcon />}
              onClick={handleExportXlsx}
              disabled={working || events.length === 0}
            >
              Export to Excel
            </Button>
            <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>Add Event</Button>
          </Stack>
        </Stack>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo('')}>{info}</Alert>}
        {(loading || working) && <LinearProgress sx={{ mb: 2 }} />}

        {/* Active proposal scope banner */}
        {proposalId ? (
          <Alert severity="info" variant="outlined" sx={{ mb: 2 }} icon={<DocIcon />}>
            Showing schedule for <strong>{proposal?.title || `proposal #${proposalId}`}</strong>.
            Events and the AI-extract picker below are scoped to this proposal only.
          </Alert>
        ) : (
          <Alert severity="warning" variant="outlined" sx={{ mb: 2 }}>
            No active proposal selected. Showing every event across the system.
          </Alert>
        )}

        {/* Source documents strip — only the docs that ACTUALLY produced events */}
        {sourceDocs.length > 0 && (
          <Card variant="outlined" sx={{ mb: 3 }}>
            <CardContent sx={{ pb: '12px !important' }}>
              <Typography variant="overline" color="text.secondary">
                Source documents ({sourceDocs.length})
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                These are the only documents that contributed events to the schedule below.
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {sourceDocs.map(d => {
                  const eventCount = events.filter(e => e.document_id === d.id).length;
                  return (
                    <Chip
                      key={d.id}
                      icon={<DocIcon />}
                      label={`#${d.id} · ${d.filename} · ${eventCount} event${eventCount === 1 ? '' : 's'}`}
                      variant="outlined"
                      size="small"
                    />
                  );
                })}
              </Stack>
            </CardContent>
          </Card>
        )}

        {/* Tabs: Solicitation Schedule | Implementation Plan */}
        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
        >
          <Tab
            label={`Solicitation Schedule (${solicitationEvents.length})`}
            id="schedule-tab-0"
          />
          <Tab
            label={`Implementation Plan (${implementationEvents.length})`}
            id="schedule-tab-1"
          />
        </Tabs>

        {tab === 1 && (
          <ImplementationTab
            events={implementationEvents}
            contractAnchor={contractAnchor}
            anchorDraft={anchorDraft}
            setAnchorDraft={setAnchorDraft}
            onSaveAnchor={handleSaveAnchor}
            onClearAnchor={() => { setAnchorDraft(''); }}
            working={working}
            proposalId={proposalId}
            onEdit={openEdit}
            onDelete={handleDelete}
            onStatusChange={handleStatusChange}
          />
        )}

        {tab === 0 && (
        <>
        {/* AI extraction panel */}
        <Card variant="outlined" sx={{ mb: 3 }}>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <ExtractIcon color="primary" />
              <Typography variant="h6">AI Schedule Extraction</Typography>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Select an ingested RFP, Notice of Solicitation, or Addendum to have Claude
              extract every date-bearing event. Existing AI-extracted events for that
              document will be replaced.
            </Typography>
            {extractableDocs.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No RFP-type documents found. Upload one from the <strong>RFP Setup</strong> page
                and mark its document type as "Notice of Solicitation" or "RFP".
              </Typography>
            ) : (
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {extractableDocs.map(d => (
                  <Button
                    key={d.id}
                    size="small"
                    variant="outlined"
                    startIcon={<ExtractIcon />}
                    onClick={() => handleExtract(d.id)}
                    disabled={working}
                    sx={{ mb: 1 }}
                  >
                    #{d.id} · {d.filename}
                    {d.document_type && (
                      <Chip size="small" label={d.document_type} sx={{ ml: 1, height: 18 }} />
                    )}
                  </Button>
                ))}
              </Stack>
            )}
          </CardContent>
        </Card>

        {/* Upcoming tiles */}
        <Typography variant="h6" sx={{ mb: 1 }}>Upcoming</Typography>
        {upcoming.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            No upcoming dated events.
          </Typography>
        ) : (
          <Grid container spacing={2} sx={{ mb: 4 }}>
            {upcoming.map(ev => {
              const days = daysFromNow(ev.event_date);
              const isSoon = days !== null && days <= 7;
              return (
                <Grid item xs={12} sm={6} md={4} key={ev.id}>
                  <Card variant="outlined" sx={{
                    borderLeft: '4px solid',
                    borderLeftColor: isSoon ? 'error.main' : ev.is_mandatory ? 'warning.main' : 'primary.main',
                  }}>
                    <CardContent>
                      <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
                        <Box sx={{ minWidth: 0, flex: 1 }}>
                          <Typography variant="overline" color="text.secondary">
                            {TYPE_LABEL[ev.event_type] || ev.event_type}
                          </Typography>
                          <Typography variant="subtitle1" fontWeight={600} noWrap>
                            {ev.label}
                          </Typography>
                          <Typography variant="body2" color="text.secondary">
                            {formatDate(ev.event_date)}
                          </Typography>
                          <Stack direction="row" spacing={0.5} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
                            <Chip
                              size="small"
                              icon={<ClockIcon />}
                              color={isSoon ? 'error' : 'default'}
                              label={days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : `in ${days} days`}
                            />
                            {ev.is_mandatory && <Chip size="small" color="warning" icon={<FlagIcon />} label="Mandatory" />}
                            {ev.confidence && (
                              <Chip size="small" variant="outlined"
                                color={CONFIDENCE_COLOR[ev.confidence] || 'default'}
                                label={ev.confidence} />
                            )}
                          </Stack>
                        </Box>
                        <Stack direction="row" spacing={0.25}>
                          <Tooltip title="Mark Complete">
                            <IconButton size="small" color="success"
                              onClick={() => handleStatusChange(ev, 'complete')}>
                              <DoneIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                          <Tooltip title="Edit">
                            <IconButton size="small" onClick={() => openEdit(ev)}>
                              <EditIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Stack>
                      </Stack>
                    </CardContent>
                  </Card>
                </Grid>
              );
            })}
          </Grid>
        )}

        {/* All events table */}
        <Typography variant="h6" sx={{ mb: 1 }}>All Events</Typography>
        <Card variant="outlined">
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: 36 }} />
                  <TableCell>Type</TableCell>
                  <TableCell>Label</TableCell>
                  <TableCell>Date</TableCell>
                  <TableCell>End</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Confidence</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {[...solicitationEvents]
                  .sort((a, b) => {
                    const da = a.event_date ? new Date(a.event_date).getTime() : Number.POSITIVE_INFINITY;
                    const db = b.event_date ? new Date(b.event_date).getTime() : Number.POSITIVE_INFINITY;
                    return da - db;
                  })
                  .map(ev => (
                    <EventRow
                      key={ev.id}
                      ev={ev}
                      onEdit={openEdit}
                      onDelete={handleDelete}
                      onVerify={handleVerify}
                      onStatusChange={handleStatusChange}
                    />
                  ))}
                {solicitationEvents.length === 0 && !loading && (
                  <TableRow>
                    <TableCell colSpan={9} align="center">
                      <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
                        No solicitation events yet. Run an AI extraction above or add one manually.
                      </Typography>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </Card>
        </>
        )}

        {/* Add/Edit dialog */}
        <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
          <DialogTitle>{editing ? 'Edit Event' : 'Add Schedule Event'}</DialogTitle>
          <DialogContent>
            <Grid container spacing={2} sx={{ mt: 0.5 }}>
              <Grid item xs={12} sm={6}>
                <FormControl fullWidth size="small">
                  <InputLabel>Event Type</InputLabel>
                  <Select label="Event Type" value={form.event_type}
                    onChange={(e) => setForm(f => ({ ...f, event_type: e.target.value }))}>
                    {EVENT_TYPES.map(t => (
                      <MenuItem key={t} value={t}>{TYPE_LABEL[t] || t}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={6}>
                <FormControl fullWidth size="small">
                  <InputLabel>Confidence</InputLabel>
                  <Select label="Confidence" value={form.confidence}
                    onChange={(e) => setForm(f => ({ ...f, confidence: e.target.value }))}>
                    <MenuItem value="high">High</MenuItem>
                    <MenuItem value="medium">Medium</MenuItem>
                    <MenuItem value="low">Low</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12}>
                <TextField fullWidth size="small" label="Label / Description"
                  value={form.label}
                  onChange={(e) => setForm(f => ({ ...f, label: e.target.value }))} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField fullWidth size="small" type="datetime-local" label="Date / Time"
                  InputLabelProps={{ shrink: true }}
                  value={form.event_date}
                  onChange={(e) => setForm(f => ({ ...f, event_date: e.target.value }))} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField fullWidth size="small" type="datetime-local" label="End Date (optional)"
                  InputLabelProps={{ shrink: true }}
                  value={form.end_date}
                  onChange={(e) => setForm(f => ({ ...f, end_date: e.target.value }))} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <FormControl fullWidth size="small">
                  <InputLabel>Mandatory?</InputLabel>
                  <Select label="Mandatory?" value={form.is_mandatory ? 'yes' : 'no'}
                    onChange={(e) => setForm(f => ({ ...f, is_mandatory: e.target.value === 'yes' }))}>
                    <MenuItem value="no">No</MenuItem>
                    <MenuItem value="yes">Yes</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={6}>
                <FormControl fullWidth size="small">
                  <InputLabel>Status</InputLabel>
                  <Select label="Status" value={form.status}
                    onChange={(e) => setForm(f => ({ ...f, status: e.target.value }))}>
                    <MenuItem value="upcoming">Upcoming</MenuItem>
                    <MenuItem value="in_progress">In Progress</MenuItem>
                    <MenuItem value="complete">Complete</MenuItem>
                    <MenuItem value="cancelled">Cancelled</MenuItem>
                    <MenuItem value="missed">Missed</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField fullWidth size="small" label="Assignee"
                  value={form.assignee}
                  onChange={(e) => setForm(f => ({ ...f, assignee: e.target.value }))} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField fullWidth size="small" label="Document ID"
                  value={form.document_id}
                  onChange={(e) => setForm(f => ({ ...f, document_id: e.target.value }))} />
              </Grid>
              <Grid item xs={12}>
                <TextField fullWidth size="small" multiline minRows={2} label="Notes"
                  value={form.notes}
                  onChange={(e) => setForm(f => ({ ...f, notes: e.target.value }))} />
              </Grid>
            </Grid>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button variant="contained" onClick={handleSave} disabled={working}>
              {editing ? 'Save Changes' : 'Add Event'}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Dedupe confirmation dialog (dry-run results, then Apply) */}
        <Dialog open={dedupOpen} onClose={() => setDedupOpen(false)} maxWidth="xs" fullWidth>
          <DialogTitle>Deduplicate schedule events?</DialogTitle>
          <DialogContent>
            {dedupResult && (
              <>
                <Typography variant="body2" sx={{ mb: 1 }}>
                  Found <strong>{dedupResult.groups_collapsed}</strong> duplicate group(s)
                  across <strong>{dedupResult.considered}</strong> events.
                </Typography>
                <Typography variant="body2" sx={{ mb: 2 }}>
                  Applying will remove <strong>{dedupResult.would_remove}</strong> event(s).
                  The keeper of each group is the verified / manual / highest-confidence /
                  most-detailed row. Events you've marked Complete or Cancelled are never touched.
                </Typography>
                {dedupResult.would_remove === 0 && (
                  <Alert severity="success" variant="outlined">
                    Nothing to do — no duplicates detected.
                  </Alert>
                )}
              </>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDedupOpen(false)}>Cancel</Button>
            <Button
              variant="contained"
              color="warning"
              onClick={() => handleDedupe(true)}
              disabled={working || !dedupResult || dedupResult.would_remove === 0}
            >
              Remove duplicates
            </Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Fade>
  );
};

export default Schedule;
