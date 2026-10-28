"use client";

import { useState } from "react";
import { ExternalLink, Plus, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Badge, Button, Callout, Card, Checkbox, Field, Input, PageHeader, Select, Spinner, Tabs, Textarea, useToast } from "@/components/ui";

interface Source { provider: string; board: string; enabled: boolean; search_terms: string[]; notes: string }
interface Company { name: string; priority: number; careers_url: string; official_domains: string[]; web_search: boolean | null; sources: Source[]; notes: string }
interface Config {
  companies: Company[];
  web_search: { enabled: boolean; scope: "no_feed" | "all"; max_queries_per_run: number; max_page_fetches_per_run: number };
  roles: { name: string; keywords: string[]; priority: "primary" | "secondary" }[];
  exclude_title_keywords: string[];
  locations: { preferred_cities: string[]; home_country: string; allow_other_home_country_cities: boolean; allow_remote: boolean; remote_regions_eligible: string[]; international_eligible_countries: string[] };
  compensation: { target_annual_lpa: number; fx_to_inr: Record<string, number> };
  freshness: { preferred_hours: number; max_age_days: number };
  weights: Record<string, number>;
  tiers: { a: number; b: number; c: number };
  experience: { hard_exclude_years_gap: number };
  limits: Record<string, number | string>;
}
interface Payload { config: Config; yaml: string; environment: Record<string, string | number | boolean> }

const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

const BOARD_HINT: Record<string, string> = {
  greenhouse: "board token, e.g. stripe", lever: "board token", ashby: "board name", workday: "host|tenant|site",
  eightfold: "host|domain", amazon: "country code, e.g. IND", atlassian: "any label", oracle: "host|siteNumber|locationId",
  google: "location, e.g. India", sitemap: "sitemap_url|url_regex", none: "",
};

export default function SettingsPage() {
  const { data, loading, error, setData } = useApi<Payload>("/api/settings");
  const [tab, setTab] = useState("targets");
  if (loading && !data) return <Spinner />;
  if (error && !data) return <Callout tone="danger">{error}</Callout>;
  if (!data) return null;
  // Keyed by the saved YAML so the editor state resets from server data after each save.
  return <SettingsEditor key={data.yaml} data={data} onSaved={setData} tab={tab} setTab={setTab} />;
}

