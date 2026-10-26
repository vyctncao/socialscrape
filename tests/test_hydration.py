import json

import pytest

from socialscrape.platforms import _hydration as h


def test_literal_state_keeps_values_and_ignores_executable_code_outside_the_object():
    text = 'document.doNotRun();profile:$R[1]={id:"7",username:"sample",flag:!1,items:$R[2]=[$R[3]={url:"https://example.com"}],bio:"literal !0 $R[3]= text"};window.doNotRun();'
    value = h.reference_object(text, "profile")
    assert value == {
        "id": "7",
        "username": "sample",
        "flag": False,
        "items": [{"url": "https://example.com"}],
        "bio": "literal !0 $R[3]= text",
    }


@pytest.mark.parametrize(
    "value",
    ["{id:runArbitraryCode()}", "{id:1,items:$R[99]}", "{id:1+2}", "{id:new Date()}"],
)
def test_expressions_and_unavailable_references_are_rejected(value):
    with pytest.raises(h.UpstreamError):
        h.reference_object("profile:$R[1]=" + value, "profile")


def test_unclosed_or_deep_state_is_not_read_as_a_partial_profile(monkeypatch):
    with pytest.raises(h.UpstreamError):
        h.reference_object('profile:$R[1]={id:"7"', "profile")
    with pytest.raises(h.UpstreamError):
        h.reference_object(
            "profile:$R[1]={a:" + "[" * 65 + "0" + "]" * 65 + "}", "profile"
        )
    monkeypatch.setattr(h, "MAX_STATE_BYTES", 16)
    with pytest.raises(h.UpstreamError):
        h.reference_object(
            "profile:$R[1]=" + json.dumps({"long": "x" * 100}), "profile"
        )
