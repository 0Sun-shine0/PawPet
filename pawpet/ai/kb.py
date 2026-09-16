r"""知识库：把你自己的资料导入，让 AI 回答时能引用。

为什么不是「全塞进提示词」
--------------------------
最直觉的做法是把导入的文件全拼进系统提示词。**那是错的**：

* 记忆那块踩过同样的坑 —— 每轮都要把内容重发一遍，几份文档就能
  把上下文撑爆、把用户的钱烧光；
* 而且大部分内容和当前问题无关，纯噪声，反而拉低回答质量。

所以这里做的是**检索**，不是灌注：

    导入 → 切块 → 建关键词索引 → 提问时按需检索 → 只把命中的几块给它

系统提示词里只放一份**目录**（有哪些资料、各多少块），
真正的正文靠 `search_kb` 按需取。这样知识库可以有几十万字，
而每轮的固定开销只有几百 token。

切块为什么按标题切
------------------
按固定字数硬切会把一段话劈成两半，检索到半截反而误导模型。
所以优先按 Markdown 标题切，切不动（没有标题的大文件）再按
段落 + 字数上限兜底。每块都记着它属于哪个文件、什么标题，
模型看到就知道这段话的出处。

支持哪些格式
------------
文本类的都读：md / txt / py / js / ts / json / csv / yml / html …
`.docx` 和 `.pdf` **不做**（要额外依赖，而且解析质量参差）。
遇到它们会明确告诉用户「先另存为 txt/md」—— 比硬读出一堆乱码好。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import files

# 容量上限
MAX_DOCS = 200                 # 最多导入多少份文档
MAX_CHUNKS_PER_DOC = 400       # 单份文档最多切多少块
CHUNK_CHARS = 1200             # 每块大致多少字
# 合并阈值：比这个短的相邻小节会被并成一块。
#
# 这里踩过坑：原来叫 MIN_CHUNK_CHARS，作用是「把短块直接丢掉」，
# 结果把「## 定价 / 基础版免费」这种**真内容**（十几字）也扔了 ——
# 用户导入的文档里那几节凭空消失，检索永远搜不到。
# 短不代表没用，正确做法是**并进相邻块**，而不是删掉。
MERGE_BELOW_CHARS = 200        # 比这个短的相邻小节会被并成一块
MAX_DOC_BYTES = 400_000        # 单份文件读取上限（比 files 的 1MB 小，因为是资料）

# 检索
DEFAULT_HITS = 4               # 默认返回几块
MAX_HITS = 8
MAX_TOTAL_CHARS = 6000         # 一次检索返回的总字数上限（约 2k token）

# 目录注入系统提示词的预算
CATALOG_BUDGET = 700

# 支持的文本扩展名。**刻意不含 docx/pdf** —— 见模块开头的说明。
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".text", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".ps1", ".bat", ".cmd",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".csv", ".tsv", ".log", ".sql", ".html", ".htm", ".xml", ".css",
}

# 明确不支持、但用户很可能想导入的格式 —— 要给出可操作的指引
UNSUPPORTED_HINT = {
    ".pdf": "PDF",
    ".docx": "Word 文档",
    ".doc": "Word 文档",
    ".xlsx": "Excel 表格",
    ".xls": "Excel 表格",
    ".pptx": "PPT",
    ".ppt": "PPT",
}


def _now() -> float:
    return time.time()


def _number(value, default: float = 0.0) -> float:
    """安全地转 float。

    存盘文件可能被手改过，出现 "不是数字" 完全可能。直接 float() 会抛
    ValueError，而调用方在 from_dict 里 —— 一条坏记录就能让整个知识库
    （连带应用启动）崩掉。memory.py 里踩过同样的坑。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ==========================================================================
#  数据结构
# ==========================================================================
@dataclass
class Chunk:
    """一块可检索的内容。"""

    text: str = ""
    heading: str = ""       # 这块所属的标题（Markdown 标题或段落首句）

    def as_dict(self) -> dict:
        return {"text": self.text, "heading": self.heading}

    @classmethod
    def from_dict(cls, raw) -> "Chunk":
        if not isinstance(raw, dict):
            return cls()
        return cls(text=str(raw.get("text") or ""),
                   heading=str(raw.get("heading") or ""))

