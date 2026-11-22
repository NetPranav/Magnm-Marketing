import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@magnm/design/magnm.css';
import { applyStoredTheme } from '@magnm/design/react';
import './mobile.css';
import { App } from './App';

applyStoredTheme();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
