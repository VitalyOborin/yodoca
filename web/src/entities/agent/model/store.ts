import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { AgentPhase } from './types';
import { runAgent as apiRunAgent } from '@/shared/api';
import type { ChatMessage, RunAgentOptions } from '@/shared/api';

export const useAgentStore = defineStore('agent', () => {
  const phase = ref<AgentPhase>('idle');
  const currentStep = ref<string>('');

  function setPhase(next: AgentPhase, step?: string) {
    phase.value = next;
    if (step !== undefined) currentStep.value = step;
  }

  async function runAgent(
    threadId: string,
    messages: ChatMessage[],
    options?: RunAgentOptions,
  ): Promise<string> {
    setPhase('thinking', 'Processing your request...');
    try {
      const text = await apiRunAgent(threadId, messages, options);
      setPhase('complete', 'Done');
      return text;
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Agent run failed';
      setPhase('error', message);
      throw e;
    }
  }

  function resetPhase() {
    setPhase('idle', '');
  }

  return {
    phase,
    currentStep,
    setPhase,
    runAgent,
    resetPhase,
  };
});
