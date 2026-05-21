<script setup>
import { computed, onMounted, reactive, watch } from "vue"
import { RouterLink, useRouter } from "vue-router"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Code2,
  FileCheck,
  Loader2,
  Save,
} from "lucide-vue-next"

import AppSelect from "@/components/AppSelect.vue"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { applyTemplatesResponse, documentTypeLabel } from "@/lib/materials-templates"

const props = defineProps({
  documentType: { type: String, required: true },
  templateId: { type: String, required: true },
})

const router = useRouter()

const editor = reactive({
  loading: true,
  saving: false,
  validating: false,
  error: "",
  message: "",
  name: "",
  description: "",
  content: "",
  validation: null,
  renderer: "",
  editableStyles: [],
  styleOverrides: {},
  targetPages: 1,
  filenamePattern: "company_role_date",
  filenameCustomLabel: "",
})

const FONT_OPTIONS = [
  "Arial",
  "Calibri",
  "Cambria",
  "Times New Roman",
  "Georgia",
  "Helvetica",
  "Garamond",
  "Verdana",
].map((font) => ({ value: font, label: font }))

const FILENAME_PATTERN_OPTIONS = [
  {
    value: "company_role_date",
    label: "Company + role + date",
    hint: "resume_stripe_backend_engineer_2026-05-21.docx",
  },
  {
    value: "type_profile_seq",
    label: "Type + profile + sequence",
    hint: "resume_jane_doe_001.docx, resume_jane_doe_002.docx, ...",
  },
  {
    value: "type_custom_seq",
    label: "Type + custom label + sequence",
    hint: "resume_<your label>_001.docx, ...",
  },
]

const filenamePatternHint = computed(
  () => FILENAME_PATTERN_OPTIONS.find((p) => p.value === editor.filenamePattern)?.hint || "",
)

watch(
  () => [props.documentType, props.templateId],
  () => {
    void loadTemplate()
  },
  { immediate: false },
)

onMounted(loadTemplate)

async function loadTemplate() {
  editor.loading = true
  editor.error = ""
  editor.message = ""

  try {
    const response = await api.templateDetail(props.documentType, props.templateId)
    const template = response.template || {}
    editor.name = template.name || template.template_id || ""
    editor.description = template.description || ""
    editor.content = template.content || ""
    editor.validation = template.validation || null
    editor.renderer = template.renderer || template.manifest?.renderer || ""
    editor.editableStyles = template.editable_styles || []
    editor.styleOverrides = buildStyleOverrideState(
      editor.editableStyles,
      template.style_overrides || {},
    )
    const manifest = template.manifest || {}
    editor.targetPages = Number(
      template.target_pages ?? manifest.target_pages ?? manifest.capacity?.max_pages ?? 1,
    )
    editor.filenamePattern =
      template.filename_pattern ?? manifest.filename_pattern ?? "company_role_date"
    editor.filenameCustomLabel =
      template.filename_custom_label ?? manifest.filename_custom_label ?? ""
  } catch (error) {
    editor.error = error.message
  } finally {
    editor.loading = false
  }
}

function buildStyleOverrideState(editableStyles, current) {
  const state = {}
  for (const entry of editableStyles) {
    const saved = current[entry.key] || {}
    const defaults = entry.defaults || {}
    state[entry.key] = {
      font: saved.font ?? defaults.font ?? "",
      size: saved.size ?? defaults.size ?? null,
      bold: saved.bold ?? defaults.bold ?? false,
      italic: saved.italic ?? defaults.italic ?? false,
      line_spacing: saved.line_spacing ?? defaults.line_spacing ?? null,
      space_before_pt: saved.space_before_pt ?? defaults.space_before_pt ?? 0,
      space_after_pt: saved.space_after_pt ?? defaults.space_after_pt ?? 0,
    }
  }
  return state
}

