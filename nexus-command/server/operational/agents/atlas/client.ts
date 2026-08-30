import { createDeskChat, type ChatFn, type ChatCompletion, type ChatMessage, type ChatToolCall } from '../llmClient.js';
import type { AtlasAiConfig } from './config.js';
import type { AtlasToolDef } from './tools.js';

export type { ChatFn, ChatCompletion, ChatMessage, ChatToolCall };

export function createAtlasChat(config: AtlasAiConfig): ChatFn {
  return createDeskChat(config, 'ATLAS');
}

export type { AtlasToolDef };
