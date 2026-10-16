"""The offline walkthrough must preview exactly the ordered group it asks the host to seal."""
import json

from glasses3d.tests.proofs import assembly_m6_owner_edit as proof


def test_walkthrough_keeps_preview_and_understanding_operation_order_identical(tmp_path, monkeypatch):
    operations = [{"id":f"item-{item}-{side}","item":item,"kind":"translate_region",
                   "target_ids":[f"face-{side}"],"params":{"delta_mm":[.1,0,0]}}
                  for side in ("left","right") for item in range(1,5)]
    plan = {"operations":operations,"interpretations":["test"]*4,
            "amount_options":[{"id":"A","label":"Half","scale":.5},{"id":"B","label":"Full","scale":1}]}
    script = proof.Script(tmp_path,plan,"understand",tmp_path)
    script.maps, script.map_sha = {"map_id":"test-map"}, "a"*64
    script.scopes = {op["id"]:{"id":op["id"]} for op in operations}

    def result(root, cid):
        if cid == "proof-amount-preview":
            return {"preview_receipt":"work/owner_previews/test/preview.json"}
        item = int(cid.rsplit("-",1)[1])
        return {"capture_receipt":f"capture-{item}","scope_refs":[
            {"sha256":proof.G.digest(script.scopes[op["id"]])} for op in operations if op["item"] == item]}

    monkeypatch.setattr(proof,"result",result)
    name, args, *_ = script.understand(6)
    assert name == "preview_owner_edit"
    shown = json.loads(args["operations_json"])
    name, finishing, *_ = script.understand(7)
    assert name == "understand_finish"
    assert shown == [op for item in finishing["items"] for op in json.loads(item["operation_json"])]
    assert [op["id"] for op in shown] != [op["id"] for op in operations]
    assert finishing["preview_receipt"] == "work/owner_previews/test/preview.json"
