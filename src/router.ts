import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/overview' },
    { path: '/login', name: 'login', component: () => import('./views/Login.vue') },
    { path: '/overview', name: 'overview', component: () => import('./views/Overview.vue') },
    { path: '/chat', name: 'chat', component: () => import('./views/Chat.vue') },
    { path: '/config', name: 'config', component: () => import('./views/Config.vue') },
    { path: '/diagnostics', name: 'diagnostics', component: () => import('./views/Diagnostics.vue') },
    { path: '/logs', name: 'logs', component: () => import('./views/Logs.vue') },
    { path: '/agreement', name: 'agreement', component: () => import('./views/Agreement.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/overview' },
  ],
})

export default router
