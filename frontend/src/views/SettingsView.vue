<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue"
import {
  AlertCircle,
  CheckCircle2,
  Database,
  FileText,
  KeyRound,
  Linkedin,
  Loader2,
  Plug,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Unplug,
} from "lucide-vue-next"

import AppSelect from "@/components/AppSelect.vue"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"
import {
  clearLinkedInSessionStore,
  connectLinkedInSession,
  linkedinSessionState,
  refreshLinkedInSession,
} from "@/lib/linkedin-session"

const fallbackOptions = computed(() => [
  { value: "", label: "Disabled" },
  ...primaryOptions.value.filter((option) => option.value !== state.form.primary_provider),
])

const primaryOptions = computed(() => {
  const configured = state.providers.filter((provider) => provider.configured)
  if (configured.length === 0) {
    // While we wait for the registry to load, show whatever the
    // settings file currently points to so the select isn't empty.
    return [{ value: state.form.primary_provider, label: state.form.primary_provider || "..." }]
  }
  return configured.map((provider) => ({
    value: provider.id,
    label: provider.display_name,
  }))
})

// Phase 17.9.8: split the registry into "what I've already set up"
// vs "what I could connect" so the list doesn't run off the screen
// with 13+ builtins + user-defined providers.
const connectedProviders = computed(() =>
  state.providers.filter((p) => p.configured),
)
const availableProviders = computed(() =>
  state.providers.filter((p) => !p.configured),
)
const visibleConnectedProviders = computed(() => {
  if (providerListUi.showAllConnected) return connectedProviders.value
  return connectedProviders.value.slice(0, CONNECTED_DEFAULT_VISIBLE)
})
const hiddenConnectedCount = computed(() =>
  Math.max(0, connectedProviders.value.length - CONNECTED_DEFAULT_VISIBLE),
)

// Group rows into labelled sections so the same provider row template
// renders inside two distinct headers. Empty sections fall out of the
// list entirely (a fresh install has no Connected section, a fully
// configured install has no Available section).
const providerSections = computed(() => {
  const sections = []
  if (connectedProviders.value.length > 0) {
    sections.push({
      key: "connected",
      title: "Connected",
      items: visibleConnectedProviders.value,
      empty: false,
    })
  }
  if (availableProviders.value.length > 0) {
    sections.push({
      key: "available",
      title: "Available",
      items: providerListUi.showAvailable ? availableProviders.value : [],
      empty: !providerListUi.showAvailable,
    })
  }
  return sections
})

const state = reactive({
  loading: true,
  saving: false,
  suspendAutosave: false,
  error: "",
  message: "",
  cache: {
    clearing: false,
  },
  providers: [],
  providerOps: reactive({}), // { [providerId]: { testing, connecting, disconnecting, using } }
  // Phase 11.4: live health snapshot from the background monitor.
  // Map of provider_id -> { ok, detail, latency_ms, checked_at }.
  providerHealth: reactive({}),
  providerHealthMeta: reactive({
    last_run_finished_at: "",
    interval_seconds: 300,
    running: false,
    refreshing: false,
  }),
  data: {
    llm: {
      primary_provider: "claude-cli",
      fallback_provider: null,
      allow_fallback: false,
    },
    available_providers: {
      "claude-cli": false,
      "codex-cli": false,
    },
    search_cache: {
      enabled: true,
      ttl_hours: 24,
    },
    job_index: {
      known: false,
      search_queries: 0,
      job_postings: 0,
      job_snapshots: 0,
      latest_success_at: null,
      states: {},
    },
    config_path: "",
  },
  form: {
    primary_provider: "claude-cli",
    fallback_provider: "",
    allow_fallback: false,
    cache_enabled: true,
    cache_ttl_hours: 24,
    // Phase 17.9.9: small-tier knobs. "" means "disabled" -> serialised
    // as small_tier_action='clear' on save so we delete the keys.
    small_provider: "",
    small_model: "",
    // Phase 17.9.11: primary model selection. Mirrors
    // `credentials.metadata.model` on the primary provider. Empty =
    // use that provider's default_model.
    primary_model: "",
  },
  // Phase 17.9.9 + 17.9.11: model catalog cache keyed by provider id.
  // Shared by the Primary model dropdown, the Small-tier model
  // dropdown, and the Connect dialog so the same catalog fetch isn't
  // repeated for each picker that touches the same provider.
  modelCatalogs: reactive({}),
  modelCatalogsLoading: false,
  // Phase 17.8: per-document-type material strategy defaults.
  materialDefaults: {
    loading: true,
    saving: false,
    error: "",
    message: "",
    templates: { resume: [], cover_letter: [] },
    documents: { resume: [], cover_letter: [] },
    form: {
      resume: {
        strategy: "regenerate",
        default_template_id: "",
        default_document_id: "",
        patch_aggressiveness: "balanced",
        patch_allow_reorder_sections: true,
        patch_allow_add_remove_bullets: true,
      },
      cover_letter: {
        strategy: "regenerate",
        default_template_id: "",
        default_document_id: "",
        patch_aggressiveness: "balanced",
        patch_allow_reorder_sections: true,
        patch_allow_add_remove_bullets: true,
      },
    },
  },
})

const connectDialog = reactive({
  open: false,
  providerId: "",
  providerLabel: "",
  apiKey: "",
  // Phase 17.9.4 + 17.9.11: catalog-only model picker. `models` is
  // the dropdown content; `modelSelection` is the currently picked
  // id. Catalog-outside ids are not entered here -- use the CLI
  // `autoapply provider set-model` for that path.
  modelSelection: "",
  models: [],
  modelsLoading: false,
  modelsSource: "catalog",
  defaultModel: "",
  baseUrl: "",
  // Whether the provider permits an empty API key (Ollama today).
  allowEmptyKey: false,
  // "Advanced" panel: base_url lives here so the default dialog stays
  // a one-glance API-key + model picker.
  showAdvanced: false,
  submitting: false,
  error: "",
})

// Phase 17.9.8: with the registry now shipping 13+ builtin providers
// plus user-defined ones, the full flat list runs off the screen on a
// laptop. The Settings page splits into:
//   - Connected: always visible; if more than CONNECTED_DEFAULT_VISIBLE
//     are connected, the overflow collapses behind "Show N more".
//   - Available (not yet connected): hidden behind a single toggle so
//     a clean install still sees the full list, but a power user with
//     three keys doesn't have to scroll past Mistral / Grok / etc.
const CONNECTED_DEFAULT_VISIBLE = 5
const providerListUi = reactive({
  showAllConnected: false,
  showAvailable: false,
})


function providerOp(providerId) {
  if (!state.providerOps[providerId]) {
    state.providerOps[providerId] = {
      testing: false,
      connecting: false,
      disconnecting: false,
      using: false,
    }
  }
  return state.providerOps[providerId]
}

