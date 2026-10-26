"""Execute the endpoint-test judge and dynamic platform discovery in Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
SOURCE = Path(__file__).parents[1] / "socialscrape/static/selftest.js"


def test_all_platforms_and_array_responses_are_checked_at_runtime():
    source = SOURCE.read_text().replace(
        "return {render:renderPage};", "return {judge, platforms, model};"
    )
    script = """const vm = require('vm'), assert = require('assert');
    const ctx = {window:{}, localStorage:{getItem(){return null;}}, document:{addEventListener(){}}};
    vm.createContext(ctx); vm.runInContext(SOURCE, ctx);
    const {judge, platforms, model} = ctx.window.EndpointTests;
    model.tests = [{platform:'youtube'}, {platform:'github'}, {platform:'bluesky'}, {platform:'github'}];
    assert.deepStrictEqual(Array.from(platforms()), ['youtube','github','bluesky']);
    const clip = {expect:['[].data.clip.id'], response_kind:'array'};
    assert.equal(judge(clip,200,'[{"data":{"clip":{"id":"clip1"}}}]').outcome,'passed');
    for(const body of ['[]','[{"data":{"clip":null}}]','{}']) assert.equal(judge(clip,200,body).outcome,'failed');
    assert.equal(judge({expect:['id']},200,'{"id":"x"}').outcome,'failed');
    assert.equal(judge({expect:['id']},200,'{"success":true,"id":"x"}').outcome,'passed');
    """.replace("SOURCE", json.dumps(source))
    subprocess.run(
        [NODE, "-e", script], check=True, capture_output=True, text=True, timeout=30
    )


def test_playground_and_docs_include_new_platforms_and_keep_metadata_out():
    import re

    source = (SOURCE.parent / "portal.js").read_text()
    function = re.search(
        r"function endpoints\(schema\) \{.*?\n\}", source, re.S
    ).group()
    script = """const assert=require('assert');
    const platformFromPath=path=>path.split('/')[2], platformName=x=>x;
    const documentedBody=op=>Boolean(op.requestBody?.content?.['application/json']);
    FUNCTION
    const schema={paths:{
      '/v1/youtube/video':{get:{}}, '/v1/bluesky/profile':{get:{}}, '/v1/github/user':{get:{}},
      '/v1/telegram/channel':{get:{}}, '/v1/linktree':{get:{}}, '/v1/account/credit-balance':{get:{}},
      '/v1/reddit/post/comments':{get:{},post:{requestBody:{content:{'application/json':{schema:{type:'object'}}}}}},
      '/meta/coverage':{get:{}},'/portal/api/me':{get:{}}
    }};
    const rows=endpoints(schema);
    assert.equal(rows.length,8);
    assert(rows.some(row=>row.platform==='github'));
    assert(rows.some(row=>row.platform==='account'));
    assert(rows.some(row=>row.method==='POST'&&row.platform==='reddit'));
    assert(!rows.some(row=>row.path.startsWith('/meta')||row.path.startsWith('/portal')));
    """.replace("FUNCTION", function)
    subprocess.run(
        [NODE, "-e", script], check=True, capture_output=True, text=True, timeout=30
    )
