"""
多模型LLM API客户端
统一封装 DashScope / OpenAI / Gemini 的消息发送与结构化输出
参考 RAG-cy/src/api_requests.py 设计
"""
import os
import json
import logging
from typing import Union, Dict, Optional, Type, Literal
from dotenv import load_dotenv
from pydantic import BaseModel
from json_repair import repair_json
from tenacity import retry, stop_after_attempt, wait_fixed

load_dotenv()
logger = logging.getLogger(__name__)


# ============================================================
# 基础处理器
# ============================================================

class BaseDashscopeProcessor:
    """DashScope (通义千问) 处理器
    使用OpenAI兼容端点(compatible-mode)：纯文本与多模态模型统一路由，
    支持qwen3.8-max等新旗舰模型（原生SDK的Generation.call仅支持纯文本端点，
    调用多模态模型会报400 url error）
    """

    COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self):
        from openai import OpenAI
        self.llm = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url=self.COMPATIBLE_BASE_URL,
            timeout=None,
            max_retries=2,
        )
        self.default_model = "qwen-turbo-latest"
        self.response_data = {}

    def send_message(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        is_structured: bool = False,
        response_format: Optional[Type[BaseModel]] = None,
        **kwargs,
    ) -> dict:
        if model is None:
            model = self.default_model

        messages = []
        if system_content:
            messages.append({"role": "system", "content": system_content})
        if human_content:
            messages.append({"role": "user", "content": human_content})

        # API错误（如403无权限、429限流）立即抛出，避免把错误响应当成正常内容吞掉
        try:
            completion = self.llm.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=messages,
            )
        except Exception as e:
            status = getattr(e, "status_code", None)
            raise RuntimeError(f"DashScope API错误 {status or 'unknown'}: {e}") from e

        content = completion.choices[0].message.content

        self.response_data = {
            "model": model,
            "input_tokens": getattr(completion.usage, "prompt_tokens", None),
            "output_tokens": getattr(completion.usage, "completion_tokens", None),
        }

        # 尝试解析JSON（结构化输出可能以JSON字符串形式返回）
        if is_structured and response_format is not None:
            return self._parse_structured(content, response_format)

        # 尝试解析为JSON dict
        try:
            return self._try_parse_json(content)
        except (json.JSONDecodeError, TypeError):
            return {"content": content}

    def send_message_stream(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        **kwargs,
    ):
        """流式发送消息，逐块yield文本增量（SSE效果的数据源）"""
        if model is None:
            model = self.default_model

        messages = []
        if system_content:
            messages.append({"role": "system", "content": system_content})
        if human_content:
            messages.append({"role": "user", "content": human_content})

        try:
            stream = self.llm.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            status = getattr(e, "status_code", None)
            raise RuntimeError(f"DashScope API错误 {status or 'unknown'}: {e}") from e

    def _parse_structured(self, content: str, response_format: Type[BaseModel]) -> dict:
        """解析结构化输出"""
        try:
            json_str = self._extract_json_str(content)
            parsed = json.loads(json_str)
            validated = response_format.model_validate(parsed)
            return validated.model_dump()
        except Exception as e:
            logger.warning(f"结构化解析失败: {e}, 尝试repair...")
            try:
                repaired = repair_json(content)
                parsed = json.loads(repaired)
                validated = response_format.model_validate(parsed)
                return validated.model_dump()
            except Exception:
                return {"content": content, "parse_error": str(e)}

    @staticmethod
    def _extract_json_str(content: str) -> str:
        """从可能包含markdown代码块的响应中提取JSON"""
        content = content.strip()
        if content.startswith("```"):
            first_nl = content.find("\n")
            last_bc = content.rfind("```")
            if first_nl > 0 and last_bc > first_nl:
                return content[first_nl + 1:last_bc].strip()
        return content

    @staticmethod
    def _try_parse_json(content: str) -> dict:
        """尝试将content解析为JSON dict"""
        content = content.strip()
        # 处理markdown代码块
        if content.startswith("```"):
            first_nl = content.find("\n")
            last_bc = content.rfind("```")
            if first_nl > 0 and last_bc > first_nl:
                content = content[first_nl + 1:last_bc].strip()
        return json.loads(content)