function authTypeLabel(authType) {
  return (
    {
      api_key: "API key",
      oauth: "OAuth",
      subprocess: "Local CLI",
    }[authType] || authType
  )
}

function syncForm() {
  state.form.primary_provider = state.data.llm.primary_provider
  state.form.fallback_provider = state.data.llm.fallback_provider || ""
  state.form.allow_fallback = Boolean(state.data.llm.allow_fallback)
  state.form.cache_enabled = Boolean(state.data.search_cache?.enabled)
  state.form.cache_ttl_hours = state.data.search_cache?.ttl_hours ?? 24
  // Phase 17.9.9: small-tier knobs. "" in the dropdown means
  // "disabled" -> we'll send small_tier_action='clear' on save.
  state.form.small_provider = state.data.llm.small_provider || ""
  state.form.small_model = state.data.llm.small_model || ""
  // Phase 17.9.11: read the primary provider's saved model so the
  // Primary model dropdown lands on the right entry.
  const primaryRow = (state.providers || []).find(
    (p) => p.id === state.form.primary_provider,
  )
  state.form.primary_model = primaryRow?.credentials?.metadata?.model || ""
}

async function loadSettings() {
  state.suspendAutosave = true
  try {
    state.data = await api.settings()
    syncForm()
    // Preload catalogs for whichever providers the form is currently
    // pointing at, so the dropdowns are ready the moment the user
    // opens the section. Small-tier (17.9.9) + primary (17.9.11) share
    // the same cache.
    if (state.form.small_provider) {
      void fetchProviderCatalog(state.form.small_provider)
    }
    if (state.form.primary_provider) {
      void fetchProviderCatalog(state.form.primary_provider)
    }
  } catch (error) {
    state.error = error.message
  } finally {
    state.suspendAutosave = false
  }
}

async function loadProviders() {
  try {
    const payload = await api.providers()
    if (!payload.ok) {
      state.error = payload.error || "Failed to load providers."
      return
    }
    state.providers = payload.providers || []
  } catch (error) {
    state.error = error.message
  }
}

async function loadProviderHealth() {
  // Phase 11.4: pull the cached background-probe snapshot. Failures
  // are swallowed -- the Settings page still works without health data,
  // and the monitor might just not have run a tick yet.
  try {
    const snapshot = await api.providersHealth()
    state.providerHealth = snapshot.records || {}
    state.providerHealthMeta.last_run_finished_at = snapshot.last_run_finished_at || ""
    state.providerHealthMeta.interval_seconds = snapshot.interval_seconds ?? 300
    state.providerHealthMeta.running = Boolean(snapshot.running)
  } catch (_error) {
    // Non-fatal -- the credential timestamp still renders as a fallback.
  }
}

async function refreshProviderHealth() {
  if (state.providerHealthMeta.refreshing) return
  state.providerHealthMeta.refreshing = true
  try {
    const snapshot = await api.refreshProvidersHealth()
    state.providerHealth = snapshot.records || {}
    state.providerHealthMeta.last_run_finished_at = snapshot.last_run_finished_at || ""
    state.providerHealthMeta.running = Boolean(snapshot.running)
  } catch (error) {
    state.error = error.message
  } finally {
    state.providerHealthMeta.refreshing = false
  }
}

function providerHealthFor(provider) {
  return state.providerHealth?.[provider.id] || null
}

async function refreshAll() {
  state.loading = true
  state.error = ""
  await Promise.all([
    loadSettings(),
    loadProviders(),
    loadProviderHealth(),
    loadMaterialDefaults(),
  ])
  state.loading = false
}

const MATERIAL_STRATEGY_OPTIONS = [
  { value: "regenerate", label: "Regenerate from a template" },
  { value: "patch_existing", label: "Patch a document from my library" },
  { value: "use_library", label: "Use library document as-is (no edits)" },
]

const PATCH_AGGRESSIVENESS_OPTIONS = [
  {
    value: "conservative",
    label: "Conservative · barely touch the wording",
  },
  {
    value: "balanced",
    label: "Balanced · sensible rewriting (recommended)",
  },
  {
    value: "aggressive",
    label: "Aggressive · rewrite freely to match the JD",
  },
]

function materialTemplateOptions(docType) {
  return [
    { value: "", label: "System default" },
    ...((state.materialDefaults.templates[docType] || []).map((tpl) => ({
      value: tpl.template_id,
      label: tpl.name || tpl.template_id,
    }))),
  ]
}

function materialDocumentOptions(docType) {
  const docs = state.materialDefaults.documents[docType] || []
  return [
    {
      value: "",
      label: docs.length
        ? "Pick a document from your library"
        : "No editable documents in your library",
    },
    ...docs.map((doc) => ({
      value: doc.id,
      label: `${doc.display_name} · ${doc.source_type.toUpperCase()}`,
    })),
  ]
}

function materialDocTypeLabel(docType) {
  return docType === "resume" ? "Resume" : "Cover Letter"
}

async function loadMaterialDefaults() {
  state.materialDefaults.loading = true
  state.materialDefaults.error = ""
  try {
    const [defaults, templates, documents] = await Promise.all([
      api.materialDefaults(),
      api.templates(),
      api.documents(),
    ])
    state.materialDefaults.templates = {
      resume: templates?.templates?.resume || [],
      cover_letter: templates?.templates?.cover_letter || [],
    }
    const docs = documents?.documents || []
    state.materialDefaults.documents = {
      resume: docs.filter((d) => d.document_type === "resume" && d.editable),
      cover_letter: docs.filter((d) => d.document_type === "cover_letter" && d.editable),
    }
    const loaded = defaults?.defaults || {}
    for (const docType of ["resume", "cover_letter"]) {
      const entry = loaded[docType] || {}
      state.materialDefaults.form[docType] = {
        strategy: entry.strategy || "regenerate",
        default_template_id: entry.default_template_id || "",
        default_document_id: entry.default_document_id || "",
        patch_aggressiveness: entry.patch_aggressiveness || "balanced",
        // Server returns explicit booleans; fall back to documented
        // defaults if the field is missing (older config files).
        patch_allow_reorder_sections:
          entry.patch_allow_reorder_sections ?? true,
        patch_allow_add_remove_bullets:
          entry.patch_allow_add_remove_bullets ?? true,
      }
    }
  } catch (err) {
    state.materialDefaults.error = err?.message || "Couldn't load material defaults."
  } finally {
    state.materialDefaults.loading = false
  }
}

async function saveMaterialDefaults() {
  state.materialDefaults.saving = true
  state.materialDefaults.error = ""
  state.materialDefaults.message = ""
  try {
    await api.updateMaterialDefaults({
      resume: state.materialDefaults.form.resume,
      cover_letter: state.materialDefaults.form.cover_letter,
    })
    state.materialDefaults.message = "Saved."
  } catch (err) {
    state.materialDefaults.error = err?.message || "Couldn't save material defaults."
  } finally {
    state.materialDefaults.saving = false
  }
}

