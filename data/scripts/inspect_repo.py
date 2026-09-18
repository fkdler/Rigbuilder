#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inspect_repo.py —— 打印 HF 仓库里所有 .gguf 文件的 路径+字节（排查多命中问题）
用法：python data/scripts/inspect_repo.py bartowski/gemma-2-9b-it-GGUF
      python data/scripts/inspect_repo.py lmstudio-community/Llama-3.3-70B-Instruct-GGUF
"""
import json
import os
import sys
import urllib.request

repo = sys.argv[1] if len(sys.argv) > 1 else "bartowski/gemma-2-9b-it-GGUF"
endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
url = f"{endpoint}/api/models/{repo}/tree/main"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
token = os.environ.get("HF_TOKEN")
if token:
    req.add_header("Authorization", f"Bearer {token}")
data = json.load(urllib.request.urlopen(req, timeout=60))
for e in data:
    if e.get("type") == "file" and e.get("path", "").lower().endswith(".gguf"):
        size = e.get("size")
        print(f"{e['path']}  {size if size is not None else '?'}  ({round((size or 0)/1048576)} MiB)")
