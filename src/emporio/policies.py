"""Policy knowledge base: the store manual (PDF) split into numbered sections + BM25 retrieval.

Why retrieval and not "paste the whole manual in the prompt"?
The manual is small (~3k tokens) so either would work today. Section-level retrieval was chosen
because (a) the model must *decide* to consult policy, which keeps data lookups and policy lookups
explicit and auditable (every answer can cite "§4.1"), (b) it scales if the manual grows, and
(c) it keeps the always-on prompt short. The PDF is parsed at start-up, so updating the manual
only requires replacing the file.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .data import DataError, normalize

_NOISE = re.compile(r"^(Empório da Música\s+Manual de Políticas e Procedimentos|Página\s+\d+)\s*$")
_H1 = re.compile(r"^(\d{1,2})\.\s+([^:]{3,70})$")  # "4. Política de Trocas e Devoluções" (no colon: not a list item)
_H2 = re.compile(r"^(\d{1,2}\.\d{1,2})\s+(\S.{2,70})$")  # "4.1 Direito de Arrependimento"

_STOPWORDS = set(
    "a o as os um uma uns umas de da do das dos em no na nos nas por para com sem e ou que se "
    "eu meu minha voces voce vcs ser ter tem tenho posso pode como qual quais quando onde ao aos "
    "mais muito ja nao sim ola oi".split()
)

# Query expansion: colloquial customer wording -> manual vocabulary.
_EXPANSIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"arrepend|desist|devolv|devoluc|voltar|reembols|estorn|cancelar? (a )?compra|quero meu dinheiro"),
     "devolucao arrependimento reembolso"),
    (re.compile(r"defeito|quebr|estrag|nao funciona|problema|danific|avaria"), "defeito troca garantia avaria"),
    (re.compile(r"troc"), "trocas preferencia"),
    (re.compile(r"horari|aberto|abre|fecha|funcionament|sabado|domingo|feriado|expediente"), "horario funcionamento"),
    (re.compile(r"endereco|onde fica|localiza|como chego|chegar"), "endereco rua cep localizacao"),
    (re.compile(r"telefone|whats|contato|e-?mail|ligar|falar com"), "telefone whatsapp email contato"),
    (re.compile(r"pagar|pagamento|parcel|cartao|credito|debito|boleto|pix|a vista|prestac"),
     "formas pagamento parcelamento"),
    (re.compile(r"frete|entrega|enviar|envio|prazo|correio|sedex|pac\b|jadlog|motoboy|transportadora"), "frete entregas prazo"),
    (re.compile(r"rastre|codigo de rastreio"), "rastreamento codigo"),
    (re.compile(r"promoc|desconto|black friday|oferta|liquidac|cupom"), "promocoes descontos"),
    (re.compile(r"lgpd|dados pessoais|privacidade|excluir meus dados|apagar meus dados"), "privacidade dados lgpd"),
    (re.compile(r"reclam|insatisf|atendimento ruim|absurdo|pessimo"), "reclamacao"),
    (re.compile(r"acessori|corda|palheta|cabo|case|pedal|amplificador"), "acessorios escopo"),
]


@dataclass(frozen=True)
class Section:
    section_id: str  # "4.1" (or "0" for the cover page)
    title: str
    path: str  # "4. Política de Trocas e Devoluções > 4.1 Direito de Arrependimento"
    text: str

    def as_dict(self) -> dict:
        return {"source": f"Manual de Políticas v2.1, seção {self.section_id}", "title": self.path, "text": self.text}


def _stem(token: str) -> str:
    """Crude Portuguese stemmer: strip a plural 's' and keep a 5-letter prefix."""
    if token.endswith("oes"):
        token = token[:-3] + "ao"
    elif len(token) > 3 and token.endswith("s"):
        token = token[:-1]
    return token[:5] if len(token) > 5 else token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", normalize(text)) if t not in _STOPWORDS and len(t) > 1]


def _clean_lines(raw: str) -> list[str]:
    lines = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.strip() or _NOISE.match(line.strip()):
            continue
        # table columns come out as runs of spaces in layout mode
        lines.append(re.sub(r"\s{2,}", " | ", line.strip()))
    return lines


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise DataError("pypdf is required to read the policy manual: pip install pypdf") from exc
    if not Path(pdf_path).is_file():
        raise DataError(f"Policy manual not found: {pdf_path}")
    reader = PdfReader(str(pdf_path))
    # "layout" mode keeps lines/table rows intact (the default mode emits one word per line here)
    return "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)


def parse_sections(raw_text: str) -> list[Section]:
    sections: list[Section] = []
    current_id, current_title, current_path, buf = "0", "Capa", "Capa e dados de contato", []
    h1_title = ""

    def flush():
        text = " ".join(buf).replace(" | ", " | ").strip()
        if text:
            sections.append(Section(current_id, current_title, current_path, text))

    for line in _clean_lines(raw_text):
        m2, m1 = _H2.match(line), _H1.match(line)
        if m2:
            flush()
            buf = []
            current_id, current_title = m2.group(1), m2.group(2).strip()
            current_path = f"{h1_title} > {m2.group(1)} {current_title}" if h1_title else f"{m2.group(1)} {current_title}"
        elif m1 and 1 <= int(m1.group(1)) <= 20:
            flush()
            buf = []
            current_id, current_title = m1.group(1), m1.group(2).strip()
            h1_title = f"{current_id}. {current_title}"
            current_path = h1_title
        else:
            buf.append(line)
    flush()
    return sections


class PolicyIndex:
    """BM25 over manual sections (title counted 3x)."""

    K1, B = 1.5, 0.75

    def __init__(self, sections: list[Section]):
        self.sections = sections
        self._docs = [tokenize(s.path) * 3 + tokenize(s.text) for s in sections]
        self._avgdl = sum(map(len, self._docs)) / max(len(self._docs), 1)
        df: Counter = Counter()
        for doc in self._docs:
            df.update(set(doc))
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self._tf = [Counter(d) for d in self._docs]

    @classmethod
    def from_pdf(cls, pdf_path: Path | str) -> "PolicyIndex":
        sections = parse_sections(extract_pdf_text(Path(pdf_path)))
        if len(sections) < 10:
            raise DataError(f"Policy manual parsed into only {len(sections)} sections; the PDF layout may have changed.")
        return cls(sections)

    @staticmethod
    def expand(query: str) -> str:
        q = normalize(query)
        extra = [exp for pattern, exp in _EXPANSIONS if pattern.search(q)]
        return " ".join([q, *extra])

    def search(self, query: str, top_k: int = 3) -> list[Section]:
        q_tokens = tokenize(self.expand(query))
        if not q_tokens:
            return []
        scored = []
        for i, tf in enumerate(self._tf):
            dl = len(self._docs[i])
            score = 0.0
            for t in q_tokens:
                f = tf.get(t, 0)
                if f:
                    score += self._idf[t] * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * dl / self._avgdl))
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [self.sections[i] for _, i in scored[:top_k]]
