import { createRouter, createWebHistory } from "vue-router"

import ApplicationsView from "./views/ApplicationsView.vue"
import CacheSettingsView from "./views/CacheSettingsView.vue"
import DashboardView from "./views/DashboardView.vue"
import JobDatabaseView from "./views/JobDatabaseView.vue"
import JobsView from "./views/JobsView.vue"
import JobPoolQualityView from "./views/JobPoolQualityView.vue"
import MaterialsLibraryView from "./views/MaterialsLibraryView.vue"
import MaterialsQuestionsView from "./views/MaterialsQuestionsView.vue"
import MaterialsView from "./views/MaterialsView.vue"
import ProfileView from "./views/ProfileView.vue"
import ReviewQueueView from "./views/ReviewQueueView.vue"
import SettingsView from "./views/SettingsView.vue"
import TasksView from "./views/TasksView.vue"
import TemplateEditorView from "./views/TemplateEditorView.vue"
import TemplateLibraryView from "./views/TemplateLibraryView.vue"

const routes = [
  { path: "/", component: DashboardView, meta: { label: "Dashboard" } },
  { path: "/jobs", component: JobsView, meta: { label: "Jobs" } },
  { path: "/jobs/quality", component: JobPoolQualityView, meta: { label: "Search Quality" } },
  { path: "/jobs-db", component: JobDatabaseView, meta: { label: "Job Database" } },
  { path: "/materials", component: MaterialsView, meta: { label: "Materials" } },
  {
    path: "/materials/library",
    component: MaterialsLibraryView,
    meta: { label: "Document Library" },
  },
  {
    path: "/materials/templates",
    component: TemplateLibraryView,
    meta: { label: "Template Library" },
  },
  {
    path: "/materials/questions",
    component: MaterialsQuestionsView,
    meta: { label: "Application Questions" },
  },
  {
    path: "/materials/templates/:documentType/:templateId",
    component: TemplateEditorView,
    meta: { label: "Edit Template" },
    props: true,
  },
  { path: "/applications", component: ApplicationsView, meta: { label: "Applications" } },
  { path: "/profile", component: ProfileView, meta: { label: "Profile" } },
  { path: "/profile/:profileId", component: ProfileView, meta: { label: "Profile" } },
  { path: "/settings", component: SettingsView, meta: { label: "Settings" } },
  {
    path: "/settings/cache",
    component: CacheSettingsView,
    meta: { label: "Cache" },
  },
  { path: "/tasks", component: TasksView, meta: { label: "Plans" } },
  { path: "/review", component: ReviewQueueView, meta: { label: "Awaiting Review" } },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
