"""
Streamlit Web UI - ResumeAgent
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 日志输出到控制台（含各阶段耗时统计），屏蔽httpx请求日志噪音
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

import streamlit as st
from src.config import get_config
from src.agent.graph import ResumeAgent
from src.knowledge.resume_file_parser import ResumeFileParser, SUPPORTED_EXTENSIONS

# 页面配置
st.set_page_config(
    page_title="ResumeAgent - 简历生成助手",
    page_icon="📄",
    layout="wide",
)

# 初始化session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent" not in st.session_state:
    st.session_state.agent = None
if "existing_resume" not in st.session_state:
    st.session_state.existing_resume = ""      # 解析后的已有简历文本
    st.session_state.resume_file_key = None    # 已解析文件的缓存键（文件名+大小）
    st.session_state.resume_file_error = ""    # 解析失败提示
    st.session_state.resume_removed = False    # 用户手动移除后不重复解析同一文件


def get_agent():
    """获取或创建Agent实例"""
    if st.session_state.agent is None:
        config = get_config()
        st.session_state.agent = ResumeAgent(config=config)
    return st.session_state.agent


def get_resume_parser():
    """获取或创建简历文件解析器"""
    if "resume_parser" not in st.session_state:
        st.session_state.resume_parser = ResumeFileParser()
    return st.session_state.resume_parser


# 侧边栏
with st.sidebar:
    st.title("📄 ResumeAgent")
    st.markdown("---")
    st.markdown("### 功能")
    st.markdown("- 💬 技术知识问答")
    st.markdown("- 📋 项目经历查询")
    st.markdown("- 📝 简历生成与优化")
    st.markdown("---")
    st.markdown("### 使用方式")
    st.markdown("直接在聊天框输入问题即可：")
    st.markdown("- 问技术问题 → 快速回答模式")
    st.markdown("- 说'生成简历' → 深度思考模式")
    st.markdown("---")

    # 已有简历上传：解析后作为生成简历时的事实骨架
    st.markdown("### 📎 我的已有简历")
    resume_types = [ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS]
    uploaded = st.file_uploader(
        "上传后生成简历将在它的基础上增强（可选）",
        type=resume_types,
    )
    if uploaded is not None:
        file_key = (uploaded.name, uploaded.size)
        if st.session_state.resume_file_key != file_key:
            # 新文件：解析一次并缓存，避免每次rerun重复解析（PDF走MinerU较重）
            st.session_state.resume_file_key = file_key
            st.session_state.resume_removed = False
            st.session_state.resume_file_error = ""
            st.session_state.existing_resume = ""
            with st.spinner(f"正在解析 {uploaded.name}..."):
                try:
                    file_bytes = uploaded.getvalue()
                    st.session_state.existing_resume = get_resume_parser().parse(
                        uploaded.name, file_bytes
                    )
                except Exception as e:
                    st.session_state.resume_file_error = str(e)
        if st.session_state.existing_resume:
            st.success(f"已上传：{uploaded.name}")
            with st.expander("预览解析结果"):
                st.markdown(st.session_state.existing_resume)
            if st.button("🗑️ 移除已有简历"):
                st.session_state.existing_resume = ""
                st.session_state.resume_removed = True
                st.rerun()
        elif st.session_state.resume_file_error:
            st.error(f"解析失败：{st.session_state.resume_file_error}")
        elif st.session_state.resume_removed:
            st.caption("已移除，本次会话不再使用该文件")
    else:
        # 未上传文件时清空状态
        st.session_state.existing_resume = ""
        st.session_state.resume_file_key = None
        st.session_state.resume_file_error = ""
        st.session_state.resume_removed = False

    st.markdown("---")
    if st.button("🗑️ 清空对话"):
        st.session_state.messages = []
        st.rerun()

# 主聊天区域
st.title("ResumeAgent - 智能简历生成助手")

# 显示历史消息
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("intent"):
            st.caption(f"模式: {msg['intent']}")

# 用户输入
if prompt := st.chat_input("输入你的问题..."):
    # 添加用户消息
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Agent处理（流式输出：边生成边展示）
    with st.chat_message("assistant"):
        try:
            agent = get_agent()

            status_placeholder = st.empty()
            answer_placeholder = st.empty()
            intent_labels = {
                "quick_response": "⚡ 快速回答",
                "deep_thinking": "🧠 深度思考",
                "chitchat": "💬 闲聊",
            }

            intent = "unknown"
            step = ""
            full_response = ""
            resume_final = ""

            with status_placeholder.container():
                for event in agent.run_stream(
                    prompt,
                    existing_resume=st.session_state.existing_resume or None,
                ):
                    etype = event.get("type")
                    if etype == "status":
                        st.caption(f"⏳ {event['text']}")
                    elif etype == "intent":
                        intent = event["value"]
                    elif etype == "token":
                        # 首个token到达后释放状态区，答案区开始增量渲染
                        status_placeholder.empty()
                        full_response += event["text"]
                        answer_placeholder.markdown(full_response + "▌")
                    elif etype == "done":
                        step = event.get("step", "")
                        resume_final = event.get("resume_final", "") or ""

            # 流式结束：去掉光标，展示最终内容
            answer_placeholder.markdown(full_response)
            st.caption(f"模式: {intent_labels.get(intent, intent)} | 步骤: {step}")

            # 如果有简历结果，提供Markdown下载
            if resume_final:
                st.download_button(
                    "📥 下载简历 (Markdown)",
                    resume_final,
                    file_name="resume.md",
                    mime="text/markdown",
                )

            # 保存消息
            st.session_state.messages.append({
                "role": "assistant",
                "content": full_response,
                "intent": intent_labels.get(intent, intent),
            })

        except Exception as e:
            error_msg = f"处理出错: {e}"
            st.error(error_msg)
            st.session_state.messages.append({
                "role": "assistant",
                "content": error_msg,
            })
