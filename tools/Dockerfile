# AgentCore Gateway tool Lambda image -- one image, three targets
# (engine/case/actions), selected per-function via the CC_TARGET env var.
# Build from the repo root so engine/core/tools are all in the build context:
#   docker build -f tools/Dockerfile .
FROM public.ecr.aws/lambda/python:3.12

COPY engine ${LAMBDA_TASK_ROOT}/build/engine
COPY core ${LAMBDA_TASK_ROOT}/build/core
COPY tools ${LAMBDA_TASK_ROOT}/build/tools

RUN pip install --no-cache-dir \
      "${LAMBDA_TASK_ROOT}/build/engine" \
      "${LAMBDA_TASK_ROOT}/build/core" \
      "${LAMBDA_TASK_ROOT}/build/tools" \
    && cp -r "${LAMBDA_TASK_ROOT}/build/engine/data" "${LAMBDA_TASK_ROOT}/data" \
    && rm -rf "${LAMBDA_TASK_ROOT}/build"

# Baked-in fallback for the demo hospitals registry (small, static). The
# real CMS reference-data sqlite is too big to bake in and is expected via
# REFDATA_S3_URI in deployed environments; HOSPITALS_PATH/REFDATA_PATH can
# still be overridden per-function.
ENV HOSPITALS_PATH=${LAMBDA_TASK_ROOT}/data/hospitals.json

CMD ["countercharge_tools.router.handler"]
