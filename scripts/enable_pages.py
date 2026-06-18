#!/usr/bin/env python3
"""Enable GitHub Pages for truth-anchor repo."""
import urllib.request, json, os

# Read from environment variable to avoid shell truncation
# Actually, let's read it from environment passed directly
token = os.environ.get("GH_TOKEN", "")
if not token:
    # Fallback: read from git credentials
    with open(os.path.expanduser("~/.git-credentials")) as f:
        line = f.readline().strip()
        token = line.split(":")[2].split("@")[0]

print(f"Token length: {len(token)}")

req = urllib.request.Request(
    "https://api.github.com/repos/PorkJelly77/truth-anchor/pages",
    data=json.dumps({
        "source": {
            "branch": "main",
            "path": "/"
        }
    }).encode(),
    headers={
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "User-Agent": "TruthAnchor",
        "Accept": "application/vnd.github+json"
    }
)
try:
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    print("Pages created! URL:", data.get("html_url"))
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"HTTP {e.code}: {body[:800]}")
