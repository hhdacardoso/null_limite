"""Extract text from .docx resumes and strip personal data (LGPD).

Runs fully offline. Each resume gets a sequential id and a fictitious Brazilian
name; the original-name mapping is never written anywhere, so the output can't be
traced back by itself. The run aborts without writing anything if the
verification step finds leftover contact data or original names.

Usage: python scripts/ingest/anonymize.py <dir with .docx> [--out data/anon]
"""
import argparse
import html
import random
import re
import shutil
import sys
import zipfile
from pathlib import Path

FIRST_NAMES = [
    "Ana", "Beatriz", "Camila", "Daniela", "Eduarda", "Fernanda", "Gabriela", "Helena", "Isabela", "Juliana",
    "Larissa", "Mariana", "Natália", "Patrícia", "Renata", "Sofia", "Tatiane", "Vanessa", "Yasmin", "Luana",
    "André", "Bruno", "Carlos", "Diego", "Eduardo", "Felipe", "Gustavo", "Henrique", "Igor", "João",
    "Lucas", "Marcelo", "Nicolas", "Otávio", "Paulo", "Rafael", "Samuel", "Thiago", "Vinícius", "Rodrigo",
]
LAST_NAMES = [
    "Almeida", "Barbosa", "Cardoso", "Carvalho", "Castro", "Costa", "Dias", "Duarte", "Fernandes", "Ferreira",
    "Gomes", "Lima", "Lopes", "Machado", "Martins", "Melo", "Mendes", "Moreira", "Nascimento", "Nunes",
    "Oliveira", "Pereira", "Pinto", "Ramos", "Ribeiro", "Rocha", "Santos", "Silva", "Soares", "Souza",
    "Teixeira", "Vieira", "Monteiro", "Freitas", "Araújo", "Batista", "Correia", "Moura", "Campos", "Reis",
]

# Words that show up in file names / header lines but are not part of a person's name
NOT_NAME = {
    "resume", "cv", "updated", "final", "new", "copy", "doc", "docx", "details", "employer", "profile",
    "sr", "senior", "jr", "junior", "lead", "java", "developer", "dev", "php", "net", "sql", "qa", "eda",
    "ba", "bsa", "business", "analyst", "analysis", "system", "systems", "data", "healthcare", "pm",
    "project", "program", "manager", "management", "scrum", "master", "pmp", "csm", "smc", "six", "sigma",
    "green", "belt", "itil", "mba", "feb", "jan", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
    "nov", "dec", "the", "and", "of", "for", "with", "consultant", "engineer", "architect", "full", "stack",
    "salesforce", "oracle", "sap", "agile", "summary", "professional", "objective", "contact", "phone",
    "email", "mobile", "cell", "name", "ab", "dt", "sr.", "technical", "software", "web", "ui", "etl",
}

SECTION_HEADING = re.compile(
    r"^\W*(professional\s+|career\s+|executive\s+|work\s+)?(summary|objective|profile|overview|experience|"
    r"technical\s+skills|skills|education|certifications?)\b",
    re.IGNORECASE,
)
REFERENCES_HEADING = re.compile(r"^\W*references?\W*$", re.IGNORECASE)

EMAIL = re.compile(r"[\w.%+-]+@[\w-]+(\.[\w-]+)+")
URL = re.compile(r"(https?://|www\.)\S+|\b\S*(linkedin|github|facebook|twitter)\.com\S*", re.IGNORECASE)
PHONE = re.compile(r"(\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Lines labelled with personal fields (form-style resumes) are dropped entirely
PERSONAL_LABEL = re.compile(
    r"\b(ssn|social security|date of birth|dob|d\.o\.b|birth|visa|work authori[sz]ation|h-?1-?b|green card|"
    r"citizenship|us citizen|ead|skype|address|current location|relocat\w*|year of entry|marital|"
    r"nationality|passport|contact number|best contact)\b",
    re.IGNORECASE,
)
# "Seattle, WA", "98101, Seattle, WA", "Plano Texas" style location lines in the header
LOCATION_LINE = re.compile(r"^[\w\s,.-]{0,40}\b([A-Z]{2}|Texas|California|New Jersey|Illinois)\b[\s\d,.-]*$")


def docx_text(path: Path) -> str:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf8")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", " ", xml)
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def name_tokens(words) -> set:
    tokens = set()
    for word in words:
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", word):
            if len(part) >= 3 and part.lower() not in NOT_NAME:
                tokens.add(part.lower())
    return tokens


