import abc
import re
import os
import sqlite3

try:
    import psycopg2
    import psycopg2.extras
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

class ValidationError(Exception):
    """Raised when a request cannot be safely executed due to validation failure."""
    pass

# Standard schemas for our relational database
TABLE_SCHEMAS = {
    'students': {
        'id': 'INTEGER',
        'name': 'TEXT',
        'email': 'TEXT',
        'cohort': 'TEXT'
    },
    'courses': {
        'id': 'INTEGER',
        'title': 'TEXT',
        'instructor': 'TEXT'
    },
    'enrollments': {
        'id': 'INTEGER',
        'student_id': 'INTEGER',
        'course_id': 'INTEGER',
        'grade': 'REAL',
        'status': 'TEXT'
    }
}

ALLOWED_METRICS = {"COUNT", "AVG", "SUM", "MIN", "MAX"}
ALLOWED_OPERATORS = {"=", "!=", "<", "<=", ">", ">=", "LIKE", "IN"}

class DatabaseAdapter(abc.ABC):
    """Abstract Base Class defining the shared interface for SQLite and PostgreSQL adapters."""

    @abc.abstractmethod
    def connect(self):
        """Establish a connection to the database."""
        pass

    @abc.abstractmethod
    def list_tables(self) -> list[str]:
        """List all user-defined tables in the database."""
        pass

    @abc.abstractmethod
    def get_table_schema(self, table: str) -> dict[str, str]:
        """Get schema (column names and types) for a given table."""
        pass

    @abc.abstractmethod
    def search(self, table: str, columns: list[str] = None, filters: list[dict] = None, limit: int = 20, offset: int = 0, order_by: str = None, descending: bool = False) -> list[dict]:
        """Search records in a table with validation, filtering, ordering, and pagination."""
        pass

    @abc.abstractmethod
    def insert(self, table: str, values: dict) -> dict:
        """Insert a row into a table and return the inserted payload including auto-generated fields."""
        pass

    @abc.abstractmethod
    def aggregate(self, table: str, metric: str, column: str = None, filters: list[dict] = None, group_by: list[str] = None) -> list[dict]:
        """Perform an aggregate query (COUNT, AVG, SUM, MIN, MAX) with optional filtering and grouping."""
        pass

    def _validate_table(self, table: str):
        """Verify the table name is valid and allowed."""
        if not table or not isinstance(table, str):
            raise ValidationError("Table name must be a non-empty string.")
        if table not in TABLE_SCHEMAS:
            raise ValidationError(f"Unknown or unauthorized table: '{table}'. Allowed tables: {list(TABLE_SCHEMAS.keys())}")

    def _validate_columns(self, table: str, columns: list[str]):
        """Verify columns exist in the table's schema."""
        if not columns:
            return
        schema = TABLE_SCHEMAS[table]
        for col in columns:
            if col not in schema:
                raise ValidationError(f"Unknown column '{col}' for table '{table}'. Valid columns: {list(schema.keys())}")

    def _validate_filters(self, table: str, filters: list[dict]):
        """Verify filter column, operator, and values are safe and valid."""
        if not filters:
            return
        if not isinstance(filters, list):
            raise ValidationError("Filters must be a list of dictionaries.")
        
        schema = TABLE_SCHEMAS[table]
        for f in filters:
            if not isinstance(f, dict):
                raise ValidationError("Each filter must be a dictionary.")
            if "column" not in f or "operator" not in f or "value" not in f:
                raise ValidationError("Filter must contain 'column', 'operator', and 'value' keys.")
            
            col = f["column"]
            op = f["operator"].upper()
            val = f["value"]

            if col not in schema:
                raise ValidationError(f"Unknown column '{col}' in filter for table '{table}'.")
            if op not in ALLOWED_OPERATORS:
                raise ValidationError(f"Unsupported filter operator '{op}'. Supported operators: {list(ALLOWED_OPERATORS)}")
            
            if op == "IN":
                if not isinstance(val, (list, tuple)):
                    raise ValidationError("Value for 'IN' operator must be a list or tuple.")
                if not val:
                    raise ValidationError("Value list for 'IN' operator cannot be empty.")

    def _validate_aggregate_request(self, table: str, metric: str, column: str = None, group_by: list[str] = None):
        """Validate aggregate arguments."""
        self._validate_table(table)
        metric_upper = metric.upper()
        if metric_upper not in ALLOWED_METRICS:
            raise ValidationError(f"Unsupported aggregate metric '{metric}'. Allowed: {list(ALLOWED_METRICS)}")
        
        if metric_upper != "COUNT" and not column:
            raise ValidationError(f"Metric '{metric}' requires a target column.")
        
        if column and column != "*":
            self._validate_columns(table, [column])
            
        if group_by:
            if isinstance(group_by, str):
                group_by = [group_by]
            self._validate_columns(table, group_by)

    def _build_where_clause(self, table: str, filters: list[dict], param_placeholder: str) -> tuple[str, list]:
        """
        Build a parameterized WHERE clause from filters.
        Returns a tuple of (sql_string, param_values).
        """
        if not filters:
            return "", []

        clauses = []
        params = []
        for f in filters:
            col = f["column"]
            op = f["operator"].upper()
            val = f["value"]

            if op == "IN":
                placeholders = ", ".join([param_placeholder] * len(val))
                clauses.append(f'"{col}" IN ({placeholders})')
                params.extend(val)
            else:
                clauses.append(f'"{col}" {op} {param_placeholder}')
                params.append(val)

        return " WHERE " + " AND ".join(clauses), params


