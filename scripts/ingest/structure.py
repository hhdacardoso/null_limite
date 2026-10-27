"""Turn anonymized resume text into structured profile JSON using Groq's free tier.

Only text produced by anonymize.py is sent. Picks a balanced subset (PM / BA / Dev,
favouring certifications and energy-sector experience) to fit the free daily token
budget, spreads requests across several models and respects their rate limits.
Already-structured profiles are skipped, so the script can be re-run after a limit hit.

Usage: python scripts/ingest/structure.py [--anon data/anon] [--out data/profiles] [--limit 100]
Needs GROQ_API_KEY in the environment or .env.
"""
import argparse
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Models with strict JSON-schema support on Groq; each has its own daily token quota
MODELS = {
    "openai/gpt-oss-120b": {"reasoning_effort": "low"},
    "openai/gpt-oss-20b": {"reasoning_effort": "low"},
    "qwen/qwen3.8-27b": {"reasoning_effort": "none"},
}
MAX_WORDS = 2500  # keeps each request well under the 8K tokens/minute limit
MAX_WAIT_SECONDS = 300  # a longer retry-after means the daily quota is gone

SECTORS = ["energia", "financeiro", "seguros", "saude", "governo", "varejo", "telecom",
           "tecnologia", "industria", "logistica", "educacao", "outros"]
EDUCATION_LEVELS = ["technical", "bachelor", "specialization", "mba", "master", "doctorate"]

def nullable(kind):
    return {"type": [kind, "null"]}

PROFILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["job_title", "years_experience", "certifications", "education", "projects"],
    "properties": {
        "job_title": {"type": "string"},
        "years_experience": nullable("integer"),
        "certifications": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "issuer", "issue_year"],
                "properties": {"name": {"type": "string"}, "issuer": nullable("string"), "issue_year": nullable("integer")},
            },
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["level", "course", "institution", "completion_year"],
                "properties": {
                    "level": {"type": "string", "enum": EDUCATION_LEVELS},
                    "course": {"type": "string"},
                    "institution": nullable("string"),
                    "completion_year": nullable("integer"),
                },
            },
        },
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "client", "sector", "role", "description", "start", "end"],
                "properties": {
                    "name": {"type": "string"},
                    "client": nullable("string"),
                    "sector": {"type": "string", "enum": SECTORS},
                    "role": nullable("string"),
                    "description": nullable("string"),
                    "start": nullable("string"),
                    "end": nullable("string"),
                },
            },
        },
    },
}

SYSTEM_PROMPT = f"""Você extrai dados estruturados de currículos (em inglês) para uma consultoria brasileira.
Regras:
- Use somente o que está escrito no currículo. Nunca invente certificações, cursos, clientes ou datas; na dúvida use null ou omita.
- job_title: cargo atual/principal, em português (ex.: "Analista de Negócios Sênior", "Gerente de Projetos", "Desenvolvedor Java").
- years_experience: anos totais de experiência declarados no resumo; null se não houver.
- certifications: apenas certificações profissionais (PMP, CSM, ITIL, Six Sigma, AWS...), não tecnologias.
- education.level: um de {EDUCATION_LEVELS}. Bachelor/B.Tech/B.E. = bachelor; Master/M.S./M.Tech = master; MBA = mba; pós-graduação/especialização = specialization.
- projects: no máximo 6, os mais recentes. client = empresa cliente/empregadora. sector = um de {SECTORS} conforme o cliente/negócio
  (bancos/cartões = financeiro; seguradoras = seguros; óleo/gás/utilities/power = energia; órgãos públicos = governo).
  role em português. description: uma frase curta em português. start/end no formato "YYYY-MM"; end null se for o emprego atual.
"""

ROLE_KEYWORDS = {
    "pm": r"project manager|program manager|\bpmo\b|delivery manager|scrum master",
    "ba": r"business analyst|business system analyst|\bbsa\b|requirements gathering",
    "dev": r"\bdeveloper\b|\bj2ee\b|spring|hibernate|programmer",
}
QUOTAS = {"ba": 40, "pm": 30, "dev": 30}
CERT_HINT = r"\b(pmp|csm|itil|six sigma|capm|cbap|safe|pmi-acp|aws certified|prince2)\b"
ENERGY_HINT = r"\b(energy|oil|gas|utilit(y|ies)|power|petroleum|electric)\b"


