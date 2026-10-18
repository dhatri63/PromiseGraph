import re
from difflib import SequenceMatcher

from falkordb import FalkorDB
from recovery_engine import analyze_recovery
from memory_store import record_incident_memory


db = FalkorDB(
    host="localhost",
    port=6379
)

graph = db.select_graph(
    "PromiseGraph"
)


# ==================================================
# READ GRAPH ENTITIES
# ==================================================

def get_graph_entities():

    result = graph.query("""
        MATCH (n)

        WHERE
            n:Commitment
            OR n:Vendor
            OR n:Resource

        RETURN
            n.id,
            n.name,
            labels(n),
            n.status,
            n.kind

        ORDER BY n.id
    """)

    entities = []

    for row in result.result_set:

        labels_list = (
            row[2]
            if row[2]
            else []
        )

        entities.append({
            "id": row[0],
            "name": row[1],
            "type":
                labels_list[0]
                if labels_list
                else "Node",
            "status": row[3],
            "kind": row[4]
        })

    return entities


# ==================================================
# NATURAL-LANGUAGE ENTITY RESOLUTION
# ==================================================

def resolve_entity(message):

    entities = get_graph_entities()

    stop_words = {
        "the", "a", "an",
        "is", "are", "was", "were",
        "by", "to", "of", "our",
        "we", "cannot", "cant",
        "can", "not", "will",
        "be", "has", "have", "had",
        "on", "in"
    }


    def tokens(text):

        words = re.findall(
            r"[a-z0-9]+",
            text.lower()
        )

        return {
            word
            for word in words
            if word not in stop_words
        }


    message_lower = message.lower()

    message_tokens = tokens(
        message
    )

    best = None
    best_score = 0


    for entity in entities:

        entity_name = (
            entity["name"] or ""
        ).lower()

        entity_type = (
            entity["type"] or ""
        ).lower()

        entity_kind = (
            entity["kind"] or ""
        ).lower().replace(
            "_",
            " "
        )


        searchable = (
            f"{entity['id']} "
            f"{entity_name} "
            f"{entity_type} "
            f"{entity_kind}"
        )


        target_tokens = tokens(
            searchable
        )


        overlap = (
            message_tokens
            & target_tokens
        )


        # Never invent a match when there
        # is no meaningful overlap.
        if not overlap:
            continue


        overlap_score = (
            len(overlap)
            /
            max(
                1,
                min(
                    len(message_tokens),
                    len(target_tokens)
                )
            )
        )


        similarity = SequenceMatcher(
            None,
            message_lower,
            searchable
        ).ratio()


        score = (
            overlap_score * 0.75
            +
            similarity * 0.25
        )


        # Exact entity name = strong signal.
        if (
            entity_name
            and entity_name
            in message_lower
        ):
            score += 1.0


        # Vendor aliases
        if (
            entity["type"] == "Vendor"
            and (
                "supplier" in message_lower
                or "vendor" in message_lower
            )
        ):
            score += 0.15


        # Resource aliases
        if (
            entity["type"] == "Resource"
            and (
                "component" in message_lower
                or "resource" in message_lower
                or "material" in message_lower
            )
        ):
            score += 0.15


        if (
            entity_kind
            and entity_kind
            in message_lower
        ):
            score += 0.15


        if score > best_score:

            best_score = score
            best = entity


    return best, best_score


# ==================================================
# FIND COMMITMENT CONNECTED TO ENTITY
# ==================================================

def find_related_commitments(entity):

    entity_type = entity["type"]
    entity_id = entity["id"]

    rows = []


    # ==============================================
    # COMMITMENT
    # ==============================================

    if entity_type == "Commitment":

        result = graph.query("""
            MATCH (c:Commitment {id:$id})

            RETURN
                c.id,
                c.name,
                c.kind,
                c.status,
                c.priority
        """, {
            "id": entity_id
        })

        rows.extend(
            result.result_set
        )


    # ==============================================
    # VENDOR
    # ==============================================

    elif entity_type == "Vendor":

        # Vendor directly responsible for commitment
        direct_result = graph.query("""
            MATCH
                (v:Vendor {id:$id})
                -[:MADE]->
                (c:Commitment)

            RETURN
                c.id,
                c.name,
                c.kind,
                c.status,
                c.priority
        """, {
            "id": entity_id
        })

        rows.extend(
            direct_result.result_set
        )


        # Vendor supplies a required resource
        supply_result = graph.query("""
            MATCH
                (v:Vendor {id:$id})
                -[:CAN_SUPPLY]->
                (r:Resource)
                <-[:REQUIRES]-
                (c:Commitment)

            RETURN
                c.id,
                c.name,
                c.kind,
                c.status,
                c.priority
        """, {
            "id": entity_id
        })

        rows.extend(
            supply_result.result_set
        )


    # ==============================================
    # RESOURCE
    # ==============================================

    elif entity_type == "Resource":

        result = graph.query("""
            MATCH
                (c:Commitment)
                -[:REQUIRES]->
                (r:Resource {id:$id})

            RETURN
                c.id,
                c.name,
                c.kind,
                c.status,
                c.priority
        """, {
            "id": entity_id
        })

        rows.extend(
            result.result_set
        )


    else:
        return []


    # Remove duplicate commitments
    commitments = {}

    for row in rows:

        commitment = {
            "id": row[0],
            "name": row[1],
            "kind": row[2],
            "status": row[3],
            "priority": row[4]
        }

        commitments[
            commitment["id"]
        ] = commitment


    return list(
        commitments.values()
    )


