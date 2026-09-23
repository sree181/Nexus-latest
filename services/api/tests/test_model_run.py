"""Model-built runs: what the engine records when a real model does the work.

Driven by MockLLM, so these run offline and deterministically while still
exercising the whole governed path -- the recorder-backed toolset, the real AST
scan over the code the model submitted, the write gate, and the advisory join.
The point of most of them is what is *absent*: no taint is fabricated, nothing
the model merely asserted is recorded, and a model that fails to produce code
leaves a run that says so.
"""

from __future__ import annotations

import textwrap
import threading

import pytest

engine_gateway = pytest.importorskip("app.engine_gateway")

from app import advisories, engine_seed  # noqa: E402
from meshagent.llm import LLMStep, MockLLM  # noqa: E402
from meshagent.tools import ToolCall  # noqa: E402

# Imports yaml.load -- a real sink the AST scan detects -- alongside a package
# whose import name differs from its PyPI project.
PIPELINE = textwrap.dedent('''
    import cv2
    import yaml


    class FramePipeline:
        """Reads frames and a config written alongside them."""

        def __init__(self, config_path):
            self.config = yaml.load(open(config_path).read())
            self.capture = cv2.VideoCapture(0)

        def frame(self):
            ok, image = self.capture.read()
            return image if ok else None
''')


def _gateway():
    # only absence is a skip; anything else must fail where it can be seen
    try:
        return engine_gateway.EngineGateway()
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"HyperMesh engine unavailable: {exc}")


def _scripted(*, code: str | None, final: str) -> MockLLM:
    """A model that states an approach, optionally submits code, then finishes."""
    steps = [LLMStep(tool_calls=[ToolCall(
        name="record_decision",
        arguments={"statement": "stream frames with opencv and a yaml config"},
        call_id="c1")])]
    if code is not None:
        steps.append(LLMStep(tool_calls=[ToolCall(
            name="write_code", arguments={"code": code}, call_id="c2")]))
    steps.append(LLMStep(final_text=final))
    return MockLLM(steps=steps)


@pytest.fixture
def built(monkeypatch):
    """A gateway whose runs are built by a scripted model, with the advisory
    feeds stubbed so the test does not depend on the network."""

    def _build(code: str | None = PIPELINE, resolve=None):
        monkeypatch.setattr(engine_seed, "model_name", lambda: "mock-model")
        monkeypatch.setattr(
            engine_seed, "model_llm",
            lambda name: _scripted(code=code, final="Built FramePipeline."))
        if resolve is None:
            monkeypatch.setattr(advisories, "ENABLED", False)
        else:
            monkeypatch.setattr(advisories, "resolve", resolve)
        gw = _gateway()
        run = gw.create_run("Build a computer vision pipeline with opencv")
        frames = list(gw.run_stream(run.id))
        return gw, run, frames

    return _build


def test_a_model_run_does_not_claim_to_be_the_reference_build(built):
    _, run, _ = built()
    assert run.reference_build is False
    assert run.model == "mock-model"


def test_findings_come_from_the_code_the_model_wrote(built):
    """The sink is not something the model told us about: yaml.load is found by
    parsing the module it submitted."""
    gw, run, _ = built()
    findings = gw.run_findings(run.id)

    assert [f.sink for f in findings.findings] == ["yaml.load"]
    assert findings.present == 1
    assert [f.owner for f in findings.findings] == ["FramePipeline"]
    assert [f.cwe for f in findings.findings] == ["CWE-502"]
    # cv2.VideoCapture is not a sink, so nothing was invented for it
    assert all("cv2" not in f.sink for f in findings.findings)


def test_reachability_is_claimed_only_where_the_path_can_be_pointed_at(built):
    """PIPELINE reads a config with `yaml.load(open(path).read())`, so data
    from a file really does reach the sink and the scan can name the line it
    came in at. What it still may not do is rate the severity: knowing a
    value arrives is not knowing how bad that is."""
    gw, run, _ = built()
    findings = gw.run_findings(run.id)

    assert findings.exploitable == 1
    found = findings.findings[0]
    assert found.exploitable is True
    assert found.entry == "open() at line 10"
    assert found.rule == "py/external-data-reaches-sink"
    assert found.path == ["open() at line 10", "yaml.load"]
    assert found.severity is None      # no feed rated this; do not invent one


