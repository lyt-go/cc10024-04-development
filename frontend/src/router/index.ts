import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import SiteAttributionView from '@/views/SiteAttributionView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 站点归属是跳转采集的正式归属清单，有自己的登记/回查/去重分组工作台。
    { path: '/site_attribution', name: 'site-attribution', component: SiteAttributionView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
