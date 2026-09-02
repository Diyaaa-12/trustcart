/** Shared TypeScript types mirroring the backend schemas. */

export interface Product {
  id: number;
  name: string;
  description: string;
  price: number;
  category: string;
  stock: number;
}

export interface CartItem {
  id: number;
  product_id: number;
  product_name: string;
  category: string;
  quantity: number;
  unit_price: number;
  line_total: number;
}

export interface SpendMandate {
  fingerprint: string;
  max_cumulative_discount_pct: number;
  max_items_per_proposal: number;
  expires_at: string;
  status: 'active' | 'expired';
}

export interface Cart {
  session_id: string;
  items: CartItem[];
  subtotal: number;
  discount_budget_used_pct: number;
  discount_budget_remaining_pct: number;
  item_count: number;
  trust_score: number;
  autonomy_tier: 'high' | 'medium' | 'low';
  mandate?: SpendMandate;
}

export interface AcceptedItem {
  product_id: number;
  product_name: string;
  original_price: number;
  discount_pct: number;
  discounted_price: number;
}

export interface RejectedItem {
  product_id: number;
  proposed_discount_pct: number;
  reason: string;
  detail: string;
}

export interface LLMProposedItem {
  product_id: number;
  product_name: string;
  discount_pct: number;
}

export interface CounterfactualComparison {
  llm_proposed_items: LLMProposedItem[];
  gate_accepted_items: AcceptedItem[];
  gate_rejected_items: RejectedItem[];
  divergence_detected: boolean;
  summary: string;
}

export interface Proposal {
  id: string;
  session_id: string;
  gate_result: 'accepted' | 'rejected' | 'partial';
  accepted_items: AcceptedItem[];
  rejected_items: RejectedItem[];
  user_action: 'pending' | 'review_required' | 'reviewed' | 'accepted' | 'declined';
  autonomy_tier: 'high' | 'medium' | 'low';
  requires_review: boolean;
  counterfactual?: CounterfactualComparison;
  created_at: string;
}

export interface CheckoutResult {
  order_id: string;
  amount_paise: number;
  currency: string;
  razorpay_key_id: string;
  session_id: string;
  mock_mode: boolean;
}

export interface ReplayStep {
  step_number: number;
  timestamp: string;
  category: 'cart' | 'agent' | 'gate' | 'trust' | 'user' | 'checkout' | 'mandate' | 'system';
  title: string;
  summary: string;
  status: 'info' | 'success' | 'warning' | 'danger';
  details: Record<string, unknown>;
}

export interface AuditReplay {
  session_id: string;
  total_steps: number;
  current_trust_score: number;
  current_autonomy_tier: string;
  steps: ReplayStep[];
}