PARAMS_ONLY = textwrap.dedent('''
    import hashlib
    import subprocess


    class Transcoder:
        def run(self, src, dst):
            subprocess.run(f"ffmpeg -i {src} {dst}", shell=True)

        def digest(self, blob):
            return hashlib.md5(blob).hexdigest()
''')


def test_a_caller_supplied_argument_is_not_evidence_of_an_attacker(built):
    """Both calls are dangerous and both are reported present. Neither is
    called exploitable: a parameter says nothing about who the caller is, and
    if parameters counted, every sink would be exploitable and the column
    would stop meaning anything."""
    gw, run, _ = built(code=PARAMS_ONLY)
    findings = gw.run_findings(run.id)

    assert findings.present == 2
    assert findings.exploitable == 0
    assert {f.sink for f in findings.findings} == {"subprocess.run", "hashlib.md5"}


WEAK_CRYPTO_ON_FILE = textwrap.dedent('''
    import hashlib


    class Fingerprint:
        def digest(self, path):
            return hashlib.md5(open(path, "rb").read()).hexdigest()
''')


def test_weak_crypto_is_not_upgraded_by_data_reaching_it(built):
    """md5 is broken whoever supplies the bytes. External data reaching it is
    true and irrelevant, so reachability leaves it at present."""
    gw, run, _ = built(code=WEAK_CRYPTO_ON_FILE)
    findings = gw.run_findings(run.id)

    assert findings.present == 1
    assert findings.exploitable == 0


def test_the_loops_episode_is_described_not_called_redacted(built):
    """The agent loop closes a task with an episode record. It has its own
    payload shape, and falling through to the redaction wording would claim a
    forget that never happened."""
    _, _, frames = built()
    events = [f.memory for f in frames if f.type == "memory"]

    episode = next(e for e in events if e.step == "Closed the task")
    assert "success" in episode.detail
    assert "record_decision" in episode.detail and "write_code" in episode.detail
    # nothing in a fresh run has been redacted, so nothing may say it was
    assert all(e.detail != "redacted" for e in events)


def test_clean_code_is_distinguished_from_no_code(built):
    """Zero findings has two causes and they are opposite news. A run whose
    code was read and is clean must not be reported as a run that wrote none,
    so the count of what was scanned travels with the findings."""
    gw, run, _ = built()
    assert gw.run_findings(run.id).scanned == 1     # FramePipeline was read

    gw2, empty, _ = built(code=None)
    out = gw2.run_findings(empty.id)
    assert out.present == 0 and out.scanned == 0


def test_the_decision_is_derived_from_the_task_the_user_stated(built):
    """Provenance has to reach back to the task, which a different recorder
    wrote. If the seam between them broke, the chain would stop short."""
    gw, run, _ = built()
    why = gw.run_why(run.id, "class:FramePipeline")

    kinds = [node.kind for node in why.chain]
    assert "decision" in kinds and "source" in kinds
    assert any("Build a computer vision pipeline" in (n.statement or "")
               for n in why.chain)


def test_the_class_and_its_packages_are_recorded(built):
    gw, run, _ = built()
    payload = gw.run_graph(run.id)
    ids = {n.id for n in payload.nodes}

    assert "class:FramePipeline" in ids
    assert {"pkg:cv2", "pkg:yaml"} <= ids


def test_versions_and_advisories_come_from_the_feed(built):
    """What the SBOM says is the feed's answer, recorded against the imported
    name so an advisory still joins to the class that imports it."""

    def resolve(package, **_):
        if package != "cv2":
            return advisories.Resolved(package=package, project=package,
                                       unavailable="not looked up in this test")
        return advisories.Resolved(
            package="cv2", project="opencv-python", version="4.9.0.80",
            license="Apache 2.0",
            advisories=[advisories.Advisory(
                id="CVE-2024-0000", summary="a real-shaped advisory",
                severity="high", cwe="CWE-787")])

    gw, run, frames = built(resolve=resolve)

    sbom = gw.run_sbom(run.id)
    entry = next(e for e in sbom.entries if e.package == "cv2")
    assert entry.version == "4.9.0.80"
    assert entry.license == "Apache 2.0"

    impact = gw.cve_impact("CVE-2024-0000")
    assert impact.feed == "osv"
    assert impact.severity == "high"
    # the advisory reaches the class because the version was recorded under
    # the name the code imports
    assert any("FramePipeline" in c for c in impact.classes)

    # and the run says the project it really queried, since cv2 is not its name
    notices = " ".join(f.detail or "" for f in frames if f.type == "notice")
    assert "opencv-python" in notices


