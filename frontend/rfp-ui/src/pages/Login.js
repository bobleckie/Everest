import React, { useState } from 'react';
import { Box, Card, CardContent, TextField, Button, Typography, Alert, CircularProgress } from '@mui/material';
import { useAuth } from '../auth/AuthContext';
import EverestLogo from '../components/EverestLogo';
import ParsonsLogo from '../components/ParsonsLogo';

const Login = () => {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401 || status === 400) {
        setError('Invalid username or password.');
      } else {
        setError(err?.response?.data?.detail || err.message || 'Login failed.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box sx={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #081931 0%, #0E4774 100%)',
      p: 2,
    }}>
      <Card sx={{ width: '100%', maxWidth: 420, borderRadius: 0, boxShadow: '0 30px 80px rgba(0,0,0,0.35)' }}>
        <CardContent sx={{ p: 4 }}>
          {/* Parsons — company. Top of the login card. */}
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mb: 2 }}>
            <ParsonsLogo variant="full" size={40} />
          </Box>

          {/* Everest — product designation */}
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
              <EverestLogo size={28} />
              <Typography variant="h5" sx={{ fontWeight: 700, color: '#1B3349' }}>Everest</Typography>
            </Box>
            <Typography variant="body2" color="text.secondary">RFP Response Platform — sign in to continue</Typography>
          </Box>

          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

          <Box component="form" onSubmit={handleSubmit} noValidate>
            <TextField
              fullWidth
              label="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
              margin="normal"
              autoComplete="username"
            />
            <TextField
              fullWidth
              type="password"
              label="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              margin="normal"
              autoComplete="current-password"
            />
            <Button
              type="submit"
              variant="contained"
              fullWidth
              size="large"
              disabled={loading || !username || !password}
              sx={{ mt: 2, py: 1.25, fontWeight: 700, borderRadius: 0 }}
            >
              {loading ? <CircularProgress size={22} sx={{ color: 'white' }} /> : 'Sign In'}
            </Button>
          </Box>

          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 3, textAlign: 'center' }}>
            Contact your system administrator if you need access.
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
};

export default Login;