async function persistSettings({ keepMessage = false } = {}) {
  if (state.loading || state.suspendAutosave) {
    return
  }
  state.saving = true
  state.error = ""
  if (!keepMessage) state.message = ""
  try {
    // Phase 17.9.9: serialise the small-tier knobs. Empty provider is
    // "disabled" and translates to small_tier_action='clear' so the
    // backend removes the keys from settings.yaml rather than writing
    // an explicit null (which would still be honoured but clutters
    // the file).
    const hasSmallTier = Boolean(state.form.small_provider)
    state.data = await api.updateSettings({
      primary_provider: state.form.primary_provider,
      fallback_provider: state.form.fallback_provider || null,
      allow_fallback: state.form.allow_fallback,
      cache_enabled: state.form.cache_enabled,
      cache_ttl_hours: Number(state.form.cache_ttl_hours) || 24,
      small_provider: hasSmallTier ? state.form.small_provider : null,
      small_model: hasSmallTier
        ? state.form.small_model || null
        : null,
      small_tier_action: hasSmallTier ? "set" : "clear",
    })
    state.suspendAutosave = true
    syncForm()
    state.message = state.data.message || "Settings updated"
  } catch (error) {
    state.error = error.message
  } finally {
    state.suspendAutosave = false
    state.saving = false
  }
}

function openConnectDialog(provider) {
  connectDialog.providerId = provider.id
  connectDialog.providerLabel = provider.display_name
  connectDialog.apiKey = ""
  const savedModel = provider.credentials?.metadata?.model || ""
  connectDialog.baseUrl = provider.credentials?.metadata?.base_url || ""
  connectDialog.allowEmptyKey = Boolean(provider.allow_empty_key)
  connectDialog.showAdvanced = Boolean(connectDialog.baseUrl)
  connectDialog.error = ""
  connectDialog.submitting = false
  // Seed the picker from the provider's own KNOWN_MODELS so the dialog
  // renders something usable even before the async catalog call lands.
  const seed = (provider.known_models || []).map((m) => ({ ...m }))
  connectDialog.models = seed
  connectDialog.modelsSource = "catalog"
  connectDialog.defaultModel = ""
  // If the user already had a saved model that's not in the seed,
  // append it so the picker shows the current selection rather than
  // silently switching them to a different default.
  if (savedModel && !seed.some((m) => m.id === savedModel)) {
    connectDialog.models.push({ id: savedModel, display_name: `${savedModel} (saved)` })
  }
  connectDialog.modelSelection = savedModel || seed[0]?.id || ""
  connectDialog.open = true
  // Fire off the catalog call -- runtime providers (Ollama) populate
  // their list from /api/tags, and even cloud providers will see a
  // canonical `default_model` flagged on the response.
  loadProviderModels(provider.id)
}

async function loadProviderModels(providerId) {
  // Connect dialog wraps the shared catalog loader so the dropdown
  // reflects any runtime-discovered models (Ollama /api/tags etc).
  connectDialog.modelsLoading = true
  try {
    const result = await fetchProviderCatalog(providerId, { force: true })
    if (!result) return
    connectDialog.models = result.models || []
    connectDialog.modelsSource = result.source || "catalog"
    connectDialog.defaultModel = result.default_model || ""
    // If the saved model id isn't in the catalog, append it as a
    // synthetic entry so the user still sees their current selection
    // highlighted (rather than silently switching them away from it).
    // We deliberately do NOT offer a "Custom..." free-text input here;
    // catalog-outside ids should go through `autoapply provider
    // set-model` so AutoApply's curated lists stay authoritative.
    if (
      connectDialog.modelSelection &&
      !connectDialog.models.some((m) => m.id === connectDialog.modelSelection)
    ) {
      connectDialog.models.push({
        id: connectDialog.modelSelection,
        display_name: `${connectDialog.modelSelection} (saved)`,
      })
    } else if (!connectDialog.modelSelection) {
      connectDialog.modelSelection =
        connectDialog.defaultModel || connectDialog.models[0]?.id || ""
    }
  } catch (_err) {
    // Catalog is non-essential -- the dialog still works with the seed.
  } finally {
    connectDialog.modelsLoading = false
  }
}

function canSubmitConnect() {
  if (connectDialog.submitting) return false
  if (!connectDialog.allowEmptyKey && !connectDialog.apiKey.trim()) return false
  return true
}

async function submitConnect() {
  if (!connectDialog.allowEmptyKey && !connectDialog.apiKey.trim()) {
    connectDialog.error = "API key is required."
    return
  }
  connectDialog.submitting = true
  connectDialog.error = ""
  state.error = ""
  // Phase 17.9.11: picker-only path. The dropdown's currently-selected
  // id IS the model; empty string sends null so the backend falls back
  // to the provider's `default_model`.
  const resolvedModel = connectDialog.modelSelection || ""
  try {
    const payload = await api.connectApiKeyProvider(connectDialog.providerId, {
      api_key: connectDialog.apiKey,
      model: resolvedModel || null,
      base_url: connectDialog.baseUrl || null,
    })
    await loadProviders()
    if (payload.ok) {
      state.message = `${connectDialog.providerLabel} connected and verified.`
      connectDialog.open = false
    } else {
      connectDialog.error =
        payload.error || "Key saved but verification failed. Check the key and try Test again."
    }
  } catch (error) {
    connectDialog.error = error.message
  } finally {
    connectDialog.submitting = false
  }
}

// Phase 17.9.9 + 17.9.11: shared model catalog fetcher. Cache keyed
// by provider id so the Primary picker, the Small-tier picker and
// the Connect dialog don't all re-hit the same endpoint. `force`
// bypasses the cache for the Connect-dialog open path (we want a
// fresh runtime list for Ollama on every dialog open).
async function fetchProviderCatalog(providerId, { force = false } = {}) {
  if (!providerId) return null
  if (!force && state.modelCatalogs[providerId]) {
    return state.modelCatalogs[providerId]
  }
  state.modelCatalogsLoading = true
  try {
    const result = await api.providerModels(providerId)
    if (result?.ok) {
      const cached = {
        models: result.models || [],
        default_model: result.default_model || "",
        source: result.source || "catalog",
      }
      state.modelCatalogs[providerId] = cached
      return cached
    }
  } catch (_err) {
    // Non-fatal -- callers degrade gracefully.
  } finally {
    state.modelCatalogsLoading = false
  }
  return null
}

function onSmallProviderChange() {
  // Switching providers invalidates whatever model id was selected
  // because each provider has its own catalog. Default to that
  // provider's default_model once the catalog lands; until then,
  // wipe so the user doesn't accidentally submit a model from the
  // previous provider.
  state.form.small_model = ""
  if (state.form.small_provider) {
    fetchProviderCatalog(state.form.small_provider).then((cached) => {
      if (cached && !state.form.small_model) {
        state.form.small_model = cached.default_model || ""
      }
    })
  }
}

