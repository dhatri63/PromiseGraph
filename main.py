from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer
)

from urllib.parse import urlparse

from falkordb import FalkorDB

from recovery_engine import (
    analyze_recovery as dynamic_recovery
)

from agent import (
    run_agent,
    apply_recommended_recovery
)

from llm_agent import (
    run_agent_with_fallback
)

import json


# ==================================================
# DATABASE
# ==================================================

db = FalkorDB(
    host="localhost",
    port=6379
)

graph = db.select_graph(
    "PromiseGraph"
)


# ==================================================
# COMMITMENTS
# ==================================================

def get_commitments():

    result = graph.query("""
        MATCH (c:Commitment)

        RETURN
            c.id,
            c.name,
            c.status,
            c.priority

        ORDER BY c.id
    """)

    return [
        {
            "id":
                row[0],

            "name":
                row[1],

            "status":
                row[2],

            "priority":
                row[3]
        }
        for row in result.result_set
    ]


# ==================================================
# COMPANY CONTEXT
# ==================================================

def get_company_context():

    # ----------------------------------------------
    # Resource / required quantity
    # ----------------------------------------------

    resource_result = graph.query("""
        MATCH (r:Resource)

        WHERE r.id IS NOT NULL

        RETURN
            r.id,
            r.name,
            r.required_quantity,
            r.status

        ORDER BY r.id

        LIMIT 1
    """)


    resource = None


    if resource_result.result_set:

        row = (
            resource_result
            .result_set[0]
        )

        resource = {
            "id":
                row[0],

            "name":
                row[1],

            "required_quantity":
                row[2],

            "status":
                row[3]
        }


    # ----------------------------------------------
    # Recovery budget
    # ----------------------------------------------

    budget_result = graph.query("""
        MATCH (b:Constraint)

        WHERE
            b.limit IS NOT NULL

        RETURN
            b.id,
            b.name,
            b.limit

        ORDER BY b.id

        LIMIT 1
    """)


    budget = None


    if budget_result.result_set:

        row = (
            budget_result
            .result_set[0]
        )

        budget = {
            "id":
                row[0],

            "name":
                row[1],

            "limit":
                row[2]
        }


    # ----------------------------------------------
    # Suppliers for current resource
    # ----------------------------------------------

    suppliers = []


    if resource:

        supplier_result = graph.query("""
            MATCH
                (v:Vendor)
                -[s:CAN_SUPPLY]->
                (r:Resource {id:$resource_id})

            RETURN
                v.id,
                v.name,
                v.status,
                s.capacity,
                s.emergency_cost,
                s.lead_time_hours

            ORDER BY v.id
        """, {
            "resource_id":
                resource["id"]
        })


        for row in supplier_result.result_set:

            suppliers.append({
                "id":
                    row[0],

                "name":
                    row[1],

                "status":
                    row[2],

                "capacity":
                    row[3],

                "emergency_cost":
                    row[4],

                "lead_time_hours":
                    row[5]
            })


    # ----------------------------------------------
    # Recovery capabilities for non-supply work
    # ----------------------------------------------

    capability_result = graph.query("""
        MATCH
            (a:RecoveryCapability)
            -[:APPLIES_TO]->
            (t:CommitmentType)

        RETURN
            a.id,
            a.name,
            a.cost,
            a.delay_hours,
            t.id,
            t.name

        ORDER BY
            t.id,
            a.id
    """)


    recovery_capabilities = []


    for row in capability_result.result_set:

        recovery_capabilities.append({
            "id":
                row[0],

            "name":
                row[1],

            "cost":
                row[2],

            "delay_hours":
                row[3],

            "type_id":
                row[4],

            "type_name":
                row[5]
        })


    return {
        "resource":
            resource,

        "budget":
            budget,

        "suppliers":
            suppliers,

        "recovery_capabilities":
            recovery_capabilities
    }

# ==================================================
# NUMBER VALIDATION
# ==================================================

def parse_non_negative_number(
    value,
    field_name
):

    # Do not accept True / False as numbers.
    if isinstance(
        value,
        bool
    ):

        raise ValueError(
            f"{field_name} must be a number."
        )


    try:

        number = float(
            value
        )

    except (
        TypeError,
        ValueError
    ):

        raise ValueError(
            f"{field_name} must be a valid number."
        )


    if number < 0:

        raise ValueError(
            f"{field_name} cannot be negative."
        )


    # Store whole-number values as integers
    # so FalkorDB data stays clean.
    if number.is_integer():

        return int(
            number
        )


    return number


