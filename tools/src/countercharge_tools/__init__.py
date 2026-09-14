"""AgentCore Gateway tool Lambdas for Countercharge.

Three Gateway targets share one container image (see ``router.py``):
``engine`` (read-only), ``case`` (internal writes) and ``actions``
(external side effects, fail-closed). A fourth entrypoint,
``followup.handler``, is invoked directly by EventBridge Scheduler rather
than through the Gateway.
"""
