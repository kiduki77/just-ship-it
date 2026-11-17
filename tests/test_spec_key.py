from justshipit.db.models import Spec
from justshipit.engineering.specs import _spec_key
from justshipit.services import projects


def test_projects_with_same_slug_prefix_get_distinct_spec_keys(s):
    a = projects.create(s, "g", name="test-dev-pipeline", start=False)
    b = projects.create(s, "g", name="test-dev-pipeline", start=False)   # slug test-dev-pipeline-2
    s.flush()
    s.add(Spec(spec_key=_spec_key(s, a), project_id=a.id, title="t", registry_path="x"))
    s.flush()
    key_b = _spec_key(s, b)
    assert key_b != "TESTDE-0001" and key_b == f"TESTDE{b.id}-0001"
    s.add(Spec(spec_key=_spec_key(s, a), project_id=a.id, title="t2", registry_path="y"))
    s.flush()
    assert _spec_key(s, a) == "TESTDE-0003"
