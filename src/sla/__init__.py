"""Simple Link Analysis: a browser-based network analysis tool.

The knowledge graph lives in Neo4j and conforms to the ontology declared in
``ontology/ontology.yaml``. Application state — charts, node positions, staged
proposals, jobs and the review queue — lives in a separate relational store so
the knowledge graph stays clean and portable.
"""

__version__ = "0.1.0"
