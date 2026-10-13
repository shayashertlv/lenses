"""Single-use owner approvals (plan/WP2_DESIGN.md section 8, Approvals): approval-request.json binds request_sha256 =
sha of job.json, the input shas, the unsent owner notes, the instructions sha, the tool-registry sha and the settings;
approval.json carries the owner's exact words; a run refuses with exit code 3 when the approval is missing, stale,
consumed or below the requested cap. Nothing here sends a request (conftest: offline, loopback only)."""
import hashlib
import json

import pytest

from glasses3d.agent import approvals as A
from glasses3d.agent.approvals import ApprovalRefused, build_request, check_approval, request_sha256

INSTRUCTIONS = hashlib.sha256(b"WP7 instructions").hexdigest()
REGISTRY = hashlib.sha256(b"tool registry").hexdigest()
SETTINGS = {"model": "gpt-6-astra", "effort": "max", "max_output_tokens": 16_384, "max_turns": 60}
ESTIMATE = {"expected_usd": "10.11", "range_usd": ["8.90", "12.69"], "tail_usd": "2.87"}
WORDS = "Yes, go ahead with $15 for this run.\nThanks!  "


def make_job(root):
    job = root / "prada-8c82d986"
    (job / "inputs" / "images").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": "Prada"}), encoding="utf-8")
    (job / "inputs" / "images" / "reference-01.png").write_bytes(b"\x89PNG fake photo one")
    (job / "inputs" / "brief.txt").write_text("owner description", encoding="utf-8")
    return job


def request_for(job, **changes):
    kwargs = dict(instructions_sha256=INSTRUCTIONS, tool_registry_sha256=REGISTRY, settings=dict(SETTINGS),
                  unsent_owner_notes=[])
    kwargs.update(changes)
    return build_request(job, **kwargs)


def estimated(job, run_id="20261007T090000-aaaaaa", cap="15", **request_changes):
    request = request_for(job, **request_changes)
    run_dir = job / "runs" / run_id
    A.write_approval_request(run_dir, job_id=job.name, request=request, estimate=ESTIMATE, suggested_cap_usd=cap)
    return run_dir, request


def refused(code, call, *args, **kwargs):
    with pytest.raises(ApprovalRefused) as raised:
        call(*args, **kwargs)
    assert raised.value.code == code and raised.value.exit_code == 3
    return raised.value


