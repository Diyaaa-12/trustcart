import { useState } from 'react';
import {
  Sparkles,
  Check,
  X,
  ChevronDown,
  ChevronUp,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  AlertTriangle,
  Split,
} from 'lucide-react';
import type { Cart, Proposal } from '../types';
import { generateProposals, recordAction, fetchCart } from '../api/client';

interface Props {
  sessionId: string;
  onCartUpdate: (cart: Cart) => void;
  onRefreshCart?: () => void;
}

export function ProposalPanel({ sessionId, onCartUpdate, onRefreshCart }: Props) {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showRejected, setShowRejected] = useState<Record<string, boolean>>({});
  const [showCounterfactual, setShowCounterfactual] = useState<Record<string, boolean>>({});

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const p = await generateProposals(sessionId);
      setProposals(prev => [p, ...prev]);
      if (onRefreshCart) {
        onRefreshCart();
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to generate recommendations');
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (
    proposal: Proposal,
    action: 'accepted' | 'declined' | 'reviewed'
  ) => {
    setActing(proposal.id);
    setError(null);
    try {
      const updated = await recordAction(sessionId, proposal.id, action);
      setProposals(prev =>
        prev.map(p => (p.id === proposal.id ? { ...p, ...updated } : p))
      );

      if (action === 'accepted') {
        // fetchCart statically imported
        const updatedCart = await fetchCart(sessionId);
        onCartUpdate(updatedCart);
      } else if (action === 'reviewed') {
        if (onRefreshCart) {
          onRefreshCart();
        }
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Failed to record ${action}`);
    } finally {
      setActing(null);
    }
  };

  const toggleRejected = (id: string) =>
    setShowRejected(prev => ({ ...prev, [id]: !prev[id] }));

  const toggleCounterfactual = (id: string) =>
    setShowCounterfactual(prev => ({ ...prev, [id]: !prev[id] }));

  const formatINR = (val: number) =>
    `₹${Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;

  const gateLabel = (result: Proposal['gate_result']) => {
    switch (result) {
      case 'accepted':
        return { text: 'All Passed', cls: 'badge-success', icon: ShieldCheck };
      case 'partial':
        return { text: 'Partially Passed', cls: 'badge-warning', icon: ShieldAlert };
      case 'rejected':
        return { text: 'Blocked', cls: 'badge-danger', icon: ShieldX };
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '1rem' }}>
      {/* Header action */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h2 className="text-base font-semibold" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Sparkles size={16} style={{ color: 'var(--primary)' }} />
            AI Recommendations
          </h2>
          <span className="text-xs text-muted">Filtered by deterministic policy gate</span>
        </div>

        <button
          id="generate-proposals-btn"
          className="btn btn-primary btn-sm"
          onClick={handleGenerate}
          disabled={loading || !sessionId}
        >
          {loading ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div className="spinner" />
              <span>Analysing cart...</span>
            </span>
          ) : (
            <>
              <Sparkles size={14} />
              <span>Get Suggestions</span>
            </>
          )}
        </button>
      </div>

      {error && (
        <div style={{
          padding: '0.75rem 1rem', borderRadius: 8,
          background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
          color: 'var(--danger)', fontSize: '0.8125rem',
        }}>
          {error}
        </div>
      )}

      {/* Proposal list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.875rem', overflowY: 'auto', flex: 1 }}>
        {proposals.length === 0 && !loading && (
          <div style={{
            textAlign: 'center', padding: '2rem 1rem',
            color: 'var(--text-muted)', fontSize: '0.875rem',
          }}>
            <Sparkles size={36} style={{ opacity: 0.2, marginBottom: 8 }} />
            <div>No suggestions yet</div>
            <div style={{ fontSize: '0.8rem', marginTop: 4 }}>Click the button above to get personalised recommendations</div>
          </div>
        )}

        {proposals.map(proposal => {
          const { text, cls } = gateLabel(proposal.gate_result);
          const isPending = proposal.user_action === 'pending';
          const isReviewRequired = proposal.user_action === 'review_required';
          const isReviewed = proposal.user_action === 'reviewed';
          const isActionable = isPending || isReviewed;
          const isActing = acting === proposal.id;
          const rejectedVisible = showRejected[proposal.id];
          const cfVisible = showCounterfactual[proposal.id];

          return (
            <div key={proposal.id} className="card slide-in" style={{ padding: '1.25rem' }}>
              {/* Proposal header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.875rem' }}>
                <div className="text-xs text-muted">
                  {new Date(proposal.created_at).toLocaleTimeString()}
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <span className={`badge ${cls}`}>Gate: {text}</span>
                  {proposal.autonomy_tier && (
                    <span className="badge badge-muted" style={{ fontSize: '0.65rem', textTransform: 'uppercase' }}>
                      {proposal.autonomy_tier}
                    </span>
                  )}
                  {proposal.user_action !== 'pending' && proposal.user_action !== 'review_required' && (
                    <span className={`badge ${proposal.user_action === 'accepted' ? 'badge-success' : proposal.user_action === 'reviewed' ? 'badge-warning' : 'badge-muted'}`}>
                      {proposal.user_action === 'accepted' ? '✓ Accepted' : proposal.user_action === 'reviewed' ? '✓ Confirmed' : '✗ Declined'}
                    </span>
                  )}
                </div>
              </div>

              {/* Accepted items */}
              {proposal.accepted_items.length === 0 && (
                <div style={{
                  padding: '0.75rem', borderRadius: 8,
                  background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
                  fontSize: '0.8rem', color: 'var(--danger)',
                  display: 'flex', alignItems: 'center', gap: '0.5rem',
                }}>
                  <ShieldX size={14} />
                  All proposals were blocked by the policy gate
                </div>
              )}

              {proposal.accepted_items.map(item => (
                <div key={item.product_id} style={{
                  padding: '0.875rem', borderRadius: 10,
                  background: 'var(--bg-input)', border: '1px solid var(--border)',
                  marginBottom: '0.5rem',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.5rem' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="text-sm font-medium" style={{ marginBottom: 2 }}>{item.product_name}</div>
                      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                        <span className="price-old text-xs">{formatINR(item.original_price)}</span>
                        <span className="price-discounted text-sm">{formatINR(item.discounted_price)}</span>
                        <span className="badge badge-success">{Number(item.discount_pct).toFixed(0)}% off</span>
                      </div>
                    </div>
                  </div>
                </div>
              ))}

              {/* Review required banner for medium/low tier */}
              {proposal.accepted_items.length > 0 && isReviewRequired && (
                <div style={{
                  marginTop: '0.75rem', padding: '0.75rem',
                  background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)',
                  borderRadius: 8, display: 'flex', flexDirection: 'column', gap: '0.5rem',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.75rem', color: 'var(--warning)' }}>
                    <AlertTriangle size={14} />
                    <span>Autonomy Tier ({proposal.autonomy_tier.toUpperCase()}): Explicit confirmation required.</span>
                  </div>
                  <button
                    className="btn btn-sm w-full"
                    style={{ background: 'var(--warning)', color: '#000', fontWeight: 600 }}
                    onClick={() => handleAction(proposal, 'reviewed')}
                    disabled={isActing}
                  >
                    {isActing ? <div className="spinner" /> : 'Confirm & Review Proposal'}
                  </button>
                </div>
              )}

              {/* Action buttons (only when actionable: pending or reviewed) */}
              {proposal.accepted_items.length > 0 && isActionable && (
                <div style={{ display: 'flex', gap: '0.625rem', marginTop: '0.75rem' }}>
                  <button
                    id={`accept-proposal-${proposal.id.slice(0, 8)}`}
                    className="btn btn-success"
                    style={{ flex: 1 }}
                    onClick={() => handleAction(proposal, 'accepted')}
                    disabled={isActing}
                  >
                    {isActing ? <div className="spinner" style={{ borderTopColor: '#fff' }} /> : <><Check size={14} /> Add to Cart</>}
                  </button>
                  <button
                    id={`decline-proposal-${proposal.id.slice(0, 8)}`}
                    className="btn btn-danger"
                    style={{ flex: 1 }}
                    onClick={() => handleAction(proposal, 'declined')}
                    disabled={isActing}
                  >
                    <X size={14} /> No thanks
                  </button>
                </div>
              )}

              {/* Counterfactual comparison collapsible */}
              {proposal.counterfactual && (
                <div style={{ marginTop: '0.75rem' }}>
                  <button
                    className="btn btn-ghost btn-sm w-full"
                    onClick={() => toggleCounterfactual(proposal.id)}
                    style={{
                      justifyContent: 'space-between',
                      fontSize: '0.75rem',
                      background: proposal.counterfactual.divergence_detected
                        ? 'rgba(239, 68, 68, 0.06)'
                        : 'rgba(59, 130, 246, 0.06)',
                      color: proposal.counterfactual.divergence_detected
                        ? 'var(--danger)'
                        : 'var(--primary)',
                      border: '1px solid',
                      borderColor: proposal.counterfactual.divergence_detected
                        ? 'var(--danger-border)'
                        : 'rgba(59, 130, 246, 0.25)',
                      borderRadius: 8,
                      padding: '0.5rem 0.75rem',
                    }}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600 }}>
                      <Split size={13} />
                      Proposed vs Allowed (Counterfactual)
                      {proposal.counterfactual.divergence_detected && (
                        <span className="badge badge-danger" style={{ fontSize: '0.65rem' }}>Divergence</span>
                      )}
                    </span>
                    {cfVisible ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>

                  {cfVisible && (
                    <div style={{
                      marginTop: '0.5rem',
                      padding: '0.75rem',
                      borderRadius: 8,
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border)',
                      fontSize: '0.75rem',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.5rem',
                    }}>
                      <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
                        {proposal.counterfactual.summary}
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                        <div style={{ padding: '0.5rem', borderRadius: 6, background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)' }}>
                          <div style={{ fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4 }}>Raw LLM Proposal</div>
                          {proposal.counterfactual.llm_proposed_items.map((item, idx) => (
                            <div key={idx} style={{ marginBottom: 4, paddingBottom: 4, borderBottom: '1px dashed var(--border)' }}>
                              <div style={{ fontWeight: 500 }}>{item.product_name}</div>
                              <div className="text-xs text-muted">Discount: {item.discount_pct}%</div>
                            </div>
                          ))}
                        </div>
                        <div style={{ padding: '0.5rem', borderRadius: 6, background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)' }}>
                          <div style={{ fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4 }}>Gate Approved</div>
                          {proposal.accepted_items.length === 0 ? (
                            <div className="text-xs text-muted" style={{ fontStyle: 'italic' }}>0 items allowed</div>
                          ) : (
                            proposal.accepted_items.map((item, idx) => (
                              <div key={idx} style={{ marginBottom: 4, paddingBottom: 4, borderBottom: '1px dashed var(--border)' }}>
                                <div style={{ fontWeight: 500 }}>{item.product_name}</div>
                                <div className="text-xs" style={{ color: 'var(--success)' }}>Discount: {item.discount_pct}%</div>
                              </div>
                            ))
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Rejected items collapsible */}
              {proposal.rejected_items.length > 0 && (
                <div style={{ marginTop: '0.75rem' }}>
                  <button
                    className="btn btn-ghost btn-sm w-full"
                    onClick={() => toggleRejected(proposal.id)}
                    style={{ justifyContent: 'space-between', color: 'var(--text-muted)', fontSize: '0.75rem' }}
                  >
                    <span>{proposal.rejected_items.length} item{proposal.rejected_items.length > 1 ? 's' : ''} blocked by policy</span>
                    {rejectedVisible ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>

                  {rejectedVisible && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem', marginTop: '0.5rem' }}>
                      {proposal.rejected_items.map((rej, idx) => (
                        <div key={idx} style={{
                          padding: '0.625rem 0.75rem', borderRadius: 8,
                          background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
                          fontSize: '0.75rem', color: 'var(--danger)',
                        }}>
                          <div style={{ fontWeight: 600, marginBottom: 2 }}>{rej.reason}</div>
                          <div style={{ opacity: 0.85 }}>{rej.detail}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
