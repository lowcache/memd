"""Stateless recall: BM25 ranking over memory sections (active files and
archive), recomputed from the markdown on every query, so there is no index
to go stale."""

from collections import Counter
from math import log
from pathlib import Path
import re

from memd.config import MEMORY_FILES
from memd.memory import split_frontmatter, split_sections, section_date

TOKEN_RE = re.compile(r"[a-z0-9_][a-z0-9_.\-]*[a-z0-9_]")
HEADING_WEIGHT = 2
BM25_K1, BM25_B = 1.2, 0.75
STOPWORDS = frozenset("""
the and for are but not you all any can had her was one our out has have from this that
with they will would there their what about which when were been into than then them these
those because after before also just only some such very does did its should could while
where who how why more most other over under again here each both few own same too let
""".split())


def tokenize(text):
    tokens = []
    for token in TOKEN_RE.findall(text.lower()):
        tokens.append(token)
        if "." in token or "-" in token:
            tokens.extend(part for part in re.split(r"[.\-]", token)
                          if len(part) >= 2)
    return tokens


def terms_from_text(text, limit=40):
    counts = Counter(token for token in TOKEN_RE.findall(text.lower())
                     if len(token) >= 3 and token not in STOPWORDS)
    return [token for token, _ in counts.most_common(limit)]


def load_sections(project_path, include_archive=True):
    memory = Path(project_path) / ".memory"
    if not memory.is_dir():
        return []
    paths = [(memory / name, name, False) for name in MEMORY_FILES
             if (memory / name).exists()]
    if include_archive:
        paths.extend((path, f"archive/{path.name}", True)
                     for path in sorted((memory / "archive").glob("*.md")))
    sections = []
    for path, name, archived in paths:
        _, body = split_frontmatter(path.read_text())
        for heading, body in split_sections(body):
            sections.append({"file": name, "heading": heading, "body": body,
                             "date": section_date(heading + "\n" + body),
                             "archived": archived})
    return sections


def rank(sections, query):
    """[(index, score)] for sections (dicts with "heading" and "body") scoring
    > 0 against query, best first, ties in section order."""
    terms = list(dict.fromkeys(token for token in tokenize(query)
                               if token not in STOPWORDS))
    if not terms:
        return []
    if not sections:
        return []
    documents = []
    for section in sections:
        counts = Counter(tokenize(section["body"]))
        counts.update({token: count * HEADING_WEIGHT for token, count in
                       Counter(tokenize(section["heading"])).items()})
        documents.append(counts)
    lengths = [sum(counts.values()) for counts in documents]
    avgdl = sum(lengths) / len(sections) or 1
    idfs = {}
    for term in terms:
        df = sum(term in counts for counts in documents)
        idfs[term] = log(1 + (len(sections) - df + 0.5) / (df + 0.5))
    hits = []
    for index, (counts, dl) in enumerate(zip(documents, lengths)):
        score = 0.0
        for term in terms:
            f = counts[term]
            if f:
                score += (idfs[term] * f * (BM25_K1 + 1)
                          / (f + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)))
        if score > 0:
            hits.append((index, score))
    hits.sort(key=lambda hit: hit[1], reverse=True)
    return hits


def search(project_path, query, limit=5, include_archive=True):
    sections = load_sections(project_path, include_archive=include_archive)
    return [{**sections[index], "score": score}
            for index, score in rank(sections, query)[:limit]]
