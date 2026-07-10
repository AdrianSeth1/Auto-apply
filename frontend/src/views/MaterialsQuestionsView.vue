<script setup>
// Materials → Questions (2026-07-07): collaborative application-question
// answering. Paste a question, get a grounded draft; the model may ask
// YOU up to three clarifying questions — answer them, refine, and save
// the final answer to the QA bank (the form-filler reuses it verbatim).
import { onMounted, reactive } from "vue"
import {
  AlertCircle,
  BookMarked,
  CheckCircle2,
  Copy,
  Loader2,
  MessageCircleQuestion,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-vue-next"

import MaterialsTabsNav from "@/components/MaterialsTabsNav.vue"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState } from "@/components/ui/empty-state"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

const form = reactive({
  question: "",
  company: "",
  title: "",
})

const state = reactive({
  drafting: false,
  refining: false,
  saving: false,
  error: "",
  message: "",
  // Current working item: { question, answer, clarifyingQuestions: [{q, reply}], final }
  draft: null,
  bank: [],
  bankError: "",
  deletingId: "",
})

async function loadBank() {
  try {
    const response = await api.questionBank()
    state.bank = response.entries || []
    state.bankError = response.list_error || ""
  } catch (error) {
    state.bankError = error.message
  }
}

async function draft() {
  if (!form.question.trim()) {
    state.error = "Paste the application question first."
    return
  }
  state.drafting = true
  state.error = ""
  state.message = ""
  state.draft = null

  try {
    const result = await api.draftQuestionAnswer({
      question: form.question,
      company: form.company,
      title: form.title,
    })
    if (!result.ok) {
      state.error = result.error || "Drafting failed."
      return
    }
    state.draft = {
      question: result.question,
      answer: result.answer,
      clarifyingQuestions: (result.clarifying_questions || []).map((q) => ({
        q,
        reply: "",
      })),
      final: result.final,
    }
  } catch (error) {
    state.error = error.message
  } finally {
    state.drafting = false
  }
}

async function refine() {
  const answered = state.draft.clarifyingQuestions.filter((item) => item.reply.trim())
  if (!answered.length) {
    state.error = "Answer at least one of the questions before refining."
    return
  }
  state.refining = true
  state.error = ""

  try {
    const result = await api.draftQuestionAnswer({
      question: state.draft.question,
      company: form.company,
      title: form.title,
      clarifications: answered.map((item) => ({ question: item.q, answer: item.reply })),
    })
    if (!result.ok) {
      state.error = result.error || "Refining failed."
      return
    }
    state.draft = {
      question: result.question,
      answer: result.answer,
      clarifyingQuestions: (result.clarifying_questions || []).map((q) => ({
        q,
        reply: "",
      })),
      final: result.final,
    }
    state.message = "Draft refined with your notes."
  } catch (error) {
    state.error = error.message
  } finally {
    state.refining = false
  }
}

async function saveToBank() {
  state.saving = true
  state.error = ""
  try {
    const result = await api.saveQuestionAnswer(state.draft.question, state.draft.answer)
    state.bank = result.entries || state.bank
    state.message =
      "Saved. The form-filler will reuse this answer for matching questions automatically."
  } catch (error) {
    state.error = error.message
  } finally {
    state.saving = false
  }
}

async function removeEntry(entry) {
  state.deletingId = entry.id
  try {
    const result = await api.deleteQuestionAnswer(entry.id)
    state.bank = result.entries || state.bank.filter((item) => item.id !== entry.id)
  } catch (error) {
    state.error = error.message
  } finally {
    state.deletingId = ""
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    state.message = "Copied to clipboard."
  } catch {
    state.error = "Clipboard unavailable — select and copy manually."
  }
}

onMounted(loadBank)
</script>

