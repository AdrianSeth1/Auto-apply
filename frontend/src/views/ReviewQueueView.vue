<script setup>
import { computed, onMounted, reactive } from "vue"
import {
  BookMarked,
  Check,
  CircleCheck,
  CircleX,
  Copy,
  ExternalLink,
  Inbox,
  Loader2,
  PauseCircle,
  RefreshCw,
  Send,
  Sparkles,
  Trash2,
  UploadCloud,
  Wand2,
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
import { EmptyState } from "@/components/ui/empty-state"
import { ProgressBanner } from "@/components/ui/progress-banner"
import { api } from "@/lib/api"
import { formatDate, formatPercent } from "@/lib/format"

const COLUMNS = [
  { id: "pending", label: "Waiting on you", showStale: true },
  { id: "approved", label: "Ready to submit" },
  { id: "submitted", label: "Submitted today" },
  { id: "rejected", label: "Skipped today" },
]

const GATE_MESSAGES = {
  refresh: "The job posting changed since AutoApply prepared this. Refresh and try again.",
  expired: "This job is no longer accepting applications.",
  missing_binding: "Internal link is missing — please refresh this entry.",
}

const REPLACE_MATERIAL_TYPE_OPTIONS = [
  { value: "resume_docx", label: "Resume (.docx)" },
  { value: "resume_pdf", label: "Resume (.pdf)" },
  { value: "cover_letter_docx", label: "Cover Letter (.docx)" },
  { value: "cover_letter_pdf", label: "Cover Letter (.pdf)" },
]

const REPLACE_STRATEGY_OPTIONS = [
  { value: "regenerate", label: "Regenerate from a template" },
  { value: "patch_existing", label: "Patch a document from my library" },
  { value: "use_library", label: "Use library document as-is (no edits)" },
]

const PATCH_AGGRESSIVENESS_OPTIONS = [
  { value: "conservative", label: "Conservative · barely touch the wording" },
  { value: "balanced", label: "Balanced · sensible rewriting (recommended)" },
  { value: "aggressive", label: "Aggressive · rewrite freely to match the JD" },
]

const state = reactive({
  loading: false,
  error: "",
  entries: [],
  pausedApplications: [],
  pausedSubmittingId: "",
  pausedDiscardingId: "",
  selected: new Set(),
  pendingAction: false,
  message: "",
  messageVariant: "info",
  filterCompany: "",
  filterTitle: "",
  // Phase 17.8: lookups for the Replace materials dialog.
  templates: { resume: [], cover_letter: [] },
  documents: { resume: [], cover_letter: [] },
})

const discardDialog = reactive({
  open: false,
  application: null,
  reason: "",
})

const replaceDialog = reactive({
  open: false,
  application: null,
  materialType: "resume_docx",
  strategy: "regenerate",
  templateId: "",
  sourceDocumentId: "",
  // Phase 18.x: per-call overrides for the three patch knobs. ``null``
  // means "use the Settings default for this document_type", which is
  // what the backend's ``resolve_material_choice`` does when these
  // arrive as ``null`` over the wire.
  patchAggressiveness: null,
  patchAllowReorderSections: null,
  patchAllowAddRemoveBullets: null,
  submitting: false,
  progress: "",
})

const promoteDialog = reactive({
  open: false,
  application: null,
  artifactPath: "",
  documentType: "resume",
  displayName: "",
  submitting: false,
})

function statusBucket(entry) {
  if (entry.status === "stale") return "pending"
  return entry.status
}

const entriesByColumn = computed(() => {
  const out = Object.fromEntries(COLUMNS.map((c) => [c.id, []]))
  for (const entry of state.entries) {
    const bucket = statusBucket(entry)
    if (out[bucket]) out[bucket].push(entry)
  }
  return out
})

const counts = computed(() => {
  const out = {}
  for (const col of COLUMNS) {
    out[col.id] = entriesByColumn.value[col.id].length
  }
  out.selected = state.selected.size
  return out
})

const selectableEntries = computed(() =>
  state.entries.filter((e) => e.status === "pending" || e.status === "stale"),
)

async function refresh() {
  state.loading = true
  state.error = ""
  try {
    const [reviewResponse, applicationsResponse, templatesResponse, documentsResponse] =
      await Promise.all([
        api.reviewList(),
        api
          .applications({ status: "REVIEW_REQUIRED", limit: 50 })
          .catch(() => ({ applications: [] })),
        api.templates().catch(() => ({ templates: {} })),
        api.documents().catch(() => ({ documents: [] })),
      ])
    state.entries = reviewResponse.entries || []
    state.pausedApplications = (applicationsResponse.applications || []).filter(
      (app) => app.status === "REVIEW_REQUIRED",
    )
    state.templates = {
      resume: templatesResponse?.templates?.resume || [],
      cover_letter: templatesResponse?.templates?.cover_letter || [],
    }
    const docs = documentsResponse?.documents || []
    state.documents = {
      resume: docs.filter((d) => d.document_type === "resume" && d.editable),
      cover_letter: docs.filter((d) => d.document_type === "cover_letter" && d.editable),
    }
    const present = new Set(state.entries.map((e) => e.id))
    state.selected = new Set([...state.selected].filter((id) => present.has(id)))
  } catch (err) {
    state.error = err?.message || "Couldn't load this queue."
  } finally {
    state.loading = false
  }
}

function materialDocumentType(materialType) {
  return materialType.startsWith("cover_letter") ? "cover_letter" : "resume"
}

function replaceTemplateOptions() {
  const docType = materialDocumentType(replaceDialog.materialType)
  return [
    { value: "", label: "Use my default" },
    ...((state.templates[docType] || []).map((tpl) => ({
      value: tpl.template_id,
      label: tpl.name || tpl.template_id,
    }))),
  ]
}

function replaceDocumentOptions() {
  const docType = materialDocumentType(replaceDialog.materialType)
  const docs = state.documents[docType] || []
  return [
    {
      value: "",
      label: docs.length ? "Pick a document" : "No editable documents in your library",
    },
    ...docs.map((doc) => ({
      value: doc.id,
      label: `${doc.display_name} · ${doc.source_type.toUpperCase()}`,
    })),
  ]
}

function openReplaceDialog(application) {
  const hasCover = Boolean(application.cover_letter_version)
  replaceDialog.application = application
  replaceDialog.materialType = "resume_docx"
  replaceDialog.strategy = "regenerate"
  replaceDialog.templateId = ""
  replaceDialog.sourceDocumentId = ""
  replaceDialog.patchAggressiveness = null
  replaceDialog.patchAllowReorderSections = null
  replaceDialog.patchAllowAddRemoveBullets = null
  replaceDialog.submitting = false
  replaceDialog.progress = ""
  replaceDialog.open = true
  // If the user clearly only has a cover letter to fix, switch the
  // initial radio so they don't have to.
  if (!application.resume_version && hasCover) {
    replaceDialog.materialType = "cover_letter_docx"
  }
}

function closeReplaceDialog() {
  replaceDialog.open = false
  replaceDialog.application = null
}

async function confirmReplace() {
  if (!replaceDialog.application) return
  replaceDialog.submitting = true
  replaceDialog.progress = "Queued material generation..."
  state.message = ""
  state.messageVariant = "info"
  try {
    const result = await api.regenerateApplicationMaterial(replaceDialog.application.id, {
      materialType: replaceDialog.materialType,
      strategy: replaceDialog.strategy || null,
      templateId: replaceDialog.templateId || null,
      sourceDocumentId: replaceDialog.sourceDocumentId || null,
      patchAggressiveness:
        replaceDialog.strategy === "patch_existing"
          ? replaceDialog.patchAggressiveness
          : null,
      patchAllowReorderSections:
        replaceDialog.strategy === "patch_existing"
          ? replaceDialog.patchAllowReorderSections
          : null,
      patchAllowAddRemoveBullets:
        replaceDialog.strategy === "patch_existing"
          ? replaceDialog.patchAllowAddRemoveBullets
          : null,
      // 5-minute budget gives the fit-planner loop room for one or
      // two LLM rounds (~30-60s each) plus DOCX rendering + PDF
      // conversion without blowing past on slow providers. The
      // backend Celery task has its own much longer time limit; this
      // poll just decides when the dialog stops waiting.
      pollTimeoutMs: 5 * 60 * 1000,
      onProgress(row) {
        const status = row?.status || "queued"
        replaceDialog.progress =
          status === "queued"
            ? "Queued; waiting for a materials worker to pick it up..."
            : `Materials worker status: ${status}`
      },
    })
    const notes = Array.isArray(result?.strategy_notes) ? result.strategy_notes : []
    const verb = replaceDialog.strategy === "use_library" ? "replaced" : "regenerated"
    state.message =
      `Materials ${verb}.` + (notes.length ? ` (${notes.join("; ")})` : "")
    state.messageVariant = "success"
    closeReplaceDialog()
    await refresh()
  } catch (err) {
    const timedOut = String(err?.message || "").includes("pollTask timed out")
    state.message = timedOut
      ? "Materials generation was queued but no worker completed it within 5 minutes. Check the materials worker is running and look at the task row for the latest status."
      : err?.message || "Couldn't regenerate materials."
    state.messageVariant = "error"
  } finally {
    replaceDialog.submitting = false
    replaceDialog.progress = ""
  }
}

function openPromoteDialog(application, artifactKind) {
  const path =
    artifactKind === "resume"
      ? application.resume_version
      : application.cover_letter_version
  if (!path) return
  promoteDialog.application = application
  promoteDialog.artifactPath = path
  promoteDialog.documentType = artifactKind === "resume" ? "resume" : "cover_letter"
  promoteDialog.displayName = `${application.job.company} – ${artifactKind === "resume" ? "Resume" : "Cover Letter"}`
  promoteDialog.submitting = false
  promoteDialog.open = true
}

function closePromoteDialog() {
  promoteDialog.open = false
  promoteDialog.application = null
}

async function confirmPromote() {
  if (!promoteDialog.application || !promoteDialog.artifactPath) return
  promoteDialog.submitting = true
  state.message = ""
  state.messageVariant = "info"
  try {
    const result = await api.promoteArtifactToLibrary({
      artifactPath: promoteDialog.artifactPath,
      documentType: promoteDialog.documentType,
      displayName: promoteDialog.displayName.trim() || "Untitled",
      applicationId: promoteDialog.application.id,
    })
    state.message =
      result.status === "exists"
        ? "That file is already in your library."
        : "Saved to your library."
    state.messageVariant = "success"
    closePromoteDialog()
  } catch (err) {
    state.message = err?.message || "Couldn't save to library."
    state.messageVariant = "error"
  } finally {
    promoteDialog.submitting = false
  }
}

async function markPausedApplicationSubmitted(application) {
  state.pausedSubmittingId = application.id
  try {
    const result = await api.markApplicationSubmitted(application.id)
    state.message = result.message || "Marked as submitted."
    state.messageVariant = "success"
    await refresh()
  } catch (err) {
    state.message = err?.message || "Couldn't mark as submitted."
    state.messageVariant = "error"
  } finally {
    state.pausedSubmittingId = ""
  }
}

async function submitPausedApplication(application) {
  state.pausedSubmittingId = application.id
  state.message = ""
  state.messageVariant = "info"
  try {
    const result = await api.submitApplication(application.id)
    state.message = result.message || "Submitted."
    state.messageVariant = "success"
    await refresh()
  } catch (err) {
    state.message = err?.message || "Couldn't submit."
    state.messageVariant = "error"
  } finally {
    state.pausedSubmittingId = ""
  }
}

function openDiscardDialog(application) {
  discardDialog.application = application
  discardDialog.reason = ""
  discardDialog.open = true
}

function closeDiscardDialog() {
  discardDialog.open = false
  discardDialog.application = null
  discardDialog.reason = ""
}

async function confirmDiscard() {
  const application = discardDialog.application
  if (!application) return
  state.pausedDiscardingId = application.id
  state.message = ""
  state.messageVariant = "info"
  try {
    await api.discardApplication(application.id, discardDialog.reason.trim())
    state.message = `Discarded the application to ${application.job.company}.`
    state.messageVariant = "success"
    closeDiscardDialog()
    await refresh()
  } catch (err) {
    state.message = err?.message || "Couldn't discard this application."
    state.messageVariant = "error"
  } finally {
    state.pausedDiscardingId = ""
  }
}

function jobUrl(application) {
  return application?.job?.application_url || ""
}

function pausedFieldSummary(application) {
  if (!application.fields_total) return ""
  return `${application.fields_filled || 0} of ${application.fields_total} fields filled`
}

const fillDetailsDialog = reactive({
  open: false,
  application: null,
})

function openFillDetailsDialog(application) {
  // Allow opening even when the backend has not recorded per-field
  // details yet -- the dialog will tell the user "no detail available"
  // instead, which is more useful than the badge silently doing nothing.
  fillDetailsDialog.application = application
  fillDetailsDialog.open = true
}

function closeFillDetailsDialog() {
  fillDetailsDialog.open = false
  fillDetailsDialog.application = null
}

// 2026-07-11: "Copy pack" -- identity + artifact paths + posting link +
// top matching saved QA answers for one review card, bundled so a manual
// application doesn't require re-typing everything per posting.
const copyPackDialog = reactive({
  open: false,
  loading: false,
  error: "",
  entry: null,
  data: null,
  copiedKey: "",
})

async function openCopyPackDialog(entry) {
  copyPackDialog.entry = entry
  copyPackDialog.data = null
  copyPackDialog.error = ""
  copyPackDialog.loading = true
  copyPackDialog.open = true
  try {
    copyPackDialog.data = await api.reviewCopyPack(entry.id)
  } catch (err) {
    copyPackDialog.error = err?.message || "Couldn't load the copy pack."
  } finally {
    copyPackDialog.loading = false
  }
}

function closeCopyPackDialog() {
  copyPackDialog.open = false
  copyPackDialog.entry = null
  copyPackDialog.data = null
  copyPackDialog.error = ""
}

const IDENTITY_LABELS = {
  full_name: "Name",
  email: "Email",
  phone: "Phone",
  location: "Location",
  linkedin_url: "LinkedIn",
}

function copyPackIdentityFields(data) {
  const identity = data?.identity || {}
  return Object.entries(IDENTITY_LABELS)
    .map(([key, label]) => ({ key, label, value: identity[key] || "" }))
    .filter((field) => field.value)
}

function copyPackAllText(data) {
  if (!data) return ""
  const lines = []
  for (const field of copyPackIdentityFields(data)) {
    lines.push(`${field.label}: ${field.value}`)
  }
  if (data.application_url) {
    lines.push("", `Application: ${data.application_url}`)
  }
  for (const match of data.qa_matches || []) {
    lines.push("", `Q: ${match.question}`, `A: ${match.answer}`)
  }
  return lines.join("\n")
}

async function copyPackItem(key, text) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copyPackDialog.copiedKey = key
    setTimeout(() => {
      if (copyPackDialog.copiedKey === key) copyPackDialog.copiedKey = ""
    }, 1500)
  } catch {
    copyPackDialog.error = "Clipboard unavailable — select and copy manually."
  }
}

