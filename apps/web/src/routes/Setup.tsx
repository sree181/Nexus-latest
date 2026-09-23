import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@meshagent/ui";
import type { DeviceOut, HealthOut } from "../lib/api";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { useIdentity } from "../lib/useIdentity";
import { timestamp } from "../lib/format";

/** Connecting an editor to this deployment, without a terminal walkthrough.
 *
 *  The bug this screen exists for: every hook configuration shipped under
 *  adapters/ names its script by a relative path or by the recorded project's
 *  own directory, both of which resolve only when the repository being recorded
 *  IS this checkout. Anyone recording their own repository gets a hook that
 *  cannot find the script, and a hook that cannot start records nothing and
 *  says nothing. So every block below is generated against an absolute path.
 *
 *  That path comes from the API, which knows where its own files are and does
 *  not know whether the editor being configured is on the same machine. It is
 *  offered as what this process sees and left editable, because the one thing
 *  worse than asking is asserting. */
export function Setup() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    staleTime: Infinity,
    retry: false,
  });
  const devices = useQuery({ queryKey: ["devices"], queryFn: api.devices });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const { me } = useIdentity();

  const [root, setRoot] = useState("");
  const [apiUrl, setApiUrl] = useState(window.location.origin);
  const [label, setLabel] = useState("work laptop");
  const [gate, setGate] = useState(true);
  const [excludes, setExcludes] = useState("*.env, secrets/*, *.pem");

  // the API's answer is the default, not the value: a developer who has
  // corrected it keeps their correction when this query refetches
  const checkout = trimSlashes(root || health.data?.checkout || "");

  // Runs this caller actually owns. Matched on the subject rather than taken
  // as the length of the list, because the analyst's list is every
  // developer's, and the seeded build is in everybody's and is nobody's.
  const mine =
    me === undefined || runs.data === undefined
      ? undefined
      : runs.data.filter((r) => !r.seeded && r.owner === me.subject).length;

  return (
    <main className="flex h-full flex-1 flex-col overflow-hidden">
      <PageHeader
        section="This machine"
        title="Connect your editor"
        meta="Everything here is generated for this deployment. Nothing is written for you."
      />

      <div className="flex flex-1 flex-col items-center overflow-auto px-8 py-8">
        <div className="flex w-full max-w-[860px] flex-col gap-6">
          <Where health={health.data} failed={health.isError} me={me} />

          <Pair
            apiUrl={apiUrl}
            setApiUrl={setApiUrl}
            label={label}
            setLabel={setLabel}
            checkout={checkout}
            devices={devices.data}
          />

          <OptIn gate={gate} setGate={setGate} excludes={excludes} setExcludes={setExcludes} />

          <Hooks
            checkout={checkout}
            reported={health.data?.checkout}
            onPath={setRoot}
          />

          <Mcp checkout={checkout} apiUrl={apiUrl} />

          <Evidence devices={devices.data} ownRuns={mine} />
        </div>
      </div>
    </main>
  );
}

function trimSlashes(path: string): string {
  return path.replace(/\/+$/, "");
}

// -- the pieces every section is built from -----------------------------------

function Section({
  title,
  detail,
  children,
}: {
  title: string;
  detail: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5"
    >
      <div>
        <h2 className="font-serif text-[17px] text-ink">{title}</h2>
        <p className="mt-1 text-[13px] leading-snug text-slate">{detail}</p>
      </div>
      {children}
    </section>
  );
}

const CLIPBOARD_REFUSED =
  "The browser would not give this page the clipboard — it allows that only over HTTPS or on localhost. The text above is selectable; copy it by hand.";

/** A generated block, with a copy control that is not the only way to get it.
 *
 *  `navigator.clipboard` needs a secure context. Someone reaching this
 *  deployment over a LAN address has no clipboard API and no error either, so
 *  the text stays plain selectable content and a failure is said out loud. */
