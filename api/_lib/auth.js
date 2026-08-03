import { createHmac, timingSafeEqual as cryptoTimingSafeEqual } from 'node:crypto';

const COOKIE_NAME = 'fdc_auth';

/**
 * Build the signed cookie value: `${exp}.${hmacHex}`
 * exp = unix seconds now+7d (caller can pass a custom exp for testing).
 */
export function signCookie(secret, exp) {
  const expiry = exp ?? Math.floor(Date.now() / 1000) + 7 * 24 * 60 * 60;
  const hmac = createHmac('sha256', secret).update(String(expiry)).digest('hex');
  return `${expiry}.${hmac}`;
}

/**
 * Constant-time string compare (equal length required, else false).
 */
export function timingSafeEqual(a, b) {
  const bufA = Buffer.from(String(a));
  const bufB = Buffer.from(String(b));
  if (bufA.length !== bufB.length) return false;
  return cryptoTimingSafeEqual(bufA, bufB);
}

/**
 * Parse the fdc_auth cookie value out of a raw Cookie header string.
 */
export function parseCookie(cookieHeader) {
  if (!cookieHeader) return null;
  const parts = cookieHeader.split(';');
  for (const part of parts) {
    const idx = part.indexOf('=');
    if (idx === -1) continue;
    const name = part.slice(0, idx).trim();
    if (name === COOKIE_NAME) {
      return part.slice(idx + 1).trim();
    }
  }
  return null;
}

/**
 * Verify a raw Cookie header against secret: checks HMAC and expiry.
 * Returns boolean.
 */
export function verifyCookie(cookieHeader, secret) {
  const value = parseCookie(cookieHeader);
  if (!value) return false;

  const dotIdx = value.indexOf('.');
  if (dotIdx === -1) return false;

  const expPart = value.slice(0, dotIdx);
  const hmacPart = value.slice(dotIdx + 1);

  if (!/^\d+$/.test(expPart)) return false;
  const exp = Number(expPart);
  if (!Number.isFinite(exp)) return false;
  if (Math.floor(Date.now() / 1000) > exp) return false;

  const expected = createHmac('sha256', secret).update(expPart).digest('hex');
  return timingSafeEqual(hmacPart, expected);
}

export { COOKIE_NAME };
