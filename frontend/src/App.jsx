import React, { useEffect, useState } from 'react';
import { onConnectionChange, getConnectionStatus } from './api/client';
import Home from './pages/Home.jsx';
import Experiment from './pages/Experiment.jsx';
import Run from './pages/Run.jsx';
import Report from './pages/Report.jsx';
import Research from './pages/Research.jsx';
import Build from './pages/Build.jsx';
import Ops from './pages/Ops.jsx';
import Markets from './pages/Markets.jsx';
import StrategyLab from './pages/StrategyLab.jsx';
import Finance from './pages/Finance.jsx';

// ---- tiny hash router ----------------------------------------------------

function parseRoute() {
  const hash = window.location.hash || '#/';
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts.length === 0) return { page: 'home' };
  if (parts[0] === 'build') return { page: 'build' };
  if (parts[0] === 'ops') return { page: 'ops' };
  if (parts[0] === 'markets') {
    return { page: 'markets', id: parts[1] ? decodeURIComponent(parts[1]) : null };
  }
  if (parts[0] === 'lab') return { page: 'lab' };
  if (parts[0] === 'finance') return { page: 'finance' };
  if (parts[0] === 'research') {
    return { page: 'research', id: parts[1] ? decodeURIComponent(parts[1]) : null };
  }
  if (parts[0] === 'experiment' && parts[1]) {
    return { page: 'experiment', id: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === 'run' && parts[1]) {
    if (parts[2] === 'report') {
      return { page: 'report', id: decodeURIComponent(parts[1]) };
    }
    return { page: 'run', id: decodeURIComponent(parts[1]) };
  }
  return { page: 'home' };
}

function useRoute() {
  const [route, setRoute] = useState(parseRoute);
  useEffect(() => {
    const onHash = () => setRoute(parseRoute());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  return route;
}

// ---- status bar ----------------------------------------------------------

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span>
      {now.toISOString().slice(0, 10)} {now.toTimeString().slice(0, 8)}
    </span>
  );
}

function ConnStatus() {
  const [ok, setOk] = useState(getConnectionStatus());
  useEffect(() => onConnectionChange(setOk), []);
  const cls = ok === true ? 'online' : ok === false ? 'offline' : 'unknown';
  const text = ok === true ? 'API ONLINE' : ok === false ? 'API OFFLINE' : 'CONNECTING';
  return <span className={`conn ${cls}`}>{text}</span>;
}

function StatusBar() {
  return (
    <header className="statusbar">
      <div className="brand">
        <a href="#/">TEZCAT — MARKET ECOLOGY LAB</a>
        <a className="navlink" href="#/build">BUILD</a>
        <a className="navlink" href="#/research">RESEARCH</a>
        <a className="navlink" href="#/lab">LAB</a>
        <a className="navlink" href="#/finance">FINANCE</a>
        <a className="navlink" href="#/markets">MARKETS</a>
        <a className="navlink" href="#/ops">TRADEOPS</a>
      </div>
      <div className="right">
        <Clock />
        <ConnStatus />
      </div>
    </header>
  );
}

// ---- app -----------------------------------------------------------------

export default function App() {
  const route = useRoute();
  let page;
  switch (route.page) {
    case 'experiment':
      page = <Experiment id={route.id} key={route.id} />;
      break;
    case 'run':
      page = <Run id={route.id} key={route.id} />;
      break;
    case 'report':
      page = <Report id={route.id} key={route.id} />;
      break;
    case 'research':
      page = <Research id={route.id} key={route.id || 'list'} />;
      break;
    case 'build':
      page = <Build />;
      break;
    case 'ops':
      page = <Ops />;
      break;
    case 'markets':
      page = <Markets id={route.id} key={route.id || 'list'} />;
      break;
    case 'lab':
      page = <StrategyLab />;
      break;
    case 'finance':
      page = <Finance />;
      break;
    default:
      page = <Home />;
  }
  return (
    <>
      <StatusBar />
      <main className="page">{page}</main>
    </>
  );
}
