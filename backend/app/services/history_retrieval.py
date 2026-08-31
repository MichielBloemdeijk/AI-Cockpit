"""Safe rg-shaped search over transactionally maintained SQLite recall text."""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import re
import shlex
from time import perf_counter

from sqlalchemy import delete, func, select, text

from app.db.session import session_scope
from app.db.tables import RecallDocument


MAX_RESULTS = 100
MAX_PATTERN = 256
MAX_SCAN = 100_000


@dataclass(slots=True)
class HistoryQuery:
    pattern: str = ""
    paths: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    fixed: bool = False
    ignore_case: bool = False
    word: bool = False
    line: bool = False
    before: int = 0
    after: int = 0
    max_count: int = 50
    sort: str = "relevance"


@dataclass(frozen=True, slots=True)
class HistoryMatch:
    source_type: str
    internal_source_id: str
    virtual_path: str
    timestamp: object
    title: str
    matching_text: str
    line_number: int
    context: tuple[dict, ...]


class HistoryRetrievalService:
    def parse(self, argv: str) -> HistoryQuery:
        try:
            tokens = shlex.split(argv, posix=True)
        except ValueError as exc:
            raise ValueError(f"Invalid history_grep arguments: {exc}") from exc
        query = HistoryQuery()
        index = 0
        positional: list[str] = []
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                positional.extend(tokens[index + 1:])
                break
            if token in {"-F", "--fixed-strings"}:
                query.fixed = True
            elif token in {"-i", "--ignore-case"}:
                query.ignore_case = True
            elif token in {"-w", "--word-regexp"}:
                query.word = True
            elif token in {"-x", "--line-regexp"}:
                query.line = True
            elif token in {"-A", "--after-context", "-B", "--before-context", "-C", "--context", "-m", "--max-count", "-g", "--glob", "--sort"}:
                index += 1
                if index >= len(tokens):
                    raise ValueError(f"Option {token} requires a value")
                self._set_option(query, token, tokens[index])
            elif token.startswith("--sort="):
                self._set_option(query, "--sort", token.split("=", 1)[1])
            elif re.fullmatch(r"-[ABCMm]\d+", token):
                self._set_option(query, token[:2], token[2:])
            elif token.startswith("-g") and len(token) > 2:
                self._set_option(query, "-g", token[2:])
            elif token.startswith("-"):
                raise ValueError(f"Unsupported history_grep option: {token}")
            else:
                positional.append(token)
            index += 1
        if not positional:
            raise ValueError("history_grep requires PATTERN")
        query.pattern = positional[0]
        query.paths = tuple(self._normalize_path(value) for value in positional[1:])
        if not query.pattern or len(query.pattern) > MAX_PATTERN or "\x00" in query.pattern:
            raise ValueError(f"Pattern must contain 1-{MAX_PATTERN} safe characters")
        if not query.fixed:
            self._safe_regex(query.pattern)
        return query

    async def search(self, argv: str) -> tuple[list[HistoryMatch], bool, float]:
        started = perf_counter()
        query = self.parse(argv)
        matcher = self._matcher(query)
        async with session_scope() as session:
            candidate_orders: list[int] | None = None
            if query.fixed and len(query.pattern) >= 3:
                quoted = '"' + query.pattern.replace('"', '""') + '"'
                try:
                    candidate_orders = [int(value) for value in (await session.execute(
                        text(
                            "SELECT rowid FROM recall_fts_trigram "
                            "WHERE recall_fts_trigram MATCH :query LIMIT :limit"
                        ),
                        {"query": quoted, "limit": MAX_SCAN},
                    )).scalars().all()]
                except Exception:
                    candidate_orders = None
            statement = select(RecallDocument)
            if candidate_orders is not None:
                if not candidate_orders:
                    return [], False, (perf_counter() - started) * 1000
                statement = statement.where(RecallDocument.document_order.in_(candidate_orders))
            statement = statement.order_by(RecallDocument.source_timestamp.desc()).limit(MAX_SCAN + 1)
            documents = list((await session.execute(statement)).scalars().all())
        scan_truncated = len(documents) > MAX_SCAN
        documents = documents[:MAX_SCAN]
        scored: list[tuple[int, object, HistoryMatch]] = []
        for document in documents:
            if not self._path_allowed(document.virtual_path, query):
                continue
            lines = document.content.splitlines() or [document.content]
            for line_index, line_text in enumerate(lines):
                matches = list(matcher.finditer(line_text))
                if not matches:
                    continue
                start = max(0, line_index - query.before)
                end = min(len(lines), line_index + query.after + 1)
                context = tuple(
                    {
                        "line_number": context_index + 1,
                        "text": lines[context_index],
                        "matching": context_index == line_index,
                    }
                    for context_index in range(start, end)
                )
                scored.append((
                    len(matches),
                    document.source_timestamp,
                    HistoryMatch(
                        source_type=document.source_type,
                        internal_source_id=document.source_id,
                        virtual_path=document.virtual_path,
                        timestamp=document.source_timestamp,
                        title=document.title,
                        matching_text=line_text,
                        line_number=line_index + 1,
                        context=context,
                    ),
                ))
                if sum(1 for _score, _time, item in scored if item.virtual_path == document.virtual_path) >= query.max_count:
                    break
        reverse = query.sort != "oldest"
        if query.sort == "relevance":
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        else:
            scored.sort(key=lambda item: item[1], reverse=reverse)
        truncated = scan_truncated or len(scored) > query.max_count
        matches = [item[2] for item in scored[:query.max_count]]
        return matches, truncated, (perf_counter() - started) * 1000

    async def rebuild(self) -> int:
        async with session_scope() as session:
            await session.execute(delete(RecallDocument))
            statements = [
                """
                INSERT OR IGNORE INTO recall_documents
                    (id, virtual_path, source_type, source_id, title, content, source_timestamp)
                SELECT lower(hex(randomblob(16))),
                       'conversation/' || m.conversation_id || '/message/' || m.id || '.txt',
                       'conversation', m.id, COALESCE(c.title, 'Conversation'), m.content, m.created_at
                FROM conversation_messages m JOIN conversations c ON c.id=m.conversation_id
                WHERE m.role IN ('user', 'assistant') AND m.communication_state != 'unheard'
                """,
                """
                INSERT OR IGNORE INTO recall_documents
                    (id, virtual_path, source_type, source_id, title, content, source_timestamp)
                SELECT lower(hex(randomblob(16))), 'task/' || id || '/summary.txt', 'task', id,
                       substr(goal, 1, 255),
                       goal || char(10) || 'Status: ' || status || char(10) || COALESCE(terminal_summary, ''),
                       created_at FROM tasks
                """,
                """
                INSERT OR IGNORE INTO recall_documents
                    (id, virtual_path, source_type, source_id, title, content, source_timestamp)
                SELECT lower(hex(randomblob(16))),
                       'task/' || task_id || '/question/' || id || '.txt', 'task', id,
                       'Task question', question || char(10) || COALESCE(answer, ''), asked_at
                FROM task_questions
                """,
                """
                INSERT OR IGNORE INTO recall_documents
                    (id, virtual_path, source_type, source_id, title, content, source_timestamp)
                SELECT lower(hex(randomblob(16))), 'memory/' || id || '.txt', 'memory', id,
                       title, content, created_at FROM memory_items WHERE status='approved'
                """,
            ]
            for statement in statements:
                await session.execute(text(statement))
            return int((await session.execute(select(func.count(RecallDocument.document_order)))).scalar_one())

    @staticmethod
    def _set_option(query: HistoryQuery, token: str, value: str) -> None:
        if token in {"-g", "--glob"}:
            query.globs += (value.replace("\\", "/"),)
            return
        if token == "--sort":
            if value not in {"relevance", "newest", "oldest"}:
                raise ValueError("--sort must be relevance, newest, or oldest")
            query.sort = value
            return
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"Option {token} requires an integer") from exc
        if token in {"-A", "--after-context"}:
            query.after = min(max(number, 0), 20)
        elif token in {"-B", "--before-context"}:
            query.before = min(max(number, 0), 20)
        elif token in {"-C", "--context"}:
            query.before = query.after = min(max(number, 0), 20)
        elif token in {"-m", "--max-count", "-M"}:
            query.max_count = min(max(number, 1), MAX_RESULTS)

    @staticmethod
    def _normalize_path(value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("History paths must stay within conversation/**, task/**, or memory/**")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized and normalized.split("/", 1)[0] not in {"conversation", "task", "memory"}:
            raise ValueError("History paths must use conversation/**, task/**, or memory/**")
        return normalized

    @staticmethod
    def _safe_regex(pattern: str) -> None:
        if re.search(r"\\[1-9]|\(\?<?[=!]|\(\?>", pattern):
            raise ValueError("Lookarounds, backreferences, and atomic groups are not supported")
        if re.search(r"(?:\*|\+|\{|\})[^\n]{0,8}(?:\*|\+|\{)", pattern):
            raise ValueError("Nested or repeated regex quantifiers are not supported")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc

    @staticmethod
    def _matcher(query: HistoryQuery):
        pattern = re.escape(query.pattern) if query.fixed else query.pattern
        if query.word:
            pattern = rf"\b(?:{pattern})\b"
        if query.line:
            pattern = rf"^(?:{pattern})$"
        return re.compile(pattern, re.IGNORECASE if query.ignore_case else 0)

    @staticmethod
    def _path_allowed(path: str, query: HistoryQuery) -> bool:
        if query.paths and not any(path == prefix or path.startswith(prefix.rstrip("*") + "/") or fnmatch(path, prefix) for prefix in query.paths):
            return False
        return not query.globs or any(fnmatch(path, glob) for glob in query.globs)


history_retrieval_service = HistoryRetrievalService()
