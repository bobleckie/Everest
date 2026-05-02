import React, { useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, useNavigate } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { CssBaseline, Snackbar, Alert } from '@mui/material';
import AppShell from './components/AppShell';
import Dashboard from './pages/Dashboard';
import PortfolioDashboard from './pages/PortfolioDashboard';
import RfpWorkspaceDashboard from './pages/RfpWorkspaceDashboard';
import ParsonsKnowledge from './pages/ParsonsKnowledge';
import ParsonsResponseWriting from './pages/ParsonsResponseWriting';
import DiffQuestionAnalysis from './pages/DiffQuestionAnalysis';
import { ProposalProvider } from './proposal/ProposalContext';
import RfpSetup from './pages/RfpSetup';
import Schedule from './pages/Schedule';
import Flashcards from './pages/Flashcards';
import KnowledgeBase from './pages/KnowledgeBase';
import ComplianceMatrix from './pages/ComplianceMatrix';
import CompetitorIntel from './pages/CompetitorIntel';
import ParsonsServices from './pages/ParsonsServices';
import PricingModel from './pages/PricingModel';
import WaitTimeAbTest from './pages/WaitTimeAbTest';
import PricingCoverage from './pages/PricingCoverage';
import Review from './pages/Review';
import Questions from './pages/Questions';
import QuestionTriage from './pages/QuestionTriage';
import RfpDiff from './pages/RfpDiff';
import ResponseWorkbench from './pages/ResponseWorkbench';
import Settings from './pages/Settings';
// Legacy pages (still accessible via direct URL)
import Workflows from './pages/Workflows';
import Documents from './pages/Documents';
import Personas from './pages/Personas';
import Proposals from './pages/Proposals';
import Login from './pages/Login';
import ChangePassword from './pages/ChangePassword';
import { useAuth } from './auth/AuthContext';
import './App.css';

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: '#00AEE6',
      contrastText: '#ffffff',
    },
    secondary: {
      main: '#50BF34',
      contrastText: '#ffffff',
    },
    info: {
      main: '#9ED9FD',
    },
    background: {
      default: '#DFDFDF',
      paper: '#ffffff',
    },
    text: {
      primary: '#505050',
      secondary: '#959595',
    },
  },
  typography: {
    h4: {
      fontWeight: 600,
    },
    h5: {
      fontWeight: 600,
    },
    h6: {
      fontWeight: 600,
    },
  },
});

// Mounted once inside <Router>: on first render (i.e. just after a
// successful login pulled us back into AuthenticatedApp), look for a saved
// post-login redirect target and navigate there. One-shot — clears itself.
function PostLoginRedirector() {
  const navigate = useNavigate();
  const { consumePostLoginRedirect } = useAuth();
  useEffect(() => {
    const target = consumePostLoginRedirect && consumePostLoginRedirect();
    if (target && target !== window.location.pathname + window.location.search + window.location.hash) {
      navigate(target, { replace: true });
    }
    // Run only on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

function AuthenticatedApp() {
  return (
    <Router>
      <PostLoginRedirector />
      {/* ProposalProvider wraps everything so any page can call useProposal()
          and get the active proposal id. It reads the URL `/p/:id/...`
          segment first, falls back to ?proposal=, then localStorage, then
          most recently updated. Pages still keep their legacy /rfp-setup
          etc. URLs working. */}
      <ProposalProvider>
        <AppShell>
          <Routes>
            {/* New top-level Portfolio dashboard */}
            <Route path="/" element={<PortfolioDashboard />} />

            {/* Per-RFP workspace — same legacy pages mounted under /p/:id
                so links stay scoped. The pages themselves use ?proposal=<id>
                for their existing behavior. */}
            <Route path="/p/:proposalId/dashboard" element={<RfpWorkspaceDashboard />} />
            <Route path="/p/:proposalId/parsons-knowledge" element={<ParsonsKnowledge />} />
            <Route path="/p/:proposalId/parsons-response" element={<ParsonsResponseWriting />} />
            <Route path="/p/:proposalId/rfp-setup" element={<RfpSetup />} />
            <Route path="/p/:proposalId/schedule" element={<Schedule />} />
            <Route path="/p/:proposalId/flashcards" element={<Flashcards />} />
            <Route path="/p/:proposalId/knowledge-base" element={<KnowledgeBase />} />
            <Route path="/p/:proposalId/compliance-matrix" element={<ComplianceMatrix />} />
            <Route path="/p/:proposalId/parsons-services" element={<ParsonsServices />} />
            <Route path="/p/:proposalId/pricing" element={<PricingModel />} />
            <Route path="/p/:proposalId/pricing-coverage" element={<PricingCoverage />} />
            <Route path="/p/:proposalId/wait-time-ab" element={<WaitTimeAbTest />} />
            <Route path="/p/:proposalId/review" element={<Review />} />
            <Route path="/p/:proposalId/questions" element={<Questions />} />
            <Route path="/p/:proposalId/question-triage" element={<QuestionTriage />} />
            <Route path="/p/:proposalId/rfp-diff" element={<RfpDiff />} />
            <Route path="/p/:proposalId/diff-questions" element={<DiffQuestionAnalysis />} />
            <Route path="/p/:proposalId/workbench" element={<ResponseWorkbench />} />

            {/* Legacy global pages (preserved) */}
            <Route path="/legacy-dashboard" element={<Dashboard />} />
            <Route path="/parsons-knowledge" element={<ParsonsKnowledge />} />
            <Route path="/parsons-response" element={<ParsonsResponseWriting />} />
            <Route path="/rfp-setup" element={<RfpSetup />} />
            <Route path="/schedule" element={<Schedule />} />
            <Route path="/flashcards" element={<Flashcards />} />
            <Route path="/knowledge-base" element={<KnowledgeBase />} />
            <Route path="/compliance-matrix" element={<ComplianceMatrix />} />
            <Route path="/competitors" element={<CompetitorIntel />} />
            <Route path="/parsons-services" element={<ParsonsServices />} />
            <Route path="/pricing" element={<PricingModel />} />
            <Route path="/pricing-coverage" element={<PricingCoverage />} />
            <Route path="/wait-time-ab" element={<WaitTimeAbTest />} />
            <Route path="/review" element={<Review />} />
            <Route path="/questions" element={<Questions />} />
            <Route path="/question-triage" element={<QuestionTriage />} />
            <Route path="/rfp-diff" element={<RfpDiff />} />
            <Route path="/diff-questions" element={<DiffQuestionAnalysis />} />
            <Route path="/workbench" element={<ResponseWorkbench />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/proposals" element={<Proposals />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/personas" element={<Personas />} />
          </Routes>
        </AppShell>
      </ProposalProvider>
    </Router>
  );
}

function App() {
  const { isAuthenticated, mustChangePassword, sessionExpired, dismissSessionExpired } = useAuth();
  let content;
  if (!isAuthenticated) {
    content = <Login />;
  } else if (mustChangePassword) {
    content = <ChangePassword />;
  } else {
    content = <AuthenticatedApp />;
  }
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {content}
      <Snackbar
        open={!!sessionExpired}
        autoHideDuration={8000}
        onClose={dismissSessionExpired}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
      >
        <Alert severity="warning" variant="filled" onClose={dismissSessionExpired} sx={{ width: '100%' }}>
          Your session expired — please sign in again. Your last page will be restored.
        </Alert>
      </Snackbar>
    </ThemeProvider>
  );
}

export default App;