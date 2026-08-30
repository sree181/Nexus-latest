import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { z } from 'zod';
import { invalidateReviewDeskFindings } from './reviewDeskCache.js';
import type { DeskAgentProfile, DeskPolicy, DeskToolCatalogItem } from './profileTypes.js';
import { POLICY_JURISDICTIONS } from './profileTypes.js';

const policySchema = z.object({
  id: z.string().regex(/^policy:[a-z0-9-]{2,80}$/),
  title: z.string().min(3).max(160),
  jurisdiction: z.enum(POLICY_JURISDICTIONS),
  source: z.string().min(3).max(240),
  body: z.string().min(20).max(8_000),
});

export function createDeskProfileStore(
  deskCode: string,
  catalog: DeskToolCatalogItem[],
  defaults: () => DeskAgentProfile,
) {
  const requiredTools = catalog.filter(item => item.required).map(item => item.name);
  const knownTools = new Set(catalog.map(item => item.name));

  const writeSchema = z.object({
    role: z.string().min(8).max(240),
    backstory: z.string().min(20).max(2_000),
    instructions: z.string().min(8).max(4_000),
    llm: z.object({
      model: z.string().min(2).max(120),
      temperature: z.number().min(0).max(1),
      maxTurns: z.number().int().min(2).max(12),
      timeoutMs: z.number().int().min(3_000).max(45_000),
    }),
    enabledTools: z.array(z.string().min(2).max(60)).min(1).max(20)
      .refine(names => names.every(name => knownTools.has(name)), `Unknown ${deskCode.toUpperCase()} tool`),
    policies: z.array(policySchema).max(24),
  });

  type Write = z.infer<typeof writeSchema>;
  let memory: DeskAgentProfile | null = null;

  function pathFor(): string {
    return resolve(process.cwd(), `data/agent-profiles/${deskCode}.json`);
  }

  function normalize(input: Write, previous?: DeskAgentProfile): DeskAgentProfile {
    const enabled = new Set(input.enabledTools.filter(name => knownTools.has(name)));
    for (const name of requiredTools) enabled.add(name);
    const policies: DeskPolicy[] = [];
    const seen = new Set<string>();
    for (const policy of input.policies) {
      if (seen.has(policy.id)) continue;
      seen.add(policy.id);
      policies.push({
        id: policy.id,
        title: policy.title.trim(),
        jurisdiction: policy.jurisdiction,
        source: policy.source.trim(),
        body: policy.body.trim(),
      });
    }
    const same = previous
      && previous.role === input.role.trim()
      && previous.backstory === input.backstory.trim()
      && previous.instructions === input.instructions.trim()
      && previous.llm.model === input.llm.model.trim()
      && previous.llm.temperature === input.llm.temperature
      && previous.llm.maxTurns === input.llm.maxTurns
      && previous.llm.timeoutMs === input.llm.timeoutMs
      && previous.enabledTools.join() === [...enabled].join()
      && JSON.stringify(previous.policies) === JSON.stringify(policies);
    return {
      deskCode,
      role: input.role.trim(),
      backstory: input.backstory.trim(),
      instructions: input.instructions.trim(),
      llm: {
        model: input.llm.model.trim(),
        temperature: Math.round(input.llm.temperature * 100) / 100,
        maxTurns: input.llm.maxTurns,
        timeoutMs: input.llm.timeoutMs,
      },
      enabledTools: catalog.map(item => item.name).filter(name => enabled.has(name)),
      policies,
      updatedAt: same && previous ? previous.updatedAt : new Date().toISOString(),
    };
  }

  function readDisk(): DeskAgentProfile | null {
    if (process.env.NODE_ENV === 'test') return null;
    try {
      return normalize(writeSchema.parse(JSON.parse(readFileSync(pathFor(), 'utf8'))));
    } catch {
      return null;
    }
  }

  function writeDisk(profile: DeskAgentProfile): void {
    if (process.env.NODE_ENV === 'test') return;
    const path = pathFor();
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, `${JSON.stringify(profile, null, 2)}\n`);
  }

  return {
    writeSchema,
    load(): DeskAgentProfile {
      if (memory) return memory;
      memory = readDisk() ?? defaults();
      return memory;
    },
    save(input: unknown): DeskAgentProfile {
      memory = normalize(writeSchema.parse(input), memory ?? undefined);
      writeDisk(memory);
      invalidateReviewDeskFindings();
      return memory;
    },
    reset(): DeskAgentProfile {
      memory = { ...defaults(), updatedAt: new Date().toISOString() };
      writeDisk(memory);
      invalidateReviewDeskFindings();
      return memory;
    },
    replaceForTest(profile: DeskAgentProfile | null): void {
      memory = profile;
    },
  };
}
