'use client';

import { useState } from 'react';
import { apiPost } from '../lib/api';

interface SaltResult {
    bracket: number;
    salt_score: number;
    flagged_cards: { card: string; bracket: number; count: number }[];
    breakdown: { [key: string]: number };
}

const BRACKET_INFO: { [key: number]: { label: string; color: string; emoji: string; desc: string } } = {
    1: { label: 'Casual', color: 'var(--accent-emerald)', emoji: '🌿', desc: 'Fair and fun — no complaints here' },
    2: { label: 'Focused', color: 'var(--accent-blue)', emoji: '🎯', desc: 'Strong synergies and popular staples' },
    3: { label: 'Optimized', color: 'var(--accent-purple)', emoji: '⚡', desc: 'Tutors, strong mana, and stax pieces' },
    4: { label: 'Competitive (cEDH)', color: 'var(--accent-red)', emoji: '☠️', desc: 'Free counters, fast mana, combo kills' },
};

export default function SaltScorePage() {
    const [decklist, setDecklist] = useState('');
    const [result, setResult] = useState<SaltResult | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    async function analyze() {
        if (!decklist.trim()) return;
        setLoading(true);
        setError('');
        try {
            const res = await apiPost<SaltResult>('/api/salt-score', { decklist });
            setResult(res);
        } catch {
            setError('Failed to analyze deck');
        } finally {
            setLoading(false);
        }
    }

    const bracketInfo = result ? BRACKET_INFO[result.bracket] : null;

    return (
        <div className="page-container">
            <h1 className="page-title">🧂 Salt Score</h1>
            <p className="page-subtitle">Commander 2026 Bracket classification — how salty is your deck?</p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
                <div className="card">
                    <h2 className="card-title" style={{ marginBottom: '1rem' }}>Paste Commander Decklist</h2>
                    <textarea
                        className="input"
                        placeholder={"1 Sol Ring\n1 Mana Crypt\n1 Force of Will\n1 Thassa's Oracle\n96 Mountain"}
                        value={decklist}
                        onChange={(e) => setDecklist(e.target.value)}
                    />
                    <button
                        className="btn btn-primary"
                        onClick={analyze}
                        disabled={loading || !decklist.trim()}
                        style={{ marginTop: '1rem', width: '100%' }}
                    >
                        {loading ? '⏳ Analyzing...' : '🧂 Calculate Salt Score'}
                    </button>
                    {error && <p style={{ color: 'var(--accent-red)', marginTop: '0.5rem' }}>{error}</p>}
                </div>

                <div className="card">
                    <h2 className="card-title" style={{ marginBottom: '1rem' }}>Bracket Result</h2>
                    {!result ? (
                        <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '3rem 0' }}>
                            Paste a deck and click Analyze
                        </p>
                    ) : (
                        <>
                            {/* Bracket Badge */}
                            <div style={{
                                textAlign: 'center',
                                padding: '1.5rem',
                                background: `${bracketInfo?.color}11`,
                                borderRadius: 'var(--radius-lg)',
                                border: `2px solid ${bracketInfo?.color}44`,
                                marginBottom: '1.5rem',
                            }}>
                                <div style={{ fontSize: '3rem' }}>{bracketInfo?.emoji}</div>
                                <div style={{
                                    fontSize: '1.8rem',
                                    fontWeight: 800,
                                    color: bracketInfo?.color,
                                    fontFamily: 'var(--font-mono)',
                                }}>
                                    Bracket {result.bracket}
                                </div>
                                <div style={{ fontSize: '1rem', fontWeight: 600 }}>{bracketInfo?.label}</div>
                                <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
                                    {bracketInfo?.desc}
                                </div>
                            </div>

                            {/* Salt Score */}
                            <div className="stats-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                                <div className="stat-card">
                                    <div className="stat-value red" style={{ fontSize: '2rem' }}>{result.salt_score}</div>
                                    <div className="stat-label">Salt Score</div>
                                </div>
                                <div className="stat-card">
                                    <div className="stat-value gold" style={{ fontSize: '2rem' }}>{result.flagged_cards.length}</div>
                                    <div className="stat-label">Flagged Cards</div>
                                </div>
                            </div>

                            {/* Flagged Cards */}
                            {result.flagged_cards.length > 0 && (
                                <div style={{ marginTop: '1rem' }}>
                                    <h3 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
                                        Flagged Cards
                                    </h3>
                                    <div style={{ maxHeight: 200, overflowY: 'auto' }}>
                                        {result.flagged_cards.map((c, i) => (
                                            <div key={i} style={{
                                                display: 'flex',
                                                justifyContent: 'space-between',
                                                padding: '0.4rem 0.75rem',
                                                borderBottom: '1px solid var(--border)',
                                                fontSize: '0.85rem',
                                            }}>
                                                <span>{c.card}</span>
                                                <span className={`badge ${c.bracket === 4 ? 'loss' : c.bracket === 3 ? 'bronze' : 'silver'}`}>
                                                    B{c.bracket}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
