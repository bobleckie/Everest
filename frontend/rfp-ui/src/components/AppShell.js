import React, { useState, useMemo } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  Divider,
  Tooltip,
  IconButton,
} from '@mui/material';
import {
  Dashboard as DashboardIcon,
  Description as DocumentIcon,
  People as PeopleIcon,
  Settings as SettingsIcon,
  Business as BusinessIcon,
  ChevronLeft as ChevronLeftIcon,
  Gavel as GavelIcon,
  AssignmentTurnedIn as AssignmentIcon,
  Engineering as EngineeringIcon,
  LibraryBooks as LibraryBooksIcon,
  EditNote as ResponseWriterIcon,
  Timeline as TimelineIcon,
  Assessment as AssessmentIcon,
  LocationOn as LocationIcon,
  Security as SecurityIcon,
  AccountBalance as AccountBalanceIcon,
  MonetizationOn as MonetizationIcon,
  Logout as LogoutIcon,
  Upload as UploadIcon,
  Storage as KnowledgeIcon,
  CompareArrows as CompetitorIcon,
  Edit as AuthoringIcon,
  FactCheck as ReviewIcon,
  EventAvailable as ScheduleIcon,
  QuestionAnswer as QuestionIcon,
  Difference as DiffIcon,
  AccountTree as CoverageIcon,
  School as FlashcardsIcon,
  Science as AbTestIcon,
} from '@mui/icons-material';
import { useAuth } from '../auth/AuthContext';
import { useProposal } from '../proposal/ProposalContext';
import EverestLogo from './EverestLogo';
import ParsonsLogo from './ParsonsLogo';

const RAIL_WIDTH = 64;
const SUBNAV_WIDTH = 260;
const TOPBAR_HEIGHT = 68;

