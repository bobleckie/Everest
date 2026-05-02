import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box, Typography, Link, Divider } from '@mui/material';

/**
 * Renders a markdown string as nicely styled HTML that matches the MUI theme.
 * Used for AI-generated content such as competitor dossier entries, writer
 * persona system prompts, and predicted proposal responses.
 */
const MarkdownView = ({ children, dense = false, sx = {} }) => {
  if (!children || typeof children !== 'string') return null;

  const size = dense ? 13 : 14;
  const lineHeight = dense ? 1.55 : 1.65;

  return (
    <Box
      sx={{
        color: 'text.primary',
        fontSize: size,
        lineHeight,
        '& > *:first-of-type': { mt: 0 },
        '& > *:last-child': { mb: 0 },
        '& p': { my: 1 },
        '& ul, & ol': { pl: 3, my: 1 },
        '& li': { mb: 0.5 },
        '& li > p': { my: 0.25 },
        '& code': {
          bgcolor: 'rgba(0,0,0,0.05)',
          px: 0.75,
          py: 0.15,
          borderRadius: 0.75,
          fontSize: size - 1,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        },
        '& pre': {
          bgcolor: '#0F172A',
          color: '#E2E8F0',
          p: 1.5,
          borderRadius: 1,
          overflowX: 'auto',
          fontSize: size - 1,
        },
        '& pre code': { bgcolor: 'transparent', color: 'inherit', p: 0 },
        '& blockquote': {
          borderLeft: '3px solid #00AEE6',
          pl: 2,
          ml: 0,
          my: 1,
          color: 'text.secondary',
          fontStyle: 'italic',
        },
        '& table': {
          borderCollapse: 'collapse',
          width: '100%',
          my: 1.5,
          fontSize: size - 1,
        },
        '& th, & td': {
          border: '1px solid',
          borderColor: 'divider',
          px: 1,
          py: 0.75,
          textAlign: 'left',
        },
        '& th': { bgcolor: '#F5F7FA', fontWeight: 700 },
        '& img': { maxWidth: '100%', borderRadius: 1 },
        '& hr': { border: 'none', borderTop: '1px solid', borderColor: 'divider', my: 2 },
        ...sx,
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ node, ...props }) => (
            <Typography variant="h5" fontWeight={800} sx={{ mt: 2, mb: 1 }} {...props} />
          ),
          h2: ({ node, ...props }) => (
            <Typography variant="h6" fontWeight={800} sx={{ mt: 2, mb: 1 }} {...props} />
          ),
          h3: ({ node, ...props }) => (
            <Typography variant="subtitle1" fontWeight={700} sx={{ mt: 1.5, mb: 0.75 }} {...props} />
          ),
          h4: ({ node, ...props }) => (
            <Typography variant="subtitle2" fontWeight={700} sx={{ mt: 1.25, mb: 0.5 }} {...props} />
          ),
          h5: ({ node, ...props }) => (
            <Typography variant="body1" fontWeight={700} sx={{ mt: 1, mb: 0.5 }} {...props} />
          ),
          h6: ({ node, ...props }) => (
            <Typography variant="body2" fontWeight={700} sx={{ mt: 1, mb: 0.5 }} {...props} />
          ),
          a: ({ node, ...props }) => (
            <Link target="_blank" rel="noopener noreferrer" underline="hover" {...props} />
          ),
          hr: () => <Divider sx={{ my: 2 }} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </Box>
  );
};

export default MarkdownView;
