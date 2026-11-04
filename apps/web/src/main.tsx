import '@studypilot/ui/theme.css';
import 'katex/dist/katex.min.css';

import { StudyPilotApp } from '@studypilot/app';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { webPlatform } from './platform';

const container = document.getElementById('root');
if (!container) throw new Error('Missing #root element');

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <StudyPilotApp platform={webPlatform} />
    </BrowserRouter>
  </StrictMode>,
);
