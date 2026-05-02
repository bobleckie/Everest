/**
 * ProposalContext
 *
 * Central source of truth for "which proposal is the user currently
 * working on". Replaces the hard-coded `DEFAULT_PROPOSAL_ID = 1` pattern
 * scattered through the legacy pages.
 *
 * Resolution order for the active proposal id:
 *   1. URL segment `/p/:proposalId/...` (set when the user clicks into
 *      a proposal from the Portfolio dashboard).
 *   2. `?proposal=<id>` query param (legacy support — RfpSetup uses this).
 *   3. localStorage["lastProposalId"] (so a refresh stays in scope).
 *   4. The most recently updated proposal returned by GET /api/proposals/.
 *   5. null — meaning "no scope yet, push the user to the Portfolio".
 *
 * Pages call `useProposal()` to read the active id and the proposal row.
 * If they need to reactively switch, they use `setActiveProposal(id)`.
 */
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { useLocation, useParams } from 'react-router-dom';
import axios from 'axios';

const ProposalCtx = createContext({
  proposalId: null,
  proposal: null,
  proposals: [],
  loading: false,
  refresh: () => {},
  setActiveProposal: () => {},
});

const LS_KEY = 'lastProposalId';

export function ProposalProvider({ children }) {
  const [proposals, setProposals] = useState([]);
  const [loading, setLoading] = useState(false);
  const [explicitId, setExplicitId] = useState(null); // user-overridden via setActiveProposal
  const location = useLocation();

  // Pull /p/:proposalId from the URL — react-router-dom v6 gives us params
  // via useParams() but only inside the matched Route element. Since this
  // provider sits ABOVE Routes we have to parse manually.
  const urlProposalId = useMemo(() => {
    const m = location.pathname.match(/^\/p\/(\d+)(?:\/|$)/);
    return m ? parseInt(m[1], 10) : null;
  }, [location.pathname]);

  // Legacy ?proposal=<id> query param (RfpSetup uses it).
  const queryProposalId = useMemo(() => {
    const sp = new URLSearchParams(location.search);
    const v = sp.get('proposal');
    const n = v ? parseInt(v, 10) : NaN;
    return Number.isFinite(n) ? n : null;
  }, [location.search]);

  const fetchProposals = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get('/api/proposals/');
      const list = Array.isArray(data) ? data : (data?.proposals || []);
      // Sort by updated_at desc so [0] is "most recent" — used as default scope.
      list.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
      setProposals(list);
    } catch (e) {
      // Silent — auth failures or backend hiccups shouldn't crash the shell.
      // The portfolio page surfaces the error if relevant.
      console.debug('ProposalContext: proposal list fetch failed', e?.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchProposals(); }, [fetchProposals]);

  // Decide the active id. Priority chain — first non-null wins.
  const proposalId = useMemo(() => {
    if (urlProposalId) return urlProposalId;
    if (queryProposalId) return queryProposalId;
    if (explicitId) return explicitId;
    const stored = parseInt(window.localStorage.getItem(LS_KEY) || '', 10);
    if (Number.isFinite(stored) && proposals.some(p => p.id === stored)) {
      return stored;
    }
    return proposals[0]?.id || null;
  }, [urlProposalId, queryProposalId, explicitId, proposals]);

  // Persist the active id to localStorage whenever it changes — so refreshes
  // and full-page reloads stay scoped.
  useEffect(() => {
    if (proposalId) window.localStorage.setItem(LS_KEY, String(proposalId));
  }, [proposalId]);

  const proposal = useMemo(
    () => proposals.find(p => p.id === proposalId) || null,
    [proposals, proposalId]
  );

  const setActiveProposal = useCallback((id) => {
    setExplicitId(Number.isFinite(parseInt(id, 10)) ? parseInt(id, 10) : null);
  }, []);

  const value = useMemo(() => ({
    proposalId, proposal, proposals, loading,
    refresh: fetchProposals, setActiveProposal,
  }), [proposalId, proposal, proposals, loading, fetchProposals, setActiveProposal]);

  return <ProposalCtx.Provider value={value}>{children}</ProposalCtx.Provider>;
}

/**
 * Hook for any page that needs the active proposal. Returns:
 *   { proposalId, proposal, proposals, loading, refresh, setActiveProposal }
 */
export function useProposal() {
  return useContext(ProposalCtx);
}

/** Helper: resolve a useParams()-style id when a Route declares :proposalId. */
export function useRouteProposalId() {
  const params = useParams();
  const v = params.proposalId;
  const n = v ? parseInt(v, 10) : NaN;
  return Number.isFinite(n) ? n : null;
}
