import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { AuthProvider } from './auth/AuthContext';

// ───────────────────────────────────────────────────────────────────
// Global fix for the Windows "post-beep" the user hears when scrolling.
//
// Cause: a focused <input type="number"> turns wheel events into value
// changes. When the value is at min/max (or otherwise can't change),
// Chrome/Edge play the Windows system "Asterisk" sound for every wheel
// tick — and the bigger the page, the more wheel ticks land on a number
// input before the page scrolls.
//
// Fix: when a wheel event hits a focused number input, blur it so the
// page scroll is what actually happens. Listening on document with
// passive:false lets us swallow the value-change without breaking scroll.
// ───────────────────────────────────────────────────────────────────
if (typeof document !== 'undefined') {
  document.addEventListener('wheel', (e) => {
    const tgt = e.target;
    if (tgt && tgt.tagName === 'INPUT' && tgt.type === 'number'
        && document.activeElement === tgt) {
      tgt.blur();
    }
  }, { passive: true });
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>
);