# ==================================================
# UPDATE COMPANY CONTEXT
# ==================================================

def update_company_context(
    payload
):

    if not isinstance(
        payload,
        dict
    ):

        raise ValueError(
            "Context payload must be a JSON object."
        )


    current_context = (
        get_company_context()
    )


    resource = (
        current_context
        .get("resource")
    )


    budget = (
        current_context
        .get("budget")
    )


    updated_fields = []


    # ==============================================
    # REQUIRED QUANTITY
    # ==============================================

    if (
        "required_quantity"
        in payload
    ):

        if not resource:

            raise ValueError(
                "No resource exists in the graph."
            )


        required_quantity = (
            parse_non_negative_number(
                payload[
                    "required_quantity"
                ],
                "Required quantity"
            )
        )


        if required_quantity == 0:

            raise ValueError(
                "Required quantity must be greater than zero."
            )


        graph.query("""
            MATCH (
                r:Resource {
                    id:$resource_id
                }
            )

            SET
                r.required_quantity =
                    $required_quantity
        """, {
            "resource_id":
                resource["id"],

            "required_quantity":
                required_quantity
        })


        updated_fields.append(
            "required_quantity"
        )


    # ==============================================
    # RECOVERY BUDGET
    # ==============================================

    if (
        "recovery_budget"
        in payload
    ):

        if not budget:

            raise ValueError(
                "No recovery budget constraint exists in the graph."
            )


        recovery_budget = (
            parse_non_negative_number(
                payload[
                    "recovery_budget"
                ],
                "Recovery budget"
            )
        )


        graph.query("""
            MATCH (
                b:Constraint {
                    id:$budget_id
                }
            )

            SET
                b.limit =
                    $budget
        """, {
            "budget_id":
                budget["id"],

            "budget":
                recovery_budget
        })


        updated_fields.append(
            "recovery_budget"
        )


    # ==============================================
    # SUPPLIER FACTS
    # ==============================================

    if (
        "suppliers"
        in payload
    ):

        suppliers = (
            payload[
                "suppliers"
            ]
        )


        if not isinstance(
            suppliers,
            list
        ):

            raise ValueError(
                "Suppliers must be a list."
            )


        if not resource:

            raise ValueError(
                "No resource exists in the graph."
            )


        for supplier in suppliers:

            if not isinstance(
                supplier,
                dict
            ):

                raise ValueError(
                    "Each supplier must be an object."
                )


            supplier_id = (
                supplier
                .get(
                    "id"
                )
            )


            if not supplier_id:

                raise ValueError(
                    "Every supplier update requires an id."
                )


            # --------------------------------------
            # Confirm supplier relationship exists
            # --------------------------------------

            existing = graph.query("""
                MATCH
                    (
                        v:Vendor {
                            id:$vendor_id
                        }
                    )
                    -[s:CAN_SUPPLY]->
                    (
                        r:Resource {
                            id:$resource_id
                        }
                    )

                RETURN
                    v.id
            """, {
                "vendor_id":
                    supplier_id,

                "resource_id":
                    resource["id"]
            })


            if not existing.result_set:

                raise ValueError(
                    f"Supplier {supplier_id} is not connected "
                    f"to {resource['name']}."
                )


            # --------------------------------------
            # Capacity
            # --------------------------------------

            if (
                "capacity"
                in supplier
            ):

                capacity = (
                    parse_non_negative_number(
                        supplier[
                            "capacity"
                        ],
                        f"{supplier_id} capacity"
                    )
                )


                graph.query("""
                    MATCH
                        (
                            v:Vendor {
                                id:$vendor_id
                            }
                        )
                        -[s:CAN_SUPPLY]->
                        (
                            r:Resource {
                                id:$resource_id
                            }
                        )

                    SET
                        s.capacity =
                            $capacity
                """, {
                    "vendor_id":
                        supplier_id,

                    "resource_id":
                        resource["id"],

                    "capacity":
                        capacity
                })


                updated_fields.append(
                    f"{supplier_id}.capacity"
                )


            # --------------------------------------
            # Emergency cost
            # --------------------------------------

            if (
                "emergency_cost"
                in supplier
            ):

                emergency_cost = (
                    parse_non_negative_number(
                        supplier[
                            "emergency_cost"
                        ],
                        f"{supplier_id} emergency cost"
                    )
                )


                graph.query("""
                    MATCH
                        (
                            v:Vendor {
                                id:$vendor_id
                            }
                        )
                        -[s:CAN_SUPPLY]->
                        (
                            r:Resource {
                                id:$resource_id
                            }
                        )

                    SET
                        s.emergency_cost =
                            $cost
                """, {
                    "vendor_id":
                        supplier_id,

                    "resource_id":
                        resource["id"],

                    "cost":
                        emergency_cost
                })


                updated_fields.append(
                    f"{supplier_id}.emergency_cost"
                )


            # --------------------------------------
            # Lead time
            # --------------------------------------

            if (
                "lead_time_hours"
                in supplier
            ):

                lead_time = (
                    parse_non_negative_number(
                        supplier[
                            "lead_time_hours"
                        ],
                        f"{supplier_id} lead time"
                    )
                )


                graph.query("""
                    MATCH
                        (
                            v:Vendor {
                                id:$vendor_id
                            }
                        )
                        -[s:CAN_SUPPLY]->
                        (
                            r:Resource {
                                id:$resource_id
                            }
                        )

                    SET
                        s.lead_time_hours =
                            $lead_time
                """, {
                    "vendor_id":
                        supplier_id,

                    "resource_id":
                        resource["id"],

                    "lead_time":
                        lead_time
                })


                updated_fields.append(
                    f"{supplier_id}.lead_time_hours"
                )


    # ==============================================
    # RECOVERY CAPABILITIES
    # ==============================================

    if (
        "recovery_capabilities"
        in payload
    ):

        capabilities = (
            payload[
                "recovery_capabilities"
            ]
        )


        if not isinstance(
            capabilities,
            list
        ):

            raise ValueError(
                "Recovery capabilities must be a list."
            )


        for capability in capabilities:

            if not isinstance(
                capability,
                dict
            ):

                raise ValueError(
                    "Each recovery capability must be an object."
                )


            capability_id = (
                capability
                .get(
                    "id"
                )
            )


            if not capability_id:

                raise ValueError(
                    "Every recovery capability update requires an id."
                )


            existing = graph.query("""
                MATCH
                    (a:RecoveryCapability {
                        id:$capability_id
                    })
                    -[:APPLIES_TO]->
                    (t:CommitmentType)

                RETURN
                    a.id,
                    t.name
            """, {
                "capability_id":
                    capability_id
            })


            if not existing.result_set:

                raise ValueError(
                    f"Recovery capability {capability_id} "
                    "does not exist in the graph."
                )


            # --------------------------------------
            # Capability cost
            # --------------------------------------

            if (
                "cost"
                in capability
            ):

                cost = (
                    parse_non_negative_number(
                        capability[
                            "cost"
                        ],
                        f"{capability_id} cost"
                    )
                )


                graph.query("""
                    MATCH (
                        a:RecoveryCapability {
                            id:$capability_id
                        }
                    )

                    SET
                        a.cost =
                            $cost
                """, {
                    "capability_id":
                        capability_id,

                    "cost":
                        cost
                })


                updated_fields.append(
                    f"{capability_id}.cost"
                )


            # --------------------------------------
            # Capability delay
            # --------------------------------------

            if (
                "delay_hours"
                in capability
            ):

                delay_hours = (
                    parse_non_negative_number(
                        capability[
                            "delay_hours"
                        ],
                        f"{capability_id} delay"
                    )
                )


                graph.query("""
                    MATCH (
                        a:RecoveryCapability {
                            id:$capability_id
                        }
                    )

                    SET
                        a.delay_hours =
                            $delay_hours
                """, {
                    "capability_id":
                        capability_id,

                    "delay_hours":
                        delay_hours
                })


                updated_fields.append(
                    f"{capability_id}.delay_hours"
                )


    # ==============================================
    # RETURN LIVE UPDATED GRAPH CONTEXT
    # ==============================================

    return {
        "status":
            "updated",

        "updated_fields":
            updated_fields,

        "context":
            get_company_context()
    }

