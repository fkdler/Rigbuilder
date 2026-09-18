#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hf_fill.py —— A 类字段自动化（Hugging Face 侧，需联网）

作用（对 data/v3/drafts/rigbuilder-pending-2026-09-03/models/*.json）：
  A. 全自动：variants[].file_size_bytes
     - 用 HF tree API 读取仓库文件树（仅元数据，不下载模型），按 quantization_method
       匹配 .gguf（同量化多分片自动求和，排除 imatrix），写回精确字节。
  B. 半自动（默认只出报告，--apply-params 才落库）：
     - 抓模型卡 README(config) 提取候选：
       total_parameters（如 "8.03B" -> 8030000000）、context_length_tokens
       （README 的 K 值 / config.json max_position_embeddings）。

用法（有网环境）：
  python data/scripts/hf_fill.py --report          # 只打印候选（默认）
  python data/scripts/hf_fill.py --apply-bytes     # 自动写 file_size_bytes
  python data/scripts/hf_fill.py --apply-bytes --apply-params   # 参数候选也写回
  # 国内网络：$env:HF_ENDPOINT="https://hf-mirror.com"；gated 卡设 $env:HF_TOKEN
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
MODELS = BASE / "data" / "v3" / "drafts" / "rigbuilder-pending-2026-09-03" / "models"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

def hf_get(path: str, token: str | None, retries: int = 2, timeout: int = 12) -> bytes:
    """HF API 读取。4xx（401/403/404）不重试——重试也没用，只会拖慢。"""
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    url = endpoint + path
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:   # 客户端错误：立即失败，不重试
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        if i < retries - 1:
            time.sleep(1.0 * (i + 1))
    raise last

def norm_q(q: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (q or "").lower())

def norm_name(p: str) -> str:
    """文件名（去 .gguf）归一化，如 'gemma-2-9b-it-Q4_K_M.gguf' -> 'gemma29bitq4km'。"""
    return norm_q(Path(p).name[:-5] if p.lower().endswith(".gguf") else Path(p).name)

def load_source_keys() -> dict:
    """source 的 url -> source_key，用于给回填字段挂证据"""
    src_dir = MODELS.parent / "sources"
    out = {}
    if not src_dir.exists():
        return out
    for f in src_dir.glob("*.json"):
        try:
            p = (json.loads(f.read_text(encoding="utf-8")).get("payload") or {})
        except Exception:  # noqa: BLE001
            continue
        if p.get("url"):
            out[p["url"].rstrip("/")] = p.get("source_key")
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="仅打印候选（默认）")
    ap.add_argument("--apply-bytes", action="store_true", help="写回 file_size_bytes")
    ap.add_argument("--apply-params", action="store_true", help="把可信的参数/上下文候选写回")
    ap.add_argument("--refresh-params", action="store_true",
                    help="对已有值也拉取官方值比对（HF API safetensors.total 精确参数量 + config.json 上下文），可校正近似值")
    ap.add_argument("--force", action="store_true", help="已填的变体字节也重新拉取")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个模型（试跑用）")
    args = ap.parse_args()
    token = os.environ.get("HF_TOKEN")
    if token:
        token = token.strip().strip('"').strip("'")
        if not token:
            token = None
        elif not token.isascii():
            print("[警告] HF_TOKEN 含非 ASCII 字符（很可能是占位符没替换，如 hf_你的token）→ 已忽略。")
            print("       公开模型不需要 token；需要 gated 模型时才设真实 token：")
            print('       $env:HF_TOKEN="hf_xxxxxxxxxxxxxxxx"')
            token = None
    src_by_url = load_source_keys()

    report = []
    diffs = []
    done = skipped = failed = 0
    consec_fail = 0
    mfiles = sorted(MODELS.glob("*.json"))
    if args.limit:
        mfiles = mfiles[:args.limit]
    total = len(mfiles)
    print(f"[开始] 待处理模型 {total} 个 | 参数刷新={'开' if args.refresh_params else '关'}"
          f" | 字节重拉={'开' if args.force else '跳过已填'}", flush=True)
    for idx, mf in enumerate(mfiles, 1):
        b = json.loads(mf.read_text(encoding="utf-8"))
        pay = b.get("payload") or {}
        row = {"model": mf.name, "file_sizes": [], "params": None, "context": None, "notes": []}
        print(f"[{idx}/{total}] {mf.stem}", flush=True)

        # ---- A. variants file_size_bytes ----
        changed = False
        for v in pay.get("variants") or []:
            m = re.match(r"https?://huggingface\.co/([^/]+/[^/]+)/?$", v.get("repository_url") or "")
            if not m:
                row["notes"].append("非HF仓库跳过")
                continue
            q = norm_q(v.get("quantization_method"))
            if v.get("file_size_bytes") and not args.force:
                # 已有真实字节 → 跳过 tree API 请求（省时间的主要来源）
                row["file_sizes"].append((v.get("quantization_method"), 0, v["file_size_bytes"]))
                continue
            try:
                # recursive=true 必需：许多 GGUF 仓把各量化放在子目录（如 DeepSeek-R1-Q8_0/xxx-00001-of-00015.gguf）
                tree = json.loads(hf_get(f"/api/models/{m.group(1)}/tree/main?recursive=true", token).decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                failed += 1
                row["notes"].append(f"API失败 {m.group(1)}: {type(e).__name__}")
                continue
            hits_all = [e for e in tree
                        if e.get("type") == "file" and e.get("path", "").lower().endswith(".gguf")
                        and "imatrix" not in e["path"].lower()]
            # 精确优先：文件名（去扩展名后归一化）以量化名结尾 → 完整单文件
            norm_qn = q
            singles = [e for e in hits_all if norm_q(norm_name(e["path"])).endswith(norm_qn)]
            # 回退：单文件不存在时，对 "-NNNNN-of-NNNNN" 分片求和
            shards = [e for e in hits_all
                      if norm_qn in norm_q(norm_name(e["path"]))
                      and re.search(r"-\d{5}-of-\d{5}\.gguf$", e["path"], re.I)]
            chosen = singles if singles else shards
            hits = chosen
            total = sum(int(e.get("size") or 0) for e in hits)
            row["file_sizes"].append((v.get("quantization_method"), len(hits), total))
            if args.apply_bytes and total > 0 and v.get("file_size_bytes") != total:
                v["file_size_bytes"] = total
                changed = True
            done += 1

        # ---- B. 参数/上下文候选（HF API 精确值 > README > config）----
        card = (pay.get("official_model_card_url") or pay.get("official_repo_url")) or ""
        repo_m = re.match(r"https?://huggingface\.co/([^/]+/[^/]+)/?$", card)
        need = args.refresh_params or pay.get("total_parameters") is None or pay.get("context_length_tokens") is None
        if repo_m and need:
            repo = repo_m.group(1)
            row["repo"] = repo
            try:
                # 1) 精确参数量：HF API safetensors.total
                if args.refresh_params or pay.get("total_parameters") is None:
                    try:
                        info = json.loads(hf_get(f"/api/models/{repo}", token).decode("utf-8"))
                        st = (info.get("safetensors") or {}).get("total")
                        if isinstance(st, int) and st > 0:
                            row["params"] = st
                            row["params_src"] = "HF API safetensors.total（精确）"
                            row["notes"].append(f"HF API safetensors.total={st}")
                    except Exception as e:  # noqa: BLE001
                        row["notes"].append(f"API /api/models 失败: {type(e).__name__}")
                # 2) README 参数字样（回退）
                readme = ""
                try:
                    readme = hf_get(f"/{repo}/raw/main/README.md", token).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
                if row["params"] is None and readme:
                    # 支持 "8.03B parameters" / "82M parameters" / "参数: 0.5B" / "568 million parameters"
                    pm = re.search(r"(\d+(?:\.\d+)?)\s*([BM])\s*[Pp]arameters?", readme)
                    if not pm:
                        pm = re.search(r"[Pp]arameters?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*([BM])", readme)
                    if not pm:
                        pm = re.search(r"(?:参数量|参数)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*([BM])", readme)
                    if not pm:
                        pm = re.search(r"(\d+(?:\.\d+)?)\s*(?:million|[Mm]\b)\s*[Pp]arameters?", readme)
                        if pm:
                            row["params"] = int(round(float(pm.group(1)) * 1e6))
                            row["params_src"] = "README: " + pm.group(0)
                    elif pm:
                        mult = 1e9 if pm.group(2) == "B" else 1e6
                        row["params"] = int(round(float(pm.group(1)) * mult))
                        row["params_src"] = "README: " + pm.group(0)
                    if row["params"]:
                        row["notes"].append(row["params_src"])
                # 3) 上下文：config.json max_position_embeddings
                cfg = None
                try:
                    cfg = json.loads(hf_get(f"/{repo}/resolve/main/config.json", token).decode("utf-8"))
                except Exception:  # noqa: BLE001
                    cfg = None
                ctx_cfg = (cfg or {}).get("max_position_embeddings")
                if isinstance(ctx_cfg, int):
                    row["context"] = ctx_cfg
                    row["notes"].append("来源 config.json max_position_embeddings")
                elif readme:
                    cm = re.search(r"(?:context|上下文)[^\d]{0,20}(\d{2,6})\s*K", readme, re.I)
                    if cm:
                        row["context"] = int(cm.group(1)) * 1000
                        row["notes"].append(f"README 上下文字样: {cm.group(0)}")
            except Exception as e:  # noqa: BLE001
                row["notes"].append(f"卡片读取失败: {type(e).__name__}")

        # ---- 写回 ----
        if args.apply_bytes and changed:
            changed = False
        wrote = False
        if args.apply_params:
            card_url = (pay.get("official_model_card_url") or pay.get("official_repo_url") or "").rstrip("/")
            skey = src_by_url.get(card_url)
            evs = pay.setdefault("evidence", [])
            for field_key, official, src_desc in (("model.total_parameters", row.get("params"), row.get("params_src")),
                                                  ("model.context_length_tokens", row.get("context"), "config.json max_position_embeddings")):
                if not official:
                    continue
                cur = pay.get("total_parameters" if field_key.endswith("parameters") else "context_length_tokens")
                if cur == official:
                    continue
                if cur is not None and not args.refresh_params:
                    continue  # 不覆盖已有值（需显式 --refresh-params）
                if cur is not None and args.refresh_params:
                    diffs.append({"model": mf.name, "field": field_key, "old": cur, "official": official, "src": src_desc})
                if field_key.endswith("parameters"):
                    pay["total_parameters"] = official
                else:
                    pay["context_length_tokens"] = official
                if skey:
                    existing = next((e for e in evs if e.get("field_key") == field_key), None)
                    note = "由公开知识值校正为官方值（原值 %s）" % cur if cur is not None else "由官方 config/API 回填"
                    claim = {
                        "source_key": skey, "field_key": field_key, "normalized_value": official,
                        "provenance_key": "official", "raw_excerpt": f"{src_desc}: {official}",
                        "notes": note, "review_status": "pending", "reviewed_at": None,
                    }
                    if existing:
                        existing.update(claim)
                    else:
                        evs.append(claim)
                wrote = True
            if wrote:
                b["review_notes"] = (b.get("review_notes") or []) + [
                    "hf_fill --refresh-params：参数量取 HF API safetensors.total（精确），上下文取 config.json max_position_embeddings；已挂 evidence（pending）。"
                ]
        if (args.apply_bytes and row["file_sizes"]) or (args.apply_params and wrote):
            mf.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            skipped += 1  # 写回计数用
        report.append(row)
        # 网络熔断：连续失败过多时提前退出，避免逐个长时间重试
        if row["notes"] and any(("API失败" in n) or ("卡片读取失败" in n) or ("API /api/models 失败" in n) for n in row["notes"]) and not (row.get("params") or row.get("context")):
            consec_fail += 1
        else:
            consec_fail = 0
        if consec_fail >= 5:
            print(f"\n[熔断] 连续 {consec_fail} 个模型访问失败，疑似网络/镜像不可用，提前退出。")
            print("请检查：$env:HF_ENDPOINT=\"https://hf-mirror.com\"（或代理），必要时设 $env:HF_TOKEN")
            break

    print("processed:", done, "models:", len(report), "failures:", failed, "written:", skipped)
    for r in report:
        extra = ""
        if r["params"] or r["context"]:
            extra = f" | 官方 params={r['params']} ctx={r['context']}"
        fs = "; ".join(f"{q}:{n}files:{t}b" for q, n, t in r["file_sizes"])
        print(f"- {r['model']}{extra}{' | ' + fs if fs else ''}")
        if r["notes"]:
            print("    notes:", "; ".join(r["notes"]))
    if diffs:
        out = BASE / "data" / "raw" / "hf-param-diffs.json"
        out.write_text(json.dumps(diffs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n=== 与现有值不一致 {len(diffs)} 条（已按官方值校正，明细 {out}）===")
        for d in diffs[:20]:
            print(f"  {d['model']}: {d['field']} {d['old']} -> {d['official']}  [{d['src']}]")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
