import { useState, useEffect } from 'react';
import {
  X,
  RefreshCw,
  Clock,
  Shield,
  Bot,
  ShoppingCart,
  CheckCircle,
  AlertTriangle,
  CreditCard,
  Key,
  HelpCircle,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import type { AuditReplay, DecisionExplanation, ReplayStep } from '../types';
import { getAuditReplay, getDecisionExplanation } from '../api/client';

interface Props {
  sessionId: string;
  isOpen: boolean;
  onClose: () => void;
}

export function AuditReplayModal({ sessionId, isOpen, onClose }: Props) {
  const [replay, setReplay] = useState<AuditReplay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [explanations, setExplanations] = useState<Record<string, DecisionExplanation>>({});
  const [loadingExpl, setLoadingExpl] = useState<Record<string, boolean>>({});

  const fetchExpl = async (proposalId: string) => {
    if (explanations[proposalId]) return;
    setLoadingExpl(prev => ({ ...prev, [proposalId]: true }));
    try {
      const res = await getDecisionExplanation(sessionId, proposalId);
      setExplanations(prev => ({ ...prev, [proposalId]: res }));
    } catch (err) {
      console.error('Failed to fetch explanation', err);
    } finally {
      setLoadingExpl(prev => ({ ...prev, [proposalId]: false }));
    }
  };

  const loadReplay = async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getAuditReplay(sessionId);
      setReplay(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load audit replay');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      loadReplay();
    }
  }, [isOpen, sessionId]);

  if (!isOpen) return null;

  const toggleExpand = (stepNum: number) => {
    setExpandedStep(prev => (prev === stepNum ? null : stepNum));
  };

  const getCategoryIcon = (category: string) => {
    switch (category) {
      case 'cart':
        return <ShoppingCart size={16} className="text-blue-400" />;
      case 'agent':
        return <Bot size={16} className="text-purple-400" />;
      case 'gate':
        return <Shield size={16} className="text-amber-400" />;
      case 'trust':
        return <CheckCircle size={16} className="text-emerald-400" />;
      case 'mandate':
        return <Key size={16} className="text-indigo-400" />;
      case 'checkout':
        return <CreditCard size={16} className="text-cyan-400" />;
      default:
        return <Clock size={16} className="text-gray-400" />;
    }
  };

  const getStatusBadge = (status: ReplayStep['status']) => {
    switch (status) {
      case 'success':
        return 'badge-success';
      case 'danger':
        return 'badge-danger';
      case 'warning':
        return 'badge-warning';
      default:
        return 'badge-muted';
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 50,
        background: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1.5rem',
      }}
    >
      <div
        className="card"
        style={{
          width: '100%',
          maxWidth: '780px',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '1.25rem 1.5rem',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
              <Clock size={20} style={{ color: 'var(--primary)' }} />
              <h2 className="text-lg font-semibold" style={{ margin: 0 }}>
                Session Audit Replay
              </h2>
            </div>
            <div className="text-xs text-muted" style={{ marginTop: '0.25rem' }}>
              Chronological, human-readable session reconstruction
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={loadReplay}
              disabled={loading}
              title="Refresh timeline"
            >
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            </button>
            <button className="btn btn-ghost btn-sm" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Overview Bar */}
        {replay && (
          <div
            style={{
              padding: '0.75rem 1.5rem',
              background: 'var(--bg-input)',
              borderBottom: '1px solid var(--border)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: '0.8rem',
            }}
          >
            <div style={{ display: 'flex', gap: '1.5rem' }}>
              <div>
                <span className="text-muted">Session: </span>
                <code style={{ fontSize: '0.75rem' }}>{replay.session_id.slice(0, 8)}...</code>
              </div>
              <div>
                <span className="text-muted">Total Steps: </span>
                <strong>{replay.total_steps}</strong>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <div>
                <span className="text-muted">Current Trust: </span>
                <strong>{replay.current_trust_score.toFixed(0)}/100</strong>
              </div>
              <span
                className={`badge ${
                  replay.current_autonomy_tier === 'high'
                    ? 'badge-success'
                    : replay.current_autonomy_tier === 'medium'
                    ? 'badge-warning'
                    : 'badge-danger'
                }`}
                style={{ textTransform: 'uppercase', fontSize: '0.7rem' }}
              >
                {replay.current_autonomy_tier} Tier
              </span>
            </div>
          </div>
        )}

        {/* Content list */}
        <div style={{ overflowY: 'auto', padding: '1.25rem 1.5rem', flex: 1 }}>
          {loading && !replay && (
            <div style={{ textAlign: 'center', padding: '3rem' }}>
              <div className="spinner" style={{ margin: '0 auto 1rem' }} />
              <div className="text-sm text-muted">Reconstructing session timeline...</div>
            </div>
          )}

          {error && (
            <div
              style={{
                padding: '1rem',
                borderRadius: 8,
                background: 'var(--danger-bg)',
                border: '1px solid var(--danger-border)',
                color: 'var(--danger)',
                fontSize: '0.875rem',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
              }}
            >
              <AlertTriangle size={16} />
              <span>{error}</span>
            </div>
          )}

          {replay && replay.steps.length === 0 && (
            <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
              No audit events recorded for this session yet.
            </div>
          )}

          {replay && replay.steps.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {replay.steps.map(step => {
                const isExpanded = expandedStep === step.step_number;
                return (
                  <div
                    key={step.step_number}
                    style={{
                      borderRadius: 10,
                      background: 'var(--bg-card)',
                      border: '1px solid var(--border)',
                      padding: '0.875rem 1rem',
                      transition: 'border-color 0.2s',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        justifyContent: 'space-between',
                        gap: '0.75rem',
                        cursor: 'pointer',
                      }}
                      onClick={() => toggleExpand(step.step_number)}
                    >
                      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                        <div
                          style={{
                            padding: '0.375rem',
                            borderRadius: 6,
                            background: 'var(--bg-input)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            marginTop: 2,
                          }}
                        >
                          {getCategoryIcon(step.category)}
                        </div>

                        <div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                              #{step.step_number}
                            </span>
                            <span className="font-semibold text-sm">{step.title}</span>
                            <span className={`badge ${getStatusBadge(step.status)}`} style={{ fontSize: '0.65rem' }}>
                              {step.category}
                            </span>
                          </div>
                          <div style={{ fontSize: '0.8125rem', color: 'var(--text)', marginTop: '0.25rem' }}>
                            {step.summary}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span className="text-xs text-muted">
                          {new Date(step.timestamp).toLocaleTimeString()}
                        </span>
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </div>
                    </div>

                    {/* Collapsible raw details */}
                    {isExpanded && (
                      <div
                        style={{
                          marginTop: '0.75rem',
                          padding: '0.75rem',
                          borderRadius: 8,
                          background: 'var(--bg-input)',
                          border: '1px solid var(--border)',
                          fontSize: '0.75rem',
                        }}
                      >
                        {typeof step.details?.proposal_id === 'string' && (
                          <div style={{ marginBottom: '0.75rem' }}>
                            {!explanations[step.details.proposal_id] ? (
                              <button
                                className="btn btn-ghost btn-sm"
                                onClick={() => fetchExpl(step.details.proposal_id as string)}
                                disabled={loadingExpl[step.details.proposal_id]}
                                style={{
                                  fontSize: '0.75rem',
                                  padding: '0.25rem 0.5rem',
                                  border: '1px solid var(--border)',
                                  borderRadius: 6,
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: '0.35rem',
                                }}
                              >
                                <HelpCircle size={13} color="var(--accent-light)" />
                                <span>{loadingExpl[step.details.proposal_id] ? 'Loading explanation...' : 'Explain Policy Decision'}</span>
                              </button>
                            ) : (
                              <div style={{
                                padding: '0.65rem 0.85rem',
                                borderRadius: 6,
                                background: 'rgba(99,102,241,0.08)',
                                border: '1px solid rgba(99,102,241,0.25)',
                                fontSize: '0.78rem',
                                lineHeight: 1.45,
                              }}>
                                <div style={{ fontWeight: 600, color: 'var(--accent-light)', marginBottom: 2 }}>
                                  {explanations[step.details.proposal_id].summary}
                                </div>
                                <div style={{ color: 'var(--text-secondary)' }}>
                                  {explanations[step.details.proposal_id].explanation}
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                        <div style={{ fontWeight: 600, color: 'var(--text-muted)', marginBottom: '0.25rem' }}>
                          Event Payload Data
                        </div>
                        <pre
                          style={{
                            margin: 0,
                            padding: '0.5rem',
                            borderRadius: 6,
                            background: 'rgba(0,0,0,0.3)',
                            overflowX: 'auto',
                            fontSize: '0.7rem',
                            color: '#a7f3d0',
                          }}
                        >
                          {JSON.stringify(step.details, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '1rem 1.5rem',
            borderTop: '1px solid var(--border)',
            display: 'flex',
            justifyContent: 'flex-end',
          }}
        >
          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            Close Replay
          </button>
        </div>
      </div>
    </div>
  );
}
