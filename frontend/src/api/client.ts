/** Typed API client -- all requests go through /api (proxied by Vite). */
import axios from 'axios';
import type { AuditReplay, Cart, CheckoutResult, DecisionExplanation, Product, Proposal } from '../types';

const api = axios.create({ baseURL: '/api' });

// Catalog
export const fetchCatalog = (): Promise<Product[]> =>
  api.get<Product[]>('/catalog').then(r => r.data);

// Cart
export const createCart = (): Promise<Cart> =>
  api.post<Cart>('/cart').then(r => r.data);

export const fetchCart = (sessionId: string): Promise<Cart> =>
  api.get<Cart>(`/cart/${sessionId}`).then(r => r.data);

export const addToCart = (sessionId: string, productId: number, quantity = 1): Promise<Cart> =>
  api.post<Cart>(`/cart/${sessionId}/items`, { product_id: productId, quantity }).then(r => r.data);

export const removeFromCart = (sessionId: string, productId: number): Promise<Cart> =>
  api.delete<Cart>(`/cart/${sessionId}/items/${productId}`).then(r => r.data);

export const updateCartItemQuantity = async (
  sessionId: string,
  productId: number,
  newQuantity: number
): Promise<Cart> => {
  if (newQuantity <= 0) {
    return removeFromCart(sessionId, productId);
  }
  return api
    .patch<Cart>(`/cart/${sessionId}/items/${productId}`, { quantity: newQuantity })
    .then(r => r.data)
    .catch(async err => {
      // Fallback in case PATCH is unavailable: delete and re-add
      if (err?.response?.status === 404 || err?.response?.status === 405) {
        await removeFromCart(sessionId, productId);
        return addToCart(sessionId, productId, newQuantity);
      }
      throw err;
    });
};

export const refreshMandate = (sessionId: string): Promise<Cart> =>
  api.post<Cart>(`/cart/${sessionId}/mandate/refresh`).then(r => r.data);

// Proposals
export const generateProposals = (sessionId: string): Promise<Proposal> =>
  api.post<Proposal>(`/proposals/${sessionId}`).then(r => r.data);

export const recordAction = (
  sessionId: string,
  proposalId: string,
  action: 'accepted' | 'declined' | 'reviewed'
): Promise<Proposal> =>
  api
    .post<Proposal>(`/proposals/${sessionId}/${proposalId}/action`, { action })
    .then(r => r.data);

// Checkout
export const createCheckout = (sessionId: string): Promise<CheckoutResult> =>
  api.post<CheckoutResult>(`/checkout/${sessionId}`).then(r => r.data);

// Audit & Replay
export const getAuditReplay = (sessionId: string): Promise<AuditReplay> =>
  api.get<AuditReplay>(`/audit/${sessionId}/replay`).then(r => r.data);

export const getDecisionExplanation = (
  sessionId: string,
  proposalId: string
): Promise<DecisionExplanation> =>
  api
    .get<DecisionExplanation>(`/audit/${sessionId}/explain/${proposalId}`)
    .then(r => r.data);
