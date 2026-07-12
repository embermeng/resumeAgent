"""
意图识别模块
分类用户请求为 quick_response / deep_thinking / chitchat
"""
import json
import logging
from enum import Enum
from typing import Dict, Any, Optional

from pydantic import BaseModel, Field

from src.api_client import APIProcessor
from src.prompts.resume_prompts import INTENT_CLASSIFY_SYSTEM, INTENT_CLASSIFY_USER

_log = logging.getLogger(__name__)


class IntentType(str, Enum):
    """意图类型枚举"""
    QUICK_RESPONSE = "quick_response"
    DEEP_THINKING = "deep_thinking"
    CHITCHAT = "chitchat"


class IntentResult(BaseModel):
    """意图识别结果"""
    intent: IntentType = Field(description="意图类型")
    entities: Dict[str, Any] = Field(default_factory=dict, description="提取的实体")
    confidence: float = Field(default=0.5, description="置信度 0-1")


class IntentClassifier:
    """意图分类器"""

    def __init__(self, provider: str = "dashscope", model: str = None):
        self.api = APIProcessor(provider=provider)
        self.model = model or self.api.default_model

    def classify(self, user_input: str) -> IntentResult:
        """
        对用户输入进行意图分类
        参数:
            user_input: 用户输入文本
        返回:
            IntentResult
        """
        user_prompt = INTENT_CLASSIFY_USER.format(user_input=user_input)

        try:
            result = self.api.send_message(
                model=self.model,
                temperature=0.1,
                system_content=INTENT_CLASSIFY_SYSTEM,
                human_content=user_prompt,
                is_structured=True,
                response_format=IntentResult,
            )

            if isinstance(result, dict):
                # 处理可能的解析错误
                if "parse_error" in result:
                    _log.warning(f"意图识别解析失败: {result['parse_error']}")
                    return self._fallback_classify(user_input)

                # 确保intent值是有效的枚举
                intent_str = result.get("intent", "chitchat")
                try:
                    intent = IntentType(intent_str)
                except ValueError:
                    intent = IntentType.CHITCHAT

                return IntentResult(
                    intent=intent,
                    entities=result.get("entities", {}),
                    confidence=result.get("confidence", 0.5),
                )
            else:
                return self._fallback_classify(user_input)

        except Exception as e:
            _log.error(f"意图识别失败: {e}")
            return self._fallback_classify(user_input)

    @staticmethod
    def _fallback_classify(user_input: str) -> IntentResult:
        """基于关键词的降级分类"""
        input_lower = user_input.lower()

        # 简历相关关键词 -> deep_thinking
        resume_keywords = ["简历", "resume", "生成简历", "优化简历", "求职", "面试准备"]
        if any(kw in input_lower for kw in resume_keywords):
            return IntentResult(
                intent=IntentType.DEEP_THINKING,
                entities={},
                confidence=0.6,
            )

        # 技术问题关键词 -> quick_response
        tech_keywords = [
            "什么", "怎么", "如何", "为什么", "区别", "原理", "流程",
            "rag", "langchain", "faiss", "embedding", "agent", "llm",
            "python", "java", "算法", "数据结构",
        ]
        if any(kw in input_lower for kw in tech_keywords):
            return IntentResult(
                intent=IntentType.QUICK_RESPONSE,
                entities={},
                confidence=0.5,
            )

        # 默认闲聊
        return IntentResult(
            intent=IntentType.CHITCHAT,
            entities={},
            confidence=0.3,
        )
