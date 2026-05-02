import React from 'react';
import parsonsBrandWhite from '../assets/parsons-brand-white.png';
import parsonsQuestmark from '../assets/parsons-questmark.png';

/**
 * Parsons corporate logo — uses the OFFICIAL Parsons brand artwork.
 *
 *   variant = 'full'  → full brand lockup: colored P questmark + white
 *                       "PARSONS" wordmark. Designed for dark backgrounds.
 *                       Asset: parsons_stacked_brand_logo_color_white_heritage.png
 *
 *   variant = 'icon'  → just the colored P questmark symbol, no wordmark.
 *                       Transparent background.
 *                       Asset: parsons_questmark_color_logo_heritage_white.png
 *
 * Both PNGs have transparent backgrounds. No CSS filters, no cropping,
 * no composite overlays — the artwork is rendered exactly as delivered.
 */
const ParsonsLogo = ({ size = 40, variant = 'full', alt = 'Parsons' }) => {
  const src = variant === 'icon' ? parsonsQuestmark : parsonsBrandWhite;
  return (
    <img
      src={src}
      alt={alt}
      style={{
        height: size,
        width: 'auto',
        display: 'block',
      }}
    />
  );
};

export default ParsonsLogo;
