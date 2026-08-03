import fs from 'node:fs';
import path from 'node:path';
import { verifyCookie } from './_lib/auth.js';

const MAX_MESSAGES = 12;
const MAX_CONTENT_LEN = 8000;
const MAX_TOOL_CALLS = 3;
const TOOL_TIMEOUT_MS = 45000;
const MAX_ROWS_FOR_MODEL = 100;

const FALLBACK_SCHEMA = `# FREED warehouse schema (fallback stub)

context/schema.md was not found at deploy time. No table/column documentation
is available. If asked a data question, explain that the schema context is
missing and the question cannot be answered right now.`;

function loadSchema() {
  try {
    return fs.readFileSync(path.join(process.cwd(), 'context', 'schema.md'), 'utf8');
  } catch {
    return FALLBACK_SCHEMA;
  }
}

function buildSystemPrompt() {
  const schema = loadSchema();
  return `You are the FREED Data Chat assistant. You answer FREED staff questions about
company data by querying the FREED Postgres warehouse via the run_sql tool.

${schema}

Hard rules:
- The database is PostgreSQL. Write PostgreSQL-dialect SQL only.
- Every run_sql call must contain exactly one statement: a single SELECT or a
  single WITH ... SELECT. Never write INSERT/UPDATE/DELETE/DDL or any other
  data-modifying statement.
- Always add a LIMIT clause of at most 500 rows, unless the query is a full
  aggregation that already returns a small, bounded number of rows (e.g. a
  GROUP BY summary).
- Never attempt to modify data under any circumstances.
- If the question cannot be answered from the documented schema, say so
  plainly instead of guessing or inventing tables/columns.
- Answer concisely. When referring to currency amounts, use ₹ (INR) where relevant.`;
}

function isValidMessage(m) {
  if (!m || typeof m !== 'object') return false;
  if (m.role !== 'user' && m.role !== 'assistant') return false;
  if (typeof m.content !== 'string') return false;
  if (m.content.length > MAX_CONTENT_LEN) return false;
  return true;
}

async function callOpenAI(apiKey, model, messages) {
  const resp = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages,
      temperature: 0.1,
      tools: [
        {
          type: 'function',
          function: {
            name: 'run_sql',
            description: 'Run one read-only PostgreSQL SELECT/WITH statement against the FREED warehouse',
            parameters: {
              type: 'object',
              properties: {
                sql: { type: 'string' },
              },
              required: ['sql'],
            },
          },
        },
      ],
    }),
  });

  const data = await resp.json();
  if (!resp.ok) {
    const errMsg = data?.error?.message || `OpenAI request failed with status ${resp.status}`;
    throw new Error(errMsg);
  }
  return data;
}

async function runSql(sql) {
  const url = process.env.QUERY_API_URL;
  const secret = process.env.QUERY_API_SECRET;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TOOL_TIMEOUT_MS);

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${secret}`,
      },
      body: JSON.stringify({ sql }),
      signal: controller.signal,
    });

    let data;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }

    if (resp.status === 200 && data) {
      return { ok: true, data };
    }
    if ((resp.status === 400 || resp.status === 422) && data) {
      return { ok: false, error: data.error || 'query rejected' };
    }
    return { ok: false, error: `data backend error (status ${resp.status})`, backendDown: true };
  } catch {
    return { ok: false, error: 'data backend unavailable', backendDown: true };
  } finally {
    clearTimeout(timer);
  }
}

export default async function handler(req, res) {
  try {
    if (req.method !== 'POST') {
      res.status(405).json({ error: 'method not allowed' });
      return;
    }

    const cookieSecret = process.env.CHAT_COOKIE_SECRET;
    if (!cookieSecret || !verifyCookie(req.headers.cookie, cookieSecret)) {
      res.status(401).json({ error: 'auth' });
      return;
    }

    const apiKey = process.env.OPENAI_API_KEY;
    if (!apiKey) {
      res.status(500).json({ error: 'server misconfigured: OPENAI_API_KEY not set' });
      return;
    }
    if (!process.env.QUERY_API_URL || !process.env.QUERY_API_SECRET) {
      res.status(500).json({ error: 'server misconfigured: QUERY_API_URL or QUERY_API_SECRET not set' });
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

    if (!Array.isArray(body.messages) || body.messages.length === 0) {
      res.status(400).json({ error: 'messages must be a non-empty array' });
      return;
    }

    const trimmed = body.messages.slice(-MAX_MESSAGES);
    if (!trimmed.every(isValidMessage)) {
      res.status(400).json({
        error: `each message must be {role: "user"|"assistant", content: string<=${MAX_CONTENT_LEN}}`,
      });
      return;
    }

    const model = process.env.OPENAI_MODEL || 'gpt-4o';
    const systemPrompt = buildSystemPrompt();

    const conversation = [
      { role: 'system', content: systemPrompt },
      ...trimmed.map((m) => ({ role: m.role, content: m.content })),
    ];

    const tables = [];
    let toolCallsUsed = 0;
    let lastAssistantContent = '';
    let backendDown = false;

    for (let round = 0; round < MAX_TOOL_CALLS + 1; round++) {
      const data = await callOpenAI(apiKey, model, conversation);
      const choice = data.choices?.[0];
      const assistantMessage = choice?.message;

      if (!assistantMessage) {
        break;
      }

      lastAssistantContent = assistantMessage.content || lastAssistantContent;

      const toolCalls = assistantMessage.tool_calls;
      if (!toolCalls || toolCalls.length === 0 || backendDown) {
        conversation.push(assistantMessage);
        break;
      }

      conversation.push(assistantMessage);

      for (const call of toolCalls) {
        if (toolCallsUsed >= MAX_TOOL_CALLS) {
          conversation.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify({ error: 'tool call budget exhausted' }),
          });
          continue;
        }

        let sql = '';
        try {
          const args = JSON.parse(call.function?.arguments || '{}');
          sql = typeof args.sql === 'string' ? args.sql : '';
        } catch {
          sql = '';
        }

        toolCallsUsed++;
        const result = await runSql(sql);

        if (result.ok) {
          const rows = Array.isArray(result.data.rows) ? result.data.rows : [];
          const columns = Array.isArray(result.data.columns) ? result.data.columns : [];
          const rowCount = typeof result.data.row_count === 'number' ? result.data.row_count : rows.length;
          const truncated = Boolean(result.data.truncated);

          tables.push({ sql, columns, rows, row_count: rowCount, truncated });

          const modelRows = rows.slice(0, MAX_ROWS_FOR_MODEL);
          conversation.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify({
              columns,
              rows: modelRows,
              row_count: rowCount,
              truncated: truncated || rows.length > MAX_ROWS_FOR_MODEL,
              note:
                rows.length > MAX_ROWS_FOR_MODEL
                  ? `only first ${MAX_ROWS_FOR_MODEL} of ${rows.length} rows shown to model`
                  : undefined,
            }),
          });
        } else {
          conversation.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify({ error: result.error }),
          });
          if (result.backendDown) {
            backendDown = true;
          }
        }
      }

      if (backendDown) {
        break;
      }
    }

    res.status(200).json({ reply: lastAssistantContent, tables });
  } catch (err) {
    res.status(500).json({ error: err?.message || 'internal error' });
  }
}