class SQLiteAdapter(DatabaseAdapter):
    """SQLite implementation of the DatabaseAdapter."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.placeholder = "?"

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_tables(self) -> list[str]:
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            return [row["name"] for row in cursor.fetchall() if row["name"] in TABLE_SCHEMAS]

    def get_table_schema(self, table: str) -> dict[str, str]:
        self._validate_table(table)
        with self.connect() as conn:
            cursor = conn.cursor()
            # PRAGMA table_info is safe when table is validated against the whitelist
            cursor.execute(f"PRAGMA table_info({table})")
            rows = cursor.fetchall()
            return {row["name"]: row["type"] for row in rows}

    def search(self, table: str, columns: list[str] = None, filters: list[dict] = None, limit: int = 20, offset: int = 0, order_by: str = None, descending: bool = False) -> list[dict]:
        self._validate_table(table)
        self._validate_columns(table, columns)
        self._validate_filters(table, filters)
        
        # Max limit polish (Clamp to a max limit of 100 for pagination guidance)
        limit = min(max(0, limit), 100)
        offset = max(0, offset)

        cols_str = ", ".join([f'"{c}"' for c in columns]) if columns else "*"
        where_clause, params = self._build_where_clause(table, filters, self.placeholder)

        sql = f"SELECT {cols_str} FROM {table}{where_clause}"

        if order_by:
            self._validate_columns(table, [order_by])
            direction = "DESC" if descending else "ASC"
            sql += f" ORDER BY {order_by} {direction}"

        sql += f" LIMIT {limit} OFFSET {offset}"

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def insert(self, table: str, values: dict) -> dict:
        self._validate_table(table)
        if not values:
            raise ValidationError("Cannot execute an empty insert.")
        self._validate_columns(table, list(values.keys()))

        cols = list(values.keys())
        placeholders = ", ".join([self.placeholder] * len(cols))
        cols_str = ", ".join([f'"{c}"' for c in cols])
        sql = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})"

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, [values[c] for c in cols])
            inserted_id = cursor.lastrowid
            conn.commit()
            
            # Fetch and return full inserted record (including autogenerated IDs)
            cursor.execute(f"SELECT * FROM {table} WHERE rowid = ?", (inserted_id,))
            row = cursor.fetchone()
            return dict(row) if row else values

    def aggregate(self, table: str, metric: str, column: str = None, filters: list[dict] = None, group_by: list[str] = None) -> list[dict]:
        if isinstance(group_by, str):
            group_by = [group_by]
            
        self._validate_aggregate_request(table, metric, column, group_by)
        self._validate_filters(table, filters)

        metric_upper = metric.upper()
        col_expr = f'"{column}"' if column and column != "*" else "*"
        agg_expr = f"{metric_upper}({col_expr}) AS value"

        select_cols = []
        if group_by:
            select_cols.extend([f'"{g}"' for g in group_by])
        select_cols.append(agg_expr)
        
        where_clause, params = self._build_where_clause(table, filters, self.placeholder)
        sql = f"SELECT {', '.join(select_cols)} FROM {table}{where_clause}"

        if group_by:
            group_cols = ", ".join([f'"{g}"' for g in group_by])
            sql += f" GROUP BY {group_cols}"

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]


class PostgreSQLAdapter(DatabaseAdapter):
    """PostgreSQL implementation of the DatabaseAdapter."""

    def __init__(self, dsn: str):
        if not HAS_POSTGRES:
            raise ImportError("psycopg2 is required for PostgreSQLAdapter.")
        self.dsn = dsn
        self.placeholder = "%s"

    def connect(self):
        return psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def list_tables(self) -> list[str]:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                      AND table_type = 'BASE TABLE';
                """)
                return [row["table_name"] for row in cursor.fetchall() if row["table_name"] in TABLE_SCHEMAS]

    def get_table_schema(self, table: str) -> dict[str, str]:
        self._validate_table(table)
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = %s AND table_schema = 'public';
                """, (table,))
                rows = cursor.fetchall()
                return {row["column_name"]: row["data_type"] for row in rows}

    def search(self, table: str, columns: list[str] = None, filters: list[dict] = None, limit: int = 20, offset: int = 0, order_by: str = None, descending: bool = False) -> list[dict]:
        self._validate_table(table)
        self._validate_columns(table, columns)
        self._validate_filters(table, filters)

        limit = min(max(0, limit), 100)
        offset = max(0, offset)

        cols_str = ", ".join([f'"{c}"' for c in columns]) if columns else "*"
        where_clause, params = self._build_where_clause(table, filters, self.placeholder)

        sql = f"SELECT {cols_str} FROM {table}{where_clause}"

        if order_by:
            self._validate_columns(table, [order_by])
            direction = "DESC" if descending else "ASC"
            sql += f" ORDER BY {order_by} {direction}"

        sql += f" LIMIT {limit} OFFSET {offset}"

        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]

    def insert(self, table: str, values: dict) -> dict:
        self._validate_table(table)
        if not values:
            raise ValidationError("Cannot execute an empty insert.")
        self._validate_columns(table, list(values.keys()))

        cols = list(values.keys())
        placeholders = ", ".join([self.placeholder] * len(cols))
        cols_str = ", ".join([f'"{c}"' for c in cols])
        
        # PostgreSQL supports RETURNING clause to easily get the inserted payload
        sql = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) RETURNING *"

        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, [values[c] for c in cols])
                row = cursor.fetchone()
                conn.commit()
                return dict(row) if row else values

    def aggregate(self, table: str, metric: str, column: str = None, filters: list[dict] = None, group_by: list[str] = None) -> list[dict]:
        if isinstance(group_by, str):
            group_by = [group_by]
            
        self._validate_aggregate_request(table, metric, column, group_by)
        self._validate_filters(table, filters)

        metric_upper = metric.upper()
        col_expr = f'"{column}"' if column and column != "*" else "*"
        agg_expr = f"{metric_upper}({col_expr}) AS value"

        select_cols = []
        if group_by:
            select_cols.extend([f'"{g}"' for g in group_by])
        select_cols.append(agg_expr)

        where_clause, params = self._build_where_clause(table, filters, self.placeholder)
        sql = f"SELECT {', '.join(select_cols)} FROM {table}{where_clause}"

        if group_by:
            group_cols = ", ".join([f'"{g}"' for g in group_by])
            sql += f" GROUP BY {group_cols}"

        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
