"""
PDF解析模块
- 简历单文件解析（parse_single）：调用 MinerU 官方 Agent 轻量 API（免 token，远程），
  云端/本地均无需部署 MinerU；用 requests 同步 HTTP，跑在后台任务线程不碰事件循环。
- 知识库课程 PDF 批量构建（parse_batch / parse_and_export_json）：仍用本地 MinerU
  （mineru.cli.common.do_parse，pipeline 后端），因课程 PDF 常超轻量 API 的 20 页限制，
  且知识库仅在本地构建、云端复用已构建产物。
"""
import json
import logging
import re
import tempfile
import time
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
        通过 MinerU 官方 Agent 轻量 API（免 token）远程解析：
          1. POST /agent/parse/file   -> task_id + 签名上传 URL(file_url)
          2. PUT  file_url            -> 上传文件字节
          3. GET  /agent/parse/{id}   -> 轮询直到 done/failed，done 返回 markdown_url
          4. GET  markdown_url        -> 下载 Markdown 文本
        该限制：≤ 10MB、≤ 20 页、单文件；IP 限频超限返回 HTTP 429。
        """
        import requests
        from src.config import get_config

        cfg = get_config().mineru
        session = self._build_retry_session(requests, cfg)
        try:
            task_id, file_url = self._agent_create_task(session, cfg, pdf_path)
            self._agent_upload(session, cfg, file_url, pdf_path)
            markdown_url = self._agent_poll(session, cfg, task_id)
            markdown_text = self._agent_download(session, cfg, markdown_url)
        except Exception as e:
            _log.error(f"MinerU API解析失败: {pdf_path.name}, error={e}")
            raise ValueError(f"简历PDF解析失败: {pdf_path.name}: {e}") from e
        finally:
            session.close()

        markdown_text = self._clean_markdown(markdown_text)
        if not markdown_text.strip():
            raise RuntimeError(f"PDF解析结果为空: {pdf_path.name}")
        return markdown_text

    # ---------- MinerU Agent 轻量 API（免 token）内部实现 ----------

    @staticmethod
    def _build_retry_session(requests, cfg):
        """
        构造带自动退避重试的 Session：CDN/API 偶发 TLS 中断(SSL EOF)、
        连接/读取超时、5xx 与 429 限频时由 urllib3 Retry 自动重试，避免瞬时网络抖动导致解析失败。
        """
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=cfg.max_retries,
            connect=cfg.max_retries,
            read=cfg.max_retries,
            status=cfg.max_retries,
            other=cfg.max_retries,
            backoff_factor=cfg.retry_backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST", "PUT"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @staticmethod
    def _agent_create_task(session, cfg, pdf_path: Path):
        """第一步：创建解析任务，返回 (task_id, file_url)"""
        url = f"{cfg.api_base}/parse/file"
        payload = {
            "file_name": pdf_path.name,
            "language": cfg.language,
            "enable_table": cfg.enable_table,
            "enable_formula": cfg.enable_formula,
            "is_ocr": cfg.is_ocr,
        }
        resp = session.post(url, json=payload, timeout=cfg.request_timeout)
        if resp.status_code == 429:
            raise RuntimeError("MinerU Agent API 触发 IP 限频(429)，请稍后重试")
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"创建解析任务失败: code={body.get('code')}, msg={body.get('msg')}")
        data = body.get("data") or {}
        task_id = data.get("task_id")
        file_url = data.get("file_url")
        if not task_id or not file_url:
            raise RuntimeError(f"创建解析任务响应缺少 task_id/file_url: {body}")
        return task_id, file_url

    @staticmethod
    def _agent_upload(session, cfg, file_url: str, pdf_path: Path) -> None:
        """第二步：PUT 文件字节到签名上传 URL（不带 Content-Type）"""
        with open(pdf_path, "rb") as f:
            resp = session.put(file_url, data=f, timeout=cfg.request_timeout)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"文件上传失败: HTTP {resp.status_code}")

    @staticmethod
    def _agent_poll(session, cfg, task_id: str) -> str:
        """第三步：轮询解析状态，done 返回 markdown_url，failed/超时抛异常"""
        url = f"{cfg.api_base}/parse/{task_id}"
        deadline = time.monotonic() + cfg.poll_timeout
        last_state = None
        while True:
            resp = session.get(url, timeout=cfg.request_timeout)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise RuntimeError(f"查询解析结果失败: code={body.get('code')}, msg={body.get('msg')}")
            data = body.get("data") or {}
            last_state = data.get("state")
            if last_state == "done":
                markdown_url = data.get("markdown_url")
                if not markdown_url:
                    raise RuntimeError(f"解析完成但缺少 markdown_url: {data}")
                return markdown_url
            if last_state == "failed":
                err = data.get("err_msg") or data.get("err_code") or "未知错误"
                raise RuntimeError(f"MinerU 解析失败: {err}")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"MinerU 解析超时（{cfg.poll_timeout}s），最后状态: {last_state}")
            time.sleep(cfg.poll_interval)

    @staticmethod
    def _agent_download(session, cfg, markdown_url: str) -> str:
        """第四步：下载 markdown_url（CDN 链接）的 Markdown 文本"""
        resp = session.get(markdown_url, timeout=cfg.request_timeout)
        resp.raise_for_status()
        return resp.text

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
