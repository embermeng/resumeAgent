"""
文本分词工具
BM25索引构建与查询必须使用同一套分词器，否则检索失效
中文无空格，不能用str.split()切词，统一使用jieba分词
"""
from typing import List

import jieba


def tokenize(text: str) -> List[str]:
    """
    jieba分词，过滤空白token
    参数:
        text: 原始文本（中文/英文/混合）
    返回:
        token列表
    """
    return [tok for tok in jieba.cut(text) if tok.strip()]
