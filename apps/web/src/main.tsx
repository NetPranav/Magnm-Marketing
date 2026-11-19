import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@magnm/design/magnm.css';
import { applyStoredTheme } from '@magnm/design/react';
import './studio.css';
import { App } from './App';

applyStoredTheme();

const rootEl = document.getElementById('root');
if (rootEl) {
  createRoot(rootEl).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
