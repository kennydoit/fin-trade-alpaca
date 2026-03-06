"""
Snowflake connection manager.

Provides a context-manager-friendly wrapper around
snowflake.connector that executes parameterised queries and
returns results as plain Python dicts.
"""

import logging
from types import TracebackType
from typing import Any, Optional

import snowflake.connector

logger = logging.getLogger(__name__)


class SnowflakeClient:
    """Thin wrapper around a Snowflake connection.

    Usage::

        with SnowflakeClient(account=..., user=..., ...) as sf:
            rows = sf.execute_query("SELECT 1 AS val")

    Or without a context manager::

        sf = SnowflakeClient(...)
        rows = sf.execute_query("SELECT 1 AS val")
        sf.close()
    """

    def __init__(
        self,
        account: str,
        user: str,
        password: str,
        database: str,
        schema: str = "PUBLIC",
        warehouse: str = "",
        role: str = "",
    ) -> None:
        connect_kwargs: dict[str, Any] = {
            "account": account,
            "user": user,
            "password": password,
            "database": database,
            "schema": schema,
        }
        if warehouse:
            connect_kwargs["warehouse"] = warehouse
        if role:
            connect_kwargs["role"] = role

        self._conn = snowflake.connector.connect(**connect_kwargs)
        logger.info("Connected to Snowflake account '%s' database '%s'", account, database)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "SnowflakeClient":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_query(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Execute *query* and return all rows as a list of dicts.

        Args:
            query:  SQL string.  Use %(name)s placeholders for parameters.
            params: Optional dict of parameter bindings.

        Returns:
            List of row dicts with column names as keys.
        """
        cursor = self._conn.cursor(snowflake.connector.DictCursor)
        try:
            cursor.execute(query, params or {})
            rows = cursor.fetchall()
            return list(rows)
        except snowflake.connector.Error as exc:
            logger.error("Snowflake query failed: %s", exc)
            raise
        finally:
            cursor.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying Snowflake connection."""
        try:
            self._conn.close()
            logger.info("Snowflake connection closed")
        except snowflake.connector.Error as exc:
            logger.warning("Error closing Snowflake connection: %s", exc)
