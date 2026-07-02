import os
import sys
import json
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware

# Add current directory to path so we can import db and init_db
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SQLiteAdapter, PostgreSQLAdapter, ValidationError, HAS_POSTGRES
from init_db import create_database

# Create the server object.
mcp = FastMCP("SQLite Lab MCP Server")

# Configure database connection
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and HAS_POSTGRES:
    print("[MCP Server] Using PostgreSQL database adapter.", file=sys.stderr)
    adapter = PostgreSQLAdapter(DATABASE_URL)
else:
    db_path = os.environ.get("SQLITE_DB_PATH", "local.db")
    # Automatically initialize SQLite database if it doesn't exist
    if not os.path.exists(db_path):
        print(f"[MCP Server] SQLite DB not found. Initializing database at {db_path}...", file=sys.stderr)
        create_database(db_path)
    else:
        print(f"[MCP Server] SQLite DB found at {db_path}.", file=sys.stderr)
    adapter = SQLiteAdapter(db_path)


class TokenAuthMiddleware(Middleware):
    """
    Middleware that intercepts incoming MCP requests and validates a Bearer token
    if the MCP_SECRET_TOKEN environment variable is set and the server is running
    over HTTP/SSE transport.
    """
    async def on_request(self, context, call_next):
        secret_token = os.environ.get("MCP_SECRET_TOKEN")
        if secret_token:
            try:
                # get_http_request raises RuntimeError if not in an HTTP context (e.g. STDIO)
                _ = get_http_request()
                
                # If we are here, we are on HTTP/SSE transport. Authenticate!
                headers = get_http_headers(include={"authorization"})
                auth_header = headers.get("authorization")
                expected = f"Bearer {secret_token}"
                
                if not auth_header or auth_header != expected:
                    raise ValidationError("Authentication failed: Invalid or missing Bearer token in Authorization header")
            except RuntimeError:
                # No HTTP context, running over local STDIO. Bypass authentication.
                pass
        return await call_next(context)


# Register token authentication middleware
mcp.add_middleware(TokenAuthMiddleware())


@mcp.tool(name="search")
def search(
    table: str, 
    filters: Optional[List[Dict[str, Any]]] = None, 
    columns: Optional[List[str]] = None, 
    limit: int = 20, 
    offset: int = 0, 
    order_by: Optional[str] = None, 
    descending: bool = False
) -> str:
    """
    Search records in a database table with filters, ordering, and pagination.
    
    Args:
        table: Name of the table (allowed: 'students', 'courses', 'enrollments')
        filters: Optional list of dicts specifying filter conditions.
                 Each filter must contain: 'column' (str), 'operator' (str), and 'value'.
                 Supported operators: '=', '!=', '<', '<=', '>', '>=', 'LIKE', 'IN'.
                 Example: [{"column": "cohort", "operator": "=", "value": "A1"}]
        columns: Optional list of specific columns to retrieve. Defaults to all columns.
        limit: Max number of rows to return (default 20, capped at 100).
        offset: Number of rows to skip (useful for pagination).
        order_by: Column to sort the results by.
        descending: Sort in descending order if True, otherwise ascending.
    """
    try:
        results = adapter.search(
            table=table,
            columns=columns,
            filters=filters,
            limit=limit,
            offset=offset,
            order_by=order_by,
            descending=descending
        )
        return json.dumps({
            "status": "success",
            "table": table,
            "count": len(results),
            "limit": min(limit, 100),
            "offset": offset,
            "data": results
        }, indent=2)
    except ValidationError as e:
        raise ValueError(f"Validation Error: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Database Error: {str(e)}")


@mcp.tool(name="insert")
def insert(table: str, values: Dict[str, Any]) -> str:
    """
    Insert a new row into a database table.
    
    Args:
        table: Name of the table to insert into ('students', 'courses', 'enrollments')
        values: Dictionary of column-value pairs representing the new record. Empty inserts are not allowed.
    """
    try:
        inserted_row = adapter.insert(table=table, values=values)
        return json.dumps({
            "status": "success",
            "table": table,
            "data": inserted_row
        }, indent=2)
    except ValidationError as e:
        raise ValueError(f"Validation Error: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Database Error: {str(e)}")


@mcp.tool(name="aggregate")
def aggregate(
    table: str, 
    metric: str, 
    column: Optional[str] = None, 
    filters: Optional[List[Dict[str, Any]]] = None, 
    group_by: Optional[List[str]] = None
) -> str:
    """
    Compute aggregate metrics (COUNT, AVG, SUM, MIN, MAX) with optional filters and groupings.
    
    Args:
        table: Name of the table ('students', 'courses', 'enrollments')
        metric: Aggregate function (allowed: 'COUNT', 'AVG', 'SUM', 'MIN', 'MAX')
        column: Column to perform aggregation on. Required for all metrics except COUNT.
        filters: Optional list of filters to apply before aggregating.
        group_by: Optional list of columns to group the results by.
    """
    try:
        results = adapter.aggregate(
            table=table,
            metric=metric,
            column=column,
            filters=filters,
            group_by=group_by
        )
        return json.dumps({
            "status": "success",
            "table": table,
            "metric": metric,
            "column": column,
            "group_by": group_by,
            "data": results
        }, indent=2)
    except ValidationError as e:
        raise ValueError(f"Validation Error: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Database Error: {str(e)}")


@mcp.resource("schema://database")
def database_schema() -> str:
    """
    Exposes the database schema snapshot as JSON.
    """
    try:
        tables = adapter.list_tables()
        full_schema = {}
        for t in tables:
            full_schema[t] = adapter.get_table_schema(t)
        return json.dumps({
            "database_type": "PostgreSQL" if (DATABASE_URL and HAS_POSTGRES) else "SQLite",
            "schema": full_schema
        }, indent=2)
    except Exception as e:
        raise RuntimeError(f"Failed to read database schema: {str(e)}")


@mcp.resource("schema://table/{table_name}")
def table_schema(table_name: str) -> str:
    """
    Exposes a dynamic table schema description as JSON.
    """
    try:
        # Validate that the table exists
        adapter._validate_table(table_name)
        schema = adapter.get_table_schema(table_name)
        return json.dumps({
            "table": table_name,
            "columns": schema
        }, indent=2)
    except ValidationError as e:
        raise ValueError(f"Validation Error: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Failed to read table schema: {str(e)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run FastMCP Database Server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"], help="Transport mechanism (stdio or sse)")
    parser.add_argument("--host", default="127.0.0.1", help="Host for SSE server")
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE server")
    args = parser.parse_args()

    if args.transport == "sse":
        print(f"[MCP Server] Starting SSE transport on {args.host}:{args.port}", file=sys.stderr)
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        print("[MCP Server] Starting STDIO transport (default)", file=sys.stderr)
        mcp.run(transport="stdio")
