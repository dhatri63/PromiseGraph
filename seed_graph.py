from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=6379)
graph = db.select_graph("PromiseGraph")

graph.query("""
MATCH (n)
DETACH DELETE n
""")

graph.query("""
CREATE

    /* ---------- Vendors ---------- */

    (v1:Vendor {
        id:'V1',
        name:'Supplier A',
        status:'disrupted'
    }),

    (v2:Vendor {
        id:'V2',
        name:'Supplier B',
        status:'available'
    }),

    (v3:Vendor {
        id:'V3',
        name:'Supplier C',
        status:'available'
    }),

    (v4:Vendor {
        id:'V4',
        name:'Supplier D',
        status:'available'
    }),


    /* ---------- Resource ---------- */

    (res:Resource {
        id:'RES1',
        name:'Component X',
        required_quantity:100
    }),


    /* ---------- Global Constraint ---------- */

    (budget:Constraint {
        id:'B1',
        name:'Emergency recovery budget',
        limit:20000
    }),


    /* ---------- Commitment Types ---------- */

    (t1:CommitmentType {
        id:'T1',
        name:'supply'
    }),

    (t2:CommitmentType {
        id:'T2',
        name:'production'
    }),

    (t3:CommitmentType {
        id:'T3',
        name:'launch'
    }),

    (t4:CommitmentType {
        id:'T4',
        name:'customer_delivery'
    }),


    /* ---------- Commitments ---------- */

    (c1:Commitment {
        id:'C1',
        name:'Deliver 100 components by Friday',
        kind:'supply',
        status:'active',
        priority:'critical',
        penalty:0,
        warning_after_hours:12,
        max_delay_hours:24
    }),

    (c2:Commitment {
        id:'C2',
        name:'Complete production by Saturday',
        kind:'production',
        status:'active',
        priority:'critical',
        penalty:50000,
        warning_after_hours:12,
        max_delay_hours:30
    }),

    (c3:Commitment {
        id:'C3',
        name:'Launch product Monday',
        kind:'launch',
        status:'active',
        priority:'high',
        penalty:15000,
        warning_after_hours:1,
        max_delay_hours:30
    }),

    (c4:Commitment {
        id:'C4',
        name:'Deliver Customer A order Tuesday',
        kind:'customer_delivery',
        status:'active',
        priority:'critical',
        penalty:100000,
        warning_after_hours:8,
        max_delay_hours:12
    }),


    /* ---------- Recovery Capabilities ---------- */

    (a1:RecoveryCapability {
        id:'A1',
        name:'Run overtime production shift',
        cost:8000,
        delay_hours:8
    }),

    (a2:RecoveryCapability {
        id:'A2',
        name:'Outsource production to partner plant',
        cost:18000,
        delay_hours:4
    }),

    (a3:RecoveryCapability {
        id:'A3',
        name:'Use premium emergency production line',
        cost:24000,
        delay_hours:2
    }),

    (a4:RecoveryCapability {
        id:'A4',
        name:'Switch to phased product launch',
        cost:6000,
        delay_hours:4
    }),

    (a5:RecoveryCapability {
        id:'A5',
        name:'Reschedule full product launch',
        cost:2000,
        delay_hours:18
    }),

    (a6:RecoveryCapability {
        id:'A6',
        name:'Use express delivery partner',
        cost:7000,
        delay_hours:2
    }),

    (a7:RecoveryCapability {
        id:'A7',
        name:'Split order across last-mile partners',
        cost:4500,
        delay_hours:5
    }),


    /* ---------- Company Graph ---------- */

    (v1)-[:MADE]->(c1),

    (c1)-[:REQUIRES]->(res),

    (v2)-[:CAN_SUPPLY {
        capacity:100,
        emergency_cost:30000,
        lead_time_hours:0
    }]->(res),

    (v3)-[:CAN_SUPPLY {
        capacity:60,
        emergency_cost:6000,
        lead_time_hours:6
    }]->(res),

    (v4)-[:CAN_SUPPLY {
        capacity:50,
        emergency_cost:4000,
        lead_time_hours:4
    }]->(res),


    (c1)-[:OF_KIND]->(t1),
    (c2)-[:OF_KIND]->(t2),
    (c3)-[:OF_KIND]->(t3),
    (c4)-[:OF_KIND]->(t4),


    (c2)-[:DEPENDS_ON]->(c1),
    (c3)-[:DEPENDS_ON]->(c2),
    (c4)-[:DEPENDS_ON]->(c3),


    /* Capabilities are company data */

    (a1)-[:APPLIES_TO]->(t2),
    (a2)-[:APPLIES_TO]->(t2),
    (a3)-[:APPLIES_TO]->(t2),

    (a4)-[:APPLIES_TO]->(t3),
    (a5)-[:APPLIES_TO]->(t3),

    (a6)-[:APPLIES_TO]->(t4),
    (a7)-[:APPLIES_TO]->(t4)
""")

print("PromiseGraph multi-type recovery scenario seeded successfully!")