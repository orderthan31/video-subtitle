import React from 'react';
import {createRoot} from 'react-dom/client';
import {AuthGate} from './AuthGate';
import {VideoWorkspace} from './VideoWorkspace';
import './styles.css';
import './workspace.css';

createRoot(document.getElementById('root')!).render(<React.StrictMode><AuthGate><VideoWorkspace/></AuthGate></React.StrictMode>);