// Phase 17.9.11: primary-model picker change handler.
async function onPrimaryProviderChange() {
  // When the user switches to a different primary provider, load that
  // provider's catalog AND read whatever model that provider has saved
  // in its credentials.metadata.model so the dropdown reflects what
  // would actually get called. Empty model -> provider's default.
  const pid = state.form.primary_provider
  if (!pid) {
    state.form.primary_model = ""
    return
  }
  // Pull the saved model from the providers list (cheaper than another
  // HTTP call for the same row).
  const provider = state.providers.find((p) => p.id === pid)
  const savedModel = provider?.credentials?.metadata?.model || ""
  state.form.primary_model = savedModel
  const cached = await fetchProviderCatalog(pid)
  if (!state.form.primary_model && cached) {
    state.form.primary_model = cached.default_model || ""
  }
}

async function persistPrimaryModel(newModel, oldModel) {
  // The primary-provider watcher already triggers when the provider
  // itself changes; this handler is only for direct model swaps on
  // the SAME provider. The check on `oldModel` skips the initial sync
  // pass when state.form is being populated from the API response.
  if (oldModel === undefined) return
  const pid = state.form.primary_provider
  if (!pid) return
  // Optimistic: don't block the autosave indicator while we PATCH.
  try {
    await api.setProviderModel(pid, newModel || null)
    // Refresh the providers list so the row's metadata.model reflects
    // the new value (used by the next render of the primary picker).
    await loadProviders()
  } catch (error) {
    state.error = error.message
  }
}

function primaryModelOptions() {
  const pid = state.form.primary_provider
  if (!pid) return []
  return state.modelCatalogs[pid]?.models || []
}

function primaryModelDefault() {
  const pid = state.form.primary_provider
  return state.modelCatalogs[pid]?.default_model || ""
}

// Phase 17.9.12: shape model catalog entries into the
// { value, label } pairs AppSelect expects, with the provider's
// `default_model` annotated and a "(saved)" hint for ids that
// aren't in the curated list but were persisted earlier.
function toAppSelectOptions(modelList, defaultId) {
  if (!Array.isArray(modelList) || modelList.length === 0) return []
  return modelList.map((m) => {
    const label = m.display_name || m.id
    if (m.id === defaultId) {
      return { value: m.id, label: `${label} · default` }
    }
    return { value: m.id, label }
  })
}

function primaryModelSelectOptions() {
  return toAppSelectOptions(primaryModelOptions(), primaryModelDefault())
}

function smallTierModelSelectOptions() {
  if (!state.form.small_provider) return []
  const cached = state.modelCatalogs[state.form.small_provider]
  if (!cached) return []
  return [
    { value: "", label: "Provider default" },
    ...toAppSelectOptions(cached.models, cached.default_model),
  ]
}

function smallTierProviderSelectOptions() {
  return [
    { value: "", label: "Disabled (use primary for everything)" },
    ...connectedProviders.value.map((p) => ({
      value: p.id,
      label: p.display_name,
    })),
  ]
}

// Subprocess providers (claude-cli, codex-cli) own their auth via
// their own login flow and don't expose a model-selection knob. The
// Primary model picker should sit out of the way for those.
function isSubprocessPrimaryProvider() {
  const pid = state.form.primary_provider
  const provider = state.providers.find((p) => p.id === pid)
  return provider?.auth_type === "subprocess"
}

async function testProvider(provider) {
  const op = providerOp(provider.id)
  op.testing = true
  state.error = ""
  state.message = ""
  try {
    const result = await api.testProvider(provider.id)
    await loadProviders()
    if (result.ok) {
      state.message = `${provider.display_name}: ${result.result?.detail || "OK"}`
    } else {
      state.error = `${provider.display_name}: ${result.error || result.result?.detail || "probe failed"}`
    }
  } catch (error) {
    state.error = error.message
  } finally {
    op.testing = false
  }
}

async function disconnectProvider(provider) {
  if (!window.confirm(`Disconnect ${provider.display_name}? The saved credential will be removed.`)) {
    return
  }
  const op = providerOp(provider.id)
  op.disconnecting = true
  state.error = ""
  state.message = ""
  try {
    const result = await api.disconnectProvider(provider.id)
    await loadProviders()
    if (result.ok) {
      state.message = result.message || `Disconnected ${provider.display_name}.`
    } else {
      state.error = result.error
    }
  } catch (error) {
    state.error = error.message
  } finally {
    op.disconnecting = false
  }
}

async function useProvider(provider) {
  const op = providerOp(provider.id)
  op.using = true
  state.error = ""
  state.message = ""
  state.suspendAutosave = true
  try {
    const result = await api.useProvider(provider.id, null)
    if (result.ok) {
      state.message = result.message
      await loadSettings()
    } else {
      state.error = result.error
    }
  } catch (error) {
    state.error = error.message
  } finally {
    op.using = false
    state.suspendAutosave = false
  }
}

async function clearSearchCache() {
  state.cache.clearing = true
  state.error = ""
  try {
    state.data = await api.clearSearchCache()
    syncForm()
    state.message = state.data.message || "Search cache cleared"
  } catch (error) {
    state.error = error.message
  } finally {
    state.cache.clearing = false
  }
}

async function connectLinkedIn() {
  await connectLinkedInSession()
}

async function clearLinkedInSession() {
  await clearLinkedInSessionStore()
}

watch(
  () => [state.form.primary_provider, state.form.fallback_provider, state.form.allow_fallback],
  (_, previous) => {
    if (previous) {
      void persistSettings({ keepMessage: true })
    }
  },
)

// Phase 17.9.9: small-tier knobs autosave too. Kept in their own watch
// so existing primary/fallback callsites don't re-trigger on a small-
// tier change (avoids double-saves when the user flips both fields).
watch(
  () => [state.form.small_provider, state.form.small_model],
  (_, previous) => {
    if (previous) {
      void persistSettings({ keepMessage: true })
    }
  },
)

watch(
  () => [state.form.cache_enabled, state.form.cache_ttl_hours],
  (_, previous) => {
    if (previous) {
      void persistSettings({ keepMessage: true })
    }
  },
)

// Phase 17.9.11: when the primary provider changes, refresh the
// primary_model field to that provider's saved (or default) model
// so the Primary model dropdown shows the right value.
watch(
  () => state.form.primary_provider,
  (newPid, oldPid) => {
    // Skip the initial sync pass.
    if (oldPid === undefined) return
    void onPrimaryProviderChange()
  },
)

// Phase 17.9.12: AppSelect doesn't surface @change, so the
// small-tier catalog load (previously inline on `@change`) moves to
// a dedicated watcher.
watch(
  () => state.form.small_provider,
  (newPid, oldPid) => {
    if (oldPid === undefined) return
    onSmallProviderChange()
  },
)