// ── Side-nav: 5 grouped top-level items ─────────────────────────────
// Each group becomes one icon on the rail. Clicking the icon opens a
// fly-out that lists the sub-pages in that group. This replaces the old
// 20-item flat rail.
//
// Groups (ordered to match the natural RFP-response workflow):
//   1. Portfolio     — single page, no sub-nav
//   2. Intake        — get the RFP into the system, frame the work
//   3. Analyze       — understand requirements, surface gaps and questions
//   4. Respond       — draft per-requirement, price, and benchmark
//   5. Review        — finalize, score, configure
//
// Notes:
//   * Proposal Authoring (/parsons-services) is intentionally NOT in the
//     nav — the route still works for direct links, but Response Writer
//     is the canonical per-requirement authoring surface.
//   * Response Workbench is feature-flagged; it appears in Respond only
//     when localStorage('responseWorkbench.enabled') === 'true'.
const navGroups = [
  {
    id: 'portfolio',
    label: 'Portfolio',
    subtitle: 'All Parsons RFPs at a glance',
    icon: <DashboardIcon />,
    path: '/',
  },
  {
    id: 'intake',
    label: 'Intake',
    subtitle: 'Get the RFP into the system and frame the work',
    icon: <UploadIcon />,
    items: [
      { id: 'rfp-setup',      path: '/rfp-setup',      label: 'RFP Setup',         icon: <UploadIcon /> },
      { id: 'schedule',       path: '/schedule',       label: 'Schedule',          icon: <ScheduleIcon /> },
      { id: 'flashcards',     path: '/flashcards',     label: 'Flashcards',        icon: <FlashcardsIcon /> },
      { id: 'rfp-diff',       path: '/rfp-diff',       label: 'Solicitation Diff', icon: <DiffIcon /> },
      { id: 'diff-questions', path: '/diff-questions', label: 'Diff → Questions',  icon: <QuestionIcon /> },
    ],
  },
  {
    id: 'analyze',
    label: 'Analyze',
    subtitle: 'Understand requirements, surface gaps and questions',
    icon: <AssignmentIcon />,
    items: [
      { id: 'requirement-browser', path: '/requirement-browser', label: 'Requirement Browser', icon: <AssignmentIcon /> },
      { id: 'review-queue',    path: '/review-queue',      label: 'Review Queue',      icon: <ReviewIcon /> },
      { id: 'compliance',      path: '/compliance-matrix', label: 'Compliance Matrix', icon: <AssignmentIcon /> },
      { id: 'knowledge',       path: '/knowledge-base',    label: 'Knowledge Base',    icon: <KnowledgeIcon /> },
      { id: 'questions',       path: '/questions',         label: 'RFP Questions',     icon: <QuestionIcon /> },
      { id: 'question-triage', path: '/question-triage',   label: 'Question Triage',   icon: <QuestionIcon /> },
    ],
  },
  {
    id: 'respond',
    label: 'Respond',
    subtitle: 'Draft per requirement, price the bid, benchmark the field',
    icon: <ResponseWriterIcon />,
    items: [
      { id: 'parsons-response',  path: '/parsons-response',  label: 'Response Writer',     icon: <ResponseWriterIcon /> },
      { id: 'section-narratives', path: '/section-narratives', label: 'Section Narratives', icon: <ResponseWriterIcon /> },
      { id: 'adversarial-scorecard', path: '/adversarial-scorecard', label: 'Adversarial Scorecard', icon: <CompetitorIcon /> },
      { id: 'solution-catalog', path: '/solution-catalog',  label: 'Solution Catalog',    icon: <LibraryBooksIcon /> },
      { id: 'gap-workspace',   path: '/gap-workspace',     label: 'Gap Workspace',       icon: <KnowledgeIcon /> },
      { id: 'parsons-knowledge', path: '/parsons-knowledge', label: 'Parsons Knowledge',   icon: <LibraryBooksIcon /> },
      { id: 'competitors',       path: '/competitors',       label: 'Competitive Intel',   icon: <CompetitorIcon /> },
      { id: 'pricing',           path: '/pricing',           label: 'Pricing Model',       icon: <MonetizationIcon /> },
      { id: 'pricing-coverage',  path: '/pricing-coverage',  label: 'Pricing Coverage',    icon: <CoverageIcon /> },
      { id: 'wait-time-ab',      path: '/wait-time-ab',      label: 'Wait Time A/B Test',  icon: <AbTestIcon /> },
      { id: 'workbench',         path: '/workbench',         label: 'Response Workbench',  icon: <AuthoringIcon />, featureFlag: 'responseWorkbench.enabled' },
    ],
  },
  {
    id: 'review',
    label: 'Review',
    subtitle: 'Finalize and score the bid',
    icon: <ReviewIcon />,
    path: '/review',
  },
];

// Bottom-pinned rail items (rendered after a divider, separate from the
// workflow groups). Settings sits here because it's a system / configuration
// surface, not part of the per-RFP workflow.
const bottomNav = [
  {
    id: 'settings',
    label: 'Settings',
    subtitle: 'AI providers, scoring rubric, and users',
    icon: <SettingsIcon />,
    path: '/settings',
  },
];

// Build subNavMap from navGroups so the existing fly-out renderer works.
// Filters feature-flag-disabled items at module load.
function _isFlagOn(flag) {
  if (!flag) return true;
  try { return window.localStorage.getItem(flag) === 'true'; } catch { return false; }
}
const subNavMap = navGroups
  .filter(g => Array.isArray(g.items))
  .reduce((acc, g) => {
    acc[g.id] = {
      title: g.label,
      items: g.items
        .filter(it => _isFlagOn(it.featureFlag))
        .map(it => ({ label: it.label, icon: it.icon, path: it.path, id: it.id })),
    };
    return acc;
  }, {});

// Flat list of all leaf items (used to derive header subtitle and to
// resolve which group the current URL belongs to). Includes the bottom
// pinned items so the breadcrumb / header subtitle works on Settings too.
const allLeaves = [
  ...navGroups.flatMap(g => Array.isArray(g.items) ? g.items : [g]),
  ...bottomNav,
];