def select_profiles(anon_dir: Path, limit: int) -> list[Path]:
    """Balanced subset: role quotas, then prefer certified / energy profiles of reasonable length."""
    buckets = {role: [] for role in QUOTAS}
    for path in sorted(anon_dir.glob("p*.txt")):
        text = path.read_text(encoding="utf8")
        words = len(text.split())
        if words < 500:
            continue  # forms and stubs, not real resumes
        head = text[:2000].lower()
        role = max(ROLE_KEYWORDS, key=lambda r: len(re.findall(ROLE_KEYWORDS[r], head)))
        score = 3 * bool(re.search(CERT_HINT, text, re.I)) + 3 * bool(re.search(ENERGY_HINT, text, re.I))
        score -= max(0, words - MAX_WORDS) / 1000
        buckets[role].append((score, path))

    scale = limit / sum(QUOTAS.values())
    chosen = []
    for role, quota in QUOTAS.items():
        ranked = [p for _, p in sorted(buckets[role], key=lambda item: -item[0])]
        chosen += ranked[: round(quota * scale)]
    leftovers = sorted({p for b in buckets.values() for _, p in b} - set(chosen))
    return sorted(chosen + leftovers[: max(0, limit - len(chosen))])


def call_groq(api_key: str, model: str, text: str) -> tuple[dict, dict]:
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "profile", "strict": True, "schema": PROFILE_SCHEMA}},
        **MODELS[model],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "null-limite-ingest"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
        return json.loads(payload["choices"][0]["message"]["content"]), dict(response.headers)


def parse_seconds(value: str | None) -> float:
    """Groq reset headers look like '7.66s', '2m59.5s' or '1h2m3s'."""
    if not value:
        return 0.0
    total = 0.0
    for amount, unit in re.findall(r"([\d.]+)(ms|h|m|s)", value):
        total += float(amount) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total


def worker(model: str, api_key: str, jobs: queue.Queue, out_dir: Path, stats: dict, lock: threading.Lock):
    while True:
        try:
            path = jobs.get_nowait()
        except queue.Empty:
            return
        text = path.read_text(encoding="utf8")
        full_name = text.splitlines()[0].removeprefix("Name: ").strip()
        prompt_text = " ".join(text.split(" ")[:MAX_WORDS])
        try:
            profile, headers = call_groq(api_key, model, prompt_text)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            jobs.put(path)  # give it back to whichever model is still available
            if error.code == 429:
                wait = float(error.headers.get("retry-after") or 10)
                if wait > MAX_WAIT_SECONDS:
                    print(f"[{model}] daily quota exhausted, stopping", file=sys.stderr)
                    return
                time.sleep(wait)
                continue
            if error.code == 400 and "reasoning" in detail and MODELS[model]:
                print(f"[{model}] reasoning_effort not accepted, retrying without it", file=sys.stderr)
                MODELS[model] = {}
                continue
            print(f"[{model}] {path.stem}: HTTP {error.code} {detail}", file=sys.stderr)
            with lock:
                stats["errors"] += 1
                if stats["errors"] > 10:
                    return
            continue
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, TimeoutError) as error:
            print(f"[{model}] {path.stem}: {error}", file=sys.stderr)
            jobs.put(path)
            with lock:
                stats["errors"] += 1
                if stats["errors"] > 10:
                    return
            continue

        record = {"id": path.stem, "full_name": full_name, "source_model": model, **profile}
        (out_dir / f"{path.stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        with lock:
            stats["done"] += 1
            print(f"[{model}] {path.stem} ok ({stats['done']}/{stats['total']})")

        # Pace by the per-minute token window: wait for reset if the next request may not fit
        remaining = int(headers.get("x-ratelimit-remaining-tokens", "100000"))
        if remaining < 6000:
            time.sleep(parse_seconds(headers.get("x-ratelimit-reset-tokens")) + 0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anon", type=Path, default=Path("data/anon"))
    parser.add_argument("--out", type=Path, default=Path("data/profiles"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        sys.exit("GROQ_API_KEY not set (add it to .env)")

    args.out.mkdir(parents=True, exist_ok=True)
    selected = select_profiles(args.anon, args.limit)
    pending = [p for p in selected if not (args.out / f"{p.stem}.json").exists()]
    print(f"{len(selected)} selected, {len(pending)} pending")

    jobs = queue.Queue()
    for path in pending:
        jobs.put(path)
    stats, lock = {"done": 0, "errors": 0, "total": len(pending)}, threading.Lock()
    threads = [threading.Thread(target=worker, args=(m, api_key, jobs, args.out, stats, lock)) for m in args.models]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"done: {stats['done']}, still pending: {jobs.qsize()}, errors: {stats['errors']}")


if __name__ == "__main__":
    main()
