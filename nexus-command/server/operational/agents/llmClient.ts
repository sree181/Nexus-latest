import type { AtlasAiConfig } from './atlas/config.js';

export interface ChatToolDef {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

export interface ChatToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string };
}

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | null;
  name?: string;
  tool_call_id?: string;
  tool_calls?: ChatToolCall[];
}

export interface ChatCompletion {
  content: string | null;
  toolCalls: ChatToolCall[];
  model: string;
}

export type ChatFn = (messages: ChatMessage[], tools: ChatToolDef[]) => Promise<ChatCompletion>;

interface OpenAiChoice {
  message?: {
    content?: string | null;
    tool_calls?: Array<{
      id?: string;
      type?: string;
      function?: { name?: string; arguments?: string };
    }>;
  };
}

export function retryDelayMs(status: number, message: string, attempt: number): number | null {
  if (status !== 429 && status !== 503) return null;
  const ms = /try again in (\d+)\s*ms/i.exec(message);
  if (ms) return Math.min(8_000, Math.max(200, Number(ms[1])));
  const seconds = /try again in ([\d.]+)\s*s(?:ec(?:onds?)?)?/i.exec(message);
  if (seconds) return Math.min(8_000, Math.max(200, Math.ceil(Number(seconds[1]) * 1000)));
  return Math.min(4_000, 300 * (attempt + 1));
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function createDeskChat(config: AtlasAiConfig, label = 'desk'): ChatFn {
  return async (messages, tools) => {
    let lastError = `The ${label} model did not respond`;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), config.timeoutMs);
      try {
        const response = await fetch(`${config.baseUrl}/chat/completions`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${config.apiKey}`,
          },
          body: JSON.stringify({
            model: config.model,
            temperature: config.temperature,
            max_tokens: 900,
            tools,
            tool_choice: 'auto',
            messages,
          }),
          signal: controller.signal,
        });
        const payload = await response.json() as {
          error?: { message?: string };
          model?: string;
          choices?: OpenAiChoice[];
        };
        if (!response.ok) {
          lastError = payload.error?.message || `${label} model HTTP ${response.status}`;
          const wait = retryDelayMs(response.status, lastError, attempt);
          if (wait !== null && attempt < 2) {
            await sleep(wait);
            continue;
          }
          throw new Error(lastError);
        }
        const message = payload.choices?.[0]?.message;
        const toolCalls = (message?.tool_calls ?? [])
          .filter(call => call.function?.name)
          .map((call, index) => ({
            id: call.id || `call_${index}`,
            type: 'function' as const,
            function: {
              name: call.function!.name!,
              arguments: call.function?.arguments || '{}',
            },
          }));
        return {
          content: typeof message?.content === 'string' ? message.content : null,
          toolCalls,
          model: payload.model || config.model,
        };
      } finally {
        clearTimeout(timer);
      }
    }
    throw new Error(lastError);
  };
}