function buildOverridePayload() {
  const payload = {}
  for (const entry of editor.editableStyles) {
    const value = editor.styleOverrides[entry.key]
    if (!value) continue
    const defaults = entry.defaults || {}
    const diff = {}
    if (value.font && value.font !== defaults.font) diff.font = value.font
    if (
      value.size !== null &&
      value.size !== undefined &&
      Number(value.size) !== Number(defaults.size)
    ) {
      diff.size = Number(value.size)
    }
    if (typeof value.bold === "boolean" && value.bold !== Boolean(defaults.bold)) {
      diff.bold = value.bold
    }
    if (typeof value.italic === "boolean" && value.italic !== Boolean(defaults.italic)) {
      diff.italic = value.italic
    }
    if (
      value.line_spacing !== null &&
      value.line_spacing !== undefined &&
      Number(value.line_spacing) !== Number(defaults.line_spacing ?? 1.0)
    ) {
      diff.line_spacing = Number(value.line_spacing)
    }
    if (
      value.space_before_pt !== null &&
      value.space_before_pt !== undefined &&
      Number(value.space_before_pt) !== Number(defaults.space_before_pt ?? 0)
    ) {
      diff.space_before_pt = Number(value.space_before_pt)
    }
    if (
      value.space_after_pt !== null &&
      value.space_after_pt !== undefined &&
      Number(value.space_after_pt) !== Number(defaults.space_after_pt ?? 0)
    ) {
      diff.space_after_pt = Number(value.space_after_pt)
    }
    if (Object.keys(diff).length) {
      payload[entry.key] = diff
    }
  }
  return payload
}

async function saveTemplate() {
  editor.saving = true
  editor.error = ""
  editor.message = ""

  const settings = {
    target_pages: Number(editor.targetPages) || 1,
    filename_pattern: editor.filenamePattern,
    filename_custom_label: editor.filenameCustomLabel,
  }

  try {
    let response
    if (isLatexEditor.value) {
      response = await api.updateTemplate(props.documentType, props.templateId, {
        template_name: editor.name,
        description: editor.description,
        content: editor.content,
        ...settings,
      })
    } else {
      response = await api.updateTemplateStyles(props.documentType, props.templateId, {
        template_name: editor.name,
        description: editor.description,
        overrides: buildOverridePayload(),
        ...settings,
      })
    }
    applyTemplatesResponse(response)
    editor.validation = response.template?.validation || null
    if (response.template) {
      editor.editableStyles = response.template.editable_styles || editor.editableStyles
      editor.styleOverrides = buildStyleOverrideState(
        editor.editableStyles,
        response.template.style_overrides || {},
      )
    }
    editor.message = "Saved template."
  } catch (error) {
    editor.error = error.message
  } finally {
    editor.saving = false
  }
}

function resetStyleToDefault(entry) {
  const defaults = entry.defaults || {}
  editor.styleOverrides[entry.key] = {
    font: defaults.font ?? "",
    size: defaults.size ?? null,
    bold: defaults.bold ?? false,
    italic: defaults.italic ?? false,
    line_spacing: defaults.line_spacing ?? null,
    space_before_pt: defaults.space_before_pt ?? 0,
    space_after_pt: defaults.space_after_pt ?? 0,
  }
}

async function validateTemplate() {
  editor.validating = true
  editor.error = ""
  editor.message = ""

  try {
    const response = await api.validateTemplate(props.documentType, props.templateId)
    editor.validation = response.validation || response.template?.validation || null
    applyTemplatesResponse(response)
    editor.message = editor.validation?.ok ? "Template validated." : "Template needs review."
  } catch (error) {
    editor.error = error.message
  } finally {
    editor.validating = false
  }
}