# ==================================================
# CHANGE GRAPH STATE
# ==================================================

def mark_entity_disrupted(entity):

    entity_type = entity["type"]
    entity_id = entity["id"]


    if entity_type == "Commitment":

        graph.query("""
            MATCH (c:Commitment {id:$id})
            SET c.status = 'failed'
        """, {
            "id": entity_id
        })


    elif entity_type == "Vendor":

        graph.query("""
            MATCH (v:Vendor {id:$id})
            SET v.status = 'disrupted'
        """, {
            "id": entity_id
        })


    elif entity_type == "Resource":

        graph.query("""
            MATCH (r:Resource {id:$id})
            SET r.status = 'disrupted'
        """, {
            "id": entity_id
        })


# ==================================================
# MARK CONNECTED COMMITMENT
# ==================================================

def mark_commitment_failed(commitment_id):

    graph.query("""
        MATCH (c:Commitment {id:$id})
        SET c.status = 'failed'
    """, {
        "id": commitment_id
    })


# ==================================================
# MULTI-HOP IMPACT
# ==================================================

def trace_impact(commitment_id):

    result = graph.query("""
        MATCH p =
            (affected:Commitment)
            -[:DEPENDS_ON*1..6]->
            (failed:Commitment {id:$id})

        RETURN
            affected.id,
            affected.name,
            affected.priority,
            length(p)

        ORDER BY length(p)
    """, {
        "id": commitment_id
    })


    return [
        {
            "id": row[0],
            "name": row[1],
            "priority": row[2],
            "distance": row[3]
        }
        for row in result.result_set
    ]


# ==================================================
# EXPLAINABLE GRAPH EVIDENCE PATH
# ==================================================

def get_evidence_path(
    entity,
    commitment_id
):

    evidence = []

    entity_type = entity["type"]
    entity_id = entity["id"]


    # ----------------------------------------------
    # Get matched commitment name
    # ----------------------------------------------

    commitment_result = graph.query("""
        MATCH (
            c:Commitment {id:$id}
        )

        RETURN c.name
    """, {
        "id": commitment_id
    })


    if commitment_result.result_set:

        commitment_name = (
            commitment_result
            .result_set[0][0]
        )

    else:

        commitment_name = commitment_id


    # ==============================================
    # COMMITMENT INPUT
    # ==============================================

    if entity_type == "Commitment":

        evidence.append({
            "from":
                entity["name"],

            "relationship":
                "MATCHED_AS",

            "to":
                "failed commitment"
        })


    # ==============================================
    # VENDOR INPUT
    # ==============================================

    elif entity_type == "Vendor":

        # Direct vendor → commitment relationship
        direct = graph.query("""
            MATCH
                (v:Vendor {id:$vendor_id})
                -[:MADE]->
                (c:Commitment {
                    id:$commitment_id
                })

            RETURN
                v.name,
                c.name
        """, {
            "vendor_id":
                entity_id,

            "commitment_id":
                commitment_id
        })


        if direct.result_set:

            row = direct.result_set[0]

            evidence.append({
                "from":
                    row[0],

                "relationship":
                    "MADE",

                "to":
                    row[1]
            })


        # Vendor → Resource → Commitment
        supply = graph.query("""
            MATCH
                (v:Vendor {
                    id:$vendor_id
                })
                -[:CAN_SUPPLY]->
                (r:Resource)
                <-[:REQUIRES]-
                (c:Commitment {
                    id:$commitment_id
                })

            RETURN
                v.name,
                r.name,
                c.name
        """, {
            "vendor_id":
                entity_id,

            "commitment_id":
                commitment_id
        })


        if supply.result_set:

            row = supply.result_set[0]

            evidence.append({
                "from":
                    row[0],

                "relationship":
                    "CAN_SUPPLY",

                "to":
                    row[1]
            })

            evidence.append({
                "from":
                    row[1],

                "relationship":
                    "REQUIRED_BY",

                "to":
                    row[2]
            })


    # ==============================================
    # RESOURCE INPUT
    # ==============================================

    elif entity_type == "Resource":

        resource_result = graph.query("""
            MATCH
                (c:Commitment {
                    id:$commitment_id
                })
                -[:REQUIRES]->
                (r:Resource {
                    id:$resource_id
                })

            RETURN
                r.name,
                c.name
        """, {
            "resource_id":
                entity_id,

            "commitment_id":
                commitment_id
        })


        if resource_result.result_set:

            row = (
                resource_result
                .result_set[0]
            )

            evidence.append({
                "from":
                    row[0],

                "relationship":
                    "REQUIRED_BY",

                "to":
                    row[1]
            })


    # ==============================================
    # DOWNSTREAM DEPENDENCY EDGES
    # ==============================================

    dependency_result = graph.query("""
        MATCH
            (a:Commitment)
            -[:DEPENDS_ON]->
            (b:Commitment)

        MATCH p =
            (a)
            -[:DEPENDS_ON*1..6]->
            (
                failed:Commitment {
                    id:$id
                }
            )

        RETURN DISTINCT
            a.name,
            b.name,
            length(p)

        ORDER BY length(p)
    """, {
        "id":
            commitment_id
    })


    for row in dependency_result.result_set:

        evidence.append({
            "from":
                row[0],

            "relationship":
                "DEPENDS_ON",

            "to":
                row[1]
        })


    return evidence