function Block({
  id,
  heading,
  language,
  value,
  children,
}: {
  id: string;
  heading: string;
  language: string;
  value: string;
  children?: ReactNode;
}) {
  const [said, setSaid] = useState("");

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p
          id={`${id}-heading`}
          className="font-mono text-[10.5px] tracking-wide text-slate"
        >
          {heading}
        </p>
        <button
          type="button"
          onClick={() => {
            const clip = navigator.clipboard;
            if (!clip) {
              setSaid(CLIPBOARD_REFUSED);
              return;
            }
            void clip.writeText(value).then(
              () => setSaid(`Copied the ${language} above to the clipboard.`),
              () => setSaid(CLIPBOARD_REFUSED),
            );
          }}
          className="rounded-lg border border-line bg-surface px-3 py-1.5 text-[12.5px] text-ink transition hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Copy
        </button>
      </div>
      <pre
        aria-labelledby={`${id}-heading`}
        tabIndex={0}
        className="overflow-x-auto rounded-xl border border-line-2 bg-surface-2 px-4 py-3 font-mono text-[12px] leading-relaxed text-ink"
      >
        {value}
      </pre>
      <p role="status" className="text-[12.5px] leading-snug text-slate">
        {said}
      </p>
      {children}
    </div>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  hint,
  width = "w-full",
}: {
  id: string;
  label: string;
  value: string;
  onChange: (next: string) => void;
  hint?: ReactNode;
  width?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className="font-mono text-[10.5px] tracking-wide text-slate"
      >
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`${width} rounded-lg border border-line bg-surface-2 px-3 py-2 font-mono text-[12.5px] text-ink`}
      />
      {hint && (
        <p className="text-[12.5px] leading-snug text-slate">{hint}</p>
      )}
    </div>
  );
}

// -- where you are ------------------------------------------------------------

function Fact({
  label,
  word,
  tone,
  detail,
}: {
  label: string;
  word: string;
  tone: "ok" | "warn" | "risk" | "accent" | "neutral";
  detail: string;
}) {
  return (
    <li className="flex flex-col gap-1.5 rounded-xl border border-line px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[10.5px] tracking-wide text-slate">
          {label}
        </span>
        <Badge tone={tone}>{word}</Badge>
      </div>
      <p className="text-[12.5px] leading-snug text-slate">{detail}</p>
    </li>
  );
}

function Where({
  health,
  failed,
  me,
}: {
  health: HealthOut | undefined;
  failed: boolean;
  me: { verified: boolean; name: string } | undefined;
}) {
  return (
    <Section
      title="Where you are"
      detail="The deployment these instructions are for, as it answers about itself."
    >
      <ul className="grid gap-3 sm:grid-cols-2">
        <Fact
          label="API"
          word={health ? "reachable" : failed ? "unreachable" : "asking"}
          tone={health ? "ok" : failed ? "risk" : "neutral"}
          detail={
            health
              ? `Version ${health.version}, served by ${health.gateway}.`
              : failed
                ? "This browser could not reach the API, so nothing below has been confirmed against a deployment."
                : "Waiting for the API to answer."
          }
        />
        <Fact
          label="RECORDING"
          word={health ? (health.mode.engine ? "engine" : "sample") : "unknown"}
          tone={health?.mode.persists ? "ok" : health ? "risk" : "neutral"}
          detail={health?.mode.note ?? "Not known until the API answers."}
        />
        <Fact
          label="STORAGE"
          word={health ? (health.durable ? "durable" : "ephemeral") : "unknown"}
          tone={health?.durable ? "ok" : health ? "warn" : "neutral"}
          detail={
            health?.durable
              ? "A memory directory is configured, so device tokens and governed memory survive a restart."
              : "No memory directory is configured. Pairing this machine will work and the token will be gone the next time the API starts."
          }
        />
        <Fact
          label="IDENTITY"
          word={me?.verified ? "verified" : "asserted"}
          tone={me?.verified ? "ok" : "warn"}
          detail={
            me?.verified
              ? `An identity provider signed the claims this deployment reads you from.`
              : `No identity provider is configured, so ${me?.name ?? "this browser"} is who this browser says it is. A device token minted here records under that same unproven name.`
          }
        />
      </ul>
    </Section>
  );
}

// -- pair this machine --------------------------------------------------------