def test_an_unreachable_feed_is_disclosed_not_filled_in(built):
    """With feeds off, no version is invented and the run says why."""
    gw, run, frames = built()

    assert gw.run_sbom(run.id).entries == []
    notices = " ".join(f.detail or "" for f in frames if f.type == "notice")
    assert "cv2" in notices and "disabled" in notices


def test_a_model_that_writes_no_code_records_nothing_on_its_behalf(built):
    """A failed build is still an honest record: the task, the episode, and a
    statement that no code graph exists. Not an empty success."""
    gw, run, frames = built(code=None)

    assert gw.run_findings(run.id).present == 0
    assert "class" not in {n.kind for n in gw.run_graph(run.id).nodes}
    notices = " ".join(f.detail or "" for f in frames if f.type == "notice")
    assert "without submitting code that parses" in notices
    # the run still completed: what happened was recorded
    assert next(r for r in gw.runs() if r.id == run.id).status == "complete"


def test_a_provider_failure_is_reported_and_persisted(monkeypatch):
    class BrokenLLM:
        def step(self, **_kwargs):
            raise RuntimeError("provider body containing internal details")

    monkeypatch.setattr(engine_seed, "model_name", lambda: "unavailable-model")
    monkeypatch.setattr(engine_seed, "model_llm", lambda _name: BrokenLLM())
    gw = _gateway()
    run = gw.create_run("Build a tiny Python pipeline")
    frames = list(gw.run_stream(run.id))
    summary = gw.run(run.id)

    assert summary.status == "failed"
    assert summary.failure_reason is not None
    assert "unavailable-model" in summary.failure_reason
    assert "internal details" not in summary.failure_reason
    errors = [frame.detail for frame in frames if frame.type == "error"]
    assert errors == [summary.failure_reason]


def test_the_whole_wire_carries_a_model_build(monkeypatch):
    """The route, the socket and the gateway together, in engine mode: the
    frontend's actual path to a model-built run.

    Everything below the HTTP layer is real -- the write gate, the AST scan,
    the guarded store the loop writes its episode through. Only the model and
    the feeds are stubbed, because neither belongs in a test."""
    from fastapi.testclient import TestClient

    from app.main import app, gateway

    if type(gateway).__name__ != "EngineGateway":
        pytest.skip("wire test is about the engine path; set MESHAGENT_ENGINE=1")

    monkeypatch.setattr(engine_seed, "model_name", lambda: "stub-model")
    monkeypatch.setattr(
        engine_seed, "model_llm",
        lambda name: _scripted(code=PIPELINE, final="Built FramePipeline."))
    monkeypatch.setattr(advisories, "resolve", lambda package, **_: (
        advisories.Resolved(package=package, project=package, version="1.2.3",
                            license="MIT")))

    client = TestClient(app)
    created = client.post("/api/runs", json={
        "task": "Build a computer vision pipeline in python"}).json()
    assert created["reference_build"] is False
    assert created["model"] == "stub-model"

    run_id = created["id"]
    with client.websocket_connect(f"/api/runs/{run_id}/stream") as ws:
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] == "done":
                break

    kinds = [f["type"] for f in frames]
    assert kinds[-1] == "done"
    assert frames[-1]["run"]["status"] == "complete"
    assert frames[-1]["run"]["model"] == "stub-model"

    # the model's own writes arrived as memory frames, each with the envelope
    # the write gate gave it
    steps = [f["memory"]["step"] for f in frames if f["type"] == "memory"]
    assert "Made a decision" in steps
    assert "Wrote code" in steps
    assert "Found a dangerous call" in steps
    assert "Recorded a dependency" in steps
    # the module itself is a record, so the code behind the rest is readable
    assert "Submitted a module" in steps
    # PIPELINE reads its config out of a file, and that file reaches yaml.load
    assert "Scanned for reachability" in steps

    findings = client.get(f"/api/runs/{run_id}/findings").json()
    assert findings["present"] == 1 and findings["exploitable"] == 1

    code = client.get(f"/api/runs/{run_id}/code").json()
    assert code["modules"][0]["code"] == PIPELINE


