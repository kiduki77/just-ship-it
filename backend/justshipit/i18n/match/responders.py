"""Who answered a request, as `response.responded_by` stores it. The name for the Project Manager deciding
under a decision rule was written in Korean before the console spoke two languages. It is a stored value the
server and old rows match, not text the owner reads (the console labels it), so it stays as it is in every
language. Rows written before the role was renamed hold the former id in it (`agents.roles`)."""
from justshipit.agents.roles import FORMER_PROJECT_MANAGER, PROJECT_MANAGER

_UNDER_A_RULE = " (결정 권한)"
PROJECT_MANAGER_DECIDED = PROJECT_MANAGER + _UNDER_A_RULE
# Every value that means the Project Manager decided, the new one first.
DECIDED_UNDER_A_RULE = (PROJECT_MANAGER_DECIDED, FORMER_PROJECT_MANAGER + _UNDER_A_RULE)
# The only names a caller of the API may give itself: the owner, and Claude answering when the owner asked it to
# (`cli.DELEGATED`). Every other value is one the server writes.
FROM_THE_API = ("owner", "claude (오너 위임)")
