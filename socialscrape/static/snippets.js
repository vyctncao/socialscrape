// Request snippets in ScrapeCreators' six layouts, its "Copy for AI" prompt, and a small highlighter for them and for
// JSON. The docs pages and the playground both build their code from here, so the two never drift. Every value that
// reaches HTML is escaped; the JSON formatter reads text, so IDs beyond 2^53 keep every digit.
const Snippets = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const languages = [['curl', 'cURL'], ['node', 'Node.js'], ['python', 'Python'], ['php', 'PHP'], ['go', 'Go'], ['java', 'Java']];
  const KEY = '<api_token>';

  // ---- Snippets ------------------------------------------------------------------------------------------------
  // A GET sends every non-empty param in the query string, a POST every one in its JSON body, as ScrapeCreators'
  // generators do (docs.scrapecreators.com's bundle). A quote is %27 so a value cannot end Node's quoted URL, and
  // "!" is %21 so an interactive shell's history expansion cannot rewrite the cURL's double-quoted one.
  function query(params) {
    const pairs = Object.entries(params).filter(([, value]) => value !== '' && value !== null && value !== undefined);
    return pairs.length ? `?${pairs.map(([name, value]) => `${name}=${encodeURIComponent(String(value)).replace(/'/g, '%27').replace(/!/g, '%21')}`).join('&')}` : '';
  }
  function get(url) {
    return {
      curl:`curl "${url}" \\\n  -H "x-api-key: ${KEY}"`,
      node:`import axios from 'axios';\n\nconst { data } = await axios.get(\n  '${url}',\n  {\n    headers: {\n      'x-api-key': '${KEY}'\n    }\n  }\n);`,
      python:`import requests\n\nurl = "${url}"\nheaders = {\n    "x-api-key": "${KEY}"\n}\n\nresponse = requests.get(url, headers=headers)\ndata = response.json()\nprint(data)`,
      php:`<?php\n\n$url = "${url}";\n\n$curl = curl_init();\n\ncurl_setopt_array($curl, [\n  CURLOPT_URL => $url,\n  CURLOPT_RETURNTRANSFER => true,\n  CURLOPT_HTTPHEADER => [\n    "x-api-key: ${KEY}"\n  ]\n]);\n\n$response = curl_exec($curl);\ncurl_close($curl);\n\n$data = json_decode($response, true);\nprint_r($data);`,
      go:`package main\n\nimport (\n  "encoding/json"\n  "fmt"\n  "io"\n  "net/http"\n)\n\nfunc main() {\n  url := "${url}"\n  \n  req, err := http.NewRequest("GET", url, nil)\n  if err != nil {\n    panic(err)\n  }\n  \n  req.Header.Set("x-api-key", "${KEY}")\n  \n  client := &http.Client{}\n  resp, err := client.Do(req)\n  if err != nil {\n    panic(err)\n  }\n  defer resp.Body.Close()\n  \n  body, err := io.ReadAll(resp.Body)\n  if err != nil {\n    panic(err)\n  }\n  \n  var result map[string]interface{}\n  json.Unmarshal(body, &result)\n  fmt.Printf("%+v", result)\n}`,
      java:`import java.net.http.HttpClient;\nimport java.net.http.HttpRequest;\nimport java.net.http.HttpResponse;\nimport java.net.URI;\nimport java.net.http.HttpResponse.BodyHandlers;\n\npublic class Main {\n    public static void main(String[] args) throws Exception {\n        HttpClient client = HttpClient.newHttpClient();\n        \n        HttpRequest request = HttpRequest.newBuilder()\n            .uri(URI.create("${url}"))\n            .header("x-api-key", "${KEY}")\n            .GET()\n            .build();\n        \n        HttpResponse<String> response = client.send(request, BodyHandlers.ofString());\n        \n        System.out.println(response.body());\n    }\n}`
    };
  }
  // The body is JSON.stringify(params, null, 2) in each language's literal. Where a typed value could end that literal
  // (a quote in the shell's '…', ''' in Python's r'''…''', a backtick in Go's `…`), it is escaped or \u-encoded.
  function post(url, body) {
    const shell = body.replace(/'/g, `'\\''`), python = body.includes("'''") ? body.replace(/'/g, '\\u0027') : body;
    return {
      curl:`curl --request POST \\\n  --url '${url}' \\\n  --header 'Content-Type: application/json' \\\n  --header 'x-api-key: ${KEY}' \\\n  --data '${shell}'`,
      node:`const response = await fetch('${url}', {\n  method: 'POST',\n  headers: {\n    'Content-Type': 'application/json',\n    'x-api-key': '${KEY}'\n  },\n  body: JSON.stringify(${body})\n});\n\nconst data = await response.json();\nconsole.log(data);`,
      python:`import json\nimport requests\n\nurl = "${url}"\nheaders = {\n    "Content-Type": "application/json",\n    "x-api-key": "${KEY}"\n}\npayload = json.loads(r'''${python}''')\n\nresponse = requests.post(url, headers=headers, json=payload)\nprint(response.json())`,
      php:`<?php\n\n$url = '${url}';\n$payload = <<<'JSON'\n${body}\nJSON;\n\n$curl = curl_init($url);\ncurl_setopt_array($curl, [\n    CURLOPT_POST => true,\n    CURLOPT_POSTFIELDS => $payload,\n    CURLOPT_RETURNTRANSFER => true,\n    CURLOPT_HTTPHEADER => [\n        'Content-Type: application/json',\n        'x-api-key: ${KEY}'\n    ]\n]);\n\n$response = curl_exec($curl);\ncurl_close($curl);\necho $response;`,
      go:`package main\n\nimport (\n  "fmt"\n  "io"\n  "net/http"\n  "strings"\n)\n\nfunc main() {\n  payload := strings.NewReader(\`${body.replace(/`/g, '\\u0060')}\`)\n  req, _ := http.NewRequest("POST", "${url}", payload)\n  req.Header.Add("Content-Type", "application/json")\n  req.Header.Add("x-api-key", "${KEY}")\n\n  response, _ := http.DefaultClient.Do(req)\n  defer response.Body.Close()\n  data, _ := io.ReadAll(response.Body)\n  fmt.Println(string(data))\n}`,
      java:`import java.net.URI;\nimport java.net.http.HttpClient;\nimport java.net.http.HttpRequest;\nimport java.net.http.HttpResponse;\n\npublic class Main {\n  public static void main(String[] args) throws Exception {\n    String requestBody = ${JSON.stringify(body)};\n    HttpRequest request = HttpRequest.newBuilder()\n        .uri(URI.create("${url}"))\n        .header("Content-Type", "application/json")\n        .header("x-api-key", "${KEY}")\n        .POST(HttpRequest.BodyPublishers.ofString(requestBody))\n        .build();\n\n    HttpResponse<String> response = HttpClient.newHttpClient().send(\n        request,\n        HttpResponse.BodyHandlers.ofString()\n    );\n    System.out.println(response.body());\n  }\n}`
    };
  }
  // {curl, node, python, php, go, java} for one request; `params` is the query (GET) or the typed body (POST).
  function build({method, url, params = {}}) {
    if (method !== 'POST') return get(url + query(params));
    const body = Object.fromEntries(Object.entries(params).filter(([, value]) => value !== '' && value !== undefined));
    return post(url, JSON.stringify(body, null, 2));
  }

  // ---- Highlighting --------------------------------------------------------------------------------------------
  const KEYWORDS = {
    curl:[], node:['import', 'from', 'const', 'await', 'new', 'return'], python:['import', 'print', 'def', 'return'],
    php:['echo', 'new', 'return'], go:['package', 'import', 'func', 'if', 'defer', 'var', 'map', 'interface', 'return'],
    java:['import', 'public', 'class', 'static', 'void', 'throws', 'new', 'return']
  };
  const LITERALS = new Set(['true', 'false', 'null', 'nil', 'None', 'True', 'False']);
  // One pass of alternatives, first match wins: strings (Python r'''…''', Go `…`, PHP nowdoc, quoted; the shell's '…'
  // has no escapes and may span lines, as --data does), the PHP tag and variables, shell flags, numbers, words (a call
  // when "(" follows), operators and punctuation. Anything else passes through as escaped text.
  const STRINGS = String.raw`r?'''[\s\S]*?'''|` + '`[^`]*`' + String.raw`|<<<'(\w+)'\n[\s\S]*?\n\2\b|"(?:\\.|[^"\\\n])*"`;
  const tokens = single => new RegExp(`(${STRINGS}|${single})` + String.raw`|(<\?php|\$\w+)|((?<=\s)--?[a-zA-Z][\w-]*)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)(?=(\s*\()?)|(=>|:=|!=|[=&<>+*]|[{}()[\];,.:])`, 'g');
  const TOKENS = {code:tokens(String.raw`'(?:\\.|[^'\\\n])*'`), shell:tokens(`'[^']*'`)};
  function highlight(code, language) {
    const keywords = new Set(KEYWORDS[language] || []), pattern = TOKENS[language === 'curl' ? 'shell' : 'code'], out = [];
    let at = 0;
    const span = (kind, text) => out.push(`<span class="tok-${kind}">${esc(text)}</span>`);
    pattern.lastIndex = 0;
    for (let match; (match = pattern.exec(code));) {
      const token = match[0];
      if (match.index > at) out.push(esc(code.slice(at, match.index)));
      if (match[1]) span('string', token);
      else if (match[3]) span(token[0] === '$' ? 'variable' : 'keyword', token);
      else if (match[4]) span('keyword', token);
      else if (match[5]) span('number', token);
      else if (match[6]) {
        if (keywords.has(token)) span('keyword', token);
        else if (LITERALS.has(token)) span('literal', token);
        else if (match[7] || (language === 'curl' && token === 'curl')) span('function', token);
        else if (/^[A-Z][A-Z0-9]*_[A-Z0-9_]+$/.test(token)) span('constant', token);
        else out.push(esc(token));
      } else span('punct', token);
      at = pattern.lastIndex;
    }
    out.push(esc(code.slice(at)));
    return out.join('');
  }

  // ---- JSON ----------------------------------------------------------------------------------------------------
  const JSON_TOKENS = /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}[\],:]/g;
  const CLOSES_NEXT = /\s*[}\]]/y, KEY_NEXT = /\s*:/y;
  // JSON text indented as JSON.stringify(value, null, 2) would, without parsing it. `html` returns escaped markup
  // with token classes.
  function formatJson(json, {html = false} = {}) {
    const out = [], open = [];  // one entry per open container: true for an array
    const put = (kind, text) => out.push(html ? `<span class="tok-${kind}">${esc(text)}</span>` : text);
    const pad = () => `\n${'  '.repeat(open.length)}`;
    JSON_TOKENS.lastIndex = 0;
    for (let match; (match = JSON_TOKENS.exec(json));) {
      const token = match[0], c = token[0];
      if (c === '{' || c === '[') {
        CLOSES_NEXT.lastIndex = JSON_TOKENS.lastIndex;
        const empty = CLOSES_NEXT.exec(json);
        if (empty) { out.push(c + empty[0].trim()); JSON_TOKENS.lastIndex = CLOSES_NEXT.lastIndex; continue; }
        open.push(c === '['); out.push(c + pad());
      } else if (c === '}' || c === ']') { open.pop(); out.push(pad() + c); }
      else if (c === ',') out.push(`,${pad()}`);
      else if (c === ':') out.push(': ');
      else if (c === '"') { KEY_NEXT.lastIndex = JSON_TOKENS.lastIndex; put(KEY_NEXT.test(json) ? 'key' : 'string', token); }
      else put(c === 't' || c === 'f' || c === 'n' ? 'literal' : 'number', token);
    }
    return out.join('');
  }
  // The members of a JSON object's text as [key, value text] pairs, in order, without parsing the values.
  function members(json) {
    const pairs = [], token = /"(?:\\.|[^"\\])*"|[{}[\],:]/g;
    let depth = 0, key = null, start = 0;
    for (let match; (match = token.exec(json));) {
      const t = match[0];
      if (depth === 1 && (t === ',' || t === '}') && key !== null) { pairs.push([key, json.slice(start, match.index).trim()]); key = null; }
      if (t === '{' || t === '[') depth++;
      else if (t === '}' || t === ']') depth--;
      else if (depth === 1 && t[0] === '"' && key === null) key = JSON.parse(t);
      else if (depth === 1 && t === ':') start = token.lastIndex;
    }
    return pairs;
  }

  // ---- Copy for AI ---------------------------------------------------------------------------------------------
  // ScrapeCreators' prompt for an AI assistant (its bundle's Ki): the call, its description, the key header, each
  // field with its choices, and the whole 200 example. Its three template slips are not copied: every field line
  // keeps the indent, "(Required)" follows the name after one space, and the choices start a real line.
  function prompt({method, url, description, params, example}) {
    const fields = params.map(param => `- ${param.name}${param.required ? ' (Required)' : ''}: ${param.description || ''}${param.options?.length ? `\n    Options: ${param.options.join(', ')}` : ''}`);
    return `I want to make an API call to ${url}. \n\n  Please help me write code to make this API call and handle the response appropriately. Include error handling and best practices.\n\n  Here are the details:\n  \n  Endpoint: ${method} ${url}\n  \n${description ? `  Description: ${description}\n  \n` : ''}  Required Headers:\n  - x-api-key: Your API key\n  \n  ${fields.length ? `Parameters:\n  ${fields.join('\n  ')}` : ''}\n  \n  Example Response:\n  ${example ? formatJson(example) : ''}\n  \n  `;
  }

  return {languages, build, highlight, formatJson, members, prompt};
})();
if (typeof module !== 'undefined') module.exports = Snippets;
