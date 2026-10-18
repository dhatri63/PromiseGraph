from falkordb import FalkorDB

# Connect to FalkorDB running in Docker
db = FalkorDB(host="localhost", port=6379)

# Use the graph we created earlier
graph = db.select_graph("PromiseGraph")

print("Connected to FalkorDB successfully!\n")

# Show all commitments
result = graph.query("""
MATCH (c:Commitment)
RETURN c.id, c.name, c.status
ORDER BY c.id
""")

print("COMMITMENTS:")
for row in result.result_set:
    print(row)

# Find every commitment affected by C1
result = graph.query("""
MATCH (affected:Commitment)-[:DEPENDS_ON*1..6]->
      (failed:Commitment {id:'C1'})
RETURN affected.id, affected.name
""")

print("\nAFFECTED BY C1 FAILURE:")
for row in result.result_set:
    print(row)