# ==================================================
# GRAPH DATA FOR VISUALIZATION
# ==================================================

def get_graph_data():

    node_result = graph.query("""
        MATCH (n)

        WHERE
            n.id IS NOT NULL

        RETURN
            n.id,
            n.name,
            labels(n),
            n.status,
            n.priority,
            n.cost,
            n.delay_hours

        ORDER BY n.id
    """)


    nodes = []


    for row in node_result.result_set:

        labels_list = (
            row[2]
            if row[2]
            else []
        )


        nodes.append({
            "id":
                row[0],

            "name":
                row[1],

            "type":
                labels_list[0]
                if labels_list
                else "Node",

            "status":
                row[3],

            "priority":
                row[4],

            "cost":
                row[5],

            "delay_hours":
                row[6]
        })


    edge_result = graph.query("""
        MATCH
            (a)-[r]->(b)

        WHERE
            a.id IS NOT NULL
            AND
            b.id IS NOT NULL

        RETURN
            a.id,
            b.id,
            type(r),
            r.reason
    """)


    edges = []


    for index, row in enumerate(
        edge_result.result_set
    ):

        edges.append({
            "id":
                f"E{index}",

            "source":
                row[0],

            "target":
                row[1],

            "type":
                row[2],

            "reason":
                row[3]
        })


    return {
        "nodes":
            nodes,

        "edges":
            edges
    }