// Changing the Primary model writes directly to the provider's
// credential metadata via PUT /api/providers/{id}/model. We do NOT
// route this through `persistSettings`, since that endpoint only
// touches settings.yaml -- the model lives on credentials.json.
watch(
  () => state.form.primary_model,
  (newModel, oldModel) => {
    void persistPrimaryModel(newModel, oldModel)
  },
)

onMounted(refreshAll)

function providerStatusVariant(provider) {
  // Subprocess providers (claude / codex CLI) only know "binary on PATH"
  // -- they cannot tell us whether the user has actually run `claude
  // login`. Showing "Connected" for an unauthenticated CLI would mislead
  // users into thinking everything's wired up. We mirror the local-CLI
  // semantics: green = available on PATH; nothing stronger.
  if (provider.auth_type === "subprocess") {
    return provider.installed ? "success" : "secondary"
  }
  return provider.configured ? "success" : "secondary"
}

function providerStatusLabel(provider) {
  if (provider.auth_type === "subprocess") {
    return provider.installed ? "Available" : "Missing"
  }
  return provider.configured ? "Connected" : "Not connected"
}

function isSubprocessProvider(provider) {
  return provider.auth_type === "subprocess"
}

/**
 * Did AutoApply store a credential record for this provider?
 *
 * For API-key providers this is "yes, user pasted a key".
 * For subprocess providers this should normally be "no" -- the CLI
 * owns its own auth and we don't store anything. The exception is
 * users upgrading from the older Phase-10 OAuth-wrapper revision,
 * who may have a "managed_by: codex-cli" breadcrumb left over. We
 * surface that as a stored credential so the Disconnect button is
 * available to clean it up.
 */
function hasStoredCredential(provider) {
  return Boolean(provider.credentials && provider.credentials.has_secret)
}

function formatJobIndexDate(value) {
  return value ? new Date(value).toLocaleString() : "Never"
}

function jobIndexStateCount(stateName) {
  return state.data.job_index?.states?.[stateName] || 0
}

function disconnectLabel(provider) {
  if (isSubprocessProvider(provider) && hasStoredCredential(provider)) {
    return "Clear stored record"
  }
  return "Disconnect"
}

function isPrimary(provider) {
  return state.form.primary_provider === provider.id
}
</script>