function SettingsEditor({ data, onSaved, tab, setTab }: { data: Payload; onSaved: (p: Payload) => void; tab: string; setTab: (t: string) => void }) {
  const toast = useToast();
  const [cfg, setCfg] = useState<Config>(() => structuredClone(data.config));
  const [yaml, setYaml] = useState(data.yaml);
  const [saving, setSaving] = useState(false);
  const [newCo, setNewCo] = useState({ name: "", priority: "2", provider: "none", board: "", careers_url: "", domains: "" });

  async function save(body: object) {
    setSaving(true);
    try { onSaved(await api.put<Payload>("/api/settings", body)); toast("ok", "Settings saved. Jobs are re-scored on the next ranking run."); }
    catch (e) { toast("danger", e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }
  const set = (patch: Partial<Config>) => setCfg({ ...cfg, ...patch });
  const dirty = JSON.stringify(cfg) !== JSON.stringify(data.config);
  const env = data.environment;
  const weightSum = ["skills", "role_fit", "experience", "compensation", "company", "location", "freshness"].reduce((s, k) => s + Number(cfg.weights[k] || 0), 0);

  return (
    <>
      <PageHeader title="Search settings" description="Target companies, roles, locations, compensation and scoring. Stored in data/search.yaml."
                  actions={tab !== "yaml" && <Button variant="primary" icon={<Save className="size-4" />} disabled={!dirty} loading={saving} onClick={() => save({ config: cfg })}>Save changes</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "targets", label: "Targets" }, { id: "companies", label: `Companies (${cfg.companies.length})` },
        { id: "scoring", label: "Scoring & limits" }, { id: "yaml", label: "Raw YAML" }, { id: "env", label: "Environment" }]} />
      <div className="pt-4">
        {tab === "targets" && (
          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Compensation">
              <Field label="Preferred minimum (₹ LPA, annual)" htmlFor="lpa" hint="Undisclosed salaries are never rejected; they are flagged as unverified.">
                <Input id="lpa" type="number" min={0} step={1} value={cfg.compensation.target_annual_lpa}
                       onChange={(e) => set({ compensation: { ...cfg.compensation, target_annual_lpa: Number(e.target.value) } })} />
              </Field>
            </Card>
            <Card title="Locations">
              <div className="grid gap-3">
                <Field label="Preferred cities (comma-separated)" htmlFor="cities">
                  <Input id="cities" defaultValue={cfg.locations.preferred_cities.join(", ")} onBlur={(e) => set({ locations: { ...cfg.locations, preferred_cities: list(e.target.value) } })} />
                </Field>
                <Field label="Home country" htmlFor="home"><Input id="home" value={cfg.locations.home_country} onChange={(e) => set({ locations: { ...cfg.locations, home_country: e.target.value } })} /></Field>
                <Field label="Other countries where you are authorised to work" htmlFor="intl" hint="Leave empty unless you are eligible.">
                  <Input id="intl" defaultValue={cfg.locations.international_eligible_countries.join(", ")} onBlur={(e) => set({ locations: { ...cfg.locations, international_eligible_countries: list(e.target.value) } })} />
                </Field>
                <Checkbox checked={cfg.locations.allow_remote} onChange={(v) => set({ locations: { ...cfg.locations, allow_remote: v } })} label="Include remote roles open to my region" />
                <Checkbox checked={cfg.locations.allow_other_home_country_cities} onChange={(v) => set({ locations: { ...cfg.locations, allow_other_home_country_cities: v } })} label="Include other cities in my home country" />
              </div>
            </Card>
            <Card title="Roles" subtitle="Title keywords per role family. Primary families score higher than secondary.">
              <div className="space-y-3">
                {cfg.roles.map((r, i) => (
                  <div key={i} className="grid grid-cols-[1fr_auto] gap-2">
                    <Field label={r.name} htmlFor={`role-${i}`}>
                      <Input id={`role-${i}`} defaultValue={r.keywords.join(", ")} onBlur={(e) => { const roles = [...cfg.roles]; roles[i] = { ...r, keywords: list(e.target.value) }; set({ roles }); }} />
                    </Field>
                    <Field label="Priority" htmlFor={`rp-${i}`}>
                      <Select id={`rp-${i}`} value={r.priority} onChange={(e) => { const roles = [...cfg.roles]; roles[i] = { ...r, priority: e.target.value as "primary" | "secondary" }; set({ roles }); }}>
                        <option value="primary">primary</option><option value="secondary">secondary</option></Select>
                    </Field>
                  </div>
                ))}
              </div>
            </Card>
            <Card title="Web search (Gemini + Google Search)" subtitle="Finds postings on official career sites that have no readable feed. Uses your daily AI budget.">
              <div className="grid gap-3">
                <Checkbox checked={cfg.web_search.enabled} onChange={(v) => set({ web_search: { ...cfg.web_search, enabled: v } })} label="Enable web search during discovery" />
                <Field label="Search which companies?" htmlFor="ws-scope">
                  <Select id="ws-scope" value={cfg.web_search.scope} onChange={(e) => set({ web_search: { ...cfg.web_search, scope: e.target.value as "no_feed" | "all" } })}>
                    <option value="no_feed">Only companies without an enabled feed (cheapest)</option>
                    <option value="all">Every company with official domains</option>
                  </Select>
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Max searches per run" htmlFor="ws-q"><Input id="ws-q" type="number" min={0} value={cfg.web_search.max_queries_per_run} onChange={(e) => set({ web_search: { ...cfg.web_search, max_queries_per_run: Number(e.target.value) } })} /></Field>
                  <Field label="Max page reads per run" htmlFor="ws-f"><Input id="ws-f" type="number" min={0} value={cfg.web_search.max_page_fetches_per_run} onChange={(e) => set({ web_search: { ...cfg.web_search, max_page_fetches_per_run: Number(e.target.value) } })} /></Field>
                </div>
              </div>
            </Card>
            <Card title="Exclusions & freshness">
              <div className="grid gap-3">
                <Field label="Exclude titles containing" htmlFor="excl"><Input id="excl" defaultValue={cfg.exclude_title_keywords.join(", ")} onBlur={(e) => set({ exclude_title_keywords: list(e.target.value) })} /></Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Preferred freshness (hours)" htmlFor="fh"><Input id="fh" type="number" value={cfg.freshness.preferred_hours} onChange={(e) => set({ freshness: { ...cfg.freshness, preferred_hours: Number(e.target.value) } })} /></Field>
                  <Field label="Ignore postings older than (days)" htmlFor="fd"><Input id="fd" type="number" value={cfg.freshness.max_age_days} onChange={(e) => set({ freshness: { ...cfg.freshness, max_age_days: Number(e.target.value) } })} /></Field>
                </div>
                <Field label="Hard-exclude when required years exceed mine by" htmlFor="gap"><Input id="gap" type="number" step={0.5} value={cfg.experience.hard_exclude_years_gap} onChange={(e) => set({ experience: { hard_exclude_years_gap: Number(e.target.value) } })} /></Field>
              </div>
            </Card>
          </div>
        )}

        {tab === "companies" && (
          <div className="space-y-4">
            <Card title="Add a company">
              <div className="grid gap-3 md:grid-cols-7">
                <Field label="Name" htmlFor="nc-n"><Input id="nc-n" value={newCo.name} onChange={(e) => setNewCo({ ...newCo, name: e.target.value })} /></Field>
                <Field label="Priority" htmlFor="nc-p"><Select id="nc-p" value={newCo.priority} onChange={(e) => setNewCo({ ...newCo, priority: e.target.value })}><option value="1">1 (highest)</option><option value="2">2</option><option value="3">3</option></Select></Field>
                <Field label="Source" htmlFor="nc-s"><Select id="nc-s" value={newCo.provider} onChange={(e) => setNewCo({ ...newCo, provider: e.target.value })}>
                  <option value="none">Web search only</option><option value="greenhouse">Greenhouse</option><option value="lever">Lever</option>
                  <option value="ashby">Ashby</option><option value="workday">Workday</option><option value="eightfold">Eightfold</option>
                  <option value="oracle">Oracle Recruiting</option><option value="sitemap">Sitemap + JSON-LD</option></Select></Field>
                <Field label="Board id" htmlFor="nc-b" hint={BOARD_HINT[newCo.provider]}><Input id="nc-b" value={newCo.board} onChange={(e) => setNewCo({ ...newCo, board: e.target.value })} disabled={newCo.provider === "none"} /></Field>
                <Field label="Official domains" htmlFor="nc-d" hint="for web search, e.g. careers.example.com"><Input id="nc-d" value={newCo.domains} onChange={(e) => setNewCo({ ...newCo, domains: e.target.value })} /></Field>
                <Field label="Careers URL" htmlFor="nc-u"><Input id="nc-u" value={newCo.careers_url} onChange={(e) => setNewCo({ ...newCo, careers_url: e.target.value })} /></Field>
                <div className="flex items-end"><Button icon={<Plus className="size-4" />} disabled={!newCo.name || (newCo.provider !== "none" && !newCo.board)} onClick={() => {
                  set({ companies: [...cfg.companies, { name: newCo.name, priority: Number(newCo.priority), careers_url: newCo.careers_url, notes: "",
                    official_domains: list(newCo.domains), web_search: null,
                    sources: newCo.provider === "none" ? [] : [{ provider: newCo.provider, board: newCo.board, enabled: true,
                      search_terms: ["workday", "eightfold", "oracle"].includes(newCo.provider) ? ["software engineer"] : [], notes: "" }] }] });
                  setNewCo({ name: "", priority: "2", provider: "none", board: "", careers_url: "", domains: "" });
                }}>Add</Button></div>
              </div>
            </Card>
            <div className="overflow-x-auto rounded-xl border border-ink-200 bg-white">
              <table className="w-full text-sm">
                <caption className="sr-only">Target companies</caption>
                <thead className="bg-ink-50 text-left text-xs text-ink-500"><tr>
                  <th className="px-3 py-2 font-medium">Company</th><th className="px-3 py-2 font-medium">Priority</th>
                  <th className="px-3 py-2 font-medium">Sources</th><th className="px-3 py-2 font-medium">Notes</th><th className="px-3 py-2"><span className="sr-only">Remove</span></th></tr></thead>
                <tbody className="divide-y divide-ink-100">
                  {cfg.companies.map((c, i) => (
                    <tr key={`${c.name}-${i}`}>
                      <td className="px-3 py-2 font-medium">{c.careers_url ? <a href={c.careers_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:underline">{c.name}<ExternalLink className="size-3" /></a> : c.name}</td>
                      <td className="px-3 py-2"><Select aria-label={`Priority for ${c.name}`} value={c.priority} className="w-20 py-1"
                        onChange={(e) => { const companies = [...cfg.companies]; companies[i] = { ...c, priority: Number(e.target.value) }; set({ companies }); }}>
                        <option value={1}>1</option><option value={2}>2</option><option value={3}>3</option></Select></td>
                      <td className="px-3 py-2">
                        {c.sources.length === 0 ? <Badge tone="violet">web search</Badge> : c.sources.map((s, j) => (
                          <label key={j} className="mr-3 inline-flex items-center gap-1.5 text-xs">
                            <input type="checkbox" className="accent-accent-600" checked={s.enabled} onChange={(e) => {
                              const companies = [...cfg.companies]; const sources = [...c.sources]; sources[j] = { ...s, enabled: e.target.checked };
                              companies[i] = { ...c, sources }; set({ companies }); }} />
                            {s.provider}: <span className="font-mono">{s.board.split("|")[0]}</span>
                          </label>
                        ))}
                      </td>
                      <td className="max-w-xs px-3 py-2 text-xs text-ink-500">{c.notes || c.sources.map((s) => s.notes).filter(Boolean).join("; ")}</td>
                      <td className="px-3 py-2 text-right"><button aria-label={`Remove ${c.name}`} className="rounded p-1 text-ink-500 hover:bg-rose-50 hover:text-rose-700"
                        onClick={() => set({ companies: cfg.companies.filter((_, k) => k !== i) })}><Trash2 className="size-4" /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "scoring" && (
          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Score weights" subtitle={`Factor weights are normalised (current sum ${weightSum.toFixed(2)}).`}>
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(cfg.weights).map(([k, v]) => (
                  <Field key={k} label={k.replace(/_/g, " ")} htmlFor={`w-${k}`} hint={k === "ai_weight" ? "Share of final score from the AI assessment" : k === "required_skill_share" ? "Required vs preferred skills" : undefined}>
                    <Input id={`w-${k}`} type="number" step={0.01} min={0} max={1} value={v} onChange={(e) => set({ weights: { ...cfg.weights, [k]: Number(e.target.value) } })} />
                  </Field>
                ))}
              </div>
            </Card>
            <div className="flex flex-col gap-5">
              <Card title="Tier thresholds">
                <div className="grid grid-cols-3 gap-3">
                  {(["a", "b", "c"] as const).map((t) => (
                    <Field key={t} label={`Tier ${t.toUpperCase()} ≥`} htmlFor={`t-${t}`}><Input id={`t-${t}`} type="number" value={cfg.tiers[t]} onChange={(e) => set({ tiers: { ...cfg.tiers, [t]: Number(e.target.value) } })} /></Field>
                  ))}
                </div>
              </Card>
              <Card title="Daily processing limits" subtitle="Model-call budget limits (calls/day, USD/day) are set in .env.">
                <div className="grid grid-cols-2 gap-3">
                  {Object.entries(cfg.limits).map(([k, v]) => (
                    <Field key={k} label={k.replace(/_/g, " ")} htmlFor={`l-${k}`}>
                      {typeof v === "number" ? <Input id={`l-${k}`} type="number" value={v} onChange={(e) => set({ limits: { ...cfg.limits, [k]: Number(e.target.value) } })} />
                        : <Select id={`l-${k}`} value={v} onChange={(e) => set({ limits: { ...cfg.limits, [k]: e.target.value } })}>{["A", "B", "C"].map((x) => <option key={x}>{x}</option>)}</Select>}
                    </Field>
                  ))}
                </div>
              </Card>
            </div>
          </div>
        )}

        {tab === "yaml" && (
          <Card title="search.yaml" actions={<Button variant="primary" size="sm" icon={<Save className="size-3.5" />} loading={saving} disabled={yaml === data.yaml} onClick={() => save({ yaml })}>Validate & save</Button>}>
            <Textarea aria-label="Search settings YAML" spellCheck={false} rows={36} value={yaml} onChange={(e) => setYaml(e.target.value)} />
          </Card>
        )}

        {tab === "env" && (
          <Card title="Server environment (read-only)" subtitle="Secrets are never displayed. Change these in .env or Secret Manager.">
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
              {Object.entries(env).map(([k, v]) => <div key={k} className="contents"><dt className="text-ink-500">{k.replace(/_/g, " ")}</dt><dd className="font-mono text-xs">{String(v)}</dd></div>)}
            </dl>
          </Card>
        )}
      </div>
    </>
  );
}
