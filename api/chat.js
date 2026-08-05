import fs from 'node:fs';
import path from 'node:path';
import { verifyCookie } from './_lib/auth.js';

const MAX_MESSAGES = 12;
const MAX_CONTENT_LEN = 8000;
const MAX_TOOL_CALLS = 3;
const TOOL_TIMEOUT_MS = 40000;
const OPENAI_TIMEOUT_MS = 20000;
const OVERALL_BUDGET_MS = 55000;
const MAX_ROWS_FOR_MODEL = 100;
const MAX_QUESTION_LEN = 500;
const BACKEND_DOWN_REPLY =
  "⚠️ The data backend is currently unavailable, so I couldn't run the query. Please try again shortly.";

const FALLBACK_SCHEMA = `# FREED warehouse schema (fallback stub)

context/schema.md was not found at deploy time. No table/column documentation
is available. If asked a data question, explain that the schema context is
missing and the question cannot be answered right now.`;

function loadSchema() {
  try {
    return fs.readFileSync(path.join(process.cwd(), 'context', 'schema.md'), 'utf8');
  } catch {
    // Schema fallback should be visible in logs — a silently missing schema
    // file at deploy time is a real production risk (see docs/reports/review-vercel.md).
    console.warn('[chat.js] context/schema.md not found; using FALLBACK_SCHEMA stub');
    return FALLBACK_SCHEMA;
  }
}

function buildSystemPrompt() {
  const schema = loadSchema();
  const today = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
  return `You are the FREED Data Chat assistant. You answer FREED staff questions about
company data by querying the FREED Postgres warehouse via the run_sql tool.

Today's date is ${today} (Asia/Kolkata). The warehouse is LIVE: it is updated
continuously from the production systems and contains current data right up to
today. Ignore any training-cutoff intuition about what year it is — trust this
date and the database.

${schema}

Data-freshness rules (important — do not violate):
- NEVER claim the data "only goes up to" some past year, is "not available"
  after a date, or is stale, unless a query result actually shows that. If you
  are unsure of the available date range for a table, run
  \`SELECT max(...), min(...)\` on its date column to check — do not guess.
- For relative dates ("today", "last week", "this month", "last 7 days"), derive
  the bounds from \`now()\` / \`current_date\` in SQL. Never hardcode a year you
  assume is "current".
- If a query returns zero rows, that means no matching rows for those filters —
  not that the data ends. Re-examine the filters (wrong status/milestone value,
  too-narrow window) before concluding anything about coverage.

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

async function callOpenAI(apiKey, model, messages, allowTools = true) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), OPENAI_TIMEOUT_MS);

  const body = {
    model,
    messages,
    temperature: 0,
    seed: 7,
  };
  if (allowTools) {
    body.tools = [
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
    ];
  } else {
    body.tool_choice = 'none';
  }

  try {
    const resp = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const data = await resp.json();
    if (!resp.ok) {
      const errMsg = data?.error?.message || `OpenAI request failed with status ${resp.status}`;
      throw new Error(errMsg);
    }
    return data;
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error('OpenAI request timed out');
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function runSqlOnce(sql, url, secret, question, chatId) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TOOL_TIMEOUT_MS);
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${secret}`,
      },
      body: JSON.stringify({ sql, question, chat_id: chatId }),
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
    // SQL rejected/failed → feed back to the model to self-correct (not an outage).
    if ((resp.status === 400 || resp.status === 422) && data) {
      return { ok: false, error: data.error || 'query rejected' };
    }
    // 5xx / other → transient, worth one retry.
    return { ok: false, error: `data backend error (status ${resp.status})`, transient: true };
  } catch (err) {
    // Our own timeout: the query took too long. Not an outage — tell the model
    // to narrow the range; it can retry within the tool-call budget.
    if (err && err.name === 'AbortError') {
      return {
        ok: false,
        error:
          'query timed out (it scanned too much data). Narrow the time range or add more selective filters, then try again.',
        timedOut: true,
      };
    }
    // Network/DNS/connection error → transient, worth one retry.
    return { ok: false, error: 'data backend connection failed', transient: true };
  } finally {
    clearTimeout(timer);
  }
}

