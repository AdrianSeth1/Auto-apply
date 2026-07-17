<script setup>
import { onMounted, ref } from "vue"
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from "lucide-vue-next"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { api } from "@/lib/api"

const report = ref(null)
const loading = ref(false)
const error = ref("")

const sourceReport = ref(null)
const sourceLoading = ref(false)
const sourceError = ref("")

async function load() {
  loading.value = true
  error.value = ""
  try {
    report.value = await api.get("/api/job-pool-v2/report")
  } catch (err) {
    error.value = err.message || "Could not load the quality report."
  } finally {
    loading.value = false
  }
}

async function loadSourceFunnel() {
  sourceLoading.value = true
  sourceError.value = ""
  try {
    sourceReport.value = await api.get("/api/job-pool-v2/source-funnel")
  } catch (err) {
    sourceError.value = err.message || "Could not load the source funnel."
  } finally {
    sourceLoading.value = false
  }
}

async function loadAll() {
  await Promise.all([load(), loadSourceFunnel()])
}

onMounted(loadAll)
</script>

<template>
  <main class="view-shell space-y-6">
    <header class="flex items-start justify-between gap-4">
      <div>
        <p class="eyebrow">Job Pool V2</p>
        <h1 class="view-title">Search quality</h1>
        <p class="view-subtitle">See how much useful supply each target receives and where jobs fall out.</p>
      </div>
      <Button variant="outline" :disabled="loading || sourceLoading" @click="loadAll">
        <Loader2 v-if="loading || sourceLoading" class="mr-2 size-4 animate-spin" />
        <RefreshCw v-else class="mr-2 size-4" /> Refresh
      </Button>
    </header>

    <div v-if="error" class="rounded-lg border border-destructive/40 p-4 text-destructive">{{ error }}</div>
    <Card v-else-if="report && !report.available">
      <CardContent class="p-6">{{ report.message }}</CardContent>
    </Card>
    <template v-else-if="report?.available">
      <div class="flex items-center gap-2">
        <Badge :variant="report.run.shadow ? 'secondary' : 'default'">
          {{ report.run.shadow ? "Shadow only — no cards created" : "Live V2 run" }}
        </Badge>
        <span class="text-sm text-muted-foreground">{{ report.run.status }}</span>
      </div>
      <div v-if="!report.run.version_current" class="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
        This report was scored with {{ report.run.scorer_version || "an older version" }}.
        The next shadow cycle will refresh it with {{ report.run.current_scorer_version }}.
      </div>

      <section class="grid gap-4 md:grid-cols-5">
        <Card v-for="(value, key) in report.counts" :key="key">
          <CardContent class="p-5">
            <div class="text-2xl font-semibold">{{ value }}</div>
            <div class="mt-1 text-sm capitalize text-muted-foreground">{{ key.replaceAll('_', ' ') }}</div>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader><CardTitle>Useful supply by target</CardTitle></CardHeader>
        <CardContent class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead><tr class="border-b text-left text-muted-foreground"><th class="py-2">Target</th><th>A</th><th>B</th><th>C</th><th>D</th></tr></thead>
            <tbody>
              <tr v-for="(supply, target) in report.target_supply" :key="target" class="border-b last:border-0">
                <td class="py-3 font-medium">{{ target }}</td>
                <td>{{ supply.tiers.A || 0 }}</td><td>{{ supply.tiers.B || 0 }}</td><td>{{ supply.tiers.C || 0 }}</td><td>{{ supply.tiers.D || 0 }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div v-if="sourceError" class="rounded-lg border border-destructive/40 p-4 text-destructive">{{ sourceError }}</div>
      <Card v-else-if="sourceReport && !sourceReport.available">
        <CardContent class="p-6">{{ sourceReport.message }}</CardContent>
      </Card>
      <Card v-else-if="sourceReport?.available">
        <CardHeader>
          <CardTitle>Supply funnel by source &amp; endpoint</CardTitle>
          <p class="text-sm text-muted-foreground">
            fetched &rarr; unique &rarr; in-policy geography &rarr; target-routed &rarr; full-JD &rarr; A/B &rarr; surfaced.
            Rows with many fetched jobs but zero routed or zero full-JD are flagged low-yield.
            “Before V2” is the difference between provider fetches and jobs that reached V2; it includes search
            filtering and identity reconciliation, so it is not a duplicate count.
            An "est." badge means this run has no real fetch telemetry for that row yet (SUP-01B instrumentation
            not wired for that adapter, or the row predates it) — counts fall back to evaluated postings.
          </p>
        </CardHeader>
        <CardContent class="overflow-x-auto">
          <p v-if="sourceReport.message" class="text-sm text-muted-foreground">{{ sourceReport.message }}</p>
          <table v-if="sourceReport.sources?.length" class="w-full min-w-[900px] text-sm">
            <thead>
              <tr class="border-b text-left text-muted-foreground">
                <th class="py-2 pr-3">Source</th>
                <th class="pr-3">Endpoint</th>
                <th class="pr-3">Fetched</th>
                <th class="pr-3">Unique</th>
                <th class="pr-3">In-policy geo</th>
                <th class="pr-3">Routed</th>
                <th class="pr-3">Full JD</th>
                <th class="pr-3">A/B</th>
                <th class="pr-3">Surfaced</th>
                <th class="pr-3">Before V2</th>
                <th class="pr-3">7d A/B</th>
                <th class="pr-3">30d A/B</th>
                <th class="pr-3">Last success</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in sourceReport.sources"
                :key="`${row.source}-${row.endpoint || 'whole'}`"
                class="border-b last:border-0"
                :class="row.low_yield ? 'bg-destructive/5' : ''"
              >
                <td class="py-2 pr-3 font-medium">{{ row.source }}</td>
                <td class="pr-3">
                  <span v-if="row.endpoint">{{ row.endpoint }}</span>
                  <span v-else-if="row.endpoint_kind === 'attribution_unknown'" class="text-muted-foreground italic">unattributed</span>
                  <span v-else class="text-muted-foreground">whole feed</span>
                  <Badge v-if="row.low_yield" variant="destructive" class="ml-1">low yield</Badge>
                  <Badge v-if="!row.fetch.instrumented" variant="outline" class="ml-1">est.</Badge>
                </td>
                <td class="pr-3">{{ row.funnel.fetched }}</td>
                <td class="pr-3">{{ row.funnel.unique }}</td>
                <td class="pr-3">{{ row.funnel.in_policy_geography }}</td>
                <td class="pr-3">{{ row.funnel.target_routed }}</td>
                <td class="pr-3">{{ row.funnel.full_jd }}</td>
                <td class="pr-3">{{ row.funnel.ab }}</td>
                <td class="pr-3">{{ row.funnel.surfaced }}</td>
                <td class="pr-3" :title="row.after_fetch_attrition_note">{{ row.after_fetch_attrition }}</td>
                <td class="pr-3">{{ row.yield?.['7d_unique_ab'] ?? 0 }}</td>
                <td class="pr-3">{{ row.yield?.['30d_unique_ab'] ?? 0 }}</td>
                <td class="pr-3 text-muted-foreground">{{ row.fetch.last_success_at ? new Date(row.fetch.last_success_at).toLocaleString() : '—' }}</td>
              </tr>
            </tbody>
            <tfoot v-if="sourceReport.totals">
              <tr class="border-t font-medium">
                <td class="py-2 pr-3">Total</td>
                <td class="pr-3"></td>
                <td class="pr-3">{{ sourceReport.totals.fetched }}</td>
                <td class="pr-3">{{ sourceReport.totals.unique }}</td>
                <td class="pr-3">{{ sourceReport.totals.in_policy_geography }}</td>
                <td class="pr-3">{{ sourceReport.totals.target_routed }}</td>
                <td class="pr-3">{{ sourceReport.totals.full_jd }}</td>
                <td class="pr-3">{{ sourceReport.totals.ab }}</td>
                <td class="pr-3">{{ sourceReport.totals.surfaced }}</td>
                <td colspan="4"></td>
              </tr>
            </tfoot>
          </table>
          <p v-else class="text-sm text-muted-foreground">No source rows for this run.</p>
        </CardContent>
      </Card>

      <div class="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Why jobs were lost</CardTitle></CardHeader>
          <CardContent class="space-y-2">
            <div v-for="reason in report.loss_reasons" :key="reason.reason" class="flex justify-between gap-4 border-b py-2 last:border-0">
              <span>{{ reason.reason.replaceAll('_', ' ') }}</span><Badge variant="outline">{{ reason.count }}</Badge>
            </div>
            <p v-if="!report.loss_reasons.length" class="text-sm text-muted-foreground">No loss reasons recorded.</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Source endpoint health</CardTitle></CardHeader>
          <CardContent class="space-y-2">
            <div v-for="(count, state) in report.endpoint_health" :key="state" class="flex justify-between border-b py-2 last:border-0">
              <span class="capitalize">{{ state.replaceAll('_', ' ') }}</span><Badge variant="outline">{{ count }}</Badge>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Proposed cards</CardTitle></CardHeader>
        <CardContent class="space-y-3">
          <div v-for="card in report.proposed_cards" :key="`${card.company}-${card.title}-${card.target}`" class="rounded-lg border p-4">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div><strong>{{ card.title }}</strong> · {{ card.company }}</div>
              <div class="flex gap-2"><Badge>{{ card.tier }}</Badge><Badge variant="outline">{{ card.target }}</Badge></div>
            </div>
            <p class="mt-2 text-sm text-muted-foreground">
              <CheckCircle2 v-if="card.reserved" class="mr-1 inline size-4" />
              {{ card.reserved ? "Card successfully created" : "Selected in shadow; no card was created" }}
            </p>
          </div>
          <p v-if="!report.proposed_cards.length" class="text-sm text-muted-foreground">This run proposed no Tier A or B cards.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Promising, but uncertain</CardTitle>
        </CardHeader>
        <CardContent class="space-y-3">
          <p class="text-sm text-muted-foreground">
            Exploration only. These jobs have no hard failures, but did not clear Tier B.
            They do not create review cards or generate materials.
          </p>
          <div
            v-for="item in report.promising_near_misses || []"
            :key="item.evaluation_id"
            class="rounded-lg border border-dashed p-4"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div><strong>{{ item.title }}</strong> · {{ item.company }}</div>
              <div class="flex gap-2">
                <Badge variant="secondary">Tier C</Badge>
                <Badge variant="outline">{{ item.target }}</Badge>
              </div>
            </div>
            <p class="mt-2 text-sm text-muted-foreground">
              Main uncertainty: {{ (item.why_not_ab || []).join(', ').replaceAll('_', ' ') || 'insufficient confidence' }}
            </p>
          </div>
          <p v-if="!(report.promising_near_misses || []).length" class="text-sm text-muted-foreground">
            No safe near-misses in this run.
          </p>
        </CardContent>
      </Card>

      <Card v-if="report.unresolved.length">
        <CardHeader><CardTitle><AlertTriangle class="mr-2 inline size-5" />Missing information</CardTitle></CardHeader>
        <CardContent class="space-y-2">
          <div v-for="item in report.unresolved" :key="item.evaluation_id" class="text-sm">
            <strong>{{ item.title }}</strong> · {{ item.company }} — {{ item.missing.join(', ') }}
          </div>
        </CardContent>
      </Card>
    </template>
  </main>
</template>
