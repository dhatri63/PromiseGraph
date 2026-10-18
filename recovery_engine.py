from itertools import combinations
from falkordb import FalkorDB


db = FalkorDB(host="localhost", port=6379)
graph = db.select_graph("PromiseGraph")


def timing_analysis(delay_hours, targets):

    hard_problems = []
    soft_impacts = []
    results = []

    for commitment in targets:

        warning = commitment["warning_after_hours"]
        maximum = commitment["max_delay_hours"]

        if maximum is not None and delay_hours > maximum:

            results.append({
                "commitment_id": commitment["id"],
                "commitment": commitment["name"],
                "status": "BREAKS",
                "delay_hours": delay_hours,
                "max_delay_hours": maximum
            })

            hard_problems.append({
                "type": "SLA_BREAK",
                "target": commitment["name"],
                "priority": commitment["priority"],
                "penalty": commitment["penalty"],
                "reason":
                    f"{delay_hours}-hour delay exceeds "
                    f"the {maximum}-hour tolerance for "
                    f"{commitment['name']}."
            })

        elif warning is not None and delay_hours > warning:

            results.append({
                "commitment_id": commitment["id"],
                "commitment": commitment["name"],
                "status": "IMPACTS",
                "delay_hours": delay_hours,
                "warning_after_hours": warning
            })

            soft_impacts.append({
                "type": "TIMING_IMPACT",
                "target": commitment["name"],
                "priority": commitment["priority"],
                "penalty": commitment["penalty"],
                "reason":
                    f"{delay_hours}-hour delay affects "
                    f"{commitment['name']} but remains "
                    f"within its {maximum}-hour limit."
            })

        else:

            results.append({
                "commitment_id": commitment["id"],
                "commitment": commitment["name"],
                "status": "SAFE",
                "delay_hours": delay_hours
            })

    return results, hard_problems, soft_impacts