class BaseOpenaiProcessor:
    """OpenAI 处理器"""

    def __init__(self):
        from openai import OpenAI
        self._openai = OpenAI
        self.llm = self._setup_llm()
        self.default_model = "gpt-4o-mini-2024-07-18"
        self.response_data = {}

    def _setup_llm(self):
        return self._openai(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=None,
            max_retries=2,
        )

    def send_message(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        is_structured: bool = False,
        response_format: Optional[Type[BaseModel]] = None,
        **kwargs,
    ) -> dict:
        if model is None:
            model = self.default_model

        params = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": human_content},
            ],
        }

        if is_structured and response_format is not None:
            params["response_format"] = response_format
            completion = self.llm.beta.chat.completions.parse(**params)
            response = completion.choices[0].message.parsed
            content = response.model_dump() if hasattr(response, "model_dump") else response.dict()
        else:
            completion = self.llm.chat.completions.create(**params)
            content = completion.choices[0].message.content
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content = {"content": content}

        self.response_data = {
            "model": completion.model,
            "input_tokens": completion.usage.prompt_tokens,
            "output_tokens": completion.usage.completion_tokens,
        }
        return content

    def send_message_stream(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        **kwargs,
    ):
        """流式发送消息，逐块yield文本增量"""
        if model is None:
            model = self.default_model

        completion = self.llm.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": human_content},
            ],
            stream=True,
        )
        for chunk in completion:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class BaseGeminiProcessor:
    """Gemini 处理器"""

    def __init__(self):
        import google.generativeai as genai
        self._genai = genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.default_model = "gemini-2.0-flash-001"
        self.response_data = {}

    @retry(wait=wait_fixed(20), stop=stop_after_attempt(3))
    def _generate_with_retry(self, model_instance, prompt, generation_config):
        return model_instance.generate_content(prompt, generation_config=generation_config)

    def send_message(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        is_structured: bool = False,
        response_format: Optional[Type[BaseModel]] = None,
        **kwargs,
    ) -> dict:
        if model is None:
            model = self.default_model

        generation_config = {"temperature": temperature}
        prompt = f"{system_content}\n\n---\n\n{human_content}"

        model_instance = self._genai.GenerativeModel(
            model_name=model,
            generation_config=generation_config,
        )

        response = self._generate_with_retry(model_instance, prompt, generation_config)
        self.response_data = {
            "model": response.model_version,
            "input_tokens": response.usage_metadata.prompt_token_count,
            "output_tokens": response.usage_metadata.candidates_token_count,
        }

        content = response.text
        if is_structured and response_format is not None:
            try:
                repaired = repair_json(content)
                parsed = json.loads(repaired)
                validated = response_format.model_validate(parsed)
                return validated.model_dump()
            except Exception:
                return {"content": content}

        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"content": content}


    def send_message_stream(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        **kwargs,
    ):
        """流式发送消息，逐块yield文本增量"""
        if model is None:
            model = self.default_model

        prompt = f"{system_content}\n\n---\n\n{human_content}"
        model_instance = self._genai.GenerativeModel(
            model_name=model,
            generation_config={"temperature": temperature},
        )
        for chunk in model_instance.generate_content(prompt, stream=True):
            if chunk.text:
                yield chunk.text


# ============================================================
# 统一API入口
# ============================================================

class APIProcessor:
    """
    统一API处理器，根据provider路由到对应的底层处理器
    用法:
        processor = APIProcessor(provider="dashscope")
        result = processor.send_message(system_content="...", human_content="...")
    """

    def __init__(self, provider: Literal["dashscope", "openai", "gemini"] = "dashscope"):
        self.provider = provider.lower()
        if self.provider == "dashscope":
            self.processor = BaseDashscopeProcessor()
        elif self.provider == "openai":
            self.processor = BaseOpenaiProcessor()
        elif self.provider == "gemini":
            self.processor = BaseGeminiProcessor()
        else:
            raise ValueError(f"不支持的provider: {self.provider}")

    @property
    def default_model(self) -> str:
        return self.processor.default_model

    @property
    def response_data(self) -> dict:
        return self.processor.response_data

    def send_message(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        is_structured: bool = False,
        response_format: Optional[Type[BaseModel]] = None,
        **kwargs,
    ) -> dict:
        """发送消息到LLM，统一接口"""
        if model is None:
            model = self.processor.default_model
        return self.processor.send_message(
            model=model,
            temperature=temperature,
            system_content=system_content,
            human_content=human_content,
            is_structured=is_structured,
            response_format=response_format,
            **kwargs,
        )

    def send_message_stream(
        self,
        model: str = None,
        temperature: float = 0.5,
        system_content: str = "You are a helpful assistant.",
        human_content: str = "Hello!",
        **kwargs,
    ):
        """流式发送消息，逐块yield文本增量"""
        if model is None:
            model = self.processor.default_model
        return self.processor.send_message_stream(
            model=model,
            temperature=temperature,
            system_content=system_content,
            human_content=human_content,
            **kwargs,
        )

    def get_embedding(self, text: str, provider: str = None, model: str = None) -> list:
        """
        获取文本embedding向量
        provider: dashscope / openai，默认使用配置中的provider
        """
        provider = provider or os.getenv("EMBEDDING_PROVIDER", "dashscope")

        if provider == "dashscope":
            import dashscope
            dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
            model = model or "text-embedding-v1"
            rsp = dashscope.TextEmbedding.call(model=model, input=[text])
            if "output" in rsp and "embeddings" in rsp["output"]:
                emb = rsp["output"]["embeddings"][0]
                if emb["embedding"] is None or len(emb["embedding"]) == 0:
                    raise RuntimeError(f"DashScope embedding为空, text_index={emb.get('text_index')}")
                return emb["embedding"]
            else:
                raise RuntimeError(f"DashScope embedding API异常: {rsp}")

        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            model = model or "text-embedding-3-large"
            embedding = client.embeddings.create(input=[text], model=model)
            return embedding.data[0].embedding

        else:
            raise ValueError(f"不支持的embedding provider: {provider}")