/** Pairing stays a two-step, two-secret flow.
 *
 *  It is tempting to put a "pair this machine" button here and be done. That
 *  would collapse the split the device flow is built around — one secret the
 *  CLI keeps, one code the human reads — and would end with a bearer token
 *  that can write memory travelling through a browser and a clipboard. */
function Pair({
  apiUrl,
  setApiUrl,
  label,
  setLabel,
  checkout,
  devices,
}: {
  apiUrl: string;
  setApiUrl: (next: string) => void;
  label: string;
  setLabel: (next: string) => void;
  checkout: string;
  devices: DeviceOut[] | undefined;
}) {
  const command = `MESHAGENT_API=${apiUrl} python3 ${checkout}/cli/meshagent.py login --label ${JSON.stringify(label)}`;

  return (
    <Section
      title="Pair this machine"
      detail="Your editor's hooks have no browser and cannot sign in the way this page did. They record under a device token instead, which exists only once you have approved it here."
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          id="api-url"
          label="MESHAGENT_API"
          value={apiUrl}
          onChange={setApiUrl}
          hint="How this browser reaches the API. A shell on another machine may need a different address."
        />
        <Field
          id="device-label"
          label="WHAT TO CALL THIS MACHINE"
          value={label}
          onChange={setLabel}
          hint="Shown on the Devices screen, and in the approval prompt you are about to read."
        />
      </div>

      <Block id="pair" heading="RUN THIS IN A SHELL" language="command" value={command}>
        <p className="text-[12.5px] leading-snug text-slate">
          It prints a short code and waits. Open{" "}
          <Link
            to="/devices"
            className="text-accent underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Devices
          </Link>
          , enter that code, and read what you are approving before you approve
          it. The token it mints can record and nothing else — it cannot read a
          run, see the fleet, or delete anything.
        </p>
      </Block>

      <div className="flex flex-col gap-1.5 border-t border-line pt-3">
        <p className="font-mono text-[10.5px] tracking-wide text-slate">
          REGISTERED TO YOU
        </p>
        {devices === undefined ? (
          <p className="text-[12.5px] text-slate">Reading registered machines…</p>
        ) : devices.length === 0 ? (
          <p className="text-[12.5px] leading-snug text-slate">
            None yet. Until one is registered, a hook posts under a name
            asserted in a header, and the coverage table says so rather than
            naming someone it cannot stand behind.
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {devices.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-2">
                <span className="text-[13px] text-ink">{d.label}</span>
                {!d.verified && <Badge tone="warn">identity asserted</Badge>}
                <span className="font-mono text-[11.5px] text-slate">
                  {d.last_used
                    ? `last recorded ${timestamp(d.last_used)}`
                    : "has not recorded anything yet"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

// -- opt the repository in ----------------------------------------------------

function OptIn({
  gate,
  setGate,
  excludes,
  setExcludes,
}: {
  gate: boolean;
  setGate: (next: boolean) => void;
  excludes: string;
  setExcludes: (next: string) => void;
}) {
  const patterns = excludes
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  const config = JSON.stringify(
    { record: true, gate, exclude: patterns },
    null,
    2,
  );

  return (
    <Section
      title="Opt this repository in"
      detail="The hooks record nothing at all until this file exists. Absent means absent: source code leaving a developer's machine is a works-council conversation before it is an engineering one."
    >
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-2.5">
          <input
            id="gate"
            type="checkbox"
            checked={gate}
            onChange={(e) => setGate(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
          />
          <label htmlFor="gate" className="text-[13px] leading-snug text-ink">
            Let MeshAgent refuse an install
            <span className="block text-[12.5px] text-slate">
              With this off the hooks still record everything and never block
              the agent. Observing and refusing are different asks, and a team
              may agree to the first without the second.
            </span>
          </label>
        </div>

        <Field
          id="excludes"
          label="NEVER RECORD THESE (COMMA SEPARATED)"
          value={excludes}
          onChange={setExcludes}
          hint="Matched against the repo-relative path and against the bare filename, so both secrets/* and *.pem do what you would expect."
        />
      </div>

      <Block
        id="optin"
        heading="SAVE AS .meshagent.json"
        language="file"
        value={config}
      >
        <p className="text-[12.5px] leading-snug text-slate">
          At the root of <span className="font-medium">the repository you want
          recorded</span> — the one you will be working in — not at the root of
          the MeshAgent checkout.
        </p>
      </Block>
    </Section>
  );
}

// -- install the hook ---------------------------------------------------------

function cursorHooks(checkout: string): string {
  const command = `python3 ${checkout}/adapters/cursor/meshagent_hook.py`;
  return JSON.stringify(
    {
      version: 1,
      hooks: {
        beforeSubmitPrompt: [{ type: "command", command, timeout: 5 }],
        afterFileEdit: [{ type: "command", command, timeout: 5 }],
        beforeShellExecution: [
          { type: "command", command, timeout: 6, failClosed: false },
        ],
        afterShellExecution: [{ type: "command", command, timeout: 5 }],
        sessionEnd: [{ type: "command", command, timeout: 10 }],
      },
    },
    null,
    2,
  );
}

function claudeHooks(checkout: string): string {
  const command = `python3 ${checkout}/adapters/claude-code/meshagent_hook.py`;
  return JSON.stringify(
    {
      hooks: {
        UserPromptSubmit: [{ hooks: [{ type: "command", command, timeout: 5 }] }],
        PreToolUse: [
          { matcher: "Bash", hooks: [{ type: "command", command, timeout: 6 }] },
        ],
        PostToolUse: [
          {
            matcher: "Write|Edit|MultiEdit|NotebookEdit|Bash",
            hooks: [{ type: "command", command, timeout: 5 }],
          },
        ],
        SessionEnd: [{ hooks: [{ type: "command", command, timeout: 10 }] }],
      },
    },
    null,
    2,
  );
}

type Editor = "cursor" | "claude";

function Hooks({
  checkout,
  reported,
  onPath,
}: {
  checkout: string;
  reported: string | undefined;
  onPath: (next: string) => void;
}) {
  const [editor, setEditor] = useState<Editor>("cursor");

  return (
    <Section
      title="Install the hook"
      detail="This is what makes the editor report at all. It watches file writes and shell commands and posts them; it never sees why, which is what the next section is for."
    >
      <Field
        id="checkout"
        label="PATH TO THE MESHAGENT CHECKOUT"
        value={checkout}
        onChange={onPath}
        hint={
          <>
            {reported
              ? `This is where the API process sees its own files. It cannot know your editor runs on the same filesystem — if it does not, replace this with the path as that machine sees it.`
              : "Not known yet: the API has not answered. Type the path as the machine running your editor sees it."}{" "}
            It has to be absolute. The editor runs these hooks with{" "}
            <span className="font-medium">your</span> repository as its working
            directory, so a relative path finds nothing and a hook that cannot
            start records nothing and reports nothing.
          </>
        }
      />

      <div
        role="group"
        aria-label="Editor"
        className="flex flex-wrap items-center gap-1"
      >
        {(
          [
            ["cursor", "Cursor"],
            ["claude", "Claude Code"],
          ] as const
        ).map(([key, name]) => (
          <button
            key={key}
            type="button"
            aria-pressed={editor === key}
            onClick={() => setEditor(key)}
            className={`rounded-lg px-3.5 py-2 text-[13px] transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
              editor === key
                ? "bg-accent-soft font-medium text-accent"
                : "text-slate hover:bg-surface-2 hover:text-ink"
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      {editor === "cursor" ? (
        <Block
          id="cursor-hooks"
          heading="SAVE AS .cursor/hooks.json"
          language="file"
          value={cursorHooks(checkout)}
        >
          <p className="text-[12.5px] leading-snug text-slate">
            In the repository you are recording. Cursor treats unparseable
            output from a permission hook as a refusal, which is why{" "}
            <span className="font-mono text-[12px]">beforeShellExecution</span>{" "}
            here always answers out loud, including when it is allowing.
          </p>
        </Block>
      ) : (
        <Block
          id="claude-hooks"
          heading="MERGE INTO .claude/settings.json"
          language="block"
          value={claudeHooks(checkout)}
        >
          <p className="text-[12.5px] leading-snug text-slate">
            Merge, not replace. That file very likely already has hooks in it,
            and overwriting it would silently remove whatever else was
            listening.
          </p>
        </Block>
      )}
    </Section>
  );
}

// -- let the agent say why ----------------------------------------------------

function Mcp({ checkout, apiUrl }: { checkout: string; apiUrl: string }) {
  const config = JSON.stringify(
    {
      mcpServers: {
        meshagent: {
          command: "python3",
          args: [`${checkout}/adapters/mcp/meshagent_mcp.py`],
          env: { MESHAGENT_API: apiUrl },
        },
      },
    },
    null,
    2,
  );

  return (
    <Section
      title="Let the agent say why"
      detail="A hook sees a file being written and never the reasoning behind it, so everything it sends arrives explicitly unexplained. This is how an agent volunteers the reason, and how it reads governed memory back."
    >
      <Block
        id="mcp"
        heading="SAVE AS .cursor/mcp.json — OR .mcp.json FOR CLAUDE CODE"
        language="file"
        value={config}
      >
        <p className="text-[12.5px] leading-snug text-slate">
          Same absolute path rule, and for the same reason. It uses the
          credential the pairing above wrote, so there is nothing further to
          export. An agent that never calls it produces a perfect, empty
          record — which is why the hooks sit underneath it.
        </p>
      </Block>
    </Section>
  );
}

// -- has any of it worked? ----------------------------------------------------

/** Evidence, and only from the API.
 *
 *  Every claim here is something the API received. None of it is a check that
 *  a file exists, because this page has never seen your filesystem: it can say
 *  a token was minted and cannot say your editor found it. A green tick
 *  standing for "probably fine" would make the whole screen worthless. */
/** Yes, not yet, or still asking. The third is not folded into the second: a
 *  query in flight and a pairing that never happened are different answers,
 *  and this screen is read to find out which. */
function verdict(count: number | undefined): {
  word: string;
  tone: "ok" | "warn" | "neutral";
} {
  if (count === undefined) return { word: "asking", tone: "neutral" };
  return count > 0 ? { word: "yes", tone: "ok" } : { word: "not yet", tone: "warn" };
}

function Evidence({
  devices,
  ownRuns,
}: {
  devices: DeviceOut[] | undefined;
  ownRuns: number | undefined;
}) {
  const registered = devices?.length;
  const recorded = devices?.filter((d) => d.last_used > 0);

  return (
    <Section
      title="Has any of it worked?"
      detail="Three things the API can actually attest to. It has not looked at your disk, so nothing below says a file is in the right place — only what arrived here."
    >
      <ul className="grid gap-3">
        <Fact
          label="A MACHINE IS REGISTERED"
          {...verdict(registered)}
          detail={
            registered === undefined
              ? "Asking the API which machines are registered to you."
              : registered > 0
                ? `The API holds ${registered} device token${registered === 1 ? "" : "s"} for you, so a pairing was approved. It does not follow that your editor can find the credential file, or that the hook is installed.`
                : "No pairing has been approved under your name. Anything a hook posts arrives under an asserted header instead."
          }
        />
        <Fact
          label="THAT MACHINE HAS RECORDED"
          {...verdict(recorded?.length)}
          detail={
            recorded === undefined
              ? "Asking the API when those machines last wrote anything."
              : recorded.length > 0
                ? `The API has accepted at least one request under ${recorded.map((d) => d.label).join(", ")}. That is a write it received, not a statement that your most recent edit was among them.`
                : "No registered machine has used its token yet. A hook that is not installed, and a hook installed with a path that does not resolve, both look exactly like this."
          }
        />
        <Fact
          label="YOU OWN A RUN"
          {...verdict(ownRuns)}
          detail={
            ownRuns === undefined
              ? "Asking the API which runs are attributed to you."
              : ownRuns > 0
                ? `${ownRuns} run${ownRuns === 1 ? " is" : "s are"} attributed to you by name. The seeded reference build is not counted: it is published to everyone and would say nothing about your setup.`
                : "No run here is attributed to you yet. The seeded reference build is readable by everyone and is deliberately not counted as yours."
          }
        />
      </ul>
    </Section>
  );
}
