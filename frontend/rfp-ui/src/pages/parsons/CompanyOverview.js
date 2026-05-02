import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Grid,
  TextField,
  Button,
  Alert,
  Fade,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Chip,
  Divider,
  List,
  ListItem,
  ListItemText,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import {
  Save as SaveIcon,
  Add as AddIcon,
  Delete as DeleteIcon,
  Business as BusinessIcon,
  LocationOn as LocationIcon,
  Phone as PhoneIcon,
  Email as EmailIcon,
  Web as WebIcon,
} from '@mui/icons-material';
import axios from 'axios';

const CompanyOverview = () => {
  const [loading, setLoading] = useState(false);
  const [notification, setNotification] = useState(null);
  const [data, setData] = useState({
    company_name: '',
    legal_name: '',
    business_type: '',
    year_established: '',
    headquarters_address: '',
    phone: '',
    email: '',
    website: '',
    description: '',
    mission_statement: '',
    vision_statement: '',
    core_values: [],
    industry_focus: [],
    service_areas: [],
    certifications: [],
    awards: [],
    employee_count: '',
    revenue_range: '',
    ownership_structure: '',
    subsidiaries: [],
    key_executives: []
  });

  const [newValue, setNewValue] = useState('');
  const [addDialog, setAddDialog] = useState({ open: false, field: '', title: '' });

  useEffect(() => {
    loadCompanyData();
  }, []);

  const loadCompanyData = async () => {
    try {
      setLoading(true);
      // Try to load existing data
      const response = await axios.get('/api/parsons/company-overview');
      if (response.data) {
        setData(response.data);
      }
    } catch (error) {
      // If no data exists, that's fine - we'll start with empty form
      console.log('No existing company data found');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      setLoading(true);
      await axios.post('/api/parsons/company-overview', data);
      setNotification({ type: 'success', message: 'Company overview saved successfully' });
    } catch (error) {
      console.error('Error saving company data:', error);
      setNotification({ type: 'error', message: 'Failed to save company overview' });
    } finally {
      setLoading(false);
    }
  };

  const handleInputChange = (field, value) => {
    setData(prev => ({
      ...prev,
      [field]: value
    }));
  };

  const handleAddItem = (field, value) => {
    if (!value.trim()) return;

    setData(prev => ({
      ...prev,
      [field]: [...(prev[field] || []), value.trim()]
    }));
    setNewValue('');
    setAddDialog({ open: false, field: '', title: '' });
  };

  const handleRemoveItem = (field, index) => {
    setData(prev => ({
      ...prev,
      [field]: prev[field].filter((_, i) => i !== index)
    }));
  };

  const openAddDialog = (field, title) => {
    setAddDialog({ open: true, field, title });
    setNewValue('');
  };

  const renderArrayField = (field, title, placeholder) => (
    <Box>
      <Typography variant="h6" gutterBottom>
        {title}
      </Typography>
      <Box display="flex" flexWrap="wrap" gap={1} mb={2}>
        {data[field]?.map((item, index) => (
          <Chip
            key={index}
            label={item}
            onDelete={() => handleRemoveItem(field, index)}
            color="primary"
            variant="outlined"
          />
        ))}
      </Box>
      <Button
        startIcon={<AddIcon />}
        onClick={() => openAddDialog(field, title)}
        variant="outlined"
        size="small"
      >
        Add {title.toLowerCase().slice(0, -1)}
      </Button>
    </Box>
  );

  return (
    <Box>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Typography variant="h4" component="h1">
          Company Overview
        </Typography>
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={handleSave}
          disabled={loading}
        >
          Save Changes
        </Button>
      </Box>

      {notification && (
        <Fade in={!!notification}>
          <Alert
            severity={notification.type}
            sx={{ mb: 2 }}
            onClose={() => setNotification(null)}
          >
            {notification.message}
          </Alert>
        </Fade>
      )}

      <Grid container spacing={3}>
        {/* Basic Information */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <BusinessIcon color="primary" />
                Basic Information
              </Typography>
              <Divider sx={{ mb: 2 }} />

              <Grid container spacing={2}>
                <Grid item xs={12} sm={6}>
                  <TextField
                    fullWidth
                    label="Company Name"
                    value={data.company_name}
                    onChange={(e) => handleInputChange('company_name', e.target.value)}
                    required
                  />
                </Grid>
                <Grid item xs={12} sm={6}>
                  <TextField
                    fullWidth
                    label="Legal Name"
                    value={data.legal_name}
                    onChange={(e) => handleInputChange('legal_name', e.target.value)}
                  />
                </Grid>
                <Grid item xs={12} sm={6}>
                  <FormControl fullWidth>
                    <InputLabel>Business Type</InputLabel>
                    <Select
                      value={data.business_type}
                      onChange={(e) => handleInputChange('business_type', e.target.value)}
                    >
                      <MenuItem value="corporation">Corporation</MenuItem>
                      <MenuItem value="llc">LLC</MenuItem>
                      <MenuItem value="partnership">Partnership</MenuItem>
                      <MenuItem value="sole_proprietorship">Sole Proprietorship</MenuItem>
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <TextField
                    fullWidth
                    label="Year Established"
                    type="number"
                    value={data.year_established}
                    onChange={(e) => handleInputChange('year_established', e.target.value)}
                  />
                </Grid>
                <Grid item xs={12} sm={6}>
                  <TextField
                    fullWidth
                    label="Employee Count"
                    value={data.employee_count}
                    onChange={(e) => handleInputChange('employee_count', e.target.value)}
                    placeholder="e.g., 500-1000"
                  />
                </Grid>
                <Grid item xs={12} sm={6}>
                  <FormControl fullWidth>
                    <InputLabel>Revenue Range</InputLabel>
                    <Select
                      value={data.revenue_range}
                      onChange={(e) => handleInputChange('revenue_range', e.target.value)}
                    >
                      <MenuItem value="under_1m">Under $1M</MenuItem>
                      <MenuItem value="1m_5m">$1M - $5M</MenuItem>
                      <MenuItem value="5m_10m">$5M - $10M</MenuItem>
                      <MenuItem value="10m_50m">$10M - $50M</MenuItem>
                      <MenuItem value="50m_100m">$50M - $100M</MenuItem>
                      <MenuItem value="over_100m">Over $100M</MenuItem>
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <FormControl fullWidth>
                    <InputLabel>Ownership Structure</InputLabel>
                    <Select
                      value={data.ownership_structure}
                      onChange={(e) => handleInputChange('ownership_structure', e.target.value)}
                    >
                      <MenuItem value="public">Public</MenuItem>
                      <MenuItem value="private">Private</MenuItem>
                      <MenuItem value="employee_owned">Employee Owned</MenuItem>
                      <MenuItem value="family_owned">Family Owned</MenuItem>
                    </Select>
                  </FormControl>
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Grid>

        {/* Contact Information */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <LocationIcon color="primary" />
                Contact Information
              </Typography>
              <Divider sx={{ mb: 2 }} />

              <Grid container spacing={2}>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label="Headquarters Address"
                    value={data.headquarters_address}
                    onChange={(e) => handleInputChange('headquarters_address', e.target.value)}
                    multiline
                    rows={3}
                  />
                </Grid>
                <Grid item xs={12} sm={4}>
                  <TextField
                    fullWidth
                    label="Phone"
                    value={data.phone}
                    onChange={(e) => handleInputChange('phone', e.target.value)}
                    InputProps={{
                      startAdornment: <PhoneIcon sx={{ mr: 1, color: 'action.active' }} />,
                    }}
                  />
                </Grid>
                <Grid item xs={12} sm={4}>
                  <TextField
                    fullWidth
                    label="Email"
                    type="email"
                    value={data.email}
                    onChange={(e) => handleInputChange('email', e.target.value)}
                    InputProps={{
                      startAdornment: <EmailIcon sx={{ mr: 1, color: 'action.active' }} />,
                    }}
                  />
                </Grid>
                <Grid item xs={12} sm={4}>
                  <TextField
                    fullWidth
                    label="Website"
                    value={data.website}
                    onChange={(e) => handleInputChange('website', e.target.value)}
                    InputProps={{
                      startAdornment: <WebIcon sx={{ mr: 1, color: 'action.active' }} />,
                    }}
                  />
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Grid>

        {/* Company Description */}
        <Grid item xs={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Company Description
              </Typography>
              <Divider sx={{ mb: 2 }} />

              <Grid container spacing={2}>
                <Grid item xs={12}>
                  <TextField
                    fullWidth
                    label="Company Description"
                    value={data.description}
                    onChange={(e) => handleInputChange('description', e.target.value)}
                    multiline
                    rows={4}
                    placeholder="Brief overview of your company and what you do..."
                  />
                </Grid>
                <Grid item xs={12} sm={6}>
                  <TextField
                    fullWidth
                    label="Mission Statement"
                    value={data.mission_statement}
                    onChange={(e) => handleInputChange('mission_statement', e.target.value)}
                    multiline
                    rows={3}
                  />
                </Grid>
                <Grid item xs={12} sm={6}>
                  <TextField
                    fullWidth
                    label="Vision Statement"
                    value={data.vision_statement}
                    onChange={(e) => handleInputChange('vision_statement', e.target.value)}
                    multiline
                    rows={3}
                  />
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Grid>

        {/* Arrays */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              {renderArrayField('core_values', 'Core Values', 'e.g., Integrity, Excellence, Innovation')}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              {renderArrayField('industry_focus', 'Industry Focus', 'e.g., Construction, Engineering, Technology')}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              {renderArrayField('service_areas', 'Service Areas', 'e.g., Project Management, Consulting, Design')}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              {renderArrayField('certifications', 'Certifications', 'e.g., ISO 9001, PMP, LEED')}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12}>
          <Card>
            <CardContent>
              {renderArrayField('awards', 'Awards & Recognition', 'e.g., Best Engineering Firm 2023')}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              {renderArrayField('subsidiaries', 'Subsidiaries', 'e.g., Parsons Engineering, Parsons Construction')}
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              {renderArrayField('key_executives', 'Key Executives', 'e.g., John Doe - CEO, Jane Smith - CTO')}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Add Item Dialog */}
      <Dialog open={addDialog.open} onClose={() => setAddDialog({ open: false, field: '', title: '' })}>
        <DialogTitle>Add {addDialog.title}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label={`New ${addDialog.title.slice(0, -1)}`}
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter') {
                handleAddItem(addDialog.field, newValue);
              }
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAddDialog({ open: false, field: '', title: '' })}>
            Cancel
          </Button>
          <Button onClick={() => handleAddItem(addDialog.field, newValue)} variant="contained">
            Add
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CompanyOverview;