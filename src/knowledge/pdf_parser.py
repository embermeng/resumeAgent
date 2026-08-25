"""
PDF解析模块
使用MinerU本地解析（pipeline后端，GPU加速），将PDF转换为Markdown文本
MinerU 3.x API入口为 mineru.cli.common.do_parse
"""
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import List, Optional

_log = logging.getLogger(__name__)

# 图片引用行（MinerU输出的Markdown中以 ![](images/xxx) 引用抽取的图片）
_IMAGE_REF_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class PDFParser:
    """
    PDF解析器，使用MinerU将PDF转换为Markdown
    接口与旧Docling版本保持一致: parse_single / parse_batch / parse_and_export_json
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        num_threads: Optional[int] = None,
        backend: str = "pipeline",
        parse_method: str = "auto",
        lang: str = "ch",
    ):
        self.output_dir = output_dir
        self.num_threads = num_threads
        self.backend = backend
        self.parse_method = parse_method
        self.lang = lang

        if self.num_threads is not None:
            import os
            os.environ["OMP_NUM_THREADS"] = str(self.num_threads)

    @staticmethod
    def _clean_markdown(markdown_text: str) -> str:
        """移除Markdown中的图片引用（知识库文本不需要图片占位）"""
        cleaned = _IMAGE_REF_PATTERN.sub("", markdown_text)
        # 压缩移除图片后产生的多余空行
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _run_mineru(self, output_dir: Path, pdf_paths: List[Path]) -> None:
        """调用MinerU do_parse批量解析，结果写入 output_dir/<文件名>/<parse_method>/"""
        from mineru.cli.common import do_parse

        do_parse(
            output_dir=str(output_dir),
            pdf_file_names=[p.stem for p in pdf_paths],
            pdf_bytes_list=[p.read_bytes() for p in pdf_paths],
            p_lang_list=[self.lang] * len(pdf_paths),
            backend=self.backend,
            parse_method=self.parse_method,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            f_dump_md=True,
            f_dump_middle_json=False,
            f_dump_model_output=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=False,
        )

    def _read_markdown(self, output_dir: Path, stem: str) -> str:
        """读取MinerU输出的Markdown文件并清理图片引用"""
        md_path = output_dir / stem / self.parse_method / f"{stem}.md"
        if not md_path.exists():
            raise RuntimeError(f"MinerU未生成Markdown输出: {md_path}")
        return self._clean_markdown(md_path.read_text(encoding="utf-8"))

    def parse_single(self, pdf_path: Path) -> str:
        """
        解析单个PDF文件，返回Markdown文本
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                self._run_mineru(Path(tmp_dir), [pdf_path])
                markdown_text = self._read_markdown(Path(tmp_dir), pdf_path.stem)
            except Exception as e:
                _log.error(f"MinerU解析失败: {pdf_path.name}, error={e}")
                raise RuntimeError(f"PDF解析失败: {pdf_path.name}") from e

        if not markdown_text.strip():
            raise RuntimeError(f"PDF解析结果为空: {pdf_path.name}")
        return markdown_text

    def parse_batch(
        self,
        pdf_dir: Path,
        output_dir: Optional[Path] = None,
        pdf_files: Optional[List[Path]] = None,
    ) -> List[dict]:
        """
        批量解析PDF文件
        参数:
            pdf_dir: PDF所在目录（pdf_files未指定时扫描该目录）
            output_dir: 指定时同时保存为.json文件
            pdf_files: 指定待解析的PDF列表（增量解析时使用）
        返回: [{"file_name": str, "source": str, "markdown": str}, ...]
        """
        output_dir = output_dir or self.output_dir
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        if pdf_files is None:
            pdf_files = sorted(pdf_dir.glob("*.pdf"))
        else:
            pdf_files = sorted(pdf_files)
        if not pdf_files:
            _log.warning(f"没有需要解析的PDF文件: {pdf_dir}")
            return []

        results = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            try:
                self._run_mineru(tmp_path, pdf_files)
            except Exception as e:
                _log.error(f"MinerU批量解析失败，尝试逐个解析: {e}")
                for pdf_path in pdf_files:
                    try:
                        self._run_mineru(tmp_path, [pdf_path])
                    except Exception as single_err:
                        _log.error(f"MinerU解析失败: {pdf_path.name}, error={single_err}")

            for pdf_path in pdf_files:
                try:
                    markdown_text = self._read_markdown(tmp_path, pdf_path.stem)
                except Exception as e:
                    _log.error(f"读取解析结果失败: {pdf_path.name}, error={e}")
                    continue
                if not markdown_text.strip():
                    _log.error(f"解析结果为空: {pdf_path.name}")
                    continue
                results.append({
                    "file_name": pdf_path.name,
                    "source": pdf_path.stem,
                    "markdown": markdown_text,
                })
                _log.info(f"解析成功(MinerU): {pdf_path.name}")

        # 保存到输出目录
        if output_dir:
            for result in results:
                source = result["source"]
                output_path = output_dir / f"{source}.json"
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)

        _log.info(f"共解析 {len(results)}/{len(pdf_files)} 个PDF文件 (MinerU)")
        return results

    def parse_and_export_json(
        self,
        pdf_dir: Path,
        output_dir: Path,
        category: str = "course",
        force: bool = False,
    ):
        """
        解析PDF并输出为分块前的JSON格式（与text_splitter兼容）
        每个PDF生成一个JSON文件: {"metainfo": {...}, "content": {"markdown": str}}
        增量解析：默认跳过output_dir中已存在结果的PDF，force=True时全量重解析
        """
        import hashlib

        output_dir.mkdir(parents=True, exist_ok=True)

        # 增量过滤：已存在 {doc_id}.json 的PDF跳过
        all_pdfs = sorted(pdf_dir.glob("*.pdf"))
        if force:
            pending_pdfs = all_pdfs
        else:
            pending_pdfs = []
            for pdf_path in all_pdfs:
                doc_id = hashlib.md5(pdf_path.stem.encode()).hexdigest()[:16]
                if (output_dir / f"{doc_id}.json").exists():
                    _log.info(f"跳过已解析的PDF: {pdf_path.name}")
                else:
                    pending_pdfs.append(pdf_path)

        if not pending_pdfs:
            _log.info("所有PDF均已解析，无新文件需要处理")
            return []

        _log.info(
            f"共 {len(all_pdfs)} 个PDF，跳过已解析 {len(all_pdfs) - len(pending_pdfs)} 个，"
            f"本次解析 {len(pending_pdfs)} 个"
        )

        # 临时保存并清除self.output_dir，避免parse_batch重复保存
        saved_output_dir = self.output_dir
        self.output_dir = None
        results = self.parse_batch(pdf_dir, pdf_files=pending_pdfs)
        self.output_dir = saved_output_dir

        for result in results:
            source = result["source"]
            doc_id = hashlib.md5(source.encode()).hexdigest()[:16]

            output_data = {
                "metainfo": {
                    "doc_id": doc_id,
                    "source": source,
                    "category": category,
                    "file_name": result["file_name"],
                },
                "content": {
                    "markdown": result["markdown"],
                },
            }

            output_path = output_dir / f"{doc_id}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)

            _log.info(f"已导出: {result['file_name']} -> {output_path.name}")

        return results
