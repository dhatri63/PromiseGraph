// PromiseGraph — Representative FalkorDB / Cypher Queries
// Graph Hacks — Track 1 + Track 2
//
// These are judge-readable versions of the graph operations used by PromiseGraph.
// Example parameters:
//   $commitment_id = "C2"
//   $resource_id   = "RES1"


// ================================================================
// 1) TRACK 1 — MULTI-HOP DOWNSTREAM IMPACT
// ================================================================
// Find commitments that depend (directly or indirectly) on a failed commitment.

MATCH p =
    (affected:Commitment)
    -[:DEPENDS_ON*1..6]->
    (failed:Commitment {id: $commitment_id})
RETURN
    affected.id AS commitment_id,
    affected.name AS commitment_name,
    affected.priority AS priority,
    length(p) AS dependency_distance
ORDER BY dependency_distance;


// ================================================================
// 2) TRACK 1 — SUPPLIER / RESOURCE RECOVERY CONTEXT
// ================================================================
// Read supplier capacity, emergency cost and lead time from graph edges.

MATCH
    (v:Vendor)
    -[s:CAN_SUPPLY]->
    (r:Resource {id: $resource_id})
RETURN
    v.id AS vendor_id,
    v.name AS vendor_name,
    v.status AS vendor_status,
    r.name AS resource_name,
    r.required_quantity AS required_quantity,
    s.capacity AS capacity,
    s.emergency_cost AS emergency_cost,
    s.lead_time_hours AS lead_time_hours
ORDER BY v.id;


// ================================================================
// 3) TRACK 1 — RECOVERY CAPABILITIES FOR A COMMITMENT
// ================================================================
// Resolve a commitment's type, then find graph-linked capabilities.

MATCH
    (c:Commitment {id: $commitment_id})
    -[:OF_KIND]->
    (t:CommitmentType)
MATCH
    (a:RecoveryCapability)
    -[:APPLIES_TO]->
    (t)
RETURN
    c.id AS commitment_id,
    c.name AS commitment_name,
    t.id AS type_id,
    t.name AS commitment_type,
    a.id AS capability_id,
    a.name AS capability_name,
    a.cost AS cost,
    a.delay_hours AS delay_hours
ORDER BY a.cost, a.delay_hours;


// ================================================================
// 4) TRACK 1 — LIVE RECOVERY BUDGET
// ================================================================
// Retrieve the current budget constraint used during feasibility checks.

MATCH (b:Constraint)
WHERE b.limit IS NOT NULL
RETURN
    b.id AS constraint_id,
    b.name AS constraint_name,
    b.limit AS recovery_budget
ORDER BY b.id
LIMIT 1;


// ================================================================
// 5) TRACK 1 — EXPLAINABLE DEPENDENCY PATH
// ================================================================
// Return the path that explains why a commitment is affected.

MATCH p =
    (affected:Commitment)
    -[:DEPENDS_ON*1..6]->
    (failed:Commitment {id: $commitment_id})
RETURN
    [n IN nodes(p) | {id: n.id, name: n.name}] AS path_nodes,
    [r IN relationships(p) | type(r)] AS path_relationships,
    length(p) AS hops
ORDER BY hops;


// ================================================================
// 6) TRACK 1 — WRITE AN APPLIED RECOVERY DECISION
// ================================================================
// The selected feasible recovery becomes part of the operational graph.

MATCH (c:Commitment {id: $commitment_id})
CREATE (d:RecoveryDecision {
    id: $decision_id,
    name: $name,
    status: 'applied',
    cost: $cost,
    delay_hours: $delay
})
CREATE (d)-[:RECOVERS]->(c)
SET c.status = 'recovering'
RETURN
    d.id AS decision_id,
    d.name AS recovery_name,
    d.status AS status;


// ================================================================
// 7) TRACK 2 — RETRIEVE PERSISTENT EPISODIC MEMORY
// ================================================================
// Retrieve prior applied recoveries for the same commitment.
// Historical context is remembered, but current feasibility is re-checked.

MATCH
    (m:IncidentMemory)
    -[:AFFECTED]->
    (c:Commitment {id: $commitment_id})
RETURN
    m.id AS memory_id,
    m.created_at AS created_at,
    m.commitment_name AS commitment_name,
    m.recovery_id AS recovery_id,
    m.recovery_name AS recovery_name,
    m.recovery_cost AS recovery_cost,
    m.recovery_delay_hours AS recovery_delay_hours,
    m.outcome AS outcome
ORDER BY m.created_at DESC;


// ================================================================
// 8) TRACK 2 — INSPECT MEMORY CONNECTIONS
// ================================================================
// Memory is graph-native: it connects back to the affected commitment
// and, when applicable, the capability/vendor used by the recovery.

MATCH (m:IncidentMemory)-[r]-(n)
RETURN
    m.id AS memory_id,
    type(r) AS relationship,
    labels(n) AS connected_labels,
    n.id AS connected_id,
    n.name AS connected_name
ORDER BY m.created_at DESC;


// ================================================================
// 9) DEMO — VIEW THE CONNECTED OPERATIONAL GRAPH
// ================================================================

MATCH (a)-[r]->(b)
RETURN a, r, b;


// ================================================================
// 10) DEMO — VIEW INCIDENT MEMORY TABLE
// ================================================================

MATCH (m:IncidentMemory)
RETURN
    m.id,
    m.created_at,
    m.commitment_name,
    m.recovery_name,
    m.recovery_cost,
    m.recovery_delay_hours,
    m.outcome
ORDER BY m.created_at DESC;