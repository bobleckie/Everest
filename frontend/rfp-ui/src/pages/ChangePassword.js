import React, { useState } from 'react';
import axios from 'axios';
import {
  Box,
  Card,
  CardContent,
  TextField,
  Button,
  Typography,
  Alert,
  CircularProgress,
} from '@mui/material';
import { useAuth } from '../auth/AuthContext';

/**
 * Forced password-change page. Shown after login when the user's
 * `must_change_password` flag is set (e.g. the seeded default admin).
 * On success, refreshes the user profile so the flag clears and the
 * main app becomes accessible.
 */
const ChangePassword = () => {
  const { user, logout, refreshUser } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);

  const validate = () => {
    if (newPassword.length < 8) return 'New password must be at least 8 characters.';
    if (newPassword !== confirmPassword) return 'New password and confirmation do not match.';
    if (newPassword === currentPassword) return 'New password must be different from the current password.';
    return '';
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    const msg = validate();
    if (msg) { setError(msg); return; }
    setLoading(true);
    try {
      await axios.post('/api/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setSuccess('Password updated. Redirecting…');
      await refreshUser();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to change password.');
    } finally {
      setLoading(false);
    }
  };

  const displayName = user?.first_name
    ? `${user.first_name} ${user.last_name || ''}`.trim()
    : user?.username || '';

  return (
    <Box sx={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #081931 0%, #0E4774 100%)',
      p: 2,
    }}>
      <Card sx={{ width: '100%', maxWidth: 460, borderRadius: 3, boxShadow: '0 30px 80px rgba(0,0,0,0.35)' }}>
        <CardContent sx={{ p: 4 }}>
          <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
            Change Your Password
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Signed in as <strong>{displayName}</strong>. You must change your
            default password before continuing.
          </Typography>

          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          {success && <Alert severity="success" sx={{ mb: 2 }}>{success}</Alert>}

          <Box component="form" onSubmit={handleSubmit} noValidate>
            <TextField
              fullWidth
              type="password"
              label="Current Password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              margin="normal"
              autoComplete="current-password"
              autoFocus
            />
            <TextField
              fullWidth
              type="password"
              label="New Password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              margin="normal"
              autoComplete="new-password"
              helperText="At least 8 characters."
            />
            <TextField
              fullWidth
              type="password"
              label="Confirm New Password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              margin="normal"
              autoComplete="new-password"
            />
            <Button
              type="submit"
              variant="contained"
              fullWidth
              size="large"
              disabled={loading || !currentPassword || !newPassword || !confirmPassword}
              sx={{ mt: 2, py: 1.25, fontWeight: 700 }}
            >
              {loading ? <CircularProgress size={22} sx={{ color: 'white' }} /> : 'Update Password'}
            </Button>
            <Button
              fullWidth
              onClick={logout}
              sx={{ mt: 1 }}
              color="inherit"
            >
              Sign out
            </Button>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
};

export default ChangePassword;