function prettyLabel(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function validationIssueSeverity(issue) {
  return issue?.severity || "warning"
}

function validationIssueBadgeClass(issue) {
  const severity = validationIssueSeverity(issue)
  if (severity === "info") {
    return "border-muted-foreground/40 bg-muted/60 text-foreground"
  }
  return "border-destructive/50 bg-destructive/10 text-destructive hover:bg-destructive/15"
}

const validationIssues = computed(() => editor.validation?.issues || [])
const isLatexEditor = computed(() => editor.renderer === "latex")
</script>

<template>
  <div class="space-y-6">
    <Card>
      <CardContent class="flex flex-col items-start justify-between gap-3 p-5 md:flex-row md:items-center">
        <div class="flex items-center gap-3">
          <RouterLink
            to="/materials/templates"
            class="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Back to Template Library"
            title="Back to Template Library"
          >
            <ArrowLeft class="h-4 w-4" />
          </RouterLink>
          <div class="space-y-1">
            <p class="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {{ documentTypeLabel(documentType) }} template
            </p>
            <h2 class="flex items-center gap-2 text-lg font-semibold tracking-tight text-foreground">
              <Code2 class="h-4 w-4 text-muted-foreground" />
              {{ editor.name || templateId }}
            </h2>
            <p class="font-mono text-xs text-muted-foreground">{{ templateId }}</p>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <Badge v-if="editor.renderer" variant="outline">
            {{ isLatexEditor ? "LaTeX" : editor.renderer.toUpperCase() }}
          </Badge>
          <Badge v-if="editor.validation" :variant="editor.validation.ok ? 'success' : 'warning'">
            {{ editor.validation.ok ? "Validated" : "Needs validation" }}
          </Badge>
        </div>
      </CardContent>
    </Card>

    <Alert v-if="editor.error" variant="destructive">
      <AlertCircle class="h-4 w-4" />
      <AlertDescription>{{ editor.error }}</AlertDescription>
    </Alert>
    <Alert v-if="editor.message" variant="success">
      <CheckCircle2 class="h-4 w-4" />
      <AlertDescription>{{ editor.message }}</AlertDescription>
    </Alert>

    <Card v-if="editor.loading">
      <CardContent class="space-y-3 p-6">
        <Skeleton class="h-10 w-full" />
        <Skeleton class="h-10 w-full" />
        <Skeleton class="h-64 w-full" />
      </CardContent>
    </Card>

    <Card v-else>
      <CardHeader>
        <CardTitle class="text-sm">Template metadata</CardTitle>
      </CardHeader>
      <CardContent class="grid gap-4 md:grid-cols-2">
        <label class="space-y-1.5">
          <span class="text-xs font-medium text-muted-foreground">Name</span>
          <Input v-model="editor.name" />
        </label>
        <label class="space-y-1.5">
          <span class="text-xs font-medium text-muted-foreground">Description</span>
          <Input v-model="editor.description" />
        </label>
      </CardContent>
    </Card>

    <Card v-if="!editor.loading">
      <CardHeader>
        <CardTitle class="text-sm">Generation settings</CardTitle>
        <p class="text-xs text-muted-foreground">
          Page target is strictly enforced — generated documents must match exactly, or
          the review queue flags an error.
        </p>
      </CardHeader>
      <CardContent class="grid gap-4 md:grid-cols-2">
        <label class="space-y-1.5">
          <span class="text-xs font-medium text-muted-foreground">Expected pages</span>
          <Input
            type="number"
            min="1"
            max="5"
            v-model.number="editor.targetPages"
          />
          <p class="text-xs text-muted-foreground">
            Content scales to roughly fill this many pages. Both overflow and underflow are
            errors at validation time.
          </p>
        </label>
        <label class="space-y-1.5">
          <span class="text-xs font-medium text-muted-foreground">Filename pattern</span>
          <AppSelect
            v-model="editor.filenamePattern"
            :options="FILENAME_PATTERN_OPTIONS"
            aria-label="Filename pattern"
            compact
          />
          <p class="text-xs text-muted-foreground">{{ filenamePatternHint }}</p>
        </label>
        <label
          v-if="editor.filenamePattern === 'type_custom_seq'"
          class="space-y-1.5 md:col-span-2"
        >
          <span class="text-xs font-medium text-muted-foreground">Custom filename label</span>
          <Input
            v-model="editor.filenameCustomLabel"
            placeholder="e.g. ml_eng_apps"
          />
          <p class="text-xs text-muted-foreground">
            Lowercased, special chars become underscores. Used in place of the company name.
          </p>
        </label>
      </CardContent>
    </Card>

    <Card v-if="!editor.loading && isLatexEditor">
      <CardHeader class="flex flex-row items-center justify-between space-y-0">
        <CardTitle class="flex items-center gap-2 text-sm">
          <Code2 class="h-4 w-4 text-muted-foreground" />
          template.tex
        </CardTitle>
        <div class="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            type="button"
            :disabled="editor.validating || editor.saving"
            @click="validateTemplate"
          >
            <Loader2 v-if="editor.validating" class="h-4 w-4 animate-spin" />
            <FileCheck v-else class="h-4 w-4" />
            {{ editor.validating ? "Validating…" : "Validate" }}
          </Button>
          <Button
            size="sm"
            type="button"
            :disabled="editor.saving || editor.validating"
            @click="saveTemplate"
          >
            <Loader2 v-if="editor.saving" class="h-4 w-4 animate-spin" />
            <Save v-else class="h-4 w-4" />
            {{ editor.saving ? "Saving…" : "Save" }}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <textarea
          v-model="editor.content"
          spellcheck="false"
          class="h-[28rem] w-full rounded-md border border-input bg-background p-3 font-mono text-xs leading-relaxed text-foreground ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
        ></textarea>
      </CardContent>
    </Card>

    <Card v-if="!editor.loading && !isLatexEditor && editor.editableStyles.length">
      <CardHeader class="flex flex-row items-center justify-between space-y-0">
        <div class="space-y-1">
          <CardTitle class="flex items-center gap-2 text-sm">
            <Code2 class="h-4 w-4 text-muted-foreground" />
            Style editor
          </CardTitle>
          <p class="text-xs text-muted-foreground">
            Per-style font, size, weight, and line spacing. Changes apply to template.docx on save.
          </p>
        </div>
        <div class="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            type="button"
            :disabled="editor.validating || editor.saving"
            @click="validateTemplate"
          >
            <Loader2 v-if="editor.validating" class="h-4 w-4 animate-spin" />
            <FileCheck v-else class="h-4 w-4" />
            {{ editor.validating ? "Validating…" : "Validate" }}
          </Button>
          <Button
            size="sm"
            type="button"
            :disabled="editor.saving || editor.validating"
            @click="saveTemplate"
          >
            <Loader2 v-if="editor.saving" class="h-4 w-4 animate-spin" />
            <Save v-else class="h-4 w-4" />
            {{ editor.saving ? "Saving…" : "Save styles" }}
          </Button>
        </div>
      </CardHeader>
      <CardContent class="space-y-3">
        <div
          v-for="entry in editor.editableStyles"
          :key="entry.key"
          class="grid gap-3 rounded-md border border-border bg-muted/30 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,2.4fr)_auto]"
        >
          <div class="space-y-0.5">
            <div class="text-sm font-medium text-foreground">{{ entry.label }}</div>
            <div class="font-mono text-xs text-muted-foreground">{{ entry.style_name }}</div>
          </div>
          <div class="grid gap-2 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
            <label class="space-y-1 text-xs">
              <span class="text-muted-foreground">Font</span>
              <AppSelect
                v-model="editor.styleOverrides[entry.key].font"
                :options="FONT_OPTIONS"
                :aria-label="`${entry.label} font`"
                compact
              />
            </label>
            <label class="space-y-1 text-xs">
              <span class="text-muted-foreground">Size (pt)</span>
              <Input
                type="number"
                min="6"
                max="48"
                v-model.number="editor.styleOverrides[entry.key].size"
              />
            </label>
            <label class="space-y-1 text-xs">
              <span class="text-muted-foreground">Line spacing</span>
              <Input
                type="number"
                step="0.05"
                min="0.8"
                max="3"
                v-model.number="editor.styleOverrides[entry.key].line_spacing"
              />
            </label>
            <label class="space-y-1 text-xs">
              <span class="text-muted-foreground">Space before (pt)</span>
              <Input
                type="number"
                step="1"
                min="0"
                max="48"
                v-model.number="editor.styleOverrides[entry.key].space_before_pt"
              />
            </label>
            <label class="space-y-1 text-xs">
              <span class="text-muted-foreground">Space after (pt)</span>
              <Input
                type="number"
                step="1"
                min="0"
                max="48"
                v-model.number="editor.styleOverrides[entry.key].space_after_pt"
              />
            </label>
            <div class="flex items-end gap-4 pb-1 text-sm">
              <label class="flex items-center gap-2">
                <input
                  type="checkbox"
                  class="h-4 w-4 rounded border-input"
                  v-model="editor.styleOverrides[entry.key].bold"
                />
                <span>Bold</span>
              </label>
              <label class="flex items-center gap-2">
                <input
                  type="checkbox"
                  class="h-4 w-4 rounded border-input"
                  v-model="editor.styleOverrides[entry.key].italic"
                />
                <span>Italic</span>
              </label>
            </div>
          </div>
          <div class="flex items-start justify-end">
            <Button
              variant="ghost"
              size="sm"
              type="button"
              :disabled="editor.saving"
              @click="resetStyleToDefault(entry)"
            >
              Reset
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>

    <Card v-if="!editor.loading && validationIssues.length">
      <CardHeader>
        <CardTitle class="flex items-center gap-2 text-sm">
          <FileCheck class="h-4 w-4 text-muted-foreground" />
          Validation issues
        </CardTitle>
      </CardHeader>
      <CardContent class="space-y-2">
        <div
          v-for="issue in validationIssues"
          :key="`${issue.type}-${issue.message}`"
          class="rounded-md border border-border bg-muted/40 p-3"
        >
          <div class="flex items-center gap-2">
            <Badge variant="outline" :class="validationIssueBadgeClass(issue)">
              {{ prettyLabel(validationIssueSeverity(issue)) }}
            </Badge>
            <strong class="text-sm text-foreground">{{ prettyLabel(issue.type) }}</strong>
          </div>
          <p class="mt-1 text-sm text-muted-foreground">{{ issue.message }}</p>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
