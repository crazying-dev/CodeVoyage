import { createRouter, createWebHistory} from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

// 路由类型 RouteRecordRaw TS内置类型
const routes: RouteRecordRaw[] = [
    {
        path: '/',
        name: 'Home',
        component: () => import('../components/Home.vue')
    },
    {
        path: '/about',
        name: 'About',
        component: () => import('../components/About.vue')
    },
    // 404
    {
        path: '/:pathMatch(.*)*',
        redirect: '/'
    }
]

const router = createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
    // hash模式: history: createWebHashHistory(),
    routes
})

export default router
