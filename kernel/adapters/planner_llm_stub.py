from __future__ import annotations
from typing import Any, Dict, List
from kernel.ports.planner import LLMPlanner

class StubPlanner(LLMPlanner):
    async def select_nodes(self, user_message: str, trees: List[Dict[str, Any]], policy_snippets: List[Dict[str, Any]]) -> List[str]:
        return ["SUPPORT/KB/Refunds", "SUPPORT/PLAYBOOKS/RefundFlow", "CUSTOMERS/cust_123"]

    async def build_plan(self, context_pack: Dict[str, Any]) -> Dict[str, Any]:
        # Support v1 plan aligned with registry
        tenant_id = context_pack["tenant_id"]
        requires_approval = True if tenant_id == "tenant_demo" else False
        return {
            "type":"plan",
            "goal":"Refund request: reply + ticket + email",
            "controls":{
                "requires_approval": requires_approval,
                "max_tool_calls": 12,
                "allowed_tools":["crm.get_customer","memory.search","ticket.create","ticket.add_comment","internal.llm.draft_reply","email.send"]
            },
            "steps":[
                {"step_id":"s1","action_id":"act_crm_get_customer_v1","args":{"customer_id":"cust_123"}},
                {"step_id":"s2","action_id":"act_memory_search_v1","args":{"query":"refund defective UE 14 days","top_k":5}},
                {"step_id":"s3","action_id":"act_ticket_create_v1","args":{
                    "customer_id":"cust_123",
                    "subject":"Remboursement - produit défectueux",
                    "description":"Produit non fonctionnel. Demander preuve et proposer solution.",
                    "priority":"normal",
                    "idempotency_key":f"idem:ticket:create:{tenant_id}:cust_123:ord_778"
                }},
                {"step_id":"s4","action_id":"act_ticket_add_comment_v1","args":{
                    "ticket_id":"$s3.output.ticket_id",
                    "comment":"Résumé policy + next steps",
                    "public":False,
                    "idempotency_key":f"idem:ticket:comment:{tenant_id}:$s3.output.ticket_id:refund"
                }, "depends_on":["s3"]},
                {"step_id":"s5","action_id":"act_draft_reply_v1","args":{
                    "language":"fr-FR",
                    "tone":"support_pro",
                    "facts":{"ticket_id":"$s3.output.ticket_id"},
                    "policy_snippets":"$s2.output.matches"
                }, "depends_on":["s1","s2","s3","s4"]},
                {"step_id":"s6","action_id":"act_email_send_v1","args":{
                    "to":"$s1.output.email",
                    "subject":"On s’occupe de votre demande (ticket $s3.output.ticket_id)",
                    "body":"$s5.output.body",
                    "idempotency_key":f"idem:email:refund:{tenant_id}:cust_123:ord_778"
                }, "depends_on":["s5"]}
            ]
        }
