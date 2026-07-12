"""
PDF解析模块
支持Docling本地解析，将PDF转换为Markdown文本
参考 RAG-cy/src/pdf_parsing.py 适配（简化，去掉sha1/company_name逻辑）
"""
import json
import logging
from pathlib import Path
from typing import List, Optional

_log = logging.getLogger(__name__)


class PDFParser:
    """
    PDF解析器，使用Docling将PDF转换为结构化JSON/Markdown
    简化版：去掉参考项目中的sha1/company_name逻辑
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        num_threads: Optional[int] = None,
    ):
        self.output_dir = output_dir
        self.num_threads = num_threads
        self._doc_converter = None

        if self.num_threads is not None:
            import os
            os.environ["OMP_NUM_THREADS"] = str(self.num_threads)

    def _create_document_converter(self):
        """延迟创建DocumentConverter（需要docling依赖）"""
        from docling.document_converter import DocumentConverter, FormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, EasyOcrOptions
        from docling.datamodel.base_models import InputFormat
        from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
        from docling.backend.docling_parse_v2_backend import DoclingParseV2DocumentBackend

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        ocr_options = EasyOcrOptions(lang=["en"], force_full_page_ocr=False)
        pipeline_options.ocr_options = ocr_options
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE

        format_options = {
            InputFormat.PDF: FormatOption(
                pipeline_cls=StandardPdfPipeline,
                pipeline_options=pipeline_options,
                backend=DoclingParseV2DocumentBackend,
            )
        }
        return DocumentConverter(format_options=format_options)

    @property
    def doc_converter(self):
        if self._doc_converter is None:
            self._doc_converter = self._create_document_converter()
        return self._doc_converter

    def parse_single(self, pdf_path: Path) -> str:
        """
        解析单个PDF文件，返回Markdown文本
        """
        from docling.datamodel.base_models import ConversionStatus

        conv_results = self.doc_converter.convert_all(source=[pdf_path])
        markdown_text = ""

        for conv_res in conv_results:
            if conv_res.status == ConversionStatus.SUCCESS:
                markdown_text = conv_res.document.export_to_markdown()
            else:
                _log.error(f"PDF解析失败: {pdf_path.name}, status={conv_res.status}")
                raise RuntimeError(f"PDF解析失败: {pdf_path.name}")

        return markdown_text

    def parse_batch(
        self,
        pdf_dir: Path,
        output_dir: Optional[Path] = None,
    ) -> List[dict]:
        """
        批量解析目录下的所有PDF文件
        返回: [{"file_name": str, "markdown": str}, ...]
        如果指定output_dir，同时保存为.json文件
        """
        from docling.datamodel.base_models import ConversionStatus

        output_dir = output_dir or self.output_dir
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        pdf_files = sorted(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            _log.warning(f"目录中没有找到PDF文件: {pdf_dir}")
            return []

        results = []
        conv_results = self.doc_converter.convert_all(source=pdf_files)

        for conv_res in conv_results:
            doc_name = conv_res.input.file.stem
            if conv_res.status == ConversionStatus.SUCCESS:
                markdown_text = conv_res.document.export_to_markdown()
                results.append({
                    "file_name": conv_res.input.file.name,
                    "source": doc_name,
                    "markdown": markdown_text,
                })
                _log.info(f"解析成功: {conv_res.input.file.name}")
            else:
                _log.error(f"解析失败: {conv_res.input.file.name}")

        # 保存到输出目录
        if output_dir:
            for result in results:
                source = result["source"]
                output_path = output_dir / f"{source}.json"
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)

        _log.info(f"共解析 {len(results)}/{len(pdf_files)} 个PDF文件")
        return results

    def parse_and_export_json(
        self,
        pdf_dir: Path,
        output_dir: Path,
        category: str = "course",
    ):
        """
        解析PDF并输出为分块前的JSON格式（与text_splitter兼容）
        每个PDF生成一个JSON文件: {"metainfo": {...}, "content": {"markdown": str}}
        """
        import hashlib

        output_dir.mkdir(parents=True, exist_ok=True)
        # 临时保存并清除self.output_dir，避免parse_batch重复保存
        saved_output_dir = self.output_dir
        self.output_dir = None
        results = self.parse_batch(pdf_dir)
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
