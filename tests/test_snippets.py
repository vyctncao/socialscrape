"""The docs' and playground's code cards (socialscrape/static/snippets.js), run under Node.js when it is installed."""

from __future__ import annotations

import html
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SNIPPETS = ROOT / "socialscrape" / "static" / "snippets.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
URL = "https://scrape.example"


def _node(expression: str, stdin: str | None = None):
    script = (f"const S = require({json.dumps(str(SNIPPETS))});\n"
              "const input = require('fs').readFileSync(0, 'utf8');\n"
              f"process.stdout.write(JSON.stringify({expression}));")
    return json.loads(subprocess.run([NODE, "-e", script], input=stdin or "", capture_output=True, text=True,
                                     check=True, timeout=30).stdout)


def build(method: str, path: str, params: dict) -> dict:
    return _node(f"S.build({json.dumps({'method': method, 'url': URL + path, 'params': params})})")


GET = {
    "curl": '''curl "https://scrape.example/v1/tiktok/profile?handle=stoolpresidente&trim=false" \\
  -H "x-api-key: <api_token>"''',
    "node": '''import axios from 'axios';

const { data } = await axios.get(
  'https://scrape.example/v1/tiktok/profile?handle=stoolpresidente&trim=false',
  {
    headers: {
      'x-api-key': '<api_token>'
    }
  }
);''',
    "python": '''import requests

url = "https://scrape.example/v1/tiktok/profile?handle=stoolpresidente&trim=false"
headers = {
    "x-api-key": "<api_token>"
}

response = requests.get(url, headers=headers)
data = response.json()
print(data)''',
    "php": '''<?php

$url = "https://scrape.example/v1/tiktok/profile?handle=stoolpresidente&trim=false";

$curl = curl_init();

curl_setopt_array($curl, [
  CURLOPT_URL => $url,
  CURLOPT_RETURNTRANSFER => true,
  CURLOPT_HTTPHEADER => [
    "x-api-key: <api_token>"
  ]
]);

$response = curl_exec($curl);
curl_close($curl);

$data = json_decode($response, true);
print_r($data);''',
    "go": '''package main

import (
  "encoding/json"
  "fmt"
  "io"
  "net/http"
)

func main() {
  url := "https://scrape.example/v1/tiktok/profile?handle=stoolpresidente&trim=false"
  
  req, err := http.NewRequest("GET", url, nil)
  if err != nil {
    panic(err)
  }
  
  req.Header.Set("x-api-key", "<api_token>")
  
  client := &http.Client{}
  resp, err := client.Do(req)
  if err != nil {
    panic(err)
  }
  defer resp.Body.Close()
  
  body, err := io.ReadAll(resp.Body)
  if err != nil {
    panic(err)
  }
  
  var result map[string]interface{}
  json.Unmarshal(body, &result)
  fmt.Printf("%+v", result)
}''',
    "java": '''import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.URI;
import java.net.http.HttpResponse.BodyHandlers;

public class Main {
    public static void main(String[] args) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://scrape.example/v1/tiktok/profile?handle=stoolpresidente&trim=false"))
            .header("x-api-key", "<api_token>")
            .GET()
            .build();
        
        HttpResponse<String> response = client.send(request, BodyHandlers.ofString());
        
        System.out.println(response.body());
    }
}''',
}
BODY = '{\n  "pageId": "367152833370567",\n  "trim": false\n}'
POST = {
    "curl": f'''curl --request POST \\
  --url 'https://scrape.example/v1/facebook/adLibrary/company/ads' \\
  --header 'Content-Type: application/json' \\
  --header 'x-api-key: <api_token>' \\
  --data '{BODY}\'''',
    "node": f'''const response = await fetch('https://scrape.example/v1/facebook/adLibrary/company/ads', {{
  method: 'POST',
  headers: {{
    'Content-Type': 'application/json',
    'x-api-key': '<api_token>'
  }},
  body: JSON.stringify({BODY})
}});

const data = await response.json();
console.log(data);''',
    "python": f'''import json
import requests

url = "https://scrape.example/v1/facebook/adLibrary/company/ads"
headers = {{
    "Content-Type": "application/json",
    "x-api-key": "<api_token>"
}}
payload = json.loads(r\'\'\'{BODY}\'\'\')

response = requests.post(url, headers=headers, json=payload)
print(response.json())''',
    "php": f'''<?php

$url = 'https://scrape.example/v1/facebook/adLibrary/company/ads';
$payload = <<<'JSON'
{BODY}
JSON;

$curl = curl_init($url);
curl_setopt_array($curl, [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => $payload,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER => [
        'Content-Type: application/json',
        'x-api-key: <api_token>'
    ]
]);

$response = curl_exec($curl);
curl_close($curl);
echo $response;''',
    "go": f'''package main

import (
  "fmt"
  "io"
  "net/http"
  "strings"
)

func main() {{
  payload := strings.NewReader(`{BODY}`)
  req, _ := http.NewRequest("POST", "https://scrape.example/v1/facebook/adLibrary/company/ads", payload)
  req.Header.Add("Content-Type", "application/json")
  req.Header.Add("x-api-key", "<api_token>")

  response, _ := http.DefaultClient.Do(req)
  defer response.Body.Close()
  data, _ := io.ReadAll(response.Body)
  fmt.Println(string(data))
}}''',
    "java": f'''import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class Main {{
  public static void main(String[] args) throws Exception {{
    String requestBody = {json.dumps(BODY)};
    HttpRequest request = HttpRequest.newBuilder()
        .uri(URI.create("https://scrape.example/v1/facebook/adLibrary/company/ads"))
        .header("Content-Type", "application/json")
        .header("x-api-key", "<api_token>")
        .POST(HttpRequest.BodyPublishers.ofString(requestBody))
        .build();

    HttpResponse<String> response = HttpClient.newHttpClient().send(
        request,
        HttpResponse.BodyHandlers.ofString()
    );
    System.out.println(response.body());
  }}
}}''',
}


