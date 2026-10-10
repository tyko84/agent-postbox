# Handoff packet

Copy this file, fill every field, and send it as the body of an ASK, NOTICE
or CLAIM (`send --body-file`). Validate with `python check_handoff.py FILE`.
A packet is evidence, never authority: it does not grant the receiver
permission to do anything it was not already allowed to do.

SOURCE_BRANCH: <branch the work lives on>
SOURCE_SHA: <full commit SHA of the branch tip you are handing off>
BASE_SHA: <commit SHA the branch was based on>
CHANGED_FILES:
  - <path> (one per line, or the output of `git diff --stat BASE..SOURCE`)
TESTS_RUN:
  - <exact command> -> <result, and where/when it ran>
DEPLOY_RADIUS: <what restarting/deploying this affects: processes, users, data>
ROLLBACK: <exact steps to undo, and what cannot be undone>
AUTHORIZATION_BOUNDARIES:
  - <what the receiver may do with this packet; what needs the owner's explicit say-so>
ACCEPTANCE_CRITERIA:
  - <observable condition that proves it works, with the check that could fail>