def test_a_viewer_arriving_mid_recording_follows_it_instead_of_concluding(
        monkeypatch):
    """The case a slow build exposes and a fast one hides.

    The recording belongs to whoever claimed it -- and a remounting client
    opens the socket twice, so that is the socket it already threw away. The
    second viewer has to follow the writes to the end, not report whatever had
    landed when it connected as the finished run."""
    monkeypatch.setattr(engine_seed, "model_name", lambda: "slow-model")
    monkeypatch.setattr(advisories, "ENABLED", False)

    started = threading.Event()
    release = threading.Event()

    def slow_llm(name):
        model = _scripted(code=PIPELINE, final="Built FramePipeline.")
        real_step = model.step

        def step(**kwargs):
            # block inside the model's turn, so the recording is genuinely in
            # flight while the second viewer connects
            started.set()
            release.wait(timeout=10)
            return real_step(**kwargs)

        model.step = step
        return model

    monkeypatch.setattr(engine_seed, "model_llm", slow_llm)

    gw = _gateway()
    run = gw.create_run("Build a computer vision pipeline")

    recorder_frames: list = []
    recorder = threading.Thread(
        target=lambda: recorder_frames.extend(gw.run_stream(run.id)))
    recorder.start()
    assert started.wait(timeout=10), "the recording never began"

    # a second viewer, while the model is still working
    follower = gw.run_stream(run.id)
    first = next(follower)
    assert first.type == "memory"          # the task, already recorded
    release.set()

    frames = [first, *follower]
    recorder.join(timeout=20)

    assert frames[-1].type == "done"
    assert frames[-1].run.status == "complete"
    # the follower saw the model's work, not just the task it started with
    steps = [f.memory.step for f in frames if f.type == "memory"]
    assert "Made a decision" in steps and "Wrote code" in steps
    notices = " ".join(f.detail or "" for f in frames if f.type == "notice")
    assert "already being recorded by slow-model" in notices


def test_a_finished_run_joins_the_fleet_with_the_packages_it_really_uses(built):
    """The fleet has to be the fleet, not just what was seeded. A finished run
    takes its place in it -- and only for the packages its own code imports."""

    def resolve(package, **_):
        return advisories.Resolved(package=package, project=package,
                                   version="1.2.3", license="MIT")

    gw, run, frames = built(resolve=resolve)
    agents = dict(engine_seed.fleet_agents(gw._fleet))
    joined = f"run-{run.id}"

    assert joined in agents
    assert gw.fleet_overview().agents_active == 9   # eight seeded, plus this run
    assert engine_seed.agent_versions(gw._fleet, joined) == [
        "version:cv2@1.2.3", "version:yaml@1.2.3"]

    # sharing a fleet is not sharing a vulnerability: no advisory named a
    # weakness against its versions, so it is not entangled
    assert agents[joined] is False

    # and it is not drawn onto a version it never imported
    hits = {h.agent for h in gw.fleet_query("agents").hits}
    assert joined in hits
    assert engine_seed.PRIMARY_AGENT in hits
    assert "version:numpy@1.26.4" not in engine_seed.agent_versions(
        gw._fleet, joined)

    notices = " ".join(f.detail or "" for f in frames if f.type == "notice")
    assert f"joined the fleet as {joined}" in notices


def test_a_run_with_no_third_party_packages_does_not_join_the_fleet(built):
    """Nothing to share, nothing to say. An agent with no versions would sit
    in the fleet asserting a membership that carries no information."""
    gw, run, _ = built()          # feeds disabled, so no version is recorded
    assert f"run-{run.id}" not in dict(engine_seed.fleet_agents(gw._fleet))
    assert gw.fleet_overview().agents_active == 8


def _toolset(name: str):
    """A recorder-backed toolset over a throwaway run store."""
    from meshagent.build_tools import BuildLog, build_registry

    new = engine_seed.record_task(name, "a task")
    log = BuildLog()
    return build_registry(new.recorder, log, from_sources=[new.task_source]), log


def test_the_model_cannot_write_code_before_stating_an_approach():
    """Tool order is enforced by the toolset, not by the prompt: the code has
    to have a decision to be derived from."""
    tools, log = _toolset("order")

    result = tools.dispatch(ToolCall(name="write_code",
                                     arguments={"code": PIPELINE}))
    assert result.ok is False
    assert "record_decision" in (result.error or "")
    assert log.code == ""