async function runSql(sql, question, chatId) {
  const url = process.env.QUERY_API_URL;
  const secret = process.env.QUERY_API_SECRET;

  let res = await runSqlOnce(sql, url, secret, question, chatId);
  // Retry exactly once on a transient connection/5xx blip (covers tunnel
  // hiccups). Do NOT retry timeouts (would double the wait) or SQL errors.
  if (!res.ok && res.transient) {
    res = await runSqlOnce(sql, url, secret, question, chatId);
    // If still transient after the retry, treat as a real outage.
    if (!res.ok && res.transient) {
      return { ok: false, error: 'data backend unavailable', backendDown: true };
    }
  }
  return res;
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

    // Correlation id + originating question for the VM audit log (logging
    // metadata only — never used for anything else in this handler).
    const chatId = crypto.randomUUID();
    const lastUserMessage = [...trimmed].reverse().find((m) => m.role === 'user');
    const currentQuestion = (lastUserMessage?.content || '').slice(0, MAX_QUESTION_LEN);

    const conversation = [
      { role: 'system', content: systemPrompt },
      ...trimmed.map((m) => ({ role: m.role, content: m.content })),
    ];

    // Overall wall-clock deadline for this invocation, captured at handler
    // start-of-work, so the worst case (up to 3 run_sql calls + several
    // OpenAI round-trips) stays comfortably under vercel.json's maxDuration
    // of 60s.
    const deadline = Date.now() + OVERALL_BUDGET_MS;

    const tables = [];
    let toolCallsUsed = 0;
    let lastAssistantContent = '';
    let backendDown = false;
    let deadlineExceeded = false;
    // True whenever the most recent assistant turn ended in tool_calls
    // rather than a final natural-language answer — i.e. we still owe the
    // user closing text.
    let needsFinalCompletion = false;

    for (let round = 0; round < MAX_TOOL_CALLS + 1; round++) {
      if (Date.now() >= deadline) {
        deadlineExceeded = true;
        break;
      }

      const data = await callOpenAI(apiKey, model, conversation);
      const choice = data.choices?.[0];
      const assistantMessage = choice?.message;

      if (!assistantMessage) {
        break;
      }

      lastAssistantContent = assistantMessage.content || lastAssistantContent;

      const toolCalls = assistantMessage.tool_calls;
      if (!toolCalls || toolCalls.length === 0) {
        conversation.push(assistantMessage);
        needsFinalCompletion = false;
        break;
      }

      conversation.push(assistantMessage);
      needsFinalCompletion = true;

      for (const call of toolCalls) {
        if (toolCallsUsed >= MAX_TOOL_CALLS) {
          conversation.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify({ error: 'tool call budget exhausted' }),
          });
          continue;
        }

        if (Date.now() >= deadline) {
          deadlineExceeded = true;
          conversation.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify({ error: 'time budget exhausted; query skipped' }),
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
        const result = await runSql(sql, currentQuestion, chatId);

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

      if (backendDown || deadlineExceeded) {
        break;
      }
    }

    if (backendDown) {
      // Contract requires a clear explanation, never an empty reply, when
      // the VM query API is unreachable. Any tables already collected
      // before the failure are still returned.
      lastAssistantContent = BACKEND_DOWN_REPLY;
    } else if (needsFinalCompletion && !deadlineExceeded && Date.now() < deadline) {
      // The tool-call budget (or round budget) was exhausted but the model
      // never got a turn with tools disabled to summarize what it found —
      // force exactly one such turn so the reply is never left empty.
      try {
        const finalData = await callOpenAI(apiKey, model, conversation, false);
        const finalMessage = finalData.choices?.[0]?.message;
        if (finalMessage?.content) {
          lastAssistantContent = finalMessage.content;
        }
      } catch {
        // fall through to the generic non-empty-reply guarantee below
      }
    }

    if (!lastAssistantContent) {
      lastAssistantContent =
        tables.length > 0
          ? "I ran the query successfully, but couldn't generate a summary in time. See the results table below."
          : "I wasn't able to generate a response in time. Please try again.";
    }

    res.status(200).json({ reply: lastAssistantContent, tables });
  } catch (err) {
    res.status(500).json({ error: err?.message || 'internal error' });
  }
}
