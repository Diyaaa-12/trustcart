import { useState } from 'react';
import { Sparkles, Shield, ShieldX, ChevronDown, ChevronUp, Check, X, AlertTriangle } from 'lucide-react';
import type { Cart, Proposal } from '../types';
import { fetchCart, generateProposals, recordAction } from '../api/client';

interface Props {
  cart: Cart | null;
  sessionId: string;
  onCartUpdate: (cart: Cart) => void;
}

const formatINR = (n: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n);

export default function ProposalPanel({ cart, sessionId, onCartUpdate }: Props) {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showRejected, setShowRejected] = useState<Record<string, boolean>>({});

  const canPropose = (cart?.item_count ?? 0) > 0;
  const currentTier = cart?.autonomy_tier ?? 'high';

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const proposal = await generateProposals(sessionId);
      setProposals(prev => [proposal, ...prev]);

      // Refresh cart to update live trust score and budget
      try {
        const updatedCart = await fetchCart(sessionId);
        onCartUpdate(updatedCart);
      } catch (err) {
        console.error('Failed to refresh cart after proposals:', err);
      }
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(typeof msg === 'string' ? msg : 'Failed to get suggestions. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (proposal: Proposal, action: 'accepted' | 'declined' | 'reviewed') => {
    setActing(proposal.id);
    try {
      const updated = await recordAction(sessionId, proposal.id, action);
      setProposals(prev => prev.map(p => p.id === proposal.id ? updated : p));

      // Refresh cart if accepted
      if (action === 'accepted') {
        try {
          const updatedCart = await fetchCart(sessionId);
          onCartUpdate(updatedCart);
        } catch (err) {
          console.error('Failed to refresh cart after action:', err);
        }
      }
    } catch (err) {
      console.error('Failed to record action:', err);
    } finally {
      setActing(null);
    }
  };

  const toggleRejected = (id: string) =>
    setShowRejected(prev => ({ ...prev, [id]: !prev[id] }));

  const gateLabel = (result: Proposal['gate_result']) => {
    if (result === 'accepted') return { text: 'All Passed', cls: 'badge-success' };
    if (result === 'rejected') return { text: 'All Blocked', cls: 'badge-danger' };
    return { text: 'Partial', cls: 'badge-warning' };
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '1rem' }}>

      {/* Header */}
      <div className="card" style={{ padding: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem', marginBottom: '1.25rem' }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: 'rgba(99,102,241,0.15)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0,
          }}>
            <Sparkles size={18} color="var(--accent-light)" />
          </div>
          <div>
            <div className="text-base font-semi">AI Suggestions</div>
            <div className="text-xs text-muted" style={{ marginTop: 2 }}>
              Tier: <strong style={{ textTransform: 'uppercase', color: currentTier === 'high' ? 'var(--success)' : currentTier === 'medium' ? 'var(--warning)' : 'var(--danger)' }}>{currentTier}</strong> • Every proposal verified by policy gate
            </div>
          </div>
        </div>

        {/* Policy gate & Trust explanation */}
        <div style={{
          display: 'flex', alignItems: 'flex-start', gap: '0.625rem',
          padding: '0.875rem', borderRadius: 10,
          background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.2)',
          marginBottom: '1rem',
        }}>
          <Shield size={15} color="var(--success)" style={{ marginTop: 1, flexShrink: 0 }} />
          <div className="text-xs" style={{ color: 'var(--success)', lineHeight: 1.5 }}>
            <strong>Trust-Adaptive Autonomy active</strong> — Gate enforces hard caps; session trust score determines autonomy tier.
            {currentTier === 'low' && ' (Volume throttled to 1 item)'}
            {currentTier !== 'high' && ' (User confirmation step required)'}
          </div>
        </div>

        <button
          id="get-suggestions-btn"
          className="btn btn-primary w-full btn-lg"
          onClick={handleGenerate}
          disabled={loading || !canPropose}
        >
          {loading ? (
            <><div className="spinner" style={{ borderTopColor: '#fff' }} /> Thinking...</>
          ) : (
            <><Sparkles size={16} /> Get AI Suggestions</>
          )}
        </button>
        {!canPropose && (
          <div className="text-xs text-muted" style={{ marginTop: '0.5rem', textAlign: 'center' }}>
            Add items to your cart first
          </div>
        )}
        {error && (
          <div style={{
            marginTop: '0.75rem', padding: '0.625rem 0.875rem',
            background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
            borderRadius: 8, fontSize: '0.8rem', color: 'var(--danger)',
          }}>
            {error}
          </div>
        )}
      </div>

      {/* Proposals feed */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', overflowY: 'auto' }}>
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
                      {proposal.user_action === 'accepted' ? '✓ Accepted' : proposal.user_action === 'reviewed' ? '👁 Confirmed' : '✕ Declined'}
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
