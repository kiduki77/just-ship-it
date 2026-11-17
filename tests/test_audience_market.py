"""A business's market decides the language of its places: no Nextdoor for 망원동, no 당근 for a shop in Ohio."""
import pytest
from justshipit.services import audience

WALKS = "맞벌이 반려견 보호자와 같은 동네 산책 도우미(대학생·은퇴자)를 연결하는 동네 기반 산책 대행 서비스"
DOG_WALKS = "Matches busy dog owners with neighbors who walk dogs, a door-to-door service for one neighborhood"


@pytest.mark.parametrize("texts,market", [
    ((WALKS,), "ko"),
    ((DOG_WALKS,), "en"),
    (("Dog walks in 망원동",), "ko"),
    (("Mango Coffee Roasters 망원동 동네 카페",), "ko"),
    (("Dogwalk", "망원동 주민을 위한 반려견 산책"), "en"),
    (("", "망원동 반려견 산책"), "ko"),
    (("", ""), "en"),
])
def test_the_market_is_the_language_the_owner_wrote_the_business_in(texts, market):
    assert audience.market_of_text(*texts) == market
