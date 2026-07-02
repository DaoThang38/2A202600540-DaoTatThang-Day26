import os
import json
import pytest
import sqlite3

from implementation.db import SQLiteAdapter, ValidationError, TABLE_SCHEMAS
from implementation.init_db import create_database
from implementation.mcp_server import search, insert, aggregate, database_schema, table_schema

@pytest.fixture
def temp_db(tmp_path):
    """Fixture that initializes a temporary SQLite database with seed data."""
    db_file = tmp_path / "test.db"
    create_database(str(db_file))
    return str(db_file)

@pytest.fixture
def sqlite_adapter(temp_db):
    """Fixture that returns a SQLiteAdapter instance connected to the temp DB."""
    return SQLiteAdapter(temp_db)


def test_list_tables(sqlite_adapter):
    tables = sqlite_adapter.list_tables()
    assert set(tables) == {"students", "courses", "enrollments"}


def test_get_table_schema(sqlite_adapter):
    schema = sqlite_adapter.get_table_schema("students")
    assert schema["name"] == "TEXT"
    assert schema["email"] == "TEXT"
    assert schema["cohort"] == "TEXT"


def test_search_all(sqlite_adapter):
    # Retrieve all columns from students
    results = sqlite_adapter.search("students")
    assert len(results) == 5
    assert results[0]["name"] == "Alice Smith"


def test_search_filters(sqlite_adapter):
    # Filter by cohort
    filters = [{"column": "cohort", "operator": "=", "value": "B2"}]
    results = sqlite_adapter.search("students", filters=filters)
    assert len(results) == 2
    assert {r["name"] for r in results} == {"Charlie Brown", "Diana Prince"}


def test_search_limit_offset(sqlite_adapter):
    # Search with limit and offset
    results = sqlite_adapter.search("students", limit=2, offset=1)
    assert len(results) == 2
    # Seed data order: Alice, Bob, Charlie...
    # offset 1 should give Bob and Charlie
    assert results[0]["name"] == "Bob Jones"
    assert results[1]["name"] == "Charlie Brown"


def test_search_order_by(sqlite_adapter):
    # Sort students descending by name
    results = sqlite_adapter.search("students", order_by="name", descending=True)
    assert results[0]["name"] == "Evan Wright"
    assert results[-1]["name"] == "Alice Smith"


def test_insert(sqlite_adapter):
    # Insert new student
    new_student = {"name": "Test Student", "email": "test@univ.edu", "cohort": "D4"}
    inserted = sqlite_adapter.insert("students", new_student)
    assert inserted["id"] is not None
    assert inserted["name"] == "Test Student"
    
    # Confirm it was saved
    search_res = sqlite_adapter.search("students", filters=[{"column": "email", "operator": "=", "value": "test@univ.edu"}])
    assert len(search_res) == 1
    assert search_res[0]["name"] == "Test Student"


def test_aggregate_count(sqlite_adapter):
    # Count students in cohort A1
    filters = [{"column": "cohort", "operator": "=", "value": "A1"}]
    res = sqlite_adapter.aggregate("students", "COUNT", filters=filters)
    assert len(res) == 1
    assert res[0]["value"] == 2


def test_aggregate_avg_grade_by_status(sqlite_adapter):
    # Group by enrollment status and compute average grade
    res = sqlite_adapter.aggregate("enrollments", "AVG", column="grade", group_by=["status"])
    assert len(res) == 2
    # status can be 'active' or 'completed'
    # Completed grades in seed data: 95.5 (Alice CS), 88.0 (Alice DB), 91.0 (Bob CS), 84.0 (Charlie DB), 92.0 (Charlie Hist), 98.0 (Diana CS), 90.0 (Diana Alg). Average is ~91.2
    # Active grades: 76.5 (Bob Alg), 82.5 (Evan Hist). Average is 79.5
    status_avgs = {r["status"]: r["value"] for r in res}
    assert status_avgs["completed"] > 90.0
    assert status_avgs["active"] == 79.5


def test_validation_invalid_table(sqlite_adapter):
    with pytest.raises(ValidationError, match="Unknown or unauthorized table"):
        sqlite_adapter.search("non_existent_table")


def test_validation_invalid_column(sqlite_adapter):
    with pytest.raises(ValidationError, match="Unknown column"):
        sqlite_adapter.search("students", columns=["invalid_column"])


def test_validation_invalid_filter_operator(sqlite_adapter):
    filters = [{"column": "cohort", "operator": "BAD_OP", "value": "A1"}]
    with pytest.raises(ValidationError, match="Unsupported filter operator"):
        sqlite_adapter.search("students", filters=filters)


def test_validation_empty_insert(sqlite_adapter):
    with pytest.raises(ValidationError, match="Cannot execute an empty insert"):
        sqlite_adapter.insert("students", {})


def test_validation_invalid_aggregate_metric(sqlite_adapter):
    with pytest.raises(ValidationError, match="Unsupported aggregate metric"):
        sqlite_adapter.aggregate("students", "INVALID_METRIC", column="id")


def test_mcp_tools(temp_db, monkeypatch):
    """Test the FastMCP tools using monkeypatch to direct the server to the temp DB."""
    monkeypatch.setenv("SQLITE_DB_PATH", temp_db)
    # We must reload adapter from env, but since mcp_server is already imported, we patch the adapter
    from implementation import mcp_server
    old_adapter = mcp_server.adapter
    mcp_server.adapter = SQLiteAdapter(temp_db)
    
    try:
        # Test tool 'search'
        res_str = search("students", limit=3)
        res = json.loads(res_str)
        assert res["status"] == "success"
        assert len(res["data"]) == 3
        
        # Test tool 'insert'
        new_course = {"title": "Calculus I", "instructor": "Dr. Newton"}
        ins_str = insert("courses", new_course)
        ins_res = json.loads(ins_str)
        assert ins_res["status"] == "success"
        assert ins_res["data"]["title"] == "Calculus I"

        # Test tool 'aggregate'
        agg_str = aggregate("students", "COUNT")
        agg_res = json.loads(agg_str)
        assert agg_res["status"] == "success"
        assert agg_res["data"][0]["value"] == 5

        # Test resource 'database_schema'
        schema_str = database_schema()
        schema_res = json.loads(schema_str)
        assert "students" in schema_res["schema"]

        # Test resource 'table_schema'
        tbl_str = table_schema("students")
        tbl_res = json.loads(tbl_str)
        assert tbl_res["table"] == "students"
        assert "cohort" in tbl_res["columns"]

    finally:
        mcp_server.adapter = old_adapter
