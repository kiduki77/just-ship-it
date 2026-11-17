"""The least a spec draft says about its users: one P1 story with a scenario and its empty case. write_spec drafts again,
then fails, without one (engineering/specs.py)."""
from justshipit.contracts.handoff import JourneyOut, JourneyStepOut, ScenarioOut, UserStoryOut

P1 = [UserStoryOut(
    title="The main task", priority="P1", story="The user does the main task.", why_priority="It is the product",
    independent_test="Do it once", scenarios=[ScenarioOut(given="a day's data", when="the user opens it", then="it shows")],
    empty_case=ScenarioOut(given="no data yet", when="the user opens it", then="an empty state"))]

JOURNEYS = [JourneyOut(
    title="Do the main task", persona="Mina, doing it for the first time", why="she needs the result today",
    brings="a day's data in a file", success="she has the result", priority="P1",
    steps=[JourneyStepOut(do="She opens the product", why="to start", see="The first screen"),
           JourneyStepOut(do="She does the main task", why="to get the result", see="The result")])]
