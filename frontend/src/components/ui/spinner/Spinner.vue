<script setup>
import { computed } from "vue"
import { cva } from "class-variance-authority"
import { Loader2 } from "lucide-vue-next"
import { cn } from "@/lib/utils"

const props = defineProps({
  size: { type: String, default: "default" },
  label: { type: String, default: "" },
  class: { type: [String, Array, Object], default: "" },
})

const spinnerVariants = cva("animate-spin shrink-0", {
  variants: {
    size: {
      sm: "h-3.5 w-3.5",
      default: "h-4 w-4",
      lg: "h-6 w-6",
      xl: "h-8 w-8",
    },
  },
  defaultVariants: { size: "default" },
})

const classes = computed(() => cn(spinnerVariants({ size: props.size }), props.class))
</script>

<template>
  <span class="inline-flex items-center gap-2" role="status">
    <Loader2 :class="classes" aria-hidden="true" />
    <span v-if="label" class="text-sm text-muted-foreground">{{ label }}</span>
    <span v-else class="sr-only">Loading</span>
  </span>
</template>
