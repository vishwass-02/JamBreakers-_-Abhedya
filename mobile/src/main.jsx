import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import { start } from './store.js';
import './styles.css';

start();
createRoot(document.getElementById('root')).render(<App />);

// Offline app shell. Browsers only allow service workers on https:// or localhost; on a plain-http LAN
// address the app still works, it simply loads from the laptop each time.
if ('serviceWorker' in navigator && (window.isSecureContext || location.hostname === 'localhost')) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
}