function applicationFillDetails(application) {
  const raw = Array.isArray(application?.fill_details)
    ? application.fill_details
    : []
  return raw.map((entry, idx) => ({
    key: `${idx}-${entry?.label || entry?.data_key || idx}`,
    label: entry?.label || "(no label detected)",
    dataKey: entry?.data_key || "",
    value: entry?.value ?? "",
    filled: Boolean(entry?.filled),
    error: entry?.error || "",
    required: Boolean(entry?.required),
    fieldType: entry?.field_type || "",
  }))
}

function artifactUrl(path) {
  return api.artifactDownloadUrl(path)
}

function pausedArtifactList(application) {
  const items = []
  if (application.resume_version) {
    items.push({ label: "Resume", path: application.resume_version })
  }
  if (application.cover_letter_version) {
    items.push({ label: "Cover Letter", path: application.cover_letter_version })
  }
  const shots = application.screenshot_paths || []
  shots.forEach((path, index) => {
    items.push({ label: `Screenshot ${index + 1}`, path })
  })
  return items
}

function toggleSelected(entry) {
  const next = new Set(state.selected)
  if (next.has(entry.id)) next.delete(entry.id)
  else next.add(entry.id)
  state.selected = next
}

function clearSelection() {
  state.selected = new Set()
}

function selectAllPending() {
  state.selected = new Set(selectableEntries.value.map((e) => e.id))
}