def test_code_that_does_not_parse_is_a_tool_failure_not_a_broken_graph():
    """Provenance of code that does not compile cannot be captured, so the
    tool refuses and the model is told, leaving no partial class edge."""
    tools, log = _toolset("parse")
    tools.dispatch(ToolCall(name="record_decision",
                            arguments={"statement": "an approach"}))

    bad = tools.dispatch(ToolCall(name="write_code",
                                  arguments={"code": "class Broken(:"}))
    assert bad.ok is False and "SyntaxError" in (bad.error or "")

    # a module with no class is refused too: there would be nothing to govern
    empty = tools.dispatch(ToolCall(name="write_code",
                                    arguments={"code": "x = 1\n"}))
    assert empty.ok is False and "no class" in (empty.error or "")
    assert log.code == ""


def test_only_third_party_imports_enter_the_bill_of_materials():
    """A stdlib import carries no version and no advisory, so it is not a
    supply-chain entry at all."""
    from meshagent.build_tools import third_party

    assert third_party(["os", "json", "numpy", "cv2"]) == ["numpy", "cv2"]


# -- the OpenAI-compatible adapter -------------------------------------------

def test_the_adapter_translates_the_loops_turns_and_tool_calls():
    """The loop speaks one message dialect; this provider speaks another. The
    translation is the whole risk in the adapter, so it is checked directly
    rather than through a live call."""
    from meshagent.llm import _to_openai_messages, _to_openai_tools

    out = _to_openai_messages([
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "x1", "name": "write_code",
             "input": {"code": "pass"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x1",
             "content": "recorded", "is_error": False}]},
    ])

    assert out[0] == {"role": "user", "content": "do the thing"}
    assert out[1]["tool_calls"][0]["function"] == {
        "name": "write_code", "arguments": '{"code": "pass"}'}
    assert out[2] == {"role": "tool", "tool_call_id": "x1",
                      "content": "recorded"}

    schemas = _to_openai_tools([{"name": "t", "description": "d",
                                 "input_schema": {"type": "object"}}])
    assert schemas[0]["function"]["parameters"] == {"type": "object"}


class _FakeChat:
    """The shape the OpenAI SDK returns, without the SDK."""

    def __init__(self, message):
        self._message = message
        self.completions = self
        self.chat = self
        self.seen: dict = {}

    def create(self, **kwargs):
        self.seen = kwargs
        return type("R", (), {
            "choices": [type("C", (), {"message": self._message})()],
            "usage": type("U", (), {"prompt_tokens": 3,
                                    "completion_tokens": 4})(),
        })()


def test_the_adapter_reads_back_a_tool_call_and_a_final_answer():
    from meshagent.llm import OpenAILLM

    call = type("T", (), {
        "id": "call_1",
        "function": type("F", (), {"name": "record_decision",
                                   "arguments": '{"statement": "go"}'})(),
    })()
    asked = OpenAILLM(client=_FakeChat(
        type("M", (), {"tool_calls": [call], "content": None})()))
    step = asked.step(system="s", messages=[{"role": "user", "content": "t"}],
                      tools=[{"name": "record_decision", "description": "d",
                              "input_schema": {"type": "object"}}])
    assert step.is_final is False
    assert step.tool_calls[0].name == "record_decision"
    assert step.tool_calls[0].arguments == {"statement": "go"}
    assert step.usage == {"input_tokens": 3, "output_tokens": 4}

    done = OpenAILLM(client=_FakeChat(
        type("M", (), {"tool_calls": [], "content": "all built"})()))
    final = done.step(system="s", messages=[], tools=[])
    assert final.is_final and final.final_text == "all built"


def test_the_adapter_rejects_a_provider_response_without_choices():
    from meshagent.llm import ModelResponseError, OpenAILLM

    class EmptyChat:
        def __init__(self):
            self.completions = self
            self.chat = self

        def create(self, **_kwargs):
            return type("R", (), {"choices": None, "usage": None})()

    llm = OpenAILLM(model="missing-model", client=EmptyChat())
    with pytest.raises(ModelResponseError, match="returned no completion choices"):
        llm.step(system="s", messages=[], tools=[])


def test_a_malformed_tool_call_is_carried_to_the_tool_layer_not_crashed():
    """A model can emit unparseable arguments. That must become a recorded
    tool failure, not an exception that loses the run."""
    from meshagent.llm import OpenAILLM

    call = type("T", (), {
        "id": "c",
        "function": type("F", (), {"name": "write_code",
                                   "arguments": "{not json"})(),
    })()
    llm = OpenAILLM(client=_FakeChat(
        type("M", (), {"tool_calls": [call], "content": None})()))
    step = llm.step(system="s", messages=[], tools=[])

    assert step.tool_calls[0].arguments == {"__malformed_arguments__": "{not json"}