# ==================================================
# MANUAL FAILURE
# ==================================================

def fail_commitment(
    commitment_id
):

    check = graph.query("""
        MATCH (
            c:Commitment {
                id:$id
            }
        )

        RETURN
            c.id
    """, {
        "id":
            commitment_id
    })


    if not check.result_set:

        return None


    graph.query("""
        MATCH (
            c:Commitment {
                id:$id
            }
        )

        SET
            c.status =
                'failed'
    """, {
        "id":
            commitment_id
    })


    result = graph.query("""
        MATCH p =
            (affected:Commitment)
            -[:DEPENDS_ON*1..6]->
            (
                failed:Commitment {
                    id:$id
                }
            )

        RETURN
            affected.id,
            affected.name,
            affected.priority,
            length(p)

        ORDER BY
            length(p)
    """, {
        "id":
            commitment_id
    })


    affected = [
        {
            "id":
                row[0],

            "name":
                row[1],

            "priority":
                row[2],

            "distance":
                row[3]
        }
        for row in result.result_set
    ]


    return {
        "failed_commitment":
            commitment_id,

        "affected_count":
            len(
                affected
            ),

        "affected_commitments":
            affected
    }


# ==================================================
# RESET
# ==================================================

def reset_scenario():

    # ----------------------------------------------
    # Remove applied recovery decisions
    # ----------------------------------------------

    graph.query("""
        MATCH (
            d:RecoveryDecision
        )

        DETACH DELETE d
    """)


    # ----------------------------------------------
    # Reset commitment runtime state
    # ----------------------------------------------

    result = graph.query("""
        MATCH (
            c:Commitment
        )

        SET
            c.status =
                'active'

        RETURN
            count(c)
    """)


    # ----------------------------------------------
    # Restore suppliers to operational state
    #
    # IMPORTANT:
    # This resets runtime disruption status only.
    # It does NOT overwrite capacity, cost,
    # lead time or other company context.
    # ----------------------------------------------

    graph.query("""
        MATCH (
            v:Vendor
        )

        SET
            v.status =
                'available'
    """)


    # ----------------------------------------------
    # Restore resource runtime state
    # ----------------------------------------------

    graph.query("""
        MATCH (
            r:Resource
        )

        SET
            r.status =
                'available'
    """)


    count = (
        result
        .result_set[0][0]

        if result.result_set

        else 0
    )


    return {
        "status":
            "reset",

        "commitments_reset":
            count
    }


# ==================================================
# HTTP API
# ==================================================

