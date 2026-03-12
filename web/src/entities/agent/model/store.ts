import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import type { AgentAuditItem, AgentDraft, AgentPhase } from './types';

export const useAgentStore = defineStore('agent', () => {
  const phase = ref<AgentPhase>('idle');
  const confidence = ref(82);
  const intentPreview = ref<string[]>([
    'Понять запрос и проверить контекст thread',
    'Сформировать план действий и показать черновик',
    'Выполнить шаги и записать лог действий',
  ]);

  const currentStep = ref<string>('');
  const auditTrail = ref<AgentAuditItem[]>([]);
  const drafts = ref<AgentDraft[]>([
    {
      id: 'draft-1',
      title: 'План внедрения split-screen UI',
      description: '5 шагов с изменениями layout, token system и agent state handling.',
      status: 'pending',
    },
  ]);

  const canStop = computed(() => phase.value === 'thinking' || phase.value === 'acting');

  function setPhase(next: AgentPhase, step?: string) {
    phase.value = next;
    if (step) currentStep.value = step;
  }

  function startRun(userPrompt: string) {
    setPhase('thinking', 'Анализирую контекст и формирую intent preview');
    confidence.value = Math.max(62, Math.min(96, 70 + Math.round(Math.random() * 24)));
    intentPreview.value = [
      'Вытащить цели из пользовательского запроса',
      'Сформировать план с проверяемыми шагами',
      'Подготовить результат и запросить подтверждение для risky action',
    ];

    pushAudit('pending', 'Получен запрос', userPrompt);
    pushAudit('running', 'Reasoning', 'Собираю контекст по треду и истории действий');
  }

  function beginExecution() {
    setPhase('acting', 'Выполняю шаги плана и обновляю audit trail');
    pushAudit('running', 'Execution', 'Запускаю инструменты и синхронизирую workspace');
  }

  function requireInput(prompt: string) {
    setPhase('waiting_input', prompt);
    pushAudit('pending', 'Требуется подтверждение', prompt);
  }

  function completeRun(summary: string) {
    setPhase('complete', 'Готово');
    pushAudit('done', 'Завершено', summary);
  }

  function failRun(reason: string) {
    setPhase('error', 'Ошибка выполнения');
    pushAudit('error', 'Ошибка', reason);
  }

  function stopRun() {
    setPhase('waiting_input', 'Выполнение остановлено пользователем');
    pushAudit('error', 'Emergency stop', 'Пользователь остановил выполнение');
  }

  function updateDraftStatus(id: string, status: AgentDraft['status']) {
    const draft = drafts.value.find((item) => item.id === id);
    if (!draft) return;
    draft.status = status;
    if (status === 'approved') {
      pushAudit('done', 'Draft approved', `Черновик «${draft.title}» подтвержден`);
    }
    if (status === 'rejected') {
      pushAudit('error', 'Draft rejected', `Черновик «${draft.title}» отклонен`);
    }
  }

  function pushAudit(status: AgentAuditItem['status'], title: string, detail?: string) {
    auditTrail.value.unshift({
      id: `audit-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`,
      at: new Date(),
      status,
      title,
      detail,
    });

    if (auditTrail.value.length > 40) {
      auditTrail.value.length = 40;
    }
  }

  return {
    phase,
    confidence,
    intentPreview,
    currentStep,
    auditTrail,
    drafts,
    canStop,
    setPhase,
    startRun,
    beginExecution,
    requireInput,
    completeRun,
    failRun,
    stopRun,
    updateDraftStatus,
  };
});