async function approveOne(entry) {
  await runAction(() => api.reviewApprove(entry.id, { reviewer: "operator" }))
}

async function rejectOne(entry) {
  await runAction(() => api.reviewReject(entry.id, { reviewer: "operator" }))
}

async function refreshOne(entry) {
  await runAction(() => api.reviewRefresh(entry.id, { reviewer: "operator" }))
}

async function markSubmittedOne(entry) {
  state.pendingAction = true
  state.message = ""
  state.messageVariant = "info"
  try {
    await api.reviewMarkSubmitted(entry.id, { reviewer: "operator" })
    state.message = "Marked as submitted — it's now in outcome tracking."
    state.messageVariant = "success"
    await refresh()
  } catch (err) {
    state.message = err?.message || "Couldn't mark as submitted."
    state.messageVariant = "error"
  } finally {
    state.pendingAction = false
  }
}

async function submitOne(entry) {
  state.pendingAction = true
  state.message = ""
  state.messageVariant = "info"
  try {
    const result = await api.reviewSubmit(entry.id, { reviewer: "operator" })
    if (result.ok) {
      state.message = "Submitted."
      state.messageVariant = "success"
    } else {
      const action = result.gate?.action || "blocked"
      const reason =
        GATE_MESSAGES[action] ||
        result.gate?.reason ||
        "Submission was blocked."
      state.message = reason
      state.messageVariant = "error"
    }
    await refresh()
  } catch (err) {
    state.message = err?.message || "Submit failed"
    state.messageVariant = "error"
  } finally {
    state.pendingAction = false
  }
}

