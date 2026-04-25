"""Simple helper for manually testing summarizer.py with pasted HTML."""

from __future__ import annotations

import json

from summarizer import summarize_html


HTML_INPUT = """
<!-- Paste raw HTML here -->
<html>
  <head>
    <title>Example Page</title>
  </head>
  <body>
    <main>
      <h1>Example Page</h1>
      <p>Replace this sample HTML with the page markup you want to summarize.</p>
    </main>
  </body>
</html>
""".strip()


result = summarize_html(HTML_INPUT)
print(json.dumps(result, indent=2))
