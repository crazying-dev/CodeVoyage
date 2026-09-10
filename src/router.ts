import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/console' },
    {
      path: '/login',
      name: 'login',
      component: () => import('./views/Login.vue'),
    },
    {
      path: '/console',
      name: 'console',
      component: () => import('./views/Console.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/console' },
  ],
})

export default router
