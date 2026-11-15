"""PostgreSQL predicates for transactionally maintained search projections.

Only internal constant identifiers and parameter names may be interpolated.
Patterns remain bound parameters. The joined text is a lossy index prefilter:
unnest confirms that a match belongs to one message, never across boundaries.
"""


def postgres_legacy_content_match_sql(table: str, pattern: str) -> str:
    return f"""(
        owui_chat_search_join({table}.owui_search_messages) LIKE {pattern}
        AND EXISTS (
            SELECT 1 FROM unnest({table}.owui_search_messages) AS content
            WHERE content LIKE {pattern}
        )
    )"""
