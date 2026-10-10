# Handoff packet (example)

SOURCE_BRANCH: feature/retry-backoff
SOURCE_SHA: 9f1c2ab7d3e4506172839405a6b7c8d9e0f1a2b3
BASE_SHA: 3a2c90e
CHANGED_FILES:
  - billing/client.py
  - tests/test_client_retry.py
TESTS_RUN:
  - python -m pytest tests/test_client_retry.py -> 14 passed (laptop, 2030-01-01)
DEPLOY_RADIUS: the billing worker only; restart takes ~5s and drops in-flight jobs, which are retried
ROLLBACK: revert the merge commit and restart the worker; no data migration is involved
AUTHORIZATION_BOUNDARIES:
  - Receiver may review and run the tests.
  - Merging and restarting the worker need the owner's explicit approval.
ACCEPTANCE_CRITERIA:
  - A forced 503 from the upstream is retried 3 times with backoff (test_retry_503 fails if it is not).
  - No change to the public function signatures of billing/client.py.
