'use client';

import { useEffect, useState } from 'react';
import { apiFetch, apiPost } from '../lib/api';

interface Era {
    id: string;
    name: string;
    format: string;
    description: string;
    deck_count: number;
}

interface Matchup {
    opponent: string;
    opponent_colors: string;
    wins: number;
    losses: number;
    draws: number;
    result: string;
}

interface GauntletResult {
    era: string;
    win_rate: number;
    total_wins: number;
    total_losses: number;
    total_matches: number;
    matchups: Matchup[];
    verdict: string;
}

export default function GauntletPage() {
    const [eras, setEras] = useState<Era[]>([]);
    const [selectedEra, setSelectedEra] = useState('');
    const [decklist, setDecklist] = useState('');
    const [result, setResult] = useState<GauntletResult | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        apiFetch<{ eras: Era[] }>('/api/gauntlet/eras').then(res => {
            setEras(res.eras);
            if (res.eras.length > 0) setSelectedEra(res.eras[0].id);
        });
    }, []);

    async function runGauntlet() {
        if (!decklist.trim() || !selectedEra) return;
        setLoading(true);
        setError('');
        setResult(null);
        try {
            const res = await apiPost<GauntletResult>('/api/gauntlet/run', {
                decklist: decklist,
                era: selectedEra,
            });
            setResult(res);
        } catch (e) {
            setError('Gauntlet failed. Check your decklist format.');
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="page-container">
            <h1 className="page-title">⏳ The Time Machine</h1>
            <p className="page-subtitle">Could your evolved deck win a World Championship?</p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
                {/* Input */}
                <div className="card">
                    <h2 className="card-title" style={{ marginBottom: '1rem' }}>Select an Era</h2>
                    <select
                        className="select input"
                        value={selectedEra}
                        onChange={(e) => setSelectedEra(e.target.value)}
                        style={{ marginBottom: '1rem', width: '100%' }}
                    >
                        {eras.map(era => (
                            <option key={era.id} value={era.id}>
                                {era.name} — {era.format} ({era.deck_count} decks)
                            </option>
                        ))}
                    </select>

                    <h3 style={{ fontSize: '0.9rem', marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
                        Paste Your Decklist
                    </h3>
                    <textarea
                        className="input"
                        placeholder={"4 Lightning Bolt\n4 Goblin Guide\n4 Monastery Swiftspear\n20 Mountain\n..."}
                        value={decklist}
                        onChange={(e) => setDecklist(e.target.value)}
                    />

                    <button
                        className="btn btn-primary"
                        onClick={runGauntlet}
                        disabled={loading || !decklist.trim()}
                        style={{ marginTop: '1rem', width: '100%' }}
                    >
                        {loading ? '⏳ Running Gauntlet...' : '🏆 Run the Gauntlet'}
                    </button>

                    {error && <p style={{ color: 'var(--accent-red)', marginTop: '0.5rem' }}>{error}</p>}
                </div>

                {/* Results */}
                <div className="card">
                    <h2 className="card-title" style={{ marginBottom: '1rem' }}>Results</h2>
                    {!result ? (
                        <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '3rem 0' }}>
                            Select an era, paste your deck, and hit Run
                        </p>
                    ) : (
                        <>
                            <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                                <div className="stat-card">
                                    <div className="stat-value emerald">{result.win_rate}%</div>
                                    <div className="stat-label">Win Rate</div>
                                </div>
                                <div className="stat-card">
                                    <div className="stat-value gold">{result.total_wins}</div>
                                    <div className="stat-label">Wins</div>
                                </div>
                                <div className="stat-card">
                                    <div className="stat-value red">{result.total_losses}</div>
                                    <div className="stat-label">Losses</div>
                                </div>
                            </div>

                            <div style={{
                                padding: '1rem',
                                background: 'rgba(240, 180, 41, 0.08)',
                                borderRadius: 'var(--radius-md)',
                                textAlign: 'center',
                                fontSize: '1.1rem',
                                fontWeight: 600,
                                marginBottom: '1rem',
                            }}>
                                {result.verdict}
                            </div>

                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Opponent</th>
                                        <th>Colors</th>
                                        <th>Record</th>
                                        <th>Result</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {result.matchups.map((m, i) => (
                                        <tr key={i}>
                                            <td style={{ fontWeight: 500 }}>{m.opponent}</td>
                                            <td style={{ color: 'var(--text-muted)' }}>{m.opponent_colors}</td>
                                            <td style={{ fontFamily: 'var(--font-mono)' }}>{m.wins}-{m.losses}</td>
                                            <td><span className={`badge ${m.result === 'W' ? 'win' : m.result === 'L' ? 'loss' : 'silver'}`}>{m.result}</span></td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