@dataclass
class Doc:
    """导入的一份资料。"""

    name: str = ""
    path: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    added: float = 0.0
    size: int = 0
    error: str = ""         # 导入失败的原因（留着给用户看）

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def chars(self) -> int:
        return sum(len(chunk.text) for chunk in self.chunks)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "chunks": [chunk.as_dict() for chunk in self.chunks],
            "added": float(self.added),
            "size": int(self.size),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw) -> "Doc":
        if not isinstance(raw, dict):
            return cls()
        chunks = []
        for item in raw.get("chunks") or []:
            chunk = Chunk.from_dict(item)
            if chunk.text:
                chunks.append(chunk)
        return cls(
            name=str(raw.get("name") or ""),
            path=str(raw.get("path") or ""),
            chunks=chunks,
            added=_number(raw.get("added")),
            size=_integer(raw.get("size")),
            error=str(raw.get("error") or ""),
        )


# ==========================================================================
#  切块
# ==========================================================================
def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题切，返回 [(标题, 正文)]。没有标题就返回一块空的。"""
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    buffer: list[str] = []

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            # 新标题：把上一段收掉
            if buffer or current_heading:
                sections.append((current_heading, buffer))
            current_heading = stripped.lstrip("#").strip()
            buffer = []
        else:
            buffer.append(line)

    if buffer or current_heading:
        sections.append((current_heading, buffer))
    return [(heading, "\n".join(lines)) for heading, lines in sections]


def _split_by_size(text: str, heading: str) -> list[Chunk]:
    """把一段长正文按段落攒到 CHUNK_CHARS 左右，切不动就硬切。"""
    paragraphs = [p for p in text.split("\n\n")]
    chunks: list[Chunk] = []
    buffer = ""

    for para in paragraphs:
        if not para.strip():
            continue
        if len(buffer) + len(para) + 2 <= CHUNK_CHARS:
            buffer = f"{buffer}\n\n{para}" if buffer else para
            continue

        if buffer:
            chunks.append(Chunk(text=buffer.strip(), heading=heading))
            buffer = ""

        # 单段就超长 —— 硬切
        while len(para) > CHUNK_CHARS:
            chunks.append(Chunk(text=para[:CHUNK_CHARS], heading=heading))
            para = para[CHUNK_CHARS:]
        buffer = para

    if buffer.strip():
        chunks.append(Chunk(text=buffer.strip(), heading=heading))
    return chunks


def _merge_small(chunks: list[Chunk]) -> list[Chunk]:
    """把过短的相邻块并起来。

    「## 定价 / 基础版免费，含专注计时和待办。」这种小节只有十几字，
    单独成块检索不到也没法用，但它**是真内容**，不能删。
    所以往后并，直到够长或者碰到一个已经够长的块。
    """
    merged: list[Chunk] = []
    buffer_text: list[str] = []
    buffer_headings: list[str] = []

    def flush() -> None:
        if not buffer_text:
            return
        text = "\n\n".join(buffer_text).strip()
        if not text:
            return
        heading = " / ".join(h for h in buffer_headings if h)
        merged.append(Chunk(text=text, heading=heading))
        buffer_text.clear()
        buffer_headings.clear()

    for chunk in chunks:
        buffer_text.append(chunk.text)
        if chunk.heading:
            buffer_headings.append(chunk.heading)
        total = sum(len(part) for part in buffer_text)
        # 够长了就落一块，避免越长越大的雪球
        if total >= MERGE_BELOW_CHARS:
            flush()

    flush()
    return merged


def chunk_text(text: str) -> list[Chunk]:
    """把一份文档切成可检索的块。"""
    text = (text or "").strip()
    if not text:
        return []

    chunks: list[Chunk] = []
    for heading, body in _split_by_heading(text):
        if body.strip():
            chunks.extend(_split_by_size(body, heading))

    # 没切出东西（比如整篇没换行）就兜底硬切
    if not chunks:
        chunks = _split_by_size(text, "")

    # 只丢掉「完全没内容」的（纯空白已经在 _split_by_size 里挡掉了）
    chunks = [c for c in chunks if c.text.strip()]
    chunks = _merge_small(chunks)
    return chunks[:MAX_CHUNKS_PER_DOC]


# ==========================================================================
#  检索
# ==========================================================================
def _tokens(query: str) -> list[str]:
    """把查询拆成关键词。

    中文没有分词器可用，所以：
    * 先按标点/空格切成**小段**（「专业版多少钱」是一个段）
    * 每段取 2-3 字滑窗，得到「专业」「业版」「版多」…… 这些是检索主力
    * 单字**平时不用** —— 实测「的」「么」这种字几乎在每个块里都有，
      会把真正相关的块淹掉（问「怎么收费的」，噪声块反而排前面）。
      只有整句短到拆不出词时才退回用单字。

    这是检索用的粗关键词，不追求语言学正确 —— 只要能把候选块捞出来，
    精确判断交给模型。
    """
    text = (query or "").lower()
    # 切成中英混合的小段
    segments: list[str] = []
    buffer = ""
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or ch.isalnum() or ch in "_-":
            buffer += ch
        else:
            if buffer:
                segments.append(buffer)
                buffer = ""
    if buffer:
        segments.append(buffer)

    words: list[str] = []      # 多字词，权重高
    singles: list[str] = []    # 单字，兜底用

    seen_word: set[str] = set()
    seen_single: set[str] = set()

    for segment in segments:
        if all("\u4e00" <= ch <= "\u9fff" for ch in segment):
            # 纯中文：滑窗取 2 字和 3 字
            for size in (2, 3):
                for index in range(len(segment) - size + 1):
                    piece = segment[index:index + size]
                    if piece not in seen_word:
                        seen_word.add(piece)
                        words.append(piece)
            for ch in segment:
                if ch not in seen_single:
                    seen_single.add(ch)
                    singles.append(ch)
        else:
            # 含英文/数字：整段当一个词
            if len(segment) >= 2 and segment not in seen_word:
                seen_word.add(segment)
                words.append(segment)

    if words:
        return words + singles[:4]
    return singles


def _is_word(token: str) -> bool:
    """这个词够不够「实」。单字不算 —— 它们只用来兜底。"""
    if len(token) >= 2:
        return True
    # 单个英文字母/数字也没意义
    return False


def score_chunk(chunk: Chunk, tokens: list[str], doc_name: str = "") -> float:
    """给一块打分。

    权重设计（都是实测调出来的）：
    * 命中的词**越长越可信** —— 「专业版」比「的」有意义得多
    * 命中标题权重更高 —— 标题通常就是主题
    * 出现次数只给很小的加分 —— 否则一个高频字能压过所有真关键词
    """
    if not tokens:
        return 0.0
    body = chunk.text.lower()
    heading = chunk.heading.lower()
    name = (doc_name or "").lower()

    score = 0.0
    for token in tokens:
        # 长度权重：2 字 1.0，3 字 1.6，更长按比例
        weight = max(1.0, (len(token) - 1) * 0.6)
        if token in heading:
            score += 2.5 * weight
        if token in name:
            score += 1.5 * weight
        count = body.count(token)
        if count:
            score += weight * (1.0 + min(count, 4) * 0.1)
    return score


def _has_real_match(chunk: Chunk, tokens: list[str], doc_name: str = "") -> bool:
    """这块是不是命中了至少一个**多字词**？

    为什么需要这个：单字匹配太容易误中。实测搜「量子计算机」时，
    单字「计」命中了「专注计**计**时」，把完全无关的资料排了上去。
    所以只靠单字命中的块直接丢掉 —— 宁可不返回，也不要给错的东西。
    """
    body = chunk.text.lower()
    heading = chunk.heading.lower()
    name = (doc_name or "").lower()
    for token in tokens:
        if not _is_word(token):
            continue
        if token in body or token in heading or token in name:
            return True
    return False


@dataclass
class Hit:
    """一条检索结果。"""

    doc: str = ""
    heading: str = ""
    text: str = ""
    score: float = 0.0

    def render(self, budget: int) -> str:
        head = self.heading or "（无标题）"
        body = self.text
        if len(body) > budget:
            body = body[: budget - 1].rstrip() + "…"
        return f"【{self.doc} · {head}】\n{body}"


def search(docs: list[Doc], query: str, limit: int = DEFAULT_HITS,
           max_chars: int = MAX_TOTAL_CHARS) -> list[Hit]:
    """在知识库里检索。返回按相关度排序的命中。"""
    tokens = _tokens(query)
    if not tokens:
        return []

    scored: list[Hit] = []
    for doc in docs:
        for chunk in doc.chunks:
            # 先要求命中至少一个多字词 —— 只靠单字命中的一律不要
            if not _has_real_match(chunk, tokens, doc.name):
                continue
            value = score_chunk(chunk, tokens, doc.name)
            if value <= 0:
                continue
            scored.append(Hit(doc=doc.name, heading=chunk.heading,
                              text=chunk.text, score=value))

    scored.sort(key=lambda hit: hit.score, reverse=True)

    limit = max(1, min(int(limit or DEFAULT_HITS), MAX_HITS))
    chosen: list[Hit] = []
    used = 0
    for hit in scored[: limit * 3]:
        if len(chosen) >= limit:
            break
        cost = len(hit.text)
        if used + cost > max_chars and chosen:
            continue
        chosen.append(hit)
        used += cost
    return chosen


# ==========================================================================
#  目录（注入系统提示词的那一小段）
# ==========================================================================
def catalog_text(docs: list[Doc], budget: int = CATALOG_BUDGET) -> str:
    """渲染知识库目录。

    **只列有什么，不列正文。** 每轮都要发的东西，必须小。
    没有资料时返回空串（不注入空壳）。
    """
    usable = [doc for doc in docs if doc.chunks]
    if not usable:
        return ""

    lines: list[str] = []
    used = 0
    for doc in sorted(usable, key=lambda d: d.added, reverse=True):
        line = f"- {doc.name}（{doc.chunk_count} 块）"
        if used + len(line) > budget:
            lines.append(f"…还有 {len(usable) - len(lines)} 份资料")
            break
        lines.append(line)
        used += len(line) + 1

    body = "\n".join(lines)
    return (
        "\n\n【知识库】\n"
        f"{body}\n"
        "这些是用户导入的资料。**需要引用时用 search_knowledge 检索**"
        "（不要凭印象编造里面的内容）。"
        "目录里没有但用户提到的资料，说明还没导入，可以问他。"
        "用户想加资料时，用 import_knowledge 传路径即可。"
    )


def describe(docs: list[Doc]) -> str:
    """给界面看的多行摘要。"""
    usable = [doc for doc in docs if doc.chunks]
    if not usable:
        return ("知识库是空的。\n"
                "把资料放到一个文件夹里，然后说「把这个文件夹导入知识库」，"
                "以后我回答时就能引用里面的内容。")

    total_chunks = sum(doc.chunk_count for doc in usable)
    total_chars = sum(doc.chars for doc in usable)
    lines = [f"共 {len(usable)} 份资料、{total_chunks} 块，"
             f"约 {total_chars // 1000} 千字。", ""]
    for doc in sorted(usable, key=lambda d: d.added, reverse=True)[:25]:
        lines.append(f"  · {doc.name}（{doc.chunk_count} 块）")
    if len(usable) > 25:
        lines.append(f"  …还有 {len(usable) - 25} 份")
    failed = [doc for doc in docs if doc.error]
    if failed:
        lines.append("")
        lines.append("没能导入的：")
        for doc in failed[:6]:
            lines.append(f"  · {doc.name}：{doc.error}")
    return "\n".join(lines)


# ==========================================================================
#  导入
# ==========================================================================
def import_file(path_text: str, existing: list[Doc]) -> tuple[Doc | None, str]:
    """导入单个文件。返回 (文档, 说明)。

    同名的会**替换**而不是追加 —— 用户重新导入更新版资料时，
    多半希望是覆盖，而不是攒出两份互相矛盾的旧版本。
    """
    try:
        expanded = files.expand(path_text)
    except files.FileDenied as exc:
        return None, str(exc)

    suffix = expanded.suffix.lower()
    if suffix in UNSUPPORTED_HINT:
        kind = UNSUPPORTED_HINT[suffix]
        return None, (f"{kind}（{suffix}）暂时不支持直接导入。"
                      "请在原程序里「另存为」纯文本或 Markdown（.txt / .md），"
                      "再导进来 —— 那样内容最准。")

    if suffix and suffix not in TEXT_SUFFIXES:
        # 未知后缀先试着当文本读；读出来是二进制会被 files 挡掉
        pass

    if not expanded.exists():
        return None, f"没有这个文件：{expanded}"
    if expanded.is_dir():
        return None, f"「{expanded.name}」是文件夹，用 import_folder 导入整个目录"

    result = files.read_text(str(expanded), max_lines=100_000,
                             max_bytes=MAX_DOC_BYTES)
    if not result.ok:
        return None, result.message

    chunks = chunk_text(result.text)
    if not chunks:
        return None, f"「{expanded.name}」里没有可用的文本内容"

    doc = Doc(name=expanded.name, path=str(expanded), chunks=chunks,
              added=_now(), size=result.size)
    return doc, f"已导入「{doc.name}」：{doc.chunk_count} 块"


def import_folder(folder_text: str, existing: list[Doc],
                  recursive: bool = False) -> tuple[list[Doc], list[str]]:
    """导入整个目录里的文本文件。返回 (新文档, 说明列表)。"""
    try:
        folder = files.expand(folder_text)
    except files.FileDenied as exc:
        return [], [str(exc)]

    if not folder.exists():
        return [], [f"没有这个目录：{folder}"]
    if not folder.is_dir():
        return [], [f"「{folder.name}」是文件，用 import_file 导入单个文件"]

    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        return [], [f"打不开这个目录：{exc}"]

    if recursive:
        entries = sorted(folder.rglob("*"), key=lambda p: str(p).lower())

    documents: list[Doc] = []
    notes: list[str] = []
    skipped = 0

    for item in entries:
        try:
            if not item.is_file():
                continue
        except OSError:
            continue
        suffix = item.suffix.lower()
        if suffix in UNSUPPORTED_HINT:
            notes.append(f"跳过 {item.name}（{UNSUPPORTED_HINT[suffix]} 不支持，"
                         "请先另存为 txt/md）")
            continue
        if suffix not in TEXT_SUFFIXES:
            skipped += 1
            continue

        doc, message = import_file(str(item), existing)
        if doc is None:
            notes.append(f"{item.name}：{message}")
            continue
        documents.append(doc)
        if len(documents) >= MAX_DOCS:
            notes.append(f"到上限了（最多 {MAX_DOCS} 份），剩下的没导")
            break

    if skipped:
        notes.append(f"跳过了 {skipped} 个非文本文件")
    return documents, notes


def merge(existing: list[Doc], incoming: list[Doc]) -> list[Doc]:
    """把新导入的合并进已有列表，同路径的替换。"""
    by_path = {doc.path: doc for doc in existing if doc.path}
    for doc in incoming:
        by_path[doc.path] = doc
    merged = list(by_path.values())
    merged.sort(key=lambda d: d.added)
    # 超上限时丢最旧的
    if len(merged) > MAX_DOCS:
        merged = merged[-MAX_DOCS:]
    return merged


class KnowledgeBase:
    """把知识库绑到 Store 上（和 MemoryBook 一个套路）。"""

    def __init__(self, store) -> None:
        self._store = store

    def load(self) -> list[Doc]:
        try:
            raw = self._store.knowledge
        except Exception:  # noqa: BLE001
            return []
        docs = []
        for item in (raw.get("docs") if isinstance(raw, dict) else None) or []:
            doc = Doc.from_dict(item)
            if doc.name and (doc.chunks or doc.error):
                docs.append(doc)
        return docs

    def save(self, docs: list[Doc]) -> None:
        self._store.state["knowledge"] = {
            "docs": [doc.as_dict() for doc in docs],
            "updated": _now(),
        }
        self._store.save()

    def add(self, incoming: list[Doc]) -> list[Doc]:
        merged = merge(self.load(), incoming)
        self.save(merged)
        return merged

    def clear(self) -> int:
        count = len(self.load())
        self.save([])
        return count

    def remove(self, name: str) -> Doc | None:
        docs = self.load()
        needle = (name or "").strip().lower()
        for index, doc in enumerate(docs):
            if doc.name.lower() == needle or (needle and needle in doc.name.lower()):
                removed = docs.pop(index)
                self.save(docs)
                return removed
        return None
