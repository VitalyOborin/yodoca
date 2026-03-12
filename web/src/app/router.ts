import { createRouter, createWebHistory } from 'vue-router';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/chat',
    },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('@/pages/chat/ChatPage.vue'),
    },
    {
      path: '/inbox',
      name: 'inbox',
      component: () => import('@/pages/inbox/InboxPage.vue'),
    },
    {
      path: '/projects',
      name: 'projects',
      component: () => import('@/pages/projects/ProjectsPage.vue'),
    },
    {
      path: '/schedule',
      name: 'schedule',
      component: () => import('@/pages/schedule/SchedulePage.vue'),
    },
    {
      path: '/agents',
      name: 'agents',
      component: () => import('@/pages/agents/AgentsPage.vue'),
    },
  ],
});

export default router;
