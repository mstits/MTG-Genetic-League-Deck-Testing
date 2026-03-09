'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from './lib/api';
import { useEloStream } from './hooks/useWebSocket';

interface StatsData {
  total_decks: number;
  total_matches: number;
  active_decks: number;
  top_elo: number;
  avg_elo: number;
  total_seasons: number;
}

interface LeaderboardEntry {
  id: number;
  name: string;
  elo: number;
  division: string;
  wins: number;
  losses: number;
  colors: string;
}

export default function Dashboard() {
  const [stats, setStats] = useState<StatsData | null>(null);
  const [topDecks, setTopDecks] = useState<LeaderboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const { eloUpdates, isConnected } = useEloStream();

  useEffect(() => {
    async function load() {
      try {
        const [statsRes, leaderboardRes] = await Promise.all([
          apiFetch<StatsData>('/api/stats'),
          apiFetch<{ decks: LeaderboardEntry[] }>('/api/leaderboard'),
        ]);
        setStats(statsRes);
        setTopDecks(leaderboardRes.decks?.slice(0, 10) || []);
      } catch (e) {
        console.error('Failed to load dashboard:', e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) return <div className="page-container"><p style={{ color: 'var(--text-secondary)' }}>Loading dashboard...</p></div>;

  return (
    <div className="page-container">
      <h1 className="page-title">⚡ Evolution Dashboard</h1>
      <p className="page-subtitle">
        Real-time genetic algorithm deck evolution
        <span style={{
          display: 'inline-flex',
          alignItems: 'center',
          marginLeft: '1rem',
          fontSize: '0.8rem',
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: isConnected ? 'var(--accent-emerald)' : 'var(--accent-red)',
            marginRight: 6,
            boxShadow: isConnected ? 'var(--glow-emerald)' : 'none',
          }} />
          {isConnected ? 'Live' : 'Offline'}
        </span>
      </p>

      {/* Stats Grid */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-value gold">{stats?.total_decks ?? '—'}</div>
          <div className="stat-label">Total Decks</div>
        </div>
        <div className="stat-card">
          <div className="stat-value emerald">{stats?.total_matches ?? '—'}</div>
          <div className="stat-label">Matches Played</div>
        </div>
        <div className="stat-card">
          <div className="stat-value blue">{stats?.active_decks ?? '—'}</div>
          <div className="stat-label">Active Decks</div>
        </div>
        <div className="stat-card">
          <div className="stat-value gold">{stats?.top_elo?.toFixed(0) ?? '—'}</div>
          <div className="stat-label">Peak ELO</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
        {/* Top Decks */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">🏆 Top 10 Decks</h2>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Deck</th>
                <th>ELO</th>
                <th>W/L</th>
                <th>Division</th>
              </tr>
            </thead>
            <tbody>
              {topDecks.map((d, i) => (
                <tr key={d.id}>
                  <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{i + 1}</td>
                  <td>
                    <span style={{ fontWeight: 600 }}>{d.name}</span>
                    {d.colors && <span style={{ marginLeft: 6, fontSize: '0.75rem', color: 'var(--text-muted)' }}>{d.colors}</span>}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--accent-gold)' }}>
                    {d.elo?.toFixed(0)}
                  </td>
                  <td style={{ fontFamily: 'var(--font-mono)' }}>{d.wins}-{d.losses}</td>
                  <td><span className={`badge ${d.division?.toLowerCase()}`}>{d.division}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Live ELO Feed */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">📡 Live ELO Feed</h2>
            <span style={{
              fontSize: '0.75rem',
              color: isConnected ? 'var(--accent-emerald)' : 'var(--text-muted)',
            }}>
              {isConnected ? '● Streaming' : '○ Disconnected'}
            </span>
          </div>
          <div className="elo-feed">
            {eloUpdates.length === 0 ? (
              <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '2rem' }}>
                Waiting for matches...
              </p>
            ) : (
              eloUpdates.map((u, i) => (
                <div key={`${u.match_id}-${i}`} className="elo-item">
                  <span className={`elo-delta ${u.delta >= 0 ? 'positive' : 'negative'}`}>
                    {u.delta >= 0 ? '+' : ''}{u.delta.toFixed(1)}
                  </span>
                  <span style={{ flex: 1 }}>
                    <strong>{u.deck_name}</strong>
                    <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
                      → {u.new_elo.toFixed(0)}
                    </span>
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