def test_request_sha_covers_every_bound_input(tmp_path):
    job = make_job(tmp_path)
    base = request_sha256(request_for(job))
    assert base == request_sha256(request_for(job))                       # deterministic
    request = request_for(job)
    assert [entry["path"] for entry in request["inputs"]] == ["inputs/brief.txt", "inputs/images/reference-01.png"]
    variants = [request_for(job, unsent_owner_notes=["make the temples 3 mm longer"]),
                request_for(job, instructions_sha256=hashlib.sha256(b"other").hexdigest()),
                request_for(job, tool_registry_sha256=hashlib.sha256(b"other").hexdigest()),
                request_for(job, settings=dict(SETTINGS, effort="high")),
                request_for(job, settings=dict(SETTINGS, max_output_tokens=8192))]
    (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": "Prada 2"}), encoding="utf-8")
    variants.append(request_for(job))
    (job / "inputs" / "images" / "reference-01.png").write_bytes(b"\x89PNG a different photo")
    variants.append(request_for(job))
    (job / "inputs" / "images" / "reference-02.png").write_bytes(b"\x89PNG a new photo")
    variants.append(request_for(job))
    shas = [request_sha256(variant) for variant in variants]
    assert base not in shas and len(set(shas)) == len(shas)


def test_job_image_layout_binds_original_and_transport_copy(tmp_path, monkeypatch):
    from PIL import Image
    from glasses3d.agent import jobs
    from glasses3d.agent.brief import build_brief

    source = tmp_path / "photos"
    source.mkdir()
    Image.new("RGB", (31, 19), "red").save(source / "front.png", compress_level=0)
    views = tmp_path / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": "both"}}))
    monkeypatch.setattr(jobs, "MAX_REFERENCE_BYTES", 1000)
    root = jobs.create_job(images=source, views=views, name="Synthetic intake", description="A synthetic photo.",
                           jobs_root=tmp_path / "jobs")
    job = jobs.load_job(root)
    image, = job["images"]
    assert image["sent_file"] != image["file"]
    photos = jobs.references(root)
    inputs = A.run_input_binding(root, build_brief(job), photos)
    request = request_for(root, run_inputs=inputs)
    assert {item["path"] for item in request["inputs"]} == {image["file"], image["sent_file"]}
    assert inputs["photos"][0]["path"] == image["sent_file"]
    run_dir, _ = estimated(root, run_inputs=inputs)
    A.write_approval(run_dir, max_usd="15", owner_words=WORDS)
    check_approval(run_dir, current_request_sha256=request_sha256(request))

    # Changing an original invalidates its approval even when the sent derivative is unchanged.
    original = root / image["file"]
    original_payload = original.read_bytes()
    original.write_bytes(original_payload + b"changed-original")
    refused("stale", check_approval, run_dir,
            current_request_sha256=request_sha256(request_for(root, run_inputs=inputs)))
    original.write_bytes(original_payload)
    # A changed derivative cannot be sent under its earlier data URL and digest.
    (root / image["sent_file"]).write_bytes(b"changed-transport-image")
    refused("stale", A.run_input_binding, root, build_brief(job), photos)


def test_job_image_layout_requires_every_declared_sent_reference(tmp_path):
    from PIL import Image
    from glasses3d.agent import jobs
    from glasses3d.agent.brief import build_brief

    source = tmp_path / "photos"
    source.mkdir()
    for name, colour in (("front.png", "red"), ("side.png", "blue")):
        Image.new("RGB", (12, 8), colour).save(source / name)
    views = tmp_path / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": "both"},
                                "side.png": {"kind": "side", "side": "left"}}))
    root = jobs.create_job(images=source, views=views, name="Two photos", description="Both are authoritative.",
                           jobs_root=tmp_path / "jobs")
    text = build_brief(jobs.load_job(root))
    photos = jobs.references(root)
    assert len(A.run_input_binding(root, text, photos)["photos"]) == 2
    refused("stale", A.run_input_binding, root, text, photos[:1])
    refused("invalid", A.run_input_binding, root, text, [photos[0], photos[0]])


def test_build_request_refuses_what_it_cannot_bind(tmp_path):
    job = make_job(tmp_path)
    refused("invalid", request_for, job, instructions_sha256="not a sha")
    refused("invalid", request_for, job, tool_registry_sha256=INSTRUCTIONS.upper())
    refused("invalid", request_for, job, settings={"effort": "max"})
    refused("invalid", request_for, job, settings=dict(SETTINGS, odd={1, 2}))
    refused("invalid", request_for, job, unsent_owner_notes=[float("nan")])
    refused("invalid", build_request, tmp_path / "missing-job", instructions_sha256=INSTRUCTIONS,
            tool_registry_sha256=REGISTRY, settings=SETTINGS)


def test_approval_request_is_written_once_with_the_estimate(tmp_path):
    job = make_job(tmp_path)
    run_dir, request = estimated(job, cap="15")
    document = json.loads((run_dir / "approval-request.json").read_text(encoding="utf-8"))
    assert document["schema"] == "g3d.approval_request/1" and document["run_id"] == run_dir.name
    assert (document["estimate"], document["suggested_cap_usd"], document["requested_cap_usd"]) == (
        ESTIMATE, "15.000000", "15.000000")
    assert (document["effort"], document["max_output_tokens"], document["max_turns"]) == ("max", 16_384, 60)
    assert document["request_sha256"] == request_sha256(request) and document["request"] == request
    with pytest.raises(FileExistsError):
        A.write_approval_request(run_dir, job_id=job.name, request=request, estimate=ESTIMATE, suggested_cap_usd="9")
    for bad in ("0", "-1", "NaN", True):
        refused("invalid", A.write_approval_request, job / "runs" / f"r-{bad}", job_id=job.name, request=request,
                estimate=ESTIMATE, suggested_cap_usd=bad)


