from __future__ import annotations
from typing import Any, Dict
from kernel.ports.toolrunner import ToolRunner

class RateLimitError(Exception): ...
class UpstreamError(Exception): ...
class CrashAfterStep(Exception): ...

class StubToolRunner(ToolRunner):
    def __init__(self):
        self.email_send_calls = 0
        self.ticket_create_calls = 0
        self.rate_limit_first_email = False

    async def call(self, tenant_id: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool == "crm.get_customer":
            return {"id": args["customer_id"], "name":"Nina", "email":"nina@example.com"}

        if tool == "memory.search":
            return {"matches":[{"doc_id":"doc_kb_refunds","summary":"UE 14 jours + défaut => preuve + remboursement/remplacement"}]}

        if tool == "ticket.create":
            self.ticket_create_calls += 1
            return {"ticket_id":"tkt_5001", "status":"open"}

        if tool == "ticket.add_comment":
            return {"comment_id":"cmt_1", "status":"ok"}

        if tool == "internal.llm.draft_reply":
            ticket_id = (args.get("facts") or {}).get("ticket_id", "tkt_XXXX")
            return {"subject": f"On s’occupe de votre demande ({ticket_id})", "body":"Bonjour, nous allons vous aider. Merci d’envoyer une photo/numéro de série."}

        if tool == "email.send":
            self.email_send_calls += 1
            if self.rate_limit_first_email and self.email_send_calls == 1:
                raise RateLimitError("429 rate limit")
            return {"message_id":"msg_9012", "status":"sent"}

        raise UpstreamError(f"Unknown tool: {tool}")
