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
  HelpCircle,
  Bot,
  Info,
} from 'lucide-react';
import type { Cart, DecisionExplanation, Proposal } from '../types';
import { generateProposals, recordAction, fetchCart, getDecisionExplanation, refreshMandate } from '../api/client';

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
  const [explanations, setExplanations] = useState<Record<string, DecisionExplanation>>({});
  const [loadingExpl, setLoadingExpl] = useState<Record<string, boolean>>({});
  const [showExpl, setShowExpl] = useState<Record<string, boolean>>({});
  const [refreshingMandate, setRefreshingMandate] = useState(false);

  const handleRefreshAuthorization = async () => {
    setRefreshingMandate(true);
    try {
      const updatedCart = await refreshMandate(sessionId);
      onCartUpdate(updatedCart);
      if (onRefreshCart) onRefreshCart();
      const prop = await generateProposals(sessionId);
      setProposals(prev => [prop, ...prev.filter(p => p.id !== prop.id)]);
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to refresh authorization');
    } finally {
      setRefreshingMandate(false);
    }
  };

  const toggleExplanation = async (proposalId: string) => {
    const next = !showExpl[proposalId];
    setShowExpl(prev => ({ ...prev, [proposalId]: next }));
    if (next && !explanations[proposalId]) {
      setLoadingExpl(prev => ({ ...prev, [proposalId]: true }));
      try {
        const expl = await getDecisionExplanation(sessionId, proposalId);
        setExplanations(prev => ({ ...prev, [proposalId]: expl }));
      } catch (e) {
        console.error("Failed to load decision explanation", e);
      } finally {
        setLoadingExpl(prev => ({ ...prev, [proposalId]: false }));
      }
    }
  };

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

  const formatINR = (val: number | string) =>
    new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(Number(val) || 0);

  const gateLabel = (result: Proposal['gate_result']) => {
    switch (result) {
      case 'accepted':
        return { text: 'Gate Approved', cls: 'badge-success', icon: ShieldCheck };
      case 'partial':
        return { text: 'Partially Approved', cls: 'badge-warning', icon: ShieldAlert };
      case 'mandate_expired':
        return { text: 'Mandate Expired', cls: 'badge-warning', icon: AlertTriangle };
      case 'mandate_invalid':
        return { text: 'Mandate Invalid', cls: 'badge-danger', icon: ShieldAlert };
      case 'no_proposals':
        return { text: 'No Proposals', cls: 'badge-muted', icon: Info };
      case 'rejected':
      default:
        return { text: 'Gate Blocked', cls: 'badge-danger', icon: ShieldX };
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '1rem' }}>
      {/* Header action -- Agent Surface */}
      <div style={{
        padding: '1rem',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-lg)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Bot size={17} style={{ color: 'var(--accent)' }} />
            <h2 className="text-base font-semibold" style={{ margin: 0 }}>
              Autonomous Proposals
            </h2>
          </div>
          <span className="text-xs text-muted">Deterministic merchant policy enforcement</span>
        </div>

        <button
          id="generate-proposals-btn"
          className="btn btn-primary btn-sm"
          onClick={handleGenerate}
          disabled={loading || !sessionId}
        >
          {loading ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div className="spinner" style={{ width: 14, height: 14 }} />
              <span>Evaluating...</span>
            </span>
          ) : (
            <>
              <Sparkles size={14} />
              <span>Request Proposals</span>
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
          <div className="card card-agent" style={{
            textAlign: 'center', padding: '2.5rem 1rem',
            color: 'var(--text-muted)', fontSize: '0.875rem',
          }}>
            <Bot size={32} style={{ opacity: 0.25, margin: '0 auto 8px', display: 'block' }} />
            <div>No active proposals</div>
            <div style={{ fontSize: '0.8rem', marginTop: 4 }}>Click 'Request Proposals' to trigger the agent pipeline</div>
          </div>
        )}

        {proposals.map(proposal => {
          const isMandateExpired = proposal.gate_result === 'mandate_expired' ||
            proposal.rejected_items.some(r => r.reason === 'mandate_expired');

          const isMandateInvalid = proposal.gate_result === 'mandate_invalid' ||
            proposal.rejected_items.some(r => r.reason === 'mandate_invalid' || r.reason === 'mandate_missing');

          const isMandateFailure = isMandateExpired || isMandateInvalid;

          const hasNoProposals = !isMandateFailure && (
            proposal.gate_result === 'no_proposals' ||
            (proposal.accepted_items.length === 0 && proposal.rejected_items.length === 0)
          );

          const { text, cls, icon: GateIcon } = isMandateFailure
            ? (isMandateExpired
                ? { text: 'Mandate Expired', cls: 'badge-warning', icon: AlertTriangle }
                : { text: 'Mandate Invalid', cls: 'badge-danger', icon: ShieldAlert })
            : hasNoProposals
            ? { text: 'No Proposals Available', cls: 'badge-muted', icon: Info }
            : gateLabel(proposal.gate_result);

          const isPending = proposal.user_action === 'pending';
          const isReviewRequired = proposal.user_action === 'review_required';
          const isReviewed = proposal.user_action === 'reviewed';
          const isActionable = (isPending || isReviewed) && proposal.accepted_items.length > 0;
          const isActing = acting === proposal.id;
          const rejectedVisible = showRejected[proposal.id];
          const cfVisible = showCounterfactual[proposal.id];

          return (
            <div key={proposal.id} className="card card-agent slide-in" style={{ padding: '1.25rem' }}>
              {/* Proposal header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.875rem' }}>
                <div className="text-xs text-muted">
                  {new Date(proposal.created_at).toLocaleTimeString()}
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <span className={`badge ${cls}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <GateIcon size={12} />
                    {text}
                  </span>
                  {proposal.autonomy_tier && (
                    <span className="badge badge-muted" style={{ fontSize: '0.65rem' }}>
                      {proposal.autonomy_tier}
                    </span>
                  )}
                  {proposal.user_action !== 'pending' && proposal.user_action !== 'review_required' && (
                    <span className={`badge ${proposal.user_action === 'accepted' ? 'badge-success' : proposal.user_action === 'reviewed' ? 'badge-warning' : 'badge-muted'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      {proposal.user_action === 'accepted' ? (
                        <><Check size={11} /> Accepted</>
                      ) : proposal.user_action === 'reviewed' ? (
                        <><Check size={11} /> Confirmed</>
                      ) : (
                        <><X size={11} /> Declined</>
                      )}
                    </span>
                  )}
                </div>
              </div>

              {/* State when accepted items is 0 */}
              {proposal.accepted_items.length === 0 && (
                isMandateFailure ? (
                  /* Mandate Failure: Distinct Red/Amber Alert with Refresh Action */
                  <div style={{
                    padding: '0.875rem 1rem', borderRadius: 8,
                    background: isMandateExpired ? 'rgba(245, 158, 11, 0.08)' : 'rgba(239, 68, 68, 0.08)',
                    border: `1px solid ${isMandateExpired ? 'rgba(245, 158, 11, 0.25)' : 'rgba(239, 68, 68, 0.25)'}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '0.75rem',
                    flexWrap: 'wrap',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', flex: 1, minWidth: 200 }}>
                      <ShieldAlert size={16} style={{ color: isMandateExpired ? 'var(--warning)' : 'var(--danger)', flexShrink: 0 }} />
                      <div>
                        <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: isMandateExpired ? 'var(--warning)' : 'var(--danger)' }}>
                          {isMandateExpired ? 'Session authorization expired' : 'Spend mandate verification failed'}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 2 }}>
                          {isMandateExpired
                            ? 'The AP2 spend mandate validity window lapsed. Refresh authorization to continue with this cart.'
                            : 'Cryptographic mandate signature check failed or authorization token is missing.'}
                        </div>
                      </div>
                    </div>
                    {isMandateExpired && (
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={handleRefreshAuthorization}
                        disabled={refreshingMandate}
                        style={{ flexShrink: 0, fontSize: '0.75rem', padding: '0.35rem 0.75rem' }}
                      >
                        {refreshingMandate ? (
                          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            <div className="spinner" style={{ width: 11, height: 11 }} />
                            <span>Refreshing...</span>
                          </span>
                        ) : (
                          'Refresh Authorization'
                        )}
                      </button>
                    )}
                  </div>
                ) : hasNoProposals ? (
                  /* Neutral Informational State (NOT a gate rejection) */
                  <div style={{
                    padding: '0.85rem 1rem', borderRadius: 8,
                    background: 'var(--bg-input)', border: '1px solid var(--border)',
                    fontSize: '0.8125rem', color: 'var(--text-secondary)',
                    display: 'flex', alignItems: 'center', gap: '0.625rem',
                  }}>
                    <Info size={16} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                    <span>No cross-sell items available for this cart right now.</span>
                  </div>
                ) : (
                  /* Actual Gate Danger State (LLM proposed items, but gate rejected them) */
                  <div style={{
                    padding: '0.75rem 1rem', borderRadius: 8,
                    background: 'var(--danger-bg)', border: '1px solid var(--danger-border)',
                    fontSize: '0.8rem', color: 'var(--danger)',
                    display: 'flex', alignItems: 'center', gap: '0.5rem',
                  }}>
                    <ShieldX size={14} style={{ flexShrink: 0 }} />
                    <span>All proposed items were blocked by merchant policy gate</span>
                  </div>
                )
              )}

              {/* Accepted items list */}
              {proposal.accepted_items.map(item => (
                <div key={item.product_id} style={{
                  padding: '0.875rem', borderRadius: 8,
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
                  background: 'var(--warning-bg)', border: '1px solid var(--warning-border)',
                  borderRadius: 8, display: 'flex', flexDirection: 'column', gap: '0.5rem',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.75rem', color: 'var(--warning)' }}>
                    <AlertTriangle size={14} />
                    <span>Autonomy Tier ({proposal.autonomy_tier.toUpperCase()}): Explicit confirmation required.</span>
                  </div>
                  <button
                    className="btn btn-sm w-full"
                    style={{ background: 'var(--warning)', color: '#0F1420', fontWeight: 700 }}
                    onClick={() => handleAction(proposal, 'reviewed')}
                    disabled={isActing}
                  >
                    {isActing ? <div className="spinner" /> : 'Confirm & Authorize Proposal'}
                  </button>
                </div>
              )}

              {/* Action buttons (only when actionable with accepted items) */}
              {isActionable && (
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
                    <X size={14} /> Decline
                  </button>
                </div>
              )}

              {/* Explain Decision Button */}
              <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => toggleExplanation(proposal.id)}
                  style={{
                    fontSize: '0.75rem',
                    padding: '0.3rem 0.6rem',
                    borderRadius: 6,
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.35rem',
                  }}
                >
                  <HelpCircle size={13} style={{ color: 'var(--accent)' }} />
                  <span>Explain Decision</span>
                </button>
              </div>

              {/* Plain-Language Explanation Panel */}
              {showExpl[proposal.id] && (
                <div style={{
                  marginTop: '0.5rem',
                  padding: '0.85rem',
                  borderRadius: 8,
                  background: 'var(--bg-input)',
                  border: '1px solid var(--accent-border)',
                  fontSize: '0.8125rem',
                  lineHeight: 1.5,
                }}>
                  {loadingExpl[proposal.id] ? (
                    <div className="flex items-center gap-2 text-muted">
                      <div className="spinner" style={{ width: 14, height: 14 }} />
                      <span>Loading decision trace...</span>
                    </div>
                  ) : explanations[proposal.id] ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      <div style={{ fontWeight: 600, color: 'var(--accent-light)', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                        <span>Explainable Policy Trace</span>
                      </div>
                      <p style={{ margin: 0, color: 'var(--text-secondary)' }}>
                        {explanations[proposal.id].explanation}
                      </p>
                      {explanations[proposal.id].factors && explanations[proposal.id].factors.length > 0 && (
                        <div style={{ marginTop: '0.35rem', display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                          {explanations[proposal.id].factors.map((f, idx) => (
                            <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: '0.4rem', fontSize: '0.75rem' }}>
                              <span style={{ color: f.passed ? 'var(--success)' : 'var(--danger)', marginTop: 2, display: 'inline-flex' }}>
                                {f.passed ? <Check size={13} strokeWidth={2.5} /> : <X size={13} strokeWidth={2.5} />}
                              </span>
                              <div>
                                <strong style={{ color: 'var(--text-primary)' }}>{f.title}:</strong>{' '}
                                <span className="text-muted">{f.detail}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-danger" style={{ fontSize: '0.75rem' }}>
                      Unable to retrieve decision explanation.
                    </div>
                  )}
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
                        ? 'var(--danger-bg)'
                        : 'var(--accent-bg)',
                      color: proposal.counterfactual.divergence_detected
                        ? 'var(--danger)'
                        : 'var(--accent-light)',
                      border: '1px solid',
                      borderColor: proposal.counterfactual.divergence_detected
                        ? 'var(--danger-border)'
                        : 'var(--accent-border)',
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
                        {hasNoProposals ? 'Agent proposed 0 items; nothing to evaluate.' : proposal.counterfactual.summary}
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                        <div style={{ padding: '0.5rem', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                          <div style={{ fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4 }}>Raw LLM Proposal</div>
                          {proposal.counterfactual.llm_proposed_items.length === 0 ? (
                            <div className="text-xs text-muted" style={{ fontStyle: 'italic' }}>0 items proposed (no candidates)</div>
                          ) : (
                            proposal.counterfactual.llm_proposed_items.map((item, idx) => (
                              <div key={idx} style={{ marginBottom: 4, paddingBottom: 4, borderBottom: '1px dashed var(--border)' }}>
                                <div style={{ fontWeight: 500 }}>{item.product_name}</div>
                                <div className="text-xs text-muted">Discount: {Number(item.discount_pct)}%</div>
                              </div>
                            ))
                          )}
                        </div>
                        <div style={{ padding: '0.5rem', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                          <div style={{ fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4 }}>Gate Approved</div>
                          {proposal.accepted_items.length === 0 ? (
                            <div className="text-xs text-muted" style={{ fontStyle: 'italic' }}>
                              {hasNoProposals ? 'Nothing to evaluate' : '0 items allowed'}
                            </div>
                          ) : (
                            proposal.accepted_items.map((item, idx) => (
                              <div key={idx} style={{ marginBottom: 4, paddingBottom: 4, borderBottom: '1px dashed var(--border)' }}>
                                <div style={{ fontWeight: 500 }}>{item.product_name}</div>
                                <div className="text-xs" style={{ color: 'var(--success)' }}>Discount: {Number(item.discount_pct)}%</div>
                              </div>
                            ))
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Rejected items collapsible (only when gate actually rejected items) */}
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