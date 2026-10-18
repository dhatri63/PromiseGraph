import json
import os
import time
import urllib.error
import urllib.request

from agent import (
    find_related_commitments,
    get_evidence_path,
    mark_commitment_failed,
    mark_entity_disrupted,
    resolve_entity,
    run_agent as deterministic_run_agent,
    trace_impact,
)

from recovery_engine import analyze_recovery
from memory_store import retrieve_prior_incidents


MODEL = "gemini-3.8-flash"

URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{MODEL}:generateContent"
)


FUNCTIONS = [
    {
        "name": "resolve_graph_entity",
        "description": (
            "Resolve a natural-language business disruption "
            "to a Vendor, Resource, or Commitment stored in "
            "the PromiseGraph FalkorDB graph. Use this first "
            "when the affected graph entity is not yet known."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The original disruption message."
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "find_related_commitments",
        "description": (
            "Find commitments connected through FalkorDB to "
            "the resolved Vendor, Resource, or Commitment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Resolved graph entity id."
                }
            },
            "required": ["entity_id"]
        }
    },
    {
        "name": "trace_impact",
        "description": (
            "Traverse multi-hop DEPENDS_ON relationships in "
            "FalkorDB to discover downstream commitments "
            "affected by a failed commitment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "commitment_id": {
                    "type": "string"
                }
            },
            "required": ["commitment_id"]
        }
    },
    {
        "name": "retrieve_prior_incidents",
        "description": (
            "Retrieve persistent prior incident and applied-recovery "
            "memory from FalkorDB for the identified commitment. "
            "Use this after the relevant commitment is known so the "
            "current decision can be grounded in organizational history."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "commitment_id": {
                    "type": "string"
                }
            },
            "required": ["commitment_id"]
        }
    },
    {
        "name": "analyze_recovery",
        "description": (
            "Generate and evaluate recovery options using "
            "FalkorDB company facts, supplier capacity, "
            "recovery capabilities, budget constraints and "
            "timing tolerances."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "commitment_id": {
                    "type": "string"
                }
            },
            "required": ["commitment_id"]
        }
    }
]


def _call_gemini(contents):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    payload = {
        "contents": contents,
        "tools": [{"functionDeclarations": FUNCTIONS}],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY"
            }
        }
    }

    transient_http_codes = {
        429,
        500,
        502,
        503,
        504
    }

    last_error = None

    for attempt in range(2):
        request = urllib.request.Request(
            URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=45
            ) as response:
                return json.loads(
                    response.read().decode("utf-8")
                )

        except urllib.error.HTTPError as error:
            last_error = error

            if (
                error.code not in transient_http_codes
                or attempt == 1
            ):
                raise

        except urllib.error.URLError as error:
            last_error = error

            if attempt == 1:
                raise

        time.sleep(1.25)

    raise last_error


def _compact_result_for_model(tool_name, result):
    if tool_name == "resolve_graph_entity":
        if not result.get("found"):
            return result
        entity = result["entity"]
        return {
            "found": True,
            "entity": {
                "id": entity.get("id"),
                "name": entity.get("name"),
                "type": entity.get("type"),
                "kind": entity.get("kind")
            },
            "confidence": result.get("confidence")
        }

    if tool_name == "find_related_commitments":
        commitments = result.get("commitments", [])
        return {
            "commitments": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "kind": item.get("kind")
                }
                for item in commitments
            ]
        }

    if tool_name == "trace_impact":
        downstream = result.get("downstream_commitments", [])
        return {
            "commitment_id": result.get("commitment_id"),
            "downstream_commitments": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "distance": item.get("distance")
                }
                for item in downstream
            ]
        }

    if tool_name == "retrieve_prior_incidents":
        incidents = result.get("prior_incidents", [])
        return {
            "commitment_id": result.get("commitment_id"),
            "memory_count": len(incidents),
            "prior_incidents": [
                {
                    "id": item.get("id"),
                    "created_at": item.get("created_at"),
                    "recovery_name": item.get("recovery_name"),
                    "recovery_cost": item.get("recovery_cost"),
                    "recovery_delay_hours": item.get("recovery_delay_hours"),
                    "outcome": item.get("outcome")
                }
                for item in incidents
            ]
        }

    if tool_name == "analyze_recovery":
        recommended = result.get("recommended_recovery")
        compact_recommended = None
        if recommended:
            compact_recommended = {
                "id": recommended.get("id"),
                "name": recommended.get("name"),
                "cost": recommended.get("cost"),
                "delay_hours": recommended.get("delay_hours")
            }
        return {
            "commitment_id": result.get("commitment_id"),
            "generated_option_count": result.get("generated_option_count"),
            "recommended_recovery": compact_recommended
        }

    return result