<template>
  <div class="space-y-6">
    <Alert v-if="state.error" variant="destructive">
      <AlertCircle class="h-4 w-4" />
      <AlertDescription>{{ state.error }}</AlertDescription>
    </Alert>
    <Alert v-if="state.message" variant="success">
      <CheckCircle2 class="h-4 w-4" />
      <AlertDescription>{{ state.message }}</AlertDescription>
    </Alert>

    <Card>
      <CardHeader class="flex flex-row items-center justify-between space-y-0">
        <CardTitle class="flex items-center gap-2 text-sm">
          <Sparkles class="h-4 w-4 text-muted-foreground" />
          AI Providers
        </CardTitle>
        <Badge variant="secondary" class="tabular-nums">
          {{ state.providers.filter((p) => p.configured).length }}/{{ state.providers.length }} connected
          <span v-if="state.saving" class="ml-2 text-[10px] uppercase tracking-wide">Saving…</span>
        </Badge>
      </CardHeader>
      <CardContent class="space-y-6">
        <p class="text-xs text-muted-foreground">
          Connect API keys (OpenAI / Anthropic / Gemini) or use the local Claude / Codex CLI, then pick which provider AutoApply uses for resume tailoring, cover letters, and form filling.
        </p>

        <!-- Sub-section: Routing -->
        <section class="space-y-3">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Routing
          </h3>
          <div class="grid gap-4 md:grid-cols-2">
            <label class="space-y-1.5">
              <span class="text-xs font-medium text-muted-foreground">Primary provider</span>
              <AppSelect
                v-model="state.form.primary_provider"
                :options="primaryOptions"
                aria-label="Primary provider"
              />
            </label>

            <!-- Phase 17.9.11+12: Primary model dropdown.
                 - Uses the global AppSelect (reka-ui) for styling parity
                   with every other picker on the page.
                 - Subprocess providers (claude-cli, codex-cli) own
                   their model selection via their own auth/login
                   flow, so the dropdown collapses into an inert note
                   for those instead of showing "No models in catalog". -->
            <label class="space-y-1.5">
              <span class="text-xs font-medium text-muted-foreground">
                Primary model
                <span v-if="state.modelCatalogsLoading" class="ml-1 text-muted-foreground/70">loading…</span>
              </span>
              <div
                v-if="isSubprocessPrimaryProvider()"
                class="flex h-10 items-center rounded-md border border-dashed border-border bg-muted/30 px-3 text-xs text-muted-foreground"
              >
                Managed by the CLI itself — run <code class="mx-1 rounded bg-muted px-1">{{ state.form.primary_provider }} login</code>
              </div>
              <AppSelect
                v-else
                v-model="state.form.primary_model"
                :options="primaryModelSelectOptions()"
                :disabled="!state.form.primary_provider || primaryModelSelectOptions().length === 0"
                :placeholder="primaryModelSelectOptions().length === 0 ? 'Connect this provider first' : 'Pick a model'"
                aria-label="Primary model"
              />
            </label>

            <label class="space-y-1.5">
              <span class="text-xs font-medium text-muted-foreground">Fallback provider</span>
              <AppSelect
                v-model="state.form.fallback_provider"
                :options="fallbackOptions"
                aria-label="Fallback provider"
              />
            </label>
          </div>

          <label class="flex items-center gap-2 text-sm text-foreground">
            <input
              v-model="state.form.allow_fallback"
              type="checkbox"
              class="h-4 w-4 rounded border-input accent-primary"
            />
            <span>Auto fallback when the primary provider fails</span>
          </label>
        </section>

        <div class="border-t border-border"></div>

        <!-- Sub-section: Small-model tier (Phase 17.9.9) -->
        <section class="space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Small-model tier
              <Badge variant="outline" class="ml-1 text-[10px] uppercase tracking-wide">
                Optional
              </Badge>
            </h3>
            <Badge v-if="state.form.small_provider" variant="default" class="text-[10px] uppercase tracking-wide">
              Active
            </Badge>
            <Badge v-else variant="secondary" class="text-[10px] uppercase tracking-wide">
              Disabled
            </Badge>
          </div>
          <p class="text-xs text-muted-foreground">
            Route extraction-style LLM calls (job-description parsing, resume import) through a cheaper model.
            Creative paths (cover letter, resume rewrite) always stay on the primary tier. Pick "Disabled" to
            send everything through the primary.
          </p>
          <div class="grid gap-3 md:grid-cols-2">
            <label class="space-y-1.5">
              <span class="text-xs font-medium text-muted-foreground">Provider</span>
              <AppSelect
                v-model="state.form.small_provider"
                :options="smallTierProviderSelectOptions()"
                aria-label="Small-tier provider"
                placeholder="Disabled"
              />
            </label>
            <label class="space-y-1.5">
              <span class="text-xs font-medium text-muted-foreground">
                Model
                <span v-if="state.modelCatalogsLoading" class="ml-1 text-muted-foreground/70">loading…</span>
              </span>
              <AppSelect
                v-model="state.form.small_model"
                :options="smallTierModelSelectOptions()"
                :disabled="!state.form.small_provider || smallTierModelSelectOptions().length === 0"
                aria-label="Small-tier model"
                placeholder="Provider default"
              />
            </label>
          </div>
        </section>

        <div class="border-t border-border"></div>

        <!-- Sub-section: Providers (split into Connected / Available, Phase 17.9.8) -->
        <section class="space-y-4">
          <div v-if="state.loading && state.providers.length === 0" class="text-sm text-muted-foreground">
            Loading providers…
          </div>

          <template v-else>
            <div v-if="connectedProviders.length === 0" class="rounded-md border border-dashed border-border px-3 py-4 text-xs text-muted-foreground">
              No providers connected yet. Pick one from "Available" below and click Connect.
            </div>

            <div v-for="section in providerSections" :key="section.key" class="space-y-2">
              <div class="flex items-center justify-between">
                <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {{ section.title }}
                  <span class="ml-1 text-muted-foreground/70">
                    ({{
                      section.key === "connected"
                        ? connectedProviders.length
                        : availableProviders.length
                    }})
                  </span>
                </h3>
                <button
                  v-if="section.key === 'connected' && hiddenConnectedCount > 0"
                  type="button"
                  class="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                  @click="providerListUi.showAllConnected = !providerListUi.showAllConnected"
                >
                  {{
                    providerListUi.showAllConnected
                      ? "Show fewer"
                      : `Show ${hiddenConnectedCount} more`
                  }}
                </button>
                <button
                  v-if="section.key === 'available'"
                  type="button"
                  class="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                  @click="providerListUi.showAvailable = !providerListUi.showAvailable"
                >
                  {{ providerListUi.showAvailable ? "Hide" : "Show all" }}
                </button>
              </div>

              <div v-if="section.empty" class="rounded-md border border-dashed border-border/60 px-3 py-2 text-xs text-muted-foreground">
                {{ availableProviders.length }} provider(s) ready to connect — click "Show all" to see the list.
              </div>

              <div
                v-for="provider in section.items"
                :key="provider.id"
                class="flex flex-col gap-3 rounded-md border border-border bg-card px-3 py-3 text-sm transition-colors hover:bg-muted/30 sm:flex-row sm:items-center sm:justify-between"
              >
              <div class="min-w-0 flex-1 space-y-1">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-medium text-foreground">{{ provider.display_name }}</span>
                  <Badge variant="outline" class="text-[10px] uppercase tracking-wide">
                    {{ authTypeLabel(provider.auth_type) }}
                  </Badge>
                  <Badge v-if="isPrimary(provider)" variant="default" class="text-[10px] uppercase tracking-wide">
                    Primary
                  </Badge>
                  <Badge :variant="providerStatusVariant(provider)" class="text-[10px] uppercase tracking-wide">
                    {{ providerStatusLabel(provider) }}
                  </Badge>
                </div>
                <div class="text-xs text-muted-foreground">
                  <span v-if="provider.credentials?.connected_at">
                    Connected {{ new Date(provider.credentials.connected_at).toLocaleString() }}.
                  </span>
                  <span v-if="providerHealthFor(provider)">
                    Last verified {{ new Date(providerHealthFor(provider).checked_at).toLocaleString() }}
                    ({{ providerHealthFor(provider).ok ? "OK" : "FAIL" }}).
                  </span>
                  <span
                    v-else-if="provider.credentials?.verified_at"
                  >
                    Last verified {{ new Date(provider.credentials.verified_at).toLocaleString() }}.
                  </span>
                  <span v-if="!provider.configured && provider.install_hint">{{ provider.install_hint }}</span>
                  <span
                    v-if="providerHealthFor(provider) && !providerHealthFor(provider).ok"
                    class="text-destructive"
                  >
                    Health: {{ providerHealthFor(provider).detail }}
                  </span>
                  <span
                    v-else-if="provider.credentials?.last_test_error"
                    class="text-destructive"
                  >
                    Last error: {{ provider.credentials.last_test_error }}
                  </span>
                </div>
              </div>

              <div class="flex flex-wrap items-center gap-1.5">
                <Button
                  v-if="provider.configured"
                  variant="ghost"
                  size="sm"
                  :disabled="providerOp(provider.id).testing"
                  @click="testProvider(provider)"
                >
                  <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': providerOp(provider.id).testing }" />
                  {{ providerOp(provider.id).testing ? "Testing…" : "Test" }}
                </Button>

                <Button
                  v-if="provider.configured && !isPrimary(provider)"
                  variant="ghost"
                  size="sm"
                  :disabled="providerOp(provider.id).using"
                  @click="useProvider(provider)"
                >
                  <CheckCircle2 class="h-4 w-4" />
                  {{ providerOp(provider.id).using ? "…" : "Use as primary" }}
                </Button>

                <Button
                  v-if="provider.auth_type === 'api_key'"
                  :variant="provider.configured ? 'ghost' : 'default'"
                  size="sm"
                  @click="openConnectDialog(provider)"
                >
                  <KeyRound class="h-4 w-4" />
                  {{ provider.configured ? "Update key" : "Connect" }}
                </Button>

                <Button
                  v-if="(!isSubprocessProvider(provider) && provider.configured) || hasStoredCredential(provider)"
                  variant="ghost"
                  size="sm"
                  class="text-destructive hover:bg-destructive/10 hover:text-destructive"
                  :disabled="providerOp(provider.id).disconnecting"
                  @click="disconnectProvider(provider)"
                >
                  <Unplug class="h-4 w-4" />
                  {{ providerOp(provider.id).disconnecting ? "…" : disconnectLabel(provider) }}
                </Button>
              </div>
            </div>
          </div>
          </template>
        </section>
      </CardContent>
    </Card>

    <Card>
      <CardHeader class="flex flex-row items-center justify-between space-y-0">
        <CardTitle class="flex items-center gap-2 text-sm">
          <FileText class="h-4 w-4 text-muted-foreground" />
          Default Material Strategy
        </CardTitle>
        <Badge variant="secondary" class="tabular-nums">
          {{ state.materialDefaults.saving ? "Saving…" : "Live" }}
        </Badge>
      </CardHeader>
      <CardContent class="space-y-5">
        <p class="text-xs text-muted-foreground">
          What AutoApply should do every time it builds a resume or cover letter. Per-job picks on the Jobs page and per-plan picks in Plans always win over these defaults.
        </p>

        <Alert v-if="state.materialDefaults.error" variant="destructive">
          <AlertCircle class="h-4 w-4" />
          <AlertDescription>{{ state.materialDefaults.error }}</AlertDescription>
        </Alert>
        <Alert v-if="state.materialDefaults.message" class="border-primary/40 bg-primary/5">
          <CheckCircle2 class="h-4 w-4" />
          <AlertDescription>{{ state.materialDefaults.message }}</AlertDescription>
        </Alert>

        <div v-for="docType in ['resume', 'cover_letter']" :key="docType" class="space-y-3 rounded-md border bg-muted/20 p-3">
          <div class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {{ materialDocTypeLabel(docType) }}
          </div>
          <div class="grid gap-3 md:grid-cols-2">
            <label class="space-y-1.5">
              <span class="text-xs font-medium text-muted-foreground">Strategy</span>
              <AppSelect
                v-model="state.materialDefaults.form[docType].strategy"
                :options="MATERIAL_STRATEGY_OPTIONS"
                :aria-label="`${materialDocTypeLabel(docType)} strategy`"
              />
            </label>
            <label
              v-if="state.materialDefaults.form[docType].strategy === 'regenerate'"
              class="space-y-1.5"
            >
              <span class="text-xs font-medium text-muted-foreground">Default template</span>
              <AppSelect
                v-model="state.materialDefaults.form[docType].default_template_id"
                :options="materialTemplateOptions(docType)"
                :aria-label="`${materialDocTypeLabel(docType)} default template`"
              />
            </label>
            <label
              v-else
              class="space-y-1.5"
            >
              <span class="text-xs font-medium text-muted-foreground">
                {{
                  state.materialDefaults.form[docType].strategy === 'use_library'
                    ? 'Document to use'
                    : 'Document to patch'
                }}
              </span>
              <AppSelect
                v-model="state.materialDefaults.form[docType].default_document_id"
                :options="materialDocumentOptions(docType)"
                :aria-label="`${materialDocTypeLabel(docType)} library document`"
              />
              <span
                v-if="state.materialDefaults.form[docType].strategy === 'use_library'"
                class="text-xs text-muted-foreground"
              >
                The chosen document will be attached to each application as-is — no LLM, no template, no edits.
              </span>
              <span
                v-else
                class="text-xs text-muted-foreground"
              >
                Patching is supported for DOCX resumes today; LaTeX and PDF documents fall back to regenerate with a warning.
              </span>
            </label>
          </div>

          <!-- Phase 18.x patch knobs: only show when strategy is patch_existing. -->
          <div
            v-if="state.materialDefaults.form[docType].strategy === 'patch_existing'"
            class="space-y-3 rounded-md border border-dashed border-border bg-background/60 p-3"
          >
            <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Patch behaviour
            </div>
            <label class="space-y-1.5 block">
              <span class="text-xs font-medium text-muted-foreground">Rewrite intensity</span>
              <AppSelect
                v-model="state.materialDefaults.form[docType].patch_aggressiveness"
                :options="PATCH_AGGRESSIVENESS_OPTIONS"
                :aria-label="`${materialDocTypeLabel(docType)} bullet rewrite intensity`"
              />
              <span class="text-xs text-muted-foreground">
                Controls how aggressively the language model rewrites individual bullet text. The two toggles below control structural changes independently.
              </span>
            </label>
            <label class="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                class="mt-0.5"
                v-model="state.materialDefaults.form[docType].patch_allow_reorder_sections"
              />
              <span>
                <span class="font-medium">Allow re-ordering sections</span>
                <span class="block text-xs text-muted-foreground">
                  When off, the patched document keeps the same section order as your source DOCX. When on, sections can be re-ordered to match the planned layout.
                </span>
              </span>
            </label>
            <label class="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                class="mt-0.5"
                v-model="state.materialDefaults.form[docType].patch_allow_add_remove_bullets"
              />
              <span>
                <span class="font-medium">Allow adding/removing bullets</span>
                <span class="block text-xs text-muted-foreground">
                  When off, each section keeps the exact bullet count of your source DOCX. When on, surplus tailored bullets are appended and unused slots are blanked.
                </span>
              </span>
            </label>
          </div>
        </div>

        <div class="flex items-center justify-end">
          <Button
            size="sm"
            :disabled="state.materialDefaults.loading || state.materialDefaults.saving"
            @click="saveMaterialDefaults"
          >
            <Save class="size-4" />
            {{ state.materialDefaults.saving ? "Saving…" : "Save defaults" }}
          </Button>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardHeader class="flex flex-row items-center justify-between space-y-0">
        <CardTitle class="flex items-center gap-2 text-sm">
          <Database class="h-4 w-4 text-muted-foreground" />
          LinkedIn Search Index
        </CardTitle>
        <Badge variant="secondary" class="tabular-nums">
          {{ state.saving ? "Saving..." : "Live" }}
        </Badge>
      </CardHeader>
      <CardContent class="space-y-4">
        <p class="text-xs text-muted-foreground">
          Controls whether normal Jobs searches reuse fresh Phase 13 indexed LinkedIn results. Fetch Fresh on the Jobs
          page always bypasses this policy and updates the index.
        </p>

        <div class="grid gap-2 sm:grid-cols-4">
          <div class="rounded-md border border-border bg-muted/30 px-3 py-2">
            <div class="text-[11px] uppercase tracking-wide text-muted-foreground">Searches</div>
            <div class="text-lg font-semibold tabular-nums">
              {{ state.data.job_index?.known ? state.data.job_index.search_queries : "-" }}
            </div>
          </div>
          <div class="rounded-md border border-border bg-muted/30 px-3 py-2">
            <div class="text-[11px] uppercase tracking-wide text-muted-foreground">Postings</div>
            <div class="text-lg font-semibold tabular-nums">
              {{ state.data.job_index?.known ? state.data.job_index.job_postings : "-" }}
            </div>
          </div>
          <div class="rounded-md border border-border bg-muted/30 px-3 py-2">
            <div class="text-[11px] uppercase tracking-wide text-muted-foreground">Snapshots</div>
            <div class="text-lg font-semibold tabular-nums">
              {{ state.data.job_index?.known ? state.data.job_index.job_snapshots : "-" }}
            </div>
          </div>
          <div class="rounded-md border border-border bg-muted/30 px-3 py-2">
            <div class="text-[11px] uppercase tracking-wide text-muted-foreground">Latest refresh</div>
            <div class="text-xs font-medium">
              {{ formatJobIndexDate(state.data.job_index?.latest_success_at) }}
            </div>
          </div>
        </div>

        <div v-if="state.data.job_index?.known" class="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span class="chip subtle">Fresh {{ jobIndexStateCount("active") }}</span>
          <span class="chip subtle">Stale {{ jobIndexStateCount("stale") }}</span>
          <span class="chip subtle">Unknown {{ jobIndexStateCount("unknown") }}</span>
          <span class="chip subtle">Expired {{ jobIndexStateCount("expired") }}</span>
        </div>
        <Alert v-else variant="warning">
          <AlertCircle class="h-4 w-4" />
          <AlertDescription>{{ state.data.job_index?.warning || "Job Index status is unavailable." }}</AlertDescription>
        </Alert>

        <label class="grid max-w-xs gap-1.5">
          <span class="text-xs font-medium text-muted-foreground">Freshness TTL hours</span>
          <Input v-model="state.form.cache_ttl_hours" type="number" min="1" step="1" />
        </label>

        <label class="flex items-center gap-2 text-sm text-foreground">
          <input
            v-model="state.form.cache_enabled"
            type="checkbox"
            class="h-4 w-4 rounded border-input accent-primary"
          />
          <span>Use indexed results by default</span>
        </label>

        <div>
          <Button
            variant="ghost"
            size="sm"
            type="button"
            class="text-destructive hover:bg-destructive/10 hover:text-destructive"
            :disabled="state.cache.clearing"
            @click="clearSearchCache"
          >
            <Trash2 class="h-4 w-4" />
            {{ state.cache.clearing ? "Clearing..." : "Clear indexed searches" }}
          </Button>
        </div>

        <!-- Phase 12.6: pointer to the runtime cache inspector. -->
        <div class="pt-2 border-t">
          <router-link
            to="/settings/cache"
            class="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
          >
            Open Runtime Cache inspector (LLM / embedding / response)
            <span aria-hidden="true">→</span>
          </router-link>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardHeader class="flex flex-row items-center justify-between space-y-0">
        <CardTitle class="flex items-center gap-2 text-sm">
          <Linkedin class="h-4 w-4 text-muted-foreground" />
          LinkedIn
        </CardTitle>
        <Badge :variant="linkedinSessionState.authenticated ? 'success' : 'secondary'">
          {{ linkedinSessionState.authenticated ? "Connected" : "Not connected" }}
        </Badge>
      </CardHeader>
      <CardContent class="space-y-4">
        <p class="text-xs text-muted-foreground">Manage the saved browser session used for authenticated search.</p>

        <div class="space-y-2">
          <div
            class="flex items-center justify-between rounded-md border border-border bg-card px-3 py-2 text-sm transition-colors hover:bg-muted/50"
          >
            <span>Saved session</span>
            <Badge :variant="linkedinSessionState.has_session_data ? 'success' : 'secondary'">
              {{ linkedinSessionState.has_session_data ? "Present" : "Empty" }}
            </Badge>
          </div>
          <div
            class="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm transition-colors hover:bg-muted/50"
          >
            <span>Status</span>
            <span class="text-xs text-muted-foreground">
              {{ linkedinSessionState.message || "Check LinkedIn session status." }}
            </span>
          </div>
        </div>

        <Alert v-if="linkedinSessionState.error" variant="destructive">
          <AlertCircle class="h-4 w-4" />
          <AlertDescription>{{ linkedinSessionState.error }}</AlertDescription>
        </Alert>

        <div class="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            size="sm"
            type="button"
            :disabled="
              linkedinSessionState.loading || linkedinSessionState.connecting || linkedinSessionState.clearing
            "
            @click="refreshLinkedInSession"
          >
            <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': linkedinSessionState.loading }" />
            {{ linkedinSessionState.loading ? "Checking..." : "Check status" }}
          </Button>
          <Button
            size="sm"
            type="button"
            :disabled="linkedinSessionState.connecting || linkedinSessionState.clearing"
            @click="connectLinkedIn"
          >
            <Linkedin class="h-4 w-4" />
            {{ linkedinSessionState.connecting ? "Waiting for login..." : "Connect LinkedIn" }}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            type="button"
            class="text-destructive hover:bg-destructive/10 hover:text-destructive"
            :disabled="linkedinSessionState.connecting || linkedinSessionState.clearing"
            @click="clearLinkedInSession"
          >
            <Trash2 class="h-4 w-4" />
            {{ linkedinSessionState.clearing ? "Clearing..." : "Clear session" }}
          </Button>
        </div>
      </CardContent>
    </Card>

    <!-- Connect (API key) dialog ----------------------------------------- -->
    <Dialog v-model:open="connectDialog.open">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Connect {{ connectDialog.providerLabel }}</DialogTitle>
          <DialogDescription>
            The key is stored locally under data/providers/credentials.json with permission 0600 and is never logged.
          </DialogDescription>
        </DialogHeader>

        <div class="space-y-3 py-2">
          <div class="space-y-1.5">
            <Label for="api-key">
              API key
              <span v-if="connectDialog.allowEmptyKey" class="ml-1 text-xs text-muted-foreground">(optional)</span>
            </Label>
            <Input
              id="api-key"
              v-model="connectDialog.apiKey"
              type="password"
              autocomplete="off"
              spellcheck="false"
              :placeholder="connectDialog.allowEmptyKey ? 'Leave blank if your server has no auth' : 'sk-...'"
            />
          </div>

          <div class="space-y-1.5">
            <Label for="api-model">
              Model
              <span v-if="connectDialog.modelsLoading" class="ml-1 text-xs text-muted-foreground">loading…</span>
              <span
                v-else-if="connectDialog.modelsSource === 'runtime' || connectDialog.modelsSource === 'merged'"
                class="ml-1 text-xs text-muted-foreground"
              >({{ connectDialog.modelsSource === 'runtime' ? 'local server catalog' : 'curated + local' }})</span>
            </Label>
            <!-- Phase 17.9.11: catalog-only picker. Catalog-outside
                 ids should go through `autoapply provider set-model`. -->
            <select
              id="api-model"
              v-model="connectDialog.modelSelection"
              class="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option v-if="connectDialog.models.length === 0" value="" disabled>
                No models in catalog — connect first then update via CLI
              </option>
              <option
                v-for="m in connectDialog.models"
                :key="m.id"
                :value="m.id"
              >
                {{ m.display_name || m.id }}<template v-if="m.id === connectDialog.defaultModel"> (default)</template>
              </option>
            </select>
          </div>

          <button
            type="button"
            class="text-xs text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
            @click="connectDialog.showAdvanced = !connectDialog.showAdvanced"
          >
            {{ connectDialog.showAdvanced ? "Hide" : "Show" }} advanced (base URL)
          </button>
          <div v-if="connectDialog.showAdvanced" class="space-y-1.5">
            <Label for="api-base-url">Base URL</Label>
            <Input
              id="api-base-url"
              v-model="connectDialog.baseUrl"
              placeholder="Override only if you proxy this provider"
            />
          </div>

          <Alert v-if="connectDialog.error" variant="destructive">
            <AlertCircle class="h-4 w-4" />
            <AlertDescription>{{ connectDialog.error }}</AlertDescription>
          </Alert>
        </div>

        <DialogFooter>
          <Button variant="ghost" @click="connectDialog.open = false">Cancel</Button>
          <Button :disabled="!canSubmitConnect()" @click="submitConnect">
            <Loader2 v-if="connectDialog.submitting" class="h-4 w-4 animate-spin" />
            {{ connectDialog.submitting ? "Saving and testing..." : "Save and test" }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

  </div>
</template>
