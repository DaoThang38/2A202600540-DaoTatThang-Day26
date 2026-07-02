@echo off
:: Set local npm cache folder
set NPM_CONFIG_CACHE=%~dp0.npm-cache
echo Starting MCP Inspector...
npx -y @modelcontextprotocol/inspector python "%~dp0mcp_server.py"
