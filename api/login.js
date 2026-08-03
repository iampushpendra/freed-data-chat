import { signCookie, timingSafeEqual, COOKIE_NAME } from './_lib/auth.js';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method not allowed' });
    return;
  }

  const chatPassword = process.env.CHAT_PASSWORD;
  const cookieSecret = process.env.CHAT_COOKIE_SECRET;

  if (!chatPassword || !cookieSecret) {
    res.status(500).json({ error: 'server misconfigured: CHAT_PASSWORD or CHAT_COOKIE_SECRET not set' });
    return;
  }

  let body = req.body;
  if (typeof body === 'string') {
    try {
      body = JSON.parse(body);
    } catch {
      body = {};
    }
  }
  body = body || {};

  const password = typeof body.password === 'string' ? body.password : '';

  if (!password || !timingSafeEqual(password, chatPassword)) {
    res.status(401).json({ ok: false });
    return;
  }

  const cookieValue = signCookie(cookieSecret);
  const maxAge = 604800; // 7 days in seconds
  res.setHeader(
    'Set-Cookie',
    `${COOKIE_NAME}=${cookieValue}; HttpOnly; Path=/; Max-Age=${maxAge}; SameSite=Lax; Secure`
  );
  res.status(200).json({ ok: true });
}
