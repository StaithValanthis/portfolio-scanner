from __future__ import annotations
import os, json, time, pathlib, hashlib
CACHE_DIR = os.getenv("CACHE_DIR", "/app/.cache")
TTL_MIN = int(os.getenv("CACHE_TTL_MIN","1440"))
pathlib.Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
def _key_to_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{hashlib.sha256(key.encode()).hexdigest()}.json")
def get(key: str):
    p = _key_to_path(key)
    if not os.path.exists(p): return None
    try:
        with open(p,"r",encoding="utf-8") as f: blob = json.load(f)
        if (time.time()-blob.get("ts",0)) > TTL_MIN*60: return None
        return blob.get("data")
    except Exception: return None
def set(key: str, data):
    try:
        with open(_key_to_path(key),"w",encoding="utf-8") as f: json.dump({"ts": time.time(), "data": data}, f)
    except Exception: pass
