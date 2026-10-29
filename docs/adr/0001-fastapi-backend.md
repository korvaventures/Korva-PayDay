# 1. FastAPI backend instead of the Next.js boilerplate
Status: accepted
The AG Grid boilerplate is a Next.js app with no invoicing code and no visible license.
We use FastAPI for all PayPal and LLM calls, matching our existing Python stack, and
use AG Studio only as a React front end that calls our REST API.
Trade-off: we hand-write the PayPal REST calls (httpx) instead of using the TypeScript
server SDK, which doesn't cover the Invoicing API anyway.