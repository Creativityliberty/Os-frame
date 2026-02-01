export type MissionCreateOut = { task_id: string; run_id: string };

export type TaskBudgetUpdateEvent = { type: "TaskBudgetUpdateEvent"; ts: string; task_id: string; run_id: string; used: any; limits: any; _seq?: number };

export type SSEEvent = Record<string, any> & { _seq?: number };

export type ApprovalDecision = "approved" | "denied";
