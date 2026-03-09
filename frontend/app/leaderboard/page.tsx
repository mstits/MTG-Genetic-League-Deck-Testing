'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

interface LeaderboardEntry {
    id: number;
    name: string;
    elo: number;
    division: string;
    wins: number;
    losses: number;
    draws: number;
    colors: string;
    generation: number;
}

export default function LeaderboardPage() {
    const [decks, setDecks] = useState<LeaderboardEntry[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        apiFetch<{ decks: LeaderboardEntry[] }>('/api/leaderboard')
            .then(res => setDecks(res.decks || []))
            .catch(() => { })
            .finally(() => setLoading(false));
    }, []);

    if (loading) return <div className="page-container"><p style={{ color: 'var(--text-secondary)' }}>Loading leaderboard...</p></div>;

    return (
        <div className="page-container">
            <h1 className="page-title">🏆 Leaderboard</h1>
            <p className="page-subtitle">{decks.length} decks ranked by ELO — the fittest survive</p>

            <div className="card">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Rank</th>
                            <th>Deck</th>
                            <th>Colors</th>
                            <th>ELO</th>
                            <th>W/L/D</th>
                            <th>Win Rate</th>
                            <th>Gen</th>
                            <th>Division</th>
                        </tr>
                    </thead>
                    <tbody>
                        {decks.map((d, i) => {
                            const total = d.wins + d.losses + (d.draws || 0);
                            const wr = total > 0 ? Math.round(d.wins / total * 100) : 0;
                            return (
                                <tr key={d.id}>
                                    <td style={{ fontFamily: 'var(--font-mono)', color: i < 3 ? 'var(--accent-gold)' : 'var(--text-muted)', fontWeight: i < 3 ? 700 : 400 }}>
                                        {i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `#${i + 1}`}
                                    </td>
                                    <td style={{ fontWeight: 600 }}>{d.name}</td>
                                    <td style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{d.colors || '—'}</td>
                                    <td style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--accent-gold)' }}>
                                        {d.elo?.toFixed(0)}
                                    </td>
                                    <td style={{ fontFamily: 'var(--font-mono)' }}>
                                        <span style={{ color: 'var(--accent-emerald)' }}>{d.wins}</span>
                                        /
                                        <span style={{ color: 'var(--accent-red)' }}>{d.losses}</span>
                                        {d.draws ? `/${d.draws}` : ''}
                                    </td>
                                    <td style={{ fontFamily: 'var(--font-mono)' }}>
                                        <span style={{ color: wr >= 60 ? 'var(--accent-emerald)' : wr >= 40 ? 'var(--text-primary)' : 'var(--accent-red)' }}>
                                            {wr}%
                                        </span>
                                    </td>
                                    <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{d.generation}</td>
                                    <td><span className={`badge ${d.division?.toLowerCase()}`}>{d.division}</span></td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
