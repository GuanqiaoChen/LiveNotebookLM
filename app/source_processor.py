from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from docx import Document
from pypdf import PdfReader

from app.config import get_settings
from app.schemas import SourceMetadata


class SourceProcessor:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_dir = Path(settings.sessions_dir).resolve()

    def save_uploaded_file_locally(
        self,
        session_id: str,
        source_id: str,
        filename: str,
        content: bytes,
    ) -> str:
        upload_dir = self.base_dir / session_id / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        safe_name = Path(filename).name.replace(" ", "_")
        local_path = upload_dir / f"{source_id}-{safe_name}"
        local_path.write_bytes(content)
        return str(local_path)

    def process_uploaded_bytes(
        self,
        source: SourceMetadata,
        filename: str,
        content: bytes,
    ) -> list[dict]:
        local_path = self.save_uploaded_file_locally(
            session_id=source.session_id,
            source_id=source.source_id,
            filename=filename,
            content=content,
        )
        return self.process_source(source=source, local_file_path=local_path)

    def process_source(
        self,
        source: SourceMetadata,
        local_file_path: Optional[str] = None,
        web_text: Optional[str] = None,
    ) -> list[dict]:
        if source.kind == "web_result":
            if not web_text:
                raise ValueError("web_text is required for web_result sources")
            text = self._clean_text(web_text)
            chunks = self._chunk_text(
                session_id=source.session_id,
                source_id=source.source_id,
                source_name=source.display_name,
                text=text,
                page=None,
                section="web",
            )
            self._persist_chunks(source.session_id, source.source_id, chunks)
            return chunks

        if not local_file_path:
            raise ValueError("local_file_path is required for uploaded_file sources")

        path = Path(local_file_path)
        suffix = path.suffix.lower()

        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            chunks = self._chunk_text(
                session_id=source.session_id,
                source_id=source.source_id,
                source_name=source.display_name,
                text=self._clean_text(text),
                page=None,
                section="text",
            )
        elif suffix == ".pdf":
            chunks = self._extract_pdf_chunks(
                session_id=source.session_id,
                source_id=source.source_id,
                source_name=source.display_name,
                pdf_path=path,
            )
        elif suffix == ".docx":
            text = self._extract_docx_text(path)
            chunks = self._chunk_text(
                session_id=source.session_id,
                source_id=source.source_id,
                source_name=source.display_name,
                text=self._clean_text(text),
                page=None,
                section="docx",
            )
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        self._persist_chunks(source.session_id, source.source_id, chunks)
        return chunks

    def get_chunks(self, session_id: str, source_id: str) -> list[dict]:
        path = self._chunks_path(session_id, source_id)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _extract_pdf_chunks(
        self,
        session_id: str,
        source_id: str,
        source_name: str,
        pdf_path: Path,
    ) -> list[dict]:
        reader = PdfReader(str(pdf_path))
        all_chunks: list[dict] = []

        for idx, page in enumerate(reader.pages, start=1):
            raw_text = page.extract_text() or ""
            text = self._clean_text(raw_text)
            if not text:
                continue

            page_chunks = self._chunk_text(
                session_id=session_id,
                source_id=source_id,
                source_name=source_name,
                text=text,
                page=idx,
                section=f"page_{idx}",
            )
            all_chunks.extend(page_chunks)

        return all_chunks

    @staticmethod
    def _extract_docx_text(path: Path) -> str:
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    @staticmethod
    def _clean_text(text: str) -> str:
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    def _chunk_text(
        self,
        session_id: str,
        source_id: str,
        source_name: str,
        text: str,
        page: Optional[int],
        section: Optional[str],
        chunk_size: int = 1200,
        overlap: int = 150,
    ) -> list[dict]:
        if not text:
            return []

        chunks: list[dict] = []
        start = 0
        chunk_index = 0

        while start < len(text):
            end = min(len(text), start + chunk_size)
            snippet = text[start:end]

            chunks.append(
                {
                    "chunk_id": f"{source_id}_chunk_{chunk_index}",
                    "session_id": session_id,
                    "source_id": source_id,
                    "source_name": source_name,
                    "text": snippet,
                    "page": page,
                    "section": section,
                }
            )

            if end == len(text):
                break

            start = max(0, end - overlap)
            chunk_index += 1

        return chunks

    def _chunks_path(self, session_id: str, source_id: str) -> Path:
        chunk_dir = self.base_dir / session_id / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        return chunk_dir / f"{source_id}.json"

    def _persist_chunks(self, session_id: str, source_id: str, chunks: list[dict]) -> None:
        path = self._chunks_path(session_id, source_id)
        path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")