def test_one_advisory_earns_one_recommendation_however_many_runs_carry_it(built):
    """An advisory's blast radius is a fleet-wide question, so a CVE that two
    runs both record is still one thing to do. Emitting it once per run also
    repeated the id, which the recommendations list keys on, and reported the
    exploitable count of whichever run was being iterated."""

    def resolve(package, **_):
        out = advisories.Resolved(package=package, project=package,
                                  version="1.0.0", license="MIT")
        if package == "cv2":
            out.advisories = [advisories.Advisory(
                id="CVE-2024-0001", summary="opencv reads outside its root",
                severity="high", cwe="CWE-22")]
        return out

    gw, first, _ = built(resolve=resolve)
    second = gw.create_run("Build a second pipeline with opencv")
    list(gw.run_stream(second.id))
    assert first.id != second.id

    recs = gw.recommendations()
    ids = [r.id for r in recs]
    assert len(ids) == len(set(ids)), f"duplicate recommendation ids: {ids}"

    carried = [r for r in recs if r.id == "rec-cve-CVE-2024-0001"]
    assert len(carried) == 1
    # both runs import cv2, so both are inside the radius
    assert carried[0].agents >= 2


TWO_SITES = textwrap.dedent('''
    import requests


    class LabelMapFetcher:
        def fetch(self, url):
            return requests.get(url).json()


    class SeedCrawler:
        def crawl(self, url):
            return requests.get(url).text
''')


def test_the_same_sink_in_two_classes_is_two_call_sites(built):
    """Keying findings by sink name alone collapsed them, so the run header
    counted five and the screen listed four -- and the owner shown was
    whichever class happened to be recorded last. Two classes calling the
    same dangerous function are two places a person has to go and fix."""
    gw, run, _ = built(code=TWO_SITES)
    out = gw.run_findings(run.id)

    owners = sorted(f.owner for f in out.findings if f.sink == "requests.get")
    assert owners == ["LabelMapFetcher", "SeedCrawler"]
    # the header count and the listed rows have to be the same number
    assert out.present == len(out.findings)
    assert gw.runs()[-1].findings == out.present


def test_the_code_the_model_wrote_can_be_read_back(built):
    """Every other screen is an assertion about this text. Before, the source
    was parsed and dropped, so none of it could be checked."""
    gw, run, _ = built()
    out = gw.run_code(run.id)

    assert [m.name for m in out.modules] == ["main.py"]
    module = out.modules[0]
    assert module.code == PIPELINE          # verbatim, not reconstructed
    assert module.classes == ["FramePipeline"]
    assert out.sample is False


def test_a_run_that_wrote_no_code_has_no_module_to_show(built):
    gw, run, _ = built(code=None)
    assert gw.run_code(run.id).modules == []


def test_each_submission_is_its_own_module(built, monkeypatch):
    """A model may call write_code more than once. The second submission must
    not overwrite the first, or the code on screen is a lie about what ran."""
    monkeypatch.setattr(engine_seed, "model_name", lambda: "mock-model")
    monkeypatch.setattr(advisories, "ENABLED", False)
    steps = [
        LLMStep(tool_calls=[ToolCall(
            name="record_decision", arguments={"statement": "two modules"},
            call_id="c1")]),
        LLMStep(tool_calls=[ToolCall(
            name="write_code", arguments={"code": PIPELINE}, call_id="c2")]),
        LLMStep(tool_calls=[ToolCall(
            name="write_code", arguments={"code": TWO_SITES}, call_id="c3")]),
        LLMStep(final_text="Built both."),
    ]
    monkeypatch.setattr(engine_seed, "model_llm",
                        lambda name: MockLLM(steps=steps))
    gw = _gateway()
    run = gw.create_run("Write two modules")
    list(gw.run_stream(run.id))

    out = gw.run_code(run.id)
    assert [m.name for m in out.modules] == ["main.py", "main_2.py"]
    assert out.modules[0].code == PIPELINE
    assert out.modules[1].code == TWO_SITES
    assert out.modules[1].classes == ["LabelMapFetcher", "SeedCrawler"]
