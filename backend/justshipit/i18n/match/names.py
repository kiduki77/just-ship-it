"""What may follow a person's name in the same word (`services/people.named`). Korean writes an honorific and a
particle straight after a name ("김민수님께", "박은주씨에게"), so the name is the start of a longer word; anything else after
it ("이정희" after "이정") makes another name. A name of one or two syllables takes a particle only after an honorific,
since a bare one can be the last syllable of a longer name ("이하나" is not "이하" with 나). English has none: "Priya's"
splits at the apostrophe."""
from justshipit.i18n import Lang

AFTER_NAME: dict[Lang, tuple[str, ...]] = {
    "ko": ((r"(?:님|씨|선생님|사장님|대표님|실장님|팀장님)?"
            r"(?:께서|께|에게|한테|이랑|랑|하고|과|와|이|가|은|는|을|를|의|도|만|이나|나)?"),),
    "en": (),
}
AFTER_SHORT_NAME: dict[Lang, tuple[str, ...]] = {
    "ko": ((r"(?:님|씨|선생님|사장님|대표님|실장님|팀장님)"
            r"(?:께서|께|에게|한테|이랑|랑|하고|과|와|이|가|은|는|을|를|의|도|만|이나|나)?"),),
    "en": (),
}
SHORT_NAME = 2  # syllables
