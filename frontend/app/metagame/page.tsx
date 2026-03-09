'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

interface Matchup {
    attacker: string;
    defender: string;
    win_rate: number;
    games: number;
}

interface MatrixData {
    colors: string[];
    matchups: Matchup[];
    total_matchups: number;
}

const COLOR_NAMES: Record<string, string> = {
    W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colorless',
    WU: 'Azorius', WB: 'Orzhov', WR: 'Boros', WG: 'Selesnya',
    UB: 'Dimir', UR: 'Izzet', UG: 'Simic', BR: 'Rakdos',
    BG: 'Golgari', RG: 'Gruul',
    WUB: 'Esper', WUR: 'Jeskai', WUG: 'Bant', WBR: 'Mardu',
    WBG: 'Abzan', WRG: 'Naya', UBR: 'Grixis', UBG: 'Sultai',
    URG: 'Temur', BRG: 'Jund',
    WUBR: 'Sans-Green', WUBG: 'Sans-Red', WURG: 'Sans-Black',
    WBRG: 'Sans-Blue', UBRG: 'Sans-White',
    WUBRG: 'Five-Color',
};

const MANA_EMOJI: Record<string, string> = {
    W: '☀️', U: '💧', B: '💀', R: '🔥', G: '🌿', C: '◇',
};

function getColorEmoji(code: string): string {
    return code.split('').map(c => MANA_EMOJI[c] || c).join('');
}

function getColorName(code: string): string {
    return COLOR_NAMES[code] || code;
}

function wrColor(wr: number): string {
    if (wr >= 60) return 'var(--accent-emerald)';
    if (wr >= 55) return '#69db7c';
    if (wr >= 45) return 'var(--text-secondary)';
    if (wr >= 40) return '#ff8787';
    return 'var(--accent-red)';
}

function wrBg(wr: number): string {
    if (wr >= 60) return 'rgba(52, 211, 153, 0.15)';
    if (wr >= 55) return 'rgba(52, 211, 153, 0.08)';
    if (wr >= 45) return 'transparent';
    if (wr >= 40) return 'rgba(239, 68, 68, 0.08)';
    return 'rgba(239, 68, 68, 0.15)';
}

