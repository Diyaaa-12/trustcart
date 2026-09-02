/** Typed API client -- all requests go through /api (proxied by Vite). */
import axios from 'axios';
import type { AuditReplay, Cart, CheckoutResult, Product, Proposal } from '../types';

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
