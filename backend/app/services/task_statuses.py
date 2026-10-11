"""Current task lifecycle categories shared by services and natural references."""
ACTIVE_TASK_STATUSES = {
    "queued", "waiting_for_worker", "running", "waiting_for_input", "waiting_until", "paused"
}