async function bulkApprove() {
  if (!state.selected.size) return
  await runBulk(() =>
    api.reviewBulkApprove([...state.selected], { reviewer: "operator" }),
  )
}

async function bulkReject() {
  if (!state.selected.size) return
  await runBulk(() =>
    api.reviewBulkReject([...state.selected], { reviewer: "operator" }),
  )
}

async function bulkRejectByFilter() {
  const payload = { reviewer: "operator" }
  if (state.filterCompany) payload.company = state.filterCompany
  if (state.filterTitle) payload.keyword_in_title = state.filterTitle
  if (!payload.company && !payload.keyword_in_title) {
    state.message = "Enter a company or title keyword to skip matching jobs."
    state.messageVariant = "info"
    return
  }
  await runBulk(() => api.reviewBulkRejectByFilter(payload))
  state.filterCompany = ""
  state.filterTitle = ""
}

async function runAction(fn) {
  state.pendingAction = true
  state.message = ""
  state.messageVariant = "info"
  try {
    await fn()
    await refresh()
  } catch (err) {
    state.message = err?.message || "Action failed"
    state.messageVariant = "error"
  } finally {
    state.pendingAction = false
  }
}

async function runBulk(fn) {
  state.pendingAction = true
  state.message = ""
  state.messageVariant = "info"
  try {
    const result = await fn()
    const ok = (result?.succeeded || []).length
    const failed = (result?.failed || []).length
    if (failed) {
      state.message = `${ok} updated, ${failed} couldn't be updated.`
      state.messageVariant = "error"
    } else {
      state.message = `${ok} updated.`
      state.messageVariant = "success"
    }
    clearSelection()
    await refresh()
  } catch (err) {
    state.message = err?.message || "Bulk action failed"
    state.messageVariant = "error"
  } finally {
    state.pendingAction = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between gap-4 flex-wrap">
      <div>
        <h2 class="text-xl font-semibold">Awaiting your review</h2>
        <p class="text-sm text-muted-foreground">
          AutoApply has these applications ready. Approve to submit them, or skip the ones you don't want.
        </p>
      </div>
      <Button variant="outline" :disabled="state.loading" @click="refresh">
        <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': state.loading }" />
        Reload
      </Button>
    </div>

    <ProgressBanner
      v-if="state.pendingAction"
      title="Working on it…"
      detail="Processing your action against the review queue."
    />

    <Alert v-if="state.error" variant="destructive">
      <AlertDescription>{{ state.error }}</AlertDescription>
    </Alert>

    <Alert
      v-if="state.message"
      :variant="state.messageVariant === 'error' ? 'destructive' : 'default'"
      :class="state.messageVariant === 'success' ? 'border-primary/40 bg-primary/5' : ''"
    >
      <AlertDescription>{{ state.message }}</AlertDescription>
    </Alert>

    <Card v-if="state.pausedApplications.length">
      <CardHeader class="pb-2">
        <CardTitle class="flex items-center gap-2 text-sm">
          <PauseCircle class="h-4 w-4 text-muted-foreground" />
          Paused mid-application
          <Badge variant="secondary">{{ state.pausedApplications.length }}</Badge>
        </CardTitle>
        <p class="text-xs text-muted-foreground">
          You started applying to these directly. AutoApply filled them out and paused right before submitting so you could give the green light.
        </p>
      </CardHeader>
      <CardContent class="space-y-3">
        <article
          v-for="application in state.pausedApplications"
          :key="application.id"
          class="rounded-md border p-3 flex flex-col gap-3 md:flex-row md:items-start md:justify-between"
        >
          <div class="min-w-0 space-y-1">
            <div class="font-medium truncate">{{ application.job.company }}</div>
            <div class="text-sm text-muted-foreground truncate">
              {{ application.job.title }}
            </div>
            <div class="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <button
                v-if="pausedFieldSummary(application)"
                type="button"
                class="inline-flex items-center gap-1 underline decoration-dotted underline-offset-2 hover:text-foreground"
                title="See exactly which fields the form-filler attempted, with values and errors"
                @click="openFillDetailsDialog(application)"
              >
                {{ pausedFieldSummary(application) }}
              </button>
              <span v-if="application.match_score !== null">
                Match {{ formatPercent(application.match_score, "0%") }}
              </span>
              <span>Started {{ formatDate(application.created_at) }}</span>
            </div>
            <div v-if="pausedArtifactList(application).length" class="space-y-1 text-xs">
              <div class="flex flex-wrap gap-x-3 gap-y-1">
                <a
                  v-for="item in pausedArtifactList(application)"
                  :key="item.path"
                  class="text-primary underline-offset-4 hover:underline"
                  :href="artifactUrl(item.path)"
                  target="_blank"
                  rel="noopener"
                >{{ item.label }}</a>
              </div>
              <div class="flex flex-wrap gap-2 pt-1">
                <button
                  v-if="application.resume_version"
                  type="button"
                  class="inline-flex items-center gap-1 rounded border border-input bg-background px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
                  title="Copy this resume into your library so you can reuse it as a base"
                  @click="openPromoteDialog(application, 'resume')"
                >
                  <BookMarked class="h-3 w-3" />
                  Save resume to library
                </button>
                <button
                  v-if="application.cover_letter_version"
                  type="button"
                  class="inline-flex items-center gap-1 rounded border border-input bg-background px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
                  title="Copy this cover letter into your library so you can reuse it as a base"
                  @click="openPromoteDialog(application, 'cover_letter')"
                >
                  <BookMarked class="h-3 w-3" />
                  Save cover letter to library
                </button>
              </div>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2 md:justify-end">
            <a
              v-if="jobUrl(application)"
              :href="jobUrl(application)"
              target="_blank"
              rel="noopener"
              class="inline-flex items-center gap-1 rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground hover:bg-muted/50"
              title="Open the original job posting in a new tab"
            >
              <ExternalLink class="h-4 w-4" />
              View job
            </a>
            <Button
              size="sm"
              variant="outline"
              :disabled="state.pausedSubmittingId === application.id || state.pausedDiscardingId === application.id"
              @click="openReplaceDialog(application)"
            >
              <Wand2 class="h-4 w-4" />
              Replace materials
            </Button>
            <Button
              size="sm"
              variant="outline"
              class="text-destructive hover:bg-destructive/10 hover:text-destructive"
              :disabled="state.pausedDiscardingId === application.id || state.pausedSubmittingId === application.id"
              @click="openDiscardDialog(application)"
            >
              <Trash2 class="h-4 w-4" />
              Discard
            </Button>
            <Button
              size="sm"
              variant="outline"
              :disabled="state.pausedSubmittingId === application.id || state.pausedDiscardingId === application.id"
              title="You applied by hand on the company's site — mark it submitted so replies and follow-ups are tracked"
              @click="markPausedApplicationSubmitted(application)"
            >
              <CircleCheck class="h-4 w-4" />
              I applied manually
            </Button>
            <Button
              size="sm"
              :disabled="state.pausedSubmittingId === application.id || state.pausedDiscardingId === application.id"
              @click="submitPausedApplication(application)"
            >
              <UploadCloud class="h-4 w-4" />
              Approve &amp; Submit
            </Button>
          </div>
        </article>
      </CardContent>
    </Card>

    <div v-if="!state.loading && !state.pausedApplications.length && !state.entries.length">
      <Card>
        <CardContent class="py-12">
          <EmptyState
            title="Nothing waiting on you"
            description="AutoApply will surface jobs here once a plan finishes preparing applications, or when a direct apply pauses for your approval."
          >
            <template #icon><Inbox /></template>
          </EmptyState>
        </CardContent>
      </Card>
    </div>

    <Card v-if="state.entries.length && (counts.selected || selectableEntries.length)">
      <CardContent class="py-3 flex flex-wrap items-center gap-2">
        <Badge variant="secondary">
          {{ counts.selected }} selected · {{ selectableEntries.length }} can act on
        </Badge>
        <Button
          size="sm"
          variant="outline"
          :disabled="!selectableEntries.length"
          @click="selectAllPending"
        >
          Select all waiting
        </Button>
        <Button
          size="sm"
          variant="outline"
          :disabled="!counts.selected"
          @click="clearSelection"
        >
          Clear
        </Button>
        <Button
          size="sm"
          :disabled="!counts.selected || state.pendingAction"
          @click="bulkApprove"
        >
          <Check class="h-4 w-4" />
          Approve {{ counts.selected }}
        </Button>
        <Button
          size="sm"
          variant="outline"
          :disabled="!counts.selected || state.pendingAction"
          @click="bulkReject"
        >
          <CircleX class="h-4 w-4" />
          Skip {{ counts.selected }}
        </Button>
      </CardContent>
    </Card>

    <Card v-if="state.entries.length">
      <CardHeader class="pb-2">
        <CardTitle class="text-sm">Skip matching jobs</CardTitle>
      </CardHeader>
      <CardContent class="flex flex-wrap items-end gap-3">
        <label class="text-xs space-y-1">
          <span class="text-muted-foreground">Company contains</span>
          <input
            v-model="state.filterCompany"
            class="block rounded border bg-background px-2 py-1 text-sm"
            placeholder="e.g. companies I don't want"
          />
        </label>
        <label class="text-xs space-y-1">
          <span class="text-muted-foreground">Title contains</span>
          <input
            v-model="state.filterTitle"
            class="block rounded border bg-background px-2 py-1 text-sm"
            placeholder="e.g. senior"
          />
        </label>
        <Button
          size="sm"
          variant="outline"
          :disabled="state.pendingAction"
          @click="bulkRejectByFilter"
        >
          Skip everything matching
        </Button>
      </CardContent>
    </Card>

    <Dialog :open="discardDialog.open" @update:open="(value) => !value && closeDiscardDialog()">
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Discard this application?</DialogTitle>
          <DialogDescription>
            <span v-if="discardDialog.application">
              {{ discardDialog.application.job.title }} at
              {{ discardDialog.application.job.company }}
            </span>
            <span class="mt-2 block text-xs text-muted-foreground">
              The application won't be submitted. The materials AutoApply already generated stay on disk so you can refer back to them, but this row will move to your history as discarded.
            </span>
          </DialogDescription>
        </DialogHeader>
        <label class="space-y-1.5 text-sm">
          <span class="text-xs font-medium text-muted-foreground">
            Why are you discarding? (optional, just for your own notes)
          </span>
          <textarea
            v-model="discardDialog.reason"
            rows="2"
            class="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="e.g. wrong location, already applied elsewhere, changed my mind"
          ></textarea>
        </label>
        <DialogFooter>
          <Button
            variant="outline"
            :disabled="!!state.pausedDiscardingId"
            @click="closeDiscardDialog"
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            :disabled="!!state.pausedDiscardingId"
            @click="confirmDiscard"
          >
            <Trash2 class="h-4 w-4" />
            {{ state.pausedDiscardingId ? "Discarding…" : "Discard application" }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog :open="replaceDialog.open" @update:open="(v) => !v && closeReplaceDialog()">
      <DialogContent class="max-w-lg">
        <DialogHeader>
          <DialogTitle>Replace materials</DialogTitle>
          <DialogDescription>
            <span v-if="replaceDialog.application">
              {{ replaceDialog.application.job.title }} at
              {{ replaceDialog.application.job.company }}
            </span>
            <span class="mt-2 block text-xs text-muted-foreground">
              Generates a new resume or cover letter for this application. The old artifact stays on disk for audit; this just changes which file the application points at.
            </span>
          </DialogDescription>
        </DialogHeader>

        <div class="space-y-3 text-sm">
          <label class="space-y-1.5 block">
            <span class="text-xs font-medium text-muted-foreground">Which material?</span>
            <AppSelect
              v-model="replaceDialog.materialType"
              :options="REPLACE_MATERIAL_TYPE_OPTIONS"
              aria-label="Which material to regenerate"
            />
          </label>

          <label class="space-y-1.5 block">
            <span class="text-xs font-medium text-muted-foreground">Strategy</span>
            <AppSelect
              v-model="replaceDialog.strategy"
              :options="REPLACE_STRATEGY_OPTIONS"
              aria-label="Regeneration strategy"
            />
          </label>

          <label v-if="replaceDialog.strategy === 'regenerate'" class="space-y-1.5 block">
            <span class="text-xs font-medium text-muted-foreground">Template</span>
            <AppSelect
              v-model="replaceDialog.templateId"
              :options="replaceTemplateOptions()"
              aria-label="Template"
            />
          </label>

          <label v-else class="space-y-1.5 block">
            <span class="text-xs font-medium text-muted-foreground">Library document</span>
            <AppSelect
              v-model="replaceDialog.sourceDocumentId"
              :options="replaceDocumentOptions()"
              aria-label="Library document"
            />
            <span
              v-if="replaceDialog.strategy === 'use_library'"
              class="text-xs text-muted-foreground"
            >
              The selected document is attached to this application as-is. No LLM, no template, no edits.
            </span>
            <span
              v-else
              class="text-xs text-muted-foreground"
            >
              Patching works for DOCX resumes today. LaTeX or PDF sources will fall back to regenerate with a warning.
            </span>
          </label>

          <!-- Phase 18.x patch knobs: per-call overrides only shown when
               strategy is patch_existing. Leaving a knob at its
               'inherit' value posts ``null`` and the backend falls
               back to the Settings → Material defaults entry. -->
          <div
            v-if="replaceDialog.strategy === 'patch_existing'"
            class="space-y-3 rounded-md border border-dashed bg-muted/30 p-3"
          >
            <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Patch behaviour (override defaults for this run)
            </div>
            <label class="space-y-1.5 block">
              <span class="text-xs font-medium text-muted-foreground">Rewrite intensity</span>
              <AppSelect
                v-model="replaceDialog.patchAggressiveness"
                :options="[
                  { value: null, label: 'Inherit from Settings' },
                  ...PATCH_AGGRESSIVENESS_OPTIONS,
                ]"
                aria-label="Bullet rewrite intensity for this regeneration"
              />
            </label>
            <label class="space-y-1.5 block">
              <span class="text-xs font-medium text-muted-foreground">Allow re-ordering sections</span>
              <AppSelect
                v-model="replaceDialog.patchAllowReorderSections"
                :options="[
                  { value: null, label: 'Inherit from Settings' },
                  { value: true, label: 'Yes — let sections re-order' },
                  { value: false, label: 'No — keep source section order' },
                ]"
                aria-label="Allow re-ordering sections for this regeneration"
              />
            </label>
            <label class="space-y-1.5 block">
              <span class="text-xs font-medium text-muted-foreground">Allow adding/removing bullets</span>
              <AppSelect
                v-model="replaceDialog.patchAllowAddRemoveBullets"
                :options="[
                  { value: null, label: 'Inherit from Settings' },
                  { value: true, label: 'Yes — add or blank bullets as needed' },
                  { value: false, label: 'No — preserve source bullet count' },
                ]"
                aria-label="Allow adding or removing bullets for this regeneration"
              />
            </label>
          </div>
        </div>

        <Alert v-if="replaceDialog.progress" class="border-border bg-muted/40">
          <Loader2 class="h-4 w-4 animate-spin" />
          <AlertDescription>{{ replaceDialog.progress }}</AlertDescription>
        </Alert>

        <DialogFooter>
          <Button variant="outline" :disabled="replaceDialog.submitting" @click="closeReplaceDialog">
            Cancel
          </Button>
          <Button
            :disabled="
              replaceDialog.submitting ||
              (replaceDialog.strategy !== 'regenerate' && !replaceDialog.sourceDocumentId)
            "
            @click="confirmReplace"
          >
            <Loader2 v-if="replaceDialog.submitting" class="h-4 w-4 animate-spin" />
            <Sparkles v-else class="h-4 w-4" />
            {{
              replaceDialog.submitting
                ? (replaceDialog.strategy === 'use_library' ? 'Replacing…' : 'Regenerating…')
                : (replaceDialog.strategy === 'use_library' ? 'Replace' : 'Regenerate')
            }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog :open="promoteDialog.open" @update:open="(v) => !v && closePromoteDialog()">
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save to your library</DialogTitle>
          <DialogDescription>
            Copies this generated file into your document library so you can use it as a base
            for future generations. The application still keeps its own copy.
          </DialogDescription>
        </DialogHeader>
        <label class="space-y-1.5 block text-sm">
          <span class="text-xs font-medium text-muted-foreground">Library name</span>
          <input
            v-model="promoteDialog.displayName"
            type="text"
            class="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            placeholder="e.g. Tailored frontend resume v2"
          />
        </label>
        <DialogFooter>
          <Button variant="outline" :disabled="promoteDialog.submitting" @click="closePromoteDialog">
            Cancel
          </Button>
          <Button
            :disabled="promoteDialog.submitting || !promoteDialog.displayName.trim()"
            @click="confirmPromote"
          >
            <BookMarked class="h-4 w-4" />
            {{ promoteDialog.submitting ? "Saving…" : "Save to library" }}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <div v-if="state.entries.length" class="grid gap-4 lg:grid-cols-4">
      <Card v-for="col in COLUMNS" :key="col.id">
        <CardHeader class="pb-2 flex flex-row items-center justify-between">
          <CardTitle class="text-sm">{{ col.label }}</CardTitle>
          <Badge variant="secondary">{{ counts[col.id] }}</Badge>
        </CardHeader>
        <CardContent class="space-y-2 max-h-[60vh] overflow-y-auto">
          <article
            v-for="entry in entriesByColumn[col.id]"
            :key="entry.id"
            class="rounded border p-3 space-y-2"
            :class="{ 'ring-2 ring-primary/30': state.selected.has(entry.id) }"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="space-y-0.5 min-w-0">
                <div class="text-sm font-semibold truncate">
                  {{ entry.title || "(untitled role)" }}
                </div>
                <div class="text-xs text-muted-foreground truncate">
                  {{ entry.company || "(unknown company)" }}
                </div>
              </div>
              <input
                v-if="entry.status === 'pending' || entry.status === 'stale'"
                type="checkbox"
                :checked="state.selected.has(entry.id)"
                aria-label="Select entry"
                @change="toggleSelected(entry)"
              />
            </div>

            <div class="flex flex-wrap items-center gap-1 text-xs">
              <Badge v-if="entry.status === 'stale'" variant="destructive">
                Refresh needed
              </Badge>
              <Badge
                v-if="entry.score_breakdown?.final_score !== undefined"
                variant="outline"
              >
                Match {{ formatPercent(entry.score_breakdown.final_score, "0%") }}
              </Badge>
            </div>

            <div
              v-if="entry.reason"
              class="text-xs italic text-muted-foreground"
            >
              {{ entry.reason }}
            </div>

            <!-- 2026-07-08: everything a human needs to apply manually:
                 the posting link + the generated materials. -->
            <div
              v-if="entry.application_url || entry.artifacts?.length"
              class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"
            >
              <a
                v-if="entry.application_url"
                class="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                :href="entry.application_url"
                target="_blank"
                rel="noopener"
              >
                <ExternalLink class="h-3 w-3" />
                View posting
              </a>
              <a
                v-for="artifact in entry.artifacts || []"
                :key="artifact.path"
                class="text-primary underline-offset-4 hover:underline"
                :href="api.artifactDownloadUrl(artifact.path)"
                target="_blank"
                rel="noopener"
              >{{ artifact.label }}</a>
            </div>

            <div v-if="col.id === 'pending'" class="flex flex-wrap gap-1">
              <Button
                size="sm"
                variant="outline"
                title="Copy identity fields, materials paths, the posting link, and matching QA answers"
                @click="openCopyPackDialog(entry)"
              >
                <Copy class="h-4 w-4" />
                Copy pack
              </Button>
              <Button
                v-if="entry.status !== 'stale'"
                size="sm"
                :disabled="state.pendingAction"
                @click="approveOne(entry)"
              >
                <CircleCheck class="h-4 w-4" />
                Approve
              </Button>
              <Button
                v-if="entry.status === 'stale'"
                size="sm"
                :disabled="state.pendingAction"
                @click="refreshOne(entry)"
              >
                <RefreshCw class="h-4 w-4" />
                Refresh
              </Button>
              <Button
                v-if="entry.status !== 'stale'"
                size="sm"
                variant="outline"
                :disabled="state.pendingAction"
                title="You applied by hand on the company's site — mark it submitted so replies and follow-ups are tracked"
                @click="markSubmittedOne(entry)"
              >
                <CircleCheck class="h-4 w-4" />
                I applied manually
              </Button>
              <Button
                size="sm"
                variant="outline"
                :disabled="state.pendingAction"
                @click="rejectOne(entry)"
              >
                Skip
              </Button>
            </div>
            <div v-else-if="col.id === 'approved'" class="flex flex-wrap gap-1">
              <Button
                size="sm"
                variant="outline"
                title="Copy identity fields, materials paths, the posting link, and matching QA answers"
                @click="openCopyPackDialog(entry)"
              >
                <Copy class="h-4 w-4" />
                Copy pack
              </Button>
              <Button
                size="sm"
                :disabled="state.pendingAction"
                @click="submitOne(entry)"
              >
                <Send class="h-4 w-4" />
                Submit
              </Button>
              <Button
                size="sm"
                variant="outline"
                :disabled="state.pendingAction"
                title="You applied by hand on the company's site — mark it submitted so replies and follow-ups are tracked"
                @click="markSubmittedOne(entry)"
              >
                <CircleCheck class="h-4 w-4" />
                I applied manually
              </Button>
              <Button
                size="sm"
                variant="outline"
                :disabled="state.pendingAction"
                @click="rejectOne(entry)"
              >
                Skip
              </Button>
            </div>
          </article>

          <EmptyState
            v-if="!entriesByColumn[col.id].length"
            class="border-none"
            :title="`Nothing here`"
            description=""
          >
            <template #icon><Inbox /></template>
          </EmptyState>
        </CardContent>
      </Card>
    </div>

    <Dialog :open="fillDetailsDialog.open" @update:open="(v) => !v && closeFillDetailsDialog()">
      <DialogContent class="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Form-fill details</DialogTitle>
          <DialogDescription>
            <template v-if="fillDetailsDialog.application">
              {{ fillDetailsDialog.application.job.title }} at
              {{ fillDetailsDialog.application.job.company }} —
              {{ fillDetailsDialog.application.fields_filled || 0 }} of
              {{ fillDetailsDialog.application.fields_total || 0 }} fields filled.
            </template>
          </DialogDescription>
        </DialogHeader>
        <div class="max-h-[60vh] overflow-y-auto space-y-2">
          <div
            v-for="entry in applicationFillDetails(fillDetailsDialog.application)"
            :key="entry.key"
            class="rounded-md border p-3 text-sm"
            :class="entry.filled
              ? 'border-emerald-500/30 bg-emerald-500/5'
              : entry.error
                ? 'border-destructive/40 bg-destructive/5'
                : 'border-amber-500/40 bg-amber-500/5'"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="font-medium text-foreground">
                {{ entry.label }}
                <span v-if="entry.required" class="text-destructive">*</span>
              </div>
              <Badge :variant="entry.filled ? 'success' : entry.error ? 'destructive' : 'warning'">
                {{ entry.filled ? "Filled" : entry.error ? "Failed" : "Skipped" }}
              </Badge>
            </div>
            <div v-if="entry.dataKey" class="mt-1 font-mono text-xs text-muted-foreground">
              maps to: {{ entry.dataKey }}<span v-if="entry.fieldType"> ({{ entry.fieldType }})</span>
            </div>
            <div v-if="entry.value !== ''" class="mt-1 text-xs">
              <span class="text-muted-foreground">Value:</span>
              <span class="ml-1 break-words text-foreground">{{ entry.value }}</span>
            </div>
            <div v-else class="mt-1 text-xs text-muted-foreground italic">
              No value attempted (no matching profile field).
            </div>
            <div v-if="entry.error" class="mt-2 text-xs text-destructive">
              Reason: {{ entry.error }}
            </div>
          </div>
          <div
            v-if="!applicationFillDetails(fillDetailsDialog.application).length"
            class="rounded-md border border-dashed p-4 text-sm text-muted-foreground"
          >
            No per-field details were recorded for this attempt. This usually means the
            ATS form was never reached (e.g. browser timed out before opening the form)
            or the application was generated before the form-fill log was added. Check
            the error log on the row for the broader failure reason.
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" @click="closeFillDetailsDialog">Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog :open="copyPackDialog.open" @update:open="(v) => !v && closeCopyPackDialog()">
      <DialogContent class="max-w-lg">
        <DialogHeader>
          <DialogTitle>Copy pack</DialogTitle>
          <DialogDescription>
            <template v-if="copyPackDialog.entry">
              {{ copyPackDialog.entry.title }} at {{ copyPackDialog.entry.company }}
            </template>
          </DialogDescription>
        </DialogHeader>

        <div
          v-if="copyPackDialog.loading"
          class="flex items-center justify-center py-8 text-sm text-muted-foreground"
        >
          <Loader2 class="h-4 w-4 animate-spin mr-2" />
          Loading copy pack…
        </div>

        <Alert v-else-if="copyPackDialog.error" variant="destructive">
          <AlertDescription>{{ copyPackDialog.error }}</AlertDescription>
        </Alert>

        <div v-else-if="copyPackDialog.data" class="max-h-[60vh] overflow-y-auto space-y-4">
          <div v-if="copyPackIdentityFields(copyPackDialog.data).length" class="space-y-1">
            <div class="text-xs font-medium uppercase text-muted-foreground">Identity</div>
            <div
              v-for="field in copyPackIdentityFields(copyPackDialog.data)"
              :key="field.key"
              class="flex items-center justify-between gap-2 rounded-md border p-2 text-sm"
            >
              <div class="min-w-0">
                <div class="text-xs text-muted-foreground">{{ field.label }}</div>
                <div class="truncate">{{ field.value }}</div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Copy"
                @click="copyPackItem(field.key, field.value)"
              >
                <Check v-if="copyPackDialog.copiedKey === field.key" class="h-3.5 w-3.5" />
                <Copy v-else class="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>

          <div v-if="copyPackDialog.data.application_url" class="space-y-1">
            <div class="text-xs font-medium uppercase text-muted-foreground">Posting</div>
            <div class="flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
              <a
                class="min-w-0 truncate text-primary underline-offset-4 hover:underline"
                :href="copyPackDialog.data.application_url"
                target="_blank"
                rel="noopener"
              >{{ copyPackDialog.data.application_url }}</a>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Copy"
                @click="copyPackItem('application_url', copyPackDialog.data.application_url)"
              >
                <Check v-if="copyPackDialog.copiedKey === 'application_url'" class="h-3.5 w-3.5" />
                <Copy v-else class="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>

          <div v-if="copyPackDialog.data.artifacts?.length" class="space-y-1">
            <div class="text-xs font-medium uppercase text-muted-foreground">Materials</div>
            <div
              v-for="artifact in copyPackDialog.data.artifacts"
              :key="artifact.path"
              class="flex items-center justify-between gap-2 rounded-md border p-2 text-sm"
            >
              <div class="min-w-0">
                <div class="text-xs text-muted-foreground">{{ artifact.label }}</div>
                <div class="truncate font-mono text-xs">{{ artifact.path }}</div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Copy"
                @click="copyPackItem(artifact.path, artifact.path)"
              >
                <Check v-if="copyPackDialog.copiedKey === artifact.path" class="h-3.5 w-3.5" />
                <Copy v-else class="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>

          <div v-if="copyPackDialog.data.qa_matches?.length" class="space-y-1">
            <div class="text-xs font-medium uppercase text-muted-foreground">
              Likely form questions
            </div>
            <div
              v-for="match in copyPackDialog.data.qa_matches"
              :key="match.id"
              class="space-y-1 rounded-md border p-2 text-sm"
            >
              <div class="flex items-start justify-between gap-2">
                <div class="font-medium">{{ match.question }}</div>
                <Button
                  size="sm"
                  variant="ghost"
                  class="shrink-0"
                  aria-label="Copy"
                  @click="copyPackItem(match.id, match.answer)"
                >
                  <Check v-if="copyPackDialog.copiedKey === match.id" class="h-3.5 w-3.5" />
                  <Copy v-else class="h-3.5 w-3.5" />
                </Button>
              </div>
              <div class="text-muted-foreground">{{ match.answer }}</div>
            </div>
          </div>

          <div
            v-if="
              !copyPackIdentityFields(copyPackDialog.data).length &&
              !copyPackDialog.data.application_url &&
              !copyPackDialog.data.artifacts?.length &&
              !copyPackDialog.data.qa_matches?.length
            "
            class="rounded-md border border-dashed p-4 text-sm text-muted-foreground"
          >
            Nothing to copy yet. Fill in your profile's identity fields and save some
            QA bank answers to get more out of this.
          </div>
        </div>

        <DialogFooter>
          <Button
            v-if="copyPackDialog.data"
            variant="outline"
            @click="copyPackItem('__all__', copyPackAllText(copyPackDialog.data))"
          >
            <Check v-if="copyPackDialog.copiedKey === '__all__'" class="h-4 w-4" />
            <Copy v-else class="h-4 w-4" />
            Copy all
          </Button>
          <Button variant="outline" @click="closeCopyPackDialog">Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
