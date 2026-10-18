import uuid
from datetime import datetime, timezone

from falkordb import FalkorDB


db = FalkorDB(
    host="localhost",
    port=6379
)

graph = db.select_graph(
    "PromiseGraph"
)


def record_incident_memory(
    commitment_id,
    recovery
):
    if not commitment_id:
        raise ValueError("commitment_id is required.")

    if not isinstance(recovery, dict):
        raise ValueError("recovery must be a dictionary.")

    commitment_result = graph.query("""
        MATCH (
            c:Commitment {
                id:$commitment_id
            }
        )
        RETURN
            c.id,
            c.name,
            c.kind,
            c.priority
    """, {
        "commitment_id": commitment_id
    })

    if not commitment_result.result_set:
        raise ValueError(
            f"Commitment {commitment_id} was not found."
        )

    row = commitment_result.result_set[0]

    commitment = {
        "id": row[0],
        "name": row[1],
        "kind": row[2],
        "priority": row[3]
    }

    memory_id = "MEM-" + uuid.uuid4().hex[:12]

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    graph.query("""
        MATCH (
            c:Commitment {
                id:$commitment_id
            }
        )

        CREATE (
            m:IncidentMemory {
                id:$memory_id,
                created_at:$created_at,
                incident_type:'commitment_disruption',
                commitment_id:$commitment_id,
                commitment_name:$commitment_name,
                commitment_kind:$commitment_kind,
                commitment_priority:$commitment_priority,
                recovery_id:$recovery_id,
                recovery_name:$recovery_name,
                recovery_cost:$recovery_cost,
                recovery_delay_hours:$recovery_delay_hours,
                outcome:'applied'
            }
        )

        CREATE
            (m)-[:AFFECTED]->(c)
    """, {
        "memory_id": memory_id,
        "created_at": created_at,
        "commitment_id": commitment["id"],
        "commitment_name": commitment["name"],
        "commitment_kind": commitment["kind"],
        "commitment_priority": commitment["priority"],
        "recovery_id": recovery.get("id"),
        "recovery_name": recovery.get("name"),
        "recovery_cost": recovery.get("cost"),
        "recovery_delay_hours": recovery.get("delay_hours")
    })

    recovery_id = recovery.get("id") or ""

    if recovery_id.startswith("AUTO-A"):
        capability_id = recovery_id.replace(
            "AUTO-",
            "",
            1
        )

        graph.query("""
            MATCH
                (
                    m:IncidentMemory {
                        id:$memory_id
                    }
                ),
                (
                    a:RecoveryCapability {
                        id:$capability_id
                    }
                )

            MERGE
                (m)-[:USED_CAPABILITY]->(a)
        """, {
            "memory_id": memory_id,
            "capability_id": capability_id
        })

    suppliers = recovery.get(
        "suppliers",
        []
    )

    for supplier in suppliers:
        supplier_id = (
            supplier.get("id")
            if isinstance(supplier, dict)
            else supplier
        )

        if not supplier_id:
            continue

        graph.query("""
            MATCH
                (
                    m:IncidentMemory {
                        id:$memory_id
                    }
                ),
                (
                    v:Vendor {
                        id:$vendor_id
                    }
                )

            MERGE
                (m)-[:USED_VENDOR]->(v)
        """, {
            "memory_id": memory_id,
            "vendor_id": supplier_id
        })

    return {
        "id": memory_id,
        "created_at": created_at,
        "commitment": commitment,
        "recovery": {
            "id": recovery.get("id"),
            "name": recovery.get("name"),
            "cost": recovery.get("cost"),
            "delay_hours": recovery.get("delay_hours")
        }
    }


def retrieve_prior_incidents(
    commitment_id,
    limit=3
):
    safe_limit = max(
        1,
        min(
            int(limit),
            10
        )
    )

    result = graph.query(f"""
        MATCH
            (
                m:IncidentMemory
            )
            -[:AFFECTED]->
            (
                c:Commitment {{
                    id:$commitment_id
                }}
            )

        RETURN
            m.id,
            m.created_at,
            m.commitment_name,
            m.commitment_kind,
            m.recovery_id,
            m.recovery_name,
            m.recovery_cost,
            m.recovery_delay_hours,
            m.outcome

        ORDER BY
            m.created_at DESC

        LIMIT {safe_limit}
    """, {
        "commitment_id": commitment_id
    })

    memories = []

    for row in result.result_set:
        memories.append({
            "id": row[0],
            "created_at": row[1],
            "commitment_name": row[2],
            "commitment_kind": row[3],
            "recovery_id": row[4],
            "recovery_name": row[5],
            "recovery_cost": row[6],
            "recovery_delay_hours": row[7],
            "outcome": row[8]
        })

    return memories


def count_incident_memories():
    result = graph.query("""
        MATCH (
            m:IncidentMemory
        )
        RETURN count(m)
    """)

    if not result.result_set:
        return 0

    return result.result_set[0][0]