// Map a pathname to its leaf item id (e.g. /compliance-matrix → 'compliance').
function resolveLeafId(pathname) {
  if (pathname === '/') return 'portfolio';
  if (pathname.startsWith('/rfp-setup')) return 'rfp-setup';
  if (pathname.startsWith('/schedule')) return 'schedule';
  if (pathname.startsWith('/flashcards')) return 'flashcards';
  if (pathname.startsWith('/knowledge')) return 'knowledge';
  if (pathname.startsWith('/compliance')) return 'compliance';
  if (pathname.startsWith('/question-triage')) return 'question-triage';
  if (pathname.startsWith('/diff-questions')) return 'diff-questions';
  if (pathname.startsWith('/questions')) return 'questions';
  if (pathname.startsWith('/rfp-diff')) return 'rfp-diff';
  if (pathname.startsWith('/competitors')) return 'competitors';
  if (pathname.startsWith('/parsons-knowledge')) return 'parsons-knowledge';
  if (pathname.startsWith('/parsons-response')) return 'parsons-response';
  // Hidden but still routable — Proposal Authoring page. Map to the
  // closest visible leaf so the rail still highlights something sane.
  if (pathname.startsWith('/parsons-services')) return 'parsons-response';
  if (pathname.startsWith('/parsons')) return 'parsons-response';
  if (pathname.startsWith('/wait-time-ab')) return 'wait-time-ab';
  if (pathname.startsWith('/pricing-coverage')) return 'pricing-coverage';
  if (pathname.startsWith('/pricing')) return 'pricing';
  if (pathname.startsWith('/workbench')) return 'workbench';
  if (pathname.startsWith('/review')) return 'review';
  if (pathname.startsWith('/settings')) return 'settings';
  // Legacy routes
  if (pathname.startsWith('/proposals')) return 'rfp-setup';
  if (pathname.startsWith('/documents')) return 'knowledge';
  if (pathname.startsWith('/workflows')) return 'review';
  if (pathname.startsWith('/personas')) return 'settings';
  return null;
}

// Given a leaf id, return the id of the group that contains it. Bottom-nav
// items are their own group (their leaf id == their group id).
function resolveGroupId(leafId) {
  if (!leafId) return null;
  if (bottomNav.some(b => b.id === leafId)) return leafId;
  for (const g of navGroups) {
    if (!Array.isArray(g.items)) {
      if (g.id === leafId) return g.id;
      continue;
    }
    if (g.items.some(it => it.id === leafId)) return g.id;
  }
  return null;
}

// IDs of nav items that operate on "the current proposal". When a proposal
// is active and the user clicks one of these, we rewrite the path to
// /p/<id>/... so the page naturally scopes itself instead of relying on the
// legacy `?proposal=` query param fallback.
const PROPOSAL_SCOPED_NAV_IDS = new Set([
  'rfp-setup', 'schedule', 'flashcards', 'knowledge', 'compliance', 'questions',
  'rfp-diff', 'parsons', 'workbench', 'pricing', 'pricing-coverage',
  'wait-time-ab', 'review',
  // Parsons Knowledge auto-shows the per-RFP tab when accessed inside a workspace
  'parsons-knowledge',
  // Response Writer is always per-proposal
  'parsons-response',
  // Diff → Questions analysis pipeline is per-proposal
  'diff-questions',
  // Question Triage curates the per-proposal candidate pool
  'question-triage',
]);