# ==================================================
# PROMISEGRAPH AGENT
# ==================================================

def run_agent(message):

    tool_trace = []


    # ----------------------------------------------
    # 1. Resolve event to graph entity
    # ----------------------------------------------

    entity, confidence = resolve_entity(
        message
    )


    tool_trace.append({
        "tool":
            "resolve_graph_entity",

        "result":
            entity["id"]
            if entity
            else None
    })


    if (
        entity is None
        or confidence < 0.28
    ):

        return {
            "status":
                "needs_clarification",

            "message":
                "I could not confidently connect "
                "this disruption to a known vendor, "
                "resource or commitment.",

            "tool_trace":
                tool_trace
        }


    # ----------------------------------------------
    # 2. Change entity state
    # ----------------------------------------------

    mark_entity_disrupted(
        entity
    )


    tool_trace.append({
        "tool":
            "mark_entity_disrupted",

        "entity_id":
            entity["id"],

        "entity_type":
            entity["type"]
    })


    # ----------------------------------------------
    # 3. Follow graph to commitments
    # ----------------------------------------------

    related_commitments = (
        find_related_commitments(
            entity
        )
    )


    tool_trace.append({
        "tool":
            "find_related_commitments",

        "count":
            len(related_commitments)
    })


    if not related_commitments:

        return {
            "status":
                "no_connected_commitment",

            "detected_entity":
                entity,

            "message":
                f"{entity['name']} was identified, "
                "but PromiseGraph could not find "
                "a connected commitment requiring "
                "recovery.",

            "tool_trace":
                tool_trace
        }


    # Demo graph has one primary affected commitment.
    commitment = related_commitments[0]

    commitment_id = (
        commitment["id"]
    )


    # ----------------------------------------------
    # 4. Mark connected commitment failed
    # ----------------------------------------------

    mark_commitment_failed(
        commitment_id
    )


    tool_trace.append({
        "tool":
            "mark_connected_commitment_failed",

        "commitment_id":
            commitment_id
    })


    # ----------------------------------------------
    # 5. Multi-hop impact traversal
    # ----------------------------------------------

    affected = trace_impact(
        commitment_id
    )


    tool_trace.append({
        "tool":
            "trace_impact",

        "affected_count":
            len(affected)
    })


    # ----------------------------------------------
    # 6. Build explainable evidence path
    # ----------------------------------------------

    evidence_path = (
        get_evidence_path(
            entity,
            commitment_id
        )
    )


    tool_trace.append({
        "tool":
            "get_evidence_path",

        "evidence_steps":
            len(evidence_path)
    })


    # ----------------------------------------------
    # 7. Generate + evaluate recoveries
    # ----------------------------------------------

    recovery = analyze_recovery(
        commitment_id
    )


    tool_trace.append({
        "tool":
            "analyze_recovery",

        "generated_options":
            recovery[
                "generated_option_count"
            ]
    })


    recommended = recovery[
        "recommended_recovery"
    ]


    # ----------------------------------------------
    # Explanation
    # ----------------------------------------------

    if recommended:

        explanation = (
            f"{entity['name']} was identified as "
            f"the disrupted "
            f"{entity['type'].lower()}. "

            f"The graph connected it to "
            f"{commitment['name']}. "

            f"{len(affected)} downstream "
            f"commitment(s) are affected. "

            f"PromiseGraph evaluated "
            f"{recovery['generated_option_count']} "
            f"recovery option(s) and recommends "
            f"{recommended['name']} at a cost of "
            f"₹{recommended['cost']} with "
            f"{recommended['delay_hours']} hours "
            f"of delay."
        )

    else:

        explanation = (
            f"{entity['name']} was identified as "
            f"the disrupted "
            f"{entity['type'].lower()}. "

            f"The graph connected it to "
            f"{commitment['name']}. "

            f"{len(affected)} downstream "
            f"commitment(s) are affected. "

            f"PromiseGraph evaluated "
            f"{recovery['generated_option_count']} "
            f"recovery option(s), but none satisfy "
            f"the current graph constraints."
        )


    return {
        "status":
            "analyzed",

        "input":
            message,

        "detected_entity":
            entity,

        "matched_commitment":
            commitment,

        "match_confidence":
            round(
                confidence,
                3
            ),

        "affected_commitments":
            affected,

        # NEW: graph reasoning evidence
        "evidence_path":
            evidence_path,

        "generated_options":
            recovery["options"],

        "recommended_recovery":
            recommended,

        "explanation":
            explanation,

        "tool_trace":
            tool_trace
    }


