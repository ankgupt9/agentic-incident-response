"""LLM judge: compares a diagnosis with the labeled truth. Blind to which arm produced it.

Uses the model gateway (hard rule 4) and a different, larger model than the agent under test, so the
agent does not grade itself. The judge sees only the truth and the diagnosis text, never the evidence.
"""

import json

from pydantic import BaseModel, ValidationError

from agent.gateway import ModelGateway

SYSTEM = """You grade incident diagnoses against a known answer. Be strict and literal.

root_cause:
- correct: names the same underlying cause as the truth (the mechanism, not just the symptom). Different wording and extra detail are fine.
- partial: points at the right component or mechanism family but misses or misstates the specific cause, or lists it only as one of several equally likely hypotheses.
- incorrect: wrong cause, restates only the symptom, or says the cause is unknown or not established.

fix:
- good: the recommended action would resolve the true cause and matches the suggested fix in substance.
- partial: relevant but vague or incomplete.
- poor: would not fix the true cause, or is unrelated.

Call submit_verdict once."""

VERDICT_TOOL = {
    "name": "submit_verdict",
    "description": "Submit the grade for this diagnosis.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
            "fix": {"type": "string", "enum": ["good", "partial", "poor"]},
            "reasoning": {"type": "string", "description": "One or two sentences"},
        },
        "required": ["root_cause", "fix", "reasoning"],
        "additionalProperties": False,
    },
}


class Verdict(BaseModel):
    root_cause: str
    fix: str
    reasoning: str


def judge_prompt(truth_category: str, truth_statement: str, truth_fix: str, root_cause: str, recommended_action: str) -> str:
    return (
        f"TRUTH\ncategory: {truth_category}\nroot cause: {truth_statement}\nsuggested fix: {truth_fix}\n\n"
        f"DIAGNOSIS TO GRADE\nroot cause: {root_cause}\nrecommended action: {recommended_action}"
    )


async def judge(gateway: ModelGateway, truth_category: str, truth_statement: str, truth_fix: str, root_cause: str, recommended_action: str) -> tuple[Verdict, float]:
    """Returns the verdict and what the judging cost in USD. One retry if the verdict is malformed."""
    messages = [{"role": "user", "content": judge_prompt(truth_category, truth_statement, truth_fix, root_cause, recommended_action)}]
    cost = 0.0
    for _ in range(2):
        response = await gateway.complete(system=SYSTEM, messages=messages, tools=[VERDICT_TOOL])
        cost += response.cost_usd
        call = next((c for c in response.tool_calls if c.name == "submit_verdict"), None)
        if call:
            try:
                return Verdict.model_validate(call.input), cost
            except ValidationError:
                pass
        messages = messages + [response.assistant_message(), {"role": "user", "content": "Call submit_verdict with root_cause, fix and reasoning."}]
    raise RuntimeError("judge did not return a valid verdict")


def dump(verdict: Verdict) -> str:
    return json.dumps(verdict.model_dump())
