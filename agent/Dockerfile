# Countercharge case agent -- Strands agent packaged with the AgentCore Runtime
# contract (BedrockAgentCoreApp), hosted on ECS Fargate behind ALB + CloudFront.
# Build from the repo root so engine/core/agent are all in the build context:
#   docker build -f agent/Dockerfile -t countercharge-agent .
FROM python:3.12-slim

WORKDIR /app

COPY engine /app/build/engine
COPY core /app/build/core
COPY agent /app/build/agent

RUN pip install --no-cache-dir \
      /app/build/engine \
      /app/build/core \
      /app/build/agent \
    && cp -r /app/build/engine/data /app/data \
    && rm -rf /app/build

# Local Chromium for the charity_researcher subagent's browser tool
# (set BROWSER_MODE=agentcore to route through the AgentCore Browser service instead,
# which needs no local browser at all).
RUN pip install --no-cache-dir playwright \
    && python -m playwright install --with-deps chromium

# Baked-in fallback for the demo hospitals registry (small, static); the real CMS
# reference-data sqlite the engine tools use is not needed by the agent itself.
ENV HOSPITALS_PATH=/app/data/hospitals.json
ENV BROWSER_MODE=local
ENV MODEL_PROVIDER=venice

EXPOSE 8080

CMD ["python", "-m", "countercharge_agent.app"]