def test_get_reproduces_scrapecreators_six_layouts_with_the_query():
    snippets = build("GET", "/v1/tiktok/profile", {"handle": "stoolpresidente", "user_id": "", "trim": False})
    assert snippets == GET  # an empty value stays out of the query; false is an example like any other
    assert _node("S.languages") == [["curl", "cURL"], ["node", "Node.js"], ["python", "Python"], ["php", "PHP"],
                                    ["go", "Go"], ["java", "Java"]]


def test_post_reproduces_scrapecreators_six_layouts_with_the_typed_body():
    snippets = build("POST", "/v1/facebook/adLibrary/company/ads", {"pageId": "367152833370567", "companyName": "",
                                                                    "trim": False})
    assert snippets == POST


def test_a_typed_value_cannot_end_its_literal():
    body = {"query": "it's ''' `tick` \"q\" \\ </span>"}
    snippets = build("POST", "/v1/facebook/adLibrary/search/ads", body)
    command = shlex.split(snippets["curl"].replace("\\\n", " "))
    assert json.loads(command[command.index("--data") + 1]) == body  # the shell reads back the same JSON
    python = re.search(r"json\.loads\((r'''.*''')\)", snippets["python"], re.S)[1]
    assert json.loads(eval(python)) == body  # noqa: S307 - the snippet's own raw-string literal
    go = re.search(r"strings\.NewReader\(`(.*)`\)", snippets["go"], re.S)[1]
    assert "`" not in go and json.loads(go) == body
    java = re.search(r"String requestBody = (.*);\n", snippets["java"])[1]
    assert json.loads(json.loads(java)) == body
    url = build("GET", "/v1/tiktok/profile", {"handle": "o'neil & co"})["node"]
    assert "'https://scrape.example/v1/tiktok/profile?handle=o%27neil%20%26%20co'," in url
    # Inside the cURL's double quotes, an interactive shell would expand "!amazing" from its history.
    curl = build("GET", "/v1/youtube/search", {"query": "wow!amazing"})["curl"]
    assert curl.startswith('curl "https://scrape.example/v1/youtube/search?query=wow%21amazing" \\\n') and "!" not in curl


def test_highlighting_escapes_every_value_and_keeps_the_text():
    hostile = build("POST", "/v1/facebook/adLibrary/search/ads", {"query": '</span><img src=x onerror=alert(1)>'})
    for snippets in (GET, POST, hostile):
        for language, code in snippets.items():
            marked = _node(f"S.highlight({json.dumps(code)}, {json.dumps(language)})")
            assert "<img" not in marked and "<api_token>" not in marked and "&lt;api_token&gt;" in marked
            assert set(re.findall(r"<(/?\w+)", marked)) <= {"span", "/span"}
            assert html.unescape(re.sub(r"<[^>]+>", "", marked)) == code
    curl = _node(f"S.highlight({json.dumps(POST['curl'])}, 'curl')")
    assert '<span class="tok-keyword">--data</span> <span class="tok-string">&#39;{\n' in curl  # one string, all lines
    php = _node(f"S.highlight({json.dumps(POST['php'])}, 'php')")
    assert '<span class="tok-variable">$payload</span>' in php and '<span class="tok-constant">CURLOPT_POST</span>' in php