<template>
  <div class="space-y-6">
    <MaterialsTabsNav />

    <Alert v-if="state.error" variant="destructive">
      <AlertCircle class="h-4 w-4" />
      <AlertDescription>{{ state.error }}</AlertDescription>
    </Alert>
    <Alert v-if="state.message" class="border-primary/40 bg-primary/5">
      <CheckCircle2 class="h-4 w-4" />
      <AlertDescription>{{ state.message }}</AlertDescription>
    </Alert>

    <Card>
      <CardHeader>
        <CardTitle class="flex items-center gap-2 text-sm">
          <MessageCircleQuestion class="h-4 w-4 text-muted-foreground" />
          Application question
        </CardTitle>
      </CardHeader>
      <CardContent class="space-y-4">
        <textarea
          v-model="form.question"
          rows="3"
          class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          placeholder='e.g. "Do you have one or two examples of exceptional performance you want to highlight?"'
        />
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Input v-model="form.company" type="text" placeholder="Company (optional context)" />
          <Input v-model="form.title" type="text" placeholder="Role title (optional context)" />
        </div>
        <Button type="button" :disabled="state.drafting" @click="draft">
          <Loader2 v-if="state.drafting" class="h-4 w-4 animate-spin" />
          <Sparkles v-else class="h-4 w-4" />
          Draft answer
        </Button>
      </CardContent>
    </Card>

    <Card v-if="state.draft">
      <CardHeader>
        <CardTitle class="flex items-center justify-between text-sm">
          <span>Draft answer</span>
          <Badge :variant="state.draft.final ? 'success' : 'secondary'">
            {{ state.draft.final ? "Ready" : "Wants your input" }}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent class="space-y-4">
        <textarea
          v-model="state.draft.answer"
          rows="7"
          class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        />

        <div v-if="state.draft.clarifyingQuestions.length" class="space-y-3">
          <div class="text-xs font-medium text-muted-foreground">
            The draft would be stronger with your input — answer what you can, then refine:
          </div>
          <div
            v-for="(item, index) in state.draft.clarifyingQuestions"
            :key="index"
            class="space-y-1.5 rounded-md border border-border p-3"
          >
            <div class="text-sm text-foreground">{{ item.q }}</div>
            <textarea
              v-model="item.reply"
              rows="2"
              class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder="Your answer (a sentence or two is plenty)"
            />
          </div>
        </div>

        <div class="flex flex-wrap gap-2">
          <Button
            v-if="state.draft.clarifyingQuestions.length"
            type="button"
            :disabled="state.refining"
            @click="refine"
          >
            <Loader2 v-if="state.refining" class="h-4 w-4 animate-spin" />
            <RefreshCw v-else class="h-4 w-4" />
            Refine with my notes
          </Button>
          <Button type="button" variant="outline" :disabled="state.saving" @click="saveToBank">
            <Loader2 v-if="state.saving" class="h-4 w-4 animate-spin" />
            <BookMarked v-else class="h-4 w-4" />
            Save to answer bank
          </Button>
          <Button type="button" variant="ghost" @click="copyText(state.draft.answer)">
            <Copy class="h-4 w-4" />
            Copy
          </Button>
        </div>
      </CardContent>
    </Card>

    <Card>
      <CardHeader>
        <CardTitle class="flex items-center gap-2 text-sm">
          <BookMarked class="h-4 w-4 text-muted-foreground" />
          Saved answers
          <span class="text-xs font-normal text-muted-foreground">
            — reused automatically when application forms ask a matching question
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Alert v-if="state.bankError" variant="destructive">
          <AlertCircle class="h-4 w-4" />
          <AlertDescription>{{ state.bankError }}</AlertDescription>
        </Alert>
        <div v-if="state.bank.length" class="space-y-3">
          <div
            v-for="entry in state.bank"
            :key="entry.id"
            class="space-y-1.5 rounded-md border border-border p-3"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="text-sm font-medium text-foreground">{{ entry.question }}</div>
              <div class="flex shrink-0 items-center gap-1">
                <Button type="button" variant="ghost" size="sm" @click="copyText(entry.answer)">
                  <Copy class="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  :disabled="state.deletingId === entry.id"
                  @click="removeEntry(entry)"
                >
                  <Loader2 v-if="state.deletingId === entry.id" class="h-3.5 w-3.5 animate-spin" />
                  <Trash2 v-else class="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
            <div class="whitespace-pre-wrap text-sm text-muted-foreground">{{ entry.answer }}</div>
          </div>
        </div>
        <EmptyState
          v-else-if="!state.bankError"
          title="No saved answers yet"
          description="Draft an answer above and save it — the form-filler reuses saved answers for matching questions."
        >
          <template #icon><BookMarked /></template>
        </EmptyState>
      </CardContent>
    </Card>
  </div>
</template>
