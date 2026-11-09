FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt requirements-agent.txt ./
RUN pip install --no-cache-dir -r requirements-agent.txt
COPY agent agent
COPY mcp_server mcp_server
COPY knowledge_store knowledge_store
COPY knowledge knowledge
COPY simulator simulator
COPY failures failures
COPY scripts scripts
# The agent's MCP server runs as a subprocess of this process (python -m mcp_server.server).
CMD ["uvicorn", "agent.trigger:app", "--host", "0.0.0.0", "--port", "8000"]