def split_header(lines):
    """Header = lines before the first section heading or long sentence (name, contacts, location)."""
    for i, line in enumerate(lines[:10]):
        if SECTION_HEADING.match(line) or len(line) > 90:
            return lines[:i], lines[i:]
    return lines[:3], lines[3:]


def anonymize(text: str, file_stem: str, fake_first: str, fake_last: str) -> tuple[str, set]:
    lines = text.splitlines()
    header, body = split_header(lines)

    # Name candidates: file name plus short header lines without digits (the name line)
    tokens = name_tokens(re.split(r"[\s_.()-]+", file_stem))
    for line in header:
        if not re.search(r"\d|@", line) and len(line.split()) <= 6:
            tokens |= name_tokens(line.replace(",", " ").split())

    # Keep job-title-like header lines (e.g. "Sr. Java Developer"), drop name/contact/location lines
    kept_header = [
        line for line in header
        if not re.search(r"\d|@", line)
        and not name_tokens(line.replace(",", " ").split()) & tokens
        and not LOCATION_LINE.match(line)
        and not PERSONAL_LABEL.search(line)
    ]

    out = []
    for line in body:
        if REFERENCES_HEADING.match(line):
            break  # reference contacts are third-party personal data
        if PERSONAL_LABEL.search(line) and len(line) < 120:
            continue
        out.append(line)

    result = "\n".join([f"Name: {fake_first} {fake_last}"] + kept_header + out)
    result = re.sub(r"\d{11,}", "", result)  # docx field-code noise, not real numbers
    result = EMAIL.sub("[email removido]", result)
    result = URL.sub("[link removido]", result)
    result = SSN.sub("", result)
    result = PHONE.sub("[telefone removido]", result)

    ordered = sorted(tokens, key=len, reverse=True)
    for i, token in enumerate(ordered):
        replacement = fake_first if i == 0 else fake_last
        result = re.sub(rf"\b{re.escape(token)}\b", replacement, result, flags=re.IGNORECASE)
    # Header name line may still read "Name: X" correctly; restore it in case a token matched the fake name
    result = re.sub(r"^Name: .*$", f"Name: {fake_first} {fake_last}", result, count=1, flags=re.MULTILINE)
    return result, tokens


def leftovers(text: str, tokens: set, fake_tokens: set) -> list:
    problems = []
    for label, pattern in (("email", EMAIL), ("phone", PHONE), ("ssn", SSN), ("url", URL)):
        if pattern.search(text):
            problems.append(label)  # never echo the match itself: it is personal data
    if re.search(r"\b(date of birth|dob|ssn|h-?1-?b)\b", text, re.IGNORECASE):
        problems.append("personal label")
    for token in tokens - fake_tokens:
        if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):
            problems.append("original name")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/anon"))
    args = parser.parse_args()

    files = sorted(p for p in args.source.iterdir() if p.suffix.lower() == ".docx")
    rng = random.Random(42)
    names = rng.sample([(f, l) for f in FIRST_NAMES for l in LAST_NAMES], len(files))

    results, failures = {}, 0
    for i, (path, (first, last)) in enumerate(zip(files, names), start=1):
        text, tokens = anonymize(docx_text(path), path.stem, first, last)
        problems = leftovers(text.split("\n", 1)[1] if "\n" in text else "", tokens, {first.lower(), last.lower()})
        if problems:
            failures += 1
            # Report by id only: printing the file name would leak the original name
            print(f"p{i:03d}: {'; '.join(problems)}", file=sys.stderr)
        results[f"p{i:03d}"] = text

    if failures:
        sys.exit(f"Verification failed for {failures} resumes; nothing written.")

    shutil.rmtree(args.out, ignore_errors=True)
    args.out.mkdir(parents=True)
    for pid, text in results.items():
        (args.out / f"{pid}.txt").write_text(text + "\n", encoding="utf8")
    print(f"{len(results)} resumes anonymized into {args.out}/")


if __name__ == "__main__":
    main()