def _build_explanation(
    entity,
    commitment,
    affected,
    recovery,
    prior_incidents
):
    recommended = recovery.get("recommended_recovery")

    memory_text = ""

    if prior_incidents:
        latest = prior_incidents[0]
        memory_count = len(prior_incidents)
        incident_word = (
            "incident"
            if memory_count == 1
            else "incidents"
        )

        memory_text = (
            f" FalkorDB memory contains {memory_count} prior "
            f"{incident_word} for this commitment; the latest applied "
            f"recovery was {latest.get('recovery_name')}. "
            "Current feasibility was re-evaluated against the present "
            "graph constraints rather than copied from memory."
        )

    if recommended:
        return (
            f"{entity['name']} was identified as the disrupted "
            f"{entity['type'].lower()}. "
            f"The graph connected it to {commitment['name']}. "
            f"{len(affected)} downstream commitment(s) are affected."
            f"{memory_text} "
            f"PromiseGraph evaluated {recovery['generated_option_count']} "
            f"recovery option(s) and recommends {recommended['name']} "
            f"at a cost of ₹{recommended['cost']} with "
            f"{recommended['delay_hours']} hours of delay."
        )

    return (
        f"{entity['name']} was identified as the disrupted "
        f"{entity['type'].lower()}. "
        f"The graph connected it to {commitment['name']}. "
        f"{len(affected)} downstream commitment(s) are affected."
        f"{memory_text} "
        f"PromiseGraph evaluated {recovery['generated_option_count']} "
        f"recovery option(s), but none satisfy the current graph constraints."
    )