def test_approval_keeps_the_owner_words_verbatim_and_is_single_write(tmp_path):
    job = make_job(tmp_path)
    run_dir, _ = estimated(job)
    words = "כן, תריץ. $15 OK\n  (owner)  "
    document = A.write_approval(run_dir, max_usd="15", owner_words=words)
    saved = json.loads((run_dir / "approval.json").read_text(encoding="utf-8"))
    assert saved == document and saved["owner_words"] == words
    assert (saved["max_usd"], saved["max_micro_usd"], saved["authorized_by"]) == ("15.000000", 15_000_000, "owner")
    assert saved["approval_request_sha256"] == hashlib.sha256((run_dir / "approval-request.json").read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        A.write_approval(run_dir, max_usd="20", owner_words=words)
    other, _ = estimated(job, run_id="20261007T090001-bbbbbb")
    for bad_words in ("", "   \n", None, 5):
        refused("invalid", A.write_approval, other, max_usd="15", owner_words=bad_words)
    for bad_amount in ("0", "-3", "NaN", "Infinity", True, "fifteen"):
        refused("invalid", A.write_approval, other, max_usd=bad_amount, owner_words=words)
    refused("missing", A.write_approval, job / "runs" / "never-estimated", max_usd="15", owner_words=words)
    assert not (other / "approval.json").exists()


def test_check_passes_for_an_unconsumed_matching_approval(tmp_path):
    job = make_job(tmp_path)
    run_dir, request = estimated(job, cap="15")
    A.write_approval(run_dir, max_usd="15", owner_words=WORDS)
    approved = check_approval(run_dir, current_request_sha256=request_sha256(request_for(job)))
    assert (approved.run_id, approved.max_micro, approved.requested_cap_micro, approved.owner_words) == (
        run_dir.name, 15_000_000, 15_000_000, WORDS)
    assert approved.approval_sha256 == hashlib.sha256((run_dir / "approval.json").read_bytes()).hexdigest()
    assert approved.binding() == {
        "approval_sha256": approved.approval_sha256, "approval_request_sha256": approved.approval_request_sha256,
        "request_sha256": request_sha256(request), "max_micro_usd": 15_000_000,
        "requested_cap_micro_usd": 15_000_000}
    # the smoke run asks for less than approved: allowed, the run's cap is what it asked for
    assert check_approval(run_dir, current_request_sha256=approved.request_sha256,
                          requested_cap_usd="1").requested_cap_micro == 1_000_000


def test_check_refuses_missing(tmp_path):
    job = make_job(tmp_path)
    current = request_sha256(request_for(job))
    refused("missing", check_approval, job / "runs" / "nothing-here", current_request_sha256=current)
    run_dir, _ = estimated(job)
    error = refused("missing", check_approval, run_dir, current_request_sha256=current)
    assert "job approve" in str(error)


def test_owner_can_approve_more_than_the_estimate_suggests(tmp_path):
    job = make_job(tmp_path)
    run_dir, request = estimated(job, cap="15")
    A.write_approval(run_dir, max_usd="18", owner_words="Approved up to $18 for this run.")
    current = request_sha256(request)
    approved = check_approval(run_dir, current_request_sha256=current)
    assert approved.requested_cap_micro == approved.max_micro == 18_000_000
    assert check_approval(run_dir, current_request_sha256=current, requested_cap_usd="1").requested_cap_micro == 1_000_000
    refused("below_cap", check_approval, run_dir, current_request_sha256=current, requested_cap_usd="19")


def test_check_refuses_stale(tmp_path):
    job = make_job(tmp_path)
    run_dir, request = estimated(job)
    A.write_approval(run_dir, max_usd="15", owner_words=WORDS)
    current = request_sha256(request)
    (job / "inputs" / "images" / "reference-01.png").write_bytes(b"\x89PNG edited after the estimate")
    refused("stale", check_approval, run_dir, current_request_sha256=request_sha256(request_for(job)))
    refused("stale", check_approval, run_dir,
            current_request_sha256=request_sha256(request_for(job, unsent_owner_notes=["a new note"])))
    # the request file was edited after the owner approved it (same request sha, different bytes)
    path = run_dir / "approval-request.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["suggested_cap_usd"] = "150.000000"
    path.write_text(json.dumps(document), encoding="utf-8")
    refused("stale", check_approval, run_dir, current_request_sha256=current)


def test_check_refuses_an_approval_of_a_different_request(tmp_path):
    job = make_job(tmp_path)
    run_dir, request = estimated(job)
    A.write_approval(run_dir, max_usd="15", owner_words=WORDS)
    path = run_dir / "approval.json"
    forged = json.loads(path.read_text(encoding="utf-8"))
    forged["request_sha256"] = "0" * 64
    path.write_text(json.dumps(forged), encoding="utf-8")
    refused("stale", check_approval, run_dir, current_request_sha256=request_sha256(request))


def test_check_refuses_consumed(tmp_path):
    job = make_job(tmp_path)
    run_dir, request = estimated(job)
    A.write_approval(run_dir, max_usd="15", owner_words=WORDS)
    current = request_sha256(request)
    (run_dir / "budget.json").write_text("{}", encoding="utf-8")
    refused("consumed", check_approval, run_dir, current_request_sha256=current)
    refused("consumed", A.write_approval, run_dir, max_usd="15", owner_words=WORDS)
    other, request = estimated(job, run_id="20261007T090002-cccccc")
    A.write_approval(other, max_usd="15", owner_words=WORDS)
    (other / "transport").mkdir()
    (other / "transport" / "0001-request.json").write_text("{}", encoding="utf-8")
    refused("consumed", check_approval, other, current_request_sha256=current)


def test_check_refuses_below_the_requested_cap(tmp_path):
    job = make_job(tmp_path)
    run_dir, request = estimated(job, cap="15")
    A.write_approval(run_dir, max_usd="10", owner_words=WORDS)
    current = request_sha256(request)
    error = refused("below_cap", check_approval, run_dir, current_request_sha256=current)
    assert "$10.000000" in str(error) and "$15.000000" in str(error)
    assert check_approval(run_dir, current_request_sha256=current, requested_cap_usd="10").max_micro == 10_000_000
    refused("below_cap", check_approval, run_dir, current_request_sha256=current, requested_cap_usd="10.000001")


@pytest.mark.parametrize("damage", ["not json", "list", "schema", "run", "hash", "settings", "words", "amount"])
def test_check_refuses_malformed_files(tmp_path, damage):
    job = make_job(tmp_path)
    run_dir, request = estimated(job)
    A.write_approval(run_dir, max_usd="15", owner_words=WORDS)
    request_path, approval_path = run_dir / "approval-request.json", run_dir / "approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    document = json.loads(request_path.read_text(encoding="utf-8"))
    if damage == "not json":
        approval_path.write_text("{partial", encoding="utf-8")
    elif damage == "list":
        approval_path.write_text("[]", encoding="utf-8")
    elif damage == "schema":
        approval_path.write_text(json.dumps(dict(approval, schema="g3d.approval/0")), encoding="utf-8")
    elif damage == "run":
        approval_path.write_text(json.dumps(dict(approval, run_id="another-run")), encoding="utf-8")
    elif damage in ("hash", "settings"):
        if damage == "hash":
            document["request"]["settings"]["max_turns"] = 120
        else:
            document["max_turns"] = 120
        raw = json.dumps(document).encode("utf-8")
        request_path.write_bytes(raw)
        approval_path.write_text(json.dumps(dict(approval, approval_request_sha256=hashlib.sha256(raw).hexdigest())),
                                 encoding="utf-8")
    elif damage == "words":
        approval_path.write_text(json.dumps(dict(approval, owner_words="  ")), encoding="utf-8")
    else:
        approval_path.write_text(json.dumps(dict(approval, max_micro_usd=15_000_000.0)), encoding="utf-8")
    refused("invalid", check_approval, run_dir, current_request_sha256=request_sha256(request))


def test_latest_pending_run_skips_consumed_runs(tmp_path):
    job = make_job(tmp_path)
    assert A.latest_pending_run(job) is None
    first, _ = estimated(job, run_id="20261007T090000-aaaaaa")
    second, _ = estimated(job, run_id="20261007T100000-bbbbbb")
    assert A.latest_pending_run(job) == second.name
    (second / "budget.json").write_text("{}", encoding="utf-8")
    assert A.latest_pending_run(job) == first.name
    (job / "runs" / "20261007T110000-cccccc").mkdir()          # aborted before an estimate: not pending
    assert A.latest_pending_run(job) == first.name


def test_refusal_codes_are_closed():
    with pytest.raises(ValueError):
        ApprovalRefused("maybe", "no such refusal")
    assert A.EXIT_REFUSED == 3 and set(A.REFUSAL_CODES) == {"missing", "stale", "consumed", "below_cap", "invalid"}
