"""Application state: charts, positions, jobs and the review queue.

Deliberately separate from Neo4j. The knowledge graph stays clean and portable
for other tools to consume, and interface state lives in an ordinary relational
database — SQLite locally, PostgreSQL when this goes multi-user.

This separation is what makes "remove from the canvas" and "delete from the
database" genuinely different operations.
"""