def run_llm_agent(message):
    state = {
        "entity": None,
        "confidence": 0,
        "commitment": None,
        "affected": None,
        "prior_incidents": None,
        "memory_checked": False,
        "recovery": None,
    }

    tool_trace = []

    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": (
                        "You are the tool-selection layer for PromiseGraph, "
                        "an operational recovery agent backed by FalkorDB.\n\n"
                        "Investigate the disruption using the available graph tools. "
                        "Do not invent company facts. Call exactly ONE tool at a time. "
                        "Later tool choices must be based on results returned from the graph.\n\n"
                        "You must identify the relevant graph entity and commitment, "
                        "retrieve downstream impact, check persistent prior-incident "
                        "memory for that commitment, and evaluate recovery options. "
                        "After a commitment is identified, use retrieve_prior_incidents "
                        "before finishing the workflow. Memory is historical context only; "
                        "current graph constraints must still determine feasibility. "
                        "Do not repeat a successful tool unnecessarily.\n\n"
                        f"Disruption: {message}"
                    )
                }
            ]
        }
    ]

    for step_number in range(1, 8):
        response = _call_gemini(contents)
        model_content = response["candidates"][0]["content"]
        function_call = None

        for part in model_content.get("parts", []):
            if "functionCall" in part:
                function_call = part["functionCall"]
                break

        if not function_call:
            break

        tool_name = function_call.get("name")
        arguments = function_call.get("args", {})
        call_id = function_call.get("id")

        if tool_name == "resolve_graph_entity":
            entity, confidence = resolve_entity(arguments.get("message", message))

            if entity is None or confidence < 0.28:
                result = {
                    "found": False,
                    "message": "No graph entity could be resolved confidently."
                }
            else:
                state["entity"] = entity
                state["confidence"] = confidence
                mark_entity_disrupted(entity)

                if entity["type"] == "Commitment":
                    full_commitments = find_related_commitments(entity)
                    state["commitment"] = (
                        full_commitments[0] if full_commitments else entity
                    )
                    mark_commitment_failed(entity["id"])

                result = {
                    "found": True,
                    "entity": entity,
                    "confidence": round(confidence, 3)
                }

        elif tool_name == "find_related_commitments":
            entity = state.get("entity")

            if not entity:
                result = {
                    "error": "Resolve the disrupted graph entity first."
                }
            else:
                commitments = find_related_commitments(entity)
                if commitments:
                    state["commitment"] = commitments[0]
                    mark_commitment_failed(commitments[0]["id"])
                result = {"commitments": commitments}

        elif tool_name == "trace_impact":
            commitment = state.get("commitment")
            commitment_id = arguments.get("commitment_id")
            if not commitment_id and commitment:
                commitment_id = commitment["id"]

            if not commitment_id:
                result = {
                    "error": "No affected commitment has been identified yet."
                }
            else:
                affected = trace_impact(commitment_id)
                state["affected"] = affected
                result = {
                    "commitment_id": commitment_id,
                    "downstream_commitments": affected
                }

        elif tool_name == "retrieve_prior_incidents":
            commitment = state.get("commitment")
            commitment_id = arguments.get("commitment_id")
            if not commitment_id and commitment:
                commitment_id = commitment["id"]

            if not commitment_id:
                result = {
                    "error": "No affected commitment has been identified yet."
                }
            else:
                prior_incidents = retrieve_prior_incidents(
                    commitment_id,
                    limit=3
                )
                state["prior_incidents"] = prior_incidents
                state["memory_checked"] = True
                result = {
                    "commitment_id": commitment_id,
                    "prior_incidents": prior_incidents
                }

        elif tool_name == "analyze_recovery":
            commitment = state.get("commitment")
            commitment_id = arguments.get("commitment_id")
            if not commitment_id and commitment:
                commitment_id = commitment["id"]

            if not commitment_id:
                result = {
                    "error": "No affected commitment has been identified yet."
                }
            else:
                recovery = analyze_recovery(commitment_id)
                state["recovery"] = recovery
                result = recovery

        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        tool_trace.append({
            "step": step_number,
            "tool": tool_name,
            "arguments": arguments
        })

        contents.append(model_content)

        function_response = {
            "name": tool_name,
            "response": {
                "result": _compact_result_for_model(tool_name, result)
            }
        }

        if call_id:
            function_response["id"] = call_id

        contents.append({
            "role": "user",
            "parts": [
                {"functionResponse": function_response}
            ]
        })

        if (
            state["entity"]
            and state["commitment"]
            and state["affected"] is not None
            and state["memory_checked"]
            and state["recovery"]
        ):
            break

    entity = state.get("entity")
    commitment = state.get("commitment")
    affected = state.get("affected")
    prior_incidents = state.get("prior_incidents") or []
    recovery = state.get("recovery")

    if (
        not entity
        or not commitment
        or affected is None
        or not state.get("memory_checked")
        or not recovery
    ):
        raise RuntimeError(
            "Gemini tool routing did not complete the recovery workflow."
        )

    evidence_path = get_evidence_path(entity, commitment["id"])
    recommended = recovery.get("recommended_recovery")

    return {
        "status": "analyzed",
        "input": message,
        "detected_entity": entity,
        "matched_commitment": commitment,
        "match_confidence": round(state["confidence"], 3),
        "affected_commitments": affected,
        "prior_incidents": prior_incidents,
        "memory_checked": True,
        "evidence_path": evidence_path,
        "generated_options": recovery.get("options", []),
        "recommended_recovery": recommended,
        "explanation": _build_explanation(
            entity,
            commitment,
            affected,
            recovery,
            prior_incidents
        ),
        "tool_trace": tool_trace,
        "agent_mode": "gemini_tool_router",
        "graph_grounded": True
    }


def _attach_fallback_memory(result):
    if not isinstance(result, dict):
        return result

    commitment = result.get("matched_commitment")
    commitment_id = (
        commitment.get("id")
        if isinstance(commitment, dict)
        else None
    )

    prior_incidents = []

    if commitment_id:
        try:
            prior_incidents = retrieve_prior_incidents(
                commitment_id,
                limit=3
            )

            trace = result.get("tool_trace")
            if isinstance(trace, list):
                trace.append({
                    "tool": "retrieve_prior_incidents",
                    "count": len(prior_incidents),
                    "mode": "deterministic_fallback"
                })

        except Exception as memory_error:
            print(
                "[PromiseGraph] Fallback memory retrieval error:",
                memory_error
            )

    result["prior_incidents"] = prior_incidents
    result["memory_checked"] = bool(commitment_id)
    result["memory_retrieval_mode"] = "deterministic_fallback"

    return result


def run_agent_with_fallback(message):
    if not os.environ.get("GEMINI_API_KEY"):
        result = deterministic_run_agent(message)
        result = _attach_fallback_memory(result)

        if isinstance(result, dict):
            result["agent_mode"] = "deterministic_fallback"
            result["graph_grounded"] = True

        return result

    try:
        return run_llm_agent(message)

    except Exception as error:
        print(
            "[PromiseGraph] Gemini router fallback:",
            error
        )

        result = deterministic_run_agent(message)
        result = _attach_fallback_memory(result)

        if isinstance(result, dict):
            result["agent_mode"] = "deterministic_fallback"
            result["graph_grounded"] = True
            result["llm_fallback_reason"] = type(error).__name__

        return result