# ==================================================
# APPLY AGENT DECISION
# ==================================================

def apply_recommended_recovery(
    commitment_id
):

    recovery = analyze_recovery(
        commitment_id
    )

    recommended = recovery.get(
        "recommended_recovery"
    )


    if not recommended:

        return {
            "status":
                "no_feasible_recovery",

            "commitment_id":
                commitment_id
        }


    decision_id = (
        f"DECISION-{commitment_id}"
    )


    # Remove older decision for same commitment
    graph.query("""
        MATCH (
            d:RecoveryDecision {id:$id}
        )

        DETACH DELETE d
    """, {
        "id":
            decision_id
    })


    # Store selected recovery in FalkorDB
    graph.query("""
        MATCH (
            c:Commitment {
                id:$commitment_id
            }
        )

        CREATE (
            d:RecoveryDecision {
                id:$decision_id,
                name:$name,
                status:'applied',
                cost:$cost,
                delay_hours:$delay
            }
        )

        CREATE
            (d)-[:RECOVERS]->(c)

        SET
            c.status = 'recovering'
    """, {
        "commitment_id":
            commitment_id,

        "decision_id":
            decision_id,

        "name":
            recommended["name"],

        "cost":
            recommended["cost"],

        "delay":
            recommended["delay_hours"]
    })


    # Connect supplier-based recovery
    # decisions to their selected vendors.
    suppliers = recommended.get(
        "suppliers",
        []
    )


    for supplier in suppliers:

        supplier_id = (
            supplier.get("id")
            if isinstance(
                supplier,
                dict
            )
            else supplier
        )


        if supplier_id:

            graph.query("""
                MATCH
                    (
                        d:RecoveryDecision {
                            id:$decision_id
                        }
                    ),
                    (
                        v:Vendor {
                            id:$vendor_id
                        }
                    )

                MERGE
                    (d)-[:USES]->(v)
            """, {
                "decision_id":
                    decision_id,

                "vendor_id":
                    supplier_id
            })


    # Store the applied recovery as persistent episodic
    # memory for Track 2. If memory storage fails, the
    # Track 1 recovery action remains applied and usable.
    incident_memory = None
    memory_recorded = False
    memory_error = None

    try:

        incident_memory = record_incident_memory(
            commitment_id,
            recommended
        )

        memory_recorded = True

    except Exception as error:

        memory_error = type(error).__name__

        print(
            "[PromiseGraph] Incident memory write failed:",
            error
        )


    return {
        "status":
            "applied",

        "commitment_id":
            commitment_id,

        "decision_id":
            decision_id,

        "recovery":
            recommended,

        "memory_recorded":
            memory_recorded,

        "incident_memory":
            incident_memory,

        "memory_error":
            memory_error
    }


# ==================================================
# LOCAL TEST
# ==================================================

if __name__ == "__main__":

    result = run_agent(
        "Supplier B is unavailable"
    )


    print(
        "\n=== PROMISEGRAPH AGENT ==="
    )


    print(
        "\nDetected entity:",
        result.get(
            "detected_entity"
        )
    )


    print(
        "\nMatched commitment:",
        result.get(
            "matched_commitment"
        )
    )


    print(
        "\nEvidence path:"
    )

    for step in result.get(
        "evidence_path",
        []
    ):

        print(
            f"{step['from']} "
            f"--[{step['relationship']}]--> "
            f"{step['to']}"
        )


    recommendation = result.get(
        "recommended_recovery"
    )


    print(
        "\nRecommended:",
        recommendation["name"]
        if recommendation
        else "NONE"
    )


    print(
        "\nExplanation:"
    )

    print(
        result.get(
            "explanation"
        )
    )


    print(
        "\nTool trace:"
    )

    for step in result.get(
        "tool_trace",
        []
    ):
        print(step)