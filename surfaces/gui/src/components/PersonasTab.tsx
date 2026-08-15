import { useEffect, useRef, useState } from "react";
import {
  deletePersona,
  exportPersona,
  getPersonas,
  getSessions,
  installPersona,
  updatePersona,
  type Persona,
  type PersonaConsent,
} from "../api";
import { chooseFolder } from "../tauri";
import type { SessionInfo } from "../types";
import { Icon } from "./Icon";

// Personas management: enable a persona, choose whether it shows in the new-session picker,
// set the default, and install more from a local directory or a GitHub repo (snapshotted).
// Re-skinned to the mock's Tailwind card idiom (§ Settings-as-page); the page title supplies the
// heading, so this drops its own "Personas" sub-header.
const CARD = "rounded-xl2 border border-line bg-panel";
const SEC_H = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const CHECK = "flex items-center gap-1.5 text-[12.5px] text-muted select-none shrink-0";
const SELECT = "px-2.5 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink shrink-0";
const INPUT =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT = "text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[12.5px] px-2.5 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 disabled:opacity-40 disabled:hover:border-line";

export function PersonasTab({ onOpenPersona }: { onOpenPersona?: (id: string) => void }) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [mode, setMode] = useState<"git" | "dir" | "zip">("git");
  const [src, setSrc] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [consent, setConsent] = useState<PersonaConsent[] | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  // Disabling archives the persona's conversations (server-side), so when there are any we
  // arm an inline confirm (same two-step idiom as delete) instead of flipping immediately.
  const [confirmOff, setConfirmOff] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  // The picker's "Import coworker…" door lands here and asks us to put the Add section
  // front and center (sharing v1).
  const addRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const focus = () => addRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.addEventListener("ocw-focus-import", focus);
    return () => window.removeEventListener("ocw-focus-import", focus);
  }, []);

  const reload = () => getPersonas().then(setPersonas).catch(() => {});
  const reloadSessions = () => getSessions().then(setSessions).catch(() => {});
  useEffect(() => {
    reload();
    reloadSessions();
  }, []);

  // Real conversations the disable would archive (unarchived; run sessions are server-hidden).
  const liveCount = (id: string) =>
    sessions.filter((s) => s.agent === id && !s.archived).length;

  const toggle = async (
    id: string,
    body: { enabled?: boolean; surfaced?: boolean; default?: boolean },
  ) => {
    const r = await updatePersona(id, body);
    if (r.personas) setPersonas(r.personas);
    else reload();
    if (body.enabled === false) reloadSessions(); // counts just changed
  };

  const requestDisable = (p: Persona) => {
    if (liveCount(p.id) > 0) setConfirmOff(p.id);
    else toggle(p.id, { enabled: false });
  };

  const remove = async (id: string) => {
    setConfirmDel(null);
    const r = await deletePersona(id);
    if (!r.ok) {
      setMsg(r.error || "delete failed");
      return;
    }
    if (r.personas) setPersonas(r.personas);
    else reload();
  };

  const finishInstall = (r: Awaited<ReturnType<typeof installPersona>>) => {
    setBusy(false);
    if (!r.ok) {
      setMsg(r.error || "install failed");
      return;
    }
    setConsent(r.consent || []);
    if (r.personas) setPersonas(r.personas);
    setMsg(`Installed ${(r.consent || []).length} coworker(s) — review and enable below.`);
    setSrc("");
  };

  const installZip = async (file: File) => {
    setBusy(true);
    setMsg(null);
    setConsent(null);
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000)
      bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    finishInstall(await installPersona({ zip_b64: btoa(bin), filename: file.name }));
  };

  const exportOne = async (p: Persona) => {
    const dir = await chooseFolder();
    if (!dir) return;
    const r = await exportPersona(p.id, dir);
    setMsg(r.ok ? `Exported to ${r.path}` : r.error || "export failed");
  };

  const install = async () => {
    if (!src.trim()) return;
    setBusy(true);
    setMsg(null);
    setConsent(null);
    const r = await installPersona(
      mode === "git" ? { git_url: src.trim() } : { dir: src.trim() },
    );
    setBusy(false);
    if (!r.ok) {
      setMsg(r.error || "install failed");
      return;
    }
    setConsent(r.consent || []);
    if (r.personas) setPersonas(r.personas);
    setMsg(`Installed ${(r.consent || []).length} coworker(s) — review and enable below.`);
    setSrc("");
  };

  return (
    <div>
      <p className="text-[12.5px] text-muted mb-3 leading-relaxed">
        Enable a coworker, then choose whether it appears in the coworker picker. The starred coworker
        is the default for new sessions.
      </p>

      <div className={CARD + " divide-y divide-line mb-6"}>
        {personas.map((p) => (
          <div key={p.id} className="px-4 py-3">
            <div className="flex items-center gap-4">
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-medium flex items-center gap-1.5">
                <span className="truncate">{p.name}</span>
                {p.default && <span className="text-accent" title="Default for new sessions">★</span>}
                {p.builtin && <span className="text-[11px] text-faint font-normal">· built-in</span>}
              </div>
              <div className="text-[12px] text-muted truncate">{p.tagline}</div>
            </div>
            <label className={CHECK}>
              <input
                type="checkbox"
                checked={p.enabled}
                onChange={(e) =>
                  e.target.checked ? toggle(p.id, { enabled: true }) : requestDisable(p)
                }
              />
              Enabled
            </label>
            <label className={CHECK + (p.enabled ? "" : " opacity-40")}>
              <input
                type="checkbox"
                checked={p.surfaced}
                disabled={!p.enabled}
                onChange={(e) => toggle(p.id, { surfaced: e.target.checked })}
              />
              In picker
            </label>
            <button
              className={BTN_BORDERED}
              disabled={p.default || !p.enabled}
              onClick={() => toggle(p.id, { default: true })}
            >
              Set default
            </button>
            {onOpenPersona && (
              <button
                className="text-faint hover:text-ink shrink-0 p-1"
                title={`Configure ${p.name}`}
                aria-label={`Configure ${p.name}`}
                data-testid={`persona-configure-${p.id}`}
                onClick={() => onOpenPersona(p.id)}
              >
                <Icon name="sliders" size={15} />
              </button>
            )}
            {!p.builtin && (
              <button
                className={BTN_BORDERED}
                title="Export this coworker as a shareable bundle"
                data-testid={`persona-export-${p.id}`}
                onClick={() => void exportOne(p)}
              >
                Export…
              </button>
            )}
            {!p.builtin &&
              (confirmDel === p.id ? (
                <span className="flex items-center gap-1.5 shrink-0">
                  <button
                    className="text-[12px] px-2 py-1.5 rounded-lg bg-danger text-white"
                    data-testid={`persona-delete-confirm-${p.id}`}
                    onClick={() => remove(p.id)}
                  >
                    Delete
                  </button>
                  <button className={BTN_BORDERED} onClick={() => setConfirmDel(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button
                  className="text-faint hover:text-danger shrink-0 p-1"
                  title="Delete this coworker"
                  aria-label={`Delete ${p.name}`}
                  data-testid={`persona-delete-${p.id}`}
                  onClick={() => setConfirmDel(p.id)}
                >
                  <Icon name="trash" size={14} />
                </button>
              ))}
            </div>
            {confirmOff === p.id && (
              <div
                className="mt-2 flex items-center gap-2.5 text-[12px] text-muted"
                data-testid={`persona-disable-warning-${p.id}`}
              >
                <span className="min-w-0">
                  Disabling archives its {liveCount(p.id)} conversation
                  {liveCount(p.id) === 1 ? "" : "s"} — they stay available under “Show
                  archived”.
                </span>
                <button
                  className="text-[12px] px-2.5 py-1.5 rounded-lg bg-accent text-white shrink-0"
                  data-testid={`persona-disable-confirm-${p.id}`}
                  onClick={() => {
                    setConfirmOff(null);
                    toggle(p.id, { enabled: false });
                  }}
                >
                  Disable
                </button>
                <button className={BTN_BORDERED} onClick={() => setConfirmOff(null)}>
                  Keep enabled
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div ref={addRef} className={SEC_H + " mb-1.5"}>Add coworkers</div>
      <p className="text-[12px] text-muted mb-3 leading-relaxed">
        Load from a local directory or a public GitHub repo. Files are copied into a managed area (a
        snapshot), so the coworker stays stable even if the source changes. No code runs — a coworker only
        composes vetted tools.
      </p>
      <div className="flex items-center gap-2">
        <select
          className={SELECT}
          value={mode}
          onChange={(e) => setMode(e.target.value as "git" | "dir" | "zip")}
        >
          <option value="git">GitHub URL</option>
          <option value="dir">Local directory</option>
          <option value="zip">Bundle zip</option>
        </select>
        {mode === "zip" ? (
          <label className={BTN_BORDERED + " cursor-pointer"}>
            {busy ? "Installing…" : "Choose a .zip bundle…"}
            <input
              type="file"
              accept=".zip"
              className="hidden"
              data-testid="persona-zip-input"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void installZip(f);
                e.target.value = "";
              }}
            />
          </label>
        ) : (
          <>
            <input
              className={INPUT}
              placeholder={mode === "git" ? "https://github.com/acme/ops-coworker" : "/path/to/coworkers"}
              value={src}
              onChange={(e) => setSrc(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && install()}
            />
            <button className={BTN_ACCENT} disabled={busy || !src.trim()} onClick={install}>
              {busy ? "Installing…" : "Install"}
            </button>
          </>
        )}
      </div>
      {msg && <div className="text-[12.5px] text-muted mt-2.5">{msg}</div>}

      {consent && consent.length > 0 && (
        <div className="mt-4 space-y-2" data-testid="consent-review">
          {/* Trust first (owner design, 2026-08-11): the source warning leads; capabilities
              are a one-line summary with the exact tools under a collapsed chevron. A
              coworker runs no third-party code, so this list is complete — but a prompt
              still steers an agent, so who it came from genuinely matters. */}
          <div className="flex items-start gap-2.5 rounded-xl border border-warnInk/30 bg-warnSoft px-3.5 py-2.5 text-[12.5px] text-warnInk">
            <Icon name="shield" size={15} className="shrink-0 mt-0.5" />
            <span>
              Only enable coworkers from someone you trust. Nothing here runs third-party
              code — but its instructions will guide the coworker's behavior.
            </span>
          </div>
          {consent.map((c) => (
            <ConsentCard
              key={c.id}
              c={c}
              enabled={personas.find((p) => p.id === c.id)?.enabled ?? false}
              onEnable={async () => {
                await toggle(c.id, { enabled: true, surfaced: true });
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// One phrase per risk class — the plain-language capability summary the consent card leads
// with; unknown classes fall back to their raw id so nothing is silently omitted.
const RISK_PHRASE: Record<string, string> = {
  read: "read files",
  write_local: "create & edit files",
  exec: "run shell commands",
  network: "access the network",
  write_remote: "act on connected services",
};

function ConsentCard({
  c,
  enabled,
  onEnable,
}: {
  c: PersonaConsent;
  enabled: boolean;
  onEnable: () => Promise<void>;
}) {
  const [showTools, setShowTools] = useState(false);
  const [busy, setBusy] = useState(false);
  const phrases = (c.risk.length ? c.risk : ["read"]).map((r) => RISK_PHRASE[r] || r);
  const summary = phrases.join(", ").replace(/, ([^,]*)$/, " and $1");
  const recommends = c.recommends || [];
  return (
    <div className={CARD + " p-3.5"} data-testid={`consent-${c.id}`}>
      <div className="text-[13.5px] font-medium flex items-center gap-2">
        <span>{c.name}</span>
        {c.version && <span className="text-[11px] text-faint font-normal">v{c.version}</span>}
      </div>
      {c.description && <div className="text-[12px] text-muted mt-0.5">{c.description}</div>}
      {c.replaces && (
        <div className="text-[12px] text-muted mt-1.5" data-testid="replaces-note">
          Replaces {c.name}
          {c.replaces.version ? ` v${c.replaces.version}` : ""}
          {c.replaces.installed_at ? ` (installed ${c.replaces.installed_at})` : ""}.
          {c.replaces.capabilities_grew
            ? " This update asks for MORE capabilities than the copy it replaces — review below before re-enabling."
            : " Same capabilities as before — it stays enabled."}
        </div>
      )}
      <div className="text-[12.5px] text-ink mt-2">
        Can {summary}
        {c.connectors === "all"
          ? " · use ALL your connected services"
          : c.connectors.length
            ? ` · use connectors: ${c.connectors.join(", ")}`
            : ""}
        {c.messaging ? " · send messages" : ""}
        {c.mcp.length ? ` · use MCP: ${c.mcp.join(", ")}` : ""}
        <button
          className="ml-2 text-accent text-[12px] hover:underline"
          data-testid="consent-tools-toggle"
          onClick={() => setShowTools((v) => !v)}
        >
          {showTools ? "Hide tools" : `Exact tools (${c.tools.length})`}
        </button>
      </div>
      {showTools && (
        <div className="text-[12px] text-muted mt-1 font-mono">{c.tools.join(" · ") || "—"}</div>
      )}
      {recommends.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {recommends.map((r) => (
            <div key={r.kind + r.ref} className="text-[12px] text-muted">
              <span className="text-ink">{r.ref}</span>
              {r.tier === "core" ? " (recommended)" : " (optional)"} — {r.reason}
            </div>
          ))}
        </div>
      )}
      <div className="flex items-center gap-3 mt-2.5">
        {/* Enable right here (owner ask 2026-08-11) — the old "enable it above" copy
            sent the user hunting back up the list. */}
        {enabled ? (
          <span className="text-[12.5px] text-muted" data-testid="consent-enabled">
            ✓ Enabled — it's in your coworker picker.
          </span>
        ) : (
          <button
            className={BTN_ACCENT}
            data-testid={`consent-enable-${c.id}`}
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void onEnable().finally(() => setBusy(false));
            }}
          >
            {busy ? "Enabling…" : "Enable this coworker"}
          </button>
        )}
        <span className="text-[12px] text-faint">Recommended mode: {c.recommended_mode}.</span>
      </div>
    </div>
  );
}