const AppShell = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { proposalId } = useProposal();

  // Rewrite a nav-item path so it lands inside the active proposal's
  // workspace when one of the proposal-scoped items is clicked. Examples:
  //   '/rfp-setup'           → '/p/3/rfp-setup'
  //   '/parsons-services?tab=2' → '/p/3/parsons-services?tab=2'
  // Only applies when proposalId is defined and the path doesn't already
  // start with /p/.
  const scopedPath = (item) => {
    if (!proposalId || !PROPOSAL_SCOPED_NAV_IDS.has(item.id)) return item.path;
    if (item.path.startsWith('/p/')) return item.path;
    return item.path.replace(/^\//, `/p/${proposalId}/`);
  };

  // Active leaf (which sub-page the user is on) and the group it belongs
  // to. Both fall back to null safely on unknown routes.
  const activeLeafId = resolveLeafId(location.pathname);
  const activeGroupId = resolveGroupId(activeLeafId);
  const activeLeaf = useMemo(
    () => allLeaves.find(l => l.id === activeLeafId) || null,
    [activeLeafId]
  );
  const activeGroup = useMemo(
    () => navGroups.find(g => g.id === activeGroupId) || null,
    [activeGroupId]
  );
  // Header subtitle: prefer leaf-specific label when we're on a sub-page,
  // otherwise the group's own subtitle.
  const activeNavItem = activeLeaf
    ? { label: activeLeaf.label, subtitle: activeGroup?.subtitle || '' }
    : activeGroup;
  // Visible groups: filter out groups whose feature flag is off (currently
  // none, but the structure is in place for the future).
  const visibleGroups = navGroups;
  // Pin the group whose leaf is active so the fly-out opens by default.
  const [pinnedSubId, setPinnedSubId] = useState(activeGroupId);

  // When the user clicks a group icon on the rail:
  //   - if the group has sub-items and is already pinned, collapse it.
  //   - if the group has sub-items but isn't pinned, pin it AND navigate
  //     to the first sub-item (so the user gets useful content immediately).
  //   - if the group is a single-page (Portfolio), just navigate.
  const handleRailClick = (group) => {
    const hasSub = Array.isArray(group.items) && group.items.length > 0;
    if (!hasSub) {
      setPinnedSubId(null);
      navigate(scopedPath(group));
      return;
    }
    if (pinnedSubId === group.id) {
      // Toggle closed
      setPinnedSubId(null);
      return;
    }
    setPinnedSubId(group.id);
    // Navigate to the first visible sub-item if we're not already inside
    // this group, so the user lands somewhere useful instead of seeing an
    // empty fly-out over the prior page.
    if (activeGroupId !== group.id) {
      const first = (subNavMap[group.id]?.items || [])[0];
      if (first) navigate(scopedPath(first));
    }
  };

  const subNav = pinnedSubId ? subNavMap[pinnedSubId] : null;

  // Highlight the active sub-nav item based on current URL (including ?tab=)
  const currentQueryTab = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return params.get('tab');
  }, [location.search]);

  const isSubItemActive = (path) => {
    const [rawP, q] = path.split('?');
    // Sub-item paths are stored as their canonical (non-scoped) form,
    // e.g. '/rfp-setup'. The current URL might be '/p/3/rfp-setup'. Match
    // both shapes so the active highlight follows the user into a workspace.
    const cur = location.pathname;
    const matchesPath =
      cur === rawP ||
      cur === `/p/${proposalId}${rawP}` ||
      (rawP !== '/' && cur.startsWith(rawP + '/')) ||
      (rawP !== '/' && cur.startsWith(`/p/${proposalId}${rawP}/`));
    if (!matchesPath) return false;
    if (!q) return !currentQueryTab;
    const params = new URLSearchParams(q);
    return params.get('tab') === currentQueryTab;
  };

  const userInitials = user
    ? ((user.first_name || user.username || '?')[0] + (user.last_name ? user.last_name[0] : '')).toUpperCase()
    : '';
  const userFullName = user
    ? `${user.first_name || user.username}${user.last_name ? ' ' + user.last_name : ''}`
    : '';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      {/* Top bar — squared off, full Parsons logo on the left,
          Everest product tag after (modeled on parsons.com header) */}
      <Box
        component="header"
        sx={{
          height: TOPBAR_HEIGHT,
          bgcolor: '#081931',
          borderBottom: '1px solid rgba(255,255,255,0.08),',
          display: 'flex',
          alignItems: 'center',
          px: 3,
          gap: 2.5,
          position: 'sticky',
          top: 0,
          zIndex: 1300,
          borderRadius: 0,
        }}
      >
        {/* Parsons corporate logo — top-left of every page */}
        <Box
          onClick={() => navigate('/')}
          sx={{
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            height: '100%',
            py: 1.25,
          }}
          aria-label="Parsons — home"
        >
          <ParsonsLogo variant="full" size={38} />
        </Box>

        {/* Vertical separator between company and product */}
        <Box sx={{ width: '1px', height: 36, bgcolor: 'rgba(255,255,255,0.2)' }} />

        {/* Everest — product designation (text is part of the logo image) */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <EverestLogo size={42} />
          <Typography sx={{ fontSize: '0.72rem', color: '#9ED9FD', lineHeight: 1.1 }}>
            RFP Response Platform
          </Typography>
        </Box>

        {/* Current page title — updates per route */}
        {activeNavItem && (
          <>
            <Box sx={{ width: '1px', height: 36, bgcolor: 'rgba(255,255,255,0.15)' }} />
            <Box>
              <Typography sx={{ fontSize: '1.05rem', fontWeight: 700, color: '#FFFFFF', lineHeight: 1.1 }}>
                {activeNavItem.label}
              </Typography>
              <Typography sx={{ fontSize: '0.72rem', color: '#9ED9FD', lineHeight: 1.1 }}>
                {activeNavItem.subtitle}
              </Typography>
            </Box>
          </>
        )}

        <Box sx={{ flexGrow: 1 }} />

        {/* Active-proposal scope indicator — only renders when the user is
            inside a /p/:id workspace. Click "Exit" to return to the portfolio. */}
        {location.pathname.startsWith('/p/') && proposalId && (
          <Tooltip title="Return to RFP Portfolio">
            <Button
              size="small"
              variant="text"
              onClick={() => navigate('/')}
              sx={{
                color: '#FFFFFF',
                bgcolor: 'rgba(255,255,255,0.1)',
                borderRadius: 2,
                px: 1.5,
                mr: 1,
                textTransform: 'none',
                fontWeight: 600,
                '&:hover': { bgcolor: 'rgba(255,255,255,0.18)' },
              }}
            >
              ← Exit to Portfolio
            </Button>
          </Tooltip>
        )}

        {/* User + logout on the right of the top bar — clean, no pill border */}
        {user && (
          <Tooltip title={userFullName} placement="bottom">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
              <Box
                sx={{
                  width: 34,
                  height: 34,
                  borderRadius: '50%',
                  bgcolor: '#00AEE6',
                  color: 'white',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 700,
                  fontSize: 13,
                }}
              >
                {userInitials}
              </Box>
              <Typography sx={{ fontSize: '0.9rem', color: '#FFFFFF', fontWeight: 600 }}>
                {userFullName}
              </Typography>
            </Box>
          </Tooltip>
        )}
        <Tooltip title="Log out" placement="bottom">
          <IconButton
            onClick={logout}
            sx={{
              color: 'rgba(255,255,255,0.7)',
              borderRadius: 0,
              ml: 0.5,
              '&:hover': { bgcolor: 'rgba(255,255,255,0.1)', color: '#FFFFFF' },
            }}
          >
            <LogoutIcon />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Body: icon rail + subnav + main content */}
      <Box sx={{ display: 'flex', flex: 1, minHeight: 0 }}>
      {/* Icon rail */}
      <Box
        component="nav"
        sx={{
          width: RAIL_WIDTH,
          flexShrink: 0,
          bgcolor: '#081931',
          color: 'white',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          py: 2,
          gap: 1,
          position: 'sticky',
          top: TOPBAR_HEIGHT,
          height: `calc(100vh - ${TOPBAR_HEIGHT}px)`,
          zIndex: 1200,
          borderRight: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        <Divider flexItem sx={{ bgcolor: 'rgba(255,255,255,0.12)', my: 1 }} />
        {visibleGroups.map((group) => {
          const active = activeGroupId === group.id;
          const pinned = pinnedSubId === group.id;
          const tip = group.label + (group.subtitle ? ` — ${group.subtitle}` : '');
          return (
            <Tooltip key={group.id} title={tip} placement="right">
              <IconButton
                onClick={() => handleRailClick(group)}
                sx={{
                  width: 44,
                  height: 44,
                  color: active || pinned ? '#00AEE6' : 'rgba(255,255,255,0.75)',
                  bgcolor: pinned ? 'rgba(0,174,230,0.15)' : active ? 'rgba(255,255,255,0.05)' : 'transparent',
                  borderLeft: active ? '3px solid #50BF34' : '3px solid transparent',
                  borderRadius: 0,
                  '&:hover': {
                    bgcolor: 'rgba(255,255,255,0.1)',
                    color: '#00AEE6',
                  },
                }}
              >
                {group.icon}
              </IconButton>
            </Tooltip>
          );
        })}

        {/* Push the bottom-pinned items to the bottom of the rail */}
        <Box sx={{ flexGrow: 1 }} />
        <Divider flexItem sx={{ bgcolor: 'rgba(255,255,255,0.12)', my: 1 }} />
        {bottomNav.map((item) => {
          const active = activeGroupId === item.id;
          const tip = item.label + (item.subtitle ? ` — ${item.subtitle}` : '');
          return (
            <Tooltip key={item.id} title={tip} placement="right">
              <IconButton
                onClick={() => { setPinnedSubId(null); navigate(scopedPath(item)); }}
                sx={{
                  width: 44,
                  height: 44,
                  color: active ? '#00AEE6' : 'rgba(255,255,255,0.75)',
                  bgcolor: active ? 'rgba(255,255,255,0.05)' : 'transparent',
                  borderLeft: active ? '3px solid #50BF34' : '3px solid transparent',
                  borderRadius: 0,
                  '&:hover': {
                    bgcolor: 'rgba(255,255,255,0.1)',
                    color: '#00AEE6',
                  },
                }}
              >
                {item.icon}
              </IconButton>
            </Tooltip>
          );
        })}

      </Box>

      {/* Expanding sub-nav panel */}
      <Box
        sx={{
          width: subNav ? SUBNAV_WIDTH : 0,
          flexShrink: 0,
          bgcolor: '#0E2747',
          color: 'white',
          overflow: 'hidden',
          transition: 'width 0.22s ease',
          position: 'sticky',
          top: TOPBAR_HEIGHT,
          height: `calc(100vh - ${TOPBAR_HEIGHT}px)`,
          zIndex: 1100,
          borderRight: subNav ? '1px solid rgba(255,255,255,0.08)' : 'none',
        }}
      >
        {subNav && (
          <Box sx={{ width: SUBNAV_WIDTH, display: 'flex', flexDirection: 'column', height: '100%' }}>
            <Box sx={{ px: 2, py: 2, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 700, color: '#9ED9FD' }}>
                {subNav.title}
              </Typography>
              <IconButton size="small" onClick={() => setPinnedSubId(null)} sx={{ color: 'rgba(255,255,255,0.7)' }}>
                <ChevronLeftIcon fontSize="small" />
              </IconButton>
            </Box>
            <Divider sx={{ bgcolor: 'rgba(255,255,255,0.1)' }} />
            <List sx={{ flex: 1, overflow: 'auto', py: 0 }}>
              {subNav.items.map((it) => {
                const active = isSubItemActive(it.path);
                // Re-scope sub-item to the active proposal so clicking
                // 'RFP Setup' from /p/3/... lands on /p/3/rfp-setup.
                const target = scopedPath(it);
                return (
                  <ListItemButton
                    key={it.path}
                    component={Link}
                    to={target}
                    sx={{
                      color: active ? '#fff' : 'rgba(255,255,255,0.8)',
                      bgcolor: active ? 'rgba(0,174,230,0.18)' : 'transparent',
                      borderLeft: active ? '3px solid #00AEE6' : '3px solid transparent',
                      borderRadius: 0,
                      '&:hover': {
                        bgcolor: 'rgba(255,255,255,0.06)',
                        color: '#fff',
                      },
                    }}
                  >
                    <ListItemIcon sx={{ color: 'inherit', minWidth: 36 }}>
                      {it.icon}
                    </ListItemIcon>
                    <ListItemText
                      primary={it.label}
                      primaryTypographyProps={{ fontSize: '0.9rem', fontWeight: active ? 600 : 500 }}
                    />
                  </ListItemButton>
                );
              })}
            </List>
          </Box>
        )}
      </Box>

      {/* Main content */}
      <Box component="main" sx={{ flexGrow: 1, p: 3, overflow: 'auto', minWidth: 0 }}>
        {children}
      </Box>
      </Box>
    </Box>
  );
};

export default AppShell;
