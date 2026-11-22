import React, { useEffect, useState } from 'react';
import { useStore, mode, logOut } from './store.js';
import Live from './tabs/Live.jsx';
import MapTab from './tabs/MapTab.jsx';
import Missions from './tabs/Missions.jsx';
import More from './tabs/More.jsx';
import Login from './Login.jsx';

const TABS = [
  { id: 'live', label: 'Home', icon: <path d="M3 12h4l3-8 4 16 3-8h4" /> },
  { id: 'map', label: 'Attack', icon: <><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z" /><path d="M9 4v14M15 6v14" /></> },
  { id: 'missions', label: 'Missions', icon: <><circle cx="12" cy="12" r="9" /><path d="m10 8 6 4-6 4z" /></> },
  { id: 'more', label: 'More', icon: <><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></> },
];

export default function App() {
  const S = useStore();
  const [tab, setTab] = useState(() => (location.hash.slice(1) || 'live'));
  useEffect(() => { history.replaceState(null, '', '#' + tab); window.scrollTo(0, 0); }, [tab]);
  const scn = mode() === 'scenario';
  if (S.auth === 'needed') return <Login />;
  if (S.auth === 'checking') return <div className="login"><p className="mute">Connecting to the laptop…</p></div>;
  const connLabel = { online: 'Live', connecting: 'Connecting…', reconnecting: 'Reconnecting…', offline: 'Offline' }[S.conn];
  return (
    <div className="app">
      <header className="top">
        <div className="logo"><img className="wordmark" src="./abhedya_logo_dark.png" alt="ABHEDYA" /></div>
        {scn && <span className="pill scn" onClick={() => setTab('missions')}>🎬 Mission</span>}
        {S.me?.role === 'observer' && <span className="pill viewonly" title="This account can watch but not control">👁 View only</span>}
        <span className={`pill conn ${S.conn}`}><i />{connLabel}</span>
        {S.me && !S.me.local && (
          <button type="button" className="pill signout" onClick={() => { if (window.confirm('Sign out of ABHEDYA on this phone?')) logOut(); }}
                  title={`Signed in as ${S.me.user}`} aria-label="Sign out">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" />
            </svg>
            Sign out
          </button>
        )}
      </header>
      {S.conn !== 'online' && (
        <div className="offline-bar">
          {S.conn === 'offline' || S.retries > 1
            ? <>Can't reach the laptop. Are you on the same Wi-Fi? Retrying…</>
            : <>Connecting to the laptop…</>}
        </div>
      )}
      <main>
        {tab === 'live' && <Live go={setTab} />}
        {tab === 'map' && <MapTab />}
        {tab === 'signal' && <More />}
        {tab === 'missions' && <Missions />}
        {tab === 'more' && <More />}
      </main>
      {S.toast && <div key={S.toast.id} className={`toast ${S.toast.level}`}>{S.toast.text}</div>}
      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? 'on' : ''} onClick={() => setTab(t.id)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">{t.icon}</svg>
            <span>{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
