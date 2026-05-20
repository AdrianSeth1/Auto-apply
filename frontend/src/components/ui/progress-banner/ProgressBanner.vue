<script setup>
import { computed } from "vue"
import { cva } from "class-variance-authority"
import { Loader2 } from "lucide-vue-next"
import { cn } from "@/lib/utils"

/**
 * Inline progress banner for long-running async operations.
 *
 * Pairs a spinning indicator with a primary status line and an
 * optional sub-line ("this may take 30-60 seconds"). Designed to
 * be dropped above or below a form while a request is in flight,
 * so the user knows whether the click registered.
 *
 * Styled to match the existing Alert variants so a screen can
 * stack a ProgressBanner, a success Alert, and a destructive
 * Alert without looking like three different design systems.
 */
const props = defineProps({
  variant: { type: String, default: "default" },
  title: { type: String, required: true },
  detail: { type: String, default: "" },
  class: { type: [String, Array, Object], default: "" },
})

const bannerVariants = cva(
  "relative flex w-full items-start gap-3 rounded-lg border px-4 py-3 text-sm",
  {
    variants: {
      variant: {
        default: "border-border bg-muted/50 text-foreground",
        accent: "border-accent/40 bg-accent/10 text-accent-foreground",
        info: "border-primary/30 bg-primary/5 text-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
)

const classes = computed(() => cn(bannerVariants({ variant: props.variant }), props.class))
</script>

<template>
  <div :class="classes" role="status" aria-live="polite">
    <Loader2 class="mt-0.5 h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
    <div class="flex min-w-0 flex-col gap-0.5">
      <div class="font-medium leading-tight">{{ title }}</div>
      <div v-if="detail" class="text-xs text-muted-foreground">{{ detail }}</div>
      <div v-if="$slots.default" class="text-xs text-muted-foreground">
        <slot />
      </div>
    </div>
  </div>
</template>
