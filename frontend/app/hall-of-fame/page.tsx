'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

interface HoFEntry {
    deck_id: number;
    deck_name: string;
    peak_elo: number;
    peak_season: number;
    total_wins: number;
    total_matches: number;
    colors: string;
    inducted_at: string;
}

export default function HallOfFamePage() {
    const [inductees, setInductees] = useState<HoFEntry[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        apiFetch<{ inductees: HoFEntry[] }>('/api/hall-of-fame')
            .then(res => setInductees(res.inductees))
            .catch(() => { })
            .finally(() => setLoading(false));
    }, []);

    if (loading) return <div className="page-container"><p style={{ color: 'var(--text-secondary)' }}>Loading...</p></div>;

    return (
        <div className="page-container">
            <h1 className="page-title">🏛️ Hall of Fame</h1>
            <p className="page-subtitle">The all-time greatest decks the GA has ever birthed</p>

            {inductees.length === 0 ? (
                <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
                    <p style={{ fontSize: '3rem', marginBottom: '1rem' }}>👑</p>
                    <p style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>The Hall awaits its first champion</p>
                    <p style={{ color: 'var(--text-muted)' }}>
                        Decks are inducted when they reach Mythic division or achieve top-3 ELO.
                    </p>
                </div>
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1.5rem' }}>
                    {inductees.map((d, i) => (
                        <div key={d.deck_id} className="card" style={{
                            background: i === 0
                                ? 'linear-gradient(135deg, rgba(240, 180, 41, 0.08), var(--bg-card))'
                                : 'var(--bg-card)',
                            border: i === 0 ? '1px solid var(--accent-gold-dim)' : undefined,
                        }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
                                <span style={{ fontSize: '1.5rem' }}>
                                    {i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : '🏅'}
                                </span>
                                <div>
                                    <div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{d.deck_name}</div>
                                    <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                                        {d.colors || 'Colorless'} • Season {d.peak_season}
                                    </div>
                                </div>
                            </div>

                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                                <div className="stat-card" style={{ padding: '0.75rem' }}>
                                    <div className="stat-value gold" style={{ fontSize: '1.5rem' }}>{d.peak_elo.toFixed(0)}</div>
                                    <div className="stat-label">Peak ELO</div>
                                </div>
                                <div className="stat-card" style={{ padding: '0.75rem' }}>
                                    <div className="stat-value emerald" style={{ fontSize: '1.5rem' }}>
                                        {d.total_matches > 0 ? Math.round(d.total_wins / d.total_matches * 100) : 0}%
                                    </div>
                                    <div className="stat-label">Win Rate</div>
                                </div>
                            </div>

                            <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                                Inducted {new Date(d.inducted_at).toLocaleDateString()} • {d.total_wins}W / {d.total_matches - d.total_wins}L
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
