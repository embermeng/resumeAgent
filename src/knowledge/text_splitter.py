"""
文本分块模块
将Markdown/文本内容按Token数分块，附带元数据（来源、分类、页码等）
参考 RAG-cy/src/text_splitter.py 适配
"""
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Optional
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tiktoken


class TextSplitter:
    """文本分块工具类，支持课程PDF、项目文档、面试题等多种来源的分块"""

    def __init__(self, encoding_name: str = "o200k_base"):
        self.encoding_name = encoding_name

    def count_tokens(self, text: str) -> int:
        """统计字符串的token数"""
        encoding = tiktoken.get_encoding(self.encoding_name)
        # disallowed_special=()：文本中可能出现特殊token字面量（如课件中的<|endoftext|>），按普通文本编码
        return len(encoding.encode(text, disallowed_special=()))

    def split_text(
        self,
        text: str,
        chunk_size: int = 300,
        chunk_overlap: int = 50,
    ) -> List[Dict]:
        """
        将文本按token数分块
        返回: [{"text": str, "length_tokens": int}, ...]
        """
        splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            model_name="gpt-4o",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            # 文本中可能包含特殊token字面量（如<|endoftext|>），禁用检查按普通文本编码
            disallowed_special=(),
        )
        chunks = splitter.split_text(text)
        return [
            {"text": chunk, "length_tokens": self.count_tokens(chunk)}
            for chunk in chunks
        ]

    def split_markdown_file(
        self,
        md_path: Path,
        chunk_size: int = 300,
        chunk_overlap: int = 50,
    ) -> List[Dict]:
        """
        将单个Markdown文件分块
        返回: [{"text": str, "length_tokens": int, "lines": [start, end]}, ...]
        """
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        return self.split_text(content, chunk_size, chunk_overlap)

    def split_and_save(
        self,
        input_dir: Path,
        output_dir: Path,
        category: str = "course",
        chunk_size: int = 300,
        chunk_overlap: int = 50,
        force: bool = False,
    ):
        """
        批量处理目录下所有Markdown文件，分块并输出为JSON
        增量分块：默认跳过output_dir中已存在结果的文档，force=True时全量重新分块
        参数:
            input_dir: 存放.md文件的目录
            output_dir: 输出.json文件的目录
            category: 知识分类 (course/project/interview)
            chunk_size: 分块大小(token数)
            chunk_overlap: 分块重叠token数
            force: 是否强制全量重新分块
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        md_files = list(input_dir.glob("*.md"))

        skipped = 0
        processed = 0
        for md_path in md_files:
            source_name = md_path.stem
            doc_id = hashlib.md5(source_name.encode()).hexdigest()[:16]

            # 增量跳过：已存在分块结果且非force
            if not force and (output_dir / f"{doc_id}.json").exists():
                print(f"跳过已分块文档: {md_path.name}")
                skipped += 1
                continue

            chunks = self.split_markdown_file(md_path, chunk_size, chunk_overlap)

            # 构建输出格式
            output_data = {
                "metainfo": {
                    "doc_id": doc_id,
                    "source": source_name,
                    "category": category,
                    "file_name": md_path.name,
                },
                "content": {
                    "chunks": [
                        {"id": i, "text": c["text"], "length_tokens": c["length_tokens"]}
                        for i, c in enumerate(chunks)
                    ]
                },
            }

            output_path = output_dir / f"{doc_id}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)

            processed += 1
            print(f"已分块: {md_path.name} -> {output_path.name} ({len(chunks)} chunks)")

        print(
            f"共 {len(md_files)} 个文件，跳过已分块 {skipped} 个，"
            f"本次分块 {processed} 个，输出到 {output_dir}"
        )

    def split_text_direct(
        self,
        text: str,
        source: str,
        category: str = "course",
        chunk_size: int = 300,
        chunk_overlap: int = 50,
    ) -> Dict:
        """
        直接对文本分块，返回完整的文档结构（不依赖文件）
        """
        chunks = self.split_text(text, chunk_size, chunk_overlap)
        doc_id = hashlib.md5(source.encode()).hexdigest()[:16]
        return {
            "metainfo": {
                "doc_id": doc_id,
                "source": source,
                "category": category,
            },
            "content": {
                "chunks": [
                    {"id": i, "text": c["text"], "length_tokens": c["length_tokens"]}
                    for i, c in enumerate(chunks)
                ]
            },
        }