class PromiseGraphHandler(
    BaseHTTPRequestHandler
):


    # ==============================================
    # JSON RESPONSE
    # ==============================================

    def send_json(
        self,
        data,
        status=200
    ):

        body = json.dumps(
            data,
            indent=2
        ).encode(
            "utf-8"
        )


        self.send_response(
            status
        )


        self.send_header(
            "Content-Type",
            "application/json"
        )


        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )


        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )


        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )


        self.end_headers()


        self.wfile.write(
            body
        )


    # ==============================================
    # READ JSON BODY
    # ==============================================

    def read_json_body(
        self
    ):

        content_length = int(
            self.headers.get(
                "Content-Length",
                0
            )
        )


        body = self.rfile.read(
            content_length
        ).decode(
            "utf-8"
        )


        if not body:

            return {}


        return json.loads(
            body
        )


    # ==============================================
    # CORS
    # ==============================================

    def do_OPTIONS(
        self
    ):

        self.send_response(
            204
        )


        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )


        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )


        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )


        self.end_headers()


    # ==============================================
    # GET
    # ==============================================

    def do_GET(
        self
    ):

        path = urlparse(
            self.path
        ).path


        # ------------------------------------------
        # HEALTH
        # ------------------------------------------

        if path == "/":

            self.send_json({
                "project":
                    "PromiseGraph",

                "status":
                    "running"
            })

            return


        # ------------------------------------------
        # COMMITMENTS
        # ------------------------------------------

        elif path == "/commitments":

            self.send_json(
                get_commitments()
            )

            return


        # ------------------------------------------
        # GRAPH
        # ------------------------------------------

        elif path == "/graph":

            self.send_json(
                get_graph_data()
            )

            return


        # ------------------------------------------
        # COMPANY CONTEXT
        # ------------------------------------------

        elif path == "/context":

            try:

                self.send_json(
                    get_company_context()
                )

            except Exception as error:

                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )

            return


        # ------------------------------------------
        # RECOVERY
        # ------------------------------------------

        elif path.startswith(
            "/recovery/"
        ):

            commitment_id = (
                path
                .split("/")[-1]
            )


            try:

                result = (
                    dynamic_recovery(
                        commitment_id
                    )
                )


                self.send_json(
                    result
                )

            except Exception as error:

                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )

            return


        # ------------------------------------------
        # NOT FOUND
        # ------------------------------------------

        else:

            self.send_json(
                {
                    "error":
                        "Not found"
                },
                404
            )

            return


    # ==============================================
    # POST
    # ==============================================

    def do_POST(
        self
    ):

        path = urlparse(
            self.path
        ).path


        # ==========================================
        # AGENT
        # ==========================================

        if path == "/agent":

            try:

                payload = (
                    self.read_json_body()
                )


            except json.JSONDecodeError:

                self.send_json(
                    {
                        "error":
                            "Invalid JSON"
                    },
                    400
                )

                return


            message = (
                payload
                .get(
                    "message",
                    ""
                )
                .strip()
            )


            if not message:

                self.send_json(
                    {
                        "error":
                            "Message is required"
                    },
                    400
                )

                return


            try:

                result = (
                    run_agent_with_fallback(
                        message
                    )
                )


                self.send_json(
                    result
                )


            except Exception as error:

                print(
                    "[PromiseGraph] "
                    "Agent error:",
                    error
                )


                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ==========================================
        # COMPANY CONTEXT UPDATE
        # ==========================================

        elif path == "/context":

            try:

                payload = (
                    self.read_json_body()
                )


            except json.JSONDecodeError:

                self.send_json(
                    {
                        "error":
                            "Invalid JSON"
                    },
                    400
                )

                return


            try:

                result = (
                    update_company_context(
                        payload
                    )
                )


                self.send_json(
                    result
                )


            except ValueError as error:

                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    400
                )


            except Exception as error:

                print(
                    "[PromiseGraph] "
                    "Context update error:",
                    error
                )


                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ==========================================
        # APPLY RECOMMENDED RECOVERY
        # ==========================================

        elif path.startswith(
            "/apply/"
        ):

            commitment_id = (
                path
                .split("/")[-1]
            )


            try:

                result = (
                    apply_recommended_recovery(
                        commitment_id
                    )
                )


                self.send_json(
                    result
                )


            except Exception as error:

                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ==========================================
        # RESET
        # ==========================================

        elif path == "/reset":

            try:

                self.send_json(
                    reset_scenario()
                )


            except Exception as error:

                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ==========================================
        # MANUAL FAILURE
        # ==========================================

        elif path.startswith(
            "/fail/"
        ):

            commitment_id = (
                path
                .split("/")[-1]
            )


            try:

                result = (
                    fail_commitment(
                        commitment_id
                    )
                )


                if result is None:

                    self.send_json(
                        {
                            "error":
                                "Commitment not found"
                        },
                        404
                    )


                else:

                    self.send_json(
                        result
                    )


            except Exception as error:

                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )


            return


        # ==========================================
        # NOT FOUND
        # ==========================================

        else:

            self.send_json(
                {
                    "error":
                        "Not found"
                },
                404
            )

            return


    # ==============================================
    # SERVER LOGGING
    # ==============================================

    def log_message(
        self,
        format,
        *args
    ):

        print(
            f"[PromiseGraph] "
            f"{format % args}"
        )


# ==================================================
# START SERVER
# ==================================================

if __name__ == "__main__":

    print(
        "PromiseGraph API running at "
        "http://127.0.0.1:8000"
    )


    print(
        "Company Context API available at "
        "http://127.0.0.1:8000/context"
    )


    server = ThreadingHTTPServer(
        (
            "127.0.0.1",
            8000
        ),
        PromiseGraphHandler
    )


    server.serve_forever()