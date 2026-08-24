const TASK_TERMINAL_EVENTS = new Set(["task.completed", "task.failed", "task.cancelled", "task.interrupted"]);
const RUN_TERMINAL_EVENTS = new Set(["run.completed", "run.failed", "run.cancelled", "run.interrupted"]);

export function isVoiceTerminalEvent(eventType: string): boolean {
  return TASK_TERMINAL_EVENTS.has(eventType) || RUN_TERMINAL_EVENTS.has(eventType);
}

export function isVoiceSpeechEvent(eventType: string): boolean {
  return eventType === "conversation.assistant.message.completed"
    || eventType === "agent.question.asked"
    || eventType.startsWith("task.")
    || RUN_TERMINAL_EVENTS.has(eventType);
}
