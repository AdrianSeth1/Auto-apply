<script setup>
import { computed, onMounted, reactive } from "vue"
import { RouterLink } from "vue-router"
import {
  Database,
  ExternalLink,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-vue-next"

import AppSelect from "@/components/AppSelect.vue"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { api } from "@/lib/api"

const filters = reactive({
  q: "",
  location: "",
  employment_type: "",
  seniority: "",
  source: "",
  company: "",
})

const state = reactive({
  loading: false,
  loaded: false,
  jobs: [],
  total: 0,
  limit: 20,
  offset: 0,
  facets: { employment_types: [], seniorities: [], sources: [], companies: [] },
  selected: {},
  error: "",
  message: "",
  generating: false,
  documentTypes: { resume: true, cover_letter: true },
})

const pageSizeOptions = [10, 20, 50, 100].map((value) => ({ value, label: `${value} / page` }))

function prettyLabel(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function facetOptions(values, placeholder) {
  return [
    { value: "", label: placeholder },
    ...values.map((value) => ({ value, label: prettyLabel(value) })),
  ]
}

const employmentTypeOptions = computed(() =>
  facetOptions(state.facets.employment_types, "Any type"),
)
const seniorityOptions = computed(() => facetOptions(state.facets.seniorities, "Any seniority"))
const sourceOptions = computed(() => facetOptions(state.facets.sources, "Any source"))
const companyOptions = computed(() => [
  { value: "", label: "Any company" },
  ...state.facets.companies.map((value) => ({ value, label: value })),
])

const currentPage = computed(() => Math.floor(state.offset / state.limit) + 1)
const totalPages = computed(() => Math.max(1, Math.ceil(state.total / state.limit)))
const selectedIds = computed(() => Object.keys(state.selected).filter((id) => state.selected[id]))
const allOnPageSelected = computed(
  () => state.jobs.length > 0 && state.jobs.every((job) => state.selected[job.id]),
)
const selectedDocumentTypes = computed(() =>
  Object.keys(state.documentTypes).filter((key) => state.documentTypes[key]),
)

async function load({ resetOffset = false } = {}) {
  if (resetOffset) {
    state.offset = 0
  }
  state.loading = true
  state.error = ""
  try {
    const response = await api.listDbJobs({
      ...filters,
      limit: state.limit,
      offset: state.offset,
    })
    state.jobs = response.jobs || []
    state.total = response.total || 0
    state.facets = response.facets || state.facets
    state.loaded = true
  } catch (error) {
    state.error = error.message
    state.jobs = []
    state.total = 0
  } finally {
    state.loading = false
  }
}

function toggleAllOnPage() {
  const next = !allOnPageSelected.value
  for (const job of state.jobs) {
    state.selected[job.id] = next
  }
}

function clearSelection() {
  state.selected = {}
}

async function generateForSelected() {
  if (!selectedIds.value.length || state.generating) {
    return
  }
  state.generating = true
  state.error = ""
  state.message = ""
  try {
    const response = await api.generateDbJobMaterials({
      job_ids: selectedIds.value,
      document_types: selectedDocumentTypes.value.length
        ? selectedDocumentTypes.value
        : ["resume", "cover_letter"],
    })
    const queued = response.queued?.length || 0
    if (queued) {
      state.message = `Queued materials generation for ${queued} job${queued === 1 ? "" : "s"}.`
      clearSelection()
    }
    if (response.errors?.length) {
      state.error = response.errors.join(" · ")
    }
  } catch (error) {
    state.error = error.message
  } finally {
    state.generating = false
  }
}

function goToPage(page) {
  const clamped = Math.min(Math.max(page, 1), totalPages.value)
  state.offset = (clamped - 1) * state.limit
  void load()
}

function changePageSize(value) {
  state.limit = Number(value) || 20
  state.offset = 0
  void load()
}

function formatDate(value) {
  if (!value) {
    return ""
  }
  try {
    return new Date(value).toLocaleDateString()
  } catch {
    return value
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="space-y-6">
    <Card class="p-4 space-y-3">
      <div class="flex items-center gap-2">
        <Database class="h-4 w-4 text-muted-foreground" />
        <h1 class="text-sm font-semibold">Job Database</h1>
        <span class="chip subtle" v-if="state.loaded">{{ state.total }} stored jobs match</span>
      </div>

      <form class="grid gap-2 md:grid-cols-3 lg:grid-cols-6" @submit.prevent="load({ resetOffset: true })">
        <input v-model="filters.q" class="input" type="text" placeholder="Title or company" aria-label="Search title or company" />
        <input v-model="filters.location" class="input" type="text" placeholder="Location (e.g. Portland, US)" aria-label="Location" />
        <AppSelect v-model="filters.company" :options="companyOptions" aria-label="Company" />
        <AppSelect v-model="filters.employment_type" :options="employmentTypeOptions" aria-label="Employment type" />
        <AppSelect v-model="filters.seniority" :options="seniorityOptions" aria-label="Seniority" />
        <AppSelect v-model="filters.source" :options="sourceOptions" aria-label="Source" />
        <div class="flex gap-2">
          <Button type="submit" class="flex-1" :disabled="state.loading">
            <Loader2 v-if="state.loading" class="h-4 w-4 animate-spin" />
            <Search v-else class="h-4 w-4" />
            Filter
          </Button>
          <Button variant="ghost" size="icon" type="button" title="Reload" aria-label="Reload" @click="load()">
            <RefreshCw class="h-4 w-4" />
          </Button>
        </div>
      </form>

      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-2 text-sm">
          <input v-model="state.documentTypes.resume" type="checkbox" />
          Resume
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="state.documentTypes.cover_letter" type="checkbox" />
          Cover letter
        </label>
        <Button
          type="button"
          :disabled="!selectedIds.length || state.generating || !selectedDocumentTypes.length"
          @click="generateForSelected"
        >
          <Loader2 v-if="state.generating" class="h-4 w-4 animate-spin" />
          <Sparkles v-else class="h-4 w-4" />
          Generate for {{ selectedIds.length }} selected
        </Button>
        <Button v-if="selectedIds.length" variant="ghost" type="button" @click="clearSelection">
          Clear selection
        </Button>
      </div>

      <Alert v-if="state.message" class="border-emerald-500/40">
        <FileText class="h-4 w-4" />
        <AlertDescription>
          {{ state.message }}
          As each job finishes (a few minutes per job with a local model), it appears under
          <RouterLink class="underline" to="/review">Awaiting Review</RouterLink>
          in the "ready" section with its apply link and resume/cover-letter downloads.
          Task progress is on the
          <RouterLink class="underline" to="/tasks">Plans task list</RouterLink>.
        </AlertDescription>
      </Alert>
      <Alert v-if="state.error" variant="destructive">
        <AlertDescription>{{ state.error }}</AlertDescription>
      </Alert>
    </Card>

    <Card>
      <div v-if="state.loading && !state.jobs.length" class="px-6 py-12">
        <EmptyState title="Loading stored jobs…">
          <template #icon><Loader2 class="animate-spin" /></template>
        </EmptyState>
      </div>

      <div v-else-if="state.jobs.length" class="job-db-table">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-muted-foreground border-b">
              <th class="p-3 w-8">
                <input
                  type="checkbox"
                  :checked="allOnPageSelected"
                  aria-label="Select all on page"
                  @change="toggleAllOnPage"
                />
              </th>
              <th class="p-3">Company</th>
              <th class="p-3">Title</th>
              <th class="p-3">Location</th>
              <th class="p-3">Type</th>
              <th class="p-3">Seniority</th>
              <th class="p-3">Source</th>
              <th class="p-3">Found</th>
              <th class="p-3 w-8"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="job in state.jobs" :key="job.id" class="border-b last:border-0 hover:bg-muted/40">
              <td class="p-3">
                <input
                  type="checkbox"
                  :checked="Boolean(state.selected[job.id])"
                  :aria-label="`Select ${job.company} ${job.title}`"
                  @change="state.selected[job.id] = !state.selected[job.id]"
                />
              </td>
              <td class="p-3 font-medium">{{ job.company }}</td>
              <td class="p-3">
                {{ job.title }}
                <span
                  v-if="job.has_application"
                  class="chip subtle"
                  title="Materials already generated — regenerate from its card under Awaiting Review"
                >Prepared</span>
              </td>
              <td class="p-3 text-muted-foreground">{{ job.location || "—" }}</td>
              <td class="p-3"><span v-if="job.employment_type" class="chip subtle">{{ prettyLabel(job.employment_type) }}</span></td>
              <td class="p-3"><span v-if="job.seniority" class="chip subtle">{{ prettyLabel(job.seniority) }}</span></td>
              <td class="p-3 text-muted-foreground">{{ job.source }}</td>
              <td class="p-3 text-muted-foreground">{{ formatDate(job.discovered_at) }}</td>
              <td class="p-3">
                <a
                  v-if="job.application_url"
                  :href="job.application_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  :aria-label="`Open posting for ${job.title}`"
                >
                  <ExternalLink class="h-4 w-4" />
                </a>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="flex items-center justify-between gap-3 p-3">
          <div class="flex items-center gap-2">
            <Button variant="ghost" size="sm" type="button" :disabled="currentPage <= 1" @click="goToPage(currentPage - 1)">
              Previous
            </Button>
            <span class="text-sm text-muted-foreground">Page {{ currentPage }} of {{ totalPages }}</span>
            <Button variant="ghost" size="sm" type="button" :disabled="currentPage >= totalPages" @click="goToPage(currentPage + 1)">
              Next
            </Button>
          </div>
          <AppSelect
            :model-value="state.limit"
            :options="pageSizeOptions"
            compact
            aria-label="Results per page"
            @update:model-value="changePageSize"
          />
        </div>
      </div>

      <div v-else class="px-6 py-12">
        <EmptyState
          title="No stored jobs match"
          description="Jobs land here automatically when searches on the Jobs tab find them. Run a search first, or loosen the filters."
        >
          <template #icon><Database /></template>
        </EmptyState>
      </div>
    </Card>
  </div>
</template>
