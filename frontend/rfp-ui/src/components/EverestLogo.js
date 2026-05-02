import React from 'react';
import logoSrc from '../assets/everest-logo-clean.png';

/**
 * Everest logo — rendered from the pre-processed transparent PNG.
 *
 * everest-logo-clean.png was generated from the source JPG with the gray
 * glow/background fully removed (alpha=0) and tightly cropped to the icon.
 * There is no surrounding halo, no gray, no background color to worry about.
 *
 * Props:
 *   size    - width & height in px (rendered as a square)
 *   variant - kept for API compatibility (unused)
 */
const EverestLogo = ({ size = 40, variant = 'icon' }) => {
  return (
    <img
      src={logoSrc}
      alt="Everest"
      style={{
        width: size,
        height: size,
        flexShrink: 0,
        display: 'block',
      }}
    />
  );
};

export default EverestLogo;