def analyze_recovery(commitment_id):

    # --------------------------------
    # Failed commitment
    # --------------------------------

    result = graph.query("""
        MATCH (c:Commitment {id:$id})

        RETURN
            c.id,
            c.name,
            c.kind,
            c.priority,
            c.penalty,
            c.warning_after_hours,
            c.max_delay_hours
    """, {"id": commitment_id})

    if not result.result_set:
        return {
            "error": "Commitment not found",
            "commitment_id": commitment_id
        }

    row = result.result_set[0]

    failed = {
        "id": row[0],
        "name": row[1],
        "kind": row[2],
        "priority": row[3],
        "penalty": row[4] or 0,
        "warning_after_hours": row[5],
        "max_delay_hours": row[6]
    }


    # --------------------------------
    # Budget
    # --------------------------------

    result = graph.query("""
        MATCH (b:Constraint)
        WHERE b.limit IS NOT NULL
        RETURN b.name, b.limit
        LIMIT 1
    """)

    budget = None

    if result.result_set:
        row = result.result_set[0]

        budget = {
            "name": row[0],
            "limit": row[1]
        }


    # --------------------------------
    # Downstream graph traversal
    # --------------------------------

    result = graph.query("""
        MATCH (failed:Commitment {id:$id})

        OPTIONAL MATCH p =
            (affected:Commitment)
            -[:DEPENDS_ON*1..6]->
            (failed)

        RETURN
            affected.id,
            affected.name,
            affected.priority,
            affected.penalty,
            affected.warning_after_hours,
            affected.max_delay_hours,
            length(p)

        ORDER BY length(p)
    """, {"id": commitment_id})

    downstream = []

    for row in result.result_set:

        if row[0] is not None:

            downstream.append({
                "id": row[0],
                "name": row[1],
                "priority": row[2],
                "penalty": row[3] or 0,
                "warning_after_hours": row[4],
                "max_delay_hours": row[5],
                "distance": row[6]
            })


    # Include failed commitment itself when
    # checking whether a recovery delay is safe.
    timing_targets = [failed] + downstream

    options = []
    required_resource = None


    # ===================================================
    # SUPPLY RECOVERY
    # Generate supplier combinations automatically
    # ===================================================

    if failed["kind"] == "supply":

        result = graph.query("""
            MATCH
                (:Commitment {id:$id})
                -[:REQUIRES]->
                (res:Resource)

            RETURN
                res.id,
                res.name,
                res.required_quantity
        """, {"id": commitment_id})

        if result.result_set:

            row = result.result_set[0]

            required_resource = {
                "id": row[0],
                "name": row[1],
                "required_quantity": row[2]
            }


            supplier_result = graph.query("""
                MATCH
                    (v:Vendor)
                    -[offer:CAN_SUPPLY]->
                    (:Resource {id:$resource_id})

                WHERE v.status = 'available'

                RETURN
                    v.id,
                    v.name,
                    offer.capacity,
                    offer.emergency_cost,
                    offer.lead_time_hours

                ORDER BY v.id
            """, {
                "resource_id": required_resource["id"]
            })


            suppliers = []

            for row in supplier_result.result_set:

                suppliers.append({
                    "id": row[0],
                    "name": row[1],
                    "capacity": row[2] or 0,
                    "cost": row[3] or 0,
                    "lead_time_hours": row[4] or 0
                })


            for size in range(1, len(suppliers) + 1):

                for combo in combinations(suppliers, size):

                    capacity = sum(
                        s["capacity"] for s in combo
                    )

                    cost = sum(
                        s["cost"] for s in combo
                    )

                    delay = max(
                        s["lead_time_hours"] for s in combo
                    )

                    names = [s["name"] for s in combo]
                    ids = [s["id"] for s in combo]

                    if len(combo) == 1:
                        name = f"Switch to {names[0]}"
                    else:
                        name = "Split across " + " + ".join(names)

                    option_id = "AUTO-" + "-".join(ids)

                    hard = []
                    soft = []
                    checks = []


                    capacity_ok = (
                        capacity >=
                        required_resource["required_quantity"]
                    )

                    checks.append({
                        "check": "supplier_capacity",
                        "passed": capacity_ok,
                        "required_quantity":
                            required_resource["required_quantity"],
                        "available_capacity": capacity
                    })

                    if not capacity_ok:

                        hard.append({
                            "type": "INSUFFICIENT_CAPACITY",
                            "target": required_resource["name"],
                            "reason":
                                f"{name} provides {capacity} units, "
                                f"but "
                                f"{required_resource['required_quantity']} "
                                f"units are required."
                        })


                    if budget:

                        budget_ok = cost <= budget["limit"]

                        checks.append({
                            "check": "budget",
                            "passed": budget_ok,
                            "repair_cost": cost,
                            "budget_limit": budget["limit"]
                        })

                        if not budget_ok:

                            hard.append({
                                "type": "BUDGET_VIOLATION",
                                "target": budget["name"],
                                "reason":
                                    f"{name} costs ₹{cost}, "
                                    f"but the recovery budget is "
                                    f"₹{budget['limit']}."
                            })


                    timing, timing_hard, timing_soft = (
                        timing_analysis(
                            delay,
                            timing_targets
                        )
                    )

                    hard.extend(timing_hard)
                    soft.extend(timing_soft)

                    checks.append({
                        "check": "delay_propagation",
                        "results": timing
                    })


                    options.append({
                        "id": option_id,
                        "name": name,
                        "generated": True,
                        "source": "supplier_graph",
                        "suppliers": list(combo),
                        "cost": cost,
                        "capacity": capacity,
                        "delay_hours": delay,
                        "checks": checks,
                        "hard_problems": hard,
                        "soft_impacts": soft
                    })


    # ===================================================
    # OTHER COMMITMENT TYPES
    # Read matching recovery capabilities from FalkorDB
    # ===================================================

    else:

        capability_result = graph.query("""
            MATCH
                (c:Commitment {id:$id})
                -[:OF_KIND]->
                (t:CommitmentType)

            MATCH
                (a:RecoveryCapability)
                -[:APPLIES_TO]->
                (t)

            RETURN
                a.id,
                a.name,
                a.cost,
                a.delay_hours

            ORDER BY a.id
        """, {"id": commitment_id})


        for row in capability_result.result_set:

            capability_id = row[0]
            name = row[1]
            cost = row[2] or 0
            delay = row[3] or 0

            hard = []
            soft = []
            checks = []


            if budget:

                budget_ok = cost <= budget["limit"]

                checks.append({
                    "check": "budget",
                    "passed": budget_ok,
                    "repair_cost": cost,
                    "budget_limit": budget["limit"]
                })

                if not budget_ok:

                    hard.append({
                        "type": "BUDGET_VIOLATION",
                        "target": budget["name"],
                        "reason":
                            f"{name} costs ₹{cost}, "
                            f"but the recovery budget is "
                            f"₹{budget['limit']}."
                    })


            timing, timing_hard, timing_soft = (
                timing_analysis(
                    delay,
                    timing_targets
                )
            )

            hard.extend(timing_hard)
            soft.extend(timing_soft)

            checks.append({
                "check": "delay_propagation",
                "results": timing
            })


            options.append({
                "id": f"AUTO-{capability_id}",
                "name": name,
                "generated": True,
                "source": "recovery_capability_graph",
                "suppliers": [],
                "cost": cost,
                "capacity": None,
                "delay_hours": delay,
                "checks": checks,
                "hard_problems": hard,
                "soft_impacts": soft
            })


    # --------------------------------
    # Rank feasible recoveries
    # --------------------------------

    feasible = [
        option
        for option in options
        if not option["hard_problems"]
    ]

    feasible.sort(
        key=lambda option: (
            len(option["soft_impacts"]),
            option["cost"],
            option["delay_hours"]
        )
    )

    best = feasible[0] if feasible else None


    return {
        "commitment_id": commitment_id,
        "failed_commitment": failed,
        "required_resource": required_resource,
        "budget": budget,
        "downstream_commitments": downstream,
        "generated_option_count": len(options),
        "options": options,
        "recommended_recovery": best
    }


if __name__ == "__main__":

    for commitment_id in ["C1", "C2", "C3", "C4"]:

        result = analyze_recovery(commitment_id)

        print(
            f"\n\n===== {commitment_id} ====="
        )

        print(
            "Type:",
            result["failed_commitment"]["kind"]
        )

        print(
            "Generated:",
            result["generated_option_count"]
        )

        for option in result["options"]:

            status = (
                "REJECTED"
                if option["hard_problems"]
                else "FEASIBLE"
            )

            print(
                f"{option['name']} -> {status}"
            )

        best = result["recommended_recovery"]

        print(
            "RECOMMENDED:",
            best["name"] if best else "NONE"
        )