import subprocess
import json
import os
import sys
import time

def run_command_in_mcp(proc, request_dict):
    """Sends a JSON-RPC request to the MCP server process and reads the response."""
    payload = json.dumps(request_dict) + "\n"
    proc.stdin.write(payload)
    proc.stdin.flush()
    
    # Read the response (which is a single line of JSON-RPC)
    line = proc.stdout.readline().strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        print(f"Failed to decode response: {line}")
        return None

def run_smoke_tests():
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
    
    # Ensure fresh SQLite DB
    db_path = "local.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    print("--- Starting Database MCP Server Process (STDIO) ---")
    proc = subprocess.Popen(
        [sys.executable, server_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True
    )
    
    # Give it a second to start
    time.sleep(1.0)
    
    if proc.poll() is not None:
        print(f"Error: Server process exited immediately with code {proc.poll()}")
        print("Check stderr logs above for details.")
        return False

    success = True
    try:
        # Step 1: Initialize the connection
        print("\n[1] Initializing connection...")
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "verify-script", "version": "1.0.0"}
            }
        }
        res = run_command_in_mcp(proc, init_req)
        print(f"Response: {json.dumps(res, indent=2)}")
        assert res and "result" in res, "Initialization failed"
        print("Initialization OK!")

        # Step 2: List Tools
        print("\n[2] Verifying tool discovery...")
        list_tools_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }
        res = run_command_in_mcp(proc, list_tools_req)
        tools = res.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        print(f"Discovered tools: {tool_names}")
        assert "search" in tool_names, "search tool missing"
        assert "insert" in tool_names, "insert tool missing"
        assert "aggregate" in tool_names, "aggregate tool missing"
        print("Tool discovery OK!")

        # Step 3: Call tool 'search' successfully
        print("\n[3] Calling search tool successfully (all students)...")
        search_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {
                    "table": "students",
                    "limit": 5
                }
            }
        }
        res = run_command_in_mcp(proc, search_req)
        content = res.get("result", {}).get("content", [])
        assert len(content) > 0, "No content returned"
        text_data = json.loads(content[0]["text"])
        print(f"Students found: {len(text_data['data'])}")
        for student in text_data["data"]:
            print(f" - {student['name']} ({student['cohort']})")
        assert len(text_data["data"]) == 5, "Expected 5 students"
        print("Search tool call OK!")

        # Step 4: Call tool 'insert' successfully
        print("\n[4] Calling insert tool successfully (new student)...")
        insert_req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "insert",
                "arguments": {
                    "table": "students",
                    "values": {
                        "name": "John Doe",
                        "email": "johndoe@university.edu",
                        "cohort": "A1"
                    }
                }
            }
        }
        res = run_command_in_mcp(proc, insert_req)
        content = res.get("result", {}).get("content", [])
        assert len(content) > 0, "No content returned"
        insert_result = json.loads(content[0]["text"])
        print(f"Inserted student: {json.dumps(insert_result['data'], indent=2)}")
        assert insert_result["status"] == "success", "Insert failed"
        assert insert_result["data"]["name"] == "John Doe", "Name mismatch"
        print("Insert tool call OK!")

        # Step 5: Call tool 'aggregate' successfully
        print("\n[5] Calling aggregate tool successfully (average grade)...")
        agg_req = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "aggregate",
                "arguments": {
                    "table": "enrollments",
                    "metric": "AVG",
                    "column": "grade",
                    "group_by": ["status"]
                }
            }
        }
        res = run_command_in_mcp(proc, agg_req)
        content = res.get("result", {}).get("content", [])
        assert len(content) > 0, "No content returned"
        agg_result = json.loads(content[0]["text"])
        print(f"Aggregation result: {json.dumps(agg_result['data'], indent=2)}")
        assert agg_result["status"] == "success", "Aggregate failed"
        print("Aggregate tool call OK!")

        # Step 6: Verify resources discovery
        print("\n[6] Verifying resource discovery...")
        list_res_req = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/list"
        }
        res = run_command_in_mcp(proc, list_res_req)
        resources = res.get("result", {}).get("resources", [])
        uris = [r["uri"] for r in resources]
        print(f"Discovered resources: {uris}")
        assert "schema://database" in uris, "schema://database missing"
        print("Resource discovery OK!")

        # Step 7: Read resource 'schema://database'
        print("\n[7] Reading resource 'schema://database'...")
        read_res_req = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {
                "uri": "schema://database"
            }
        }
        res = run_command_in_mcp(proc, read_res_req)
        contents = res.get("result", {}).get("contents", [])
        assert len(contents) > 0, "No content returned"
        schema_data = json.loads(contents[0]["text"])
        print(f"Schema type: {schema_data['database_type']}")
        print(f"Tables: {list(schema_data['schema'].keys())}")
        assert "students" in schema_data["schema"], "students table schema missing"
        print("Read resource OK!")

        # Step 8: Call tool with invalid input (invalid table name)
        print("\n[8] Calling search tool with invalid table name (expecting error)...")
        bad_req = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {
                    "table": "unknown_table"
                }
            }
        }
        res = run_command_in_mcp(proc, bad_req)
        print(f"Response (expecting error): {json.dumps(res, indent=2)}")
        # Check if error or isError is in response
        assert "error" in res or res.get("result", {}).get("isError") or "Error" in res.get("result", {}).get("content", [{}])[0].get("text", ""), "Error was not handled"
        print("Invalid table call rejected correctly!")

    except AssertionError as ae:
        print(f"\nAssertion Error during smoke testing: {ae}")
        success = False
    except Exception as e:
        print(f"\nUnexpected error during smoke testing: {e}")
        success = False
    finally:
        # Terminate server
        proc.terminate()
        proc.wait()
        print("\n--- Server Process Terminated ---")
    
    if success:
        print("\nALL SMOKE TESTS PASSED SUCCESSFULLY!")
    else:
        print("\nSMOKE TESTS FAILED!")
    return success

if __name__ == "__main__":
    success = run_smoke_tests()
    sys.exit(0 if success else 1)