def test_json_is_indented_like_json_stringify_without_losing_digits():
    operations = json.loads((ROOT / "socialscrape" / "data" / "doc_examples.json").read_text())["operations"]
    compact = {f"{key} {status}": json.dumps(body, separators=(",", ":"), ensure_ascii=False)
               for key, entry in operations.items() for status, body in entry["responses"].items()}
    formatted = _node("Object.fromEntries(Object.entries(JSON.parse(input)).map(([k, v]) => [k, S.formatJson(v)]))",
                      json.dumps(compact))
    for key, text in compact.items():
        assert formatted[key] == json.dumps(json.loads(text), indent=2, ensure_ascii=False), key
    raw = '{"id":6689804660171082501,"a":[],"o":{},"s":"}\\"]","n":-1.5e+300}'
    marked = _node(f"S.formatJson({json.dumps(raw)}, {{html: true}})")
    assert '<span class="tok-key">&quot;id&quot;</span>: <span class="tok-number">6689804660171082501</span>' in marked
    assert '"a": [],\n  "o": {},' in html.unescape(re.sub(r"<[^>]+>", "", marked))


def test_the_biggest_documented_sample_formats_quickly():
    operations = json.loads((ROOT / "socialscrape" / "data" / "doc_examples.json").read_text())["operations"]
    biggest = max((json.dumps(body, separators=(",", ":")) for entry in operations.values()
                   for body in entry["responses"].values()), key=len)
    elapsed = _node("(() => { const t = Date.now(); S.formatJson(input, {html: true}); return Date.now() - t; })()",
                    biggest)
    assert len(biggest) > 100_000 and elapsed < 500


def test_members_read_the_examples_envelope_in_order(client):
    text = client.get("/meta/examples", params={"method": "POST", "path": "/v1/facebook/adLibrary/company/ads"}).text
    members = _node("S.members(input)", text)
    assert [key for key, _ in members] == ["success", "method", "path", "params", "responses"]
    responses = _node("S.members(JSON.parse(input))", json.dumps(dict(members)["responses"]))
    assert [key for key, _ in responses] == ["200", "200 (trimmed)", "404", "403", "500"]
    assert json.loads(dict(responses)["404"])["error"] == "company_not_found"


def test_copy_for_ai_writes_scrapecreators_prompt():
    example = '{"success":true,"id":6689804660171082501,"results":[{"a":1},{"a":2}]}'
    prompt = _node("S.prompt(JSON.parse(input))", json.dumps({
        "method": "GET", "url": f"{URL}/v1/facebook/adLibrary/search/ads", "description": "Search the Meta Ad Library.",
        "example": example, "params": [
            {"name": "query", "required": True, "description": "Keyword to search for."},
            {"name": "status", "required": False, "description": "Ad status.", "options": ["ALL", "ACTIVE"]}]}))
    url = f"{URL}/v1/facebook/adLibrary/search/ads"
    assert prompt == (
        f"I want to make an API call to {url}. \n\n"
        "  Please help me write code to make this API call and handle the response appropriately. Include error "
        "handling and best practices.\n\n"
        "  Here are the details:\n  \n"
        f"  Endpoint: GET {url}\n  \n"
        "  Description: Search the Meta Ad Library.\n  \n"
        "  Required Headers:\n  - x-api-key: Your API key\n  \n"
        "  Parameters:\n  - query (Required): Keyword to search for.\n  - status: Ad status.\n    Options: ALL, ACTIVE\n  \n"
        "  Example Response:\n  "
        + json.dumps(json.loads(example), indent=2)
        + "\n  \n  ")
    assert '"id": 6689804660171082501' in prompt and '"a": 2' in prompt  # the whole example, every digit
    bare = _node("S.prompt(JSON.parse(input))", json.dumps({"method": "GET", "url": URL, "params": []}))
    assert "Description:" not in bare and "Parameters:" not in bare and bare.endswith("  Example Response:\n  \n  \n  ")
