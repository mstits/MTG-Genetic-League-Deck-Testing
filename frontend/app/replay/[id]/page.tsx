'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { apiFetch } from '../../lib/api';

interface Turn {
    turn: number;
    events: string[];
}

interface MatchReplay {
    match_id: number;
    deck1: { id: number; name: string };
    deck2: { id: number; name: string };
    winner: string | null;
    total_turns: number;
    turns: Turn[];
    raw_log: string[];
}

export default function ReplayPage() {
    const params = useParams();
    const matchId = params.id;
    const [replay, setReplay] = useState<MatchReplay | null>(null);
    const [currentTurn, setCurrentTurn] = useState(0);
    const [isPlaying, setIsPlaying] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [speed, setSpeed] = useState(1500); // ms between turns

    useEffect(() => {
        async function load() {
            try {
                const data = await apiFetch<MatchReplay>(`/api/match/${matchId}`);
                setReplay(data);
            } catch (e: any) {
                setError(e.message || 'Failed to load match');
            } finally {
                setLoading(false);
            }
        }
        if (matchId) load();
    }, [matchId]);

    // Auto-play timer
    useEffect(() => {
        if (!isPlaying || !replay) return;
        if (currentTurn >= replay.turns.length - 1) {
            setIsPlaying(false);
            return;
        }
        const timer = setTimeout(() => setCurrentTurn(t => t + 1), speed);
        return () => clearTimeout(timer);
    }, [isPlaying, currentTurn, replay, speed]);

    const goToTurn = useCallback((t: number) => {
        if (replay) setCurrentTurn(Math.max(0, Math.min(t, replay.turns.length - 1)));
    }, [replay]);

    if (loading) return (
        <div className="page-container">
            <p style={{ color: 'var(--text-secondary)' }}>Loading replay...</p>
        </div>
    );

    if (error || !replay) return (
        <div className="page-container">
            <h1 className="page-title">⚠️ Error</h1>
            <p style={{ color: 'var(--accent-red)' }}>{error || 'Match not found'}</p>
        </div>
    );

    const turn = replay.turns[currentTurn];
    const progress = replay.turns.length > 1
        ? (currentTurn / (replay.turns.length - 1)) * 100
        : 100;

    return (
        <div className="page-container">
            <h1 className="page-title">🎬 Match Replay #{replay.match_id}</h1>
            <p className="page-subtitle">
                <strong>{replay.deck1.name}</strong> vs <strong>{replay.deck2.name}</strong>
                {replay.winner && (
                    <span style={{
                        marginLeft: '1rem',
                        padding: '2px 10px',
                        borderRadius: 12,
                        background: 'var(--accent-gold)',
                        color: '#000',
                        fontSize: '0.8rem',
                        fontWeight: 700,
                    }}>
                        🏆 {replay.winner}
                    </span>
                )}
            </p>

            {/* Timeline */}
            <div style={{
                background: 'var(--bg-card)',
                borderRadius: 12,
                padding: '1.5rem',
                marginBottom: '1.5rem',
                border: '1px solid var(--border)',
            }}>
                {/* Progress Bar */}
                <div style={{
                    width: '100%',
                    height: 6,
                    borderRadius: 3,
                    background: 'var(--bg-elevated)',
                    marginBottom: '1rem',
                    overflow: 'hidden',
                    cursor: 'pointer',
                }}
                    onClick={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        const pct = (e.clientX - rect.left) / rect.width;
                        goToTurn(Math.round(pct * (replay.turns.length - 1)));
                    }}
                >
                    <div style={{
                        width: `${progress}%`,
                        height: '100%',
                        borderRadius: 3,
                        background: 'linear-gradient(90deg, var(--accent-blue), var(--accent-purple))',
                        transition: 'width 0.3s ease',
                    }} />
                </div>

                {/* Controls */}
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '1rem',
                    justifyContent: 'center',
                }}>
                    <button
                        className="btn-sm"
                        onClick={() => goToTurn(0)}
                        style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 12px', cursor: 'pointer', color: 'var(--text-primary)' }}
                    >⏮</button>
                    <button
                        className="btn-sm"
                        onClick={() => goToTurn(currentTurn - 1)}
                        style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 12px', cursor: 'pointer', color: 'var(--text-primary)' }}
                    >◀</button>
                    <button
                        onClick={() => setIsPlaying(!isPlaying)}
                        style={{
                            background: isPlaying ? 'var(--accent-red)' : 'var(--accent-emerald)',
                            border: 'none',
                            borderRadius: 8,
                            padding: '8px 20px',
                            cursor: 'pointer',
                            color: '#fff',
                            fontWeight: 700,
                            fontSize: '1rem',
                            minWidth: 70,
                        }}
                    >{isPlaying ? '⏸' : '▶'}</button>
                    <button
                        className="btn-sm"
                        onClick={() => goToTurn(currentTurn + 1)}
                        style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 12px', cursor: 'pointer', color: 'var(--text-primary)' }}
                    >▶</button>
                    <button
                        className="btn-sm"
                        onClick={() => goToTurn(replay.turns.length - 1)}
                        style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 12px', cursor: 'pointer', color: 'var(--text-primary)' }}
                    >⏭</button>

                    <span style={{
                        fontFamily: 'var(--font-mono)',
                        color: 'var(--text-muted)',
                        marginLeft: '1rem',
                    }}>
                        Turn {turn?.turn || 0} / {replay.total_turns}
                    </span>

                    {/* Speed control */}
                    <select
                        value={speed}
                        onChange={e => setSpeed(Number(e.target.value))}
                        style={{
                            background: 'var(--bg-elevated)',
                            border: '1px solid var(--border)',
                            borderRadius: 6,
                            padding: '4px 8px',
                            color: 'var(--text-primary)',
                            fontSize: '0.85rem',
                        }}
                    >
                        <option value={3000}>0.5x</option>
                        <option value={1500}>1x</option>
                        <option value={750}>2x</option>
                        <option value={300}>5x</option>
                    </select>
                </div>
            </div>

            {/* Game Log for Current Turn */}
            <div className="card" style={{ minHeight: 300 }}>
                <div className="card-header">
                    <h2 className="card-title">
                        📜 Turn {turn?.turn || 0} Events
                    </h2>
                </div>
                <div style={{
                    padding: '1rem',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '0.85rem',
                    lineHeight: 1.6,
                }}>
                    {turn?.events.map((event, i) => {
                        // Color-code events
                        let color = 'var(--text-secondary)';
                        if (event.includes('deals') || event.includes('damage')) color = 'var(--accent-red)';
                        else if (event.includes('draws') || event.includes('draw')) color = 'var(--accent-blue)';
                        else if (event.includes('destroys') || event.includes('exiles')) color = '#ff6b6b';
                        else if (event.includes('enters') || event.includes('deploying')) color = 'var(--accent-emerald)';
                        else if (event.includes('attacks') || event.includes('attack')) color = 'var(--accent-gold)';
                        else if (event.includes('blocks') || event.includes('blocking')) color = '#9775fa';
                        else if (event.includes('counters')) color = '#ffd43b';
                        else if (event.includes('holding up mana')) color = '#69db7c';
                        else if (event.includes('LETHAL') || event.includes('wins')) color = 'var(--accent-gold)';

                        return (
                            <div key={i} style={{ color, padding: '2px 0' }}>
                                {event}
                            </div>
                        );
                    })}
                    {(!turn || turn.events.length === 0) && (
                        <p style={{ color: 'var(--text-muted)', textAlign: 'center' }}>
                            No events this turn
                        </p>
                    )}
                </div>
            </div>

            {/* Full Log (collapsible) */}
            <details style={{ marginTop: '1.5rem' }}>
                <summary style={{
                    cursor: 'pointer',
                    color: 'var(--text-muted)',
                    padding: '0.5rem',
                    fontSize: '0.9rem',
                }}>
                    📋 View Full Raw Log ({replay.raw_log.length} lines)
                </summary>
                <div className="card" style={{ marginTop: '0.5rem' }}>
                    <pre style={{
                        padding: '1rem',
                        fontFamily: 'var(--font-mono)',
                        fontSize: '0.75rem',
                        lineHeight: 1.5,
                        color: 'var(--text-secondary)',
                        maxHeight: 400,
                        overflow: 'auto',
                        whiteSpace: 'pre-wrap',
                    }}>
                        {replay.raw_log.join('\n')}
                    </pre>
                </div>
            </details>
        </div>
    );
}
