'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

interface MutationEntry {
    card_added: string;
    card_removed: string;
    avg_delta: number;
    swap_count: number;
}

export default function HeatmapsPage() {
    const [mutations, setMutations] = useState<MutationEntry[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        apiFetch<{ mutations: MutationEntry[] }>('/api/mutations/heatmap?limit=100')
            .then(res => setMutations(res.mutations))
            .catch(() => { })
            .finally(() => setLoading(false));
    }, []);

    const maxDelta = Math.max(...mutations.map(m => Math.abs(m.avg_delta)), 1);

    function getColor(delta: number): string {
        const intensity = Math.min(Math.abs(delta) / maxDelta, 1);
        if (delta > 0) {
            return `rgba(52, 211, 153, ${0.2 + intensity * 0.8})`;
        }
        return `rgba(239, 68, 68, ${0.2 + intensity * 0.8})`;
    }

    if (loading) return <div className="page-container"><p style={{ color: 'var(--text-secondary)' }}>Loading heatmap data...</p></div>;

    return (
        <div className="page-container">
            <h1 className="page-title">🔥 Mutation Heatmaps</h1>
            <p className="page-subtitle">Which card swaps produced the biggest ELO spikes?</p>

            {mutations.length === 0 ? (
                <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
                    <p style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>No mutation data yet</p>
                    <p style={{ color: 'var(--text-muted)' }}>
                        Run a few GA generations and mutation swaps will appear here.
                    </p>
                </div>
            ) : (
                <div className="card">
                    <div className="card-header">
                        <h2 className="card-title">Top Card Swaps by ELO Impact</h2>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', fontSize: '0.8rem' }}>
                            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                <span style={{ width: 12, height: 12, borderRadius: 3, background: 'rgba(52, 211, 153, 0.7)' }} />
                                Positive
                            </span>
                            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                <span style={{ width: 12, height: 12, borderRadius: 3, background: 'rgba(239, 68, 68, 0.7)' }} />
                                Negative
                            </span>
                        </div>
                    </div>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Card Removed</th>
                                <th>→</th>
                                <th>Card Added</th>
                                <th>Avg ELO Δ</th>
                                <th>Swaps</th>
                                <th>Impact</th>
                            </tr>
                        </thead>
                        <tbody>
                            {mutations.map((m, i) => (
                                <tr key={i}>
                                    <td style={{ color: 'var(--accent-red)', fontWeight: 500 }}>– {m.card_removed}</td>
                                    <td style={{ color: 'var(--text-muted)', textAlign: 'center' }}>→</td>
                                    <td style={{ color: 'var(--accent-emerald)', fontWeight: 500 }}>+ {m.card_added}</td>
                                    <td style={{
                                        fontFamily: 'var(--font-mono)',
                                        fontWeight: 700,
                                        color: m.avg_delta >= 0 ? 'var(--accent-emerald)' : 'var(--accent-red)',
                                    }}>
                                        {m.avg_delta >= 0 ? '+' : ''}{m.avg_delta}
                                    </td>
                                    <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{m.swap_count}</td>
                                    <td>
                                        <div style={{
                                            width: `${Math.min(Math.abs(m.avg_delta) / maxDelta * 100, 100)}%`,
                                            minWidth: '10%',
                                            height: 8,
                                            borderRadius: 4,
                                            background: getColor(m.avg_delta),
                                        }} />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
