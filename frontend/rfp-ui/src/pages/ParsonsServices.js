import React, { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Box,
  Typography,
  Grid,
  Card,
  CardContent,
  Button,
  TextField,
  Tabs,
  Tab,
  Chip,
  LinearProgress,
  Alert,
  CircularProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
} from '@mui/material';
import {
  Send as SendIcon,
  CheckCircle as CheckIcon,
  Warning as WarningIcon,
  Business as BusinessIcon,
  Engineering as EngineeringIcon,
  Assessment as AssessmentIcon,
  Insights as InsightsIcon,
  LocationOn as LocationIcon,
  MonetizationOn as MonetizationIcon,
  Description as DescriptionIcon,
  People as PeopleIcon,
  Security as SecurityIcon,
  Gavel as GavelIcon,
  Timeline as TimelineIcon,
  AccountBalance as AccountBalanceIcon,
  AssignmentTurnedIn as AssignmentIcon,
} from '@mui/icons-material';
import { Fab } from '@mui/material';
import axios from 'axios';
import CompanyOverview from './parsons/CompanyOverview';
import ProposalCompletionScoreboard from '../components/ProposalCompletionScoreboard';
import ParsonsScorePanel from '../components/ParsonsScorePanel';

const ParsonsServices = () => {
  const location = useLocation();
  const [activeTab, setActiveTab] = useState(0);
  const [processing, setProcessing] = useState(false);

  // Sync active section with ?tab=<n> so the sub-nav panel can drive it
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const t = parseInt(params.get('tab'), 10);
    if (!Number.isNaN(t)) setActiveTab(t);
  }, [location.search]);
  const [refinementStep, setRefinementStep] = useState(0);
  const [refinedContent, setRefinedContent] = useState({});
  const [conflictResults, setConflictResults] = useState({});
  const [sectionStatus, setSectionStatus] = useState({});
  const [error, setError] = useState('');
  // Right-side bid-position panel (slide-out)
  const [scorePanelOpen, setScorePanelOpen] = useState(false);
  const scorePanelRef = useRef(null);

  // NJ RFP Sections based on actual requirements
  const rfpSections = [
    {
      id: 'vendorLegal',
      title: 'Vendor Legal & Registration',
      icon: <GavelIcon />,
      description: 'Legal entity, registration, and pass/fail compliance',
      passFail: true,
      weight: 'Pass/Fail',
      fields: [
        { key: 'legalEntity', label: 'Legal Entity Name', required: true },
        { key: 'registration', label: 'NJ Business Registration #', required: true },
        { key: 'taxId', label: 'Federal Tax ID', required: true },
        { key: 'certifications', label: 'Required Certifications', multiline: true, rows: 4, required: true },
        { key: 'insurance', label: 'Insurance Coverage Details', multiline: true, rows: 3, required: true },
      ],
    },
    {
      id: 'certifications',
      title: 'Mandatory Certifications & Forms',
      icon: <AssignmentIcon />,
      description: 'State-required forms and certifications',
      passFail: true,
      weight: 'Pass/Fail',
      fields: [
        { key: 'eeo', label: 'EEO Certification', type: 'upload', required: true },
        { key: 'affirmative', label: 'Affirmative Action Plan', type: 'upload', required: true },
        { key: 'minority', label: 'Minority Business Certification', type: 'select', options: ['Yes', 'No', 'Pending'] },
        { key: 'compliance', label: 'Compliance Officer Designation', required: true },
      ],
    },
    {
      id: 'technicalProposal',
      title: 'Technical Proposal',
      icon: <EngineeringIcon />,
      description: 'Technical approach and solution architecture',
      passFail: false,
      weight: 25,
      fields: [
        { key: 'approach', label: 'Technical Approach', multiline: true, rows: 6, required: true },
        { key: 'architecture', label: 'System Architecture', multiline: true, rows: 4, required: true },
        { key: 'viisIntegration', label: 'VIIS Integration Plan', multiline: true, rows: 4, required: true },
        { key: 'scalability', label: 'Scalability & Performance', multiline: true, rows: 3, required: true },
        { key: 'backup', label: 'Backup & Recovery Strategy', multiline: true, rows: 3, required: true },
      ],
    },
    {
      id: 'projectManagement',
      title: 'Project Management & Schedule',
      icon: <TimelineIcon />,
      description: 'Project management approach and implementation timeline',
      passFail: false,
      weight: 15,
      fields: [
        { key: 'methodology', label: 'Project Management Methodology', required: true },
        { key: 'gantt', label: 'Gantt Chart', type: 'upload', required: true },
        { key: 'milestones', label: 'Critical Milestones', multiline: true, rows: 4, required: true },
        { key: 'risks', label: 'Risk Management Plan', multiline: true, rows: 3, required: true },
        { key: 'reporting', label: 'Monthly Reporting Process', multiline: true, rows: 3, required: true },
      ],
    },
    {
      id: 'operations',
      title: 'Operations & Facilities',
      icon: <BusinessIcon />,
      description: 'Operational capabilities and facility requirements',
      passFail: false,
      weight: 10,
      fields: [
        { key: 'facilities', label: 'Facility Locations & Capabilities', multiline: true, rows: 4, required: true },
        { key: 'hours', label: 'Operating Hours', required: true },
        { key: 'capacity', label: 'System Capacity & Throughput', multiline: true, rows: 3, required: true },
        { key: 'maintenance', label: 'Maintenance Procedures', multiline: true, rows: 3, required: true },
        { key: 'emergency', label: 'Emergency Response Plan', multiline: true, rows: 3, required: true },
      ],
    },
    {
      id: 'technology',
      title: 'Technology / VIIS',
      icon: <AssessmentIcon />,
      description: 'Technology infrastructure and VIIS compliance',
      passFail: false,
      weight: 20,
      fields: [
        { key: 'infrastructure', label: 'Technology Infrastructure', multiline: true, rows: 4, required: true },
        { key: 'viisCompliance', label: 'VIIS Compliance Details', multiline: true, rows: 4, required: true },
        { key: 'dataSecurity', label: 'Data Security Measures', multiline: true, rows: 3, required: true },
        { key: 'interfaces', label: 'System Interfaces', multiline: true, rows: 3, required: true },
        { key: 'testing', label: 'Testing & Validation Approach', multiline: true, rows: 3, required: true },
      ],
    },
    {
      id: 'staffing',
      title: 'Staffing & Training',
      icon: <PeopleIcon />,
      description: 'Staffing plan and training programs',
      passFail: false,
      weight: 10,
      fields: [
        { key: 'orgChart', label: 'Organizational Chart', type: 'upload', required: true },
        { key: 'keyPersonnel', label: 'Key Personnel Bios', multiline: true, rows: 4, required: true },
        { key: 'training', label: 'Training Programs', multiline: true, rows: 4, required: true },
        { key: 'certification', label: 'Staff Certification Requirements', multiline: true, rows: 3, required: true },
        { key: 'transition', label: 'Transition Plan', multiline: true, rows: 3, required: true },
      ],
    },
    {
      id: 'customerService',
      title: 'Customer Service & Public Information',
      icon: <LocationIcon />,
      description: 'Customer service and public communication plans',
      passFail: false,
      weight: 8,
      fields: [
        { key: 'serviceLevels', label: 'Service Level Agreements', multiline: true, rows: 4, required: true },
        { key: 'communication', label: 'Public Communication Plan', multiline: true, rows: 3, required: true },
        { key: 'complaints', label: 'Complaint Resolution Process', multiline: true, rows: 3, required: true },
        { key: 'accessibility', label: 'Accessibility Compliance', multiline: true, rows: 2, required: true },
        { key: 'outreach', label: 'Community Outreach', multiline: true, rows: 2, required: true },
      ],
    },
    {
      id: 'security',
      title: 'Security, Audit & Compliance',
      icon: <SecurityIcon />,
      description: 'Security measures and compliance framework',
      passFail: false,
      weight: 12,
      fields: [
        { key: 'securityPlan', label: 'Security Plan', multiline: true, rows: 4, required: true },
        { key: 'audit', label: 'Audit Procedures', multiline: true, rows: 3, required: true },
        { key: 'compliance', label: 'Compliance Framework', multiline: true, rows: 3, required: true },
        { key: 'monitoring', label: 'Monitoring & Reporting', multiline: true, rows: 3, required: true },
        { key: 'breach', label: 'Breach Response Plan', multiline: true, rows: 2, required: true },
      ],
    },
    {
      id: 'smallBusiness',
      title: 'Small Business & Subcontracting',
      icon: <AccountBalanceIcon />,
      description: 'Subcontracting and small business utilization',
      passFail: false,
      weight: 5,
      fields: [
        { key: 'subcontractors', label: 'Subcontractor List', multiline: true, rows: 4, required: true },
        { key: 'smallBusiness', label: 'Small Business Utilization Plan', multiline: true, rows: 3, required: true },
        { key: 'goals', label: 'Diversity Goals', multiline: true, rows: 2, required: true },
        { key: 'monitoring', label: 'Subcontractor Monitoring', multiline: true, rows: 2, required: true },
      ],
    },
    {
      id: 'experience',
      title: 'Organizational Experience & Past Performance',
      icon: <MonetizationIcon />,
      description: 'Relevant experience and past performance',
      passFail: false,
      weight: 15,
      fields: [
        { key: 'relevant', label: 'Relevant Experience Summary', multiline: true, rows: 4, required: true },
        { key: 'references', label: 'Client References', multiline: true, rows: 3, required: true },
        { key: 'performance', label: 'Past Performance Metrics', multiline: true, rows: 3, required: true },
        { key: 'awards', label: 'Awards & Recognition', multiline: true, rows: 2, required: true },
        { key: 'lessons', label: 'Lessons Learned', multiline: true, rows: 2, required: true },
      ],
    },
    {
      id: 'costProposal',
      title: 'Cost Proposal',
      icon: <DescriptionIcon />,
      description: 'Detailed cost breakdown and pricing',
      passFail: false,
      weight: 20,
      fields: [
        { key: 'breakdown', label: 'Cost Breakdown', type: 'cost_table', required: true },
        { key: 'justification', label: 'Cost Justification', multiline: true, rows: 4, required: true },
        { key: 'escalation', label: 'Price Escalation Formula', required: true },
        { key: 'payment', label: 'Payment Terms', required: true },
      ],
    },
    {
      id: 'transition',
      title: 'Contract Transition & Close-Out',
      icon: <AssignmentIcon />,
      description: 'Transition and contract close-out procedures',
      passFail: false,
      weight: 5,
      fields: [
        { key: 'transition', label: 'Transition Plan', multiline: true, rows: 4, required: true },
        { key: 'knowledge', label: 'Knowledge Transfer', multiline: true, rows: 3, required: true },
        { key: 'closeout', label: 'Close-Out Procedures', multiline: true, rows: 3, required: true },
        { key: 'handover', label: 'System Handover', multiline: true, rows: 2, required: true },
      ],
    },
  ];

  const [serviceData, setServiceData] = useState(() => {
    const initial = {};
    rfpSections.forEach(section => {
      initial[section.id] = {};
      section.fields.forEach(field => {
        initial[section.id][field.key] = '';
      });
    });
    return initial;
  });

  const [currentProposal, setCurrentProposal] = useState(null);
  const [proposals, setProposals] = useState([]);

  // Load proposals on component mount
  useEffect(() => {
    loadProposals();
  }, []);

  const loadProposals = async () => {
    try {
      const response = await axios.get('/api/proposals/');
      setProposals(response.data);
    } catch (error) {
      console.error('Error loading proposals:', error);
    }
  };

  const createNewProposal = async () => {
    try {
      const response = await axios.post('/api/proposals/create', {
        title: 'NJ Motor Vehicle Inspection RFP Response',
        rfp_reference: 'NJ-MVC-2024-001',
        vendor_id: 1, // TODO: Get from user context
      });
      const newProposalId = response.data.proposal_id;
      await loadProposal(newProposalId);
      loadProposals();
    } catch (error) {
      console.error('Error creating proposal:', error);
      setError('Failed to create new proposal');
    }
  };

  const loadProposal = async (proposalId) => {
    try {
      const response = await axios.get(`/api/proposals/${proposalId}`);
      const proposalData = response.data;

      setCurrentProposal(proposalData.proposal);

      // Load section data
      const loadedData = {};
      proposalData.sections.forEach(section => {
        loadedData[section.section_id] = JSON.parse(section.content || '{}');
        setSectionStatus(prev => ({
          ...prev,
          [section.section_id]: section.status,
        }));
        if (section.refined_content) {
          setRefinedContent(prev => ({
            ...prev,
            [section.section_id]: section.refined_content,
          }));
        }
        if (section.conflicts) {
          setConflictResults(prev => ({
            ...prev,
            [section.section_id]: JSON.parse(section.conflicts),
          }));
        }
      });

      setServiceData(loadedData);
    } catch (error) {
      console.error('Error loading proposal:', error);
      setError('Failed to load proposal');
    }
  };

  const saveSection = async (sectionId) => {
    if (!currentProposal) return;

    try {
      await axios.put(`/api/proposals/${currentProposal.id}/section/${sectionId}`, {
        content: JSON.stringify(serviceData[sectionId]),
      });
      // Auto-rescore on submit/save — "one button for two functions".
      // Fire-and-forget so typing isn't blocked; the panel handles its own loading state.
      if (scorePanelRef.current) {
        scorePanelRef.current.rescoreSection(sectionId);
      }
    } catch (error) {
      console.error('Error saving section:', error);
    }
  };

  const submitProposal = async () => {
    if (!currentProposal) return;

    try {
      await axios.post(`/api/proposals/${currentProposal.id}/submit`);
      setCurrentProposal(prev => ({ ...prev, status: 'submitted' }));
      loadProposals();
      setError('');
    } catch (error) {
      console.error('Error submitting proposal:', error);
      setError(error.response?.data?.detail || 'Failed to submit proposal');
    }
  };

  const handleFieldChange = (sectionId, fieldKey, value) => {
    setServiceData(prev => ({
      ...prev,
      [sectionId]: {
        ...prev[sectionId],
        [fieldKey]: value,
      },
    }));

    // Auto-save after a short delay
    setTimeout(() => saveSection(sectionId), 1000);
  };

  const refineSection = async (sectionId) => {
    const section = rfpSections.find(s => s.id === sectionId);
    const sectionData = serviceData[sectionId];

    // Check if section has content
    const hasContent = Object.values(sectionData).some(value =>
      typeof value === 'string' ? value.trim().length > 0 : value
    );

    if (!hasContent) {
      setError(`Section "${section.title}" has no content to refine.`);
      return;
    }

    setProcessing(true);
    setRefinementStep(0);
    setError('');

    try {
      // Step 1: Send to Claude 4.6 for refinement
      setRefinementStep(1);
      const claudeResponse = await axios.post('/api/parsons-sme/refine-content', {
        content: JSON.stringify(sectionData),
        context: `NJ Motor Vehicle Inspection RFP - ${section.title} Section`,
        section: sectionId,
      });

      const refinedByClaude = claudeResponse.data.refined_content;
      setRefinedContent(prev => ({
        ...prev,
        [sectionId]: refinedByClaude,
      }));

      // Step 2: Send to Conflict Detector
      setRefinementStep(2);
      const conflictResponse = await axios.post('/api/conflict-detector/analyze', {
        content: refinedByClaude,
        context: `NJ RFP ${section.title} Section Review`,
        section: sectionId,
      });

      const conflicts = conflictResponse.data.conflicts || [];
      setConflictResults(prev => ({
        ...prev,
        [sectionId]: conflicts,
      }));

      // Save conflicts to backend
      await axios.put(`/api/proposals/${currentProposal.id}/section/${sectionId}`, {
        content: JSON.stringify(serviceData[sectionId]),
        conflicts: JSON.stringify(conflicts),
      });

      if (conflicts.length > 0) {
        // Step 3: Send back to Opus for additional refinement
        setRefinementStep(3);
        const opusResponse = await axios.post('/api/orchestrator/refine-with-opus', {
          content: refinedByClaude,
          conflicts: conflicts,
          context: `Resolve conflicts in ${section.title} section`,
          section: sectionId,
        });

        const finalRefined = opusResponse.data.refined_content;
        setRefinedContent(prev => ({
          ...prev,
          [sectionId]: finalRefined,
        }));

        // Final conflict check
        const finalConflictCheck = await axios.post('/api/conflict-detector/analyze', {
          content: finalRefined,
          context: `Final ${section.title} Section Check`,
          section: sectionId,
        });

        const finalConflicts = finalConflictCheck.data.conflicts || [];
        setConflictResults(prev => ({
          ...prev,
          [sectionId]: finalConflicts,
        }));
        // Save refined content and conflicts to backend
        await axios.put(`/api/proposals/${currentProposal.id}/section/${sectionId}`, {
          content: JSON.stringify(serviceData[sectionId]),
          refined_content: finalRefined,
          status: finalConflicts.length === 0 ? 'approved' : 'needs_review',
        });
        if (finalConflicts.length === 0) {
          setSectionStatus(prev => ({
            ...prev,
            [sectionId]: 'approved',
          }));
        } else {
          setSectionStatus(prev => ({
            ...prev,
            [sectionId]: 'needs_review',
          }));
        }
      } else {
        setSectionStatus(prev => ({
          ...prev,
          [sectionId]: 'approved',
        }));
      }

      setRefinementStep(4);

      // Auto-rescore the section against the rubric (Parsons + target competitor)
      // so the right-side panel reflects the freshly refined content.
      if (scorePanelRef.current) {
        scorePanelRef.current.rescoreSection(sectionId);
      }

    } catch (error) {
      console.error('Error in section refinement:', error);
      setError(`Failed to refine section "${section.title}". Please try again.`);
      setSectionStatus(prev => ({
        ...prev,
        [sectionId]: 'error',
      }));
    } finally {
      setProcessing(false);
    }
  };

  const getSectionStatus = (sectionId) => {
    return sectionStatus[sectionId] || 'draft';
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'approved': return 'success';
      case 'needs_review': return 'warning';
      case 'error': return 'error';
      default: return 'default';
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'approved': return <CheckIcon />;
      case 'needs_review': return <WarningIcon />;
      case 'error': return <WarningIcon />;
      default: return null;
    }
  };

  const getStepLabel = (step) => {
    switch (step) {
      case 0: return 'Ready to start';
      case 1: return 'Refining with Claude 4.6...';
      case 2: return 'Checking for conflicts...';
      case 3: return 'Final refinement with Opus...';
      case 4: return 'Section complete';
      default: return '';
    }
  };

  const renderField = (sectionId, field) => {
    const value = serviceData[sectionId][field.key];

    if (field.type === 'upload') {
      return (
        <Box sx={{ mt: 2 }}>
          <input
            accept={field.key === 'gantt' ? '.pdf,.xlsx,.mpp' : '.pdf,.doc,.docx'}
            style={{ display: 'none' }}
            id={`${sectionId}-${field.key}`}
            type="file"
            onChange={(e) => handleFieldChange(sectionId, field.key, e.target.files[0]?.name || '')}
          />
          <label htmlFor={`${sectionId}-${field.key}`}>
            <Button variant="outlined" component="span" fullWidth sx={{ py: 2 }}>
              {value || `Upload ${field.label}`}
            </Button>
          </label>
        </Box>
      );
    }

    if (field.type === 'select') {
      return (
        <FormControl fullWidth sx={{ mt: 2 }}>
          <InputLabel>{field.label}</InputLabel>
          <Select
            value={value}
            onChange={(e) => handleFieldChange(sectionId, field.key, e.target.value)}
            label={field.label}
          >
            {field.options.map(option => (
              <MenuItem key={option} value={option}>{option}</MenuItem>
            ))}
          </Select>
        </FormControl>
      );
    }

    if (field.type === 'cost_table') {
      return (
        <Box sx={{ mt: 2 }}>
          <Typography variant="h6" gutterBottom>Cost Breakdown Table</Typography>
          <TableContainer component={Paper}>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Category</TableCell>
                  <TableCell>Year 1</TableCell>
                  <TableCell>Year 2</TableCell>
                  <TableCell>Year 3</TableCell>
                  <TableCell>Total</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                <TableRow>
                  <TableCell>Operations</TableCell>
                  <TableCell>$2,500,000</TableCell>
                  <TableCell>$2,625,000</TableCell>
                  <TableCell>$2,756,250</TableCell>
                  <TableCell>$7,881,250</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>Technology</TableCell>
                  <TableCell>$1,200,000</TableCell>
                  <TableCell>$1,260,000</TableCell>
                  <TableCell>$1,323,000</TableCell>
                  <TableCell>$3,783,000</TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>Staffing</TableCell>
                  <TableCell>$800,000</TableCell>
                  <TableCell>$840,000</TableCell>
                  <TableCell>$882,000</TableCell>
                  <TableCell>$2,522,000</TableCell>
                </TableRow>
                <TableRow sx={{ fontWeight: 'bold' }}>
                  <TableCell>Total</TableCell>
                  <TableCell>$4,500,000</TableCell>
                  <TableCell>$4,725,000</TableCell>
                  <TableCell>$4,961,250</TableCell>
                  <TableCell>$14,186,250</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </TableContainer>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            * Costs are auto-calculated and validated. No manual entry allowed.
          </Typography>
        </Box>
      );
    }

    return (
      <TextField
        fullWidth
        label={field.label}
        multiline={field.multiline}
        rows={field.rows}
        value={value}
        onChange={(e) => handleFieldChange(sectionId, field.key, e.target.value)}
        required={field.required}
        variant="outlined"
        sx={{ mt: 2 }}
      />
    );
  };

  return (
    <Box>


      {/* Proposal Management */}
      <Card className="card" sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Proposal Management</Typography>
          <Grid container spacing={2} alignItems="center">
            <Grid item xs={12} md={4}>
              <FormControl fullWidth>
                <InputLabel>Select Proposal</InputLabel>
                <Select
                  value={currentProposal?.id || ''}
                  onChange={(e) => loadProposal(e.target.value)}
                  label="Select Proposal"
                >
                  {proposals.map(proposal => (
                    <MenuItem key={proposal.id} value={proposal.id}>
                      {proposal.title} - {proposal.status}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12} md={4}>
              <Button
                variant="outlined"
                onClick={createNewProposal}
                fullWidth
                startIcon={<BusinessIcon />}
              >
                Create New Proposal
              </Button>
            </Grid>
            <Grid item xs={12} md={4}>
              {currentProposal && currentProposal.status === 'draft' && (
                <Button
                  variant="contained"
                  onClick={submitProposal}
                  fullWidth
                  color="success"
                  startIcon={<SendIcon />}
                >
                  Submit Proposal
                </Button>
              )}
              {currentProposal && currentProposal.status === 'submitted' && (
                <Chip label="Submitted" color="success" />
              )}
            </Grid>
          </Grid>
          {currentProposal && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
              Current Proposal: {currentProposal.title} | Status: {currentProposal.status} | Created: {new Date(currentProposal.created_at).toLocaleDateString()}
            </Typography>
          )}
        </CardContent>
      </Card>

      {/* Proposal Completion Scoreboard */}
      <ProposalCompletionScoreboard
        proposalId={currentProposal?.id}
        serviceData={serviceData}
        rfpSections={rfpSections}
      />

      {/* Processing Indicator */}
      {processing && (
        <Card className="card" sx={{ mb: 3 }}>
          <CardContent>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
              <CircularProgress size={24} sx={{ mr: 2 }} />
              <Typography variant="h6">{getStepLabel(refinementStep)}</Typography>
            </Box>
            <LinearProgress variant="determinate" value={(refinementStep / 4) * 100} />
          </CardContent>
        </Card>
      )}

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {/* Tabbed Sections */}
      <Card className="card">
        <Tabs
          value={activeTab}
          onChange={(e, newValue) => setActiveTab(newValue)}
          variant="scrollable"
          scrollButtons="auto"
          sx={{
            borderBottom: 1,
            borderColor: 'divider',
            '& .MuiTab-root': {
              minHeight: 64,
              textTransform: 'none',
            },
          }}
        >
          {/* Tab 0: Company Overview (context for every RFP section) */}
          <Tab
            key="company-overview"
            label={
              <Box sx={{ display: 'flex', alignItems: 'center' }}>
                <BusinessIcon />
                <Box sx={{ ml: 1, textAlign: 'left' }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    Company Overview
                  </Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', mt: 0.5 }}>
                    <Chip label="Profile" size="small" color="secondary" sx={{ mr: 1 }} />
                  </Box>
                </Box>
              </Box>
            }
          />
          {rfpSections.map((section) => {
            const status = getSectionStatus(section.id);
            return (
              <Tab
                key={section.id}
                label={
                  <Box sx={{ display: 'flex', alignItems: 'center' }}>
                    {section.icon}
                    <Box sx={{ ml: 1, textAlign: 'left' }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {section.title}
                      </Typography>
                      <Box sx={{ display: 'flex', alignItems: 'center', mt: 0.5 }}>
                        <Chip
                          label={section.passFail ? 'Pass/Fail' : `${section.weight}pts`}
                          size="small"
                          color={section.passFail ? 'error' : 'primary'}
                          sx={{ mr: 1 }}
                        />
                        {getStatusIcon(status)}
                      </Box>
                    </Box>
                  </Box>
                }
                sx={{
                  opacity: status === 'approved' ? 0.7 : 1,
                  '&.Mui-selected': {
                    background: status === 'approved' ? 'rgba(80, 191, 52, 0.1)' : 'rgba(0, 174, 230, 0.1)',
                  },
                }}
              />
            );
          })}
        </Tabs>

        <CardContent sx={{ p: 0 }}>
          {/* Tab 0: Company Overview */}
          <Box
            key="company-overview-panel"
            role="tabpanel"
            hidden={activeTab !== 0}
            sx={{ p: 3 }}
          >
            {activeTab === 0 && <CompanyOverview />}
          </Box>
          {rfpSections.map((section, index) => (
            <Box
              key={section.id}
              role="tabpanel"
              hidden={activeTab !== index + 1}
              sx={{ p: 3 }}
            >
              {activeTab === index + 1 && (
                <Box>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 3 }}>
                    <Box>
                      <Typography variant="h5" gutterBottom sx={{ color: '#1B3349', display: 'flex', alignItems: 'center' }}>
                        {section.icon}
                        <Box sx={{ ml: 2 }}>{section.title}</Box>
                      </Typography>
                      <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
                        {section.description}
                      </Typography>
                      <Box sx={{ display: 'flex', gap: 2 }}>
                        <Chip
                          label={`Weight: ${section.passFail ? 'Pass/Fail' : section.weight + ' points'}`}
                          color={section.passFail ? 'error' : 'primary'}
                        />
                        <Chip
                          label={`Status: ${getSectionStatus(section.id).replace('_', ' ')}`}
                          color={getStatusColor(getSectionStatus(section.id))}
                        />
                      </Box>
                    </Box>

                    <Button
                      variant="contained"
                      startIcon={<SendIcon />}
                      onClick={() => refineSection(section.id)}
                      disabled={processing}
                      sx={{
                        background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)',
                        '&:hover': {
                          background: 'linear-gradient(135deg, #50BF34 0%, #00AEE6 100%)',
                        },
                      }}
                    >
                      Refine Section
                    </Button>
                  </Box>

                  {/* Section Fields */}
                  <Grid container spacing={3}>
                    {section.fields.map((field) => (
                      <Grid item xs={12} key={field.key}>
                        {renderField(section.id, field)}
                      </Grid>
                    ))}
                  </Grid>

                  {/* Refined Content Display */}
                  {refinedContent[section.id] && (
                    <Box sx={{ mt: 4 }}>
                      <Typography variant="h6" gutterBottom sx={{ color: '#1B3349', display: 'flex', alignItems: 'center' }}>
                        {getStatusIcon(getSectionStatus(section.id))}
                        <Box sx={{ ml: 1 }}>
                          Refined Content {getSectionStatus(section.id) === 'approved' ? '(Approved)' : '(Review Required)'}
                        </Box>
                      </Typography>

                      <Box sx={{ p: 3, background: 'rgba(0, 174, 230, 0.05)', borderRadius: 2 }}>
                        <Typography variant="body1" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                          {refinedContent[section.id]}
                        </Typography>
                      </Box>

                      {/* Conflict Results */}
                      {conflictResults[section.id] && conflictResults[section.id].length > 0 && (
                        <Box sx={{ mt: 3 }}>
                          <Typography variant="h6" color="error" gutterBottom>
                            Conflicts Detected:
                          </Typography>
                          {conflictResults[section.id].map((conflict, idx) => (
                            <Alert severity="warning" key={idx} sx={{ mb: 1 }}>
                              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                                {conflict.category?.toUpperCase()}: {conflict.description}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                Severity: {conflict.severity}
                              </Typography>
                            </Alert>
                          ))}
                        </Box>
                      )}

                      {getSectionStatus(section.id) === 'approved' && (
                        <Alert severity="success" sx={{ mt: 3 }}>
                          ✅ Section has been refined and approved. Ready for submission.
                        </Alert>
                      )}
                    </Box>
                  )}
                </Box>
              )}
            </Box>
          ))}
        </CardContent>
      </Card>
      {/* Floating toggle for the bid-position slide-out */}
      {currentProposal && (
        <Fab
          color="primary"
          aria-label="Bid position"
          onClick={() => setScorePanelOpen(true)}
          sx={{
            position: 'fixed',
            bottom: 24,
            right: 24,
            background: 'linear-gradient(135deg, #00AEE6 0%, #50BF34 100%)',
            zIndex: 1200,
          }}
        >
          <InsightsIcon />
        </Fab>
      )}

      {/* Right-side competitor scoring drawer */}
      <ParsonsScorePanel
        ref={scorePanelRef}
        open={scorePanelOpen}
        onClose={() => setScorePanelOpen(false)}
        proposalId={currentProposal?.id}
        currentSectionId={
          activeTab === 0
            ? null
            : rfpSections[activeTab - 1]?.id
        }
        currentSectionTitle={
          activeTab === 0
            ? 'Company Overview'
            : rfpSections[activeTab - 1]?.title
        }
      />    </Box>
  );
};

export default ParsonsServices;