export default function MetagamePage() {
    const [data, setData] = useState<MatrixData | null>(null);
    const [loading, setLoading] = useState(true);
    const [hoveredCell, setHoveredCell] = useState<string | null>(null);

    useEffect(() => {
        async function load() {
            try {
                const res = await apiFetch<MatrixData>('/api/matchup-matrix');
                setData(res);
            } catch (e) {
                console.error('Failed to load matchup data:', e);
            } finally {
                setLoading(false);
            }
        }
        load();
    }, []);

    if (loading) return (
        <div className="page-container">
            <p style={{ color: 'var(--text-secondary)' }}>Loading metagame data...</p>
        </div>
    );

    if (!data || data.colors.length === 0) return (
        <div className="page-container">
            <h1 className="page-title">🎯 Metagame Wheel</h1>
            <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '3rem' }}>
                Not enough match data yet. Run some seasons to populate the matchup matrix!
            </p>
        </div>
    );

    // Build lookup map
    const wrMap: Record<string, Matchup> = {};
    for (const m of data.matchups) {
        wrMap[`${m.attacker}-${m.defender}`] = m;
    }

    // Find strongest and weakest matchups
    const sorted = [...data.matchups].sort((a, b) => b.win_rate - a.win_rate);
    const topEdges = sorted.slice(0, 5);
    const bottomEdges = sorted.slice(-5).reverse();

    return (
        <div className="page-container">
            <h1 className="page-title">🎯 Metagame Wheel</h1>
            <p className="page-subtitle">
                Rock-Paper-Scissors dynamics across {data.colors.length} color archetypes
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginBottom: '1.5rem' }}>
                {/* Strongest Matchups */}
                <div className="card">
                    <div className="card-header">
                        <h2 className="card-title">💪 Strongest Edges</h2>
                    </div>
                    <div style={{ padding: '0.5rem 1rem' }}>
                        {topEdges.map((m, i) => (
                            <div key={i} style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                padding: '8px 0',
                                borderBottom: i < topEdges.length - 1 ? '1px solid var(--border)' : 'none',
                            }}>
                                <span>
                                    {getColorEmoji(m.attacker)} <strong>{getColorName(m.attacker)}</strong>
                                    <span style={{ color: 'var(--text-muted)', margin: '0 6px' }}>→</span>
                                    {getColorEmoji(m.defender)} {getColorName(m.defender)}
                                </span>
                                <span style={{
                                    fontFamily: 'var(--font-mono)',
                                    fontWeight: 700,
                                    color: 'var(--accent-emerald)',
                                }}>
                                    {m.win_rate}% <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>({m.games}g)</span>
                                </span>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Weakest Matchups */}
                <div className="card">
                    <div className="card-header">
                        <h2 className="card-title">📉 Weakest Matchups</h2>
                    </div>
                    <div style={{ padding: '0.5rem 1rem' }}>
                        {bottomEdges.map((m, i) => (
                            <div key={i} style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                padding: '8px 0',
                                borderBottom: i < bottomEdges.length - 1 ? '1px solid var(--border)' : 'none',
                            }}>
                                <span>
                                    {getColorEmoji(m.attacker)} <strong>{getColorName(m.attacker)}</strong>
                                    <span style={{ color: 'var(--text-muted)', margin: '0 6px' }}>→</span>
                                    {getColorEmoji(m.defender)} {getColorName(m.defender)}
                                </span>
                                <span style={{
                                    fontFamily: 'var(--font-mono)',
                                    fontWeight: 700,
                                    color: 'var(--accent-red)',
                                }}>
                                    {m.win_rate}% <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>({m.games}g)</span>
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* Full Matrix */}
            <div className="card">
                <div className="card-header">
                    <h2 className="card-title">📊 Full Matchup Matrix</h2>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        Row vs Column win rate (min 5 games)
                    </span>
                </div>
                <div style={{ overflowX: 'auto', padding: '1rem' }}>
                    <table style={{
                        borderCollapse: 'collapse',
                        width: '100%',
                        fontSize: '0.8rem',
                    }}>
                        <thead>
                            <tr>
                                <th style={{
                                    padding: 8,
                                    textAlign: 'center',
                                    color: 'var(--text-muted)',
                                    borderBottom: '2px solid var(--border)',
                                    minWidth: 70,
                                }}>vs</th>
                                {data.colors.map(c => (
                                    <th key={c} style={{
                                        padding: 8,
                                        textAlign: 'center',
                                        color: 'var(--text-secondary)',
                                        borderBottom: '2px solid var(--border)',
                                        minWidth: 60,
                                    }}>
                                        <div>{getColorEmoji(c)}</div>
                                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{c}</div>
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {data.colors.map(c1 => (
                                <tr key={c1}>
                                    <td style={{
                                        padding: 8,
                                        fontWeight: 600,
                                        color: 'var(--text-primary)',
                                        borderRight: '2px solid var(--border)',
                                        whiteSpace: 'nowrap',
                                    }}>
                                        {getColorEmoji(c1)} {c1}
                                    </td>
                                    {data.colors.map(c2 => {
                                        if (c1 === c2) {
                                            return (
                                                <td key={c2} style={{
                                                    padding: 8,
                                                    textAlign: 'center',
                                                    color: 'var(--text-muted)',
                                                    background: 'var(--bg-elevated)',
                                                }}>—</td>
                                            );
                                        }
                                        const m = wrMap[`${c1}-${c2}`];
                                        const cellKey = `${c1}-${c2}`;
                                        const isHovered = hoveredCell === cellKey;
                                        return (
                                            <td
                                                key={c2}
                                                onMouseEnter={() => setHoveredCell(cellKey)}
                                                onMouseLeave={() => setHoveredCell(null)}
                                                style={{
                                                    padding: 8,
                                                    textAlign: 'center',
                                                    fontFamily: 'var(--font-mono)',
                                                    fontWeight: 600,
                                                    color: m ? wrColor(m.win_rate) : 'var(--text-muted)',
                                                    background: m ? wrBg(m.win_rate) : 'transparent',
                                                    cursor: m ? 'pointer' : 'default',
                                                    transition: 'all 0.15s ease',
                                                    transform: isHovered ? 'scale(1.1)' : 'none',
                                                    position: 'relative',
                                                }}
                                                title={m ? `${getColorName(c1)} vs ${getColorName(c2)}: ${m.win_rate}% (${m.games} games)` : 'No data'}
                                            >
                                                {m ? `${m.win_rate}%` : '—'}
                                            </td>
                                        );
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
